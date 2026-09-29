#!/usr/bin/env python3
"""Gate T1 workspace access without saving a reply that could reveal a sealed GUID."""
import argparse
from pathlib import Path

from t5_canary_preflight import write_preflight

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
    report = write_preflight(args.out, args.base_url, args.model, CANARIES, Path(__file__),
                             run_sh=ROOT / 'run.sh')
    if report['status'] != 'passed':
        raise SystemExit('T1 canary preflight failed; see status in canary-preflight.json')


if __name__ == '__main__':
    main()
