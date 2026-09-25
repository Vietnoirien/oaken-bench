"""Tests for scripts/direct_env.py (issue #28).

No GPU, no live llama-server: `nvidia-smi` is mocked via
`unittest.mock.patch('subprocess.run', ...)`, same pattern as
test_server_config.py, and `server_config.capture()` itself is monkeypatched
for the `build_environment()` tests so this suite pins direct_env.py's own
logic (locality gate, backend classification, VRAM merge) without needing a
real /props endpoint -- server_config.py already has its own tests for that.
"""
import os
import sys
import threading
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import direct_env  # noqa: E402
from direct_env import (  # noqa: E402
    VramSampler, build_environment, detect_backend, is_local_base_url,
)


# ---------------------------------------------------------------------------
# is_local_base_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('url', [
    'http://localhost:8080/v1',
    'http://127.0.0.1:8080/v1',
    'http://127.5.5.5:8080/v1',
    'http://172.17.0.1:8080/v1',
])
def test_is_local_base_url_recognises_local_hosts(url):
    assert is_local_base_url(url) is True


@pytest.mark.parametrize('url', [
    'https://api.example.com/v1',
    'http://192.168.1.50:8080/v1',
    'http://172.17.0.2:8080/v1',  # a different container, not the gateway
    'not a url at all',
    '',
])
def test_is_local_base_url_rejects_remote_or_unparsable(url):
    assert is_local_base_url(url) is False


# ---------------------------------------------------------------------------
# detect_backend
# ---------------------------------------------------------------------------

def test_detect_backend_reads_cuda_from_device_flag():
    name, reason = detect_backend({'device': 'CUDA0,CUDA1'}, [])
    assert name == 'cuda'
    assert '--device' in reason


def test_detect_backend_reads_rocm_from_device_flag():
    name, reason = detect_backend({'device': 'ROCm0'}, [])
    assert name == 'rocm'


def test_detect_backend_reads_vulkan_from_override_tensor_flag():
    name, reason = detect_backend({'overrideTensor': r'blk\.0\.=Vulkan0'}, [])
    assert name == 'vulkan'
    assert '-ot' in reason


def test_detect_backend_falls_back_to_cuda_inference_from_nvidia_smi():
    name, reason = detect_backend({}, [{'name': 'RTX 5070'}])
    assert name == 'cuda'
    assert 'cannot rule out' in reason


def test_detect_backend_unknown_when_no_signal_at_all():
    name, reason = detect_backend({}, [])
    assert name == 'unknown'
    name2, _ = detect_backend({}, {'error': 'nvidia-smi unavailable'})
    assert name2 == 'unknown'


def test_detect_backend_device_flag_wins_over_nvidia_smi_signal():
    # Vulkan build on an NVIDIA card: the flag is authoritative, the weaker
    # nvidia-smi-implies-cuda fallback must not override it.
    name, _ = detect_backend({'device': 'Vulkan0'}, [{'name': 'RTX 5070'}])
    assert name == 'vulkan'


# ---------------------------------------------------------------------------
# VramSampler
# ---------------------------------------------------------------------------

def _nvidia_smi_result(csv_out):
    return type('R', (), {'returncode': 0, 'stdout': csv_out, 'stderr': ''})()


def test_vram_sampler_reports_peak_across_samples():
    readings = iter([
        'index, memory.used [MiB]\n0, 1000\n1, 500\n',
        '0, 4000\n1, 600\n',
        '0, 2000\n1, 700\n',
    ])

    def fake_run(cmd, **kwargs):
        return _nvidia_smi_result(next(readings, '0, 2000\n1, 700\n'))

    with patch('subprocess.run', side_effect=fake_run):
        sampler = VramSampler(interval=0.01)
        with sampler:
            time.sleep(0.05)
    peak = sampler.peak_mib()
    assert peak[0] == 4000
    assert peak[1] == 700


def test_vram_sampler_reports_error_when_nvidia_smi_never_answers():
    with patch('subprocess.run', side_effect=FileNotFoundError('no nvidia-smi')):
        sampler = VramSampler(interval=0.01)
        with sampler:
            time.sleep(0.02)
    peak = sampler.peak_mib()
    assert 'error' in peak


def test_vram_sampler_ignores_malformed_lines():
    with patch('subprocess.run', return_value=_nvidia_smi_result('not,a,number,line\n0, 1234\n')):
        sampler = VramSampler(interval=0.01)
        with sampler:
            pass
    assert sampler.peak_mib() == {0: 1234}


def test_vram_sampler_samples_even_for_a_run_shorter_than_one_interval():
    # __enter__ samples synchronously before starting the thread, so a body
    # that returns immediately still gets at least one reading.
    with patch('subprocess.run', return_value=_nvidia_smi_result('0, 999\n')):
        sampler = VramSampler(interval=10)
        with sampler:
            pass
    assert sampler.peak_mib() == {0: 999}


# ---------------------------------------------------------------------------
# build_environment
# ---------------------------------------------------------------------------

def _fake_capture_factory(props=None, launch_flags=None, host_gpus=None):
    def fake_capture(base_url, *, image_ref=None, port=None, include_host_info=True,
                      hash_max_bytes=None):
        ctx = {'propsUrl': base_url, 'props': props or {}}
        if include_host_info:
            ctx['launchFlags'] = launch_flags or {}
            ctx['hostGpus'] = host_gpus if host_gpus is not None else []
        return ctx
    return fake_capture


def test_build_environment_local_populates_backend_and_gpus():
    fake_capture = _fake_capture_factory(
        launch_flags={'device': 'CUDA0,CUDA1'},
        host_gpus=[{'name': 'RTX 5070', 'memoryTotal': '12288 MiB'},
                   {'name': 'RTX 3060', 'memoryTotal': '12288 MiB'}])
    with patch.object(direct_env.server_config, 'capture', side_effect=fake_capture):
        env = build_environment('http://127.0.0.1:8080/v1', vram_peak={0: 9000, 1: 3000})
    assert env['local'] is True
    assert env['backend']['name'] == 'cuda'
    assert env['gpus'][0]['peakVramUsedMiB'] == 9000
    assert env['gpus'][1]['peakVramUsedMiB'] == 3000
    assert env['gpus'][0]['name'] == 'RTX 5070'


def test_build_environment_local_missing_vram_sample_is_explicit_not_zero():
    fake_capture = _fake_capture_factory(host_gpus=[{'name': 'RTX 5070'}])
    with patch.object(direct_env.server_config, 'capture', side_effect=fake_capture):
        env = build_environment('http://127.0.0.1:8080/v1', vram_peak={})
    assert env['gpus'][0]['peakVramUsedMiB'] is None
    assert 'peakVramError' in env['gpus'][0]


def test_build_environment_local_vram_sampler_error_propagates_per_gpu():
    fake_capture = _fake_capture_factory(host_gpus=[{'name': 'RTX 5070'}])
    with patch.object(direct_env.server_config, 'capture', side_effect=fake_capture):
        env = build_environment('http://127.0.0.1:8080/v1',
                                 vram_peak={'error': 'nvidia-smi unavailable: no binary'})
    assert env['gpus'][0]['peakVramUsedMiB'] is None
    assert env['gpus'][0]['peakVramError'] == 'nvidia-smi unavailable: no binary'


def test_build_environment_local_host_gpus_error_carried_through():
    fake_capture = _fake_capture_factory(host_gpus={'error': 'nvidia-smi unavailable: no binary'})
    with patch.object(direct_env.server_config, 'capture', side_effect=fake_capture):
        env = build_environment('http://127.0.0.1:8080/v1')
    assert env['gpus'] == {'error': 'nvidia-smi unavailable: no binary'}
    assert env['backend']['name'] == 'unknown'


def test_build_environment_remote_base_url_is_unknown_with_reason():
    fake_capture = _fake_capture_factory()
    with patch.object(direct_env.server_config, 'capture', side_effect=fake_capture) as capture:
        env = build_environment('https://api.example.com/v1')
        # include_host_info must be False for a remote URL: this process must
        # not run host-only probes (ps/nvidia-smi) against an unrelated host.
        assert capture.call_args.kwargs['include_host_info'] is False
    assert env['local'] is False
    assert env['backend'] == {'name': 'unknown', 'reason': env['backend']['reason']}
    assert 'reason' in env['backend'] and env['backend']['reason']
    assert env['gpus'] == {'error': env['backend']['reason']}


def test_build_environment_explicit_remote_flag_overrides_local_looking_url():
    fake_capture = _fake_capture_factory()
    with patch.object(direct_env.server_config, 'capture', side_effect=fake_capture) as capture:
        env = build_environment('http://127.0.0.1:8080/v1', remote=True)
        assert capture.call_args.kwargs['include_host_info'] is False
    assert env['local'] is False
    assert env['backend']['name'] == 'unknown'


def test_build_environment_explicit_remote_false_forces_local_path():
    # A hostname this module's heuristic would not recognise as local, but
    # the caller knows it is (e.g. a private DNS alias for the same box).
    fake_capture = _fake_capture_factory(host_gpus=[{'name': 'RTX 5070'}],
                                          launch_flags={'device': 'CUDA0'})
    with patch.object(direct_env.server_config, 'capture', side_effect=fake_capture) as capture:
        env = build_environment('http://llama-box.internal:8080/v1', remote=False)
        assert capture.call_args.kwargs['include_host_info'] is True
    assert env['local'] is True
    assert env['backend']['name'] == 'cuda'


def test_server_root_url_strips_v1_suffix():
    assert direct_env.server_root_url('http://172.17.0.1:8080/v1') == 'http://172.17.0.1:8080'
    assert direct_env.server_root_url('http://172.17.0.1:8080/v1/') == 'http://172.17.0.1:8080'
    assert direct_env.server_root_url('http://127.0.0.1:8099') == 'http://127.0.0.1:8099'


def test_build_environment_asks_capture_for_the_root_not_v1(monkeypatch):
    seen = {}

    def fake_capture(url, **kw):
        seen['url'] = url
        return {}

    monkeypatch.setattr(direct_env.server_config, 'capture', fake_capture)
    direct_env.build_environment('https://api.example.com/v1', remote=True)
    assert seen['url'] == 'https://api.example.com'
