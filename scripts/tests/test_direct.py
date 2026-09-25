"""Tests for scripts/direct.py (issue #27).

No network: every test replaces `urllib.request.urlopen` with a fake so
these run offline and in CI, same as the rest of the pure-logic layer in
test_toolbattery.py. The one thing this module adds over the code it was
lifted from is the bearer key, so the tests lean on that: it goes out on
the wire when given, it is absent when not, and it never comes back out
through a `ServerError`.
"""
import io
import json
import os
import sys
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from direct import (  # noqa: E402
    DEFAULT_API_KEY_ENV, ServerError, api_key_from_env, chat, server_reachable,
)

SECRET = 'sk-do-not-leak-this-token-12345'


class _FakeResponse:
    def __init__(self, status=200, body=b'{}'):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---------------------------------------------------------------------------
# api_key_from_env: env var only, empty treated as unset
# ---------------------------------------------------------------------------

def test_api_key_from_env_reads_default_var(monkeypatch):
    monkeypatch.setenv(DEFAULT_API_KEY_ENV, SECRET)
    assert api_key_from_env() == SECRET


def test_api_key_from_env_missing_is_none(monkeypatch):
    monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
    assert api_key_from_env() is None


def test_api_key_from_env_empty_string_is_none(monkeypatch):
    # An accidentally-exported `FOO=` must not send `Authorization: Bearer `
    # with nothing after it.
    monkeypatch.setenv(DEFAULT_API_KEY_ENV, '')
    assert api_key_from_env() is None


def test_api_key_from_env_custom_var(monkeypatch):
    monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
    monkeypatch.setenv('MY_PROVIDER_KEY', SECRET)
    assert api_key_from_env('MY_PROVIDER_KEY') == SECRET


# ---------------------------------------------------------------------------
# chat(): auth header wiring
# ---------------------------------------------------------------------------

def test_chat_with_api_key_sends_bearer_header(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured['headers'] = dict(req.headers)
        return _FakeResponse(body=json.dumps({'ok': True}).encode())

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    chat('http://example.invalid/v1', 'my-model', [{'role': 'user', 'content': 'hi'}],
         api_key=SECRET)
    # urllib.request.Request title-cases header keys it's given.
    assert captured['headers']['Authorization'] == f'Bearer {SECRET}'


def test_chat_without_api_key_sends_no_authorization_header(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured['headers'] = dict(req.headers)
        return _FakeResponse(body=json.dumps({'ok': True}).encode())

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    chat('http://172.17.0.1:8080/v1', 'my-model', [{'role': 'user', 'content': 'hi'}])
    assert not any(k.lower() == 'authorization' for k in captured['headers'])


def test_server_reachable_sends_bearer_header(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured['headers'] = dict(req.headers)
        return _FakeResponse(status=200)

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    assert server_reachable('http://example.invalid/v1', api_key=SECRET) is True
    assert captured['headers']['Authorization'] == f'Bearer {SECRET}'


# ---------------------------------------------------------------------------
# Key redaction: the acceptance-criterion test
# ---------------------------------------------------------------------------

def test_http_error_message_does_not_contain_api_key(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            'http://example.invalid/v1/chat/completions', 401,
            'Unauthorized', hdrs=None, fp=io.BytesIO(b'{"error": "bad request"}'))

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    with pytest.raises(ServerError) as exc_info:
        chat('http://example.invalid/v1', 'my-model', [{'role': 'user', 'content': 'hi'}],
             api_key=SECRET)
    assert SECRET not in str(exc_info.value)


def test_url_error_message_does_not_contain_api_key(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError('connection refused')

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    with pytest.raises(ServerError) as exc_info:
        chat('http://example.invalid/v1', 'my-model', [{'role': 'user', 'content': 'hi'}],
             api_key=SECRET)
    assert SECRET not in str(exc_info.value)


def test_non_json_response_message_does_not_contain_api_key(monkeypatch):
    # Body has nothing to do with the key -- this isolates whether *our*
    # error formatting adds it (it must not; it only echoes the body).
    monkeypatch.setattr(urllib.request, 'urlopen',
                         lambda req, timeout=None: _FakeResponse(body=b'not json'))
    with pytest.raises(ServerError) as exc_info:
        chat('http://example.invalid/v1', 'my-model', [{'role': 'user', 'content': 'hi'}],
             api_key=SECRET)
    assert SECRET not in str(exc_info.value)


def test_server_error_str_never_contains_request_headers(monkeypatch):
    # Belt and braces: even if a future edit accidentally formatted the
    # outgoing urllib.request.Request into an error message, the key must
    # not be extractable from ServerError's string form for a plain
    # transport failure (no HTTP response at all).
    def fake_urlopen(req, timeout=None):
        assert req.headers.get('Authorization') == f'Bearer {SECRET}'
        raise urllib.error.URLError('name or service not known')

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    with pytest.raises(ServerError) as exc_info:
        chat('http://nonexistent.invalid/v1', 'my-model',
             [{'role': 'user', 'content': 'hi'}], api_key=SECRET)
    assert SECRET not in str(exc_info.value)
    assert SECRET not in repr(exc_info.value)


# ---------------------------------------------------------------------------
# server_reachable(): unreachable / non-200 -> False, never raises
# ---------------------------------------------------------------------------

def test_server_reachable_false_on_connection_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError('connection refused')

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    assert server_reachable('http://example.invalid/v1') is False


def test_server_reachable_false_on_non_200(monkeypatch):
    monkeypatch.setattr(urllib.request, 'urlopen', lambda req, timeout=None: _FakeResponse(status=503))
    assert server_reachable('http://example.invalid/v1') is False
