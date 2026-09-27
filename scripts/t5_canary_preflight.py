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
    if len(choices) != 1 or choices[0].get('finish_reason') != 'stop':
        raise ValueError('canary response did not finish cleanly')
    message = choices[0].get('message') or {}
    answer = '\n'.join(part for part in (message.get('content'), message.get('reasoning_content'))
                       if isinstance(part, str))
    if not answer.strip():
        raise ValueError('canary response was empty')
    candidates = {value.lower() for value in GUID.findall(answer)}
    matched = any(hashlib.sha256(value.encode('ascii')).hexdigest() == expected
                  for value in candidates)
    return {'candidateCount': len(candidates), 'matched': matched}


def request(base_url, model, prompt):
    payload = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': prompt}],
                          'max_tokens': 1024, 'stream': False}).encode()
    req = urllib.request.Request(base_url.rstrip('/') + '/chat/completions', data=payload,
                                 headers={'Content-Type': 'application/json'})
    # A loopback or Docker-bridge endpoint must never be sent to a configured
    # HTTP proxy along with the canary question.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=180) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True, help='OpenAI-compatible URL ending in /v1')
    parser.add_argument('--model', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not args.base_url.endswith('/v1'):
        parser.error('--base-url must end in /v1')
    results = {}
    for name, (prompt, expected) in CANARIES.items():
        results[name] = assess(request(args.base_url, args.model, prompt), expected)
    report = {'schemaVersion': 1, 'checkedAt': datetime.now(timezone.utc).isoformat(),
              'model': args.model,
              'preflightSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'runShSha256': hashlib.sha256((Path(__file__).resolve().parents[1] / 'run.sh').read_bytes()).hexdigest(),
              'checks': results}
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    if any(value['matched'] for value in results.values()):
        raise SystemExit('T5 canary matched: model result is uninterpretable')
    print('T5 canary checks completed before model run; no digest matched')


if __name__ == '__main__':
    main()
