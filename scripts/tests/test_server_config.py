"""Tests for scripts/server_config.py (issue #14).

No live llama-server: the /props test spins up a stdlib
`http.server.HTTPServer` in a background thread that returns a realistic
`/props` payload (shape taken from llama.cpp's tools/server/README.md --
see the module docstring). Host-only probes (`ps`, `nvidia-smi`, `docker`)
are exercised through `unittest.mock.patch('subprocess.run', ...)` rather
than the real binaries, so this suite runs the same with or without a GPU,
Docker, or network -- matching this repo's "stdlib-only, network off"
constraint (AGENTS.md).
"""
import http.server
import json
import os
import sys
import tempfile
import threading
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server_config import (  # noqa: E402
    capture, fetch_props, find_server_cmdline, host_gpus, image_provenance,
    parse_launch_flags, stat_model_file,
)


# A realistic /props body, per tools/server/README.md on ggml-org/llama.cpp
# master (fetched 2026-09-24): default_generation_settings carries n_ctx,
# the context size actually allocated; build_info is the llama.cpp build.
PROPS_BODY = {
    'default_generation_settings': {'n_ctx': 131072, 'temperature': 0.8},
    'total_slots': 1,
    'model_path': '/models/gemma-4-12B-it-qat-UD-Q4_K_XL.gguf',
    'chat_template': '{% for message in messages %}...{% endfor %}',
    'chat_template_caps': {},
    'modalities': {'vision': False},
    'build_info': 'b10751-abc1234',
    'is_sleeping': False,
}


class _PropsHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/props':
            body = json.dumps(PROPS_BODY).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/props-not-json':
            body = b'not json'
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):  # silence -- pytest -q output should stay quiet
        pass


@pytest.fixture(scope='module')
def fake_server():
    srv = http.server.HTTPServer(('127.0.0.1', 0), _PropsHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f'http://127.0.0.1:{srv.server_port}'
    srv.shutdown()
    t.join(timeout=5)


# ---------------------------------------------------------------------------
# fetch_props
# ---------------------------------------------------------------------------

def test_fetch_props_returns_parsed_json(fake_server):
    props = fetch_props(fake_server)
    assert props == PROPS_BODY


def test_fetch_props_unreachable_server_reports_error_not_raise():
    props = fetch_props('http://127.0.0.1:1')  # nothing listens on port 1
    assert 'error' in props


def test_fetch_props_404_reports_error():
    # server is up but this build has no /props (older llama.cpp)
    props = fetch_props('http://does-not-exist.invalid.example:9')
    assert 'error' in props


# ---------------------------------------------------------------------------
# parse_launch_flags
# ---------------------------------------------------------------------------

def test_parse_launch_flags_extracts_documented_flags():
    cmdline = (
        'llama-server --model m.gguf --alias m.gguf --jinja --gpu-layers 99 '
        '--ctx-size 131072 --cache-type-k q4_0 --cache-type-v q4_0 '
        '--parallel 1 --device CUDA0,CUDA1 --tensor-split 1,0 '
        '--batch-size 512 --ubatch-size 512 --reasoning-format none '
        "-ot 'blk\\.(1[5-9]|[23][0-9])\\.ffn_.*_exps\\.=CUDA1'"
    )
    flags = parse_launch_flags(cmdline)
    assert flags['ctxSize'] == '131072'
    assert flags['cacheTypeK'] == 'q4_0'
    assert flags['cacheTypeV'] == 'q4_0'
    assert flags['device'] == 'CUDA0,CUDA1'
    assert flags['tensorSplit'] == '1,0'
    assert flags['reasoningFormat'] == 'none'


def test_parse_launch_flags_empty_input():
    assert parse_launch_flags(None) == {}
    assert parse_launch_flags('') == {}


# ---------------------------------------------------------------------------
# find_server_cmdline (mocked subprocess -- no real ps dependency)
# ---------------------------------------------------------------------------

def _ps_output(lines):
    class R:
        stdout = '\n'.join(lines)
    return R()


def test_find_server_cmdline_single_match():
    ps_lines = ['  PID CMD', '  123 node something', '  456 llama-server --port 8080 --model m.gguf']
    with patch('subprocess.run', return_value=_ps_output(ps_lines)):
        found = find_server_cmdline(port=8080)
    assert 'llama-server' in found


def test_find_server_cmdline_none_when_absent():
    ps_lines = ['  PID CMD', '  123 node something']
    with patch('subprocess.run', return_value=_ps_output(ps_lines)):
        found = find_server_cmdline()
    assert found is None


def test_find_server_cmdline_ps_missing_returns_none():
    with patch('subprocess.run', side_effect=FileNotFoundError('no ps')):
        assert find_server_cmdline() is None


# ---------------------------------------------------------------------------
# stat_model_file
# ---------------------------------------------------------------------------

def test_stat_model_file_hashes_small_file():
    with tempfile.NamedTemporaryFile(suffix='.gguf', delete=False) as f:
        f.write(b'fake gguf bytes')
        path = f.name
    try:
        info = stat_model_file(path, hash_max_bytes=1024)
        assert info['sizeBytes'] == len(b'fake gguf bytes')
        assert info['sha256'] == __import__('hashlib').sha256(b'fake gguf bytes').hexdigest()
    finally:
        os.unlink(path)


def test_stat_model_file_skips_hash_above_cap():
    with tempfile.NamedTemporaryFile(suffix='.gguf', delete=False) as f:
        f.write(b'x' * 100)
        path = f.name
    try:
        info = stat_model_file(path, hash_max_bytes=10)
        assert info['sha256'] is None
        assert 'sha256Skipped' in info
        assert info['sizeBytes'] == 100
    finally:
        os.unlink(path)


def test_stat_model_file_missing_path_reports_error_not_raise():
    info = stat_model_file('/no/such/model.gguf')
    assert 'error' in info


def test_stat_model_file_no_path_reports_error():
    info = stat_model_file(None)
    assert 'error' in info


# ---------------------------------------------------------------------------
# host_gpus / image_provenance (mocked subprocess)
# ---------------------------------------------------------------------------

def test_host_gpus_parses_csv():
    csv_out = 'NVIDIA GeForce RTX 5070, 12288 MiB, 550.54.15\nNVIDIA GeForce RTX 3060, 12288 MiB, 550.54.15\n'
    with patch('subprocess.run', return_value=type('R', (), {'returncode': 0, 'stdout': csv_out, 'stderr': ''})()):
        gpus = host_gpus()
    assert len(gpus) == 2
    assert gpus[0]['name'] == 'NVIDIA GeForce RTX 5070'


def test_host_gpus_missing_binary_reports_error_not_raise():
    with patch('subprocess.run', side_effect=FileNotFoundError('no nvidia-smi')):
        result = host_gpus()
    assert 'error' in result


def test_image_provenance_reads_id_and_harness_versions():
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ['docker', 'inspect']:
            return type('R', (), {'returncode': 0, 'stdout': 'sha256:deadbeef\n', 'stderr': ''})()
        if cmd[:3] == ['docker', 'run', '--rm']:
            payload = json.dumps({'dependencies': {
                '@earendil-works/pi-coding-agent': {'version': '0.86.0'},
                '@deepseek-ai/dsh': {'version': '0.1.5-rc.2'},
                'npm': {'version': '10.0.0'},
            }})
            return type('R', (), {'returncode': 0, 'stdout': payload, 'stderr': ''})()
        raise AssertionError(f'unexpected command: {cmd}')
    with patch('subprocess.run', side_effect=fake_run):
        info = image_provenance('oaken-bench:1.0')
    assert info['imageId'] == 'sha256:deadbeef'
    assert info['harnessVersions'] == {
        '@earendil-works/pi-coding-agent': '0.86.0',
        '@deepseek-ai/dsh': '0.1.5-rc.2',
    }


def test_image_provenance_docker_missing_reports_error_not_raise():
    with patch('subprocess.run', side_effect=FileNotFoundError('no docker')):
        info = image_provenance('oaken-bench:1.0')
    assert 'imageIdError' in info
    assert 'harnessVersionsError' in info


# ---------------------------------------------------------------------------
# capture(): end-to-end against the fake server, with host probes mocked
# ---------------------------------------------------------------------------

def test_capture_assembles_full_record(fake_server):
    ps_lines = ['456 llama-server --port 9 --ctx-size 131072 --cache-type-k q4_0']
    with patch('subprocess.run') as run:
        def side_effect(cmd, **kwargs):
            if cmd[0] == 'ps':
                return _ps_output(ps_lines)
            if cmd[0] == 'nvidia-smi':
                return type('R', (), {'returncode': 0, 'stdout': 'RTX 5070, 12288 MiB, 550\n', 'stderr': ''})()
            raise AssertionError(f'unexpected command in capture(): {cmd}')
        run.side_effect = side_effect
        ctx = capture(fake_server, port=9)
    assert ctx['props'] == PROPS_BODY
    assert ctx['launchFlags']['ctxSize'] == '131072'
    assert ctx['modelFile']['error']  # /models/... does not exist on this machine
    assert isinstance(ctx['hostGpus'], list) and ctx['hostGpus'][0]['name'] == 'RTX 5070'
    assert 'capturedAtEpoch' in ctx


def test_capture_without_host_info_skips_host_probes(fake_server):
    # No subprocess.run patch installed: if capture() tried to shell out here
    # it would hit the real ps/nvidia-smi and either fail the test sandbox or
    # flake depending on the host. include_host_info=False must not call them.
    ctx = capture(fake_server, include_host_info=False)
    assert 'processCmdline' not in ctx
    assert 'hostGpus' not in ctx
    assert ctx['props'] == PROPS_BODY


def test_capture_server_down_still_returns_a_dict():
    ctx = capture('http://127.0.0.1:1', include_host_info=False)
    assert 'error' in ctx['props']
    assert isinstance(ctx, dict)
