#!/usr/bin/env python3
"""Final comparison table across all scored runs."""
import json, os, statistics, sys

B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = os.path.join(B, 'results')

# The original 9-run matrix from README. This fixes row order so pi and dsh
# runs interleave predictably; it is no longer the list of runs shown -- see
# list_result_labels() -- just the preferred order for the ones it names.
ORDER = ['ceiling-claude', 'pi-01', 'pi-02', 'pi-03', 'dsh-01', 'dsh-02', 'dsh-03']

# README limitation 4: the dsh-gemma* runs were the only set produced
# end-to-end under the current (containerized) scoring pipeline. Everything
# else is historical. Mixing the two into one mean would be misleading, so
# aggregates are grouped by this label rather than by harness alone.
#
# Keyed on the ctx marker rather than on `dsh-gemma`, because that prefix
# encoded an accident of who had been run at the time, not a property of
# the pipeline: a pi run at the same context would have been dropped into
# "historical" purely for not being dsh.
GEMMA_GROUP = 'gemma 131k/192k (current pipeline)'
HISTORICAL_GROUP = 'historical (pre-container pipeline)'
CURRENT_PIPELINE_MARKERS = ('gemma131k', 'gemma192k')

VOID_REASON = 'VOID run (thinkbug) -- excluded from every aggregate'


def list_result_labels(results_dir):
    """Every run with a score.json, in ORDER's order for the runs it names,
    then everything else appended afterward, sorted."""
    all_labels = sorted(
        name for name in os.listdir(results_dir)
        if os.path.exists(os.path.join(results_dir, name, 'score.json'))
    )
    ordered = [label for label in ORDER if label in all_labels]
    rest = sorted(label for label in all_labels if label not in ORDER)
    return ordered + rest


def build_row(results_dir, label):
    p = os.path.join(results_dir, label, 'score.json')
    d = json.load(open(p))
    hm = d.get('harnessMetrics') or {}
    u = hm.get('usage') or {}
    return {
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
        # Absence of harnessMetricsVersion means version 1, the pre-#9/#10
        # extractor: `compactions` was double-counted on pi and matched the
        # word 'compact' in prose on dsh, and `usage` was summed from
        # streaming partials. Those runs' traces are gone, so the figures
        # cannot be recomputed -- they can only be shown as untrustworthy.
        'hmv': hm.get('harnessMetricsVersion', 1),
        'inp': u.get('input', u.get('inputTokens', 0)),
        'out': u.get('output', u.get('outputTokens', 0)),
        'cache': u.get('cacheRead', u.get('cacheReadTokens', 0)),
    }


def collect_rows(results_dir):
    """Ordered list of row dicts for every scored run in results_dir."""
    return [build_row(results_dir, label) for label in list_result_labels(results_dir)]


def exclusion_reason(row):
    """None if row belongs in an aggregate, else a string reason it doesn't.

    Both exclusions are deliberate, and issue #12's complaint was about a run
    leaving the table with no comment -- so neither may be a silent drop.
    ceiling-claude is scored by the same pipeline but its harness is
    'claude-code': it is the reference ceiling the local runs are measured
    against, not a harness under test, and averaging it in would flatter
    whichever column it landed in.
    """
    if 'VOID' in row['label']:
        return VOID_REASON
    if row['harness'] not in ('pi', 'dsh'):
        return f"harness {row['harness']!r} -- reference ceiling, not a harness under test"
    return None


def group_label(row):
    """Which README-comparable set this row's run belongs to, for aggregate
    grouping. ceiling-claude's harness is 'claude-code', not pi/dsh, so it
    is excluded upstream by exclusion_reason() and never reaches a group.

    NOTE the marker is the context size, not the harness: `dsh-gemma131k-02`
    and `pi-gemma131k-01` belong to the same set. What it does NOT capture is
    the llama-server configuration a run was made under -- MODELS.md section
    4 records that `--reasoning-format` changes this model's behaviour enough
    to break one harness outright, and nothing in score.json says which
    setting was in force. Read `harnessMetricsVersion` (2 = scored after that
    was discovered) alongside this grouping, not instead of it.
    """
    if any(marker in row['label'] for marker in CURRENT_PIPELINE_MARKERS):
        return GEMMA_GROUP
    return HISTORICAL_GROUP


def aggregate_groups(rows):
    """Group non-excluded rows by (harness, README-comparable set), each
    tagged with a human-readable label naming what it covers. Returns
    (groups, excluded) where groups is a list of dicts with 'harness',
    'group_label', 'rows', and excluded is a list of (row, reason).
    """
    excluded = []
    included = []
    for r in rows:
        reason = exclusion_reason(r)
        if reason:
            excluded.append((r, reason))
        else:
            included.append(r)

    groups_by_key = {}
    order = []
    for r in included:
        key = (r['harness'], group_label(r))
        if key not in groups_by_key:
            groups_by_key[key] = []
            order.append(key)
        groups_by_key[key].append(r)

    groups = [
        {'harness': h, 'group_label': lbl, 'rows': groups_by_key[(h, lbl)]}
        for (h, lbl) in order
    ]
    return groups, excluded


def format_row(r):
    lines = []
    mut = '?' if r['mut'] is None else str(r['mut'])
    if r['mut'] == 0 and r['tools']:
        mut += ' !!'
    # A '~' marks a compaction count produced by the version-1 extractor:
    # overstated by an unknown factor that differs between the harnesses,
    # so pi's and dsh's cmp columns are not comparable on those rows.
    comp = f"{r['comp']}~" if r['hmv'] < 2 else str(r['comp'])
    lines.append(
        f"{r['label']:<26} {r['outcome']:<26} "
        f"{r['hid']:>4}/132 {r['hidr']*100:>5.1f}% "
        f"{r['vis']:>3}/52 {r['visr']*100:>5.1f}% "
        f"{r['gap']*100:>+6.1f} "
        f"{'ok' if r['tc'] else 'X':>3} {r['wall']:>5}s {r['turns']:>6} {r['tools']:>6} "
        f"{mut:>6} {comp:>5}"
    )
    if r['unknown']:
        # A run full of unrecognised tool names must not read as a normal
        # row -- see events.py's KNOWN_TOOLS comment. Unknown tools are
        # named here for visibility; they are not folded into mutatingCalls,
        # which counts edit/write calls only.
        lines.append(f"{'':<26} unknown tools: {r['unknown']}")
    return '\n'.join(lines)


def main():
    rows = collect_rows(R)

    header = (f"{'run':<26} {'outcome':<26} {'hidden':>12} {'visible':>11} {'gap':>7} "
              f"{'tc':>3} {'wall':>6} {'turns':>6} {'tools':>6} {'mut':>6} {'cmp':>5}")
    print(header)
    print('-' * len(header))
    for r in rows:
        print(format_row(r))

    groups, excluded = aggregate_groups(rows)

    print()
    for g in groups:
        h, lbl, grows = g['harness'], g['group_label'], g['rows']
        hr = [r['hidr'] * 100 for r in grows]
        print(f"{h:>4} [{lbl}]: hidden mean {statistics.mean(hr):5.1f}%  "
              f"spread {min(hr):.1f}-{max(hr):.1f}  "
              f"best {max(hr):.1f}%  "
              f"tools {sum(r['tools'] for r in grows)}  "
              f"compactions {sum(r['comp'] for r in grows)}"
              f"{'~' if any(r['hmv'] < 2 for r in grows) else ''}  "
              f"outcomes: {', '.join(sorted(set(r['outcome'] for r in grows)))}")

    if excluded:
        print()
        print('excluded from every aggregate:')
        for r, reason in excluded:
            print(f"  {r['label']:<26} {reason}")

    # Three of this table's columns can carry a symbol rather than a plain
    # number, and each means "this figure is not what it looks like". A
    # reader who does not know that will read the marked rows as sound.
    print()
    print("mut '?'  mutatingCalls not recorded for this run (pre-#3), "
          "NOT a confirmed zero")
    print("mut '!!' zero writes against a nonzero tool count -- the run "
          "never touched the seed")
    print("cmp '~'  counted by the version-1 extractor (pre-#10): "
          "overstated, and by a different factor on each harness")

    print(f"\nbar = 80% hidden.  runs clearing it: "
          f"{[r['label'] for r in rows if r['hidr'] >= 0.8] or 'none of the Gemma runs'}")


if __name__ == '__main__':
    main()
