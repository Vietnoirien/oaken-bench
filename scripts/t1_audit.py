#!/usr/bin/env python3
"""Fail if a publishable T1 JSON artifact resembles a raw trace or source."""
import json
from pathlib import Path
import re

from t1 import ROOT

SUSPECT = re.compile(r'```|<\|channel>|\b(?:function|export|import)\s+|\b(?:const|let)\s+\w+\s*=')


def inspect(value, path):
    if isinstance(value, dict):
        for key, item in value.items():
            inspect(key, path + '.key')
            inspect(item, path + '.' + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            inspect(item, f'{path}[{index}]')
    elif isinstance(value, str):
        if len(value) > 256 or '\n' in value or SUSPECT.search(value):
            raise ValueError('possible raw code or trace in ' + path)


def main():
    files = []
    for directory in (ROOT / 'results').glob('t1-*'):
        for name in ('score.json', 'events-summary.json', 'canary-preflight.json'):
            path = directory / name
            if path.exists():
                files.append(path)
    files.extend((ROOT / 't1').glob('*.json'))
    for path in files:
        inspect(json.loads(path.read_text()), str(path.relative_to(ROOT)))
    print(f'audited {len(files)} publishable T1 JSON files')


if __name__ == '__main__':
    main()
