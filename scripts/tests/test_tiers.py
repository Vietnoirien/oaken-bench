"""Tests for scripts/tiers.py (issue #36).

The registry's job: resolve a results/ label to exactly one tier, never
fall an unregistered tier prefix through to T2 by accident, and make it
possible to prove -- structurally, not just by eyeballing summarize.py's
output -- that no cross-tier aggregate exists.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tiers import TIERS, resolve_tier, tier_by_id  # noqa: E402


# ---------------------------------------------------------------------------
# resolve_tier(): un-prefixed labels are T2, by definition
# ---------------------------------------------------------------------------

def test_unprefixed_label_resolves_to_t2():
    """Every one of the runs committed before tiering existed -- dsh-01,
    pi-qwen35q4ks-b10751-02, ceiling-claude, ... -- has no t<N>- prefix.
    They are T2 by definition (issue #36) and must resolve there, not to
    some generic 'unknown' bucket."""
    for label in ('dsh-01', 'pi-qwen35q4ks-b10751-02', 'ceiling-claude',
                  'dsh-qwen32k-VOID-thinkbug'):
        assert resolve_tier(label).id == 't2'


def test_registered_tiers():
    """Issue #36's brief: T2 is the only registered tier today; T1/T3/T4/T5
    register later, each as their own ticket. A test pinning this count
    isn't asserting a permanent fact -- it's a tripwire so the day a new
    Tier() lands, whoever adds it notices this test and updates it
    deliberately, rather than the registry silently growing."""
    assert [t.id for t in TIERS] == ['t2', 't4']


def test_only_one_tier_may_claim_the_unprefixed_fallback():
    unprefixed = [t for t in TIERS if t.dir_prefix is None]
    assert len(unprefixed) == 1
    assert unprefixed[0].id == 't2'


# ---------------------------------------------------------------------------
# resolve_tier(): a prefixed label with no matching registration
# ---------------------------------------------------------------------------

def test_tier_prefixed_label_for_an_unregistered_tier_raises():
    """A t3-foo label exists in the world (issue #36's own acceptance
    criterion asks for one) before T3 (#42) registers a Tier for it. That
    must fail loudly, not silently score as T2 -- a t3- run scored by T2's
    scorer would produce a score.json that LOOKS like a T2 result."""
    with pytest.raises(ValueError, match='t3-'):
        resolve_tier('t3-foo')


def test_error_names_the_missing_registration_not_just_the_label():
    with pytest.raises(ValueError, match='no Tier is registered'):
        resolve_tier('t5-bugfarm-01')


# ---------------------------------------------------------------------------
# tier_by_id()
# ---------------------------------------------------------------------------

def test_tier_by_id_finds_t2():
    assert tier_by_id('t2').dir_prefix is None


def test_tier_by_id_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        tier_by_id('t99')


# ---------------------------------------------------------------------------
# score.py's CLI dispatches through the registry, not through score_run()
# directly -- issue #36's second acceptance criterion: a results/t3-foo/
# directory is scored by ITS tier's scorer.
# ---------------------------------------------------------------------------

def test_score_cli_dispatches_a_t3_label_to_its_registered_scorer(tmp_path, monkeypatch):
    import tiers as tiers_module
    import score as score_module

    calls = []
    fake_tier = tiers_module.Tier(
        id='t3', label='T3 -- fixture', dir_prefix='t3-',
        score=lambda result_dir, detail=True: calls.append((result_dir, detail)))
    original_tiers = tiers_module.TIERS
    tiers_module.TIERS = original_tiers + (fake_tier,)
    tiers_module._BY_PREFIX['t3-'] = fake_tier
    tiers_module._BY_ID['t3'] = fake_tier

    result_dir = tmp_path / 't3-foo'
    result_dir.mkdir()

    monkeypatch.setattr(sys, 'argv', ['score.py', str(result_dir)])

    # score_run must NOT be called for a t3- label -- if it were, this
    # would score the run as T2 despite the t3- prefix. Poisoning it turns
    # that mistake into a hard failure instead of a silent misclassification.
    def _must_not_be_called(*a, **kw):
        raise AssertionError('score_run (T2) was called for a t3- label')
    monkeypatch.setattr(score_module, 'score_run', _must_not_be_called)

    try:
        score_module.main()
    finally:
        tiers_module.TIERS = original_tiers
        tiers_module._BY_PREFIX.pop('t3-', None)
        tiers_module._BY_ID.pop('t3', None)

    assert len(calls) == 1
    called_dir, called_detail = calls[0]
    assert os.path.basename(called_dir) == 't3-foo'
    assert called_detail is True  # --detail is the default


def test_score_cli_dispatches_an_unprefixed_label_to_t2(tmp_path, monkeypatch):
    """The flip side: a plain label still reaches score_run() -- the CLI's
    tier dispatch must not accidentally stop calling T2's scorer for the
    runs that ARE T2."""
    import score as score_module

    calls = []
    monkeypatch.setattr(score_module, 'score_run',
                        lambda result_dir, detail=True: calls.append(result_dir))

    result_dir = tmp_path / 'dsh-01'
    result_dir.mkdir()
    monkeypatch.setattr(sys, 'argv', ['score.py', str(result_dir)])

    score_module.main()

    assert len(calls) == 1
    assert os.path.basename(calls[0]) == 'dsh-01'


# ---------------------------------------------------------------------------
# No cross-tier total: the structural guarantee issue #36 asks a test for.
# ---------------------------------------------------------------------------

def test_no_cross_tier_total_exists_in_summarize(tmp_path):
    """Build score.json fixtures for two 'tiers' -- T2 (unprefixed) and a
    synthetic registered tier standing in for a future T3/T4/T5 -- and
    prove summarize.py's own grouping keeps them apart: no aggregate
    group's rows span both tiers, and rows_by_tier() never merges them
    into one bucket. This is what "tiers are never combined into one
    number" means structurally, not just as a visual habit in the
    printed table.
    """
    import tiers as tiers_module
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from summarize import aggregate_groups, build_row, rows_by_tier

    # Register a throwaway tier the way T3/T4/T5 eventually will, without
    # touching the real TIERS tuple other tests depend on.
    fake_tier = tiers_module.Tier(
        id='t9', label='T9 -- fixture-only tier', dir_prefix='t9-',
        score=lambda result_dir, detail=True: {})
    original_tiers = tiers_module.TIERS
    tiers_module.TIERS = original_tiers + (fake_tier,)
    tiers_module._BY_PREFIX['t9-'] = fake_tier
    tiers_module._BY_ID['t9'] = fake_tier
    try:
        def write_score(label, hidden_passed):
            run_dir = tmp_path / label
            run_dir.mkdir()
            import json
            (run_dir / 'score.json').write_text(json.dumps({
                'harness': 'dsh', 'outcome': 'complete',
                'hidden': {'passed': hidden_passed, 'total': 132,
                           'rate': hidden_passed / 132},
                'visible': {'passed': 0, 'total': 52, 'rate': 0.0},
                'overfitGap': 0.0, 'typecheckClean': True,
                'wallclockSeconds': 1, 'harnessMetrics': {'usage': {}},
            }))

        write_score('dsh-01', 10)              # T2 (unprefixed)
        write_score('t9-bugfarm-01', 100)       # fixture tier

        rows = [build_row(str(tmp_path), l)
                for l in ('dsh-01', 't9-bugfarm-01')]
        by_tier = dict(rows_by_tier(rows))

        assert set(by_tier) == {'t2', 't9'}
        assert {r['label'] for r in by_tier['t2']} == {'dsh-01'}
        assert {r['label'] for r in by_tier['t9']} == {'t9-bugfarm-01'}

        # And aggregate_groups(), run per-tier the way main() runs it,
        # never receives both tiers' rows at once.
        for _tier_id, trows in rows_by_tier(rows):
            groups, _excluded = aggregate_groups(trows)
            for g in groups:
                labels = {r['label'] for r in g['rows']}
                assert labels <= {r['label'] for r in trows}
    finally:
        tiers_module.TIERS = original_tiers
        tiers_module._BY_PREFIX.pop('t9-', None)
        tiers_module._BY_ID.pop('t9', None)
