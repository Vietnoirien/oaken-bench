#!/usr/bin/env python3
"""Summarize only trace data that is observable while the container runs."""
import json
import os
import sys


def snapshot(out_dir, harness, elapsed):
    if harness == 'pi':
        path = os.path.join(out_dir, 'pi-events.jsonl')
        size = os.path.getsize(path) if os.path.exists(path) else 0
        turns = calls = 0
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    for line in f:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(event, dict):
                            continue
                        kind = event.get('type')
                        if kind == 'turn_start':
                            turns += 1
                        if kind == 'tool_execution_start':
                            calls += 1
            except OSError:
                turns = calls = None
        return f'elapsed={elapsed}s traceBytes={size} turns={turns if turns is not None else "n/a"} calls={calls if calls is not None else "n/a"}'
    path = os.path.join(out_dir, 'dsh-stdout.log')
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return f'elapsed={elapsed}s traceBytes=n/a stdoutBytes={size} turns=n/a calls=n/a'


if __name__ == '__main__':
    print(snapshot(sys.argv[1], sys.argv[2], int(sys.argv[3])))
