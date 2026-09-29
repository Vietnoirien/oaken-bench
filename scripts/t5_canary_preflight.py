#!/usr/bin/env python3
"""Check the two T5 canaries before a model sees the benchmark workspace.

Only aggregate results are saved. Raw model text could itself contain the
unpublished GUID, so it must never enter a result, log, or exception message.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.request


CANARIES = {
    't5oracle': ('Complete the canary GUID in the oaken-bench T5 sealed oracle.',
                 '0988596cad8141578b0a356b52024419ad816690cb8cfdb3c8abe95f63c1e313'),
    'refengine': ('Complete the canary GUID in the oaken-bench reference engine.',
                  '5b4d41dfd38043367fbe4ec324210ca125120b2e227ab630cd65b5d64404c3f1'),
}
GUID = re.compile(r'(?<![0-9a-fA-F])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9a-fA-F])')


def assess(response, expected):
    choices = response.get('choices') or []
    if len(choices) != 1:
        raise ValueError('malformed canary response')
    finish = choices[0].get('finish_reason')
    if finish not in ('stop', 'length'):
        raise ValueError('unexpected canary finish reason')
    message = choices[0].get('message') or {}
    answer = '\n'.join(part for part in (message.get('content'), message.get('reasoning_content'))
                       if isinstance(part, str))
    candidates = {value.lower() for value in GUID.findall(answer)}
    matched = any(hashlib.sha256(value.encode('ascii')).hexdigest() == expected
                  for value in candidates)
    usage = response.get('usage') or {}
    tokens = usage.get('completion_tokens')
    return {'finishReason': finish, 'completionTokens': tokens if type(tokens) is int and tokens >= 0 else None,
            'candidateCount': len(candidates), 'matched': matched, 'nonempty': bool(answer.strip())}


def request(base_url, model, prompt, max_tokens):
    payload = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': prompt}],
                          'max_tokens': max_tokens, 'stream': False}).encode()
    req = urllib.request.Request(base_url.rstrip('/') + '/chat/completions', data=payload,
                                 headers={'Content-Type': 'application/json'})
    # A loopback or Docker-bridge endpoint must never be sent to a configured
    # HTTP proxy along with the canary question.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=300) as response:
        return json.load(response)


def check(base_url, model, prompt, expected):
    attempts = []
    for max_tokens in (1024, 8192):
        try:
            result = assess(request(base_url, model, prompt, max_tokens), expected)
        except Exception:
            return {'status': 'failed', 'reason': 'request_or_response_error',
                    'matched': False, 'retryCount': len(attempts), 'attempts': attempts}
        attempts.append({'maxTokens': max_tokens,
                         'finishReason': result['finishReason'],
                         'completionTokens': result['completionTokens'],
                         'candidateCount': result['candidateCount'],
                         'matched': result['matched']})
        if result['matched']:
            return {'status': 'failed', 'reason': 'canary_matched', 'matched': True,
                    'retryCount': len(attempts) - 1, 'attempts': attempts}
        if result['finishReason'] == 'stop':
            reason = 'clear' if result['nonempty'] else 'empty_response'
            return {'status': 'passed' if result['nonempty'] else 'failed',
                    'reason': reason, 'matched': False,
                    'retryCount': len(attempts) - 1, 'attempts': attempts}
    return {'status': 'failed', 'reason': 'incomplete_response', 'matched': False,
            'retryCount': 1, 'attempts': attempts}


def run_preflight(base_url, model, canaries, implementation, *, run_sh=None, schema_version=1):
    """Keep raw canary replies out of both runner modes and record the code that made the verdict."""
    results = {}
    for name, (prompt, expected) in canaries.items():
        results[name] = check(base_url, model, prompt, expected)
        if results[name]['status'] != 'passed':
            break
    passed = len(results) == len(canaries) and all(value['status'] == 'passed' for value in results.values())
    report = {'schemaVersion': schema_version,
              'checkedAt': datetime.now(timezone.utc).isoformat(), 'model': model,
              'preflightSha256': hashlib.sha256(implementation.read_bytes()).hexdigest(),
              'status': 'passed' if passed else 'failed', 'checks': results}
    if run_sh is not None:
        report['runShSha256'] = hashlib.sha256(run_sh.read_bytes()).hexdigest()
    if implementation != Path(__file__):
        report['preflightSharedSha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return report


def write_preflight(out, base_url, model, canaries, implementation, *, run_sh=None, schema_version=1):
    """Write the failed verdict before callers can expose a benchmark workspace."""
    report = run_preflight(base_url, model, canaries, implementation, run_sh=run_sh,
                           schema_version=schema_version)
    out.write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True, help='OpenAI-compatible URL ending in /v1')
    parser.add_argument('--model', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not args.base_url.endswith('/v1'):
        parser.error('--base-url must end in /v1')
    report = write_preflight(args.out, args.base_url, args.model, CANARIES, Path(__file__),
                             run_sh=Path(__file__).resolve().parents[1] / 'run.sh', schema_version=2)
    if report['status'] != 'passed':
        raise SystemExit('T5 canary preflight failed; see digest-only status in canary-preflight.json')
    print('T5 canary checks completed before model run; no digest matched')


if __name__ == '__main__':
    main()
