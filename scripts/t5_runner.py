#!/usr/bin/env python3
"""Prepare model-facing T5 workspaces and score their archived bot deliverable.

The model receives only public development assets and the callable reference
engine. Oracle files are never included in the container's input mount.
"""
import argparse
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
ENTRY = 'bot.mjs'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def stage_public(destination):
    from t5_oracle import check_frozen
    check_frozen()
    subprocess.run(['sha256sum', '-c', 'FROZEN.sha256'], cwd=ROOT,
                   check=True, stdout=subprocess.DEVNULL)
    destination.mkdir(parents=True, exist_ok=False)
    # Enumerate permitted trees. Copying t5/ wholesale would mount its oracle.
    for name in ('seed', 't5/sim', 't5/bots', 't5/bin', 't5/tests'):
        shutil.copytree(ROOT / name, destination / name,
                        ignore=shutil.ignore_patterns('node_modules', '.git', '__pycache__'))
    for name in ('refengine.tar.gz.enc', 'refengine.sha256', 't5/SPEC.md',
                 't5/package.json', 't5/tsconfig.json', 't5/seeds.visible.json',
                 't5/AUTHORSHIP.md', 't5/FROZEN.sha256'):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (destination / 'bot').mkdir()
    (destination / 'bot/package.json').write_text('{"type":"module"}\n')


def assemble(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    try:
        stage_public(destination / 'work')
        with tarfile.open(destination / 'workspace.tgz', 'w:gz') as archive:
            archive.add(destination / 'work', arcname='work')
        shutil.rmtree(destination / 'work')
    except Exception:
        shutil.rmtree(destination)
        raise


def extract_bot(result_dir, destination):
    """Only regular bot files cross from an untrusted workspace onto the host.

    Do not extractall: an agent can leave symlinks or traversal members in its
    workspace, and helpers outside bot/ would evade the bot tree digest.
    """
    seen = set()
    size = 0
    with tarfile.open(result_dir / 'workspace.tgz', 'r:gz') as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.parts[:2] != ('work', 'bot'):
                continue
            relative = PurePosixPath(*path.parts[2:])
            if (any(part.startswith('.') or part in ('node_modules', 'hidden', 'refengine')
                    for part in relative.parts) or path.is_absolute() or
                    member.issym() or member.islnk()):
                raise ValueError('unsafe bot archive member')
            if member.isdir():
                continue
            if not member.isfile() or relative.as_posix() in seen:
                raise ValueError('bot archive needs unique regular files')
            if relative.suffix not in ('.mjs', '.js', '.ts', '.mts', '.json'):
                raise ValueError('bot directory may contain only modules and JSON assets')
            size += member.size
            if size > 10 * 1024 * 1024 or len(seen) >= 1000:
                raise ValueError('bot deliverable exceeds 10 MiB or 1000 files')
            seen.add(relative.as_posix())
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source:
                target.write_bytes(source.read())
    if not (destination / ENTRY).is_file():
        raise ValueError('missing deliverable: work/bot/bot.mjs')


def visible_score(bot_dir):
    """The frozen visible simulator is cooperative; execute agent code in Docker.

    No host repository mount, network, or writable host path reaches that code.
    Only aggregate fields from successful evaluations enter the report.
    """
    image = os.environ.get('OAKEN_IMAGE', 'oaken-bench:1.0')
    reports = []
    with tempfile.TemporaryDirectory(prefix='oaken-t5-visible-') as td:
        work = Path(td) / 'work'
        stage_public(work)
        shutil.copytree(bot_dir, work / 'bot', dirs_exist_ok=True)
        for baseline in ('random', 'cheapest', 'merger'):
            name = 'oaken-t5-visible-' + uuid.uuid4().hex
            command = ['docker', 'run', '--rm', '--name', name, '--network', 'none',
                       '--cpus', '2', '--memory', '2g', '--pids-limit', '128',
                       '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                       '--read-only', '--tmpfs', '/tmp:rw,exec,size=512m',
                       '--user', f'{os.getuid()}:{os.getgid()}',
                       '-e', 'OAKEN_T5_SIM_LOG=off',
                       '-v', f'{work}:/work:ro', '--workdir', '/work',
                       '--entrypoint', '/bin/bash', image, 't5/bin/t5-sim',
                       'play', '--bot', './bot/bot.mjs', '--vs', f'baseline:{baseline}', '--json']
            try:
                run = subprocess.run(command, capture_output=True, timeout=140)
                if run.returncode:
                    raise ValueError(f'visible T5 evaluation against {baseline} failed; no score')
                raw = json.loads(run.stdout)
                if raw.get('protocol') != 't5-visible-head-to-head/1' or raw['stats']['matches'] != 200:
                    raise ValueError('invalid visible T5 evaluation')
                reports.append({key: raw[key] for key in (
                    'schemaVersion', 'protocol', 'contract', 'runtime', 'spec',
                    'simulator', 'engine', 'seeds', 'bots', 'stats', 'counters')})
            finally:
                subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
    return {'schemaVersion': 1, 'tier': 't5', 'runKind': 'model-visible-evaluation',
            'protocol': 't5-visible-head-to-head/1', 'evaluations': reports}


def publish_scores(result_dir, config, report, visible):
    """Prepare every destination before replacing either public score.

    A failed archive write must not leave one new score paired with an old
    one. Keep backups until all replacements succeed so ordinary I/O errors
    can restore the previous pair, including when scoring inside the archive.
    """
    archive = Path(os.environ.get('OAKEN_ARCHIVE', Path.home() / '.cache/oaken-bench')) / result_dir.name
    destinations = [result_dir]
    if archive.is_dir() and archive.resolve() != result_dir.resolve():
        destinations.append(archive)
    values = {'run.json': config, 'score.json': report, 'visible-score.json': visible}
    with ExitStack() as stack:
        replacements = []
        for directory in destinations:
            stage = Path(stack.enter_context(tempfile.TemporaryDirectory(
                prefix='.t5-score-', dir=directory)))
            for name, value in values.items():
                target = directory / name
                if target.is_symlink():
                    raise ValueError('score destination must not be a symlink')
                saved = stage / (name + '.previous') if target.exists() else None
                if saved is not None:
                    shutil.copyfile(target, saved)
                prepared = stage / name
                write_json(prepared, value)
                replacements.append((prepared, target, saved))
        changed = []
        try:
            for prepared, target, saved in replacements:
                prepared.replace(target)
                changed.append((target, saved))
        except BaseException:
            for target, saved in reversed(changed):
                if saved is None:
                    target.unlink()
                else:
                    saved.replace(target)
            raise


def score_run(result_dir, detail=True):
    from t5_oracle import score_run as sealed_score
    result_dir = Path(result_dir).resolve()
    if not (result_dir / 'workspace.tgz').is_file():
        return sealed_score(result_dir, detail=detail)
    if not result_dir.name.startswith('t5-'):
        raise ValueError('T5 results require a t5- label')
    meta = dict(part.split('=', 1) for part in (result_dir / 'run.meta').read_text().split()
                if '=' in part)
    provenance = {
        'schemaVersion': 1, 'runKind': 'bot-implementation',
        'harness': meta.get('harness'), 'model': meta.get('model'),
        'timeoutSeconds': int(meta['timeout']) if 'timeout' in meta else None,
        'exitCode': int((result_dir / 'exit.code').read_text()) if (result_dir / 'exit.code').exists() else None,
        'workspaceSha256': digest(result_dir / 'workspace.tgz'),
        'runnerSha256': digest(Path(__file__)),
        'runContextSha256': digest(result_dir / 'run-context.json') if (result_dir / 'run-context.json').exists() else None,
    }
    context = result_dir / 't5-run-context.json'
    if context.exists():
        provenance['inputs'] = json.loads(context.read_text())
    # Always restore the archived submission, even on rescoring. A loose copy
    # left from an earlier score must not silently replace the measured bot.
    with tempfile.TemporaryDirectory(prefix='oaken-t5-bot-') as td:
        bot_dir = Path(td) / 'bot'
        bot_dir.mkdir()
        extract_bot(result_dir, bot_dir)
        visible = visible_score(bot_dir)
        submission = result_dir / 'bot'
        if submission.is_symlink():
            raise ValueError('bot destination must not be a symlink')
        if submission.exists():
            shutil.rmtree(submission)
        shutil.copytree(bot_dir, submission)
        config = {'bot': str(submission / ENTRY)}
        staged_result = Path(td) / result_dir.name
        staged_result.mkdir()
        write_json(staged_result / 'run.json', config)
        for name in ('pi-events.jsonl', 'dsh-sessions.tgz'):
            trace = result_dir / name
            if trace.is_file():
                (staged_result / name).symlink_to(trace)
        # The existing oracle writes score.json. Confine that intermediate
        # output until provenance and both final artifacts are ready.
        progress = io.StringIO()
        with redirect_stdout(progress):
            report = sealed_score(staged_result, detail=detail)
    report['modelRun'] = provenance
    visible['modelRun'] = provenance
    publish_scores(result_dir, config, report, visible)
    print(progress.getvalue(), end='')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['assemble', 'provenance'])
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    if args.operation == 'assemble':
        assemble(args.destination)
    else:
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        image = os.environ.get('OAKEN_IMAGE', 'oaken-bench:1.0')
        image_id = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
        write_json(args.destination, {'protocol': 't5-model-run/1', 'revision': revision,
                   'image': image, 'imageId': image_id,
                   'promptSha256': digest(ROOT / 't5/PROMPT.txt'),
                   'frozenSha256': digest(ROOT / 't5/FROZEN.sha256'),
                   'runnerSha256': digest(Path(__file__)),
                   'entrypointSha256': digest(ROOT / 'docker/entrypoint.sh')})


if __name__ == '__main__':
    main()
