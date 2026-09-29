import hashlib
import json
import sys

import pytest

from scripts import t5_canary_preflight as preflight


def response(text, finish='stop', tokens=20):
    return {'choices': [{'finish_reason': finish, 'message': {'content': text}}],
            'usage': {'completion_tokens': tokens}}


def test_matches_bare_guid_even_in_truncated_reply():
    guid = '12345678-1234-1234-1234-123456789abc'
    expected = hashlib.sha256(guid.encode()).hexdigest()
    result = preflight.assess(response(f'The GUID is {guid}.', 'length'), expected)
    assert result['matched'] is True
    assert result['candidateCount'] == 1
    assert result['finishReason'] == 'length'


def test_retries_only_truncated_nonmatching_reply(monkeypatch):
    budgets = []

    def fake_request(*args):
        budgets.append(args[-1])
        return response('Still thinking' if len(budgets) == 1 else 'I do not know.',
                        'length' if len(budgets) == 1 else 'stop')

    monkeypatch.setattr(preflight, 'request', fake_request)
    result = preflight.check('http://localhost/v1', 'fixture', 'prompt', 'unused')
    assert result['status'] == 'passed'
    assert result['retryCount'] == 1
    assert budgets == [1024, 8192]
    assert [a['finishReason'] for a in result['attempts']] == ['length', 'stop']


def test_match_stops_before_retry(monkeypatch):
    guid = '12345678-1234-1234-1234-123456789abc'
    expected = hashlib.sha256(guid.encode()).hexdigest()
    budgets = []

    def fake_request(*args):
        budgets.append(args[-1])
        return response(guid, 'length')

    monkeypatch.setattr(preflight, 'request', fake_request)
    result = preflight.check('http://localhost/v1', 'fixture', 'prompt', expected)
    assert result['status'] == 'failed'
    assert result['reason'] == 'canary_matched'
    assert result['matched'] is True
    assert budgets == [1024]


@pytest.mark.parametrize('answers,reason', [
    ([response('thinking', 'length'), response('thinking', 'length')], 'incomplete_response'),
    ([response('')], 'empty_response'),
])
def test_inconclusive_reply_fails(monkeypatch, answers, reason):
    monkeypatch.setattr(preflight, 'request', lambda *_: answers.pop(0))
    assert preflight.check('http://localhost/v1', 'fixture', 'prompt', 'unused')['reason'] == reason


def test_failed_check_writes_metadata_only_and_stops(monkeypatch, tmp_path):
    output = tmp_path / 'preflight.json'
    monkeypatch.setattr(sys, 'argv', ['preflight', '--base-url', 'http://localhost/v1',
                                      '--model', 'fixture', '--out', str(output)])
    monkeypatch.setattr(preflight, 'request', lambda *_: response('secret raw reply', 'length'))
    with pytest.raises(SystemExit, match='preflight failed'):
        preflight.main()
    saved = output.read_text()
    assert 'secret raw reply' not in saved
    report = json.loads(saved)
    assert report['status'] == 'failed'
    assert report['checks']['t5oracle']['reason'] == 'incomplete_response'
    assert 'refengine' not in report['checks']
