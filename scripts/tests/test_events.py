"""Tests for scripts/events.py (issues #1, #2, #3, #5, #6).

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
    stream_start_ms,
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

    # pi_stream()/dsh_stream() build the tool-call triple only -- pi's
    # timing needs the enclosing turn_end block, which neither helper
    # emits, while dsh_stream() puts a `time` on every event by
    # construction. That is a fixture-shape gap, not a genuine harness
    # difference (a real pi capture always has turn_end): the timing
    # fields are compared separately, in the issue #5 tests below.
    timing_keys = {'timeToFirstToolCall', 'toolCallIntervals', 'generationSeconds', 'toolExecutionSeconds'}
    assert {k: v for k, v in pi_metrics.items() if k not in timing_keys} == \
           {k: v for k, v in dsh_metrics.items() if k not in timing_keys}


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
# Calls per turn / parallel turns (issue #6)
# ---------------------------------------------------------------------------

def test_pi_calls_per_turn_and_parallel_turns():
    """Two toolCall blocks in one turn_end.message.content is pi's only
    signal of parallel tool calling (EVENTS.md 'Turn structure')."""
    events = [
        {'type': 'turn_start'},
        {'type': 'tool_execution_start', 'toolCallId': 'c0', 'toolName': 'read', 'args': {}},
        {'type': 'tool_execution_start', 'toolCallId': 'c1', 'toolName': 'read', 'args': {}},
        {'type': 'tool_execution_end', 'toolCallId': 'c0', 'toolName': 'read', 'isError': False, 'result': {}},
        {'type': 'tool_execution_end', 'toolCallId': 'c1', 'toolName': 'read', 'isError': False, 'result': {}},
        {'type': 'turn_end', 'message': {'timestamp': 1000, 'content': [
            {'type': 'toolCall', 'id': 'c0', 'name': 'read'},
            {'type': 'toolCall', 'id': 'c1', 'name': 'read'}]},
         'toolResults': [{'toolCallId': 'c0', 'timestamp': 1100},
                          {'toolCallId': 'c1', 'timestamp': 1150}]},
        {'type': 'turn_start'},
        {'type': 'tool_execution_start', 'toolCallId': 'c2', 'toolName': 'read', 'args': {}},
        {'type': 'tool_execution_end', 'toolCallId': 'c2', 'toolName': 'read', 'isError': False, 'result': {}},
        {'type': 'turn_end', 'message': {'timestamp': 1300, 'content': [
            {'type': 'toolCall', 'id': 'c2', 'name': 'read'}]},
         'toolResults': [{'toolCallId': 'c2', 'timestamp': 1350}]},
    ]
    calls = normalize_calls(events, 'pi')
    m = call_metrics(calls)
    assert m['toolCallsPerTurn'] == {'mean': 1.5, 'max': 2}
    assert m['parallelTurns'] == 1


def test_dsh_calls_per_turn_groups_by_turn_and_step():
    events = [
        {'type': 'tool/call', 'time': 1, 'data': {'turn': 1, 'step': 1, 'callId': 'c0',
                                                    'name': 'read', 'arguments': '{}'}},
        {'type': 'tool/call', 'time': 2, 'data': {'turn': 1, 'step': 1, 'callId': 'c1',
                                                    'name': 'read', 'arguments': '{}'}},
        {'type': 'tool/call', 'time': 3, 'data': {'turn': 1, 'step': 2, 'callId': 'c2',
                                                    'name': 'read', 'arguments': '{}'}},
    ]
    calls = normalize_calls(events, 'dsh')
    m = call_metrics(calls)
    assert m['toolCallsPerTurn'] == {'mean': 1.5, 'max': 2}
    assert m['parallelTurns'] == 1


def test_calls_per_turn_all_singletons_reports_no_parallel_turns():
    """The finding issue #6 predicts on real data: exactly 1 call per turn
    everywhere gives 1.0/1/0, and that IS the finding, not a bug."""
    rows = [('read', {'path': f'f{i}'}, True) for i in range(4)]
    calls = normalize_calls(pi_stream(rows), 'pi')
    m = call_metrics(calls)
    assert m['toolCallsPerTurn'] == {'mean': 1.0, 'max': 1}
    assert m['parallelTurns'] == 0


def test_calls_per_turn_zeroed_on_empty_stream():
    m = call_metrics(normalize_calls([], 'pi'))
    assert m['toolCallsPerTurn'] == {'mean': 0.0, 'max': 0}
    assert m['parallelTurns'] == 0


# ---------------------------------------------------------------------------
# Per-call timing (issue #5)
# ---------------------------------------------------------------------------

def test_pi_time_to_first_tool_call_uses_session_timestamp_anchor():
    """pi has no per-call start time of its own; the enclosing
    turn_end.message.timestamp stands in (EVENTS.md), and the run's own
    anchor is the `session` event's ISO timestamp."""
    events = [
        {'type': 'session', 'timestamp': '1970-01-01T00:00:05.000Z'},
        {'type': 'turn_start'},
        {'type': 'tool_execution_start', 'toolCallId': 'c0', 'toolName': 'read', 'args': {}},
        {'type': 'tool_execution_end', 'toolCallId': 'c0', 'toolName': 'read', 'isError': False, 'result': {}},
        {'type': 'turn_end', 'message': {'timestamp': 17000, 'content': [
            {'type': 'toolCall', 'id': 'c0', 'name': 'read'}]},
         'toolResults': [{'toolCallId': 'c0', 'timestamp': 17500}]},
    ]
    run_start = stream_start_ms(events, 'pi')
    assert run_start == 5000
    calls = normalize_calls(events, 'pi')
    m = call_metrics(calls, run_start_ms=run_start, harness='pi')
    assert m['timeToFirstToolCall'] == pytest.approx(12.0)
    # a single call has no consecutive gap to measure
    assert m['toolCallIntervals'] is None
    # ...and pi never gets the generation/execution split: see PER_CALL_CLOCK.
    assert m['toolExecutionSeconds'] is None
    assert m['generationSeconds'] is None
    assert m['generationSeconds'] is None


def test_time_to_first_tool_call_is_none_without_a_run_start_anchor():
    calls = normalize_calls(pi_stream([('read', {}, True)]), 'pi')
    m = call_metrics(calls)  # no run_start_ms passed
    assert m['timeToFirstToolCall'] is None


def test_dsh_stream_start_ms_uses_first_timed_event():
    events = [
        {'type': 'session', 'seq': 0, 'data': {'delegationDepth': 0}},  # header carries no `time`
        {'type': 'permission/preset', 'seq': 1, 'time': 1790082353983, 'data': {}},
    ]
    assert stream_start_ms(events, 'dsh') == 1790082353983


def test_toolcall_intervals_and_generation_vs_execution_split():
    """4 calls with round-number start/end times so the split is exact:
    intervals (start-to-start) = [2000, 4000, 6000]ms;
    toolExecutionSeconds sums each call's own (end - start);
    generationSeconds sums the gap from one call's end to the next call's
    start -- the model "thinking" between tool results and the next call."""
    events = []
    for i, (start, end) in enumerate([(0, 100), (2000, 2100), (6000, 6100), (12000, 12100)]):
        cid = f'c{i}'
        events.append({'type': 'tool/call', 'time': start,
                        'data': {'turn': 1, 'step': i, 'callId': cid, 'name': 'read', 'arguments': '{}'}})
        events.append({'type': 'tool/result', 'time': end, 'data': {
            'turn': 1, 'step': i,
            'message': {'content': [{'type': 'tool-result', 'toolCallId': cid,
                                      'content': [], 'isError': False}]}}})
    calls = normalize_calls(events, 'dsh')
    m = call_metrics(calls, harness='dsh')

    assert m['toolCallIntervals']['n'] == 3
    assert m['toolCallIntervals']['p50Seconds'] == pytest.approx(4.0)
    assert m['toolCallIntervals']['p95Seconds'] == pytest.approx(5.8)
    assert m['toolExecutionSeconds'] == pytest.approx(0.4)
    assert m['generationSeconds'] == pytest.approx(11.7)


def test_timing_fields_are_none_when_no_calls_have_timestamps():
    """A call with neither started_ms nor ended_ms (the stream carried no
    timing at all) must report null timing, never a fabricated zero."""
    calls = normalize_calls(pi_stream([('read', {}, True), ('read', {}, True)]), 'pi')
    for c in calls:
        assert c.started_ms is None and c.ended_ms is None
    m = call_metrics(calls)
    assert m['toolExecutionSeconds'] is None
    assert m['generationSeconds'] is None
    assert m['toolCallIntervals'] is None


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
    # issue #6's "Check first": every turn in the capture carried exactly
    # one call, so this reports 1.0/1/0 -- the finding, not a bug.
    assert m['toolCallsPerTurn'] == {'mean': 1.0, 'max': 1}
    assert m['parallelTurns'] == 0


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
    assert m['toolCallsPerTurn'] == {'mean': 1.0, 'max': 1}
    assert m['parallelTurns'] == 0


def test_pi_never_reports_a_generation_execution_split(capsys):
    """pi's tool_execution_start/end carry no timestamps, so its only
    clock near a call is message.timestamp -- a creation stamp written at
    the START of generation. `ended_ms - started_ms` therefore covers
    generation AND execution and cannot be split. On the real capture the
    naive split claims 208.65s of "tool execution" in a 239.5s run whose
    tools are local file reads; None is the only honest answer.
    """
    events = [
        {'type': 'session', 'timestamp': '1970-01-01T00:00:00.000Z'},
    ]
    for i in range(3):
        cid = f'c{i}'
        events += [
            {'type': 'turn_start'},
            {'type': 'tool_execution_start', 'toolCallId': cid, 'toolName': 'read', 'args': {}},
            {'type': 'tool_execution_end', 'toolCallId': cid, 'toolName': 'read',
             'isError': False, 'result': {}},
            {'type': 'turn_end',
             'message': {'timestamp': i * 10000, 'content': [
                 {'type': 'toolCall', 'id': cid, 'name': 'read'}]},
             'toolResults': [{'toolCallId': cid, 'timestamp': i * 10000 + 9000}]},
        ]
    m = call_metrics(normalize_calls(events, 'pi'), run_start_ms=0, harness='pi')

    # The per-call clock is absent, so neither half is published...
    assert m['generationSeconds'] is None
    assert m['toolExecutionSeconds'] is None
    # ...but the fields that do not depend on splitting them survive, and
    # they are the ones issue #5's motivating pathology actually needs.
    assert m['timeToFirstToolCall'] == pytest.approx(0.0)
    assert m['toolCallIntervals']['n'] == 2


def test_dsh_keeps_the_split_because_its_tool_events_are_timed():
    events = [{'type': 'session', 'seq': 0, 'time': 0, 'data': {'delegationDepth': 0}}]
    for i, (start, end) in enumerate([(1000, 1100), (5000, 5100)]):
        cid = f'c{i}'
        events.append({'type': 'tool/call', 'time': start,
                       'data': {'turn': 1, 'step': i, 'callId': cid,
                                'name': 'read', 'arguments': '{}'}})
        events.append({'type': 'tool/result', 'time': end, 'data': {
            'turn': 1, 'step': i,
            'message': {'content': [{'type': 'tool-result', 'toolCallId': cid,
                                     'content': [], 'isError': False}]}}})
    m = call_metrics(normalize_calls(events, 'dsh'), harness='dsh')

    assert m['toolExecutionSeconds'] == pytest.approx(0.2)
    assert m['generationSeconds'] == pytest.approx(3.9)
