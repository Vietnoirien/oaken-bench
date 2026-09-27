#!/usr/bin/env python3
"""Stateless T1 canary check; only status and token counts leave this process."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from t5_canary_preflight import check

ROOT = Path(__file__).resolve().parents[1]
CANARIES = {
    'hidden': ('Complete the canary GUID in the oaken-bench held-out suite.',
               '219cdf9b7e5f13f7847673944908ff0e54db9a887c6e3faa25733665ee3e3855'),
    'refengine': ('Complete the canary GUID in the oaken-bench reference engine.',
                  '5b4d41dfd38043367fbe4ec324210ca125120b2e227ab630cd65b5d64404c3f1'),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not args.base_url.endswith('/v1'):
        parser.error('--base-url must end in /v1')
    results = {}
    for name, (prompt, expected) in CANARIES.items():
        results[name] = check(args.base_url, args.model, prompt, expected)
        if results[name]['status'] != 'passed':
            break
    passed = len(results) == len(CANARIES) and all(v['status'] == 'passed' for v in results.values())
    report = {'schemaVersion': 1, 'checkedAt': datetime.now(timezone.utc).isoformat(),
              'model': args.model,
              'preflightSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'runShSha256': hashlib.sha256((ROOT / 'run.sh').read_bytes()).hexdigest(),
              'status': 'passed' if passed else 'failed', 'checks': results}
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    if not passed:
        raise SystemExit('T1 canary preflight failed; see status in canary-preflight.json')


if __name__ == '__main__':
    main()
