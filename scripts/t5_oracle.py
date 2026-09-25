#!/usr/bin/env python3
"""Capture the sealed T5 pool or score a bot through the frozen T5 launcher."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / 't5oracle.tar.gz.enc'
DIGEST = ROOT / 't5oracle.sha256'
POOL = ROOT / 't5oracle' / 'pool.json'
META = ROOT / 't5' / 'oracle' / 'DIGESTS.json'
LAUNCHER = ROOT / 't5' / 'bin' / 't5-sim'
DRIVER = ROOT / 't5' / 'oracle' / 'driver.test.ts'
LABEL = re.compile(r'^t5-[a-zA-Z0-9][a-zA-Z0-9_.-]*$')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def check_frozen():
    subprocess.run(['sha256sum', '-c', 't5/FROZEN.sha256'], cwd=ROOT,
                   check=True, stdout=subprocess.DEVNULL)


def run_driver(mode, output, **env):
    variables = dict(os.environ, OAKEN_T5_ORACLE_MODE=mode,
                     OAKEN_T5_ORACLE_OUTPUT=str(output), OAKEN_T5_SIM_LOG='off')
    variables.update({f'OAKEN_T5_ORACLE_{k.upper()}': str(v) for k, v in env.items()})
    subprocess.run([str(LAUNCHER), 'test', '--test-name-pattern=^t5 oracle driver$',
                    str(DRIVER)], cwd=ROOT, env=variables, check=True,
                   stdout=subprocess.DEVNULL)


def capture():
    check_frozen()
    if ARCHIVE.exists() or DIGEST.exists() or POOL.exists() or META.exists():
        raise ValueError('oracle already exists; refusing to replace fixed seeds or pool')
    POOL.parent.mkdir(mode=0o700)
    try:
        run_driver('capture', POOL, meta=META)
        subprocess.run([str(ROOT / 'scripts/hidden.sh'), 'lock', 't5oracle'],
                       cwd=ROOT, check=True)
        subprocess.run([str(ROOT / 'scripts/hidden.sh'), 'verify', 't5oracle'],
                       cwd=ROOT, check=True)
        metadata = json.loads(META.read_text())
        metadata['archiveSha256'] = sha(ARCHIVE.read_bytes())
        metadata['plaintextArchiveSha256'] = DIGEST.read_text().strip()
        META.write_text(json.dumps(metadata, indent=2) + '\n')
    finally:
        shutil.rmtree(POOL.parent, ignore_errors=True)


def open_pool(directory):
    passphrase = os.environ.get('OAKEN_T5ORACLE_PASS', 'oaken-bench-t5oracle-v1')
    plain = directory / 'pool.tar.gz'
    variables = dict(os.environ, _OAKEN_PASS=passphrase)
    subprocess.run(['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2',
                    '-iter', '200000', '-pass', 'env:_OAKEN_PASS',
                    '-in', str(ARCHIVE), '-out', str(plain)],
                   check=True, env=variables, stdout=subprocess.DEVNULL)
    if sha(plain.read_bytes()) != DIGEST.read_text().strip():
        raise ValueError('sealed oracle digest mismatch')
    with tarfile.open(plain, 'r:gz') as archive:
        names = archive.getnames()
        if names != ['t5oracle', 't5oracle/pool.json']:
            raise ValueError('unexpected sealed oracle files')
        member = archive.extractfile('t5oracle/pool.json')
        if member is None:
            raise ValueError('sealed pool missing')
        pool = directory / 'pool.json'
        pool.write_bytes(member.read())
    plain.unlink()
    return pool


def score_run(result_dir, detail=True):
    """Tier-registry scorer. Only aggregates leave the ignored result directory."""
    del detail
    check_frozen()
    result_dir = Path(result_dir)
    if not LABEL.fullmatch(result_dir.name):
        raise ValueError('T5 result labels must start with t5-')
    config = json.loads((result_dir / 'run.json').read_text())
    bot = config['bot']
    if not isinstance(bot, str) or not bot:
        raise ValueError('run.json needs a bot')
    bwrap = shutil.which('bwrap')
    prlimit = shutil.which('prlimit')
    if not bwrap or not prlimit:
        raise ValueError('Bubblewrap and prlimit are required for held-out T5 scoring')
    with tempfile.TemporaryDirectory(prefix='oaken-t5-oracle-') as temporary:
        temp = Path(temporary)
        pool = open_pool(temp)
        output = temp / 'score.json'
        run_driver('score', output, pool=pool, bot=bot,
                   archive_sha=sha(ARCHIVE.read_bytes()), bwrap=bwrap,
                   prlimit=prlimit)
        report = json.loads(output.read_text())
        if report.get('tier') != 't5' or report.get('matches') != 144:
            raise ValueError('invalid T5 score')
        destination = result_dir / 'score.json'
        staged = result_dir / 'score.json.tmp'
        staged.write_bytes(output.read_bytes())
        staged.replace(destination)
    print(f"{result_dir.name}: {report['wins']}/{report['matches']} "
          f"win rate {report['winRate']} "
          f"95% CI [{report['confidenceInterval']['lower']}, "
          f"{report['confidenceInterval']['upper']}]")
    return report


def summary_row(data, label, tier):
    if data.get('tier') != 't5' or data.get('protocol') != 't5-sealed-snapshot/1':
        raise ValueError(f'{label}: not a T5 sealed-snapshot score')
    return {
        'label': label, 'tier': tier.id, 'tierLabel': tier.label,
        'bot': data['bot']['spec'], 'matches': data['matches'],
        'winRate': data['winRate'], 'ci': data['confidenceInterval'],
        'oracle': data['oracle']['archiveSha256'],
    }


def print_summary(label, rows):
    print(label)
    for row in rows:
        ci = row['ci']
        print(f"{row['label']}: {row['bot']}  {row['winRate']:.4f} "
              f"95% CI [{ci['lower']:.4f}, {ci['upper']:.4f}] "
              f"n={row['matches']} oracle={row['oracle'][:12]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('capture')
    run = sub.add_parser('run')
    run.add_argument('--bot', required=True)
    run.add_argument('--label', required=True)
    args = parser.parse_args()
    if args.command == 'capture':
        capture()
        return
    label = f't5-{args.label}'
    if not LABEL.fullmatch(label):
        parser.error('invalid label')
    result_dir = ROOT / 'results' / label
    result_dir.mkdir(parents=True, exist_ok=False)
    (result_dir / 'run.json').write_text(json.dumps({'bot': args.bot}) + '\n')
    score_run(result_dir)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f't5-oracle: {error}', file=sys.stderr)
        sys.exit(2)
