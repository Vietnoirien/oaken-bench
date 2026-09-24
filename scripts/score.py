#!/usr/bin/env python3
"""Score one benchmark run.

  ./scripts/score.py results/<label> [--no-detail]

Reconstructs the agent's workspace, runs the visible and held-out suites,
checks the frozen artefacts were not tampered with, and extracts harness
metrics. Held-out results are reported as COUNTS ONLY; per-test detail is
written to <label>/hidden-detail.json and is deliberately not printed.

hidden-detail.json holds the held-out test FILE names and pass/fail status,
which is itself benchmark data (see docker/scorer.sh) -- it is written
straight to the gitignored result directory, never merged into score.json
or events-summary.json, both of which are published. Written by default
since it costs nothing and cannot leak past .gitignore; pass --no-detail to
skip it (e.g. to save the container round trip when only the counts in
score.json are wanted).
"""
import json, os, re, shutil, signal, subprocess, sys, tarfile, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from events import (  # noqa: E402
    call_metrics, load_dsh_events, load_dsh_root_sessions, load_pi_events,
    normalize_calls, stream_start_ms,
)

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(BENCH, 'seed')
HIDDEN = os.path.join(BENCH, 'hidden')

# Bump only when a change to normalize_calls()/call_metrics() alters the
# shape of events-summary.json -- consumers pin to this, not to the harness
# versions in EVENTS.md, because the extractor can change independently.
#
# v2 (issues #5, #6): added toolCallsPerTurn, parallelTurns,
# timeToFirstToolCall, toolCallIntervals, generationSeconds and
# toolExecutionSeconds to call_metrics()'s output and to
# PUBLISHED_CALL_FIELDS below. Timestamps only -- no raw arguments.
EVENTS_SUMMARY_SCHEMA_VERSION = 2

# Bump when a change to pi_metrics()/dsh_metrics() alters what `usage` or
# `compactions` in `harnessMetrics` MEAN, as opposed to their shape.
# EVENTS_SUMMARY_SCHEMA_VERSION above pins events-summary.json's fields
# (from call_metrics(), via normalize_calls()); this pins the separate,
# older `harnessMetrics.usage`/`.compactions` fields that pi_metrics() and
# dsh_metrics() have produced since before events.py existed, and that 16
# committed score.json files still carry with the pre-fix (wrong) meaning.
# Runs scored under version 1 summed streaming partials into `usage` and
# double-/over-counted `compactions` (issues #9, #10) and could pick a
# subagent transcript instead of the run for dsh (issue #11); version 2 is
# the first to fix all three. See EVENTS.md sections 1-2 and README's
# Fairness section for the before/after figures.
HARNESS_METRICS_SCHEMA_VERSION = 2

# Exactly the call_metrics() fields allowed into the published score.json.
# Extending call_metrics() does not extend this: adding a field here is the
# deliberate act of publishing it (see merge_events_into_harness_metrics).
PUBLISHED_CALL_FIELDS = (
    'toolCalls', 'toolOutcomes', 'errorRate', 'errorsByTool', 'toolHistogram',
    'unknownTools', 'mutatingCalls', 'repeatedCalls', 'longestRepeatRun',
    'distinctCallRatio', 'callSequence',
    # Issue #6 (calls per turn / parallel tool calling) and issue #5
    # (per-call timing). All aggregates or timestamps derived from them --
    # never raw arguments -- so publishing these is not a change to what
    # the allowlist is for, only to its length.
    'toolCallsPerTurn', 'parallelTurns',
    'timeToFirstToolCall', 'toolCallIntervals', 'generationSeconds',
    'toolExecutionSeconds',
)

FROZEN_PATHS = [
    'SPEC.md', 'data/items.json', 'package.json', 'tsconfig.json', 'vitest.config.ts',
]


def _reap(pgid):
    """SIGTERM then SIGKILL a whole process group, ignoring races."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        for _ in range(10):
            try:
                os.killpg(pgid, 0)       # group still alive?
            except (ProcessLookupError, OSError):
                return
            time.sleep(0.5)


def sh(cmd, cwd=None, timeout=900):
    """Run a command in its own process group and reap the whole tree.

    subprocess.run(timeout=...) kills only the direct child. vitest forks a
    worker pool, so a non-terminating test left ~85 orphans pinning 4 cores for
    hours. start_new_session puts the child in its own group so every
    descendant can be signalled -- on timeout AND on normal exit, since vitest
    can return while workers are still spinning.
    """
    p = subprocess.Popen(cmd, cwd=cwd, shell=isinstance(cmd, str),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    try:
        pgid = os.getpgid(p.pid)
    except (ProcessLookupError, OSError):
        pgid = None
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if pgid:
            _reap(pgid)
        try:
            p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()
        return -9, '', 'TIMEOUT'
    finally:
        if pgid:
            _reap(pgid)                  # sweep stragglers on every path
    return p.returncode, out, err


VITEST_BOUND = 420  # seconds


def vitest(workdir, tag, bound=VITEST_BOUND):
    """Runs vitest. Returns (passed, failed, total, raw_json_or_None, hung)."""
    out = os.path.join(workdir, f'.vitest-{tag}.json')
    rc, so, se = sh(
        f'npx vitest run --reporter=json --testTimeout=10000 --outputFile={out} 2>&1 || true',
        cwd=workdir, timeout=bound)
    hung = (rc == -9 and se == 'TIMEOUT')
    if hung or not os.path.exists(out):
        return 0, 0, 0, None, hung
    try:
        data = json.load(open(out))
    except Exception:
        return 0, 0, 0, None, hung
    return (data.get('numPassedTests', 0), data.get('numFailedTests', 0),
            data.get('numTotalTests', 0), data, hung)


def canonical_totals():
    """Total test counts for the pristine suites. A run whose code breaks a
    module makes vitest fail to COLLECT the importing test files, which drops
    them from vitest's own total and silently inflates the pass rate. The
    denominator must therefore be fixed, not taken from the run."""
    cache = os.path.join(BENCH, 'canonical-totals.json')
    if os.path.exists(cache):
        return json.load(open(cache))
    work = tempfile.mkdtemp(prefix='canonical-')
    shutil.copytree(SEED, work, dirs_exist_ok=True, symlinks=True)
    _, _, vt, _, _ = vitest(work, 'visible')
    shutil.rmtree(os.path.join(work, 'tests'), ignore_errors=True)
    os.makedirs(os.path.join(work, 'tests'))
    for fn in sorted(os.listdir(HIDDEN)):
        if fn.endswith('.test.ts'):
            shutil.copy(os.path.join(HIDDEN, fn), os.path.join(work, 'tests', fn))
    _, _, ht, _, _ = vitest(work, 'hidden')
    shutil.rmtree(work, ignore_errors=True)
    totals = {'visible': vt, 'hidden': ht}
    json.dump(totals, open(cache, 'w'))
    return totals


def restore_workspace(result_dir, dest):
    tgz = os.path.join(result_dir, 'workspace.tgz')
    if not os.path.exists(tgz):
        return False
    with tempfile.TemporaryDirectory() as td:
        with tarfile.open(tgz) as t:
            t.extractall(td, filter='data')
        src = os.path.join(td, 'work')
        if not os.path.isdir(src):
            return False
        shutil.copytree(src, dest, dirs_exist_ok=True)
    # node_modules and .git were excluded from the archive
    shutil.copytree(os.path.join(SEED, 'node_modules'),
                    os.path.join(dest, 'node_modules'), dirs_exist_ok=True,
                    symlinks=True)
    return True


def check_frozen(workdir):
    """Returns list of frozen files the agent modified."""
    import hashlib
    tampered = []
    for rel in FROZEN_PATHS:
        a, b = os.path.join(SEED, rel), os.path.join(workdir, rel)
        if not os.path.exists(b):
            tampered.append(rel + ' (deleted)')
            continue
        ha = hashlib.sha256(open(a, 'rb').read()).hexdigest()
        hb = hashlib.sha256(open(b, 'rb').read()).hexdigest()
        if ha != hb:
            tampered.append(rel)
    return tampered


def pi_metrics(result_dir):
    f = os.path.join(result_dir, 'pi-events.jsonl')
    if not os.path.exists(f):
        return None
    counts, usage = {}, {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}
    tools = []
    compactions = 0
    compactions_aborted = 0
    for line in open(f, errors='replace'):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        t = e.get('type', '?')
        counts[t] = counts.get(t, 0) + 1
        # A compaction is one `compaction_start` (issue #10): pi also emits
        # a matching `compaction_end`, and a substring test on the event
        # type ('compact' in t.lower()) matched both and double-counted.
        # `compaction_end` still carries information worth keeping --
        # `aborted` marks a compaction that did not complete -- so it is
        # tallied separately rather than folded into `compactions` or
        # dropped.
        if t == 'compaction_start':
            compactions += 1
        elif t == 'compaction_end' and e.get('aborted'):
            compactions_aborted += 1
        if t == 'tool_execution_start':
            d = e.get('data') or {}
            n = (e.get('toolName') or e.get('name') or e.get('tool')
                 or d.get('toolName') or d.get('name'))
            if n:
                tools.append(n)
        # Usage lives at message.usage (issue #9): `e['usage']` /
        # `e['data']['usage']` matches nothing but `message_update`, the
        # per-chunk streaming deltas, which are cumulative snapshots, not
        # increments -- summing them overstates every figure. The real
        # value appears once per message, but on BOTH `message_end` and
        # `turn_end`; summing wherever it appears double-counts. Pick
        # `turn_end` alone: it fires exactly once per assistant turn.
        if t == 'turn_end':
            u = (e.get('message') or {}).get('usage')
            if isinstance(u, dict):
                for k in usage:
                    v = u.get(k)
                    if isinstance(v, (int, float)):
                        usage[k] += v
    return {'events': counts, 'turns': counts.get('turn_start', 0),
            'toolCalls': len(tools), 'tools': tools,
            'usage': usage, 'compactions': compactions,
            'compactionsAborted': compactions_aborted,
            'harnessMetricsVersion': HARNESS_METRICS_SCHEMA_VERSION}


def dsh_metrics(result_dir):
    tgz = os.path.join(result_dir, 'dsh-sessions.tgz')
    if not os.path.exists(tgz):
        return None
    # Select the ROOT session the same way events.load_dsh_events() does
    # (issue #11), instead of re-walking the tarball and sorting by mtime:
    # a subagent transcript can outlive its parent, which inverts mtime
    # order and would silently read the whole run's metrics off 20 events
    # instead of the run. Sharing load_dsh_root_sessions() also means this
    # no longer re-implements (and re-bugs) the loader's own extraction.
    roots = load_dsh_root_sessions(tgz)
    if not roots:
        return None
    # More than one root session is a real ambiguity -- report it, don't
    # resolve it by timestamp or any other guess.
    ambiguous = len(roots) > 1
    events = roots[0][1]

    counts, tools = {}, []
    compactions = 0
    pruned_count = 0
    pruned_shadowed_tokens = 0
    usage = {'inputTokens': 0, 'outputTokens': 0, 'cacheReadTokens': 0}
    # Output tokens attributed to the (turn, step) that produced them, plus
    # the set of steps that ended in a tool call. generationSeconds only
    # measures the wall-clock BETWEEN calls, so only the tokens generated in
    # those windows belong in a rate built on it -- see _derived_decode_rate.
    output_by_step = {}
    steps_with_call = set()
    for e in events:
        if not isinstance(e, dict):
            continue
        t = e.get('type', '?')
        counts[t] = counts.get(t, 0) + 1
        d = e.get('data')
        d = d if isinstance(d, dict) else {}
        # A compaction is one `compaction/start` (issue #10): the previous
        # heuristic substring-matched 'compact' against the first 2000
        # chars of the whole serialised event, which caught
        # compaction/start, /end, /summary AND /prune, plus any prose
        # (e.g. a user/message) that happened to use the word.
        if t == 'compaction/start':
            compactions += 1
        elif t == 'compaction/prune':
            # The model-free tool-result pruner (README's Fairness
            # section) -- a different mechanism from compaction, not a
            # subtype of it. Recorded separately, not folded in.
            pruned_count += 1
            v = d.get('shadowedTokenCount')
            if isinstance(v, (int, float)):
                pruned_shadowed_tokens += v
        if t == 'tool/call':
            n = d.get('toolName') or d.get('name') or d.get('tool') or e.get('toolName')
            if n:
                tools.append(n)
            steps_with_call.add((d.get('turn'), d.get('step')))
        # Usage lives at assistant/message.data.usage only (issue #9). The
        # old fallback (`d.get('usage') if isinstance(..., dict) else d`)
        # treated every OTHER event's whole `data` dict as a usage record,
        # absorbing any stray key shaped like `inputTokens` anywhere in
        # the stream -- deleted, not narrowed.
        if t == 'assistant/message':
            u = d.get('usage')
            if isinstance(u, dict):
                for k in usage:
                    v = u.get(k)
                    if isinstance(v, (int, float)):
                        usage[k] += v
                out = u.get('outputTokens')
                if isinstance(out, (int, float)):
                    key = (d.get('turn'), d.get('step'))
                    output_by_step[key] = output_by_step.get(key, 0) + out
    result = {
        'events': counts, 'steps': counts.get('step/start', 0),
        'toolCalls': len(tools), 'tools': tools,
        'usage': usage, 'compactions': compactions,
        'prunedToolResults': {'count': pruned_count,
                              'shadowedTokenCount': pruned_shadowed_tokens},
        'generationOutputTokens': sum(v for k, v in output_by_step.items()
                                      if k in steps_with_call),
        'harnessMetricsVersion': HARNESS_METRICS_SCHEMA_VERSION,
    }
    if ambiguous:
        result['rootSessionAmbiguous'] = True
        result['rootSessionCount'] = len(roots)
    return result


# (harness, trace filename, loader). Order matters only as a tie-break for
# a result dir that somehow holds both.
TRACES = (
    ('pi', 'pi-events.jsonl', load_pi_events),
    ('dsh', 'dsh-sessions.tgz', load_dsh_events),
)


def _ordered_traces(harness):
    """TRACES, with the named harness's entry (if any) moved to the front.
    `harness` is only a hint (see `_load_calls`), so every candidate stays
    in the list -- just reordered -- rather than being dropped."""
    if not harness:
        return TRACES
    matched = [t for t in TRACES if t[0] == harness]
    rest = [t for t in TRACES if t[0] != harness]
    return matched + rest


def _load_calls(result_dir, harness):
    """The one place score.py decides which raw trace file backs a run.

    `harness` comes from run.meta and is only a hint: it falls back to
    whichever trace file is actually present. Keying on it alone would mean
    a run with a missing or misspelt harness= line silently produces no
    events-summary.json at all -- a run that looks scored but carries none
    of #1-#3's fields, which is precisely the record loss issue #7 exists
    to stop.

    A candidate whose file exists but whose loader yields zero raw events
    is not taken at face value either: `docker/entrypoint.sh` tars up
    `dsh-sessions.tgz` unconditionally for every run, pi included, so a pi
    result directory always contains a ~45-byte stub archive with no
    session inside it. Before this fix, a pi run only avoided being scored
    off that empty stub because TRACES happens to list pi first when
    `harness` is missing or wrong -- an ordering accident, not a rule. Now
    every candidate whose trace is present is tried in order, and the
    first one that actually yields events wins; a present-but-empty trace
    is remembered as a last-resort fallback rather than accepted outright,
    so a genuinely empty run (the harness ran and captured nothing at all)
    still gets a real, zeroed summary instead of silently returning None.

    Returns `(calls, run_start_ms, resolved_harness)`, or
    `(None, None, None)` only when no trace file exists at all. The third
    value is the harness the trace itself identifies, which is what the
    timing fields must key on -- run.meta's `harness=` is a hint this
    function is already willing to override, so passing that hint on to
    call_metrics() would reintroduce the same mistake one layer up.
    """
    fallback = None
    for name, filename, loader in _ordered_traces(harness):
        f = os.path.join(result_dir, filename)
        if not os.path.exists(f):
            continue
        events = loader(f)
        if events:
            return normalize_calls(events, name), stream_start_ms(events, name), name
        if fallback is None:
            fallback = (name, events)
    if fallback is not None:
        name, events = fallback
        return normalize_calls(events, name), stream_start_ms(events, name), name
    return None, None, None


def merge_events_into_harness_metrics(hm, events_summary):
    """Fold call_metrics() (via events_summary) into the harnessMetrics dict
    that pi_metrics()/dsh_metrics() built, replacing the flat per-call
    `tools` list -- 327 copies of the string "read" in results/pi-01, almost
    all of that published file's 5.6 KB (issue #3) -- with the equivalent
    run-length-encoded `callSequence`.

    Existing field names (`events`, `turns`/`steps`, `usage`, `compactions`)
    are left untouched: other tooling and every already-committed score.json
    depend on those names, and this function only adds/replaces.
    """
    if hm is None or events_summary is None:
        return hm
    merged = dict(hm)
    merged.pop('tools', None)
    # An allowlist, not a denylist. score.json is published, and the rule
    # that raw arguments never reach it should hold by construction rather
    # than because a test happens to cover today's field set -- a denylist
    # would admit whatever a future call_metrics() starts returning.
    for k in PUBLISHED_CALL_FIELDS:
        if k in events_summary:
            merged[k] = events_summary[k]
    merged['derivedDecodeTokensPerSecond'] = _derived_decode_rate(merged)
    return merged


def _derived_decode_rate(hm):
    """Issue #5's caveat: "Any timing metric should be reported next to the
    measured t/s for that run, not on its own." Decode rate on this hardware
    varies with what else is resident -- MODELS.md section 3 notes a desktop
    app reopening mid-run costs ~700 MiB and can flip a configuration into
    the post-OOM fallback at half speed -- so a timing figure read without
    it is not interpretable.

    Nothing in this repo records the server's own `timings.predicted_per_second`
    per run: the harness makes the requests, not score.py, and llama-server's
    figure never reaches the trace. What the trace does support is
    output tokens / generation seconds, which is why this is `derived`, not
    `measured`. It is an EFFECTIVE rate: the denominator is wall-clock
    between one tool result and the next call, so it includes prefill and
    harness overhead and will read lower than a bare decode benchmark.

    None unless both inputs exist -- which means None for pi, whose
    generationSeconds is itself unavailable (see events.PER_CALL_CLOCK).
    An absent rate is not a slow one.
    """
    gen = hm.get('generationSeconds')
    # NOT total usage.output. generationSeconds only measures the wall-clock
    # windows BETWEEN one tool result and the next call, so the numerator has
    # to be the tokens generated in those windows -- the messages that ended
    # in a tool call. Tokens from a step that produced no call (a final
    # answer, or a runaway that burned maxTokens and stopped) have no
    # corresponding time in the denominator.
    #
    # Measured on results/dsh-gemma131k-04, which is exactly that case: of
    # 17791 output tokens, 8192 -- precisely maxTokens -- came from a final
    # step that emitted no call. Dividing all of them by 102.9s gives
    # 172.9 t/s from a server whose own logs report 96-97 t/s for that run.
    # Using only the 9599 call-producing tokens gives 93.3 t/s, which agrees
    # with the server. A rate faster than the hardware is the tell.
    out = hm.get('generationOutputTokens')
    if not isinstance(gen, (int, float)) or gen <= 0:
        return None
    if not isinstance(out, (int, float)) or out <= 0:
        return None
    return round(out / gen, 2)


def build_events_summary(result_dir, label, harness, model):
    """The committed, backfillable record issue #7 asks for: the raw traces
    stay gitignored (they carry agent-written solution code), but the
    metrics derived from them do not have to live only inside score.json,
    which the 16 pre-existing runs can never regrow a trace to regenerate.

    Returns None if there is no trace to summarise -- callers must not write
    a file in that case, or a missing trace would silently look like a
    zero-call run.
    """
    calls, run_start_ms, resolved_harness = _load_calls(result_dir, harness)
    if calls is None:
        return None
    return {
        'schemaVersion': EVENTS_SUMMARY_SCHEMA_VERSION,
        'label': label,
        'harness': harness,
        'model': model,
        **call_metrics(calls, run_start_ms=run_start_ms,
                       harness=resolved_harness),
    }


# --- issue #4: pi aborts fatally on one malformed llama-server response ---
#
# MODELS.md section 6 and FINAL-REPORT.md section 4.3 are the ONLY surviving
# evidence for this failure. The archived captures at
# ~/.cache/oaken-bench/schema-capture-{pi,dsh}/stderr.log are both 0 bytes --
# those two runs succeeded -- and the 11 aborts FINAL-REPORT 4.3 counted by
# hand came from stderr.log files that no longer exist (issue #7). So this
# pattern is reconstructed from that prose, NOT verified against a real
# captured abort. If a real one is ever captured, check this against it and
# correct the pattern/comment -- do not assume it is right just because it
# is here.
#
# The docs name exactly one signature: llama-server's `peg-native`
# parser/grammar, which pi relays to stderr verbatim (docker/entrypoint.sh
# redirects pi's stderr to stderr.log, so the capture path is sound even
# though no real sample exists to check the string against). FINAL-REPORT
# 4.3 is explicit that the failure originates in llama-server, not in
# either harness, and that dsh survived the identical responses -- so no
# distinct dsh-side error string is documented anywhere in this repo. Since
# both harnesses talk to the same llama-server and dsh's stderr is captured
# the same way (entrypoint.sh), this one pattern is applied to both rather
# than inventing a second, wholly unverified string for dsh. If dsh is ever
# observed logging its own distinct text for this failure, add it here.
PARSE_ERROR_PATTERN = re.compile(r'peg[-_]native', re.IGNORECASE)


def count_parse_errors(stderr_text):
    """How many times the malformed-tool-call signature appears in
    stderr.log. See PARSE_ERROR_PATTERN's comment above for the "derived
    from documentation, not matched against a real trace" caveat."""
    if not stderr_text:
        return 0
    return len(PARSE_ERROR_PATTERN.findall(stderr_text))


def resolve_parse_errors(stderr_present, stderr_text):
    """The harnessMetrics.parseErrors value for one run: null when
    stderr.log was never written (no evidence either way -- we did not
    look), 0 when it exists and the signature is absent (a real negative
    result: we looked and found none), else the match count.

    Split out from main() so the null-vs-0 boundary this repo cares about
    (see mutatingCalls in events.py, and HARNESS_METRICS_SCHEMA_VERSION's
    comment on what `usage`/`compactions` MEAN) is exercised directly by a
    test instead of only implicitly, through a full scoring run.
    """
    if not stderr_present:
        return None
    return count_parse_errors(stderr_text)


# MODELS.md section 6's advice: "Always check wallclock.seconds: anything
# under ~20s did not engage with the task." The issue's proposal says
# scripts/batch.sh already retries on this pattern and asks to share that
# detection rather than re-deriving it -- but batch.sh (and
# batch-gemma-ctx.sh, run.sh, docker/entrypoint.sh) were grepped for any
# wallclock/retry/threshold logic and none exists; only wallclock.seconds is
# read, and only to print it. There is nothing to share, so this constant is
# the first place the ~20s threshold is encoded anywhere in this repo.
NO_ENGAGEMENT_WALLCLOCK_SECONDS = 20


def classify(exit_code, visible_fail, hidden_rate, stderr_text, restored, hung=False,
            wall_seconds=None):
    if not restored:
        return 'crash'
    if hung:
        return 'hang_nonterminating_code'
    if exit_code == 124 or exit_code == 143:
        return 'timeout'
    low = (stderr_text or '').lower()
    if 'context overflow recovery failed' in low or 'context_window_exceeded' in low:
        return 'context_exhausted'
    # Placed ahead of the exit!=0 crash branch below, per the issue: pi
    # EXITS 0 on this abort (FINAL-REPORT 4.3), so a branch placed after an
    # exit-code check would never fire for the case it exists to catch.
    # Checked against both exit 0 and nonzero, since the signature is more
    # informative than a generic crash either way.
    if count_parse_errors(stderr_text) > 0:
        return 'malformed_tool_calls'
    # The short-wallclock guard MODELS.md section 6 gives as advice, now
    # code instead of prose. Placed after the more specific signals above
    # (a parse-error abort explains WHY better than a generic "too fast"
    # would), but still ahead of the exit!=0 catch-all and the hidden-rate
    # branches below: a run that exited in a few seconds without engaging
    # is mischaracterized by both 'crash' (implies something broke mid
    # attempt) and by the hidden-rate outcomes (imply a scored attempt
    # happened at all). It only ever applies to restored runs -- the
    # `not restored` branch above already claims everything else.
    if wall_seconds is not None and wall_seconds < NO_ENGAGEMENT_WALLCLOCK_SECONDS:
        return 'no_engagement'
    if exit_code != 0:
        return 'crash'
    if hidden_rate >= 0.80:
        return 'complete'
    if visible_fail > 0:
        return 'declared_done_tests_red'
    return 'incomplete_hidden_below_bar'


IMAGE = os.environ.get('OAKEN_IMAGE', 'oaken-bench:1.0')
HIDDEN_ENC = os.path.join(BENCH, 'hidden.tar.gz.enc')
HIDDEN_PASS = os.environ.get('OAKEN_HIDDEN_PASS', 'oaken-bench-held-out')


def score_in_container(result_dir, detail=False, timeout=1800):
    """Run both suites and the typecheck inside the runner image.

    The held-out suite is decrypted to a container-only path, so its plaintext
    never lands on the host filesystem and cannot be swept into a dataset. It
    also means a non-terminating test tree is reaped by container teardown
    rather than by signalling process groups from here.

    Returns (visible, hidden, typecheck_clean, tampered) or None if the
    container could not produce results.
    """
    if not os.path.exists(HIDDEN_ENC):
        raise SystemExit(f'missing {HIDDEN_ENC} -- see CANARY.md')
    out = tempfile.mkdtemp(prefix='scoreout-')
    cmd = [
        'docker', 'run', '--rm', '--network', 'none',
        '-v', f'{result_dir}:/in:ro',
        '-v', f'{os.path.dirname(HIDDEN_ENC)}:/enc:ro',
        '-v', f'{out}:/out',
        '-e', f'OAKEN_HIDDEN_PASS={HIDDEN_PASS}',
        '-e', f'HOST_UID={os.getuid()}', '-e', f'HOST_GID={os.getgid()}',
        IMAGE, 'score',
    ] + (['--detail'] if detail else [])
    rc, so, se = sh(cmd, timeout=timeout)

    def grab(name, default=None):
        p = os.path.join(out, name)
        if not os.path.exists(p):
            return default
        try:
            return json.load(open(p))
        except Exception:
            return default

    res = (grab('suite-visible.json'), grab('suite-hidden.json'),
           grab('typecheck.json', {}), grab('tampered.json', []))
    shutil.rmtree(out, ignore_errors=True)
    if res[0] is None and res[1] is None:
        sys.stderr.write(f'container scoring produced nothing (rc={rc})\n{se[-2000:]}\n')
    return res



def main():
    argv = sys.argv[1:]
    # Default on: hidden-detail.json never reaches a published field (see
    # the module docstring), so there is no cost to writing it, only to
    # NOT writing it -- issue #16 was exactly a run where the flag existed
    # but nothing ever set it, and the pair it would have distinguished
    # is now unrecoverable. --detail is accepted as a no-op for parity
    # with docker/entrypoint.sh's own flag of the same name.
    detail = '--no-detail' not in argv
    argv = [a for a in argv if a not in ('--detail', '--no-detail')]
    if len(argv) < 1:
        print(__doc__)
        sys.exit(2)
    result_dir = os.path.abspath(argv[0])
    label = os.path.basename(result_dir)

    def read(name, default=''):
        p = os.path.join(result_dir, name)
        return open(p, errors='replace').read().strip() if os.path.exists(p) else default

    meta = dict(kv.split('=', 1) for kv in read('run.meta').split() if '=' in kv)
    exit_code = int(read('exit.code', '-1') or -1)
    wall_raw = read('wallclock.seconds', '').strip()
    wall = int(wall_raw or 0)
    # `wall` keeps defaulting to 0 for the published wallclockSeconds field,
    # which is what the existing rows carry. But classify()'s no_engagement
    # branch must NOT see that 0: a run whose wallclock.seconds was never
    # written (an older run, or a container killed before run.sh got to it)
    # would then be classified "did not engage with the task" on the strength
    # of a missing file. Absent is not the same fact as fast -- the same
    # null-vs-zero distinction resolve_parse_errors() makes just below.
    wall_for_classify = wall if wall_raw else None
    # Existence, not just content: read()'s default ('') makes "the file is
    # empty" and "the file was never written" look identical, but they mean
    # different things for parseErrors below (see the null-vs-0 comment at
    # its assignment).
    stderr_present = os.path.exists(os.path.join(result_dir, 'stderr.log'))
    stderr_text = read('stderr.log')[-20000:]

    restored = os.path.exists(os.path.join(result_dir, 'workspace.tgz'))

    report = {
        'label': label, 'harness': meta.get('harness'), 'model': meta.get('model'),
        'exitCode': exit_code, 'wallclockSeconds': wall, 'restored': restored,
    }

    if restored:
        TOT = canonical_totals()
        vis, hid, tc, tampered = score_in_container(result_dir, detail=detail)
        vis = vis or {}
        hid = hid or {}
        # Pop, don't leave in place: 'detail' carries held-out test FILE
        # names (docker/scorer.sh), and report['hidden'] below is built by
        # pulling specific keys off `hid` rather than by copying it, but a
        # future edit to that pattern must not find this key still sitting
        # here to copy by accident.
        hidden_detail = hid.pop('detail', None)
        if hidden_detail is not None:
            with open(os.path.join(result_dir, 'hidden-detail.json'), 'w') as f:
                json.dump(hidden_detail, f, indent=2)
        report['tamperedFrozenFiles'] = tampered or []
        report['typecheckClean'] = bool((tc or {}).get('clean'))
        report['suiteHung'] = bool(vis.get('__hung')) or bool(hid.get('__hung'))
        vp, vt = vis.get('passed', 0), vis.get('total', 0)
        hp, ht = hid.get('passed', 0), hid.get('total', 0)
        report['visible'] = {'passed': vp, 'failed': vis.get('failed', 0),
                             'total': TOT['visible'], 'collected': vt,
                             'uncollected': TOT['visible'] - vt,
                             'rate': round(vp / TOT['visible'], 4) if TOT['visible'] else 0.0}
        report['hidden'] = {'passed': hp, 'failed': hid.get('failed', 0),
                            'total': TOT['hidden'], 'collected': ht,
                            'uncollected': TOT['hidden'] - ht,
                            'files': hid.get('files', 0),
                            'rate': round(hp / TOT['hidden'], 4) if TOT['hidden'] else 0.0}
        if hid.get('__error'):
            report['hidden']['error'] = hid['__error']
    else:
        report['visible'] = {'passed': 0, 'failed': 0, 'total': 0, 'rate': 0.0}
        report['hidden'] = {'passed': 0, 'failed': 0, 'total': 0, 'rate': 0.0}
        report['typecheckClean'] = False
        report['tamperedFrozenFiles'] = []

    harness = meta.get('harness')
    hm = pi_metrics(result_dir) or dsh_metrics(result_dir)
    events_summary = build_events_summary(result_dir, label, harness, meta.get('model'))
    if events_summary is not None:
        with open(os.path.join(result_dir, 'events-summary.json'), 'w') as f:
            json.dump(events_summary, f, indent=2)
        hm = merge_events_into_harness_metrics(hm, events_summary)
    if hm is not None:
        # null, not 0, when stderr.log was never written: 0 means "we
        # looked and found none" (a real result -- both 0-byte
        # schema-capture-{pi,dsh}/stderr.log archives are exactly this:
        # present, empty, genuinely no parse errors because those two runs
        # succeeded), while null means "we have no evidence either way".
        # mutatingCalls (events.py) draws the same line for its read-only
        # case, and HARNESS_METRICS_SCHEMA_VERSION's own comment treats
        # `usage`/`compactions`' meaning as worth this level of care -- a
        # bare 0 here would silently claim "no parse errors" for every run
        # that predates stderr.log capture, or whose container never wrote
        # one, which is exactly the kind of false negative issue #7 is
        # about.
        hm['parseErrors'] = resolve_parse_errors(stderr_present, stderr_text)
    report['harnessMetrics'] = hm
    report['outcome'] = classify(exit_code, report['visible']['failed'],
                                 report['hidden']['rate'], stderr_text, restored,
                                 report.get('suiteHung', False),
                                 wall_for_classify)
    report['overfitGap'] = round(report['visible']['rate'] - report['hidden']['rate'], 4)

    with open(os.path.join(result_dir, 'score.json'), 'w') as f:
        json.dump(report, f, indent=2)

    hm = report['harnessMetrics'] or {}
    print(f"=== {label} [{report['harness']}/{report['model']}] ===")
    print(f"  outcome        : {report['outcome']}")
    print(f"  hidden         : {report['hidden']['passed']}/{report['hidden']['total']} "
          f"({report['hidden']['rate']*100:.1f}%)   BAR = 80%")
    print(f"  visible        : {report['visible']['passed']}/{report['visible']['total']} "
          f"({report['visible']['rate']*100:.1f}%)")
    unc = report['hidden'].get('uncollected', 0) + report['visible'].get('uncollected', 0)
    if unc:
        print(f"  uncollected    : {report['hidden'].get('uncollected',0)} hidden + "
              f"{report['visible'].get('uncollected',0)} visible tests never ran "
              f"(module failed to import)")
    print(f"  overfit gap    : {report['overfitGap']*100:+.1f} pts")
    print(f"  typecheck      : {'clean' if report['typecheckClean'] else 'DIRTY'}")
    print(f"  tampered       : {report['tamperedFrozenFiles'] or 'none'}")
    print(f"  wallclock      : {wall}s ({wall/60:.1f} min)   exit={exit_code}")
    print(f"  turns/steps    : {hm.get('turns', hm.get('steps', '?'))}")
    print(f"  tool calls     : {hm.get('toolCalls', '?')}  "
          f"(mutating={hm.get('mutatingCalls', '?')}, errors={hm.get('toolOutcomes', {}).get('error', '?')})")
    print(f"  compactions    : {hm.get('compactions', '?')}")
    print(f"  usage          : {hm.get('usage', {})}")
    if hm.get('unknownTools'):
        # Silence here is exactly how an unrecognised-tool run would pass
        # for a normal one -- unknownTools already lands in mutatingCalls
        # (see events.py), but a reader scanning this table has to be told,
        # not left to notice the count is one higher than expected.
        print(f"  UNKNOWN TOOLS  : {hm['unknownTools']}")


if __name__ == '__main__':
    main()
