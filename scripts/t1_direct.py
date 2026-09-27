#!/usr/bin/env python3
"""One local, single-shot T1 module completion with a frozen input workspace."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import time
from urllib.parse import urlparse

from direct import chat
from t1 import ROOT, assemble, score_run
from t1_canary_preflight import CANARIES
from t5_canary_preflight import check
from server_config import capture


def local_endpoint(url):
    parsed = urlparse(url)
    return (parsed.scheme == 'http' and parsed.hostname in
            ('localhost', '127.0.0.1', '172.17.0.1') and parsed.path.rstrip('/') == '/v1')


def code_from_reply(response):
    choices = response.get('choices') or []
    if len(choices) != 1:
        raise ValueError('direct response has no single choice')
    content = (choices[0].get('message') or {}).get('content')
    if not isinstance(content, str) or not content.strip():
        raise ValueError('direct response has no code')
    match = re.fullmatch(r'\s*```(?:typescript|ts)?\s*\n(.*?)\n```\s*', content, re.S | re.I)
    code = match.group(1) if match else content
    if not code.strip() or len(code.encode()) > 100_000:
        raise ValueError('direct response has invalid code size')
    return code + ('\n' if not code.endswith('\n') else '')


def prompt_for(work, module):
    parts = [f'Implement src/{module}.ts. Return only the complete TypeScript file, '
             'without explanation. Keep its public interface and edit no other file.',
             'SPEC.md:\n' + (work / 'SPEC.md').read_text()]
    for path in sorted((work / 'src').glob('*.ts')):
        parts.append(f'{path.relative_to(work)}:\n{path.read_text()}')
    return '\n\n'.join(parts)


def run(module, model, base_url, label, max_tokens, timeout):
    if not local_endpoint(base_url):
        raise ValueError('T1 direct mode accepts only a local HTTP endpoint')
    if not label.startswith('t1-' + module + '-'):
        raise ValueError('label must start with t1-<module>-')
    out = ROOT / '.t1-direct-results' / label
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    (out / 'run-context.json').write_text(json.dumps(
        capture(base_url.removesuffix('/v1'), image_ref=os.environ.get('OAKEN_IMAGE', 'oaken-bench:1.0'),
                port=urlparse(base_url).port, model=model), indent=2) + '\n')
    checks = {}
    for name, (question, expected) in CANARIES.items():
        checks[name] = check(base_url, model, question, expected)
        if checks[name]['status'] != 'passed':
            break
    passed = len(checks) == len(CANARIES) and all(c['status'] == 'passed' for c in checks.values())
    (out / 'canary-preflight.json').write_text(json.dumps({
        'schemaVersion': 1, 'checkedAt': datetime.now(timezone.utc).isoformat(),
        'model': model, 'status': 'passed' if passed else 'failed', 'checks': checks}, indent=2) + '\n')
    if not passed:
        raise RuntimeError('T1 direct canary check failed')
    with tempfile.TemporaryDirectory(prefix='oaken-t1-direct-') as td:
        package = assemble(Path(td) / 'package', module)
        shutil.copyfile(package / 'manifest.json', out / 't1-manifest.json')
        with tarfile.open(package / 'workspace.tgz') as archive:
            archive.extractall(td, filter='data')
        work = Path(td) / 'work'
        prompt = prompt_for(work, module)
        (out / 'prompt.txt').write_text(prompt)
        (out / 'run.meta').write_text(
            f'harness=direct model={model} timeout={timeout} maxTokens={max_tokens} tier=t1\n')
        start = time.monotonic()
        try:
            response = chat(base_url, model, [{'role': 'user', 'content': prompt}],
                            max_tokens=max_tokens, timeout=timeout)
            (out / 'raw-response.json').write_text(json.dumps(response) + '\n')
            (work / 'src' / (module + '.ts')).write_text(code_from_reply(response))
            exit_code = 0
        except Exception as error:
            (out / 'direct-error.txt').write_text(type(error).__name__ + '\n')
            exit_code = 1
        (out / 'wallclock.seconds').write_text(str(round(time.monotonic() - start)) + '\n')
        (out / 'exit.code').write_text(str(exit_code) + '\n')
        with tarfile.open(out / 'workspace.tgz', 'w:gz') as archive:
            archive.add(work, arcname='work')
    archive = Path(os.environ.get('OAKEN_ARCHIVE', Path.home() / '.cache/oaken-bench')) / label
    archive.mkdir(parents=True, exist_ok=False)
    shutil.copytree(out, archive, dirs_exist_ok=True)
    report = score_run(out)
    shutil.copyfile(out / 'score.json', archive / 'score.json')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('module')
    parser.add_argument('model')
    parser.add_argument('label')
    parser.add_argument('--base-url', default='http://172.17.0.1:8080/v1')
    parser.add_argument('--max-tokens', type=int, default=8192)
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    run(args.module, args.model, args.base_url, args.label, args.max_tokens, args.timeout)


if __name__ == '__main__':
    main()
