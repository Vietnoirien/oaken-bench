import hashlib
import sys

import pytest

from scripts import t5_canary_preflight as preflight

assess = preflight.assess


def response(text, finish='stop'):
    return {'choices': [{'finish_reason': finish, 'message': {'content': text}}]}


def test_matches_only_the_bare_guid_digest():
    guid = '12345678-1234-1234-1234-123456789abc'
    expected = hashlib.sha256(guid.encode()).hexdigest()
    assert assess(response(f'The GUID is {guid}.'), expected) == {
        'candidateCount': 1, 'matched': True}
    assert assess(response('I do not know.'), expected) == {
        'candidateCount': 0, 'matched': False}


def test_incomplete_or_empty_reply_cannot_clear_preflight():
    with pytest.raises(ValueError, match='finish cleanly'):
        assess(response('I do not know.', 'length'), 'unused')
    with pytest.raises(ValueError, match='empty'):
        assess(response(''), 'unused')


def test_failed_check_leaves_no_clearance_artifact(monkeypatch, tmp_path):
    output = tmp_path / 'preflight.json'
    monkeypatch.setattr(sys, 'argv', ['preflight', '--base-url', 'http://localhost/v1',
                                      '--model', 'fixture', '--out', str(output)])
    monkeypatch.setattr(preflight, 'request', lambda *_: response('still thinking', 'length'))
    with pytest.raises(ValueError, match='finish cleanly'):
        preflight.main()
    assert not output.exists()
