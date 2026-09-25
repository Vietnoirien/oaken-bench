#!/usr/bin/env python3
"""Shared OpenAI-compatible chat client for direct-mode batteries (issue #27).

Lifted out of `toolbattery.py` (issue #8) so later direct-mode batteries
(T0.5 recall/abstention, issues #31/#32) do not grow a second copy that
drifts from this one. This is a prefactor: the interface is deliberately
narrow -- `chat()`, `server_reachable()`, `ServerError`, plus the env-var
bearer key -- so a caller pulls in exactly the HTTP seam and nothing about
how any one battery scores or reports.

Talks to any OpenAI-compatible `/v1/chat/completions` endpoint: an
unauthenticated local llama-server (the default -- pass no `api_key` and
no `Authorization` header is sent, exactly as toolbattery.py behaved
before this module existed) or a hosted HTTP API that wants
`Authorization: Bearer <key>` (#26's maintainer comment: direct mode
should also run against HTTP APIs).

The key comes from an environment variable ONLY -- never argv, which
lands in `ps` output and shell history, and it is never put into a
report, a log line or an exception string. `chat()`'s exception text
below echoes only the response BODY the server sent back (what a
misconfigured endpoint said), never the request we made, so the
`Authorization` header we set can't leak through it even if a caller
logs the exception verbatim. See scripts/tests/test_direct.py for the
test that pins this down against a real `HTTPError`, not just by
inspection.
"""
import json
import os
import urllib.error
import urllib.request

# Read by api_key_from_env() when a caller doesn't name its own var. A
# battery-specific override (e.g. a per-provider key) can still pass its
# own env var name through; this is just the default so most callers don't
# have to invent one.
DEFAULT_API_KEY_ENV = 'OAKEN_DIRECT_API_KEY'


class ServerError(RuntimeError):
    pass


def api_key_from_env(env_var=DEFAULT_API_KEY_ENV):
    """Read the bearer key from `env_var`. None if unset OR empty --
    an accidentally-exported `FOO=` should behave like no key was given,
    not send a literal `Bearer ` with nothing after it."""
    return os.environ.get(env_var) or None


def _auth_headers(api_key):
    return {'Authorization': f'Bearer {api_key}'} if api_key else {}


def server_reachable(base_url, timeout=5, api_key=None):
    try:
        req = urllib.request.Request(base_url.rstrip('/') + '/models', method='GET',
                                      headers=_auth_headers(api_key))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def chat(base_url, model, messages, tools=None, max_tokens=512, timeout=60, api_key=None):
    """One /v1/chat/completions call. Returns the parsed JSON response.
    Raises ServerError on any transport/HTTP failure -- callers decide
    whether that fails one case or the whole run.

    `api_key`, when given, goes out as `Authorization: Bearer <api_key>`
    and nowhere else -- see the module docstring on why the exception
    branches below are safe to log as-is."""
    payload = {'model': model, 'messages': messages, 'max_tokens': max_tokens}
    if tools:
        payload['tools'] = tools
    data = json.dumps(payload).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    headers.update(_auth_headers(api_key))
    req = urllib.request.Request(
        base_url.rstrip('/') + '/chat/completions', data=data,
        headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise ServerError(f'HTTP {e.code} from {base_url}: {e.read()[:500]!r}') from e
    except urllib.error.URLError as e:
        raise ServerError(f'request to {base_url} failed: {e}') from e
    try:
        return json.loads(body)
    except (ValueError, TypeError) as e:
        raise ServerError(f'non-JSON response from {base_url}: {body[:500]!r}') from e
