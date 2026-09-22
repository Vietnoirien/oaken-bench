#!/usr/bin/env python3
"""Final comparison table across all scored runs."""
import json, os, statistics, sys

B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = os.path.join(B, 'results')
ORDER = ['ceiling-claude', 'pi-01', 'pi-02', 'pi-03', 'dsh-01', 'dsh-02', 'dsh-03']

rows = []
for label in ORDER:
    p = os.path.join(R, label, 'score.json')
    if not os.path.exists(p):
        continue
    d = json.load(open(p))
    hm = d.get('harnessMetrics') or {}
    u = hm.get('usage') or {}
    rows.append({
        'label': label,
        'harness': d.get('harness'),
        'outcome': d.get('outcome'),
        'hid': d['hidden']['passed'], 'hidr': d['hidden']['rate'],
        'vis': d['visible']['passed'], 'visr': d['visible']['rate'],
        'gap': d.get('overfitGap', 0.0),
        'tc': d.get('typecheckClean'),
        'wall': d.get('wallclockSeconds', 0),
        'tools': hm.get('toolCalls', 0),
        # mutatingCalls/unknownTools are absent from score.json for runs
        # scored before this change -- 0 there must read as "not known",
        # not "confirmed zero writes" (issue #3's whole point).
        'mut': hm.get('mutatingCalls'),
        'unknown': hm.get('unknownTools') or [],
        'turns': hm.get('turns', hm.get('steps', 0)),
        'comp': hm.get('compactions', 0),
        'inp': u.get('input', u.get('inputTokens', 0)),
        'out': u.get('output', u.get('outputTokens', 0)),
        'cache': u.get('cacheRead', u.get('cacheReadTokens', 0)),
    })

print(f"{'run':<15} {'outcome':<26} {'hidden':>12} {'visible':>11} {'gap':>7} "
      f"{'tc':>3} {'wall':>6} {'turns':>6} {'tools':>6} {'mut':>6} {'cmp':>4}")
print('-' * 113)
for r in rows:
    # A bare tool-call total conflates "did nothing" with "worked hard" --
    # 24 calls/0 writes and 60 calls/75.8% hidden are not comparable on
    # that number alone (issue #3). mut='0' next to a nonzero tools count
    # is the thing that must be impossible to miss in this table; '?'
    # means the run predates mutatingCalls, not that it was actually zero.
    # One fixed-width token, flag included: a separate flag column would
    # shift `cmp` right on exactly the rows a reader is scanning for.
    mut = '?' if r['mut'] is None else str(r['mut'])
    if r['mut'] == 0 and r['tools']:
        mut += ' !!'
    print(f"{r['label']:<15} {r['outcome']:<26} "
          f"{r['hid']:>4}/132 {r['hidr']*100:>5.1f}% "
          f"{r['vis']:>3}/52 {r['visr']*100:>5.1f}% "
          f"{r['gap']*100:>+6.1f} "
          f"{'ok' if r['tc'] else 'X':>3} {r['wall']:>5}s {r['turns']:>6} {r['tools']:>6} "
          f"{mut:>6} {r['comp']:>4}")
    if r['unknown']:
        # A run full of unrecognised tool names must not read as a normal
        # row -- see events.py's KNOWN_TOOLS comment on why those calls are
        # folded into mutatingCalls but still need naming separately.
        print(f"{'':<15} unknown tools: {r['unknown']}")

print()
for h in ('pi', 'dsh'):
    g = [r for r in rows if r['harness'] == h]
    if not g:
        continue
    hr = [r['hidr'] * 100 for r in g]
    print(f"{h:>4}: hidden mean {statistics.mean(hr):5.1f}%  "
          f"spread {min(hr):.1f}-{max(hr):.1f}  "
          f"best {max(hr):.1f}%  "
          f"tools {sum(r['tools'] for r in g)}  "
          f"compactions {sum(r['comp'] for r in g)}  "
          f"outcomes: {', '.join(sorted(set(r['outcome'] for r in g)))}")
print(f"\nbar = 80% hidden.  runs clearing it: "
      f"{[r['label'] for r in rows if r['hidr'] >= 0.8] or 'none of the Gemma runs'}")
