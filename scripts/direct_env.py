#!/usr/bin/env python3
"""Run-environment capture for direct-mode battery artefacts (issue #28).

`toolbattery.py` (#8) and the T0.5 batteries #31/#32 talk to a model with no
harness and no container in between. Without this module their artefacts
carry a score and nothing about what produced it: which GPU, which backend
(CUDA / Vulkan / ROCm), how much VRAM the run actually used, or which
llama-server flags were live. Issue #26's comment names exactly this gap
for the other project consuming these numbers.

This module does not re-implement `/props` parsing or `nvidia-smi`
inventory parsing -- `server_config.capture()` and `server_config.host_gpus()`
(#14) already own that, and a second copy would drift from the first the
way MODELS.md's "declared vs served" gap describes for context size. What
this module adds on top:

  1. `VramSampler` -- a context manager that polls `nvidia-smi` for
     per-card memory.used on a background thread while the battery runs,
     and reports the peak. `ctxprobe.sh` documents why load-time VRAM is
     the wrong number: llama.cpp allocates the KV cache lazily, so the
     peak is reached only once requests are actually made, and only
     sampling *during* the run can catch it.
  2. `build_environment()` -- assembles the artefact's `environment` block
     by calling `server_config.capture()` and `VramSampler`'s result,
     plus backend classification (below) and the remote/local gate #28
     asks for.

## Backend detection: what is told apart, and what is not

llama.cpp's `/props` does not name a backend (see server_config.py's
docstring on that endpoint's shape) and neither does `build_info` -- it is
a build number and commit hash, the same string regardless of which
`GGML_*` backend that build was compiled with. So this looks at two other
signals, in order, and is explicit about which is a fact and which is a
guess:

  - **Launch flags name it directly.** llama.cpp's own device-naming
    convention prefixes a device with its backend: `CUDA0`, `ROCm0`,
    `Vulkan0` (`--device` or an `-ot` override's `=CUDA1` suffix, both
    already parsed by `server_config.parse_launch_flags()`). When one of
    these prefixes appears in `launchFlags['device']` or
    `launchFlags['overrideTensor']`, that IS the backend -- this is
    llama-server's own command line, not an inference.
  - **Otherwise, `nvidia-smi` answering is a weak fallback, not proof.**
    This repo's own hardware (MODELS.md) is NVIDIA-only, so a responding
    `nvidia-smi` is *evidence* of a CUDA build. It is not conclusive: the
    same NVIDIA card can run a Vulkan build of llama.cpp, and this signal
    cannot distinguish that case. The reason string says so, so a reader
    of the artefact knows this field is an inference, not a readback.
  - **No device flag and no nvidia-smi is `"unknown"`.** ROCm leaves no
    positive signal here (no `rocm-smi` probe exists in this repo, and
    inferring ROCm from "nvidia-smi failed" would be guessing a specific
    wrong answer instead of admitting there is none) -- see #28's
    acceptance criterion that hardware fields must be `"unknown"` with a
    reason, never guessed.

## Remote endpoints: hardware fields are unknown, not absent

Per #26's maintainer comment, direct mode also runs against hosted HTTP
APIs. This process cannot see another host's GPUs, processes, or launch
flags -- reporting empty fields there would look like "we checked and
found nothing" when the true answer is "we could not check". So a
non-local `base_url` (anything whose hostname is not `localhost`,
`127.0.0.1`/`127.x`, or `172.17.0.1` -- this repo's documented Docker
bridge gateway, see run.sh) short-circuits straight to `"unknown"` with a
reason, and `server_config.capture()` is called with
`include_host_info=False` so it does not even try the host-only probes.
An explicit `remote=True` forces the same path for a base URL that
resolves local but is not (an SSH tunnel to a real remote box, say);
`remote=False` forces the opposite for a non-default local hostname this
heuristic does not recognise.
"""
import re
import subprocess
import threading
import time
import urllib.parse

import server_config

# This repo's two documented "the server is on this machine" spellings:
# bare localhost/loopback, and 172.17.0.1, which run.sh's own comment names
# as the default Docker bridge gateway address llama-server is expected to
# listen behind. Anything else is treated as a real remote host.
_LOCAL_HOSTNAMES = {'localhost', '172.17.0.1'}

# llama.cpp's own device-naming convention across backends (confirmed in
# server_config.py's test fixtures and MODELS.md's `--device CUDA0,CUDA1`
# examples): a device name is BACKEND + index. Order matters only in that
# all three are tried; a build using two of these at once is not a
# configuration this repo's hardware can produce, so first match wins.
_DEVICE_BACKEND_PATTERN = re.compile(r'(CUDA|ROCm|Vulkan)\d*')
_DEVICE_BACKEND_NAMES = {'CUDA': 'cuda', 'ROCm': 'rocm', 'Vulkan': 'vulkan'}


def is_local_base_url(base_url):
    """True if `base_url`'s hostname is one this process can plausibly
    share a machine (and therefore a GPU, a process table, a filesystem)
    with. Unparsable input is treated as NOT local -- "cannot tell" must
    not default to trusting host probes that would silently describe the
    wrong machine."""
    try:
        host = urllib.parse.urlsplit(base_url).hostname
    except ValueError:
        return False
    if not host:
        return False
    return host in _LOCAL_HOSTNAMES or host == '127.0.0.1' or host.startswith('127.')


def detect_backend(launch_flags, host_gpus_result):
    """Classify CUDA / Vulkan / ROCm / unknown. See the module docstring's
    "Backend detection" section for what each branch does and does not
    prove; returns (name, reason)."""
    device_flag = (launch_flags or {}).get('device') or ''
    ot_flag = (launch_flags or {}).get('overrideTensor') or ''
    for source_name, text in (('--device', device_flag), ('-ot', ot_flag)):
        m = _DEVICE_BACKEND_PATTERN.search(text)
        if m:
            prefix = m.group(1)
            return _DEVICE_BACKEND_NAMES[prefix], (
                f'{source_name} flag names device {m.group(0)!r} -- llama.cpp prefixes '
                f'device names with their backend, so this is read off the launch command, '
                f'not inferred')
    if isinstance(host_gpus_result, list) and host_gpus_result:
        return 'cuda', (
            'no --device/-ot flag seen, but nvidia-smi reported GPU(s); this repo\'s only '
            'tested hardware is NVIDIA (MODELS.md), so CUDA is the default inference -- this '
            'cannot rule out a Vulkan build running against the same NVIDIA card')
    return 'unknown', (
        'no --device/-ot flag named a backend, and nvidia-smi reported no GPUs or was '
        'unreachable -- there is no ROCm probe in this repo, so a failed nvidia-smi is not '
        'evidence of ROCm, only an absence of CUDA evidence')


class VramSampler:
    """Context manager: polls `nvidia-smi` for per-card `memory.used` on a
    background thread for the duration of the `with` block, and reports the
    peak per GPU index afterwards via `peak_mib()`.

    Load-time VRAM is the wrong number for this repo's models -- llama.cpp
    allocates the KV cache lazily as context fills (`ctxprobe.sh`'s
    docstring), so the peak is only reached while requests are actually in
    flight. Hence sampling *during* the battery, not before/after it.

    Deliberately its own small `nvidia-smi --query-gpu=index,memory.used`
    call rather than a second pass over `server_config.host_gpus()`'s
    output: that function reports static inventory (name/total/driver)
    once, not a live series, and reusing its parsing for a different query
    shape would only entangle the two. Both still go through nvidia-smi's
    CSV output in the same way host_gpus() does.
    """

    def __init__(self, interval=0.5):
        self.interval = interval
        self._peak = {}
        self._error = None
        self._stop = threading.Event()
        self._thread = None

    def _sample_once(self):
        try:
            out = subprocess.run(
                ['nvidia-smi', '--query-gpu=index,memory.used', '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            self._error = f'nvidia-smi unavailable: {e}'
            return
        if out.returncode != 0:
            self._error = f'nvidia-smi exited {out.returncode}: {out.stderr.strip()[:200]}'
            return
        for line in out.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(',')]
            if len(parts) != 2:
                continue
            try:
                index, used_mib = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            self._peak[index] = max(self._peak.get(index, 0), used_mib)

    def _loop(self):
        while not self._stop.is_set():
            self._sample_once()
            self._stop.wait(self.interval)

    def __enter__(self):
        # Sample once synchronously before the thread starts: the caller's
        # workload can begin the instant __enter__ returns, and a run short
        # enough to finish inside one `interval` must still get a reading.
        self._sample_once()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 5)
        # And once more after the workload ends, in case the peak landed
        # while the loop was mid-sleep and would otherwise be missed.
        self._sample_once()
        return False

    def peak_mib(self):
        """{gpu_index: peak MiB observed} across the sampling window. When
        nvidia-smi was never reachable, returns {'error': ...} instead of
        an empty dict -- an empty dict would silently read as "sampled, 0
        GPUs found" rather than "could not sample at all"."""
        if not self._peak and self._error:
            return {'error': self._error}
        return dict(self._peak)


def server_root_url(base_url):
    """The server root for a battery's OpenAI-style base URL.

    Batteries are pointed at `.../v1` because that is where
    `/chat/completions` lives, but llama-server serves `/props` and
    `/tokenize` at the ROOT, not under `/v1`. Passing the `/v1` URL straight
    to `server_config.capture()` asks for `/v1/props`, gets a 404, and the
    artefact silently records "no server config" for every local run."""
    u = base_url.rstrip('/')
    return u[:-3] if u.endswith('/v1') else u


def build_environment(base_url, *, image_ref=None, port=None, remote=None, vram_peak=None):
    """Assemble the `environment` block for a direct-mode battery artefact.

    `remote`: None auto-detects locality from `base_url` (`is_local_base_url`);
    pass True/False to override it explicitly (issue #28: "or an explicit
    flag") -- e.g. an SSH tunnel that resolves to 127.0.0.1 but is not
    actually this machine.

    `vram_peak`: the dict `VramSampler.peak_mib()` returned after wrapping
    the actual battery call. This function does not sample itself -- it is
    meant to be called once, after the run, to assemble the record from
    what the caller already collected -- so a caller that skips the
    sampler simply gets `peakVramUsedMiB: null` per GPU with a reason,
    never a guess.
    """
    local = is_local_base_url(base_url) if remote is None else not remote
    server_cfg = server_config.capture(server_root_url(base_url), image_ref=image_ref, port=port,
                                        include_host_info=local)
    env = {'baseUrl': base_url, 'local': local, 'serverConfig': server_cfg}

    if not local:
        reason = ('base URL is not localhost/127.x/172.17.0.1, or remote=True was passed '
                   'explicitly: this process cannot see another host\'s GPUs, processes, or '
                   'launch flags')
        env['backend'] = {'name': 'unknown', 'reason': reason}
        env['gpus'] = {'error': reason}
        return env

    launch_flags = server_cfg.get('launchFlags') or {}
    host_gpus_result = server_cfg.get('hostGpus')
    name, reason = detect_backend(launch_flags, host_gpus_result)
    env['backend'] = {'name': name, 'reason': reason}

    if not isinstance(host_gpus_result, list):
        # host_gpus() itself failed (see server_config.host_gpus): already an
        # {'error': ...} dict, carried through as-is rather than re-wrapped.
        env['gpus'] = host_gpus_result
        return env

    peak = vram_peak if isinstance(vram_peak, dict) else {}
    peak_error = peak.get('error')
    gpus = []
    # nvidia-smi lists GPUs in index order by default, and both host_gpus()
    # and VramSampler query it without --id, so position in host_gpus()'s
    # list is assumed to equal the CUDA device index VramSampler recorded.
    # Not verified against a live multi-GPU box in this environment (no GPU
    # access for this work -- see AGENTS.md); flagged here rather than
    # silently assumed correct.
    for index, gpu in enumerate(host_gpus_result):
        entry = dict(gpu)
        if peak_error:
            entry['peakVramUsedMiB'] = None
            entry['peakVramError'] = peak_error
        elif index in peak:
            entry['peakVramUsedMiB'] = peak[index]
        else:
            entry['peakVramUsedMiB'] = None
            entry['peakVramError'] = 'no sample recorded for this GPU index during the run'
        gpus.append(entry)
    env['gpus'] = gpus
    return env
