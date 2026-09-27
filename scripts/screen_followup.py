#!/usr/bin/env python3
"""Measure fresh chain cases and recall depths without changing the calibrated screen."""
import argparse
import json
import os
import re
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
CHAIN_PROMPTS = {
    'order-ship-track': (
        'Ship the latest order for amira@example.com and check its delivery status.',
        'Find the latest order for leo@example.com, ship it, then report its tracking status.',
    ),
    'ticket-assign-contact': (
        'Open a ticket for "printer drops off Wi-Fi", assign an agent, and find the agent email.',
        'File a ticket for "nightly backup fails", assign an agent, then get their contact email.',
    ),
    'device-register-activate': (
        'Register device "lobby-12", activate it, and check its status.',
        'Register device "scanner-04", activate it, and report its status.',
    ),
    'expense-submit-release': (
        'Submit a $57.25 expense for "taxi fare", route it for approval, notify the approver, '
        'and release payment once approved.',
        'Submit a $19.80 expense for "parking", route it for approval, notify the approver, '
        'and release payment after approval.',
    ),
}


def chain_cases(seed):
    """Change prompt wording and result IDs while preserving each dependency graph."""
    if seed not in SEEDS:
        raise ValueError(f'undeclared follow-up seed: {seed}')
    variant = SEEDS.index(seed)
    scenarios = []
    for scenario in toolbattery.CHAIN_SCENARIOS:
        replacements = {}
        steps = []
        for step in scenario.steps:
            dependency = ({key: replacements.get(value, value) for key, value in step.dependency.items()}
                          if step.dependency else None)
            result = {}
            for key, value in step.result.items():
                match = re.fullmatch(r'([a-z]+)_[0-9a-f]{12}', value) if isinstance(value, str) else None
                if match:
                    replacements[value] = toolbattery._seeded_id(
                        f'followup/{seed}/{scenario.chain_id}/{key}', match.group(1))
                result[key] = replacements.get(value, value)
            steps.append(toolbattery.ChainStep(step.tool, step.decoys, dependency, result))
        scenarios.append(toolbattery.ChainScenario(
            scenario.chain_id, CHAIN_PROMPTS[scenario.chain_id][variant], steps))
    return scenarios


def run(base_url, model, api_key=None, seeds=SEEDS):
    started = time.monotonic()
    runs = []
    with VramSampler() as sampler:
        for seed in seeds:
            errors = []
            chains = toolbattery.run_short_chains(
                base_url, model, 8192, 180, errors, api_key=api_key,
                scenarios=chain_cases(seed))
            depths = recall.run_battery(
                base_url, model, DEPTHS, seed=seed, max_tokens=16384,
                timeout=360, api_key=api_key, num_abstention_each=2)
            runs.append({'seed': seed, 'chains': chains, 'chainTransportErrors': errors,
                         'recall': depths,
                         'complete': (not errors and chains['notAttempted'] == 0
                                      and not any(c['outputTruncated'] for c in chains['cases'])
                                      and depths['overall']['depthsAttempted'] == len(DEPTHS)
                                      and depths['overall']['depthsSkipped'] == 0
                                      and depths['overall']['depthsFailed'] == 0)})
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
