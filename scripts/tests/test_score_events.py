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
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from score import (  # noqa: E402
    EVENTS_SUMMARY_SCHEMA_VERSION, HARNESS_METRICS_SCHEMA_VERSION,
    build_events_summary, dsh_metrics, merge_events_into_harness_metrics,
    pi_metrics,
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


def write_pi_result_raw(tmp_path, events, meta='harness=pi model=test-model timeout=240'):
    """Like write_pi_result(), but takes a raw list of already-shaped pi
    event dicts instead of (tool, args, ok) rows -- for tests that need to
    control event types directly (compaction_start/_end, message_end vs
    turn_end usage), rather than the tool-call triple pi_stream()/
    write_pi_result() build."""
    result_dir = tmp_path / 'pi-fixture'
    result_dir.mkdir()
    with open(result_dir / 'pi-events.jsonl', 'w') as f:
        for e in events:
            f.write(json.dumps(e) + '\n')
    (result_dir / 'run.meta').write_text(meta)
    return result_dir


def write_dsh_tarball(tmp_path, sessions, meta='harness=dsh model=test-model timeout=240'):
    """Builds a dsh-sessions.tgz holding one or more session files.

    `sessions` is a list of (session_id, header, body_events, mtime_offset)
    tuples. `header` is written as the session's first jsonl line (its
    `delegationDepth`/`parentSession` decide root-vs-subagent, per
    EVENTS.md); `body_events` follow it. `mtime_offset` (seconds, may be
    negative) sets the compressed file's mtime relative to "now", so a
    test can construct a tarball where the subagent's file is NEWER than
    the root's -- the exact inversion issue #11 is about.
    """
    result_dir = tmp_path / 'dsh-fixture'
    result_dir.mkdir()
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, '.dsh', 'sessions', 'slug')
        os.makedirs(base)
        zpaths = []
        for session_id, header, body_events, mtime_offset in sessions:
            sdir = os.path.join(base, session_id)
            os.makedirs(sdir)
            jsonl_path = os.path.join(sdir, 'session.v3.jsonl')
            with open(jsonl_path, 'w') as f:
                for e in [header] + list(body_events):
                    f.write(json.dumps(e) + '\n')
            subprocess.run(['zstd', '-f', '-q', jsonl_path, '-o', jsonl_path + '.zstd'], check=True)
            os.remove(jsonl_path)
            zpaths.append((jsonl_path + '.zstd', mtime_offset))

        now = time.time()
        for zpath, offset in zpaths:
            t = now + offset
            os.utime(zpath, (t, t))

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
# Regressions: #11 dsh root-session selection, #10 compaction double-count,
# #9 usage summed from streaming partials
# ---------------------------------------------------------------------------

def test_dsh_metrics_selects_root_session_even_when_subagent_is_newer():
    """Issue #11. sessions.sort(key=os.path.getmtime); sessions[-1] would
    pick the subagent here, because its file is written after the root's
    and so has the newer mtime -- exactly the inversion EVENTS.md
    describes for a subagent that outlives its parent. dsh_metrics() must
    select on delegationDepth==0 instead."""
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        root_header = {'type': 'session', 'id': 'root-id', 'delegationDepth': 0}
        root_body = [
            {'type': 'step/start', 'seq': 1, 'time': 1, 'data': {'turn': 1, 'step': 1}},
            {'type': 'tool/call', 'seq': 2, 'time': 2,
             'data': {'turn': 1, 'step': 1, 'callId': 'c0', 'name': 'read', 'arguments': '{}'}},
        ]
        sub_header = {'type': 'session', 'id': 'sub-id', 'parentSession': 'root-id', 'delegationDepth': 1}
        sub_body = [
            {'type': 'step/start', 'seq': 1, 'time': 1, 'data': {'turn': 1, 'step': 1}},
        ]
        result_dir = write_dsh_tarball(tmp_path, [
            ('root-session', root_header, root_body, -100),  # older mtime
            ('sub-session', sub_header, sub_body, 0),         # newer mtime -- would win by mtime
        ])
        hm = dsh_metrics(str(result_dir))

    # The subagent transcript has no tool/call at all; picking it would
    # report 0 tool calls for a run that made one.
    assert hm['toolCalls'] == 1
    assert not hm.get('rootSessionAmbiguous')


def test_dsh_metrics_reports_ambiguity_when_more_than_one_root_session():
    """Two root sessions in one tarball is a real ambiguity (EVENTS.md /
    issue #11's "Proposed" section): it must be surfaced, not silently
    resolved by picking one."""
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        header_a = {'type': 'session', 'id': 'a', 'delegationDepth': 0}
        header_b = {'type': 'session', 'id': 'b', 'delegationDepth': 0}
        result_dir = write_dsh_tarball(tmp_path, [
            ('session-a', header_a, [], 0),
            ('session-b', header_b, [], 1),
        ])
        hm = dsh_metrics(str(result_dir))

    assert hm['rootSessionAmbiguous'] is True
    assert hm['rootSessionCount'] == 2


def test_pi_metrics_counts_compaction_start_once_not_plus_end():
    """Issue #10. A substring test on the event type matched both
    compaction_start and compaction_end; a real compaction (one start,
    one end) must count as 1, not 2."""
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        result_dir = write_pi_result_raw(tmp_path, [
            {'type': 'compaction_start', 'reason': 'context'},
            {'type': 'compaction_end', 'reason': 'context', 'result': 'ok',
             'aborted': False, 'willRetry': False},
        ])
        hm = pi_metrics(str(result_dir))

    assert hm['compactions'] == 1
    assert hm['compactionsAborted'] == 0


def test_pi_metrics_splits_out_aborted_compactions():
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        result_dir = write_pi_result_raw(tmp_path, [
            {'type': 'compaction_start'},
            {'type': 'compaction_end', 'aborted': True, 'willRetry': True},
            {'type': 'compaction_start'},
            {'type': 'compaction_end', 'aborted': False, 'willRetry': False},
        ])
        hm = pi_metrics(str(result_dir))

    assert hm['compactions'] == 2
    assert hm['compactionsAborted'] == 1


def test_dsh_metrics_does_not_count_the_word_compact_in_prose():
    """Issue #10. A user/message whose text happens to contain the word
    'compact' must not be counted -- the old heuristic substring-matched
    the whole serialised event, so it was."""
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        header = {'type': 'session', 'id': 'root', 'delegationDepth': 0}
        body = [
            {'type': 'user/message', 'seq': 1, 'time': 1,
             'data': {'message': {'content': [{'type': 'text',
                                                'text': 'please compact your answer'}]}}},
            {'type': 'compaction/start', 'seq': 2, 'time': 2, 'data': {'compactionId': 'c1', 'turn': 1}},
            {'type': 'compaction/end', 'seq': 3, 'time': 3, 'data': {'compactionId': 'c1', 'turn': 1}},
        ]
        result_dir = write_dsh_tarball(tmp_path, [('root-session', header, body, 0)])
        hm = dsh_metrics(str(result_dir))

    assert hm['compactions'] == 1


def test_dsh_metrics_records_pruned_tool_results_separately_from_compactions():
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        header = {'type': 'session', 'id': 'root', 'delegationDepth': 0}
        body = [
            {'type': 'compaction/prune', 'seq': 1, 'time': 1, 'data': {'shadowedTokenCount': 1000}},
            {'type': 'compaction/prune', 'seq': 2, 'time': 2, 'data': {'shadowedTokenCount': 500}},
        ]
        result_dir = write_dsh_tarball(tmp_path, [('root-session', header, body, 0)])
        hm = dsh_metrics(str(result_dir))

    assert hm['compactions'] == 0
    assert hm['prunedToolResults'] == {'count': 2, 'shadowedTokenCount': 1500}


def test_pi_metrics_usage_from_turn_end_not_also_message_end():
    """Issue #9. The same message's usage appears on both message_end and
    turn_end; summing both double-counts. Only turn_end should be read."""
    msg_usage = {'input': 100, 'output': 20, 'cacheRead': 5, 'cacheWrite': 0}
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        result_dir = write_pi_result_raw(tmp_path, [
            {'type': 'message_end', 'message': {'usage': dict(msg_usage)}},
            {'type': 'turn_end', 'message': {'usage': dict(msg_usage)}, 'toolResults': []},
        ])
        hm = pi_metrics(str(result_dir))

    assert hm['usage'] == msg_usage


def test_pi_metrics_ignores_message_update_streaming_partials():
    """Issue #9. message_update carries cumulative per-chunk snapshots
    under a top-level `usage` key; summing them (the old `e['usage']`
    path) wildly overstates every figure. They must not contribute at
    all -- only turn_end's message.usage should."""
    real_usage = {'input': 10, 'output': 2, 'cacheRead': 0, 'cacheWrite': 0}
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        result_dir = write_pi_result_raw(tmp_path, [
            {'type': 'message_update', 'usage': {'input': 9999, 'output': 9999,
                                                    'cacheRead': 9999, 'cacheWrite': 9999}},
            {'type': 'turn_end', 'message': {'usage': dict(real_usage)}, 'toolResults': []},
        ])
        hm = pi_metrics(str(result_dir))

    assert hm['usage'] == real_usage


def test_dsh_metrics_usage_ignores_non_assistant_message_events():
    """Issue #9. The old fallback (`d.get('usage') if isinstance(...) else
    d`) treated any other event's whole `data` dict as a usage record. A
    stray inputTokens-shaped `data` on an unrelated event must not be
    absorbed; only assistant/message.data.usage counts."""
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        header = {'type': 'session', 'id': 'root', 'delegationDepth': 0}
        body = [
            {'type': 'tool/result', 'seq': 1, 'time': 1,
             'data': {'inputTokens': 99999, 'outputTokens': 99999, 'cacheReadTokens': 99999}},
            {'type': 'assistant/message', 'seq': 2, 'time': 2,
             'data': {'usage': {'inputTokens': 10, 'outputTokens': 5, 'cacheReadTokens': 1}}},
        ]
        result_dir = write_dsh_tarball(tmp_path, [('root-session', header, body, 0)])
        hm = dsh_metrics(str(result_dir))

    assert hm['usage'] == {'inputTokens': 10, 'outputTokens': 5, 'cacheReadTokens': 1}


def test_pi_dsh_metrics_carry_harness_metrics_version():
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        pi_dir = write_pi_result_raw(tmp_path, [{'type': 'turn_start'}])
        assert pi_metrics(str(pi_dir))['harnessMetricsVersion'] == HARNESS_METRICS_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Hardening (found in review, no issue number): docker/entrypoint.sh:69 runs
# `tar czf dsh-sessions.tgz -C /root .dsh/sessions` unconditionally for BOTH
# harnesses. In a pi run's container, ~/.dsh/sessions never exists, so that
# command still produces a small, valid, member-less gzip tarball -- not an
# error and not an empty file. Confirmed: load_dsh_root_sessions() opens it
# fine and returns zero sessions, i.e. it loads to 0 events and 0 calls.
#
# Before this fix, _load_calls() picking the right trace for a pi run
# depended entirely on TRACES listing pi before dsh: a result dir with a
# missing/misspelt harness= line (or a reordered TRACES) would silently
# accept dsh's empty stub as the answer and publish an events-summary.json
# claiming zero tool calls for a run that made several -- indistinguishable
# from "the agent never acted".
# ---------------------------------------------------------------------------

def write_empty_dsh_tarball(result_dir):
    """The real shape entrypoint.sh's stray `tar czf` produces for a pi
    run: a syntactically valid gzip tarball with no members. Built with
    the same `tarfile` module the loader reads with, rather than shelling
    out to `tar`, so the test doesn't depend on host `tar` semantics for
    a nonexistent source directory."""
    tgz_path = result_dir / 'dsh-sessions.tgz'
    with tarfile.open(tgz_path, 'w:gz'):
        pass
    return tgz_path


def test_load_calls_falls_through_stray_empty_dsh_tarball_for_pi_run():
    """The worst case: no harness hint at all, plus the stray empty dsh
    tarball every pi result directory carries. The real pi trace must
    still win."""
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        result_dir = write_pi_result(tmp_path, [
            ('read', {'path': 'a'}, True),
            ('edit', {'path': 'b'}, True),
            ('bash', {'cmd': 'ls'}, True),
        ], meta='')  # no harness= line at all
        write_empty_dsh_tarball(result_dir)

        summary = build_events_summary(str(result_dir), 'pi-fixture', None, 'test-model')

    assert summary is not None
    assert summary['toolCalls'] == 3


def test_load_calls_falls_through_even_when_trace_order_is_reversed():
    """Same regression, forced the other way: the old code was correct
    only because TRACES happens to list pi first. If that order were ever
    swapped, the empty dsh stub must still not win over a real pi trace."""
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        result_dir = write_pi_result(tmp_path, [('read', {'path': 'a'}, True)] * 5, meta='')
        write_empty_dsh_tarball(result_dir)

        import score
        original_traces = score.TRACES
        score.TRACES = tuple(reversed(original_traces))
        try:
            summary = build_events_summary(str(result_dir), 'pi-fixture', None, 'test-model')
        finally:
            score.TRACES = original_traces

    assert summary is not None
    assert summary['toolCalls'] == 5


def test_load_calls_reports_a_genuinely_empty_trace_as_zero_not_none():
    """The fallthrough must not turn a real zero-tool-call run into a
    missing summary: if every candidate trace is empty (a pi run that
    made no calls, sitting next to the usual empty dsh stub), the first
    one is still used, with zeroed -- not absent -- metrics."""
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp_path = pathlib.Path(td)
        result_dir = write_pi_result(tmp_path, [], meta='harness=pi model=test-model timeout=240')
        write_empty_dsh_tarball(result_dir)

        summary = build_events_summary(str(result_dir), 'pi-fixture', 'pi', 'test-model')

    assert summary is not None
    assert summary['toolCalls'] == 0


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


# ---------------------------------------------------------------------------
# harnessMetrics (pi_metrics()/dsh_metrics()) against the real captures --
# the regression oracle for issues #9, #10, #11. These are the aggregate
# figures quoted in the issues and in EVENTS.md, checked against the
# captured traces rather than trusted from the doc.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not os.path.isdir(PI_CAPTURE_DIR), reason='no local pi capture at ~/.cache/oaken-bench')
def test_pi_metrics_matches_capture_after_fix():
    hm = pi_metrics(PI_CAPTURE_DIR)
    assert hm['compactions'] == 6
    assert hm['compactionsAborted'] == 0
    assert hm['usage'] == {'input': 146608, 'output': 17785, 'cacheRead': 418442, 'cacheWrite': 0}


@pytest.mark.skipif(not os.path.isdir(DSH_CAPTURE_DIR), reason='no local dsh capture at ~/.cache/oaken-bench')
def test_dsh_metrics_matches_capture_after_fix():
    hm = dsh_metrics(DSH_CAPTURE_DIR)
    assert hm['compactions'] == 2
    assert hm['prunedToolResults']['count'] == 3
    assert not hm.get('rootSessionAmbiguous')
