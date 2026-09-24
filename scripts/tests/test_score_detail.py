"""Tests for issue #16: score.py's --detail path was unreachable.

main() never passed detail=True to score_in_container(), and there was no
CLI flag to ask for it, so hidden-detail.json was never written for any
run even though the module docstring promised it. score_in_container()
itself shells out to `docker run`, which this box does not have, so these
tests fake it out (monkeypatching score.score_in_container) rather than
exercising the real container path -- the thing under test is main()'s
plumbing, not the container.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import score  # noqa: E402


def write_restored_result(tmp_path, label='fixture-run'):
    """A minimal result directory that looks 'restored' to main() -- that
    is the branch that calls score_in_container() at all."""
    result_dir = tmp_path / label
    result_dir.mkdir()
    (result_dir / 'workspace.tgz').write_bytes(b'')
    (result_dir / 'run.meta').write_text('harness=pi model=test-model timeout=240')
    (result_dir / 'exit.code').write_text('0')
    (result_dir / 'wallclock.seconds').write_text('100')
    return result_dir


def fake_score_in_container(detail_seen, hidden_detail_payload):
    """Stands in for the real container call: records whether `detail` was
    passed truthy, and returns a hidden-suite dict shaped like
    docker/scorer.sh's output -- with a 'detail' key present only when
    detail was requested, matching the real script's `if detail or
    tag=='visible'` gate."""
    def _fake(result_dir, detail=False, timeout=1800):
        detail_seen.append(detail)
        hidden = {'passed': 1, 'failed': 1, 'total': 2, 'files': 1}
        if detail:
            hidden['detail'] = hidden_detail_payload
        visible = {'passed': 1, 'failed': 0, 'total': 1, 'files': 1,
                   'detail': [{'name': 'visible.test.ts', 'status': 'passed'}]}
        return visible, hidden, {'clean': True}, []
    return _fake


def run_main(monkeypatch, argv, detail_seen, hidden_detail_payload=None):
    monkeypatch.setattr(
        score, 'score_in_container',
        fake_score_in_container(detail_seen, hidden_detail_payload or
                                 [{'name': 'h1.test.ts', 'status': 'failed'}]))
    monkeypatch.setattr(sys, 'argv', ['score.py'] + argv)
    score.main()


def test_detail_defaults_on_and_writes_hidden_detail_json(tmp_path, monkeypatch):
    """The bug: detail was always False because nothing ever set it. Fixed
    behaviour defaults it on, per the issue's own recommendation -- the
    file is gitignored (see .gitignore's /results/*/* block) so there is
    no cost to always writing it."""
    result_dir = write_restored_result(tmp_path)
    detail_seen = []
    run_main(monkeypatch, [str(result_dir)], detail_seen)

    assert detail_seen == [True]
    out_path = result_dir / 'hidden-detail.json'
    assert out_path.exists()
    assert json.load(open(out_path)) == [{'name': 'h1.test.ts', 'status': 'failed'}]


def test_no_detail_flag_disables_it(tmp_path, monkeypatch):
    """--no-detail is the escape hatch for a faster scoring pass; when
    given, no hidden-detail.json should appear at all."""
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


def test_hidden_detail_never_reaches_score_json(tmp_path, monkeypatch):
    """hidden-detail.json holding held-out test file names must stay a
    separate, gitignored artefact -- score.json is published (AGENTS.md),
    so the per-test detail must not be copied into report['hidden']."""
    result_dir = write_restored_result(tmp_path)
    detail_seen = []
    run_main(monkeypatch, [str(result_dir)], detail_seen,
             hidden_detail_payload=[{'name': 'super-secret-hidden-file.test.ts',
                                      'status': 'passed'}])

    score_json = json.load(open(result_dir / 'score.json'))
    blob = json.dumps(score_json)
    assert 'super-secret-hidden-file' not in blob
    assert 'detail' not in score_json['hidden']

    events_summary_path = result_dir / 'events-summary.json'
    if events_summary_path.exists():
        assert 'super-secret-hidden-file' not in open(events_summary_path).read()


def test_hidden_detail_json_is_gitignored():
    """Belt-and-braces check on the .gitignore rule itself, since that is
    what makes 'default detail on' safe per the issue's own reasoning."""
    gitignore = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), '.gitignore')).read()
    assert '/results/*/*' in gitignore
    assert '!/results/*/hidden-detail.json' not in gitignore
