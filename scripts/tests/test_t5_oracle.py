"""T5's sealed pool and scorer stay separate from the published T2 scores."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from t5_oracle import ARCHIVE, DIGEST, META, ROOT, behavior_for_run, open_pool, score_run  # noqa: E402
from tiers import resolve_tier  # noqa: E402

NODE = os.environ.get('OAKEN_T5_NODE') or shutil.which('node')


def require_node(monkeypatch):
    if not NODE:
        pytest.skip('Node 24 with TypeScript support required')
    check = subprocess.run([NODE, '-e',
                            'process.exit(Number(process.versions.node.split(".")[0]) >= 24 && process.features.typescript ? 0 : 1)'])
    if check.returncode:
        pytest.skip('Node 24 with TypeScript support required')
    monkeypatch.setenv('OAKEN_T5_NODE', NODE)


def test_namespace():
    assert resolve_tier('t5-cheapest-01').id == 't5'
    assert resolve_tier('pi-01').id == 't2'
    assert resolve_tier('t5-cheapest-01').score is not resolve_tier('pi-01').score


def test_behavior_reads_archived_pi_trace_without_publishing_arguments(tmp_path, monkeypatch):
    result = tmp_path / 'results' / 't5-synthetic'
    result.mkdir(parents=True)
    archive = tmp_path / 'archive'
    (archive / result.name).mkdir(parents=True)
    bot = tmp_path / 'bot.ts'
    bot.write_text('export function decide(state) { return state.botSeed === 7 ? [] : []; }')
    events = [
        {'type': 'tool_execution_start', 'toolCallId': 'a', 'toolName': 'bash',
         'args': {'cmd': 't5-sim play --bot bot.ts --vs baseline:random; secret-tool-arg'}},
        {'type': 'tool_execution_end', 'toolCallId': 'a', 'isError': False,
         'result': {'content': [{'type': 'text', 'text': 'secret-result'}]}},
    ]
    (archive / result.name / 'pi-events.jsonl').write_text(
        ''.join(json.dumps(e) + '\n' for e in events))
    monkeypatch.setenv('OAKEN_ARCHIVE', str(archive))
    metrics = behavior_for_run(result, str(bot), 0.5)
    assert metrics['traceAvailable'] is True
    assert metrics['simulationsRun'] == 1
    assert metrics['strategiesTried'] == 1
    assert metrics['visibleSeedHardCoding']['sourceSeedLiteralCount'] == 1
    assert 'secret' not in json.dumps(metrics)
    assert 'bot.ts' not in json.dumps(metrics)


def test_seal_roundtrip(tmp_path):
    root = tmp_path / 'bundle-root'
    root.mkdir()
    (root / ARCHIVE.name).write_bytes(ARCHIVE.read_bytes())
    (root / DIGEST.name).write_bytes(DIGEST.read_bytes())
    env = dict(os.environ, OAKEN_BENCH_ROOT=str(root))
    subprocess.run([str(ROOT / 'scripts/hidden.sh'), 'unlock', 't5oracle'],
                   check=True, env=env, capture_output=True)
    subprocess.run([str(ROOT / 'scripts/hidden.sh'), 'verify', 't5oracle'],
                   check=True, env=env, capture_output=True)
    original = (root / 't5oracle/pool.json').read_bytes()
    with_pool = tmp_path / 'direct'
    with_pool.mkdir()
    assert open_pool(with_pool).read_bytes() == original


def test_scoring_determinism_and_no_raw_oracle_leak(tmp_path, monkeypatch):
    require_node(monkeypatch)
    result = tmp_path / 't5-test-replay'
    result.mkdir()
    (result / 'run.json').write_text(json.dumps({'bot': 'baseline:cheapest'}))
    first = score_run(result)
    first_bytes = (result / 'score.json').read_bytes()
    second = score_run(result)
    assert second == first
    assert (result / 'score.json').read_bytes() == first_bytes
    assert first['matches'] == 144
    assert first['wins'] + first['losses'] == first['matches']
    assert first['winRate'] == round(first['wins'] / first['matches'], 4)
    ci = first['confidenceInterval']
    assert 0 <= ci['lower'] <= first['winRate'] <= ci['upper'] <= 1
    assert ci['method'] == 'seed-cluster-percentile-bootstrap'
    assert first['tier'] == 't5' and 'harnessMetrics' not in first
    assert set(first['oracle']) == {'archiveSha256', 'seedsSha256', 'poolSha256',
                                     'frozenSha256', 'refengineSha256', 'scorerSha256'}
    assert first['oracle']['archiveSha256'] == hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    with_pool = tmp_path / 'plaintext'
    with_pool.mkdir()
    pool = json.loads(open_pool(with_pool).read_text())
    assert all(str(seed) not in first_bytes.decode() for seed in pool['seeds'])
    assert pool['canary'].encode() not in first_bytes
    assert 'cases' not in first and 'seeds' not in first
    metadata = json.loads(META.read_text())
    assert first['oracle']['seedsSha256'] == metadata['seedsSha256']
    assert first['oracle']['poolSha256'] == metadata['poolSha256']


def test_score_cli_dispatches_t5(tmp_path, monkeypatch):
    require_node(monkeypatch)
    result = tmp_path / 't5-cli-dispatch'
    result.mkdir()
    (result / 'run.json').write_text('{"bot":"baseline:cheapest"}')
    process = subprocess.run([sys.executable, str(ROOT / 'scripts/score.py'), str(result)],
                             cwd=ROOT, text=True, capture_output=True)
    assert process.returncode == 0, process.stderr
    assert json.loads((result / 'score.json').read_text())['tier'] == 't5'


def test_impure_bot_gets_no_score(tmp_path, monkeypatch):
    require_node(monkeypatch)
    bot = tmp_path / 'impure.mjs'
    bot.write_text('let n = 0; export function decide() { return [{type:"sell",slot:n++}]; }')
    result = tmp_path / 't5-impure'
    result.mkdir()
    (result / 'run.json').write_text(json.dumps({'bot': str(bot)}))
    with pytest.raises(subprocess.CalledProcessError):
        score_run(result)
    assert not (result / 'score.json').exists()


def test_bot_process_cannot_read_sealed_pool_or_archive(tmp_path, monkeypatch):
    require_node(monkeypatch)
    bot = tmp_path / 'boundary.mjs'
    bot.write_text(
        'import fs from "node:fs";\n'
        f'const archive = {json.dumps(str(ARCHIVE))};\n'
        'export function decide() {\n'
        '  if (fs.existsSync(archive) || process.env.OAKEN_T5_ORACLE_POOL) '
        'throw new Error("oracle exposed");\n'
        '  return [{type:"end"}];\n'
        '}\n')
    result = tmp_path / 't5-boundary'
    result.mkdir()
    (result / 'run.json').write_text(json.dumps({'bot': str(bot)}))
    report = score_run(result)
    assert report['counters']['decideErrors'] == 0
    assert report['wins'] == 0


def test_non_array_return_is_malformed_under_process_boundary(tmp_path, monkeypatch):
    require_node(monkeypatch)
    bot = tmp_path / 'undefined.mjs'
    bot.write_text('export function decide() { return undefined; }\n')
    result = tmp_path / 't5-malformed'
    result.mkdir()
    (result / 'run.json').write_text(json.dumps({'bot': str(bot)}))
    report = score_run(result)
    assert report['counters']['malformed'] > 0
    assert report['counters']['decideErrors'] == 0
