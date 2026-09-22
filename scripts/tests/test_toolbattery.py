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
from toolbattery import (  # noqa: E402
    DEFAULT_BASE_URL, REFUSAL_TOOLS, SCHEMA_VERSION, TOOL_BOOK_ROOM,
    TOOL_CREATE_REMINDER, TOOL_GET_WEATHER, _slug, _to_call, chat,
    parse_message, run_battery, score_dependency, score_error_recovery,
    score_refusal, score_schema_adherence, score_tool_selection,
    server_reachable, strip_reasoning,
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

def test_slug_is_filesystem_safe():
    assert _slug('gemma-4-12B-it-qat-UD-Q4_K_XL.gguf') == 'gemma-4-12B-it-qat-UD-Q4_K_XL.gguf'
    assert '/' not in _slug('weird/model:name?')


def test_schema_version_is_an_int():
    assert isinstance(SCHEMA_VERSION, int)


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
