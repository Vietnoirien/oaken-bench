# T4 spec handoff

This directory completes the spec deliverable for [issue #43](https://github.com/Vietnoirien/oaken-bench/issues/43).
The frozen spec contains no implementation. [Issue #44](https://github.com/Vietnoirien/oaken-bench/issues/44)
adds the independently authored sealed oracle, workspace assembly and separate
version scoring described below.

The starting commit is `56a5820`, on `issue-37-refengine`. The three draft files
were recovered from the stopped Claude worktree `agent-a866a1f49ac4d5151`.
Both JSON files retain the recovered bytes. Codex completed the spec, clarified
its interfaces and input limits, checked its public examples, and froze it on
2026-09-25. Authorship and the ban on reusing either spec author for the oracle
are recorded in the frozen spec, section 14. The recovered draft did not record
its author's model identity or reading history; those cannot be independently
verified from the files.

| File | Purpose |
|---|---|
| `SPEC-v1.1.md` | Additive rules, exports, errors, input limits and worked examples |
| `data/items-v1.1.json` | One new item, with four rarity tiers |
| `data/encounters-v1.1.json` | Five encounters, including rewards for all four perks |
| `FROZEN.sha256` | Pins the three normative files above |
| `validate.py` | Checks public data, example arithmetic and both freeze manifests |

From the repository root, run:

```bash
python3 t4/validate.py
sha256sum -c t4/FROZEN.sha256
sha256sum -c FROZEN.sha256
```

The validator uses only Python's standard library. It checks the JSON schema,
unique ids, legal encounter slots and rewards, the absence of shock in encounter
towers, all eight schedule-count rows, and the seeded crit example. It also
checks the arithmetic for the public combat, perk and reward examples. These
are author-side consistency checks of published examples. They do not execute
an engine, test its mutation or error behavior, or constitute the T4 oracle.
No GPU, network access, reference-engine decryption or held-out suite is needed.

For the later T4 workspace, keep v1.0 as `SPEC.md`, add this document as
`SPEC-v1.1.md`, and put both new JSON files beside `data/items.json`. The starting
implementation comes from the reference engine. The repository's `seed/` files
and root `FROZEN.sha256` stay unchanged. Workspace assembly and scoring from issue #44 report the v1.0 and v1.1
results separately.

The new item is excluded from shop rolls to preserve the v1.0 RNG sequence.
Epic and Legendary multicast overlap the next primary trigger while retaining
the v1.0 cooldown floor. New assertions involving combat exclude shock on both
sides because skipped primaries and multicast are underspecified in v1.0.
Other inherited gaps and the exact test-input limits are in section 11.

The freeze precedes handoff to the independent oracle author. Do not regenerate
the manifest to accommodate edits after that handoff. Record later ambiguities
in a separate note and decide explicitly whether a new spec version is needed.

## Independent oracle and T4 execution

Issue #44 adds the named `t4oracle` bundle, 137 tests, authored after freeze.
See [CANARY.md](../CANARY.md#6-independent-t4-v11-oracle-issue-44) for the
separate author record, task-ID provenance limitation, and canary digest.
Neither frozen manifest changed.

The suite covers the new item throughout placement, merging, selling and
snapshots; schedule boundaries and dropped-trigger RNG consumption; perks,
including errors, order and duplication; encounter outcomes and rewards; and
run-loop integration. Assertions follow section 11's input limits. No v1.0
held-out source or reference implementation was consulted to write them.

With Docker and the pinned `oaken-bench:1.0` image available:

```bash
# CPU-only assembly and scoring, with no model endpoint.
python3 scripts/t4.py baseline results/t4-reference-sanity-new

# Assembly only, for a separately managed implementation session.
# The destination must not exist. Treat its reference source as contaminated.
python3 scripts/t4.py assemble /tmp/my-t4-input

# A live implementation trial, when a model server is intentionally available.
./run.sh pi MODEL_ID t4-LABEL
python3 scripts/score.py results/t4-LABEL --no-detail
python3 scripts/summarize.py
```

`OAKEN_IMAGE` overrides the runner image. Bundle passphrase overrides are
`OAKEN_REFENGINE_PASS`, `OAKEN_HIDDEN_PASS` and `OAKEN_T4ORACLE_PASS`.
Assembly receives the reference bundle and frozen public inputs only.
It writes `workspace.tgz`, containing `work/` with the reference modules,
`SPEC.md`, `SPEC-v1.1.md`, and the three data files. `run.sh` mounts this
workspace and the T4 prompt for the harness. Neither oracle is mounted in
the agent's container. T3's instance setup and T5's registry remain separate.

The scorer mounts the two encrypted oracles only for evaluation. It restores
candidate `src/` into fresh workspaces with trusted tests, config, data and
pinned dependencies. It records original frozen-file drift before restoring
those inputs. Each suite runs separately. Missing imports, startup failures
and skipped tests cannot shrink its fixed denominator. `uncollected` reports
missing assertions; `failed` includes every test that did not pass.

The score has `suites["v1.0"]` and `suites["v1.1"]`. There is no combined
pass count, denominator or rate. The T4 summary prints the two counts side by
side through the tier registry's summary callbacks. Existing T2/T3 scores
and their summary fields retain their meanings. T4 accepts the shared
`--detail` option for dispatch compatibility but always emits counts only.

[The reference sanity score](../results/t4-reference-sanity/score.json) records
132/132 v1.0 and 2/137 v1.1, with a clean typecheck and no frozen-file drift.
It ran through the tier namespace on the unchanged reference engine. This is
an assembly/scoring smoke run, not a live agent implementation trial. No GPU
or model endpoint was used. That smoke run did not validate a positive v1.1
implementation. The first live pilot is recorded below.

### First live model pilot, 2026-09-26

The exploratory Pi run [t4-qwen35moe-desktop-pi-20260926-01](../results/t4-qwen35moe-desktop-pi-20260926-01/score.json)
scored 132/132 on v1.0 and 136/137 on v1.1. Typecheck passed, all 269
assertions were collected, and no frozen inputs drifted. The unchanged
reference engine scored 132/132 and 2/137 through this T4 scorer, so this
run confirms that the model implemented most of the v1.1 extension without
regressing on the earlier suite. One run does not establish reliability or
separate model variance from harness variance.

The model was `Qwen3.6-35B-A3B-UD-Q4_K_S.gguf` under Pi 0.86.0. The guarded
`qwen35moe-desktop` preset served it with llama.cpp b10751 on an RTX 5070 and
RTX 3060, 131072 context, q8_0 K/V, one slot, and expert blocks 14-39 on the
3060. Its command used `--jinja --gpu-layers 99 --ctx-size 131072`,
`--cache-type-k q8_0 --cache-type-v q8_0 --parallel 1 --device CUDA0,CUDA1`,
`--tensor-split 1,0 -ot 'blk\.(1[4-9]|[23][0-9])\.ffn_.*_exps\.=CUDA1'`,
`--batch-size 512 --ubatch-size 256 --cont-batching --no-context-shift`,
and `--host 172.17.0.1 --port 8080`. Its smoke
test returned a well-formed tool call at 96.5 tokens/s with 488/322 MiB free.
The trial had a 3600-second cap and finished in 391 seconds with container
exit 0, 39 turns, and 56 tool calls. The image ID was
`sha256:db70dc755e2d68747eabb681ebcd89fd1d46ecc443f114b44cb6698277e874f6`.
The run context identifies the model file by path, size (20,893,015,008
bytes), and mtime; its hash was skipped by the configured 4 GiB cap.

The score identifies the sealed v1.0 suite as
`fff6d7c5e9bba656c4b2f23499d1d96f89d9d3f6f9b1f4759c4873d3f8a00a97`,
the sealed T4 oracle as
`7f8fbed573482c11342de45ed06a3762e08aacff655fff504280609b5a0a3d8b`,
and the frozen v1.1 spec commit as
`2e0fe3d750a3c0b91bc2817440046f8ffc0eb8a2`. The model received no
repository context for separate canary prompts. Neither suite's canary digest
matched its response. The v1.0 check needed a short-answer retry after an
unqualified response hit its token limit; the retry completed. The raw trace,
workspace archive, and full run context are preserved under
`~/.cache/oaken-bench/t4-qwen35moe-desktop-pi-20260926-01/`. They are not
published because the trace contains agent-written code. The archived
`workspace.tgz` has SHA-256
`e93a2c192a8487efe7108710705fc8a611ea5b1c33befe8e8f59ff9a77755545`.

The new Python machinery has public fixture tests in `scripts/tests/test_t4.py`.
They check tier dispatch, separate version counts, fixed denominators, archive
path rejection, frozen-input checks, candidate-test/config exclusion, offline
Docker arguments and cleanup. Those fixtures contain no held-out cases.

### Offline runner handoff check

To exercise `run.sh` and the actual Pi startup without a model or GPU:

```bash
OAKEN_T4_OFFLINE_SMOKE=1 OAKEN_SERVER_PORT=49199 \
  OAKEN_ARCHIVE=/tmp/t4-smoke-archive \
  ./run.sh pi gemma-4-12B-it-qat-UD-Q4_K_XL.gguf t4-pi-smoke-new 5
# A timeout/failure is expected. Score the archived workspace afterwards.
python3 scripts/score.py /tmp/t4-smoke-archive/t4-pi-smoke-new --no-detail
```

This opt-in mode requires T4, Pi, an explicit isolated port, and no URL
override. It disables container networking and skips readiness, inference and
host-GPU provenance probes. The normal endpoint validation and fail-closed
configuration step still run. Ordinary trials retain their existing preflight.
The archive records `runKind=offline-smoke`, and its score carries that marker.

The review run `t4-pi-offline-smoke` used port 49199 and the command above.
Pi made no tool calls, reached the five-second limit, and returned 124 through
both the container entrypoint and `run.sh`. The outer progress loop made the
wall time 30 seconds. Its archived source and file list matched the assembled
reference workspace byte for byte. Both public specs matched their frozen
inputs. No oracle or private-draft paths appeared, and `diff.stat` was empty.
The archived workspace was then scored through `scripts/score.py`.
