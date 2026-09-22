"""Tests for scripts/events.py (issues #1, #2, #3).

Fixtures are hand-written event lists in exactly the shapes EVENTS.md
documents -- never real captured traces. A real trace contains agent-written
solution code for the held-out spec; committing one as a fixture would be
exactly the kind of leak CANARY.md exists to prevent.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from events import (  # noqa: E402
    Call, call_metrics, load_dsh_events, load_pi_events, normalize_calls,
)


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

def pi_stream(rows):
    """Build a minimal pi event stream: one (tool, args, ok_or_None) per
    row becomes a turn_start + tool_execution_start (+ end, if ok_or_None
    is not None). Real pi always emits an end for every start; the
    "unpaired" test builds its own stream by hand instead of using this."""
    events = []
    for i, (tool, args, ok) in enumerate(rows):
        call_id = f'call{i}'
        events.append({'type': 'turn_start'})
        events.append({'type': 'tool_execution_start', 'toolCallId': call_id,
                        'toolName': tool, 'args': args})
        if ok is not None:
            events.append({'type': 'tool_execution_end', 'toolCallId': call_id,
                            'toolName': tool, 'isError': not ok,
                            'result': {'content': [{'type': 'text', 'text': 'boom'}]} if not ok else {}})
    return events


def dsh_stream(rows):
    """Same idea for dsh: (tool, args_dict, ok_or_None). args_dict is
    encoded to a JSON string, matching the real `data.arguments` shape."""
    events = []
    for i, (tool, args, ok) in enumerate(rows):
        call_id = f'call{i}'
        events.append({'type': 'tool/call', 'time': 1000 + i,
                        'data': {'turn': 1, 'step': i, 'callId': call_id,
                                 'name': tool, 'arguments': json.dumps(args)}})
        if ok is not None:
            events.append({'type': 'tool/result', 'time': 1001 + i, 'data': {
                'turn': 1, 'step': i,
                'message': {'source': {'kind': 'tool', 'callId': call_id},
                            'content': [{'type': 'tool-result', 'toolCallId': call_id,
                                         'content': [{'type': 'text', 'text': 'boom'}],
                                         'isError': not ok}]}}})
    return events


# ---------------------------------------------------------------------------
# The seam: pi and dsh agree
# ---------------------------------------------------------------------------

def test_pi_and_dsh_produce_identical_metrics_from_equivalent_input():
    rows = [
        ('read', {'path': 'SPEC.md'}, True),
        ('edit', {'path': 'a.ts', 'oldText': 'x', 'newText': 'y'}, True),
        ('edit', {}, False),
        ('bash', {'cmd': 'npx vitest'}, True),
    ]
    pi_calls = normalize_calls(pi_stream(rows), 'pi')
    dsh_calls = normalize_calls(dsh_stream(rows), 'dsh')

    pi_metrics = call_metrics(pi_calls)
    dsh_metrics = call_metrics(dsh_calls)

    assert pi_metrics == dsh_metrics


def test_dsh_json_string_arguments_digest_equal_to_pi_object_form():
    args = {'path': 'SPEC.md', 'offset': 0}
    pi_calls = normalize_calls(pi_stream([('read', args, True)]), 'pi')
    dsh_calls = normalize_calls(dsh_stream([('read', args, True)]), 'dsh')
    assert pi_calls[0].args_digest == dsh_calls[0].args_digest


# ---------------------------------------------------------------------------
# Pairing / outcome classification
# ---------------------------------------------------------------------------

def test_unpaired_pi_call_is_unknown_and_excluded_from_error_rate():
    events = [
        {'type': 'turn_start'},
        {'type': 'tool_execution_start', 'toolCallId': 'c0', 'toolName': 'read', 'args': {}},
        # no matching tool_execution_end
        {'type': 'turn_start'},
        {'type': 'tool_execution_start', 'toolCallId': 'c1', 'toolName': 'edit', 'args': {'a': 1}},
        {'type': 'tool_execution_end', 'toolCallId': 'c1', 'toolName': 'edit', 'isError': True,
         'result': {'content': [{'type': 'text', 'text': 'bad'}]}},
    ]
    calls = normalize_calls(events, 'pi')
    assert calls[0].ok is None
    m = call_metrics(calls)
    assert m['toolOutcomes'] == {'ok': 0, 'error': 1, 'unknown': 1}
    # denominator excludes the unknown call: 1 error / 1 known = 1.0, not 1/2
    assert m['errorRate'] == 1.0


def test_dsh_duplicate_results_for_one_call_id_counted_once():
    call_id = 'dup0'
    events = [
        {'type': 'tool/call', 'time': 1, 'data': {'turn': 1, 'step': 1, 'callId': call_id,
                                                    'name': 'read', 'arguments': '{"path":"a"}'}},
        {'type': 'tool/result', 'time': 2, 'data': {
            'turn': 1, 'step': 1,
            'message': {'source': {'callId': call_id},
                        'content': [{'type': 'tool-result', 'toolCallId': call_id,
                                     'content': [], 'isError': False}]}}},
        # a second, duplicate result for the same callId
        {'type': 'tool/result', 'time': 3, 'data': {
            'turn': 1, 'step': 1,
            'message': {'source': {'callId': call_id},
                        'content': [{'type': 'tool-result', 'toolCallId': call_id,
                                     'content': [], 'isError': False}]}}},
    ]
    calls = normalize_calls(events, 'dsh')
    assert len(calls) == 1
    m = call_metrics(calls)
    assert m['toolCalls'] == 1
    assert m['toolOutcomes'] == {'ok': 1, 'error': 0, 'unknown': 0}


def test_dsh_error_on_any_duplicate_result_wins():
    call_id = 'dup1'
    events = [
        {'type': 'tool/call', 'time': 1, 'data': {'turn': 1, 'step': 1, 'callId': call_id,
                                                    'name': 'edit', 'arguments': '{}'}},
        {'type': 'tool/result', 'time': 2, 'data': {
            'turn': 1, 'step': 1,
            'message': {'source': {'callId': call_id},
                        'content': [{'type': 'tool-result', 'toolCallId': call_id,
                                     'content': [], 'isError': False}]}}},
        {'type': 'tool/result', 'time': 3, 'data': {
            'turn': 1, 'step': 1,
            'message': {'source': {'callId': call_id},
                        'content': [{'type': 'tool-result', 'toolCallId': call_id,
                                     'content': [{'type': 'text', 'text': 'validation failed'}],
                                     'isError': True}]}}},
    ]
    calls = normalize_calls(events, 'dsh')
    assert calls[0].ok is False


# ---------------------------------------------------------------------------
# Mutating calls / zero-write visibility (issue #3)
# ---------------------------------------------------------------------------

def test_zero_write_run_has_zero_mutating_calls():
    rows = [('read', {'path': f'f{i}.ts'}, True) for i in range(23)] + [('bash', {'cmd': 'ls'}, True)]
    calls = normalize_calls(pi_stream(rows), 'pi')
    m = call_metrics(calls)
    assert m['mutatingCalls'] == 0
    assert m['toolHistogram'] == {'read': 23, 'bash': 1}


def test_write_and_edit_count_as_mutating():
    rows = [('write', {'path': 'a'}, True), ('edit', {'path': 'b'}, True), ('read', {'path': 'c'}, True)]
    calls = normalize_calls(pi_stream(rows), 'pi')
    m = call_metrics(calls)
    assert m['mutatingCalls'] == 2


def test_unrecognized_tool_is_named_but_not_counted_as_mutating():
    """KNOWN_TOOLS is what one capture happened to see, not a harness
    inventory, so an unrecognised name must not inflate mutatingCalls: a
    read-only tool missing from the list would otherwise erase issue #3's
    "0 writes every time" finding. It is reported by name instead."""
    rows = [('mystery_tool', {'x': 1}, True)]
    calls = normalize_calls(pi_stream(rows), 'pi')
    m = call_metrics(calls)
    assert m['unknownTools'] == ['mystery_tool']
    assert m['mutatingCalls'] == 0


def test_zero_writes_survives_an_unrecognized_read_only_tool():
    """The regression this guards: a run that only reads, using a tool this
    module has never seen, must still read as zero-mutation."""
    rows = [('grep', {'q': 'Tower'}, True)] * 12 + [('read', {'path': 'SPEC.md'}, True)]
    m = call_metrics(normalize_calls(pi_stream(rows), 'pi'))
    assert m['mutatingCalls'] == 0
    assert m['unknownTools'] == ['grep']


def test_tool_name_from_the_model_is_capped_and_charset_restricted():
    """A pi toolName is echoed from model output, so it is agent-written text
    heading for a published artefact. Long or structured names must not pass
    through into toolHistogram/callSequence verbatim."""
    rows = [('x' * 200, {'a': 1}, True), ('{"leak": "solution code"}', {'a': 1}, True)]
    m = call_metrics(normalize_calls(pi_stream(rows), 'pi'))
    names = list(m['toolHistogram'])
    assert all(len(n) <= 64 for n in names)
    assert 'unparseable' in names
    assert 'leak' not in json.dumps(m)


# ---------------------------------------------------------------------------
# Repeat-run detection (issue #2)
# ---------------------------------------------------------------------------

def test_twenty_call_identical_argument_loop():
    rows = [('read', {'path': 'SPEC.md'}, True)] * 20
    calls = normalize_calls(pi_stream(rows), 'pi')
    m = call_metrics(calls)
    assert m['longestRepeatRun'] == 20
    assert m['distinctCallRatio'] == pytest.approx(1 / 20)
    assert m['repeatedCalls'] == 19
    assert m['callSequence'] == [[calls[0].tool, calls[0].args_digest, 20]]


def test_longest_repeat_run_breaks_on_interruption():
    rows = ([('read', {'p': 'a'}, True)] * 5
            + [('bash', {'c': 'ls'}, True)]
            + [('read', {'p': 'a'}, True)] * 3)
    calls = normalize_calls(pi_stream(rows), 'pi')
    m = call_metrics(calls)
    assert m['longestRepeatRun'] == 5
    assert m['repeatedCalls'] == 7  # 4 extra reads in the first run + 2 in the second (first of each run is not a repeat)


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_empty_event_stream_yields_zeroed_metrics():
    calls = normalize_calls([], 'pi')
    assert calls == []
    m = call_metrics(calls)
    assert m['toolCalls'] == 0
    assert m['errorRate'] == 0.0
    assert m['distinctCallRatio'] == 0.0
    assert m['toolOutcomes'] == {'ok': 0, 'error': 0, 'unknown': 0}
    assert m['callSequence'] == []


def test_malformed_events_do_not_raise():
    events = [
        None,
        42,
        'not an event',
        {'type': 'tool_execution_start'},  # missing toolCallId
        {'type': 'tool_execution_end', 'toolCallId': 'ghost', 'isError': True},
        {'type': 'tool_execution_start', 'toolCallId': 'c0', 'toolName': 'read', 'args': {'p': 1}},
    ]
    calls = normalize_calls(events, 'pi')
    assert len(calls) == 1
    m = call_metrics(calls)
    assert m['toolCalls'] == 1


def test_dsh_malformed_arguments_string_does_not_raise():
    events = [
        {'type': 'tool/call', 'time': 1, 'data': {'turn': 1, 'step': 1, 'callId': 'c0',
                                                    'name': 'read', 'arguments': '{not json'}},
    ]
    calls = normalize_calls(events, 'dsh')
    assert calls[0].args is None
    # still digestible and comparable, just not decodable to a dict
    m = call_metrics(calls)
    assert m['toolCalls'] == 1


def test_unknown_harness_raises():
    with pytest.raises(ValueError):
        normalize_calls([], 'nonexistent')


# ---------------------------------------------------------------------------
# No argument leakage (issue #2's core requirement)
# ---------------------------------------------------------------------------

def test_metrics_never_leak_argument_text():
    secret = 'const SECRET_SOLUTION_TOKEN = "xyzzy-do-not-leak";'
    rows = [('edit', {'path': 'a.ts', 'newText': secret}, True)]
    calls = normalize_calls(pi_stream(rows), 'pi')
    m = call_metrics(calls)
    serialised = json.dumps(m)
    assert 'xyzzy' not in serialised
    assert secret not in serialised
    # and Call.args itself is not reachable from the metrics dict
    for v in m.values():
        assert v is not calls[0].args


# ---------------------------------------------------------------------------
# Optional integration test against real captures
# ---------------------------------------------------------------------------

PI_CAPTURE = os.path.expanduser('~/.cache/oaken-bench/schema-capture-pi/pi-events.jsonl')
DSH_CAPTURE = os.path.expanduser('~/.cache/oaken-bench/schema-capture-dsh/dsh-sessions.tgz')


@pytest.mark.skipif(not os.path.exists(PI_CAPTURE), reason='no local pi capture at ~/.cache/oaken-bench')
def test_pi_capture_matches_events_md():
    calls = normalize_calls(load_pi_events(PI_CAPTURE), 'pi')
    m = call_metrics(calls)
    assert m['toolCalls'] == 35
    assert m['toolOutcomes']['error'] == 4
    assert m['toolHistogram'] == {'read': 15, 'edit': 19, 'bash': 1}


@pytest.mark.skipif(not os.path.exists(DSH_CAPTURE), reason='no local dsh capture at ~/.cache/oaken-bench')
def test_dsh_capture_matches_events_md():
    calls = normalize_calls(load_dsh_events(DSH_CAPTURE), 'dsh')
    m = call_metrics(calls)
    assert m['toolCalls'] == 37
    distinct_error_call_ids = m['toolOutcomes']['error']
    assert distinct_error_call_ids == 7
    assert m['toolHistogram'] == {
        'read': 10, 'todo_write': 11, 'write': 10, 'glob': 2, 'bash': 2, 'edit': 1, 'subagent': 1,
    }
