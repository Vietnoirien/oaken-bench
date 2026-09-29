#!/usr/bin/env python3
"""Assemble and score T1 missing-module workspaces without opening the oracle.

The eight held-out test files share names with the eight writable modules. That
file assignment is the scoring seam. Tests can call through other modules, so
the target-file score is reported beside the full 132-test regression count.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('combat', 'economy', 'items', 'rng', 'run', 'shop', 'snapshot', 'tower')
COUNTS = {'combat': 35, 'economy': 12, 'items': 15, 'rng': 5,
          'run': 12, 'shop': 15, 'snapshot': 24, 'tower': 14}
PROMPT = """Implement only src/{module}.ts in the TypeScript engine in /work.
Read SPEC.md, the target stub, and the supplied modules as needed. The other
modules are complete and must stay unchanged. Keep the target module's public
interface. You may run the visible tests and typecheck. Finish when you have
implemented the target; do not edit tests, data, configuration, or other source.
"""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_frozen():
    subprocess.run(['sha256sum', '-c', 'FROZEN.sha256'], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)


def reference_dir():
    path = ROOT / 'refengine'
    if not path.is_dir():
        subprocess.run([str(ROOT / 'scripts/hidden.sh'), 'unlock', 'refengine'],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    subprocess.run([str(ROOT / 'scripts/hidden.sh'), 'verify', 'refengine'],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    return path


def assemble(destination, module, restore=False):
    if module not in MODULES:
        raise ValueError('unknown T1 module: ' + module)
    verify_frozen()
    source = reference_dir()
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    try:
        work = destination / 'work'
        shutil.copytree(ROOT / 'seed', work,
                        ignore=shutil.ignore_patterns('node_modules', '.git', '__pycache__'))
        for name in MODULES + ('index', 'types'):
            if name != module or restore:
                shutil.copyfile(source / (name + '.ts'), work / 'src' / (name + '.ts'))
        manifest = {'schemaVersion': 1, 'tier': 't1', 'module': module,
                    'runKind': 'reference-control' if restore else 'implementation',
                    'referenceBundleSha256': (ROOT / 'refengine.sha256').read_text().strip(),
                    'oracleBundleSha256': (ROOT / 'hidden.sha256').read_text().strip(),
                    'fixedSourceSha256': {
                        name + '.ts': digest(source / (name + '.ts'))
                        for name in MODULES + ('index', 'types') if name != module}}
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (destination / 'PROMPT.txt').write_text(PROMPT.format(module=module))
        with tarfile.open(destination / 'workspace.tgz', 'w:gz') as archive:
            archive.add(work, arcname='work')
        shutil.rmtree(work)
    except Exception:
        shutil.rmtree(destination)
        raise
    return destination


def fixed_source_changes(result_dir, manifest):
    changed = []
    with tarfile.open(result_dir / 'workspace.tgz', 'r:gz') as archive:
        members = {m.name: m for m in archive if m.isfile()}
        for name, expected in manifest['fixedSourceSha256'].items():
            member = members.get('work/src/' + name)
            if member is None or member.size > 1024 * 1024:
                changed.append(name)
                continue
            with archive.extractfile(member) as stream:
                if hashlib.sha256(stream.read()).hexdigest() != expected:
                    changed.append(name)
        for path in (ROOT / 'seed/tests').glob('*.ts'):
            key = 'work/tests/' + path.name
            member = members.get(key)
            if member is None or member.size > 1024 * 1024:
                changed.append('tests/' + path.name)
                continue
            with archive.extractfile(member) as stream:
                if hashlib.sha256(stream.read()).hexdigest() != digest(path):
                    changed.append('tests/' + path.name)
        expected_source = {manifest['module'] + '.ts', *manifest['fixedSourceSha256']}
        for name in members:
            if name.startswith('work/src/') and name.endswith('.ts') and name[9:] not in expected_source:
                changed.append(name[5:])
    return changed


def score_run(result_dir, detail=True):
    from score import score_run as score_v10
    result_dir = Path(result_dir).resolve()
    manifest = json.loads((result_dir / 't1-manifest.json').read_text())
    module = manifest['module']
    if module not in MODULES or manifest['schemaVersion'] != 1:
        raise ValueError('invalid T1 manifest')
    report = score_v10(str(result_dir), detail=True)
    target = {'passed': 0, 'failed': 0, 'total': COUNTS[module],
              'collected': 0, 'uncollected': COUNTS[module], 'rate': 0.0}
    detail_path = result_dir / 'hidden-detail.json'
    if detail_path.is_file():
        private = json.loads(detail_path.read_text())
        matching = [f for f in private['files'] if f['file'] == module + '.test.ts']
        if len(matching) == 1:
            item = matching[0]
            target.update(passed=item['passed'], failed=item['failed'],
                          collected=item['total'],
                          uncollected=COUNTS[module] - item['total'],
                          rate=round(item['passed'] / COUNTS[module], 4))
    changed = fixed_source_changes(result_dir, manifest) if report['restored'] else []
    report.update(schemaVersion=1, tier='t1', module=module,
                  runKind=manifest['runKind'], targetHidden=target,
                  fullHidden=report.pop('hidden'), fixedSourceChanges=changed,
                  referenceBundleSha256=manifest['referenceBundleSha256'],
                  oracleBundleSha256=manifest['oracleBundleSha256'])
    report['scorerSha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if changed or report['tamperedFrozenFiles']:
        report['outcome'] = 'invalid_package_mutation'
    elif target['uncollected'] or report['fullHidden']['uncollected']:
        report['outcome'] = 'suite_incomplete'
    elif manifest['runKind'] == 'reference-control' and (target['passed'] == COUNTS[module] and
                                                      report['fullHidden']['passed'] == 132 and
                                                      report['typecheckClean']):
        report['outcome'] = 'reference_control_passed'
    elif (report['harness'] == 'direct' and report['exitCode'] == 0 and
          report['outcome'] == 'no_engagement'):
        # T2's <20s guard describes failed agent startup. A single direct
        # completion can legitimately finish faster than that.
        report['outcome'] = 'scored'
    elif report['outcome'] in ('complete', 'declared_done_tests_red',
                               'incomplete_hidden_below_bar'):
        # T2's 80% full-suite bar does not define T1 success. A completed
        # attempt can pass any number of target tests.
        report['outcome'] = 'scored'
    report['moduleSuccess'] = (report['outcome'] in ('scored', 'reference_control_passed') and
                               target['passed'] == target['total'] and
                               report['fullHidden']['passed'] == 132 and
                               report['typecheckClean'])
    (result_dir / 'score.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"T1 {module}: {target['passed']}/{target['total']} target, "
          f"{report['fullHidden']['passed']}/132 full; {report['outcome']}")
    return report


def summary_row(data, label, tier):
    return {'label': label, 'tier': tier.id, 'tierLabel': tier.label,
            'module': data['module'], 'mode': data.get('harness'),
            'target': data['targetHidden'], 'full': data['fullHidden'],
            'typecheckClean': data['typecheckClean'],
            'moduleSuccess': data['moduleSuccess'],
            'wallclockSeconds': data['wallclockSeconds'],
            'outcome': data['outcome']}


def print_summary(label, rows):
    print(f'=== {label} ===')
    for row in rows:
        target = row['target']
        print(f"{row['label']}: {target['passed']}/{target['total']} target, "
              f"{row['full']['passed']}/132 full, "
              f"typecheck={'clean' if row['typecheckClean'] else 'dirty'}, "
              f"{row['wallclockSeconds']}s, {row['outcome']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('assemble')
    p.add_argument('module', choices=MODULES)
    p.add_argument('destination', type=Path)
    p.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if args.command == 'assemble':
        assemble(args.destination, args.module, args.restore)


if __name__ == '__main__':
    main()
