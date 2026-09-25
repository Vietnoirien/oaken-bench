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
