import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from run_progress import snapshot  # noqa: E402


def test_pi_progress_counts_observable_events_and_bytes(tmp_path):
    trace = tmp_path / 'pi-events.jsonl'
    trace.write_text('\n'.join([
        json.dumps({'type': 'turn_start'}),
        json.dumps({'type': 'tool_execution_start'}),
        '{truncated',
    ]))
    line = snapshot(str(tmp_path), 'pi', 31)
    assert f'traceBytes={trace.stat().st_size}' in line
    assert 'turns=1 calls=1' in line


def test_dsh_progress_marks_unavailable_metrics_explicit(tmp_path):
    (tmp_path / 'dsh-stdout.log').write_text('visible output')
    line = snapshot(str(tmp_path), 'dsh', 60)
    assert 'traceBytes=n/a stdoutBytes=14' in line
    assert 'turns=n/a calls=n/a' in line
