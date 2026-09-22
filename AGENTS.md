# AGENTS.md

Guidance for coding agents working in this repository.

## What this repo is

A benchmark that measures which agent harness (pi vs DeepSeek Harness) drives a local
model further through a long-horizon TypeScript implementation task. It is a
**measurement instrument**, not an application. That shapes almost every rule below:
the thing being protected is the interpretability of the numbers, not uptime.

Start with [README.md](README.md), then [MODELS.md](MODELS.md) before running anything.

## Things that will bite you

- **The held-out suite is the oracle.** It ships encrypted as `hidden.tar.gz.enc`.
  Do not read `hidden/` when authoring or fixing a task -- see [CANARY.md](CANARY.md).
  A score from a contaminated model is not interpretable, and the contamination is
  silent.
- **`results/*/score.json` and `results/*/events-summary.json` are published.** Raw
  tool-call arguments are agent-written solution code and must never reach them.
  `scripts/events.py` stores digests for exactly this reason.
- **Raw traces are gitignored but not disposable.** `run.sh` archives them to
  `$OAKEN_ARCHIVE` (default `~/.cache/oaken-bench/<label>/`). Deleting yours means the
  runs can never be re-scored against a new metric -- that is what issue #7 is about.
- **Changing a published field changes what 23 committed results mean.** Those runs'
  traces are gone, so old numbers cannot be recomputed for comparison, only withdrawn.
  Prefer documenting a wrong field over silently redefining it; `EVENTS.md` records
  three such fields today.
- **Do not amend `seed/SPEC.md`, `seed/data/items.json`, or anything in
  `FROZEN.sha256`.** The held-out suite was written blind against them. Editing them
  after the fact risks a desync no test run can detect.

## Layout

`README.md` has the full map. The short version: `seed/` is the task, `hidden*` is the
oracle, `docker/` builds the runner, `scripts/` scores and aggregates, `results/` holds
one directory per run.

`EVENTS.md` documents both harnesses' event schemas, captured live, since neither
harness documents its own. Read it before touching anything under `harnessMetrics`.

## Running things

```bash
scripts/bootstrap.sh                      # one-time: vendor deps, unlock oracle, build image
./run.sh <pi|dsh> <model-id> <label>      # one trial (needs llama-server on 172.17.0.1:8080)
./scripts/score.py results/<label>        # score it
python3 -m pytest scripts/tests -q        # the harness's own tests
```

`scripts/` is Python 3 with no runtime dependencies beyond the stdlib; `pytest` is the
only dev dependency. Keep it that way -- runs execute with the network off.

## Code style

There is no linter. Match what is there:

- Comments and docstrings explain **the trap being avoided**, not the mechanics. See
  `sh()` and `canonical_totals()` in `scripts/score.py` for the house voice.
- Prose in `*.md` is direct and concrete, states limitations up front, and does not
  oversell. `README.md`'s "Before you trust a number from this" is the tone.

## Agent skills

### Issue tracker

GitHub issues on `Vietnoirien/oaken-bench`. **Bare `gh issue view <n>` fails on this
box** (old `gh`, deprecated `projectCards` field); add `--json` or use `gh api`. Lists
and writes are unaffected. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, unchanged; four of them still need creating on the repo. See
`docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root, neither created yet. See
`docs/agents/domain.md`.
