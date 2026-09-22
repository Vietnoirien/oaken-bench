#!/usr/bin/env python3
"""Score one benchmark run.

  ./scripts/score.py results/<label>

Reconstructs the agent's workspace, runs the visible and held-out suites,
checks the frozen artefacts were not tampered with, and extracts harness
metrics. Held-out results are reported as COUNTS ONLY; per-test detail is
written to <label>/hidden-detail.json and is deliberately not printed.
"""
import json, os, re, shutil, signal, subprocess, sys, tarfile, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from events import (  # noqa: E402
    call_metrics, load_dsh_events, load_pi_events, normalize_calls,
)

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(BENCH, 'seed')
HIDDEN = os.path.join(BENCH, 'hidden')

# Bump only when a change to normalize_calls()/call_metrics() alters the
# shape of events-summary.json -- consumers pin to this, not to the harness
# versions in EVENTS.md, because the extractor can change independently.
EVENTS_SUMMARY_SCHEMA_VERSION = 1

# Exactly the call_metrics() fields allowed into the published score.json.
# Extending call_metrics() does not extend this: adding a field here is the
# deliberate act of publishing it (see merge_events_into_harness_metrics).
PUBLISHED_CALL_FIELDS = (
    'toolCalls', 'toolOutcomes', 'errorRate', 'errorsByTool', 'toolHistogram',
    'unknownTools', 'mutatingCalls', 'repeatedCalls', 'longestRepeatRun',
    'distinctCallRatio', 'callSequence',
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
    tools, compactions = [], 0
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
        if 'compact' in t.lower():
            compactions += 1
        if t == 'tool_execution_start':
            d = e.get('data') or {}
            n = (e.get('toolName') or e.get('name') or e.get('tool')
                 or d.get('toolName') or d.get('name'))
            if n:
                tools.append(n)
        u = e.get('usage') or (e.get('data') or {}).get('usage')
        if isinstance(u, dict):
            for k in usage:
                v = u.get(k)
                if isinstance(v, (int, float)):
                    usage[k] += v
    return {'events': counts, 'turns': counts.get('turn_start', 0),
            'toolCalls': len(tools), 'tools': tools,
            'usage': usage, 'compactions': compactions}


def dsh_metrics(result_dir):
    tgz = os.path.join(result_dir, 'dsh-sessions.tgz')
    if not os.path.exists(tgz):
        return None
    with tempfile.TemporaryDirectory() as td:
        with tarfile.open(tgz) as t:
            t.extractall(td, filter='data')
        sessions = []
        for root, _, files in os.walk(td):
            for fn in files:
                if fn.endswith('.jsonl.zstd'):
                    sessions.append(os.path.join(root, fn))
        if not sessions:
            return None
        sessions.sort(key=os.path.getmtime)
        rc, so, se = sh(['zstd', '-dc', sessions[-1]], timeout=120)
        counts, tools, compactions = {}, [], 0
        usage = {'inputTokens': 0, 'outputTokens': 0, 'cacheReadTokens': 0}
        for line in so.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            t = e.get('type', '?')
            counts[t] = counts.get(t, 0) + 1
            if 'compact' in json.dumps(e)[:2000].lower():
                compactions += 1
            d = e.get('data') or {}
            if t == 'tool/call':
                n = d.get('toolName') or d.get('name') or d.get('tool') or e.get('toolName')
                if n:
                    tools.append(n)
            u = d.get('usage') if isinstance(d.get('usage'), dict) else d
            if isinstance(u, dict):
                for k in usage:
                    v = u.get(k)
                    if isinstance(v, (int, float)):
                        usage[k] += v
        return {'events': counts, 'steps': counts.get('step/start', 0),
                'toolCalls': len(tools), 'tools': tools,
                'usage': usage, 'compactions': compactions}


# (harness, trace filename, loader). Order matters only as a tie-break for
# a result dir that somehow holds both.
TRACES = (
    ('pi', 'pi-events.jsonl', load_pi_events),
    ('dsh', 'dsh-sessions.tgz', load_dsh_events),
)


def _load_calls(result_dir, harness):
    """The one place score.py decides which raw trace file backs a run.

    `harness` comes from run.meta and is only a hint: it falls back to
    whichever trace file is actually present. Keying on it alone would mean
    a run with a missing or misspelt harness= line silently produces no
    events-summary.json at all -- a run that looks scored but carries none
    of #1-#3's fields, which is precisely the record loss issue #7 exists
    to stop. Returns None only when there is genuinely no trace to read.
    """
    for name, filename, loader in TRACES:
        if harness and harness != name:
            continue
        f = os.path.join(result_dir, filename)
        if os.path.exists(f):
            return normalize_calls(loader(f), name)
    if harness:                       # the named harness had no trace; try the rest
        return _load_calls(result_dir, None)
    return None


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
    return merged


def build_events_summary(result_dir, label, harness, model):
    """The committed, backfillable record issue #7 asks for: the raw traces
    stay gitignored (they carry agent-written solution code), but the
    metrics derived from them do not have to live only inside score.json,
    which the 16 pre-existing runs can never regrow a trace to regenerate.

    Returns None if there is no trace to summarise -- callers must not write
    a file in that case, or a missing trace would silently look like a
    zero-call run.
    """
    calls = _load_calls(result_dir, harness)
    if calls is None:
        return None
    return {
        'schemaVersion': EVENTS_SUMMARY_SCHEMA_VERSION,
        'label': label,
        'harness': harness,
        'model': model,
        **call_metrics(calls),
    }


def classify(exit_code, visible_fail, hidden_rate, stderr_text, restored, hung=False):
    if not restored:
        return 'crash'
    if hung:
        return 'hang_nonterminating_code'
    if exit_code == 124 or exit_code == 143:
        return 'timeout'
    low = (stderr_text or '').lower()
    if 'context overflow recovery failed' in low or 'context_window_exceeded' in low:
        return 'context_exhausted'
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
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    result_dir = os.path.abspath(sys.argv[1])
    label = os.path.basename(result_dir)

    def read(name, default=''):
        p = os.path.join(result_dir, name)
        return open(p, errors='replace').read().strip() if os.path.exists(p) else default

    meta = dict(kv.split('=', 1) for kv in read('run.meta').split() if '=' in kv)
    exit_code = int(read('exit.code', '-1') or -1)
    wall = int(read('wallclock.seconds', '0') or 0)
    stderr_text = read('stderr.log')[-20000:]

    restored = os.path.exists(os.path.join(result_dir, 'workspace.tgz'))

    report = {
        'label': label, 'harness': meta.get('harness'), 'model': meta.get('model'),
        'exitCode': exit_code, 'wallclockSeconds': wall, 'restored': restored,
    }

    if restored:
        TOT = canonical_totals()
        vis, hid, tc, tampered = score_in_container(result_dir)
        vis = vis or {}
        hid = hid or {}
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
    report['harnessMetrics'] = hm
    report['outcome'] = classify(exit_code, report['visible']['failed'],
                                 report['hidden']['rate'], stderr_text, restored,
                                 report.get('suiteHung', False))
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
