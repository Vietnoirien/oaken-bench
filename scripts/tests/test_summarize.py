"""Tests for scripts/summarize.py (issue #12).

Fixtures are synthetic score.json trees built under tmp_path -- never real
data from the repo's results/ directory. Real run data belongs in results/,
not committed as a test fixture. The one exception is the golden-output
test at the bottom of this file (issue #36): it deliberately runs against
the repo's own committed results/, because its whole point is to pin down
that tiering summarize.py did not change what those runs' summary says.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from summarize import (  # noqa: E402
    ORDER, aggregate_groups, build_row, collect_rows, exclusion_reason,
    format_row, list_result_labels, rows_by_tier,
)

BENCH_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testdata',
                      'summarize_golden.txt')
GOLDEN_PRE_TIERING = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'testdata',
    'summarize_golden_pre_tiering.txt')


def write_score(results_dir, label, harness='dsh', outcome='complete',
                 hidden_passed=10, hidden_total=132, visible_passed=5,
                 visible_total=52, overfit_gap=0.0, typecheck_clean=True,
                 wallclock=100, mutating_calls=None, unknown_tools=None,
                 tool_calls=0, turns=0, compactions=0):
    """Write a minimal synthetic score.json for `label` under results_dir."""
    run_dir = results_dir / label
    run_dir.mkdir(parents=True, exist_ok=True)
    harness_metrics = {
        'toolCalls': tool_calls,
        'turns': turns,
        'compactions': compactions,
        'usage': {},
    }
    if mutating_calls is not None:
        harness_metrics['mutatingCalls'] = mutating_calls
    if unknown_tools is not None:
        harness_metrics['unknownTools'] = unknown_tools
    data = {
        'harness': harness,
        'outcome': outcome,
        'hidden': {'passed': hidden_passed, 'total': hidden_total,
                   'rate': hidden_passed / hidden_total},
        'visible': {'passed': visible_passed, 'total': visible_total,
                    'rate': visible_passed / visible_total},
        'overfitGap': overfit_gap,
        'typecheckClean': typecheck_clean,
        'wallclockSeconds': wallclock,
        'harnessMetrics': harness_metrics,
    }
    (run_dir / 'score.json').write_text(json.dumps(data))
    return run_dir


# ---------------------------------------------------------------------------
# list_result_labels / collect_rows: ordering and coverage
# ---------------------------------------------------------------------------

def test_run_not_in_order_still_appears(tmp_path):
    write_score(tmp_path, 'dsh-01')
    write_score(tmp_path, 'dsh-gemma131k-01')

    labels = list_result_labels(str(tmp_path))

    assert 'dsh-gemma131k-01' in labels


def test_order_runs_keep_relative_order_and_come_first(tmp_path):
    # Write in an order deliberately scrambled relative to ORDER, plus
    # some extras not in ORDER at all.
    for label in ['dsh-03', 'zzz-extra', 'pi-01', 'aaa-extra',
                  'ceiling-claude', 'dsh-01', 'pi-03', 'dsh-02', 'pi-02']:
        write_score(tmp_path, label)

    labels = list_result_labels(str(tmp_path))

    order_present = [l for l in labels if l in ORDER]
    assert order_present == ORDER  # relative order preserved
    assert labels[:len(ORDER)] == ORDER  # and they come first
    # everything else appended after, sorted
    assert labels[len(ORDER):] == ['aaa-extra', 'zzz-extra']


def test_collect_rows_covers_every_scored_run(tmp_path):
    labels = ['dsh-01', 'dsh-gemma131k-01', 'pi-qwen32k-01', 'dsh-q48kB-01']
    for label in labels:
        write_score(tmp_path, label, harness='pi' if label.startswith('pi') else 'dsh')

    rows = collect_rows(str(tmp_path))

    assert {r['label'] for r in rows} == set(labels)


def test_run_without_score_json_is_ignored(tmp_path):
    write_score(tmp_path, 'dsh-01')
    (tmp_path / 'no-score-run').mkdir()

    labels = list_result_labels(str(tmp_path))

    assert labels == ['dsh-01']


# ---------------------------------------------------------------------------
# VOID exclusion
# ---------------------------------------------------------------------------

def test_void_run_is_excluded_from_every_mean(tmp_path):
    write_score(tmp_path, 'dsh-qwen32k-01', harness='dsh',
                hidden_passed=100, hidden_total=132)
    write_score(tmp_path, 'dsh-qwen32k-VOID-thinkbug', harness='dsh',
                hidden_passed=0, hidden_total=132)
    write_score(tmp_path, 'pi-qwen32k-VOID-thinkbug', harness='pi',
                hidden_passed=0, hidden_total=132)

    rows = collect_rows(str(tmp_path))
    groups, excluded = aggregate_groups(rows)

    excluded_labels = {r['label'] for r, _reason in excluded}
    assert excluded_labels == {'dsh-qwen32k-VOID-thinkbug', 'pi-qwen32k-VOID-thinkbug'}
    for r, reason in excluded:
        assert 'VOID' in reason

    for group in groups:
        group_labels = {r['label'] for r in group['rows']}
        assert not (group_labels & excluded_labels)


def test_void_run_is_still_visible_in_collect_rows(tmp_path):
    write_score(tmp_path, 'dsh-qwen32k-VOID-thinkbug', harness='dsh')

    rows = collect_rows(str(tmp_path))

    assert any(r['label'] == 'dsh-qwen32k-VOID-thinkbug' for r in rows)


def test_exclusion_reason_is_none_for_a_normal_run(tmp_path):
    row = build_row(str(write_score(tmp_path, 'dsh-01').parent), 'dsh-01')
    assert exclusion_reason(row) is None


# ---------------------------------------------------------------------------
# Aggregate grouping: labeled, not one mean over everything
# ---------------------------------------------------------------------------

def test_aggregate_groups_are_labeled_with_the_set_they_cover(tmp_path):
    write_score(tmp_path, 'dsh-01', harness='dsh')
    write_score(tmp_path, 'dsh-gemma131k-01', harness='dsh')

    rows = collect_rows(str(tmp_path))
    groups, _excluded = aggregate_groups(rows)

    assert len(groups) == 2
    labels = {g['group_label'] for g in groups}
    assert len(labels) == 2  # each group names a distinct set
    for g in groups:
        assert g['group_label']  # non-empty: every mean says what it covers


def test_gemma_and_historical_runs_are_not_mixed_into_one_mean(tmp_path):
    write_score(tmp_path, 'dsh-01', harness='dsh', hidden_passed=0, hidden_total=132)
    write_score(tmp_path, 'dsh-gemma131k-01', harness='dsh', hidden_passed=132, hidden_total=132)

    rows = collect_rows(str(tmp_path))
    groups, _excluded = aggregate_groups(rows)

    dsh_groups = [g for g in groups if g['harness'] == 'dsh']
    assert len(dsh_groups) == 2  # historical and gemma stay separate
    for g in dsh_groups:
        # each group's rows are internally consistent with its own mean,
        # i.e. no group contains both the 0% and the 100% run
        rates = {r['hid'] for r in g['rows']}
        assert rates in ({0}, {132})


def test_group_only_contains_matching_harness(tmp_path):
    write_score(tmp_path, 'dsh-01', harness='dsh')
    write_score(tmp_path, 'pi-01', harness='pi')

    rows = collect_rows(str(tmp_path))
    groups, _excluded = aggregate_groups(rows)

    for g in groups:
        assert all(r['harness'] == g['harness'] for r in g['rows'])


def test_non_pi_dsh_harness_is_excluded_with_a_stated_reason(tmp_path):
    """ceiling-claude must not be averaged into pi or dsh -- but issue #12 is
    about runs leaving the table without comment, so it must be reported as
    excluded rather than silently skipped."""
    write_score(tmp_path, 'ceiling-claude', harness='claude-code')

    rows = collect_rows(str(tmp_path))
    groups, excluded = aggregate_groups(rows)

    assert groups == []
    assert [r['label'] for r, _ in excluded] == ['ceiling-claude']
    assert 'claude-code' in excluded[0][1]


# ---------------------------------------------------------------------------
# mutatingCalls '?' preservation (still exercised at the row-building seam)
# ---------------------------------------------------------------------------

def test_missing_mutating_calls_reads_as_unknown_not_zero(tmp_path):
    write_score(tmp_path, 'dsh-01', harness='dsh', mutating_calls=None)

    row = build_row(str(tmp_path), 'dsh-01')

    assert row['mut'] is None


def test_present_mutating_calls_is_preserved(tmp_path):
    write_score(tmp_path, 'dsh-01', harness='dsh', mutating_calls=0, tool_calls=5)

    row = build_row(str(tmp_path), 'dsh-01')

    assert row['mut'] == 0


# ---------------------------------------------------------------------------
# harnessMetricsVersion: version-1 compaction counts must not read as sound
# ---------------------------------------------------------------------------

def test_absent_harness_metrics_version_reads_as_version_one(tmp_path):
    """The 16 pre-fix runs carry no harnessMetricsVersion key at all.
    Absence is version 1, not 'unversioned and therefore fine'."""
    write_score(tmp_path, 'dsh-01', harness='dsh', compactions=25)

    row = build_row(str(tmp_path), 'dsh-01')

    assert row['hmv'] == 1
    assert '25~' in format_row(row)


def test_version_two_compactions_print_unmarked(tmp_path):
    run_dir = write_score(tmp_path, 'dsh-01', harness='dsh', compactions=2)
    data = json.loads((run_dir / 'score.json').read_text())
    data['harnessMetrics']['harnessMetricsVersion'] = 2
    (run_dir / 'score.json').write_text(json.dumps(data))

    row = build_row(str(tmp_path), 'dsh-01')

    assert row['hmv'] == 2
    assert '~' not in format_row(row)


def test_a_pi_run_at_the_same_context_joins_the_same_group_as_dsh():
    """The group marker is the context size, not the harness prefix. Keying
    on 'dsh-gemma' put a pi run at the identical context into 'historical'
    purely for not being dsh -- an accident of who had been run, not a
    property of the pipeline."""
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        tmp_path = pathlib.Path(td)
        write_score(tmp_path, 'dsh-gemma131k-01', harness='dsh')
        write_score(tmp_path, 'pi-gemma131k-01', harness='pi')
        write_score(tmp_path, 'pi-01', harness='pi')

        rows = collect_rows(str(tmp_path))
        groups, _ = aggregate_groups(rows)

    by_label = {r['label']: g['group_label']
                for g in groups for r in g['rows']}
    assert by_label['pi-gemma131k-01'] == by_label['dsh-gemma131k-01']
    assert by_label['pi-01'] != by_label['pi-gemma131k-01']


def test_dual_gpu_qwen_runs_do_not_join_the_historical_mean(tmp_path):
    """Anything without a known marker falls through to 'historical'. The
    first Qwen3.6-35B-A3B runs on 5070+3060 did exactly that, and moved the
    historical pi mean from 17.2 % to 12.9 % -- a different model on
    different hardware, silently averaged into a set it has nothing to do
    with."""
    write_score(tmp_path, 'pi-qwen35q4ks-01', harness='pi')
    write_score(tmp_path, 'dsh-qwen35q4ks-01', harness='dsh')
    write_score(tmp_path, 'pi-01', harness='pi')
    write_score(tmp_path, 'pi-gemma131k-01', harness='pi')

    groups, _ = aggregate_groups(collect_rows(str(tmp_path)))
    by_label = {r['label']: g['group_label']
                for g in groups for r in g['rows']}

    assert by_label['pi-qwen35q4ks-01'] == by_label['dsh-qwen35q4ks-01']
    assert by_label['pi-qwen35q4ks-01'] != by_label['pi-01']
    assert by_label['pi-qwen35q4ks-01'] != by_label['pi-gemma131k-01']


def test_runs_on_a_newer_llama_cpp_build_do_not_pool_with_the_old_one(tmp_path):
    """b9716 aborts this model's pi stream on llama.cpp #24807 and b10751 does
    not, so the same model, quant and layout are two different measurements
    across that line. The newer label also contains the older marker, which
    is exactly how it would have slipped in."""
    write_score(tmp_path, 'pi-qwen35q4ks-01', harness='pi')
    write_score(tmp_path, 'pi-qwen35q4ks-b10751-01', harness='pi')

    groups, _ = aggregate_groups(collect_rows(str(tmp_path)))
    by_label = {r['label']: g['group_label']
                for g in groups for r in g['rows']}

    assert by_label['pi-qwen35q4ks-b10751-01'] != by_label['pi-qwen35q4ks-01']


def test_each_two_card_model_gets_its_own_group(tmp_path):
    """Three models share the two-card layout and llama.cpp b10751. A mean
    across them would average models, which is not a thing this benchmark
    measures; each label marker keeps its own group."""
    for label, h in [('pi-oss20b-01', 'pi'), ('dsh-oss20b-01', 'dsh'),
                     ('pi-glm47flash-01', 'pi'), ('dsh-glm47flash-01', 'dsh'),
                     ('pi-qwen35q4ks-b10751-01', 'pi'), ('pi-01', 'pi')]:
        write_score(tmp_path, label, harness=h)

    groups, _ = aggregate_groups(collect_rows(str(tmp_path)))
    by_label = {r['label']: g['group_label']
                for g in groups for r in g['rows']}

    assert by_label['pi-oss20b-01'] == by_label['dsh-oss20b-01']
    assert by_label['pi-glm47flash-01'] == by_label['dsh-glm47flash-01']
    assert len({by_label['pi-oss20b-01'], by_label['pi-glm47flash-01'],
                by_label['pi-qwen35q4ks-b10751-01'], by_label['pi-01']}) == 4


def test_the_262k_q4_kv_qwen_runs_do_not_pool_with_the_131k_q8_ones(tmp_path):
    """Same weights, same cards, same build -- but a doubled window and a
    halved KV precision. Two variables apart is not one set."""
    write_score(tmp_path, 'pi-qwen35kvq4-262k-01', harness='pi')
    write_score(tmp_path, 'pi-qwen35q4ks-b10751-01', harness='pi')
    write_score(tmp_path, 'pi-01', harness='pi')

    groups, _ = aggregate_groups(collect_rows(str(tmp_path)))
    by_label = {r['label']: g['group_label']
                for g in groups for r in g['rows']}

    assert by_label['pi-qwen35kvq4-262k-01'] != by_label['pi-qwen35q4ks-b10751-01']
    # and not the fall-through either, which is where an unknown label lands
    assert by_label['pi-qwen35kvq4-262k-01'] != by_label['pi-01']


# ---------------------------------------------------------------------------
# Golden output: issue #36's acceptance criterion that tiering does not
# change what the committed runs' summary says, apart from a T2 label.
# ---------------------------------------------------------------------------

def _run_summarize():
    """The real CLI, as a subprocess against the repo's own results/ --
    not summarize.main() in-process, so this also catches an import-time
    break (e.g. a circular import with tiers.py) that capsys would hide."""
    proc = subprocess.run(
        [sys.executable, os.path.join(BENCH_ROOT, 'scripts', 'summarize.py')],
        cwd=BENCH_ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_committed_results_output_matches_golden_fixture():
    """Regenerate scripts/tests/testdata/summarize_golden.txt (via
    `python3 scripts/summarize.py > scripts/tests/testdata/summarize_golden.txt`)
    whenever a new run is legitimately committed to results/ -- that is the
    one expected reason this test's fixture goes stale. Any OTHER diff
    means summarize.py changed what an already-committed run's row says,
    which issue #36 explicitly rules out.
    """
    actual = _run_summarize()
    expected = open(GOLDEN).read()
    assert actual == expected


def test_the_only_diff_from_pre_tiering_output_is_the_t2_heading():
    """summarize_golden_pre_tiering.txt was captured from this same
    results/ tree on the commit immediately before tiering landed. Tiering
    must add exactly one line -- the T2 heading -- and change nothing else:
    same rows, same aggregates, same excluded list, same legend, same bar
    line.
    """
    # The T5 block is new; the original T2 block must still match exactly.
    actual_lines = _run_summarize().split('T5 -- sealed snapshot-pool strategy')[0].splitlines()
    pre_lines = open(GOLDEN_PRE_TIERING).read().splitlines()

    added = [l for l in actual_lines if l not in pre_lines]
    removed = [l for l in pre_lines if l not in actual_lines]

    assert removed == []
    assert added == ['=== T2 -- long-horizon greenfield (the original task) ===']


def test_rows_by_tier_keeps_t5_apart_from_t2():
    """The T5 score cannot enter a T2 aggregate."""
    from summarize import R, collect_rows

    rows = collect_rows(R)
    grouped = rows_by_tier(rows)

    assert len(grouped) == 2
    tier_id, trows = grouped[0]
    assert tier_id == 't2'
    assert len(trows) == len(rows) - 1
    assert grouped[1][0] == 't5'
    assert [row['label'] for row in grouped[1][1]] == ['t5-cheapest-01']
