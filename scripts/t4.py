#!/usr/bin/env python3
"""T4 workspace and score entry points, with separate v1.0/v1.1 denominators.

  python3 scripts/t4.py assemble /path/to/new/directory
  python3 scripts/t4.py baseline results/t4-reference-sanity
  python3 scripts/score.py results/t4-label

assemble writes workspace.tgz containing the reference source and both specs.
baseline also scores that unchanged workspace via the tier dispatcher. It is
an assembly/scoring smoke run, not an agent implementation trial.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = 'oaken-bench:1.0'
PASSES = {'hidden': 'oaken-bench-held-out', 'refengine': 'oaken-bench-refengine-v1',
          't4oracle': 'oaken-bench-t4-v1.1'}


def verify_frozen(root=ROOT):
    for manifest in ('FROZEN.sha256', 't4/FROZEN.sha256'):
        for line in (root / manifest).read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
                raise ValueError('frozen input drift: ' + name)


def stage_inputs(dest, mode, result_dir=None, root=ROOT):
    verify_frozen(root)
    # No whole-repository mount: an agent workspace must not expose an oracle.
    shutil.copytree(root / 'seed', dest / 'seed',
                    ignore=shutil.ignore_patterns('node_modules', '.git', '__pycache__'))
    (dest / 't4' / 'data').mkdir(parents=True)
    shutil.copyfile(root / 't4/SPEC-v1.1.md', dest / 't4/SPEC-v1.1.md')
    for name in ('items-v1.1.json', 'encounters-v1.1.json'):
        shutil.copyfile(root / 't4/data' / name, dest / 't4/data' / name)
    bundles = ('refengine',) if mode == 'assemble' else ('hidden', 't4oracle')
    for bundle in bundles:
        for suffix in ('.sha256', '.tar.gz.enc'):
            shutil.copyfile(root / (bundle + suffix), dest / (bundle + suffix))
    if mode == 'score':
        shutil.copyfile(result_dir / 'workspace.tgz', dest / 'workspace.tgz')
        shutil.copyfile(root / 't4/oracle.json', dest / 'oracle.json')


def container(mode, inputs, output):
    from score import sh
    name = 'oaken-t4-' + uuid.uuid4().hex
    image = os.environ.get('OAKEN_IMAGE', DEFAULT_IMAGE)
    env = dict(os.environ)
    bundles = ('refengine',) if mode == 'assemble' else ('hidden', 't4oracle')
    cmd = ['docker', 'run', '--rm', '--name', name, '--network', 'none',
           '--cpus', '2', '--memory', '2g', '--pids-limit', '256',
           '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
           '--read-only', '--tmpfs', '/tmp:rw,exec,size=1g',
           '--user', f'{os.getuid()}:{os.getgid()}',
           '-e', 'HOME=/tmp', '-e', 'TMPDIR=/tmp',
           '-v', f'{inputs}:/input:ro', '-v', f'{output}:/out:rw',
           '-v', f'{ROOT / "docker/t4_runtime.py"}:/runner.py:ro',
           '--entrypoint', 'python3']
    for bundle in bundles:
        var = 'OAKEN_' + bundle.upper() + '_PASS'
        env[var] = os.environ.get(var, PASSES[bundle])
        # sh inherits os.environ; pass defaults as explicit Docker env values.
        cmd.extend(['-e', var + '=' + env[var]])
    cmd.extend([image, '/runner.py', mode])
    try:
        rc, _, err = sh(cmd, timeout=1100 if mode == 'score' else 120)
        if rc:
            raise RuntimeError(f'T4 Docker operation failed (exit {rc}); no score produced: {err[:500]}')
    finally:
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30)


def assemble(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with tempfile.TemporaryDirectory(prefix='oaken-t4-input-') as td:
            inputs = Path(td)
            stage_inputs(inputs, 'assemble')
            container('assemble', inputs, destination)
        if not (destination / 'workspace.tgz').is_file():
            raise RuntimeError('assembly did not produce a workspace')
    except Exception:
        # Leave no apparently usable result from a failed assembly.
        shutil.rmtree(destination)
        raise
    return destination


def read_counts(raw, total):
    fields = ('passed', 'failed', 'total', 'collected', 'uncollected')
    if any(type(raw.get(k)) is not int or raw[k] < 0 for k in fields):
        raise ValueError('invalid suite counts')
    if (raw['total'] != total or raw['passed'] + raw['failed'] != total or
            raw['collected'] + raw['uncollected'] != total or raw['passed'] > raw['collected']):
        raise ValueError('inconsistent suite counts')
    return {**{k: raw[k] for k in fields}, 'rate': round(raw['passed'] / total, 6),
            'hung': raw.get('hung') is True, 'reportAvailable': raw.get('reportAvailable') is True}


def score_run(result_dir, detail=True):
    # detail is accepted for the shared dispatcher; T4 publishes counts only.
    result_dir = Path(result_dir).resolve()
    if not result_dir.name.startswith('t4-'):
        raise ValueError('T4 results require a t4- label')
    if not (result_dir / 'workspace.tgz').is_file():
        raise ValueError('missing workspace.tgz')
    manifest = json.loads((ROOT / 't4/oracle.json').read_text())
    with tempfile.TemporaryDirectory(prefix='oaken-t4-score-') as td:
        inputs, output = Path(td) / 'input', Path(td) / 'out'
        inputs.mkdir(); output.mkdir()
        stage_inputs(inputs, 'score', result_dir)
        container('score', inputs, output)
        raw = json.loads((output / 'counts.json').read_text())
    suites = {v: read_counts(raw['suites'][v], manifest['totals'][v]) for v in ('v1.0', 'v1.1')}
    allowed_frozen = {'SPEC.md', 'SPEC-v1.1.md', 'data/items.json', 'data/items-v1.1.json',
                      'data/encounters-v1.1.json', 'package.json', 'tsconfig.json', 'vitest.config.ts'}
    tampered = raw['tamperedFrozenFiles']
    if not isinstance(tampered, list) or any(p not in allowed_frozen for p in tampered):
        raise ValueError('invalid frozen-file report')
    report = {'schemaVersion': 1, 'tier': 't4', 'label': result_dir.name,
              'suites': suites, 'typecheckClean': raw['typecheckClean'] is True,
              'tamperedFrozenFiles': tampered,
              'oracle': {'bundle': 't4oracle', 'sha256': (ROOT / 't4oracle.sha256').read_text().strip(),
                         'v10Sha256': (ROOT / 'hidden.sha256').read_text().strip(),
                         'specCommit': manifest['specCommit']},
              'image': os.environ.get('OAKEN_IMAGE', DEFAULT_IMAGE)}
    report['outcome'] = ('invalid_frozen_inputs' if tampered else
                         'suite_incomplete' if any(s['hung'] or not s['reportAvailable'] or s['uncollected'] for s in suites.values())
                         else 'scored')
    target = result_dir / 'score.json'
    target.with_suffix('.json.tmp').write_text(json.dumps(report, indent=2) + '\n')
    target.with_suffix('.json.tmp').replace(target)
    print(f"{result_dir.name}: v1.0 {suites['v1.0']['passed']}/{suites['v1.0']['total']}; "
          f"v1.1 {suites['v1.1']['passed']}/{suites['v1.1']['total']}; {report['outcome']}")
    return report


def summary_row(data, label, tier):
    return {'label': label, 'tier': tier.id, 'tierLabel': tier.label,
            'suites': data['suites'], 'outcome': data.get('outcome')}


def print_summary(label, rows):
    print(f"=== {label} ===")
    for row in rows:
        a, b = row['suites']['v1.0'], row['suites']['v1.1']
        print(f"{row['label']}: v1.0 {a['passed']}/{a['total']}; "
              f"v1.1 {b['passed']}/{b['total']}; {row['outcome']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['assemble', 'baseline'])
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    if args.operation == 'baseline' and not args.destination.name.startswith('t4-'):
        parser.error('baseline requires a t4- label')
    destination = assemble(args.destination)
    if args.operation == 'baseline':
        (destination / 'run.meta').write_text('tier=t4 harness=none model=refengine-v1 runKind=reference-sanity\n')
        from tiers import resolve_tier
        resolve_tier(destination.name).score(destination, detail=False)


if __name__ == '__main__':
    main()
