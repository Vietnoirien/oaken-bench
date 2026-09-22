"""Tests for score.py's classify() outcomes and parseErrors field (issue #4).

Malformed-tool-call detection ('peg-native' in stderr.log) is documented in
MODELS.md section 6 and FINAL-REPORT.md section 4.3 but is NOT verified
against a real captured trace: the archived schema-capture-{pi,dsh}
stderr.log files are both 0 bytes (those two runs succeeded), and the 11
aborts FINAL-REPORT counted by hand came from stderr.log files that no
longer exist (issue #7). Every stderr fixture below is therefore hand-written
prose reconstructing what the docs describe, never a real capture -- see
CANARY.md and test_events.py's docstring for why that boundary matters here.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from score import (  # noqa: E402
    NO_ENGAGEMENT_WALLCLOCK_SECONDS, count_parse_errors, classify,
    resolve_parse_errors,
)


# ---------------------------------------------------------------------------
# count_parse_errors()
# ---------------------------------------------------------------------------

def test_count_parse_errors_none_when_absent():
    assert count_parse_errors('') == 0
    assert count_parse_errors(None) == 0
    assert count_parse_errors('some unrelated warning about a socket timeout') == 0


def test_count_parse_errors_matches_peg_native_signature():
    # Hand-written prose reconstructing MODELS.md section 6's description
    # ("its `peg-native` parser cannot read") -- not a real captured line.
    text = 'fatal: llama-server response rejected by peg-native parser, aborting'
    assert count_parse_errors(text) == 1


def test_count_parse_errors_is_case_insensitive_and_counts_repeats():
    text = 'PEG-NATIVE parse failure\n...\nanother peg_native rejection later'
    assert count_parse_errors(text) == 2


# ---------------------------------------------------------------------------
# classify() ordering
# ---------------------------------------------------------------------------
# Signature: classify(exit_code, visible_fail, hidden_rate, stderr_text,
#                      restored, hung=False, wall_seconds=None)

def test_classify_not_restored_wins_over_everything():
    assert classify(0, 0, 1.0, 'peg-native parser error', restored=False,
                    hung=True, wall_seconds=1) == 'crash'


def test_classify_hung_wins_over_malformed_and_no_engagement():
    assert classify(-9, 0, 0.0, 'peg-native parser error', restored=True,
                    hung=True, wall_seconds=5) == 'hang_nonterminating_code'


def test_classify_timeout_exit_code():
    assert classify(124, 0, 0.0, '', restored=True, wall_seconds=1800) == 'timeout'
    assert classify(143, 0, 0.0, '', restored=True, wall_seconds=1800) == 'timeout'


def test_classify_context_exhausted_before_malformed_tool_calls():
    stderr = 'context overflow recovery failed\npeg-native parser error'
    assert classify(1, 0, 0.0, stderr, restored=True,
                    wall_seconds=100) == 'context_exhausted'


def test_classify_malformed_tool_calls_on_exit_0():
    # The documented case: pi aborts on the peg-native signature and EXITS
    # 0 (FINAL-REPORT 4.3). A branch placed after an exit!=0 check would
    # never see this -- that is exactly why malformed_tool_calls sits ahead
    # of the crash branch below.
    stderr = 'response rejected: peg-native parser could not parse tool call'
    assert classify(0, 0, 0.0, stderr, restored=True, wall_seconds=8) == 'malformed_tool_calls'


def test_classify_malformed_tool_calls_also_wins_on_nonzero_exit():
    stderr = 'response rejected: peg-native parser could not parse tool call'
    assert classify(1, 0, 0.0, stderr, restored=True, wall_seconds=8) == 'malformed_tool_calls'


def test_classify_malformed_tool_calls_ahead_of_crash_branch():
    # Same run as above but with a nonzero exit that would have been
    # 'crash' under the old chain -- confirms placement, not just presence.
    stderr = 'peg-native parser rejected the response'
    result = classify(1, 0, 0.0, stderr, restored=True, wall_seconds=8)
    assert result == 'malformed_tool_calls'
    assert result != 'crash'


def test_classify_no_engagement_short_wallclock_no_signature():
    # MODELS.md section 6: "anything under ~20s did not engage with the
    # task." No peg-native signature here -- a different, unexplained
    # reason the run ended fast.
    assert classify(1, 0, 0.0, '', restored=True, wall_seconds=5) == 'no_engagement'


def test_classify_no_engagement_even_on_exit_0():
    # Without the no_engagement branch this would fall through to
    # incomplete_hidden_below_bar, wrongly implying a scored attempt.
    assert classify(0, 0, 0.0, '', restored=True, wall_seconds=3) == 'no_engagement'


def test_classify_no_engagement_boundary_is_exclusive():
    at_threshold = NO_ENGAGEMENT_WALLCLOCK_SECONDS
    assert classify(1, 0, 0.0, '', restored=True,
                    wall_seconds=at_threshold) != 'no_engagement'
    assert classify(1, 0, 0.0, '', restored=True,
                    wall_seconds=at_threshold - 1) == 'no_engagement'


def test_classify_no_wall_seconds_never_triggers_no_engagement():
    # Backward-compatible default: a caller that doesn't pass wall_seconds
    # gets the pre-issue-#4 behaviour for this branch.
    assert classify(1, 0, 0.0, '', restored=True) == 'crash'


def test_classify_malformed_tool_calls_takes_priority_over_no_engagement():
    # Both conditions hold (short wallclock AND the signature); the more
    # specific diagnosis wins.
    stderr = 'peg-native parser rejected the response'
    assert classify(0, 0, 0.0, stderr, restored=True, wall_seconds=5) == 'malformed_tool_calls'


def test_classify_generic_crash_still_reachable():
    assert classify(1, 0, 0.0, 'segmentation fault', restored=True,
                    wall_seconds=100) == 'crash'


def test_classify_complete_unaffected_by_new_branches():
    assert classify(0, 0, 0.85, '', restored=True, wall_seconds=600) == 'complete'


def test_classify_declared_done_tests_red_unaffected():
    assert classify(0, 3, 0.10, '', restored=True, wall_seconds=600) == 'declared_done_tests_red'


def test_classify_incomplete_hidden_below_bar_unaffected():
    assert classify(0, 0, 0.10, '', restored=True, wall_seconds=600) == 'incomplete_hidden_below_bar'


# ---------------------------------------------------------------------------
# resolve_parse_errors(): null vs 0
# ---------------------------------------------------------------------------

def test_resolve_parse_errors_null_when_stderr_absent():
    # stderr.log was never written -- e.g. a run whose container never
    # produced one. We have no evidence either way, so this must NOT be 0.
    assert resolve_parse_errors(stderr_present=False, stderr_text='') is None


def test_resolve_parse_errors_zero_when_present_and_empty():
    # Exactly the two archived schema-capture-{pi,dsh}/stderr.log files:
    # present, 0 bytes, because those runs succeeded. We looked; there is
    # nothing there. That is a real result, distinct from "we did not look".
    assert resolve_parse_errors(stderr_present=True, stderr_text='') == 0


def test_resolve_parse_errors_zero_when_present_with_unrelated_content():
    assert resolve_parse_errors(stderr_present=True,
                                stderr_text='warning: retrying connection') == 0


def test_resolve_parse_errors_counts_when_present_with_signature():
    stderr = 'peg-native parser rejected the response'
    assert resolve_parse_errors(stderr_present=True, stderr_text=stderr) == 1


def test_missing_wallclock_is_not_treated_as_a_fast_exit():
    """score.py defaults a missing wallclock.seconds to 0 for the published
    wallclockSeconds field. classify() must not see that 0: `0 < 20` would
    label every run with no wallclock file as no_engagement, on the strength
    of an absent file rather than a measured duration."""
    assert classify(exit_code=0, visible_fail=0, hidden_rate=0.0, stderr_text='',
                    restored=True, wall_seconds=None) != 'no_engagement'
    # ...while a genuinely measured fast exit still is one.
    assert classify(exit_code=0, visible_fail=0, hidden_rate=0.0, stderr_text='',
                    restored=True, wall_seconds=3) == 'no_engagement'
