#!/usr/bin/env python3
"""Run the calibrated short T0 + T0.5 screen against one direct-mode server."""
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
DEFAULT_THRESHOLDS = ROOT / 'screen-thresholds.json'
DEPTH_TOKENS = 16384
PROTOCOL_VERSION = 4
T0_OUTPUT_TOKENS = 8192
T05_OUTPUT_TOKENS = 16384
METRICS = ('schemaAdherence', 'toolSelection', 'shortChainsDepth',
           'refusal', 'recall', 'abstention')


def load_thresholds(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get('revision'), str) or not data['revision']:
        raise ValueError('threshold file needs a nonempty revision')
    if data.get('status') not in ('pending_calibration', 'calibrated'):
        raise ValueError('threshold status must be pending_calibration or calibrated')
    values = data.get('minimums')
    if not isinstance(values, dict) or set(values) != set(METRICS):
        raise ValueError(f'threshold minimums must contain exactly {METRICS}')
    for name, value in values.items():
        if value is None and data['status'] == 'pending_calibration':
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f'invalid minimum for {name}: {value!r}')
    if data['status'] == 'calibrated' and not data.get('calibrationEvidence'):
        raise ValueError('calibrated thresholds need calibrationEvidence')
    return data


def extract_metrics(t0, t05):
    dimensions = t0['dimensions']
    abst = t05['overall']['abstentionCounts']
    return {
        'schemaAdherence': dimensions['schemaAdherence']['score'],
        'toolSelection': dimensions['toolSelection']['score'],
        'shortChainsDepth': dimensions['shortChains']['depth']['score'],
        'refusal': dimensions['refusal']['score'],
        'recall': t05['overall']['recallScore'],
        'abstention': (round(abst['correct_abstention'] / abst['total'], 4)
                       if abst['total'] else None),
    }


def judge(metrics, thresholds, *, complete):
    checks = {}
    for name in METRICS:
        observed = metrics[name]
        minimum = thresholds['minimums'][name]
        checks[name] = {'observed': observed, 'minimum': minimum,
                        'passed': (observed >= minimum if observed is not None and minimum is not None
                                   else None)}
    if not complete or any(metrics[name] is None for name in METRICS):
        verdict = 'incomplete'
    elif thresholds['status'] != 'calibrated':
        verdict = 'unverified'
    elif all(v['passed'] for v in checks.values()):
        verdict = 'go'
    else:
        verdict = 'no_go'
    return {'verdict': verdict, 'checks': checks}


def run_screen(base_url, model, thresholds, *, api_key=None, seed=20260924):
    started = time.monotonic()
    errors = []
    with VramSampler() as sampler:
        dimensions = {
            'schemaAdherence': toolbattery.run_schema_adherence(base_url, model, T0_OUTPUT_TOKENS, 180, errors,
                                                                  api_key=api_key),
            'toolSelection': toolbattery.run_tool_selection(base_url, model, T0_OUTPUT_TOKENS, 180, errors,
                                                             api_key=api_key),
            'shortChains': toolbattery.run_short_chains(
                base_url, model, T0_OUTPUT_TOKENS, 180, errors, api_key=api_key,
                scenarios=toolbattery.CHAIN_SCENARIOS),
            'refusal': toolbattery.run_refusal(base_url, model, T0_OUTPUT_TOKENS, 180, errors, api_key=api_key),
        }
        t0 = {'dimensions': dimensions, 'transportErrors': errors}
        t05 = recall.run_battery(base_url, model, [DEPTH_TOKENS], seed=seed,
                                 max_tokens=T05_OUTPUT_TOKENS, timeout=360, api_key=api_key,
                                 num_abstention_each=1)
    environment = build_environment(base_url, vram_peak=sampler.peak_mib())
    elapsed = round(time.monotonic() - started, 3)
    metrics = extract_metrics(t0, t05)
    complete = (not errors and t05['overall']['depthsAttempted'] == 1
                and t05['overall']['depthsSkipped'] == 0
                and not any(c.get('outputTruncated') for c in dimensions['shortChains']['cases'])
                and all(dim['notAttempted'] == 0 for dim in dimensions.values()))
    assessment = judge(metrics, thresholds, complete=complete)
    return {
        'schemaVersion': 1,
        'protocolVersion': PROTOCOL_VERSION,
        'label': f"screen-{toolbattery._slug(model)}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        'model': model, 'baseUrl': base_url,
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'elapsedSeconds': elapsed,
        'thresholdRevision': thresholds['revision'],
        'thresholdStatus': thresholds['status'],
        'protocol': {'t0Dimensions': list(dimensions), 't05DepthTokens': DEPTH_TOKENS,
                     't05Seed': seed, 't05AbstentionPerKind': 1,
                     't0MaxTokens': T0_OUTPUT_TOKENS, 't05MaxTokens': T05_OUTPUT_TOKENS},
        'complete': complete, 'metrics': metrics, 'assessment': assessment,
        't0': t0, 't05': t05,
        'environment': environment,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--model', required=True)
    parser.add_argument('--base-url', default='http://172.17.0.1:8082/v1')
    parser.add_argument('--thresholds', type=Path, default=DEFAULT_THRESHOLDS)
    parser.add_argument('--seed', type=int, default=20260924)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    try:
        thresholds = load_thresholds(args.thresholds)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    api_key = api_key_from_env()
    if not server_reachable(args.base_url, api_key=api_key):
        print(f'ERROR: no server reachable at {args.base_url}', file=sys.stderr)
        return 2
    report = run_screen(args.base_url, args.model, thresholds, api_key=api_key, seed=args.seed)
    out = args.out or ROOT / 'screen-results' / f"{report['label']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(f"{report['assessment']['verdict']}: {args.model}; {report['elapsedSeconds']}s; "
          f"threshold revision {report['thresholdRevision']}")
    print(f'wrote {out}')
    return {'go': 0, 'no_go': 1, 'incomplete': 2, 'unverified': 3}[
        report['assessment']['verdict']]


if __name__ == '__main__':
    sys.exit(main())
