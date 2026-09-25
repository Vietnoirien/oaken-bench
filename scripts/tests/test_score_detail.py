"""Tests for issue #16: score.py's --detail path was unreachable.

main() never passed detail=True to score_in_container(), and there was no
CLI flag to ask for it, so hidden-detail.json was never written for any
run even though the module docstring promised it. score_in_container()
itself shells out to `docker run`, which this box does not have, so these
tests fake it out (monkeypatching score.score_in_container) rather than
exercising the real container path -- the thing under test is main()'s
plumbing, not the container. docker/score_detail.py (the per-test digest
logic that actually runs inside the container) is tested separately below,
directly, since it is plain stdlib and needs no container either.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import score  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def write_restored_result(tmp_path, label='fixture-run'):
    """A minimal result directory that looks 'restored' to main() -- that
    is the branch that calls score_in_container() at all. Also carries a
    trivial pi-events.jsonl so build_events_summary() actually produces
    events-summary.json, matching what a real run looks like closely
    enough for test_hidden_detail_never_reaches_score_json below to be a
    real check rather than a no-op on a file that's never written."""
    result_dir = tmp_path / label
    result_dir.mkdir()
    (result_dir / 'workspace.tgz').write_bytes(b'')
    (result_dir / 'run.meta').write_text('harness=pi model=test-model timeout=240')
    (result_dir / 'exit.code').write_text('0')
    (result_dir / 'wallclock.seconds').write_text('100')
    events = [
        {'type': 'turn_start'},
        {'type': 'tool_execution_start', 'toolCallId': 'call0',
         'toolName': 'read', 'args': {'path': 'a'}},
        {'type': 'tool_execution_end', 'toolCallId': 'call0',
         'toolName': 'read', 'isError': False, 'result': {}},
    ]
    with open(result_dir / 'pi-events.jsonl', 'w') as f:
        for e in events:
            f.write(json.dumps(e) + '\n')
    return result_dir


def hidden_detail_payload(digest='a' * 16, status='passed'):
    """Shaped like docker/score_detail.py's build_detail('hidden', ...)
    output: file-level counts (basenames only) plus per-test digests,
    never plaintext held-out test names."""
    return {'detailVersion': 1,
            'files': [{'file': 'h1.test.ts', 'passed': 1, 'failed': 0, 'total': 1}],
            'tests': [{'digest': digest, 'status': status}]}


def fake_score_in_container(detail_seen, payload):
    """Stands in for the real container call: records whether `detail` was
    passed truthy, and returns a hidden-suite dict shaped like
    docker/scorer.sh's output -- with a 'detail' key present only when
    detail was requested, matching score_detail.py's own
    `if detail_on or tag == 'visible'` gate."""
    def _fake(result_dir, detail=False, timeout=1800):
        detail_seen.append(detail)
        hidden = {'passed': 1, 'failed': 1, 'total': 2, 'files': 1}
        if detail:
            hidden['detail'] = payload
        visible = {'passed': 1, 'failed': 0, 'total': 1, 'files': 1,
                   'detail': {'detailVersion': 1,
                              'files': [{'file': 'v1.test.ts', 'passed': 1,
                                         'failed': 0, 'total': 1}],
                              'tests': [{'file': 'v1.test.ts', 'name': 'a visible test',
                                         'status': 'passed'}]}}
        return visible, hidden, {'clean': True}, []
    return _fake


def run_main(monkeypatch, argv, detail_seen, payload=None):
    monkeypatch.setattr(
        score, 'score_in_container',
        fake_score_in_container(detail_seen, payload or hidden_detail_payload()))
    monkeypatch.setattr(sys, 'argv', ['score.py'] + argv)
    score.main()


def test_detail_defaults_on_and_writes_hidden_detail_json(tmp_path, monkeypatch):
    """The bug: detail was always False because nothing ever set it. Fixed
    behaviour defaults it on, per the issue's own recommendation -- the
    file is gitignored (see .gitignore's /results/*/* block) so there is
    no cost to always writing it."""
    result_dir = write_restored_result(tmp_path)
    detail_seen = []
    payload = hidden_detail_payload()
    run_main(monkeypatch, [str(result_dir)], detail_seen, payload=payload)

    assert detail_seen == [True]
    out_path = result_dir / 'hidden-detail.json'
    assert out_path.exists()
    assert json.load(open(out_path)) == payload


def test_no_detail_flag_disables_it(tmp_path, monkeypatch):
    """--no-detail is the escape hatch for a caller who wants score.json's
    counts without the per-test file sitting next to it; when given, no
    hidden-detail.json should appear at all."""
    result_dir = write_restored_result(tmp_path)
    detail_seen = []
    run_main(monkeypatch, [str(result_dir), '--no-detail'], detail_seen)

    assert detail_seen == [False]
    assert not (result_dir / 'hidden-detail.json').exists()


def test_explicit_detail_flag_is_accepted_and_is_a_noop(tmp_path, monkeypatch):
    """--detail is kept for parity with docker/entrypoint.sh's flag of the
    same name; since detail already defaults to True, passing it must not
    be treated as a second positional (result_dir) argument."""
    result_dir = write_restored_result(tmp_path)
    detail_seen = []
    run_main(monkeypatch, [str(result_dir), '--detail'], detail_seen)

    assert detail_seen == [True]
    assert (result_dir / 'hidden-detail.json').exists()


def test_both_flags_together_do_not_swallow_result_dir(tmp_path, monkeypatch):
    """Regression for the flag-parsing bug the first code-review pass
    caught: filtering only whichever flag an if/elif happened to match
    left the other flag in argv, which could be consumed as result_dir.
    Both flags stripped unconditionally, in any order, must still leave
    result_dir as the sole positional."""
    result_dir = write_restored_result(tmp_path)
    detail_seen = []
    run_main(monkeypatch, ['--detail', '--no-detail', str(result_dir)], detail_seen)

    assert detail_seen == [False]
    assert (result_dir / 'score.json').exists()


def test_hidden_detail_never_reaches_score_json_or_events_summary(tmp_path, monkeypatch):
    """hidden-detail.json holding held-out per-test digests must stay a
    separate, gitignored artefact -- score.json and events-summary.json
    are both published (AGENTS.md), so the per-test detail must not be
    copied into either. A recognisable digest stands in for what would be
    a real leak; it must not appear anywhere in either published file."""
    result_dir = write_restored_result(tmp_path)
    detail_seen = []
    sentinel_digest = 'deadbeefcafef00d'
    run_main(monkeypatch, [str(result_dir)], detail_seen,
             payload=hidden_detail_payload(digest=sentinel_digest))

    score_json = json.load(open(result_dir / 'score.json'))
    assert sentinel_digest not in json.dumps(score_json)
    assert 'detail' not in score_json['hidden']

    events_summary_path = result_dir / 'events-summary.json'
    assert events_summary_path.exists(), (
        'fixture should produce events-summary.json (real pi-events.jsonl); '
        'if this stops being written the assertion below is checking nothing')
    assert sentinel_digest not in open(events_summary_path).read()


def test_stale_hidden_detail_json_is_removed_on_no_detail_rescore(tmp_path, monkeypatch):
    """Re-scoring a label with --no-detail after a previous detailed pass
    must not leave the OLD hidden-detail.json sitting next to the NEW
    score.json -- that pairing would silently claim the old per-test
    detail still describes the new score."""
    result_dir = write_restored_result(tmp_path)
    stale_path = result_dir / 'hidden-detail.json'
    stale_path.write_text(json.dumps({'stale': True}))

    detail_seen = []
    run_main(monkeypatch, [str(result_dir), '--no-detail'], detail_seen)

    assert not stale_path.exists()


def test_stale_hidden_detail_json_is_removed_when_container_reports_no_detail(tmp_path, monkeypatch):
    """Same stale-file hazard, but triggered by the container side: a
    decrypt __error or a __hung hidden suite means score_in_container()
    returns a hidden dict with no 'detail' key even though `detail=True`
    was requested. A leftover file from an earlier successful pass must
    still be cleared."""
    result_dir = write_restored_result(tmp_path)
    stale_path = result_dir / 'hidden-detail.json'
    stale_path.write_text(json.dumps({'stale': True}))

    def _fake_error(result_dir, detail=False, timeout=1800):
        hidden = {'__error': 'could not decrypt held-out suite'}
        visible = {'passed': 1, 'failed': 0, 'total': 1, 'files': 1}
        return visible, hidden, {'clean': True}, []

    monkeypatch.setattr(score, 'score_in_container', _fake_error)
    monkeypatch.setattr(sys, 'argv', ['score.py', str(result_dir)])
    score.main()

    assert not stale_path.exists()


def test_stale_hidden_detail_json_is_removed_when_not_restored(tmp_path, monkeypatch):
    """A label whose workspace.tgz is missing entirely never reaches
    score_in_container() at all -- the stale-file cleanup must still run
    for it, since a leftover hidden-detail.json next to a
    restored=False score.json is just as misleading."""
    result_dir = tmp_path / 'not-restored'
    result_dir.mkdir()
    (result_dir / 'run.meta').write_text('harness=pi model=test-model timeout=240')
    (result_dir / 'exit.code').write_text('1')
    stale_path = result_dir / 'hidden-detail.json'
    stale_path.write_text(json.dumps({'stale': True}))

    monkeypatch.setattr(sys, 'argv', ['score.py', str(result_dir)])
    score.main()

    assert not stale_path.exists()
    score_json = json.load(open(result_dir / 'score.json'))
    assert score_json['restored'] is False


def test_hidden_detail_json_is_gitignored():
    """Belt-and-braces check on the .gitignore rule itself, since that is
    what makes 'default detail on' safe per the issue's own reasoning."""
    gitignore = open(os.path.join(REPO_ROOT, '.gitignore')).read()
    assert '/results/*/*' in gitignore
    assert '!/results/*/hidden-detail.json' not in gitignore


# ---------------------------------------------------------------------------
# docker/score_detail.py -- the per-test digest logic that actually runs
# inside the container. Plain stdlib, so it's testable directly from the
# host without docker or vitest: feed it a vitest --reporter=json-shaped
# dict and check what it builds.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(REPO_ROOT, 'docker'))
import score_detail  # noqa: E402


FAKE_VITEST_JSON = {
    'numPassedTests': 2, 'numFailedTests': 1, 'numTotalTests': 3,
    'testResults': [
        {'name': '/work/tests/hidden/auth.test.ts',
         'assertionResults': [
             {'fullName': 'does the secret thing correctly', 'title': 'x', 'status': 'passed'},
             {'fullName': 'does the other secret thing', 'title': 'y', 'status': 'failed'},
         ]},
        {'name': '/work/tests/hidden/another.test.ts',
         'assertionResults': [
             {'fullName': 'a third secret assertion', 'title': 'z', 'status': 'passed'},
         ]},
    ],
}


def test_score_detail_hidden_tag_never_emits_plaintext_names():
    # File basenames ('auth.test.ts') are intentionally plaintext per
    # build_detail()'s docstring -- which files exist is not secret. Only
    # each individual assertion's text is; that's what this checks for.
    detail = score_detail.build_detail('hidden', FAKE_VITEST_JSON)

    blob = json.dumps(detail)
    for secret in ('does the secret thing', 'does the other secret',
                   'a third secret assertion'):
        assert secret not in blob, f'held-out test text leaked into hidden detail: {secret!r}'

    assert detail['detailVersion'] == 1
    assert {f['file'] for f in detail['files']} == {'auth.test.ts', 'another.test.ts'}
    assert len(detail['tests']) == 3
    for t in detail['tests']:
        assert set(t) == {'digest', 'status'}
        assert len(t['digest']) == 16
        int(t['digest'], 16)  # hex


def test_score_detail_visible_tag_keeps_plaintext_names():
    detail = score_detail.build_detail('visible', FAKE_VITEST_JSON)

    names = {t['name'] for t in detail['tests']}
    assert 'does the secret thing correctly' in names
    for t in detail['tests']:
        assert set(t) == {'file', 'name', 'status'}


def test_score_detail_digest_is_stable_and_content_addressed():
    """Two runs of the same held-out suite, scored separately, must
    produce identical digests for the same test -- that comparability is
    the entire point of issue #16's motivating question. A different test
    name must produce a different digest."""
    d1 = score_detail.test_digest('h1.test.ts', 'some test name')
    d2 = score_detail.test_digest('h1.test.ts', 'some test name')
    d3 = score_detail.test_digest('h1.test.ts', 'a different test name')
    d4 = score_detail.test_digest('h2.test.ts', 'some test name')

    assert d1 == d2
    assert d1 != d3
    assert d1 != d4  # file basename is part of the digest input too
    assert len(d1) == 16


def test_score_detail_file_counts_match_assertion_statuses():
    detail = score_detail.build_detail('hidden', FAKE_VITEST_JSON)
    by_file = {f['file']: f for f in detail['files']}
    assert by_file['auth.test.ts'] == {
        'file': 'auth.test.ts', 'passed': 1, 'failed': 1, 'total': 2}
    assert by_file['another.test.ts'] == {
        'file': 'another.test.ts', 'passed': 1, 'failed': 0, 'total': 1}
