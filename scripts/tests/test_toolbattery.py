"""Tests for scripts/toolbattery.py (issue #8).

Two layers:

1. Pure-logic tests against hand-built `parsed`-shaped dicts -- no network,
   no server. These are the ones that must never regress and always run.
2. A handful of end-to-end tests against the real llama-server endpoint,
   gated behind `server_reachable()` the same way test_score_events.py
   gates its real-capture tests on `os.path.isdir(...)`: skipped, not
   failed, when nothing is listening. These never run in CI and are not
   part of the 94 (now more) tests the repo's README-adjacent tooling
   counts on to always pass.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import toolbattery  # noqa: E402
from toolbattery import (  # noqa: E402
    CALL_LEVEL_NO_CALL, CALL_LEVEL_NOT_WELL_FORMED, CALL_LEVEL_RIGHT_ARGS,
    CALL_LEVEL_RIGHT_TOOL, CALL_LEVEL_SCHEMA_VALID, CALL_LEVEL_WELL_FORMED,
    DEFAULT_BASE_URL, REFUSAL_TOOLS, SCHEMA_VERSION, TOOL_BOOK_ROOM,
    TOOL_CREATE_REMINDER, TOOL_EDIT_FILE, TOOL_GET_WEATHER, TOOLS_BY_NAME,
    _by_tool_classification, _call_level_counts, _pseudo_calls_extra,
    _schema_adherence_by_tool, _slug, _to_call, chat, classify_call,
    detect_pseudo_tool_calls, parse_message, run_battery, run_schema_adherence,
    score_dependency, score_error_recovery, score_refusal, score_schema_adherence,
    score_tool_selection, server_reachable, strip_reasoning,
)


def call(name, args, call_id='c1'):
    return {'id': call_id, 'name': name, 'args': args, 'args_raw': json.dumps(args), 'parse_error': None}


# ---------------------------------------------------------------------------
# strip_reasoning: Gemma's inline <|channel>thought markers (MODELS.md §4)
# ---------------------------------------------------------------------------

def test_strip_reasoning_extracts_and_removes_marker():
    content = ('<|channel>thought\nI should call get_weather.<channel|>'
               'Here is the weather.')
    reasoning, final = strip_reasoning(content)
    assert reasoning == 'I should call get_weather.'
    assert final == 'Here is the weather.'


def test_strip_reasoning_no_marker_passes_through():
    reasoning, final = strip_reasoning('just a plain answer')
    assert reasoning is None
    assert final == 'just a plain answer'


def test_strip_reasoning_none_content():
    reasoning, final = strip_reasoning(None)
    assert reasoning is None and final is None


def test_strip_reasoning_tool_call_turn_has_no_final_text():
    # Observed live: on a tool-call turn, content is ONLY the reasoning block.
    content = '<|channel>thought\nPlan: call get_weather with city=Paris.<channel|>'
    reasoning, final = strip_reasoning(content)
    assert reasoning == 'Plan: call get_weather with city=Paris.'
    assert final == ''


# ---------------------------------------------------------------------------
# parse_message
# ---------------------------------------------------------------------------

def test_parse_message_extracts_tool_calls_and_decodes_json_string_args():
    message = {
        'role': 'assistant',
        'content': '<|channel>thought\nplan<channel|>',
        'tool_calls': [{'id': 'abc', 'type': 'function',
                        'function': {'name': 'get_weather', 'arguments': '{"city":"Paris"}'}}],
    }
    parsed = parse_message(message)
    assert parsed['reasoning_present'] is True
    assert parsed['text'] == ''
    assert len(parsed['tool_calls']) == 1
    tc = parsed['tool_calls'][0]
    assert tc['name'] == 'get_weather'
    assert tc['args'] == {'city': 'Paris'}
    assert tc['parse_error'] is None


def test_parse_message_records_parse_error_on_malformed_json_args():
    message = {'content': None, 'tool_calls': [
        {'id': 'x', 'function': {'name': 'get_weather', 'arguments': '{city: Paris}'}}]}
    parsed = parse_message(message)
    tc = parsed['tool_calls'][0]
    assert tc['args'] is None
    assert tc['parse_error'] is not None


def test_parse_message_no_tool_calls():
    parsed = parse_message({'content': 'The capital of France is Paris.'})
    assert parsed['tool_calls'] == []
    assert parsed['text'] == 'The capital of France is Paris.'


# ---------------------------------------------------------------------------
# score_schema_adherence
# ---------------------------------------------------------------------------

def test_schema_adherence_passes_when_required_present_and_typed_right():
    c = call('book_meeting_room', {
        'room_name': 'Falcon', 'start_time': '2026-10-01T14:00:00',
        'duration_minutes': 30, 'attendees': ['alice@example.com']})
    passed, reasons = score_schema_adherence(TOOL_BOOK_ROOM, c)
    assert passed is True
    assert reasons == []


def test_schema_adherence_fails_on_missing_required():
    c = call('book_meeting_room', {'room_name': 'Falcon', 'start_time': '2026-10-01T14:00:00'})
    passed, reasons = score_schema_adherence(TOOL_BOOK_ROOM, c)
    assert passed is False
    assert any('missing required' in r for r in reasons)


def test_schema_adherence_fails_on_invented_param():
    c = call('get_weather', {'city': 'Paris', 'forecast_days': 5})
    passed, reasons = score_schema_adherence(TOOL_GET_WEATHER, c)
    assert passed is False
    assert any('invented params' in r for r in reasons)


def test_schema_adherence_fails_on_wrong_type():
    c = call('book_meeting_room', {
        'room_name': 'Falcon', 'start_time': '2026-10-01T14:00:00',
        'duration_minutes': '30', 'attendees': ['a@example.com']})  # string, not integer
    passed, reasons = score_schema_adherence(TOOL_BOOK_ROOM, c)
    assert passed is False
    assert any('type mismatch' in r for r in reasons)


def test_schema_adherence_fails_when_wrong_tool_called():
    c = call('list_available_rooms', {'building': 'Riverside'})
    passed, reasons = score_schema_adherence(TOOL_BOOK_ROOM, c)
    assert passed is False
    assert 'wrong tool called' in reasons[0]


def test_schema_adherence_fails_when_no_call_made():
    passed, reasons = score_schema_adherence(TOOL_BOOK_ROOM, None)
    assert passed is False


# ---------------------------------------------------------------------------
# Compound-schema probes (#15): a required top-level scalar (`path`) plus a
# required nested array-of-objects (`edits[]`), shaped like pi's real `edit`
# tool -- exactly the shape that let a live Gemma run score schemaAdherence
# 3/3 while failing 17/17 real `edit` calls, all by omitting `path` while
# filling in the nested array correctly.
# ---------------------------------------------------------------------------

def test_schema_adherence_passes_on_well_formed_compound_call():
    c = call('edit_file', {'path': 'runbook.md',
                            'edits': [{'oldText': 'Owner: TBD', 'newText': 'Owner: SRE'}]})
    passed, reasons = score_schema_adherence(TOOL_EDIT_FILE, c)
    assert passed is True
    assert reasons == []


def test_schema_adherence_catches_the_exact_15_failure_mode():
    # The nested edits[] is filled in correctly; only the sibling top-level
    # scalar `path` is missing. A flat probe cannot construct this case at
    # all -- there is no sibling to drop.
    c = call('edit_file', {'edits': [{'oldText': 'Owner: TBD', 'newText': 'Owner: SRE'}]})
    passed, reasons = score_schema_adherence(TOOL_EDIT_FILE, c)
    assert passed is False
    assert any('missing required' in r and 'path' in r for r in reasons)


def test_schema_adherence_compound_still_checks_the_nested_arrays_type():
    c = call('edit_file', {'path': 'runbook.md', 'edits': 'Owner: SRE'})  # not an array
    passed, reasons = score_schema_adherence(TOOL_EDIT_FILE, c)
    assert passed is False
    assert any('type mismatch' in r for r in reasons)


def test_schema_adherence_boolean_not_mistaken_for_integer():
    # Python bools are ints; the checker must not accept True for an
    # `integer`-typed param.
    c = call('book_meeting_room', {
        'room_name': 'Falcon', 'start_time': '2026-10-01T14:00:00',
        'duration_minutes': True, 'attendees': ['a@example.com']})
    passed, reasons = score_schema_adherence(TOOL_BOOK_ROOM, c)
    assert passed is False


# ---------------------------------------------------------------------------
# score_tool_selection
# ---------------------------------------------------------------------------

def test_tool_selection_passes_on_exact_match():
    passed, reasons = score_tool_selection('get_weather', call('get_weather', {'city': 'Lisbon'}))
    assert passed is True


def test_tool_selection_fails_on_decoy():
    passed, reasons = score_tool_selection('get_weather', call('get_weather_alerts', {'region': 'Gulf'}))
    assert passed is False
    assert 'decoy' in reasons[0]


def test_tool_selection_fails_on_no_call():
    passed, reasons = score_tool_selection('get_weather', None)
    assert passed is False


# ---------------------------------------------------------------------------
# score_dependency
# ---------------------------------------------------------------------------

def test_dependency_passes_when_second_call_uses_first_result():
    c2 = call('get_order_status', {'customer_id': 'cus_48291'})
    passed, reasons = score_dependency(c2, 'customer_id', 'cus_48291')
    assert passed is True


def test_dependency_fails_when_value_invented():
    c2 = call('get_order_status', {'customer_id': 'made-up-id'})
    passed, reasons = score_dependency(c2, 'customer_id', 'cus_48291')
    assert passed is False
    assert 'mismatch' in reasons[0]


def test_dependency_fails_when_key_missing():
    c2 = call('get_order_status', {})
    passed, reasons = score_dependency(c2, 'customer_id', 'cus_48291')
    assert passed is False


def test_dependency_fails_when_no_second_call():
    passed, reasons = score_dependency(None, 'customer_id', 'cus_48291')
    assert passed is False


# ---------------------------------------------------------------------------
# score_error_recovery -- built on events.call_metrics()'s repeat detection
# ---------------------------------------------------------------------------

def test_error_recovery_fails_on_verbatim_repeat():
    c1 = call('create_reminder', {'text': 'call vendor', 'remind_at': 'next Tuesday 3pm'})
    c2 = call('create_reminder', {'text': 'call vendor', 'remind_at': 'next Tuesday 3pm'}, call_id='c2')
    passed, reasons = score_error_recovery(c1, c2)
    assert passed is False
    assert 'repeated' in reasons[0]


def test_error_recovery_passes_when_arguments_change():
    c1 = call('create_reminder', {'text': 'call vendor', 'remind_at': 'next Tuesday 3pm'})
    c2 = call('create_reminder', {'text': 'call vendor', 'remind_at': '2026-10-06T15:00:00'}, call_id='c2')
    passed, reasons = score_error_recovery(c1, c2)
    assert passed is True


def test_error_recovery_passes_when_different_tool_used():
    c1 = call('book_meeting_room', {'room_name': 'Falcon', 'start_time': '2026-10-01T14:00:00',
                                     'duration_minutes': 30, 'attendees': ['a@example.com']})
    c2 = call('list_available_rooms', {'building': 'Riverside'}, call_id='c2')
    passed, reasons = score_error_recovery(c1, c2)
    assert passed is True


def test_error_recovery_reports_not_attempted_when_model_gives_up():
    """Not retrying is neither recovery nor a verbatim repeat. Scoring it
    as a pass let a live Gemma run report errorRecovery 2/2 (100%) when
    neither case recovered from anything -- so it is a third outcome,
    None, excluded from the denominator by _summarize()."""
    c1 = call('create_reminder', {'text': 'x', 'remind_at': 'tomorrow'})
    passed, reasons = score_error_recovery(c1, None)
    assert passed is None
    assert 'never exercised' in reasons[0]


def test_not_attempted_cases_leave_the_denominator_rather_than_inflating_it():
    from toolbattery import _summarize
    cases = [
        {'case': 'a', 'passed': True, 'reasons': []},
        {'case': 'b', 'passed': None, 'reasons': []},
        {'case': 'c', 'passed': False, 'reasons': []},
    ]
    d = _summarize('errorRecovery', cases)
    assert (d['passed'], d['total'], d['notAttempted']) == (1, 2, 1)
    assert d['score'] == 0.5


def test_a_dimension_with_no_attempted_cases_scores_none_not_zero():
    from toolbattery import _summarize
    d = _summarize('errorRecovery', [{'case': 'a', 'passed': None, 'reasons': []}])
    assert d['total'] == 0
    assert d['notAttempted'] == 1
    # None, not 0.0: no evidence is not the same fact as failed everything.
    assert d['score'] is None


def test_error_recovery_uses_the_same_digest_as_events_call_metrics():
    # Same (tool, args) pair, different key order -- _canonical_digest
    # (reused from events.py) must treat these as identical, exactly as it
    # does for a real harness trace.
    c1 = call('create_reminder', {'text': 'x', 'remind_at': 'y'})
    c2 = call('create_reminder', {'remind_at': 'y', 'text': 'x'}, call_id='c2')
    ca, cb = _to_call(c1, 1), _to_call(c2, 2)
    assert ca.args_digest == cb.args_digest
    passed, _ = score_error_recovery(c1, c2)
    assert passed is False


# ---------------------------------------------------------------------------
# score_refusal
# ---------------------------------------------------------------------------

def test_refusal_passes_when_no_tool_called():
    parsed = {'tool_calls': []}
    passed, reasons = score_refusal(parsed)
    assert passed is True


def test_refusal_fails_when_a_tool_is_called_anyway():
    parsed = {'tool_calls': [call('convert_currency', {'amount': 10, 'from_currency': 'mi', 'to_currency': 'km'})]}
    passed, reasons = score_refusal(parsed)
    assert passed is False
    assert 'convert_currency' in reasons[0]


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _schema_adherence_by_tool: per-tool breakdown (#15)
# ---------------------------------------------------------------------------

def test_schema_adherence_by_tool_hides_nothing_behind_the_aggregate():
    # The scenario the issue is named for: three flat probes pass, the one
    # compound-schema probe fails. The aggregate (3/4, 75%) reads fine; the
    # breakdown must still show book_meeting_room clean and edit_file at 0%.
    cases = [
        {'case': 'room-basic', 'passed': True, 'reasons': [], 'expectedTool': 'book_meeting_room'},
        {'case': 'room-recurring', 'passed': True, 'reasons': [], 'expectedTool': 'book_meeting_room'},
        {'case': 'weather-units', 'passed': True, 'reasons': [], 'expectedTool': 'get_weather'},
        {'case': 'edit-single', 'passed': False, 'reasons': ['missing required params'],
         'expectedTool': 'edit_file'},
    ]
    by_tool = _schema_adherence_by_tool(cases)
    assert by_tool['book_meeting_room'] == {'passed': 2, 'total': 2, 'notAttempted': 0, 'score': 1.0}
    assert by_tool['edit_file'] == {'passed': 0, 'total': 1, 'notAttempted': 0, 'score': 0.0}
    assert by_tool['get_weather']['score'] == 1.0


def test_schema_adherence_by_tool_excludes_not_attempted_from_its_own_denominator():
    cases = [
        {'case': 'edit-single', 'passed': None, 'reasons': [], 'expectedTool': 'edit_file'},
    ]
    by_tool = _schema_adherence_by_tool(cases)
    assert by_tool['edit_file'] == {'passed': 0, 'total': 0, 'notAttempted': 1, 'score': None}


def test_schema_adherence_by_tool_skips_cases_with_no_expected_tool():
    cases = [{'case': 'x', 'passed': True, 'reasons': []}]  # no 'expectedTool' key
    assert _schema_adherence_by_tool(cases) == {}


def test_run_schema_adherence_cases_now_include_the_compound_probes():
    from toolbattery import SCHEMA_CASES
    tool_names = {tool['function']['name'] for _, _, tool in SCHEMA_CASES}
    assert 'edit_file' in tool_names
    edit_cases = [c for c in SCHEMA_CASES if c[2]['function']['name'] == 'edit_file']
    assert len(edit_cases) >= 2  # at least one is not enough to rule out passing by chance


def test_slug_is_filesystem_safe():
    assert _slug('gemma-4-12B-it-qat-UD-Q4_K_XL.gguf') == 'gemma-4-12B-it-qat-UD-Q4_K_XL.gguf'
    assert '/' not in _slug('weird/model:name?')


def test_schema_version_is_an_int():
    assert isinstance(SCHEMA_VERSION, int)


def test_schema_version_is_2_for_issue_29():
    # v1 committed artefacts (toolbattery-results/*.json, predating this
    # change) are NOT rescored -- see the module docstring's "v1 vs v2".
    # This pins the bump itself so a future edit can't silently drift it
    # back without a reviewer noticing.
    assert SCHEMA_VERSION == 2


# ---------------------------------------------------------------------------
# classify_call: well-formed / schema-valid / right tool / right args (#29)
# ---------------------------------------------------------------------------

def test_classify_call_no_call_made():
    assert classify_call(TOOLS_BY_NAME, 'get_weather', None) == CALL_LEVEL_NO_CALL


def test_classify_call_not_well_formed_on_unparsed_arguments():
    c = {'id': 'c1', 'name': 'get_weather', 'args': None,
         'args_raw': '{city: Paris}', 'parse_error': 'bad json'}
    assert classify_call(TOOLS_BY_NAME, 'get_weather', c) == CALL_LEVEL_NOT_WELL_FORMED


def test_classify_call_well_formed_but_schema_invalid_missing_required():
    c = call('get_weather', {})  # missing required "city"
    assert classify_call(TOOLS_BY_NAME, 'get_weather', c) == CALL_LEVEL_WELL_FORMED


def test_classify_call_well_formed_when_tool_name_unknown():
    # A well-formed call to a tool this module has no schema for at all --
    # nothing to check it against, so it cannot be judged past well-formed.
    c = call('delete_universe', {'confirm': True})
    assert classify_call(TOOLS_BY_NAME, 'get_weather', c) == CALL_LEVEL_WELL_FORMED


def test_classify_call_schema_valid_but_wrong_tool_decoy_taken():
    c = call('get_weather_alerts', {'region': 'Gulf Coast'})  # valid for the tool it named
    assert classify_call(TOOLS_BY_NAME, 'get_weather', c) == CALL_LEVEL_SCHEMA_VALID


def test_classify_call_right_tool_when_right_args_ok_is_false():
    c = call('get_order_status', {'customer_id': 'made-up-id'})
    level = classify_call(TOOLS_BY_NAME, 'get_order_status', c, right_args_ok=False)
    assert level == CALL_LEVEL_RIGHT_TOOL


def test_classify_call_right_args_when_right_args_ok_is_true():
    c = call('get_order_status', {'customer_id': 'cus_48291'})
    level = classify_call(TOOLS_BY_NAME, 'get_order_status', c, right_args_ok=True)
    assert level == CALL_LEVEL_RIGHT_ARGS


def test_classify_call_right_args_collapses_when_right_args_ok_is_none():
    # schemaAdherence/toolSelection: no expected VALUE exists, only an
    # expected shape -- right_args_ok=None means "same as schema-valid",
    # so right-tool and right-args collapse into one reachable ceiling.
    c = call('get_weather', {'city': 'Lisbon'})
    assert classify_call(TOOLS_BY_NAME, 'get_weather', c) == CALL_LEVEL_RIGHT_ARGS


def test_classify_call_refusal_case_caps_at_schema_valid():
    # expected_tool_name=None: no tool is ever "right" in a refusal case.
    c = call('get_weather', {'city': 'Paris'})
    assert classify_call(TOOLS_BY_NAME, None, c) == CALL_LEVEL_SCHEMA_VALID


def test_classify_call_compound_schema_missing_sibling_scalar_is_well_formed():
    # The exact #15 failure mode: edits[] filled in correctly, path dropped.
    c = call('edit_file', {'edits': [{'oldText': 'a', 'newText': 'b'}]})
    assert classify_call(TOOLS_BY_NAME, 'edit_file', c) == CALL_LEVEL_WELL_FORMED


# ---------------------------------------------------------------------------
# detect_pseudo_tool_calls: six formats, one test per format, plus the
# negative case (bare prose mentioning a tool name is NOT a pseudo-call)
# ---------------------------------------------------------------------------

KNOWN = {'get_weather', 'book_meeting_room', 'lookup_customer'}


def test_pseudo_detects_bare_json_object_naming_a_known_tool():
    text = 'Sure, here is the call: {"name": "get_weather", "arguments": {"city": "Paris"}} done.'
    found = detect_pseudo_tool_calls(text, KNOWN)
    assert len(found) == 1
    assert found[0].format == 'jsonObject'
    assert found[0].tool == 'get_weather'


def test_pseudo_ignores_json_object_naming_an_unknown_tool():
    text = 'Example: {"name": "not_a_real_tool", "arguments": {}}'
    assert detect_pseudo_tool_calls(text, KNOWN) == []


def test_pseudo_detects_tool_call_xml_tag():
    text = ('<tool_call>\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n'
            '</tool_call>')
    found = detect_pseudo_tool_calls(text, KNOWN)
    assert len(found) == 1
    assert found[0].format == 'toolCallTag'
    assert found[0].tool == 'get_weather'


def test_pseudo_detects_sentinel_tool_call_token():
    text = '<|tool_call|>{"name": "book_meeting_room", "arguments": {"room_name": "Falcon"}}<|/tool_call|>'
    found = detect_pseudo_tool_calls(text, KNOWN)
    assert len(found) == 1
    assert found[0].format == 'sentinelTag'
    assert found[0].tool == 'book_meeting_room'


def test_pseudo_detects_hermes_qwen_function_xml():
    text = '<function=lookup_customer>{"email": "dana@example.com"}</function>'
    found = detect_pseudo_tool_calls(text, KNOWN)
    assert len(found) == 1
    assert found[0].format == 'functionXml'
    assert found[0].tool == 'lookup_customer'


def test_pseudo_detects_mistral_tool_calls_marker():
    text = '[TOOL_CALLS] [{"name": "get_weather", "arguments": {"city": "Paris"}}]'
    found = detect_pseudo_tool_calls(text, KNOWN)
    assert len(found) == 1
    assert found[0].format == 'mistralToolCalls'
    assert found[0].tool == 'get_weather'


def test_pseudo_detects_gpt_oss_harmony_to_functions_leak():
    text = 'to=functions.get_weather<|constrain|>json{"city": "Paris"}'
    found = detect_pseudo_tool_calls(text, KNOWN)
    assert len(found) == 1
    assert found[0].format == 'harmonyLeak'
    assert found[0].tool == 'get_weather'


def test_pseudo_detects_fenced_json_code_block():
    text = 'Here:\n```json\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n```\n'
    found = detect_pseudo_tool_calls(text, KNOWN)
    assert len(found) == 1
    assert found[0].format == 'fencedCode'
    assert found[0].tool == 'get_weather'


def test_pseudo_detects_fenced_call_syntax_block():
    text = '```python\nget_weather(city="Paris")\n```'
    found = detect_pseudo_tool_calls(text, KNOWN)
    assert len(found) == 1
    assert found[0].format == 'fencedCode'
    assert found[0].tool == 'get_weather'


def test_pseudo_prose_merely_mentioning_a_tool_name_is_not_counted():
    text = ('You could call get_weather here, but I do not have enough information -- '
            'ask the user which city they mean.')
    assert detect_pseudo_tool_calls(text, KNOWN) == []


def test_pseudo_no_double_count_across_formats():
    # The JSON inside a <tool_call> tag must not ALSO be picked up by the
    # generic bare-jsonObject scan.
    text = '<tool_call>\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n</tool_call>'
    found = detect_pseudo_tool_calls(text, KNOWN)
    assert len(found) == 1


def test_pseudo_empty_or_non_string_text_returns_empty():
    assert detect_pseudo_tool_calls(None, KNOWN) == []
    assert detect_pseudo_tool_calls('', KNOWN) == []


def test_pseudo_calls_extra_tags_each_finding_with_its_turn():
    turn1 = 'no leak here'
    turn2 = '{"name": "get_weather", "arguments": {"city": "Paris"}}'
    found = _pseudo_calls_extra(KNOWN, (1, turn1), (2, turn2))
    assert len(found) == 1
    assert found[0]['turn'] == 2
    assert found[0]['tool'] == 'get_weather'


# ---------------------------------------------------------------------------
# _call_level_counts / _by_tool_classification: the generalised byTool (#29)
# ---------------------------------------------------------------------------

def test_call_level_counts_tallies_every_level():
    calls = [
        {'turn': 1, 'tool': None, 'expectedTool': 'get_weather', 'level': CALL_LEVEL_NO_CALL},
        {'turn': 1, 'tool': 'get_weather', 'expectedTool': 'get_weather', 'level': CALL_LEVEL_RIGHT_ARGS},
        {'turn': 1, 'tool': 'edit_file', 'expectedTool': 'edit_file', 'level': CALL_LEVEL_WELL_FORMED},
    ]
    counts = _call_level_counts(calls)
    assert counts[CALL_LEVEL_NO_CALL] == 1
    assert counts[CALL_LEVEL_RIGHT_ARGS] == 1
    assert counts[CALL_LEVEL_WELL_FORMED] == 1
    assert counts[CALL_LEVEL_SCHEMA_VALID] == 0


def test_by_tool_classification_spans_multiple_dimensions():
    # The scenario the ticket is named for: one tool (edit_file) fails
    # badly, but split across dimensions no single dimension's own byTool
    # would show more than a couple of failures.
    calls = [
        {'turn': 1, 'tool': 'edit_file', 'expectedTool': 'edit_file', 'level': CALL_LEVEL_WELL_FORMED},
        {'turn': 1, 'tool': 'edit_file', 'expectedTool': 'edit_file', 'level': CALL_LEVEL_WELL_FORMED},
        {'turn': 1, 'tool': 'get_weather', 'expectedTool': 'get_weather', 'level': CALL_LEVEL_RIGHT_ARGS},
        {'turn': 1, 'tool': None, 'expectedTool': None, 'level': CALL_LEVEL_NO_CALL},
    ]
    by_tool = _by_tool_classification(calls)
    assert by_tool['edit_file'][CALL_LEVEL_WELL_FORMED] == 2
    assert by_tool['edit_file'][CALL_LEVEL_RIGHT_ARGS] == 0
    assert by_tool['get_weather'][CALL_LEVEL_RIGHT_ARGS] == 1
    assert CALL_LEVEL_NO_CALL not in by_tool['edit_file']  # not a per-tool bucket
    assert None not in by_tool


# ---------------------------------------------------------------------------
# Wiring: run_schema_adherence() attaches calls + pseudoToolCalls per case,
# without hitting a real network -- chat() is monkeypatched.
# ---------------------------------------------------------------------------

def test_run_schema_adherence_attaches_call_classification(monkeypatch):
    def fake_chat(base_url, model, messages, tools=None, max_tokens=None, timeout=None, api_key=None):
        tool_name = tools[0]['function']['name']
        if tool_name == 'book_meeting_room':
            args = {'room_name': 'Falcon', 'start_time': '2026-10-01T14:00:00',
                     'duration_minutes': 30, 'attendees': ['alice@example.com']}
        elif tool_name == 'get_weather':
            args = {'city': 'Tokyo', 'units': 'fahrenheit'}
        else:
            args = {'path': 'ops.yaml', 'edits': [{'oldText': 'a', 'newText': 'b'}]}
        return {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
            {'id': 'c1', 'type': 'function',
             'function': {'name': tool_name, 'arguments': json.dumps(args)}}]}}]}

    monkeypatch.setattr(toolbattery, 'chat', fake_chat)
    errors = []
    summary = run_schema_adherence(DEFAULT_BASE_URL, 'fake-model', 300, 30, errors)
    assert errors == []
    for case in summary['cases']:
        assert case['calls'][0]['level'] == CALL_LEVEL_RIGHT_ARGS
        assert case['pseudoToolCalls'] == []


def test_run_schema_adherence_pseudo_detection_on_text_only_reply(monkeypatch):
    def fake_chat(base_url, model, messages, tools=None, max_tokens=None, timeout=None, api_key=None):
        tool_name = tools[0]['function']['name']
        text = f'I would call {{"name": "{tool_name}", "arguments": {{}}}} but let me just answer.'
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]}

    monkeypatch.setattr(toolbattery, 'chat', fake_chat)
    errors = []
    summary = run_schema_adherence(DEFAULT_BASE_URL, 'fake-model', 300, 30, errors)
    assert errors == []
    for case in summary['cases']:
        assert case['calls'][0]['level'] == CALL_LEVEL_NO_CALL
        assert len(case['pseudoToolCalls']) == 1
        assert case['pseudoToolCalls'][0]['format'] == 'jsonObject'


# ---------------------------------------------------------------------------
# Live-server tests -- skipped, not failed, when nothing answers
# ---------------------------------------------------------------------------

LIVE_BASE_URL = os.environ.get('OAKEN_TOOLBATTERY_BASE_URL', DEFAULT_BASE_URL)
LIVE_MODEL = os.environ.get('OAKEN_TOOLBATTERY_MODEL')


def _live_ready():
    return bool(LIVE_MODEL) and server_reachable(LIVE_BASE_URL, timeout=3)


@pytest.mark.skipif(not _live_ready(),
                     reason='no live server reachable (set OAKEN_TOOLBATTERY_MODEL and have a server up '
                            'to run this)')
def test_live_chat_returns_a_tool_call():
    resp = chat(LIVE_BASE_URL, LIVE_MODEL,
                [{'role': 'user', 'content': 'What is the weather in Paris? Use the tool.'}],
                tools=[TOOL_GET_WEATHER], max_tokens=200)
    choice = resp['choices'][0]
    parsed = parse_message(choice['message'])
    assert parsed['tool_calls'], 'expected at least one tool call'
    assert parsed['tool_calls'][0]['name'] == 'get_weather'


@pytest.mark.skipif(not _live_ready(),
                     reason='no live server reachable (set OAKEN_TOOLBATTERY_MODEL and have a server up '
                            'to run this)')
def test_live_full_battery_runs_and_produces_the_expected_shape():
    report = run_battery(LIVE_BASE_URL, LIVE_MODEL, max_tokens=300, timeout=60)
    assert report['schemaVersion'] == SCHEMA_VERSION
    assert set(report['dimensions']) == {
        'schemaAdherence', 'toolSelection', 'multiStepDependency', 'errorRecovery', 'refusal'}
    assert report['overall']['total'] == sum(d['total'] for d in report['dimensions'].values())
    assert 'edit_file' in report['dimensions']['schemaAdherence']['byTool']
