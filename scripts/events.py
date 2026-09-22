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

    The tarball can hold more than one session because dsh forks a
    subagent into its own session file. We pick the one whose header has
    `delegationDepth == 0` (equivalently, no `parentSession`); anything
    else is a subagent transcript and reading metrics off it instead of
    the root would silently score the wrong process tree.
    """
    with tempfile.TemporaryDirectory() as td:
        with tarfile.open(tgz_path) as t:
            t.extractall(td, filter='data')
        session_files = []
        for root, _dirs, files in os.walk(td):
            for fn in files:
                if fn.endswith('.jsonl.zstd'):
                    session_files.append(os.path.join(root, fn))

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
            is_root = header.get('delegationDepth') == 0 or (
                header.get('delegationDepth') is None and 'parentSession' not in header
            )
            if not is_root:
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
            return events
    return []


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

def call_metrics(calls):
    """Aggregate a list of `Call`s into the fields issues #1-#3 asked for.

    Never returns raw arguments -- only `args_digest`. That is not an
    oversight to double check later: it is the fix for issue #2, whose
    complaint is precisely that agent-written solution code (`edit`
    arguments) could end up in a published `results/*/score.json`.
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
    }
