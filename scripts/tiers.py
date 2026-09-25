#!/usr/bin/env python3
"""Tier registry (issue #26's ladder, issue #36's namespacing).

Every tier gets its own `results/` namespace, its own scorer, and its own
summary -- and #26 is explicit that tiers are **never combined into one
number**. This module is the one place that mapping lives, so a later tier
(T1 #46, T3 #42, T4 #44) registers itself by adding one `Tier(...)`
entry to TIERS below. Nothing outside this file should grow a
tier-by-tier if/else: `scripts/score.py` and `scripts/summarize.py` both
dispatch through `resolve_tier()`.

## The registry interface

A `Tier` is:

  id         short identifier used in aggregate keys and test assertions,
             e.g. 't2'. Never appears in a results/ directory name for T2
             specifically -- see `dir_prefix`.
  label      human-readable heading, printed by summarize.py above that
             tier's table so two tiers' numbers are never mistaken for one
             set even when skimmed.
  dir_prefix the `results/<prefix><label>/` prefix that identifies a run as
             this tier, e.g. 't3-' for `results/t3-foo/`. Exactly one tier
             may have `dir_prefix=None`: the un-prefixed fallback. T2 holds
             that slot today because the 23 runs committed before tiering
             existed have no prefix and must not be renamed (issue #36's
             acceptance criterion) -- so "no prefix" IS T2's identification
             rule, not a default for "unknown tier".
  score      `callable(result_dir, detail=True) -> dict`. Scores one run
             under `result_dir` (a `results/<label>` path), writes that
             tier's own `score.json` (and whatever else the tier needs --
             T2 also writes `events-summary.json` and `hidden-detail.json`),
             prints the same kind of summary `score.py`'s CLI has always
             printed, and returns the report dict for callers that want it
             without re-reading the file (tests, `summarize.py` never calls
             this -- it only reads the score.json the run already produced).
             T2's `score` is `scripts.score.score_run`, wired in below
             rather than reimplemented here.
  build_row  optional `callable(data, label, tier) -> dict` for a tier's
             distinct score schema. T2 uses summarize.py's original row.
  print_rows optional `callable(label, rows)` for that tier's summary. T2
             uses summarize.py's original table and aggregates.

`resolve_tier(label)` is the only function callers need: it decides a
result directory's tier from its name alone, by prefix. A label that starts
with `t<N>-` for an `<N>` with no registered tier is a mistake worth failing
loudly on (a typo'd tier prefix would otherwise silently fall through to
T2's un-prefixed rule and get scored by the wrong scorer) -- see
`resolve_tier`'s docstring below for exactly which labels raise.
"""
import collections
import re

Tier = collections.namedtuple('Tier',
    ['id', 'label', 'dir_prefix', 'score', 'build_row', 'print_rows'],
    defaults=[None, None])

# A results/ label claims tier N by starting with this. Matched before
# falling through to the None-prefix (T2) rule -- see resolve_tier().
_TIER_PREFIX_RE = re.compile(r'^t(\d+)-')


def _t2_score(result_dir, detail=True):
    """T2 (the original long-horizon greenfield task) delegates to
    score.py's own score_run() rather than duplicating it -- that function
    predates this registry and 23+ committed score.json files are its
    output. Imported lazily (not at module load) so `import tiers` alone
    never pulls in score.py's own heavier imports (subprocess, tarfile,
    docker invocations) for a caller that only wants resolve_tier()."""
    from score import score_run
    return score_run(result_dir, detail=detail)


def _t3_score(result_dir, detail=True):
    """T3 (planted bugs, issue #42) reuses T2's scorer outright. The
    workspace format (a workspace.tgz whose `work/` restores exactly the
    way score.restore_workspace() expects), the frozen-file check, and the
    oracle are all identical to T2 -- issue #26's plan is explicit that T3
    reuses the existing held-out suite rather than getting a new one. Only
    what the agent STARTS from differs (a buggy `scripts/planted_bugs.py`
    instance instead of seed/src's stubs), and that is entirely a fact
    about how the run was prepared, not about how it is scored. Lazily
    imported for the same reason tiers._t2_score imports score lazily:
    `import tiers` alone must not drag in score.py's subprocess/docker
    machinery."""
    from score import score_run
    return score_run(result_dir, detail=detail)


def _t4_score(result_dir, detail=True):
    from t4 import score_run
    return score_run(result_dir, detail=detail)


def _t4_build_row(data, label, tier):
    from t4 import summary_row
    return summary_row(data, label, tier)


def _t4_print_rows(label, rows):
    from t4 import print_summary
    return print_summary(label, rows)


def _t5_score(result_dir, detail=True):
    from t5_oracle import score_run
    return score_run(result_dir, detail=detail)


def _t5_build_row(data, label, tier):
    from t5_oracle import summary_row
    return summary_row(data, label, tier)


def _t5_print_rows(label, rows):
    from t5_oracle import print_summary
    return print_summary(label, rows)


TIERS = (
    Tier(id='t2',
         label='T2 -- long-horizon greenfield (the original task)',
         dir_prefix=None,
         score=_t2_score),
    Tier(id='t3',
         label='T3 -- planted bugs (fix a buggy reference engine, issue #42)',
         dir_prefix='t3-',
         score=_t3_score),
    Tier(id='t4', label='T4 -- v1.1 extension and v1.0 regressions',
         dir_prefix='t4-', score=_t4_score,
         build_row=_t4_build_row, print_rows=_t4_print_rows),
    Tier(id='t5', label='T5 -- sealed snapshot-pool strategy',
         dir_prefix='t5-', score=_t5_score,
         build_row=_t5_build_row, print_rows=_t5_print_rows),
    # T1 (#46), T4 (#44): add a Tier(...) here, each
    # with its own dir_prefix ('t1-', 't4-') and its own
    # score callable. Nothing else in this file, or in score.py /
    # summarize.py, needs to change.
)

_BY_PREFIX = {t.dir_prefix: t for t in TIERS if t.dir_prefix is not None}
_BY_ID = {t.id: t for t in TIERS}
_DEFAULT = next(t for t in TIERS if t.dir_prefix is None)


def resolve_tier(label):
    """Which registered Tier a `results/<label>` directory belongs to.

    Un-prefixed labels (every committed run to date, and any future T2 run)
    resolve to the default tier -- today that's T2, by construction (see
    the module docstring). A label matching `t<N>-` for a REGISTERED tier
    resolves to that tier. A label matching `t<N>-` for a tier that is not
    yet registered raises: that is a run for a ticket (#46/#42/#44/#40)
    that has not landed its Tier() entry yet, and scoring it as T2 by
    silent fallback would be worse than refusing -- it would produce a
    score.json that LOOKS like a T2 result.
    """
    m = _TIER_PREFIX_RE.match(label)
    if m:
        prefix = f't{m.group(1)}-'
        tier = _BY_PREFIX.get(prefix)
        if tier is None:
            raise ValueError(
                f"{label!r} looks like a tier-{m.group(1)} run (prefix "
                f"{prefix!r}) but no Tier is registered for it in "
                f"scripts/tiers.py -- add one before scoring this run")
        return tier
    return _DEFAULT


def tier_by_id(tier_id):
    """Look up a registered Tier by its `id` (e.g. for tests asserting on
    a specific tier without hardcoding its prefix or label text)."""
    return _BY_ID[tier_id]
