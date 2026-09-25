#!/usr/bin/env python3
"""Container-only T4 assembly/scoring. No oracle diagnostics leave /tmp.

The input mount holds only the assets needed for the requested operation.
Assembly sees the reference bundle, never either oracle. Scoring replaces
candidate tests/config/data with frozen inputs and runs each suite in a fresh
workspace so a broken first suite cannot alter the second one's workspace.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

INPUT = Path('/input')
OUTPUT = Path('/out')
SEED = INPUT / 'seed'


def extract(archive, target):
    """Reject links as well as traversal before extracting candidate archives."""
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts
            if member.name.startswith('/') or '..' in parts or not (member.isfile() or member.isdir()):
                raise ValueError('unsafe archive')
        tar.extractall(target, filter='data')


def decrypt(bundle, target):
    passvar = 'OAKEN_' + bundle.upper().replace('-', '_') + '_PASS'
    result = subprocess.run(
        ['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-iter', '200000',
         '-pass', 'env:' + passvar, '-in', str(INPUT / (bundle + '.tar.gz.enc'))],
        capture_output=True, check=False)
    if result.returncode:
        raise ValueError('bundle decryption failed')
    digest = (INPUT / (bundle + '.sha256')).read_text().strip()
    if hashlib.sha256(result.stdout).hexdigest() != digest:
        raise ValueError('bundle digest mismatch')
    extract(result.stdout, target)
    return target / bundle


def frozen_files():
    files = {p: SEED / p for p in ['SPEC.md', 'data/items.json', 'package.json',
                                  'tsconfig.json', 'vitest.config.ts']}
    files['SPEC-v1.1.md'] = INPUT / 't4' / 'SPEC-v1.1.md'
    for p in ['items-v1.1.json', 'encounters-v1.1.json']:
        files['data/' + p] = INPUT / 't4' / 'data' / p
    return files


def copy_frozen(work):
    for rel, src in frozen_files().items():
        dest = work / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)


def assemble():
    work = Path('/tmp/assembly/work')
    work.mkdir(parents=True)
    shutil.copytree(SEED, work, dirs_exist_ok=True)
    source = decrypt('refengine', Path('/tmp/reference'))
    modules = list(source.glob('*.ts'))
    if not modules or not (source / 'index.ts').is_file():
        raise ValueError('reference modules missing')
    for module in modules:
        shutil.copyfile(module, work / 'src' / module.name)
    copy_frozen(work)
    with tarfile.open(OUTPUT / 'workspace.tgz', 'w:gz') as tar:
        tar.add(work, arcname='work')


def counts(raw, total):
    # Assertion lists avoid vitest's nested-describe double counting in some
    # top-level fields. The sealed canonical total never follows collection.
    assertions = [a for f in raw.get('testResults', []) for a in f.get('assertionResults', [])]
    if len(assertions) > total:
        raise ValueError('suite exceeds sealed canonical total')
    passed = sum(a.get('status') == 'passed' for a in assertions)
    return {'passed': passed, 'failed': total - passed, 'total': total,
            'collected': len(assertions), 'uncollected': total - len(assertions),
            'rate': round(passed / total, 6) if total else None}


def run_suite(work, bundle, total):
    tests = work / 'tests'
    tests.mkdir()
    # Support modules are sealed with the tests, and share their canary.
    for path in bundle.glob('*.ts'):
        shutil.copyfile(path, tests / path.name)
    output = work / 'report.json'
    command = ['/opt/seed/node_modules/.bin/vitest', 'run', '--reporter=json',
               '--testTimeout=10000', '--maxWorkers=1', '--minWorkers=1',
               '--outputFile=' + str(output)]
    result = subprocess.run(['timeout', '-s', 'KILL', '420', *command], cwd=work,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hung = result.returncode in (124, 137, -9)
    try:
        raw = json.loads(output.read_text()) if not hung else {}
    except (OSError, ValueError):
        raw = {}
    summary = counts(raw, total)
    summary['hung'] = hung
    summary['reportAvailable'] = bool(raw)
    return summary


def candidate_workspace(source, path):
    path.mkdir(parents=True)
    # Candidate test files, config, dependencies and reports cannot choose
    # what gets scored. Only implementation modules cross this boundary.
    shutil.copytree(source / 'src', path / 'src')
    copy_frozen(path)
    (path / 'node_modules').symlink_to('/opt/seed/node_modules')
    return path


def score():
    original = Path('/tmp/candidate')
    extract((INPUT / 'workspace.tgz').read_bytes(), original)
    source = original / 'work'
    if not (source / 'src').is_dir():
        raise ValueError('workspace has no source directory')
    tampered = [rel for rel, expected in frozen_files().items()
                if not (source / rel).is_file() or
                (source / rel).read_bytes() != expected.read_bytes()]
    check = candidate_workspace(source, Path('/tmp/typecheck'))
    shutil.copytree(SEED / 'tests', check / 'tests')
    tc = subprocess.run(['timeout', '-s', 'KILL', '120',
                         '/opt/seed/node_modules/.bin/tsc', '--noEmit'], cwd=check,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    # Decrypt each oracle only after typechecking candidate source.
    totals = json.loads((INPUT / 'oracle.json').read_text())['totals']
    suites = {}
    for version, bundle in [('v1.0', 'hidden'), ('v1.1', 't4oracle')]:
        oracle = decrypt(bundle, Path('/tmp/sealed-' + bundle))
        work = candidate_workspace(source, Path('/tmp/score-' + bundle))
        suites[version] = run_suite(work, oracle, totals[version])
        shutil.rmtree(work)
        shutil.rmtree(oracle.parent)
    report = {'suites': suites, 'typecheckClean': tc, 'tamperedFrozenFiles': tampered}
    (OUTPUT / 'counts.json').write_text(json.dumps(report))


if __name__ == '__main__':
    try:
        {'assemble': assemble, 'score': score}[sys.argv[1]]()
    except Exception:
        # Exceptions can include decrypted paths or attacker-written code.
        # Emit no traceback into Docker logs or published score records.
        print('T4 container operation failed; no score produced', file=sys.stderr)
        sys.exit(1)
