#!/usr/bin/env python3
"""Capture llama-server's actual served configuration, for a run's provenance
record (issue #14).

  ./scripts/server_config.py http://172.17.0.1:8080 [--image oaken-bench:1.0] > run-context.json

Why this exists: `run.meta` recorded `harness=... model=... timeout=...` and
nothing about what actually served the request. MODELS.md documents several
ways two runs with an identical `run.meta` can have been produced under
configurations that give different answers -- `--reasoning-format`, KV
quant, context actually allocated (vs. declared), a silent post-OOM
half-speed fallback (MODELS.md §4.1) -- and none of that reached the
artefacts. This module is the fix: it is a library first (`capture()`),
called by `run.sh` today and, per issue #26's ladder, by #28's direct-mode
runner later, which is why the capture logic lives here rather than inline
in a shell script.

Every sub-capture is independently best-effort and returns an `"error"` key
rather than raising. A provenance sidecar failing must never cost a
30-minute trial: `capture()` always returns a dict, never throws.

## `/props` shape

llama.cpp does not ship an OpenAPI spec, and no `/props` response was
reachable to inspect live in this environment (per this repo's rule: no
live llama-server, test against fakes -- see scripts/tests/test_server_config.py).
The shape below is taken from `tools/server/README.md` in the llama.cpp
source tree (ggml-org/llama.cpp, `master`, fetched 2026-09-24), which is the
project's own documentation of the endpoint:

    {
      "default_generation_settings": {"n_ctx": 1024, "temperature": 0.8, ...},
      "total_slots": 1,
      "model_path": "../models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
      "chat_template": "...",
      "chat_template_caps": {},
      "modalities": {"vision": false},
      "media_marker": "...",
      "build_info": "b(build number)-(build commit hash)",
      "is_sleeping": false
    }

`default_generation_settings.n_ctx` is the context size actually allocated
(MODELS.md §2/§3's "declared vs served" gap); `build_info` is the
llama.cpp build (MODELS.md §4.2's "check llama-server --version before
blaming the model", now captured instead of hand-checked); `model_path` is
whatever path the server was launched with, used below to stat/hash the
model file. `/props` does NOT surface KV cache type, device, or
tensor-split -- those are process flags with no completion-settings
equivalent, which is why the process command line is captured too.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Above this, sha256 of a GGUF is not "cheap" (issue #14: "model file + sha
# if cheap"). Every model measured in MODELS.md is 6-21 GiB; this threshold
# means the hash is skipped for all of them by default and the mechanism
# stays exercised for small test fixtures. Override with
# OAKEN_HASH_MODEL_MAX_BYTES for a one-off where the wait is acceptable.
DEFAULT_HASH_MAX_BYTES = int(os.environ.get('OAKEN_HASH_MODEL_MAX_BYTES', 4 * 1024**3))

# Flags this repo's own launchers (MODELS.md §4, examples/launch-*.sh) have
# needed to distinguish two runs by. Not every llama-server flag -- just the
# ones documented here as changing the answer.
_FLAG_PATTERNS = {
    'ctxSize': re.compile(r'--ctx-size[= ](\S+)'),
    'cacheTypeK': re.compile(r'--cache-type-k[= ](\S+)'),
    'cacheTypeV': re.compile(r'--cache-type-v[= ](\S+)'),
    'device': re.compile(r'--device[= ](\S+)'),
    'tensorSplit': re.compile(r'--tensor-split[= ](\S+)'),
    'parallel': re.compile(r'--parallel[= ](\S+)'),
    'reasoningFormat': re.compile(r'--reasoning-format[= ](\S+)'),
    'batchSize': re.compile(r'--batch-size[= ](\S+)'),
    'ubatchSize': re.compile(r'--ubatch-size[= ](\S+)'),
    'overrideTensor': re.compile(r'-ot[= ](\S+)'),
}


def fetch_props(base_url, timeout=5):
    """GET <base_url>/props. Returns the parsed JSON dict, or a dict with
    only an "error" key -- an old llama.cpp build without this endpoint,
    or no server at all, must not be fatal to capture()."""
    url = base_url.rstrip('/') + '/props'
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        return {'error': f'HTTP {e.code} from {url}'}
    except urllib.error.URLError as e:
        return {'error': f'request to {url} failed: {e}'}
    except Exception as e:  # noqa: BLE001 -- provenance capture must not raise
        return {'error': f'unexpected failure fetching {url}: {e}'}
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        return {'error': f'{url} did not return JSON: {e}'}


def find_server_cmdline(port=None):
    """Best-effort `ps` scan for a running `llama-server` process, for the
    flags `/props` does not surface (KV type, device, tensor-split).

    Only meaningful when this runs on the same host as the server -- true
    for run.sh (bare host) and for #28's planned direct-mode runner, false
    for anything invoked from inside the benchmark's own container (which
    has its own PID namespace and cannot see it). Returns None, not an
    error, when nothing matches: "no llama-server visible from here" is a
    normal outcome, not a failure.
    """
    try:
        out = subprocess.run(['ps', '-eo', 'pid,args'], capture_output=True,
                              text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    candidates = [line for line in out.splitlines() if 'llama-server' in line
                  and 'ps -eo' not in line]
    if port is not None:
        by_port = [line for line in candidates if f'--port {port}' in line
                   or f'--port={port}' in line]
        if by_port:
            candidates = by_port
    if not candidates:
        return None
    # More than one match (two servers, or a probe left running) is reported
    # as-is rather than guessed at -- picking the wrong one silently would
    # be worse than making the caller look.
    return candidates[0].strip() if len(candidates) == 1 else candidates


def parse_launch_flags(cmdline):
    """Pull the flags MODELS.md documents as answer-changing out of a
    `llama-server` command line. Returns {} for None/empty input."""
    if not cmdline or not isinstance(cmdline, str):
        return {}
    return {name: m.group(1) for name, pat in _FLAG_PATTERNS.items()
            if (m := pat.search(cmdline))}


def stat_model_file(path, hash_max_bytes=DEFAULT_HASH_MAX_BYTES):
    """Size/mtime/sha256 for the model file `/props.model_path` names.

    Only resolvable when this process shares a filesystem with the server
    (true on the host that launched it; not true from inside the bench
    container, which never mounts the model directory). A missing or
    unreadable path is reported, not raised -- the model_path string itself
    is still useful provenance even when the file cannot be reached from
    here.
    """
    if not path:
        return {'error': 'no model_path available'}
    try:
        st = os.stat(path)
    except OSError as e:
        return {'path': path, 'error': f'stat failed: {e}'}
    info = {'path': path, 'sizeBytes': st.st_size, 'mtimeEpoch': int(st.st_mtime)}
    if st.st_size > hash_max_bytes:
        info['sha256'] = None
        info['sha256Skipped'] = (
            f'{st.st_size} bytes > {hash_max_bytes} byte cap '
            '(OAKEN_HASH_MODEL_MAX_BYTES) -- hashing a multi-GiB GGUF on '
            'every run is not "cheap"'
        )
        return info
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
    except OSError as e:
        info['sha256'] = None
        info['sha256Error'] = str(e)
        return info
    info['sha256'] = h.hexdigest()
    return info


def host_gpus():
    """`nvidia-smi` inventory of the host's GPUs. Absent nvidia-smi (no GPU,
    or captured from inside a container that does not have it -- this
    image's Dockerfile installs no CUDA tooling) is reported, not raised."""
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=name,memory.total,driver_version',
             '--format=csv,noheader'],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        return {'error': f'nvidia-smi unavailable: {e}'}
    if out.returncode != 0:
        return {'error': f'nvidia-smi exited {out.returncode}: {out.stderr.strip()[:200]}'}
    gpus = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(',')]
        if len(parts) == 3:
            gpus.append({'name': parts[0], 'memoryTotal': parts[1], 'driverVersion': parts[2]})
    return gpus


def image_provenance(image_ref):
    """Docker image id and the two harnesses' actually-installed versions,
    read from the built image rather than from `docker/Dockerfile`'s pins --
    the whole point per issue #14 is that a rebuild can drift from those
    pins silently. `--entrypoint npm` bypasses this image's own ENTRYPOINT
    (entrypoint.sh), which otherwise expects `<harness> <model> <timeout>`.
    """
    result = {'imageRef': image_ref}
    try:
        out = subprocess.run(['docker', 'inspect', '--format={{.Id}}', image_ref],
                              capture_output=True, text=True, timeout=10)
        result['imageId'] = out.stdout.strip() if out.returncode == 0 else None
        if out.returncode != 0:
            result['imageIdError'] = out.stderr.strip()[:300]
    except (OSError, subprocess.SubprocessError) as e:
        result['imageIdError'] = str(e)
    try:
        out = subprocess.run(
            ['docker', 'run', '--rm', '--entrypoint', 'npm', image_ref,
             'ls', '-g', '--depth=0', '--json'],
            capture_output=True, text=True, timeout=30)
        deps = json.loads(out.stdout).get('dependencies', {}) if out.stdout else {}
        result['harnessVersions'] = {
            name: info.get('version') for name, info in deps.items()
            if name in ('@earendil-works/pi-coding-agent', '@deepseek-ai/dsh')
        }
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as e:
        result['harnessVersionsError'] = str(e)
    return result


def capture(base_url, *, image_ref=None, port=None,
            hash_max_bytes=DEFAULT_HASH_MAX_BYTES, include_host_info=True):
    """Assemble the full provenance record. `include_host_info=False` skips
    the process/filesystem/GPU probes that only make sense on the machine
    running the server -- pass it when calling from inside the container
    (a future caller; run.sh today always runs on the host)."""
    ctx = {
        'capturedAtEpoch': int(time.time()),
        'propsUrl': base_url.rstrip('/') + '/props',
    }
    ctx['props'] = fetch_props(base_url)
    if include_host_info:
        cmdline = find_server_cmdline(port=port)
        ctx['processCmdline'] = cmdline
        ctx['launchFlags'] = parse_launch_flags(
            cmdline if isinstance(cmdline, str) else None)
        model_path = (ctx['props'] or {}).get('model_path')
        ctx['modelFile'] = stat_model_file(model_path, hash_max_bytes=hash_max_bytes)
        ctx['hostGpus'] = host_gpus()
    if image_ref:
        ctx['image'] = image_provenance(image_ref)
    return ctx


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    base_url = argv[0]
    image_ref = None
    if '--image' in argv:
        image_ref = argv[argv.index('--image') + 1]
    json.dump(capture(base_url, image_ref=image_ref), sys.stdout, indent=2)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
