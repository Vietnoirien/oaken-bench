# T4 spec handoff

This directory completes the spec deliverable for [issue #43](https://github.com/Vietnoirien/oaken-bench/issues/43).
It contains no T4 implementation or held-out tests. Compatibility with the reference
engine has not been tested here. [Issue #44](https://github.com/Vietnoirien/oaken-bench/issues/44)
assigns the sealed oracle and end-to-end T4 run to a different author.

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
and root `FROZEN.sha256` stay unchanged. Workspace assembly and scoring belong
to issue #44, which reports the v1.0 and v1.1 results separately.

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
or model endpoint was used. A positive v1.1 implementation has not been
validated against this new suite; that remains a separate calibration step.

The new Python machinery has public fixture tests in `scripts/tests/test_t4.py`.
They check tier dispatch, separate version counts, fixed denominators, archive
path rejection, frozen-input checks, candidate-test/config exclusion, offline
Docker arguments and cleanup. Those fixtures contain no held-out cases.
