#!/usr/bin/env python3
"""Tool-call metrics from harness event streams (issues #1, #2, #3).

This module is a library, not a script. It has one seam:
`normalize_calls()` turns pi's and dsh's differently-shaped event streams
into the same list of `Call` objects, and everything downstream
(`call_metrics()`) reads only that shape. Nothing here prints, and nothing
here does file I/O beyond the two `load_*` functions, which exist only so
callers do not have to know that dsh hides its session under a tarball of
zstd-compressed jsonl and pi does not.

Raw tool arguments never leave this module: `call_metrics()` returns digests,
not arguments, because `results/*/score.json` is published and `edit`
arguments are agent-written solution code (issue #2).
"""
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# Tools observed mutating the workspace in either harness's capture (see
# EVENTS.md). `bash` is deliberately excluded even though a shell command
# *could* touch the filesystem: unlike edit/write we cannot tell from the
# tool name alone whether a given bash call wrote anything, and guessing
# would make `mutatingCalls` lie in the other direction. This is exactly
# the set the "24 calls, 0 writes" run in issue #3 needs: read + bash only,
# mutatingCalls == 0.
MUTATING_TOOLS = frozenset({'edit', 'write'})

# Every tool name either harness capture in EVENTS.md was observed emitting.
# This is what one 240-second capture of one model happened to use, NOT an
# inventory of either harness's tool set -- neither harness documents one.
# So a name outside this set means "this capture never saw it", and it is
# reported through `unknownTools` and nowhere else. It deliberately does
# NOT feed `mutatingCalls`: issue #3's finding is "seven Qwen runs, two
# harnesses, two quants, four context sizes, 0 writes every time", and a
# read-only tool this list happens to omit (`grep`, `list`, `webfetch`)
# would erase exactly that signal by inflating the count it is read from.
KNOWN_TOOLS = frozenset({'read', 'bash', 'edit', 'write', 'glob', 'todo_write', 'subagent'})

# A tool name reaches us from the model's own output, so it is agent-written
# text on its way to a published artefact (see the module docstring). Cap and
# restrict it at the seam rather than at each of the four places it is
# emitted -- toolHistogram, errorsByTool, unknownTools and callSequence.
TOOL_NAME_MAX = 64
_TOOL_NAME_OK = re.compile(r'\A[A-Za-z0-9_.:-]+\Z')


def _safe_tool_name(name):
    if not isinstance(name, str) or not name:
        return 'unknown'
    name = name[:TOOL_NAME_MAX]
    return name if _TOOL_NAME_OK.match(name) else 'unparseable'


DIGEST_LEN = 16  # hex chars; short enough to keep score.json small, long enough not to collide in one run


@dataclass
class Call:
    tool: str
    call_id: Optional[str]
    args: Optional[dict]
    args_digest: str
    ok: Optional[bool]     # True/False, or None if the stream never said
    turn: Optional[int]
    step: Optional[int]
    started_ms: Optional[int]
    ended_ms: Optional[int]
    error_text: Optional[str]


def _canonical_digest(args):
    """Digest of the canonicalised arguments, independent of key order or
    of whether the source encoded them as a dict (pi) or a JSON string
    (dsh) -- see `_decode_args`. Sorted, separator-tight JSON is the
    canonical form so the same logical call digests identically across
    harnesses; that comparability is the entire point of issue #2."""
    canon = json.dumps(args, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canon.encode('utf-8')).hexdigest()[:DIGEST_LEN]


def _decode_args(raw):
    """dsh's `arguments` is a JSON-encoded string; pi's `args` is already
    an object. Decoding dsh's here, once, is what makes `args_digest`
    comparable across harnesses -- digesting the *string* would hash JSON
    formatting quirks (key order, whitespace) instead of the call."""
    if isinstance(raw, dict) or raw is None:
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


# ---------------------------------------------------------------------------
# Loaders (thin: parse, skip garbage, return)
# ---------------------------------------------------------------------------

def load_pi_events(path):
    """Read pi's newline-delimited JSON event log. Lines that don't parse
    are skipped rather than raising -- a truncated trace (killed mid-run,
    the common case for a benchmark harness) should still yield whatever
    events came before the cut, not blow up the whole scoring pass."""
    events = []
    with open(path, errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except (ValueError, TypeError):
                continue
    return events


def load_dsh_events(tgz_path):
    """Extract dsh's session tarball, decompress the ROOT session (not the
    newest file -- see EVENTS.md on why mtime ordering is luck, not a
    rule), and parse its jsonl.

    Thin wrapper over `load_dsh_root_sessions()`: returns the first root
    session's events, or `[]` if there is none. If the tarball holds more
    than one root session -- a real ambiguity, not resolvable by timestamp
    -- this silently picks the first; callers that need to detect and
    report that ambiguity (issue #11) should call
    `load_dsh_root_sessions()` directly and look at its length.
    """
    roots = load_dsh_root_sessions(tgz_path)
    return roots[0][1] if roots else []


def _iter_dsh_sessions(tgz_path):
    """Yield (header, events) for every session file in the tarball,
    root or subagent alike. Internal: extraction + zstd-decompression
    lives here once, so `load_dsh_root_sessions()` is the only place that
    walks the tarball and shells out to `zstd` (issue #11's "Related"
    note about double-decompression)."""
    with tempfile.TemporaryDirectory() as td:
        with tarfile.open(tgz_path) as t:
            t.extractall(td, filter='data')
        session_files = []
        for root, _dirs, files in os.walk(td):
            for fn in files:
                if fn.endswith('.jsonl.zstd'):
                    session_files.append(os.path.join(root, fn))
        # Sorted, because os.walk yields in readdir order: without this,
        # which root session a multi-root tarball hands back varies by
        # filesystem and by extraction order. That is the same class of
        # mistake as selecting by mtime (issue #11) -- an incidental
        # property deciding which transcript a run is scored from. Path
        # order is arbitrary too, but it is at least reproducible, and
        # callers are told when the choice was ambiguous.
        session_files.sort()

        for path in session_files:
            proc = subprocess.run(['zstd', '-dc', path], capture_output=True, text=True)
            if proc.returncode != 0:
                continue
            lines = proc.stdout.splitlines()
            if not lines:
                continue
            try:
                header = json.loads(lines[0])
            except (ValueError, TypeError):
                continue
            events = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except (ValueError, TypeError):
                    continue
            yield header, events


def _is_dsh_root_header(header):
    return header.get('delegationDepth') == 0 or (
        header.get('delegationDepth') is None and 'parentSession' not in header
    )


def load_dsh_root_sessions(tgz_path):
    """Return `[(header, events), ...]` for every ROOT session in the
    tarball: header has `delegationDepth == 0`, or no `delegationDepth`
    and no `parentSession` at all.

    The tarball can hold more than one session because dsh forks a
    subagent into its own session file -- a subagent's header carries
    `parentSession` and `delegationDepth >= 1`. Ordinarily this returns
    exactly one root. More than one is a genuine ambiguity (two runs'
    worth of root-level history in one archive); it is returned as-is
    and it is the caller's job to detect and report that, not to resolve
    it by mtime or any other guess (issue #11).
    """
    return [(h, e) for h, e in _iter_dsh_sessions(tgz_path) if _is_dsh_root_header(h)]


def _parse_iso_ms(ts):
    """Parse an ISO-8601 timestamp (pi's `session.timestamp`, e.g.
    "2026-09-22T13:01:37.393Z") to epoch milliseconds, or None if it
    doesn't parse. `Z` is normalised to `+00:00` for Pythons whose
    `fromisoformat` predates PEP 615's `Z` support."""
    if not isinstance(ts, str):
        return None
    s = ts[:-1] + '+00:00' if ts.endswith('Z') else ts
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def stream_start_ms(events, harness):
    """The run's own wall-clock anchor, used only to express
    `timeToFirstToolCall` (issue #5) relative to when the harness itself
    started producing events -- not to container boot or model load time,
    which happen before either trace's first event.

    pi has no numeric top-level timestamp on most events; its `session`
    event carries the run's start as an ISO string. dsh's `session` header
    carries no `time` of its own (unlike every other dsh event), so this
    falls through to the first event that does.

    Returns None if the stream never says -- callers must then leave
    `timeToFirstToolCall` null, not compute it against a guess.
    """
    if harness == 'pi':
        for e in events:
            if isinstance(e, dict) and e.get('type') == 'session':
                ms = _parse_iso_ms(e.get('timestamp'))
                if ms is not None:
                    return ms
        return None
    if harness == 'dsh':
        for e in events:
            if isinstance(e, dict) and isinstance(e.get('time'), (int, float)):
                return e['time']
        return None
    return None


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------

def normalize_calls(events, harness):
    """Turn a raw event stream into `Call`s, one shape regardless of which
    harness produced it. Everything past this function is harness-blind."""
    if harness == 'pi':
        return _normalize_pi(events)
    if harness == 'dsh':
        return _normalize_dsh(events)
    raise ValueError(f'unknown harness: {harness!r}')


def _normalize_pi(events):
    calls = {}
    order = []
    turn = 0
    for e in events:
        if not isinstance(e, dict):
            continue
        t = e.get('type')
        if t == 'turn_start':
            turn += 1
        elif t == 'tool_execution_start':
            call_id = e.get('toolCallId')
            if call_id is None:
                continue
            args = e.get('args') if isinstance(e.get('args'), dict) else None
            calls[call_id] = Call(
                tool=_safe_tool_name(e.get('toolName')),
                call_id=call_id,
                args=args,
                args_digest=_canonical_digest(args),
                ok=None,
                turn=turn,
                step=None,
                started_ms=None,
                ended_ms=None,
                error_text=None,
            )
            order.append(call_id)
        elif t == 'tool_execution_end':
            call_id = e.get('toolCallId')
            c = calls.get(call_id)
            if c is None:
                continue
            is_error = e.get('isError')
            if isinstance(is_error, bool):
                c.ok = not is_error
                if is_error:
                    c.error_text = _pi_error_text(e.get('result'))
        elif t == 'turn_end':
            message = e.get('message') or {}
            start_ts = message.get('timestamp')
            for block in (message.get('content') or []):
                if isinstance(block, dict) and block.get('type') == 'toolCall':
                    c = calls.get(block.get('id'))
                    if c is not None and c.started_ms is None:
                        c.started_ms = start_ts
            for tr in (e.get('toolResults') or []):
                if not isinstance(tr, dict):
                    continue
                c = calls.get(tr.get('toolCallId'))
                if c is not None and c.ended_ms is None:
                    c.ended_ms = tr.get('timestamp')
    return [calls[cid] for cid in order]


def _pi_error_text(result):
    if isinstance(result, dict):
        content = result.get('content')
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                return first.get('text')
    return None


def _normalize_dsh(events):
    calls = {}
    order = []
    for e in events:
        if not isinstance(e, dict):
            continue
        t = e.get('type')
        d = e.get('data') or {}
        if not isinstance(d, dict):
            continue
        if t == 'tool/call':
            call_id = d.get('callId')
            if call_id is None:
                continue
            args = _decode_args(d.get('arguments'))
            calls[call_id] = Call(
                tool=_safe_tool_name(d.get('name')),
                call_id=call_id,
                args=args,
                args_digest=_canonical_digest(args),
                ok=None,
                turn=d.get('turn'),
                step=d.get('step'),
                started_ms=e.get('time'),
                ended_ms=None,
                error_text=None,
            )
            order.append(call_id)
        elif t == 'tool/result':
            message = d.get('message') or {}
            content = message.get('content')
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict) or item.get('type') != 'tool-result':
                    continue
                call_id = item.get('toolCallId')
                c = calls.get(call_id)
                if c is None:
                    continue
                # A callId can have more than one result event (EVENTS.md:
                # 37 calls, 40 results here). Classify per callId, not per
                # result: an error on any result for this call wins, so a
                # duplicate "it worked" after a real failure can't hide it.
                if c.ended_ms is None:
                    c.ended_ms = e.get('time')
                is_error = item.get('isError')
                if is_error is True:
                    c.ok = False
                    if c.error_text is None:
                        c.error_text = _dsh_error_text(item)
                elif is_error is False and c.ok is not False:
                    c.ok = True
    return [calls[cid] for cid in order]


def _dsh_error_text(item):
    content = item.get('content')
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            return first.get('text')
    return None


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _percentile(sorted_values, pct):
    """Linear-interpolated percentile (numpy's default 'linear' method)
    over an already-sorted list. `sorted_values` is non-empty."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    idx = (pct / 100.0) * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _calls_per_turn(calls):
    """Issue #6: group calls by turn (pi: `turn`; dsh: `(turn, step)`, per
    EVENTS.md section 3 -- a dsh "turn" is the whole conversation turn, so
    calls-per-assistant-step needs the finer `(turn, step)` key. For pi,
    `step` is always None, so the same key degenerates to grouping by
    `turn` alone.
    """
    groups = {}
    for c in calls:
        if c.turn is None:
            continue
        groups.setdefault((c.turn, c.step), []).append(c)
    counts = [len(v) for v in groups.values()]
    if not counts:
        # No turn attribution to group on -- either the run made no calls at
        # all, or it made calls whose events carried no `turn`. Returning
        # 0.0/0/0 here would put a plausible-looking "never batched a call"
        # next to a nonzero toolCalls total, which is the false negative
        # this file refuses everywhere else (see _call_timing's None returns
        # and score.py's resolve_parse_errors). Mean of nothing is not zero.
        return {'mean': None, 'max': None}, None
    mean = round(sum(counts) / len(counts), 4)
    return {'mean': mean, 'max': max(counts)}, sum(1 for n in counts if n > 1)


# Harnesses whose tool events carry their own timestamps, so a call's
# execution time can be separated from the generation that preceded it.
#
# dsh qualifies: `tool/call` and `tool/result` are distinct events, each
# with its own `time`. pi does NOT: its `tool_execution_start` and
# `tool_execution_end` carry no timestamp at all (verified against the
# archived capture -- their only keys are toolCallId/toolName/args and
# toolCallId/toolName/result/isError). The only clock pi exposes near a
# call is `message.timestamp`, and that is a single creation stamp,
# identical on `message_start` and `message_end`, written when the
# assistant message is created -- i.e. at the START of generation, 1ms
# after the previous tool result came back. So for pi:
#
#   ended_ms - started_ms   = generation AND execution, inseparable
#   next started_ms - ended_ms = ~1ms of bookkeeping, not generation
#
# On the archived pi capture that yields 208.65s of "tool execution" in a
# 239.5s run whose tools are local file reads, and a "generation" figure
# of 30.88s that is really the 5 completed compactions: 29 of its 34
# inter-call gaps are under 10ms. Publishing either number would assert
# something plainly false, so pi reports None for both. See EVENTS.md.
PER_CALL_CLOCK = frozenset({'dsh'})


def _call_timing(calls, run_start_ms, per_call_clock=True):
    """Issue #5. Built entirely from `Call.started_ms`/`ended_ms` -- for
    pi those are the turn-level surrogates EVENTS.md documents (the
    enclosing `turn_end.message.timestamp` / `toolResults[].timestamp`),
    for dsh the call's own `tool/call`/`tool/result` `time`. Whichever
    harness produced them, the same arithmetic applies:

    - a call's own `toolExecutionSeconds` contribution is its
      `ended_ms - started_ms` (EVENTS.md: "the gap between them is the
      tool's own execution time");
    - the `generationSeconds` between two calls is the previous call's
      `ended_ms` to the next call's `started_ms` ("the gap between one
      turn's result and the next turn's message is generation time").

    Any field this cannot compute (no timestamps at all, or fewer than 2
    calls for the interval/generation figures) comes back None rather
    than a fabricated 0 -- silence is not the same fact as "instant".
    """
    ordered = [c for c in calls if isinstance(c.started_ms, (int, float))]

    time_to_first = None
    if run_start_ms is not None and ordered:
        time_to_first = round(max(0, ordered[0].started_ms - run_start_ms) / 1000.0, 3)

    starts = [c.started_ms for c in ordered]
    gaps = [b - a for a, b in zip(starts, starts[1:]) if b >= a]
    intervals = None
    if gaps:
        gaps_sorted = sorted(gaps)
        intervals = {
            'n': len(gaps),
            'p50Seconds': round(_percentile(gaps_sorted, 50) / 1000.0, 3),
            'p95Seconds': round(_percentile(gaps_sorted, 95) / 1000.0, 3),
        }

    exec_ms, exec_n = 0, 0
    for c in calls:
        if (isinstance(c.started_ms, (int, float)) and isinstance(c.ended_ms, (int, float))
                and c.ended_ms >= c.started_ms):
            exec_ms += c.ended_ms - c.started_ms
            exec_n += 1
    tool_execution_seconds = round(exec_ms / 1000.0, 3) if exec_n else None

    gen_ms, gen_n, prev_end = 0, 0, None
    for c in calls:
        if (prev_end is not None and isinstance(c.started_ms, (int, float))
                and c.started_ms >= prev_end):
            gen_ms += c.started_ms - prev_end
            gen_n += 1
        if isinstance(c.ended_ms, (int, float)):
            prev_end = c.ended_ms
    generation_seconds = round(gen_ms / 1000.0, 3) if gen_n else None

    if not per_call_clock:
        # The harness gave us no clock that distinguishes the two, and a
        # plausible-looking number here is worse than an absent one: it
        # would be read as a measurement. #5's other two fields survive,
        # and they are the ones its motivating pathology needs.
        generation_seconds = None
        tool_execution_seconds = None

    return {
        'timeToFirstToolCall': time_to_first,
        'toolCallIntervals': intervals,
        'generationSeconds': generation_seconds,
        'toolExecutionSeconds': tool_execution_seconds,
    }


def call_metrics(calls, run_start_ms=None, harness=None):
    """Aggregate a list of `Call`s into the fields issues #1-#3, #5 and #6
    asked for.

    Never returns raw arguments -- only `args_digest`. That is not an
    oversight to double check later: it is the fix for issue #2, whose
    complaint is precisely that agent-written solution code (`edit`
    arguments) could end up in a published `results/*/score.json`.

    `run_start_ms` is optional and only affects `timeToFirstToolCall`
    (issue #5): it is the run's own anchor from `stream_start_ms()`, not
    part of a `Call`, because it is a property of the whole stream, not of
    any one call. Omit it (or pass None) and that one field comes back
    null; every other field here needs no such anchor.
    """
    total = len(calls)
    ok = sum(1 for c in calls if c.ok is True)
    error = sum(1 for c in calls if c.ok is False)
    unknown = sum(1 for c in calls if c.ok is None)

    errors_by_tool = {}
    for c in calls:
        if c.ok is False:
            errors_by_tool[c.tool] = errors_by_tool.get(c.tool, 0) + 1

    histogram = {}
    for c in calls:
        histogram[c.tool] = histogram.get(c.tool, 0) + 1

    # Counts only tools known to mutate. An unrecognised name is reported
    # through `unknownTools` instead of being folded in here -- see the
    # KNOWN_TOOLS comment on why folding destroys the zero-write signal.
    unknown_tools = sorted({c.tool for c in calls if c.tool not in KNOWN_TOOLS})
    mutating = sum(1 for c in calls if c.tool in MUTATING_TOOLS)

    seen_pairs = set()
    repeated = 0
    longest_run = 0
    current_run = 0
    prev_pair = None
    sequence = []  # run-length encoded [tool, digest, count]
    for c in calls:
        pair = (c.tool, c.args_digest)
        if pair in seen_pairs:
            repeated += 1
        seen_pairs.add(pair)

        if pair == prev_pair:
            current_run += 1
        else:
            current_run = 1
            prev_pair = pair
        longest_run = max(longest_run, current_run)

        if sequence and sequence[-1][0] == c.tool and sequence[-1][1] == c.args_digest:
            sequence[-1][2] += 1
        else:
            sequence.append([c.tool, c.args_digest, 1])

    distinct_ratio = round(len(seen_pairs) / total, 4) if total else 0.0

    # `unknown` calls are excluded from the denominator: the stream simply
    # did not say whether they succeeded, which is a different fact from
    # "it succeeded" (would deflate errorRate) or "it failed" (would
    # inflate it). Silence should not be counted as evidence either way.
    denom = ok + error
    error_rate = round(error / denom, 4) if denom else 0.0

    tool_calls_per_turn, parallel_turns = _calls_per_turn(calls)
    timing = _call_timing(calls, run_start_ms,
                          per_call_clock=harness in PER_CALL_CLOCK)

    return {
        'toolCalls': total,
        'toolOutcomes': {'ok': ok, 'error': error, 'unknown': unknown},
        'errorRate': error_rate,
        'errorsByTool': errors_by_tool,
        'toolHistogram': histogram,
        'unknownTools': unknown_tools,
        'mutatingCalls': mutating,
        'repeatedCalls': repeated,
        'longestRepeatRun': longest_run,
        'distinctCallRatio': distinct_ratio,
        'callSequence': [[t, dg, n] for t, dg, n in sequence],
        'toolCallsPerTurn': tool_calls_per_turn,
        'parallelTurns': parallel_turns,
        **timing,
    }
