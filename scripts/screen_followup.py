#!/usr/bin/env python3
"""Measure fresh chain cases and recall depths without changing the calibrated screen."""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from direct import api_key_from_env, server_reachable  # noqa: E402
from direct_env import VramSampler, build_environment  # noqa: E402
import recall  # noqa: E402
import toolbattery  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEEDS = (20260927, 20260928)
DEPTHS = (16384, 32768, 65536)
def chain_cases(seed):
    """Keep follow-up IDs and their dependent arguments under toolbattery's single owner."""
    return toolbattery.followup_chain_scenarios(seed)


def run(base_url, model, api_key=None, seeds=SEEDS):
    started = time.monotonic()
    runs = []
    failed = False
    with VramSampler() as sampler:
        for seed in seeds:
            errors = []
            phase = 'chains'
            chains = None
            depths = None
            try:
                chains = toolbattery.run_short_chains(
                    base_url, model, 8192, 180, errors, api_key=api_key,
                    scenarios=chain_cases(seed))
                phase = 'recall'
                depths = recall.run_battery(
                    base_url, model, DEPTHS, seed=seed, max_tokens=16384,
                    timeout=360, api_key=api_key, num_abstention_each=2)
            except Exception as exc:
                # An exception can contain a server reply. Persist only fixed metadata and counts.
                failed = True
                runs.append({'seed': seed, 'phase': phase, 'complete': False,
                             'failureType': type(exc).__name__,
                             'completedChains': len(chains['cases']) if chains else 0,
                             'completedChainLinks': sum(c['depthReached'] for c in chains['cases']) if chains else 0,
                             'completedRecallDepths': depths['overall']['depthsAttempted'] if depths else 0,
                             'chainTransportErrorCount': len(errors),
                             'elapsedSeconds': round(time.monotonic() - started, 3)})
                break
            runs.append({'seed': seed, 'chains': chains, 'chainTransportErrors': errors,
                         'recall': depths,
                         'complete': (not errors and chains['notAttempted'] == 0
                                      and not any(c['outputTruncated'] for c in chains['cases'])
                                      and depths['overall']['depthsAttempted'] == len(DEPTHS)
                                      and depths['overall']['depthsSkipped'] == 0
                                      and depths['overall']['depthsFailed'] == 0)})
    if failed:
        # Earlier completed seeds may include raw calls. A failed attempt is metadata-only throughout.
        runs = [r if 'phase' in r else {
            'seed': r['seed'], 'phase': 'complete', 'complete': r['complete'],
            'completedChains': len(r['chains']['cases']),
            'completedChainLinks': sum(c['depthReached'] for c in r['chains']['cases']),
            'completedRecallDepths': r['recall']['overall']['depthsAttempted'],
            'chainTransportErrorCount': len(r['chainTransportErrors']),
        } for r in runs]
    return {
        'schemaVersion': 1,
        'protocol': 'screen-followup-v1',
        'model': model, 'baseUrl': base_url,
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'elapsedSeconds': round(time.monotonic() - started, 3),
        'seeds': list(seeds), 'depthTokens': list(DEPTHS),
        'chainMaxTokens': 8192, 'recallMaxTokens': 16384,
        'chainRequestTimeoutSeconds': 180, 'recallRequestTimeoutSeconds': 360,
        'abstentionPerKind': 2,
        'runs': runs,
        **({'incomplete': True} if failed else {}),
        'environment': build_environment(base_url, vram_peak=sampler.peak_mib()),
        'caveat': 'Exploratory follow-up; calibrated 16k screen thresholds do not apply.',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--model', required=True)
    parser.add_argument('--base-url', default='http://172.17.0.1:8082/v1')
    parser.add_argument('--seed', type=int, choices=SEEDS,
                        help='run one predeclared seed; default runs both')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    api_key = api_key_from_env()
    if not server_reachable(args.base_url, api_key=api_key):
        print(f'ERROR: no server reachable at {args.base_url}', file=sys.stderr)
        return 2
    report = run(args.base_url, args.model, api_key=api_key,
                 seeds=(args.seed,) if args.seed else SEEDS)
    label = f"screen-followup-{toolbattery._slug(args.model)}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out = args.out or ROOT / 'screen-results' / f'{label}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(f"wrote {out}; complete seeds: {sum(r['complete'] for r in report['runs'])}/{len(report['runs'])}")
    return 0 if all(r['complete'] for r in report['runs']) else 2


if __name__ == '__main__':
    sys.exit(main())
