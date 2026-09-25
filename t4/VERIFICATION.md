# Issue #44 verification

Verified on 2026-09-25 in the standalone clone
`/tmp/oaken-issue44-independent`, branch `codex/issue-44-independent`.
Integrated main commit `4d5b2a4f2121348ee3afeb884f7e6eb37889dca6`, including
PRs #63, #65, #66, #67 and #68. The shared workspace was not edited.

The frozen spec is commit `2e0fe3d750a3c0b91bc2817440046f8ffc0eb8a2`.
Both `sha256sum -c FROZEN.sha256` and `sha256sum -c t4/FROZEN.sha256` pass.
`scripts/hidden.sh verify t4oracle` confirms the seal. Its 137 tests collect
across four files; the fifth sealed file is their support module.

The Docker image was `oaken-bench:1.0`, image ID
`sha256:1f95bcf5e8eae76f5c28b00017a9a4f813fbeadf807f97b799707fb1d48b8b42`.
No GPU, model server, or request to ports 8080/8081 was used.

| Run | v1.0 | v1.1 | Typecheck | Frozen drift |
| --- | --- | --- | --- | --- |
| `t4-reference-sanity` | 132/132 | 2/137 | clean | none |
| `t4-pi-offline-smoke` | 132/132 | 2/137 | clean | none |

Both reports have zero uncollected tests. The counts are never summed.
The baseline used `python3 scripts/t4.py baseline results/t4-reference-sanity`.
The full runner check used:

```bash
OAKEN_T4_OFFLINE_SMOKE=1 OAKEN_SERVER_PORT=49199 \
  OAKEN_ARCHIVE=/tmp/oaken44-review/archive OAKEN_IMAGE=oaken-bench:1.0 \
  ./run.sh pi gemma-4-12B-it-qat-UD-Q4_K_XL.gguf t4-pi-offline-smoke 5

python3 scripts/score.py \
  /tmp/oaken44-review/archive/t4-pi-offline-smoke --no-detail
```

Pi made zero tool calls. Its five-second timeout returned 124 through the
entrypoint and `run.sh`; `exit.code` also contains 124. The existing outer
progress loop made wall time 30 seconds. The archived workspace matches the
result workspace byte for byte. Its file list and source bytes match the
assembled reference, both public specs match the frozen files, and the
baseline Git diff is empty. No oracle or private-draft paths are present.
Raw traces remain in the clone's ignored result directory and at
`/tmp/oaken44-review/archive/t4-pi-offline-smoke`.

A separate network-disabled entrypoint check with an invalid endpoint URL
returned 78 before Pi started, confirming #63's fail-closed behavior.
The real runner check above confirms #65's exit propagation. The tier registry
contains T2, T3, T4 and T5. Summary golden tests preserve the existing rows and
add only the two T4 records. #68's T5 behavior metrics were retained unchanged.

Harness tests:

```bash
PYTHONPATH=/tmp/issue42-pydeps python3 -m pytest scripts/tests -q
# 437 passed, 7 skipped
```

The host lacks pytest; the command used the existing temporary pytest install.
The local fake HTTP server tests needed sandbox permission to bind sockets.
No test source or per-test oracle diagnostics were returned from the v1.0 run.

These are negative-control and runner checks. No positive v1.1 implementation
or live agent completion was evaluated. The supervisor checked the distinct
oracle-agent ID against the spec's final author and recorded the shared desktop
task-ID limitation in `CANARY.md`.
