"""A failed T1 gate must leave only a verdict, never a canary reply or runnable workspace."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import t1_canary_preflight as harness  # noqa: E402
import t1_direct as direct  # noqa: E402
import t5_canary_preflight as shared  # noqa: E402


def reply(content, finish='stop'):
    return {'choices': [{'finish_reason': finish, 'message': {'content': content}}]}


@pytest.mark.parametrize('response,reason', [
    (reply(''), 'empty_response'),
    (reply('partial SECRET', 'length'), 'incomplete_response'),
    ({'choices': []}, 'request_or_response_error'),
    (RuntimeError('raw SECRET'), 'request_or_response_error'),
    (reply('GUID 00000000-0000-0000-0000-000000000000'), 'canary_matched'),
])
@pytest.mark.parametrize('mode', ['harness', 'direct'])
def test_t1_failures_write_only_metadata(monkeypatch, tmp_path, response, reason, mode):
    raw = 'raw SECRET'
    if reason == 'canary_matched':
        digest = hashlib.sha256(b'00000000-0000-0000-0000-000000000000').hexdigest()
        monkeypatch.setattr(harness, 'CANARIES', {'hidden': ('prompt', digest)})
    else:
        monkeypatch.setattr(harness, 'CANARIES', {'hidden': ('prompt', 'unused')})

    def fake_request(*_):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(shared, 'request', fake_request)
    if mode == 'harness':
        out = tmp_path / 'canary-preflight.json'
        monkeypatch.setattr(sys, 'argv', ['preflight', '--base-url', 'http://localhost/v1',
                                         '--model', 'fixture', '--out', str(out)])
        with pytest.raises(SystemExit, match='preflight failed'):
            harness.main()
    else:
        monkeypatch.setattr(direct, 'ROOT', tmp_path)
        monkeypatch.setattr(direct, 'capture', lambda *_args, **_kwargs: {})
        with pytest.raises(RuntimeError, match='canary check failed'):
            direct.run('combat', 'fixture', 'http://localhost/v1', 't1-combat-test', 10, 10)
        out = tmp_path / '.t1-direct-results/t1-combat-test/canary-preflight.json'

    report = json.loads(out.read_text())
    assert report['status'] == 'failed'
    assert report['checks']['hidden']['reason'] == reason
    assert report['preflightSha256'] == hashlib.sha256(Path(harness.__file__).read_bytes()).hexdigest()
    assert report['preflightSharedSha256'] == hashlib.sha256(
        Path(shared.__file__).read_bytes()).hexdigest()
    assert ('runShSha256' in report) == (mode == 'harness')
    assert raw not in out.read_text()
    assert '00000000-0000-0000-0000-000000000000' not in out.read_text()
