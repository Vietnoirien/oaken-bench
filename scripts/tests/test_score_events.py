"""Tests for score.py's events-summary wiring (issue #7 + the tail of #3).

These build fake result directories under tmp_path and drive
build_events_summary()/merge_events_into_harness_metrics() directly rather
than through main(), which needs docker and is out of scope here. Real
captured traces are exercised separately, in
test_events_summary_matches_real_captures below.
"""
import json
import os
import subprocess
import sys
import tarfile
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from score import (  # noqa: E402
    EVENTS_SUMMARY_SCHEMA_VERSION, build_events_summary, dsh_metrics,
    merge_events_into_harness_metrics, pi_metrics,
)


def write_pi_result(tmp_path, rows, meta='harness=pi model=test-model timeout=240'):
    """rows: list of (tool, args_dict, ok_or_None), same shape as
    test_events.py's pi_stream, written out as a real pi-events.jsonl."""
    result_dir = tmp_path / 'pi-fixture'
    result_dir.mkdir()
    lines = []
    for i, (tool, args, ok) in enumerate(rows):
        call_id = f'call{i}'
        lines.append({'type': 'turn_start'})
        lines.append({'type': 'tool_execution_start', 'toolCallId': call_id,
                       'toolName': tool, 'args': args})
        if ok is not None:
            lines.append({'type': 'tool_execution_end', 'toolCallId': call_id,
                           'toolName': tool, 'isError': not ok,
                           'result': {'content': [{'type': 'text', 'text': 'boom'}]} if not ok else {}})
    with open(result_dir / 'pi-events.jsonl', 'w') as f:
        for e in lines:
            f.write(json.dumps(e) + '\n')
    (result_dir / 'run.meta').write_text(meta)
    return result_dir


def write_dsh_result(tmp_path, rows, meta='harness=dsh model=test-model timeout=240'):
    """Builds a real dsh-sessions.tgz: tar containing
    .dsh/sessions/<slug>/<session>/session.v3.jsonl.zstd, root session
    (delegationDepth 0), matching what load_dsh_events() expects."""
    result_dir = tmp_path / 'dsh-fixture'
    result_dir.mkdir()

    events = [{'type': 'session', 'seq': 0, 'time': 0,
               'data': {'id': 'root', 'delegationDepth': 0}}]
    for i, (tool, args, ok) in enumerate(rows):
        call_id = f'call{i}'
        events.append({'type': 'tool/call', 'seq': i * 2 + 1, 'time': 1000 + i,
                        'data': {'turn': 1, 'step': i, 'callId': call_id,
                                 'name': tool, 'arguments': json.dumps(args)}})
        if ok is not None:
            events.append({'type': 'tool/result', 'seq': i * 2 + 2, 'time': 1001 + i, 'data': {
                'turn': 1, 'step': i,
                'message': {'source': {'kind': 'tool', 'callId': call_id},
                            'content': [{'type': 'tool-result', 'toolCallId': call_id,
                                         'content': [{'type': 'text', 'text': 'boom'}],
                                         'isError': not ok}]}}})

    with tempfile.TemporaryDirectory() as td:
        session_dir = os.path.join(td, '.dsh', 'sessions', 'slug', 'sess1')
        os.makedirs(session_dir)
        jsonl_path = os.path.join(session_dir, 'session.v3.jsonl')
        with open(jsonl_path, 'w') as f:
            for e in events:
                f.write(json.dumps(e) + '\n')
        subprocess.run(['zstd', '-f', '-q', jsonl_path, '-o', jsonl_path + '.zstd'], check=True)
        os.remove(jsonl_path)

        tgz_path = result_dir / 'dsh-sessions.tgz'
        with tarfile.open(tgz_path, 'w:gz') as tar:
            tar.add(os.path.join(td, '.dsh'), arcname='.dsh')

    (result_dir / 'run.meta').write_text(meta)
    return result_dir


# ---------------------------------------------------------------------------
# events-summary.json content
# ---------------------------------------------------------------------------

def test_events_summary_has_expected_keys_and_provenance():
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        result_dir = write_pi_result(tmp_path, [
            ('read', {'path': 'a'}, True),
            ('edit', {'path': 'b'}, False),
        ])
        summary = build_events_summary(str(result_dir), 'pi-fixture', 'pi', 'test-model')

    assert summary['schemaVersion'] == EVENTS_SUMMARY_SCHEMA_VERSION
    assert summary['label'] == 'pi-fixture'
    assert summary['harness'] == 'pi'
    assert summary['model'] == 'test-model'
    assert summary['toolCalls'] == 2
    assert summary['toolOutcomes'] == {'ok': 1, 'error': 1, 'unknown': 0}
    assert 'callSequence' in summary


def test_events_summary_none_when_no_trace_present():
    with tempfile.TemporaryDirectory() as td:
        # No pi-events.jsonl / dsh-sessions.tgz written at all.
        assert build_events_summary(td, 'empty', 'pi', 'm') is None
        assert build_events_summary(td, 'empty', 'dsh', 'm') is None
        assert build_events_summary(td, 'empty', 'unknown-harness', 'm') is None


def test_events_summary_never_contains_raw_argument_text():
    secret = 'const SECRET_SOLUTION_TOKEN = "xyzzy-do-not-leak";'
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        result_dir = write_pi_result(tmp_path, [('edit', {'path': 'a.ts', 'newText': secret}, True)])
        summary = build_events_summary(str(result_dir), 'pi-fixture', 'pi', 'test-model')

    serialised = json.dumps(summary)
    assert 'xyzzy' not in serialised
    assert secret not in serialised
    assert 'newText' not in serialised


def test_trace_is_found_when_run_meta_harness_is_wrong_or_missing():
    """run.meta's harness= is a hint, not the authority. A run whose meta
    line is absent or misspelt still has a trace on disk, and issue #7 is
    about not losing that record -- a summary skipped here would leave a
    run looking scored with none of #1-#3's fields.
    """
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        result_dir = write_pi_result(tmp_path, [('read', {'path': 'a'}, True)] * 3)
        for harness in (None, '', 'dsh', 'typo'):
            summary = build_events_summary(str(result_dir), 'pi-fixture', harness, 'm')
            assert summary is not None, harness
            assert summary['toolCalls'] == 3


# ---------------------------------------------------------------------------
# harnessMetrics merge (issue #3's field-shape change)
# ---------------------------------------------------------------------------

def test_merge_keeps_old_harness_metrics_field_names():
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        result_dir = write_pi_result(tmp_path, [('read', {'path': 'a'}, True)] * 3)
        hm = pi_metrics(str(result_dir))
        summary = build_events_summary(str(result_dir), 'pi-fixture', 'pi', 'test-model')
        merged = merge_events_into_harness_metrics(hm, summary)

    # Old fields other tooling (summarize.py) and committed score.json rows
    # depend on by name must survive the merge untouched.
    for key in ('events', 'turns', 'usage', 'compactions'):
        assert key in merged
        assert merged[key] == hm[key]


def test_merge_replaces_flat_tools_list_with_run_length_sequence():
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        result_dir = write_pi_result(tmp_path, [('read', {'path': 'a'}, True)] * 5)
        hm = pi_metrics(str(result_dir))
        assert hm['tools'] == ['read'] * 5  # the pre-existing shape (issue #3's complaint)
        summary = build_events_summary(str(result_dir), 'pi-fixture', 'pi', 'test-model')
        merged = merge_events_into_harness_metrics(hm, summary)

    assert 'tools' not in merged
    assert merged['callSequence'] == [['read', merged['callSequence'][0][1], 5]]


def test_merge_is_noop_when_no_events_summary():
    hm = {'events': {}, 'turns': 1}
    assert merge_events_into_harness_metrics(hm, None) is hm
    assert merge_events_into_harness_metrics(None, {'toolCalls': 1}) is None


# ---------------------------------------------------------------------------
# dsh fixture sanity (exercises the real tarball/zstd path, not synthetic events)
# ---------------------------------------------------------------------------

def test_dsh_events_summary_from_real_tarball_fixture():
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        result_dir = write_dsh_result(tmp_path, [
            ('read', {'file_path': 'a'}, True),
            ('write', {'file_path': 'b'}, True),
        ])
        summary = build_events_summary(str(result_dir), 'dsh-fixture', 'dsh', 'test-model')

    assert summary['toolCalls'] == 2
    assert summary['mutatingCalls'] == 1
    assert summary['toolOutcomes'] == {'ok': 2, 'error': 0, 'unknown': 0}


# ---------------------------------------------------------------------------
# Real captures on disk (skipped if not present -- see EVENTS.md provenance)
# ---------------------------------------------------------------------------

def _assert_no_solution_code(summary):
    """The synthetic fixtures can only prove the leak check against strings
    the test itself planted. These two run against real traces, whose `edit`
    arguments are actual agent-written solution code for the held-out spec --
    the thing CANARY.md says must never reach a published artefact. This is
    the only assertion in the suite backed by real content.
    """
    blob = json.dumps(summary)
    for marker in ('SPEC.md', 'import ', 'export ', 'function ', 'oldText',
                   'newText', 'file_path', 'Math.floor', 'not implemented'):
        assert marker not in blob, f'argument text reached the published summary: {marker!r}'


PI_CAPTURE_DIR = os.path.expanduser('~/.cache/oaken-bench/schema-capture-pi')
DSH_CAPTURE_DIR = os.path.expanduser('~/.cache/oaken-bench/schema-capture-dsh')


@pytest.mark.skipif(not os.path.isdir(PI_CAPTURE_DIR), reason='no local pi capture at ~/.cache/oaken-bench')
def test_pi_capture_events_summary_matches_events_md():
    summary = build_events_summary(PI_CAPTURE_DIR, 'schema-capture-pi', 'pi', 'gemma-capture')
    assert summary['toolCalls'] == 35
    assert summary['toolOutcomes']['error'] == 4
    assert summary['schemaVersion'] == EVENTS_SUMMARY_SCHEMA_VERSION
    assert 'args' not in summary  # digests only -- see call_metrics() docstring
    _assert_no_solution_code(summary)


@pytest.mark.skipif(not os.path.isdir(DSH_CAPTURE_DIR), reason='no local dsh capture at ~/.cache/oaken-bench')
def test_dsh_capture_events_summary_matches_events_md():
    summary = build_events_summary(DSH_CAPTURE_DIR, 'schema-capture-dsh', 'dsh', 'gemma-capture')
    assert summary['toolCalls'] == 37
    assert summary['toolOutcomes']['error'] == 7
    assert summary['schemaVersion'] == EVENTS_SUMMARY_SCHEMA_VERSION
    assert 'args' not in summary
    _assert_no_solution_code(summary)
