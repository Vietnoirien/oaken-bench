"""Public plumbing tests. Fixtures below contain no real oracle test cases."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import t4
import tiers

spec = importlib.util.spec_from_file_location('t4_runtime', t4.ROOT / 'docker/t4_runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def report_fixture():
    def suite(total, passed):
        return {'passed': passed, 'failed': total-passed, 'total': total,
                'collected': total, 'uncollected': 0, 'hung': False,
                'reportAvailable': True, 'privateSource': 'never publish'}
    return {'suites': {'v1.0': suite(132, 132), 'v1.1': suite(137, 2)},
            'typecheckClean': True, 'tamperedFrozenFiles': [], 'testNames': ['never publish']}


def test_dispatch_and_summary_never_sum_versions(tmp_path, monkeypatch, capsys):
    import score
    import summarize
    path = tmp_path / 't4-fixture'
    path.mkdir()
    (path / 'workspace.tgz').write_bytes(b'fixture')
    monkeypatch.setattr(t4, 'stage_inputs', lambda *a: None)
    def fake_container(mode, inputs, out):
        (out / 'counts.json').write_text(json.dumps(report_fixture()))
    monkeypatch.setattr(t4, 'container', fake_container)
    monkeypatch.setattr(sys, 'argv', ['score.py', str(path)])
    score.main()
    data = json.loads((path / 'score.json').read_text())
    assert data['tier'] == 't4'
    assert set(data['suites']) == {'v1.0', 'v1.1'}
    assert 'total' not in data and 'hidden' not in data and 'rate' not in data
    assert 'never publish' not in json.dumps(data)
    row = summarize.build_row(tmp_path, path.name)
    tiers.tier_by_id('t4').print_rows('T4', [row])
    out = capsys.readouterr().out
    assert 'v1.0 132/132; v1.1 2/137' in out
    assert '/269' not in out
    assert tiers.resolve_tier('t5-existing').id == 't5'


def test_assembly_mount_has_no_oracle_or_private_drafts(tmp_path):
    t4.stage_inputs(tmp_path, 'assemble')
    assert (tmp_path / 'refengine.tar.gz.enc').is_file()
    assert (tmp_path / 't4/SPEC-v1.1.md').is_file()
    assert not (tmp_path / 'hidden.tar.gz.enc').exists()
    assert not (tmp_path / 't4oracle.tar.gz.enc').exists()
    assert not (tmp_path / 't4/validate.py').exists()
    assert not (tmp_path / 'seed/node_modules').exists()


def test_scoring_mount_has_both_seals_but_no_reference_plaintext(tmp_path):
    inp = tmp_path / 'input'; inp.mkdir()
    (tmp_path / 'workspace.tgz').write_bytes(b'fake workspace')
    t4.stage_inputs(inp, 'score', tmp_path)
    assert (inp / 'hidden.tar.gz.enc').is_file()
    assert (inp / 't4oracle.tar.gz.enc').is_file()
    assert not (inp / 'refengine.tar.gz.enc').exists()
    assert not (inp / 't4oracle').exists()


def test_fixed_denominator_on_partial_or_failed_collection():
    raw = {'testResults': [{'assertionResults': [{'status': 'passed'}, {'status': 'failed'}]}],
           'numTotalTests': 999, 'numPassedTests': 999}
    assert runtime.counts(raw, 7) == {'passed': 1, 'failed': 6, 'total': 7,
                                     'collected': 2, 'uncollected': 5, 'rate': 0.142857}
    assert runtime.counts({}, 137)['failed'] == 137
    with pytest.raises(ValueError):
        runtime.counts(raw, 1)


@pytest.mark.parametrize('field,value', [('passed', 138), ('collected', -1),
                                         ('uncollected', 1), ('total', 10), ('passed', True)])
def test_invalid_counts_rejected(field, value):
    raw = report_fixture()['suites']['v1.1']; raw[field] = value
    with pytest.raises(ValueError):
        t4.read_counts(raw, 137)


@pytest.mark.parametrize('name,kind', [('../escape', 'file'), ('/escape', 'file'),
                                       ('work/src/link', 'symlink'), ('work/src/link', 'hardlink')])
def test_workspace_traversal_and_links_rejected(tmp_path, name, kind):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        info = tarfile.TarInfo(name)
        if kind == 'symlink': info.type = tarfile.SYMTYPE; info.linkname = '/tmp/outside'
        if kind == 'hardlink': info.type = tarfile.LNKTYPE; info.linkname = '/tmp/outside'
        tar.addfile(info)
    with pytest.raises(ValueError):
        runtime.extract(buf.getvalue(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_container_is_cpu_only_offline_and_removed_even_on_failure(tmp_path, monkeypatch):
    import score
    calls = []
    monkeypatch.setattr(score, 'sh', lambda cmd, **kw: (calls.append(cmd) or (1, '', '')))
    removals = []
    monkeypatch.setattr(subprocess, 'run', lambda cmd, **kw: removals.append(cmd))
    with pytest.raises(RuntimeError):
        t4.container('score', tmp_path, tmp_path)
    cmd = calls[0]
    assert cmd[cmd.index('--network')+1] == 'none'
    assert '--gpus' not in cmd and '--device' not in cmd
    assert '--read-only' in cmd and '--tmpfs' in cmd
    assert not any('OAKEN_REFENGINE_PASS' in str(x) for x in cmd)
    assert removals[0][:3] == ['docker', 'rm', '-f']


def test_failed_assembly_removes_partial_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(t4, 'stage_inputs', lambda *a: None)
    def failure(*args): raise RuntimeError('fixture')
    monkeypatch.setattr(t4, 'container', failure)
    with pytest.raises(RuntimeError): t4.assemble(tmp_path / 'new')
    assert not (tmp_path / 'new').exists()
    occupied = tmp_path / 'existing'; occupied.mkdir()
    (occupied / 'keep').write_text('keep')
    with pytest.raises(FileExistsError): t4.assemble(occupied)
    assert (occupied / 'keep').read_text() == 'keep'


def test_frozen_manifest_rejects_changed_inputs(tmp_path):
    (tmp_path / 'FROZEN.sha256').write_text('0' * 64 + '  input.txt\n')
    (tmp_path / 'input.txt').write_text('changed')
    with pytest.raises(ValueError, match='frozen input drift'):
        t4.verify_frozen(tmp_path)


def test_scorer_ignores_candidate_tests_and_configuration(tmp_path, monkeypatch):
    frozen = tmp_path / 'config'; frozen.write_text('trusted config')
    source = tmp_path / 'source'; (source / 'src').mkdir(parents=True)
    (source / 'src/engine.ts').write_text('candidate module')
    (source / 'test.test.ts').write_text('candidate test must not run')
    (source / 'vitest.config.ts').write_text('candidate configuration')
    monkeypatch.setattr(runtime, 'frozen_files', lambda: {'vitest.config.ts': frozen})
    deps = tmp_path / 'deps'; deps.mkdir()
    monkeypatch.setattr(runtime, 'DEPS', deps)
    result = runtime.candidate_workspace(source, tmp_path / 'work')
    assert (result / 'src/engine.ts').read_text() == 'candidate module'
    assert (result / 'vitest.config.ts').read_text() == 'trusted config'
    assert not (result / 'test.test.ts').exists()


@pytest.mark.parametrize('port,harness,url', [('', 'pi', ''), ('8080', 'pi', ''),
                                             ('8081', 'pi', ''), ('49199', 'dsh', ''),
                                             ('49199', 'pi', 'http://example.test/v1')])
def test_offline_smoke_requires_pi_and_an_isolated_explicit_port(port, harness, url):
    env = {**os.environ, 'OAKEN_T4_OFFLINE_SMOKE': '1', 'OAKEN_SERVER_PORT': port,
           'OAKEN_SERVER_URL': url}
    result = subprocess.run(['bash', str(t4.ROOT / 'run.sh'), harness, 'fixture',
                             't4-rejected-offline-fixture', '1'], env=env, capture_output=True, text=True)
    assert result.returncode == 64
    assert 'isolated port' in result.stderr
    assert not (t4.ROOT / 'results/t4-rejected-offline-fixture').exists()
