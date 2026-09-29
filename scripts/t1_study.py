#!/usr/bin/env python3
"""Run the predeclared T1 pilot, matrix, and safe count-only summary."""
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys

from direct import server_reachable
from t1 import MODULES, ROOT

MODEL = 'gemma-ctx196k.gguf'
PILOT_MODULE = 'economy'
PILOT_CAP = 600
ORDERS = (('direct', 'pi', 'dsh'), ('pi', 'dsh', 'direct'),
          ('dsh', 'direct', 'pi'))
BASE_URL = 'http://172.17.0.1:8082/v1'
IMAGE = 'oaken-bench:t1-study'
ARCHIVE = Path(os.environ.get('OAKEN_ARCHIVE', Path.home() / '.cache/oaken-bench'))


def result_dir(label, mode):
    return ROOT / ('.t1-direct-results' if mode == 'direct' else 'results') / label


def one(module, mode, label, cap):
    if result_dir(label, mode).exists() or (ARCHIVE / label).exists():
        raise FileExistsError('trial label already exists: ' + label)
    if mode == 'direct':
        cmd = [sys.executable, str(ROOT / 'scripts/t1_direct.py'), module, MODEL,
               label, '--base-url', BASE_URL, '--max-tokens', '8192',
               '--timeout', str(cap)]
    else:
        cmd = [str(ROOT / 'run.sh'), mode, MODEL, label, str(cap)]
    env = dict(os.environ, OAKEN_IMAGE=IMAGE, OAKEN_SERVER_PORT='8082')
    run = subprocess.run(cmd, cwd=ROOT, env=env)
    if mode != 'direct' and (result_dir(label, mode) / 'workspace.tgz').is_file():
        subprocess.run([sys.executable, str(ROOT / 'scripts/score.py'),
                        str(result_dir(label, mode))], cwd=ROOT, env=env, check=True)
    score = result_dir(label, mode) / 'score.json'
    if not score.is_file() or not (ARCHIVE / label / 'workspace.tgz').is_file():
        raise RuntimeError('trial lacks a score or raw archive: ' + label)
    return {'label': label, 'mode': mode, 'module': module, 'runExit': run.returncode,
            'wallclockSeconds': json.loads(score.read_text())['wallclockSeconds']}


def pilot():
    if not server_reachable(BASE_URL):
        raise RuntimeError('local model endpoint unavailable; no pilot started')
    records = []
    for mode in ORDERS[0]:
        records.append(one(PILOT_MODULE, mode, f't1-{PILOT_MODULE}-{mode}-pilot', PILOT_CAP))
    # A pilot that exits early does not justify a shorter cap. Require that
    # every path produced a scored implementation and an archived workspace.
    for item in records:
        score = json.loads((result_dir(item['label'], item['mode']) / 'score.json').read_text())
        if score['outcome'] in ('no_engagement', 'crash', 'malformed_tool_calls'):
            raise RuntimeError('pilot needs investigation: ' + item['label'])
    cap = max(600, min(1200, math.ceil(1.5 * max(r['wallclockSeconds'] for r in records) / 60) * 60))
    path = ROOT / 't1' / 'pilot.json'
    path.write_text(json.dumps({'schemaVersion': 1, 'model': MODEL, 'image': IMAGE,
                                'pilotCapSeconds': PILOT_CAP, 'matrixCapSeconds': cap,
                                'records': records}, indent=2) + '\n')
    print('matrix cap:', cap, 'seconds')


def matrix():
    plan = json.loads((ROOT / 't1/pilot.json').read_text())
    cap = plan['matrixCapSeconds']
    if plan['model'] != MODEL or plan['image'] != IMAGE or not 600 <= cap <= 1200:
        raise ValueError('pilot plan does not match this protocol')
    if not server_reachable(BASE_URL):
        raise RuntimeError('local model endpoint unavailable; no matrix trial started')
    for module in MODULES:
        for trial, order in enumerate(ORDERS, 1):
            for mode in order:
                label = f't1-{module}-{mode}-{trial:02d}'
                if ((result_dir(label, mode) / 'score.json').is_file() and
                        (ARCHIVE / label / 'workspace.tgz').is_file()):
                    continue
                one(module, mode, label, cap)
    summarize()


def summarize():
    records = []
    for module in MODULES:
        for mode in ('direct', 'pi', 'dsh'):
            for trial in range(1, 4):
                label = f't1-{module}-{mode}-{trial:02d}'
                path = result_dir(label, mode) / 'score.json'
                if not path.is_file():
                    raise FileNotFoundError('incomplete matrix: ' + label)
                score = json.loads(path.read_text())
                usage = (score.get('harnessMetrics') or {}).get('usage') or {}
                if mode == 'direct':
                    raw = result_dir(label, mode) / 'raw-response.json'
                    if raw.is_file():
                        response = json.loads(raw.read_text())
                        choice = (response.get('choices') or [{}])[0]
                        message = choice.get('message') or {}
                        usage = response.get('usage') or {}
                        if not message.get('content') and choice.get('finish_reason') == 'length':
                            failure_mode = 'output_budget_no_code'
                        elif not message.get('content'):
                            failure_mode = 'empty_direct_response'
                        else:
                            failure_mode = None
                    else:
                        failure_mode = 'missing_direct_response'
                elif ((score.get('harnessMetrics') or {}).get('toolCalls') == 0 and
                      score['exitCode'] != 0):
                    failure_mode = 'harness_no_action'
                else:
                    failure_mode = None
                gpu = ('NVIDIA GeForce RTX 5070'
                       if module in MODULES[:6] or
                       (module == 'snapshot' and mode == 'direct' and trial == 1)
                       else 'NVIDIA GeForce RTX 3060')
                records.append({'module': module, 'mode': mode, 'trial': trial,
                                'gpu': gpu,
                                'targetPassed': score['targetHidden']['passed'],
                                'targetTotal': score['targetHidden']['total'],
                                'fullPassed': score['fullHidden']['passed'],
                                'fullTotal': score['fullHidden']['total'],
                                'typecheckClean': score['typecheckClean'],
                                'moduleSuccess': score['moduleSuccess'],
                                'outcome': score['outcome'],
                                'failureMode': failure_mode,
                                'inputTokens': usage.get('input', usage.get('inputTokens', usage.get('prompt_tokens'))),
                                'outputTokens': usage.get('output', usage.get('outputTokens', usage.get('completion_tokens'))),
                                'wallclockSeconds': score['wallclockSeconds'],
                                'archiveComplete': (ARCHIVE / label / 'workspace.tgz').is_file()})
    groups = []
    for module in MODULES:
        for mode in ('direct', 'pi', 'dsh'):
            subset = [r for r in records if r['module'] == module and r['mode'] == mode]
            values = [r['targetPassed'] for r in subset]
            groups.append({'module': module, 'mode': mode, 'n': 3,
                           'moduleSuccessCount': sum(r['moduleSuccess'] for r in subset),
                           'targetValues': values,
                           'targetMedian': statistics.median(values),
                           'targetMin': min(values), 'targetMax': max(values),
                           'targetPopulationStdDev': round(statistics.pstdev(values), 3),
                           'gpuSegments': sorted({r['gpu'] for r in subset}),
                           'wallclockMedianSeconds': statistics.median(r['wallclockSeconds'] for r in subset),
                           'wallclockTotalSeconds': sum(r['wallclockSeconds'] for r in subset)})
    (ROOT / 't1/study-results.json').write_text(json.dumps({
        'schemaVersion': 1, 'model': MODEL, 'image': IMAGE,
        'pilot': json.loads((ROOT / 't1/pilot.json').read_text()),
        'records': records, 'groups': groups}, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('pilot', 'matrix', 'summarize'))
    args = parser.parse_args()
    {'pilot': pilot, 'matrix': matrix, 'summarize': summarize}[args.phase]()


if __name__ == '__main__':
    main()
