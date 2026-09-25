# Local-model benchmark: tiered probes, with pi vs dsh at T2

This repo measures local models at separate tasks and probes. Each tier answers
a different question and keeps its own results. The original pi vs DeepSeek
Harness comparison is T2.

## Tiers

T0 and T0.5 are direct model probes. T1 is deferred. T3 has a generator and
runner, and T4 has a sealed v1.1 oracle and runner; neither has a model
measurement. T5 has a sealed snapshot oracle and one CPU baseline measurement.
The 53 earlier `results/*` score files are T2 runs; their meaning and values
are unchanged.

| Tier | What it isolates | Status and command |
|---|---|---|
| T0 | Tool-call reliability over short chains, including schema use, tool choice, refusals, and recovery. Direct model call, no harness. | Shipped. `./scripts/toolbattery.py --model your-model.gguf --base-url http://172.17.0.1:8082/v1` |
| T0.5 | Long-context recall and abstention when a fact is absent. Direct model call, no harness. | Shipped. `./scripts/recall.py --model your-model.gguf --base-url http://172.17.0.1:8082/v1` |
| T1 | Isolated single-module implementation, to locate where the Gemma plateau comes from. | Deferred. Three new archived 192k runs varied with work order and early exits; see [the 192k retest](MODELS.md#192k-with-less-free-5070-memory-2026-09-25). No command. |
| T2 | Long-horizon greenfield implementation, comparing pi with DeepSeek Harness on the same task. | Shipped and measured. `./bench.sh <pi|dsh> <model-id> <label>` |
| T3 | Find and fix planted bugs against the reference engine. | Generator and runner shipped; no model measurements. See [T3 setup](docs/T3.md). |
| T4 | Extend the existing engine and count regressions against the earlier suite. | Shipped. `./run.sh pi <model-id> t4-<label>` then `python3 scripts/score.py results/t4-<label>`; see [T4 setup](t4/README.md). Only reference and offline runner checks exist, with no model measurement. |
| T5 | Write a bot that plays the game against fixed held-out baseline snapshots. | Shipped. `python3 scripts/t5_oracle.py run --bot baseline:cheapest --label cheapest-01`; see [T5 oracle](t5/oracle/README.md). |

The short screening command combines T0 and T0.5. Its six thresholds and
under-15-minute runtime target are still uncalibrated; see [Short T0 + T0.5
screen](#short-t0--t05-screen). The harness-effect comparison for T0.5 is
implemented, but no live model or harness comparison has been run; see
[Comparing the T0.5 harness effect](#comparing-the-t05-harness-effect).

The tiers are reported separately. A score from one tier does not combine with
or stand in for a score from another.

## Where it stands

| | hidden | |
|---|---|---|
| Claude ceiling probe | 129/132 (97.7 %) | not harness-mediated |
| **Qwen3.6-35B-A3B UD-Q4_K_S, RTX 5070 + 3060** | **pi 94.7-97.0 %, dsh 93.9-99.2 %** (n=3 each) | **six of six clear the 80 % bar** |
| Qwen3.6-35B-A3B, same, at 262k with q4_0 KV | pi 79.5-93.9 %, dsh 0-93.2 % (n=3 each) | four of six; the window went unused, the KV precision cost ~9 points |
| gpt-oss-20b MXFP4, RTX 5070 + 3060 | pi 0-81.8 %, dsh 0 % (n=3 each) | one of six clears the bar |
| GLM-4.7-Flash UD-Q4_K_XL, RTX 5070 + 3060 | pi 0-54.5 %, dsh 0.8-65.1 % (n=3 each) | none of six |
| Gemma 4 12B, RTX 5070 | 0-83.3 % across 14 runs, not n=3 per harness | one run clears the bar |

The task is solvable by a local model on consumer hardware, and reliably so:
Qwen3.6-35B-A3B split across two 12 GB cards, on a llama.cpp build after b9754.
On the older b9716 the same configuration scored 0 % on pi twice, from a
llama.cpp parser bug, not the model. See [FINAL-REPORT.md](FINAL-REPORT.md) §3.6.

The Qwen result is the model's, not the setup's: gpt-oss-20b and GLM-4.7-Flash
ran the same protocol on the same cards, build and configs, and managed one pass
in twelve runs between them. GLM is slow at depth (26.6 t/s decode at 99k) and
ran into the cap five times in six; see FINAL-REPORT §3.7-3.8.

**Which harness is better is still unanswered.** On Qwen and GLM the two are
indistinguishable at n=3; on Gemma the spread swamped the difference. On
gpt-oss the harness decided everything: dsh died in under 20 s on all three
runs, on a malformed tool-call header llama-server could not parse, and pi never
hit it (FINAL-REPORT §4.5). pi also finished the Qwen task faster, a median 529 s
against dsh's 1228 s.

## Under test

- **Original head-to-head:** Gemma 4 12B QAT Q4_K_XL + MTP — 93.7 t/s, 131k ctx,
  native sampling, one RTX 5070.
- **Two-card run:** Qwen 3.6 35B-A3B UD-Q4_K_S — ~99 t/s, 131k ctx, q8_0 KV,
  RTX 5070 + RTX 3060. Planned as a one-run confirmatory at 15.3 t/s on the winning
  harness; with no winner and ~6x the budgeted speed, it ran n=3 on both. Not
  comparable with any single-card run (limitation 3 below).
- **Two-card comparisons:** gpt-oss-20b MXFP4 (119 t/s, 131k ctx) and
  GLM-4.7-Flash UD-Q4_K_XL (~92 t/s short, 26.6 t/s at 99k filled), n=3 per
  harness each, on llama.cpp b10751 through `examples/launch-dual.sh`. gpt-oss was
  originally excluded for a "32k context cap", which was wrong: its GGUF declares
  131072 (YaRN from 4096). What was true is that its 12 GiB of weights leave no
  room for context on one 12 GB card.

## Run matrix (9 runs, as originally planned)

| Phase | Runs | Purpose |
|---|---|---|
| Ceiling probe | 2 — Claude via pi, Claude via dsh | Viability gate. **Run first.** |
| Head-to-head | 6 — 3 pi + 3 dsh, Gemma | The measurement |
| Confirmatory | 1 — Qwen on the winner | Is 6x slower worth it |

If the ceiling probe cannot clear the 80% bar within 60 minutes, the spec is
ambiguous or the slice is oversized — cut scope **before** spending Gemma runs.

What actually ran departs from this in two places. The Gemma head-to-head grew
to 14 runs across two context arms (FINAL-REPORT §3.5). The confirmatory became
10 Qwen runs: four on llama.cpp b9716 (two pi, both aborted; two dsh), then n=3
per harness on b10751 once the pi aborts were traced to a parser bug fixed upstream. The
b9716 runs stay in `results/` and `summarize.py` keeps the builds apart.

## The task

Headless TypeScript implementation of an async PvP auto-battler engine:
deterministic frame-based combat, a 20-item pool over 4 rarities with two tag
synergies, tower slot placement with triple merging, the day-loop economy, and a
server-authoritative snapshot store. Spec: `seed/SPEC.md`. No UI, no network,
no new dependencies.

## Protocol

- **Sealed.** One prompt, no human input, no clarifications.
- **30-minute wall clock**, recorded as its own outcome (cut from 60 after the
  ceiling probe finished in 5.4 minutes; see Results). The `*-gemma131k-01`/
  `-04` pair added later ran with a 60-minute cap and did not reach it (700 s
  and 196 s), so the cap is not what separated them — but they are not
  protocol-identical to the original six and should not be pooled with them.
- **Fresh container per run**, fresh copy of `seed/`.
- **Pre-declared interfaces** in `seed/src/*.ts` so the held-out suite can bind.
- **3 runs per harness** at native sampling — variance is the point, so not temp 0.

## Scoring

- **Primary:** held-out pass rate. Success bar **>= 80%**.
- **Secondary:** visible-minus-hidden gap — the overfitting signal.
- **Per run:** tool calls, turns/steps, token and cache counts, compaction
  events, wall clock, and an outcome from
  `{timeout, crash, declared_done_tests_red, context_exhausted, complete}`.

  **Not comparable across the fix for issues #9/#10/#11.** `harnessMetrics`
  carries a `harnessMetricsVersion`, 2 from here on. The 16 runs committed
  before the fix carry no such key at all -- *absence is version 1*, and any
  reader of these files has to treat it that way. Under version 1, `usage`
  was summed from streaming partials and `compactions` double- (pi)
  or up to 5.5x- (dsh) counted — both overstated, not by a fixed ratio. The 16
  runs committed under version 1 keep their original figures (their raw traces
  are gone, so the old numbers cannot be recomputed) but those figures are
  known wrong and must not be compared against runs scored after the fix. See
  [EVENTS.md](EVENTS.md) §1-2 for the measured before/after deltas.

## Fairness

- **Bash timeout equalised.** dsh ships `timeoutMs: 60000`; pi's bash has no
  default at all. Raised to dsh's documented maximum (600s) via a profile patch
  so neither binds. See `docker/config/dsh/profiles/headless/cordis.patch.yml`.
- **Stream idle timeout raised to 900s on both.** Both shipped 300s defaults,
  which a large prefill on this hardware can exceed — it would have killed runs
  mid-experiment for reasons unrelated to the harnesses.
- **Compaction left as shipped.** pi at `ctx - 16384` (~114.7k) keeping 20k;
  dsh at `0.8 * ctx` (~104.9k) keeping ~21% plus its model-free tool-result
  pruner. This difference *is* the product difference being measured.
- `DSH_PERMISSION_MODE` stays at its default; the workspace fence permits
  everything the task needs.

## Blinding

The spec, interfaces and visible tests were written by Claude (Opus). The
**held-out suite was written by a separate Sonnet agent from the frozen spec
alone**, and the author of the spec has not read it. `FROZEN.sha256` pins every
input artefact.

Residual, not designed away: the spec's author is also the ceiling probe. Treat
the probe's number as a ceiling, not a baseline.

## Usage

```bash
# 0. One-time: vendor dependencies, unlock the held-out suite, build the image.
scripts/bootstrap.sh

# 1. Register your model in BOTH harness configs, then find its real context
#    ceiling. A model that loads is not a model that works — see MODELS.md.
scripts/ctxprobe.sh /path/to/model.gguf q4_0  65536 131072 163840

# 2. Start llama-server on the docker bridge (NOT 127.0.0.1 — containers
#    cannot reach it). Flags that matter are in MODELS.md §4.
llama-server --model /path/to/model.gguf --alias your-model.gguf \
  --parallel 1 --ctx-size 131072 --host 172.17.0.1 --port 8080 ...

# 3. Run a trial
./run.sh <pi|dsh> <model-id> <label> [timeout-seconds]
# Raw traces are archived to ~/.cache/oaken-bench/<label>/ after the run.
# Override the location with OAKEN_ARCHIVE.
# Progress is printed every 30s. Pi turn/call counts are read from its live
# JSONL trace; dsh only exposes stdout until its session archive is finalized,
# so its live turn/call counts are marked n/a.
# The startup decode probe is recorded in run-context.json. Keep the default
# port 8080, or set OAKEN_SERVER_PORT=8081 / OAKEN_SERVER_URL=http://172.17.0.1:8081/v1.

# 4. Score it
./scripts/score.py results/<label>

# 3+4 in one step, so scoring isn't a step you can forget:
./bench.sh <pi|dsh> <model-id> <label> [timeout-seconds]
```

**Read [MODELS.md](MODELS.md) before your first run.** It covers registering a
model, the compaction arithmetic that silently breaks small context windows, the
`llama-server` flags that decide whether the thing works at all, and the harness
failure modes that look like model failures and are not.

**Read [CANARY.md](CANARY.md) before publishing any score.** The held-out suite
ships encrypted and carries a canary GUID; a score from a contaminated model is
not interpretable.

**Read [EVENTS.md](EVENTS.md) before changing anything in `harnessMetrics`.** It
records the pi and dsh event schemas from a live capture, since neither harness
documents them, and it names two fields (`usage`, `compactions`) whose values
in `score.json` files committed before `harnessMetricsVersion` 2 were derived
incorrectly and are not comparable with later runs (see Scoring above).

## Screening a model before spending GPU-hours on it

The main task above is expensive (README's own arithmetic: ~4.5 GPU-hours for
nine runs) and confounded for screening purposes -- a 0/132 hidden score can
mean the model cannot call tools, cannot hold the spec in context, cannot
write TypeScript, or just ran out of clock, and the harness's own
retry/compaction logic sits between the model and the failure. `scripts/toolbattery.py`
is a minutes-per-model battery that talks to the model's OpenAI-compatible
endpoint DIRECTLY (no pi, no dsh) and scores six tool-calling dimensions
independently, so a bad result says *what* broke instead of just *that*
something did:

```bash
./scripts/toolbattery.py --model your-model.gguf \
  --base-url http://172.17.0.1:8080/v1     # your llama-server, same as the main task
# writes toolbattery-results/toolbattery-<model>-<timestamp>.json
```

- **schemaAdherence** -- required params present, types correct, no invented params
- **toolSelection** -- picks the right tool out of a set with plausible decoys
- **multiStepDependency** -- call 2 must use the value call 1's (simulated) result returned, not an invented one
- **errorRecovery** -- call 1 is made to fail; does call 2 repeat it verbatim, or adapt? Reuses
  `events.py`'s own repeated-call digest (`call_metrics()`'s `longestRepeatRun`), so "repeated
  verbatim" means exactly what it means in a `score.json`, not a second, parallel definition.
  **Sensitive to `--max-tokens`**: at a tight budget, Gemma 4 12B's inline `<|channel>thought`
  reasoning (MODELS.md §4) can eat the whole completion before a tool call is even emitted --
  scored as a failure to probe (not a failure to recover), and the case record says so. Give this
  battery at least ~700 output tokens on a model that reasons inline, or its measurements will be
  bound by the token budget rather than by tool-calling capability -- the exact failure mode this
  battery exists to distinguish, one more time.
- **refusal** -- no supplied tool applies; does the model call one anyway (the false-positive direction)
- **shortChains** (issue #30) -- four fictional workflows of 3-5 dependent calls each (ship-and-track,
  open-a-ticket, register-a-device, submit-an-expense); call N+1 needs a value only call N's simulated
  result carries. Every threaded value is a 12-hex-digit id/token built by `_seeded_id()` from a fixed
  seed -- deterministic run to run, but not a small guessable example like multiStepDependency's
  `cus_48291`, so a correct downstream call is evidence the model actually carried the result forward.
  Scored as **depth reached**, not only pass/fail: each case records `depthReached`/`chainLength` and,
  when it broke, `brokenAtStep` plus the `classify_call()` level (below) the breaking call actually
  reached -- `schemaValid` for a decoy taken, `notWellFormed` for truncated JSON, and so on. A model
  that skips straight to a later step's tool, guessing at a value it was never handed, is judged
  against the step it's ACTUALLY on and caps at `schemaValid` at best, so it earns zero depth rather
  than credit for a lucky-looking guess; a turn with more than one tool call only counts its first call
  toward the chain; any others are recorded but never advance or break it. See
  `run_short_chains()`'s and `CHAIN_SCENARIOS`'s docstrings for the full reasoning.

A case that never exercised its dimension is reported as **not attempted** and left out of that
dimension's denominator -- it is not scored as a pass. This matters more than it sounds: a model
that answers in text instead of retrying has not recovered from anything, and counting that as a
pass had Gemma 4 12B reporting `errorRecovery 2/2 (100%)` on a run where neither case recovered.
It now reports `0/1 [1 not attempted]`. A dimension with nothing attempted scores `null`, not
`0.0` -- no evidence is not the same fact as failed everything, the same distinction
`harnessMetrics.parseErrors` and the `mut ?` column make elsewhere.

The prompts and tool schemas are fresh and generic (a fictional weather/calendar/customer-support
assistant) -- **not** drawn from `SPEC.md`, `seed/`, or the held-out suite, per CANARY.md. They
are, however, a new contaminable asset in their own right, committed in plaintext with none of the
held-out suite's protections; see [CANARY.md §3](CANARY.md#3-scriptstoolbatterypys-probes-are-a-new-contaminable-asset)
for why, and for the recommendation on how much confidence to put in a score from it.

**Per-call classification and pseudo-tool-calls (issue #29, `schemaVersion` 2).** A pass/fail bit
per case cannot say WHERE a call went wrong -- a model that emits `<tool_call>` XML instead of a
structured call, one that calls the right tool with a truncated argument string, and one that
calls the wrong tool outright all used to land on the same `passed: false`. Every call each case
produces is now additionally classified into four CUMULATIVE levels (a call can only reach level N
having cleared every level below it): **well-formed** (`arguments` decoded as JSON) ->
**schema-valid** (satisfies its own tool's schema) -> **right tool** (matches the tool the case
expected) -> **right args** (dimension-specific: for `multiStepDependency`, the value the simulated
first result returned; for `errorRecovery`'s retry, adapted rather than repeated verbatim; for
`schemaAdherence`/`toolSelection`, no further check beyond the schema itself). See
`classify_call()`'s docstring in `scripts/toolbattery.py` for the exact per-dimension definitions.
`report['callClassification']` folds every call from every dimension into one table, `byTool`
included -- generalising #15's `schemaAdherence.byTool` past a single dimension, so one
catastrophic tool used in several places is visible even if no single dimension's own numbers show
it. `detect_pseudo_tool_calls()` separately scans each case's free-text answer for a tool call
written as TEXT instead of landing in the structured `tool_calls` array -- JSON objects naming a
known tool, `<tool_call>...</tool_call>`, a `<|tool_call|>` sentinel or `<function=...>` XML,
Mistral's `[TOOL_CALLS]` marker, gpt-oss's Harmony `to=functions.x` leak, and fenced code blocks --
reported per case and rolled up in `report['pseudoToolCalls']`.

**v1 vs v2.** The four artefacts already committed under `toolbattery-results/` predate this change
(`schemaVersion` 1) and were **not rescored** -- they carry per-dimension pass/fail and (for the
GLM/gpt-oss/Qwen3.6 runs, after #15) `schemaAdherence.byTool`, but no per-call classification and no
pseudo-tool-call detection. Per AGENTS.md's rule on published fields, that is documented here rather
than silently redefined: read a `schemaVersion: 1` artefact as "passed the v1 battery", a
`schemaVersion: 2` one as "passed the v1 battery AND has per-call classification and
pseudo-tool-call counts".

The artefact is a JSON file with a `schemaVersion`, one block per dimension, and a `cases` list per
block -- shaped after `events-summary.json`'s conventions, not embedded in `results/*/score.json`
(this script never touches `results/`). It stores the actual tool-call arguments the model produced,
not just digests: unlike `edit`/`write` arguments in a real harness trace, these are short generic
values answering fixed public prompts, not agent-written solution code against a held-out spec, so
there is no CANARY.md-style asset at risk in keeping them legible.

## Recall at context depth, and abstention

`ctxprobe.sh` finds the context size that actually *loads*. It says nothing about
whether the model can find anything inside it once loaded, or whether it knows the
difference between "found" and "not there". `scripts/recall.py` (issue #31's recall pass,
issue #32's abstention pass, together T0.5 of the ladder in #26) is the direct-mode
battery for both: `scripts/haystack.py` builds a seeded, fictional "operations ledger"
document at each of a few context depths and plants a handful of facts in it at
controlled positions; `scripts/abstain.py` builds a handful of guaranteed-absent
`(entity, attribute)` pairs against that SAME haystack (an entity that never appears; an
entity that appears with a DIFFERENT attribute; an attribute that appears on a DIFFERENT
entity). One tool call per depth asks about both kinds of pair together, sharing the
haystack and the cold-prefill cost between the two probes.

```bash
./scripts/recall.py --model your-model.gguf \
  --base-url http://172.17.0.1:8080/v1     # your llama-server, same as the main task
# writes recall-results/recall-<model>-<timestamp>.json
```

- **Fictional facts only.** Entities, attribute names and values are all invented, seeded
  from `--seed` (default fixed, override for a fresh set) -- a model cannot answer from
  training-time exposure to `SPEC.md` or anything else in this repo, only from what is
  actually in its context window.
- **Depths stop at the served context.** `--depths` defaults to `4096,16384,32768,65536,131072`
  (accepts a `k` suffix: `4k,16k,...`); any depth that would not fit inside the server's own
  `/props`-reported `n_ctx` (minus headroom for the question and the response) is skipped, not
  attempted, and the skip and its reason are in the artefact.
- **Token sizing** uses the server's `/tokenize` endpoint when available (llama.cpp has it) to
  size each haystack to its target depth; falls back to a documented ~4 chars/token estimate
  otherwise. Which one was used is recorded per depth (`haystack.tokenCountSource`).
- **Prefill/decode tok/s per depth**, each labelled with its source. llama.cpp's own `timings`
  object is used when the response carries one; wall-clock is the fallback, per metric. Getting
  an honest wall-clock split at all means deliberately exploiting llama.cpp's prompt cache within
  a depth (`measure_depth()`'s two-call pair) while deliberately avoiding it across depths (every
  depth gets its own, differently-seeded haystack) -- see `recall.py`'s module docstring, "the
  cache trap", before touching that code.
- **Abstention, five-way classified.** For every planted fact, an answer is `correct_answer`,
  `wrong_answer`, or `false_abstention` (the model claimed the fact was absent when it wasn't --
  over-abstaining, made visible so a model that always says "not in context" cannot score a
  clean recall failure indistinguishable from genuinely not finding anything). For every
  guaranteed-absent pair, an answer is `correct_abstention` or `invented_answer`. The model is
  told in the system prompt to answer exactly `"not in context"` when a pair is absent, but
  scoring accepts a documented, unit-tested list of paraphrases leniently -- see `abstain.py`'s
  module docstring for the accepted phrasings and why each is (or is deliberately not) on the
  list. Counts are reported per depth (`depths[i].presentQuestionCounts` /
  `depths[i].abstention.counts`, the latter also broken down `byKind`) and rolled up once more
  into `overall.presentQuestionCounts` / `overall.abstentionCounts`.

Same plaintext-probe caveat as `toolbattery.py`, with one difference worth knowing: unlike
`toolbattery.py`'s fixed prompts and answers, `recall.py`'s planted facts (and abstain.py's
absent pairs) are regenerated fresh every seed, so a leaked run's answers do not transfer to a
different seed's. The probe SHAPE (question template, tool schema, `haystack.py`'s fixed
vocabulary) is still constant across runs, the same lower-but-nonzero contamination risk
`toolbattery.py` carries. See
[CANARY.md §3b](CANARY.md#3b-scriptsrecallpy-scriptshaystackpy-and-scriptsabstainpy-the-same-asset-one-difference).

### Comparing the T0.5 harness effect

`scripts/harness_effect.py` runs the same seeded ledger and present/absent questions through
direct mode, pi, and dsh, then reports per-mode recall and abstention counts plus the
difference from direct mode. Direct mode receives the ledger inline. pi and dsh run inside
the existing `oaken-bench` container, with the ledger mounted as `/work/ledger.txt`; their
prompt tells them to read that file. Each item has a stable SHA-256 ID made from its pair and
depth. The artefact records each mode's status and classification per ID, plus per-depth
present-recall and absent-abstention deltas. A delta is the harness score minus direct mode,
in percentage points, over IDs both modes scored. It is null if either mode failed or returned
unparseable output. Aggregate deltas also use only shared scored IDs. The artefact contains
counts, statuses, elapsed time, and digests, never the pairs, expected values, or model answers.

Every run requires an explicit `--base-url`; port 8080 is rejected. The pi and dsh adapters
create temporary configs that point to this URL rather than using the checked-in endpoint.
For example, with a separate model server reachable from the container:

```bash
./scripts/harness_effect.py --model your-model --base-url http://172.17.0.1:18081/v1 \
  --depths 4k,16k --out harness-effect-results/run.json
```

`--pi-command` and `--dsh-command` replace the container invocation with host commands or
fakes. The shared cases control the question set; each harness still applies its own
prompting, context handling, and retries. The runner asks `/tokenize` and `/props` for
the actual ledger and served-context sizes. It skips a case when the direct prompt
plus a 4096-token answer reserve exceeds that context. If either endpoint is
unavailable, the artefact labels the size as an estimate.

One live Qwen3.6-35B-A3B UD-Q4_K_S run on 2026-09-25 used the 131072-token q8_0
preset in `examples/launch-dual.sh`, llama.cpp b10751, and the pinned runner image.
The [per-item result](harness-effect-results/qwen35-131k-20260925-01.json) has
no ledger text or answers. The generated ledger's server-tokenized sizes were
5347, 21277, 42426, and 84673 tokens at the first four targets. At each of
those depths, direct, pi, and dsh all answered the same 3 present and 6 absent
pairs correctly: 12/12 recall and 24/24 abstention per mode in total. The
requested 131072-token target produced a 169477-token ledger, over the served
window. An exploratory run obtained pi and dsh answers there while direct mode
received a server error; that case is marked invalid and excluded from every
aggregate. The new runner skips it before calling any mode. This single run
shows a ceiling on these items, so it cannot resolve a harness advantage.

## Short T0 + T0.5 screen

`scripts/screen.py` runs one command against a direct-mode server:

```bash
python3 scripts/screen.py --model your-model.gguf --base-url http://172.17.0.1:8082/v1
```

T0 uses the existing five schema probes, five tool-selection probes, one
three-call chain, and four refusal probes. T0.5 uses one 16k-token haystack
with three planted facts and three absent pairs. Each run writes
`screen-results/screen-<model>-<timestamp>.json`, including the raw battery
records, six extracted scores, elapsed seconds, and the threshold revision.
The default port is 8082 because port 8080 belongs to another project on
this machine. The command never starts a server.

The threshold file is [screen-thresholds.json](screen-thresholds.json).
Its current revision is `pending-2026-09-25`: the six minimums are `null`,
and the verdict is `unverified`. The four required models have not been
rerun on this exact short protocol, and the GPU needed for that calibration
was unavailable. Older `toolbattery-results/` files predate the short screen
and contain no T0.5 scores. They cannot establish these thresholds. See
[screen-calibration.md](screen-calibration.md) for the fixed run plan and the
evidence required before changing the revision to `calibrated`.

The under-15-minute wall-clock criterion is also unverified until a live
12 GB-class run records `elapsedSeconds`. Per-request timeouts bound normal
T0 and T0.5 requests, but they do not prove the full command meets that
criterion. A skipped depth, transport error, or missing score cannot produce
`go`. The plaintext-probe caveats in CANARY.md sections 3 and 3b apply.

## Layout

For the planted-bug tier, see [docs/T3.md](docs/T3.md). It covers instance
generation, sealing, runner setup, and scoring under `results/t3-<label>/`.

```
seed/              the repo each run starts from (SPEC.md, src stubs, visible tests, frozen item data)
hidden.tar.gz.enc  held-out suite, encrypted. `scripts/hidden.sh unlock` -> hidden/
hidden/            the oracle, once unlocked. Gitignored. Do not read when authoring a task.
refengine.tar.gz.enc  sealed 132/132 v1.0 solution (issue #37), encrypted the same way. `scripts/hidden.sh unlock refengine` -> refengine/
refengine/         the reference engine, once unlocked (`*.ts`, one file per `seed/src/` module).
                   Gitignored. T3/T4/T5 build on this, not on an agent's own T2 output -- see
                   CANARY.md section 4 for the contaminated author and the verification record
                   in refengine.score.json (committed, totals only).
docker/            image, harness configs, entrypoint, PROMPT.txt
  scorer.sh        runs both suites INSIDE the image (see MODELS.md §8)
examples/          guarded llama-server launchers: Gemma on one card; launch-dual.sh presets for the two-card models
scripts/
  bootstrap.sh     one-time setup
  hidden.sh        lock / unlock / verify the held-out suite
  ctxprobe.sh      find the context ceiling that actually serves
  score.py         run both suites, classify the outcome
  summarize.py     aggregate across runs
  gen_items.py     item-data provenance
  toolbattery.py   short tool-calling screening battery, talks to the model directly (issue #8)
  server_config.py llama-server /props + process/image/GPU provenance capture (issue #14);
                   run.sh writes its output to <label>/run-context.json before every run
  direct.py        shared OpenAI-compatible HTTP client for direct-mode batteries (issue #27)
  direct_env.py    direct-mode run-environment capture: GPU, backend, peak VRAM (issue #28)
  haystack.py      seeded fictional-fact haystack generator for recall/abstention batteries (issue #31)
  abstain.py       guaranteed-absent (entity, attribute) questions + abstention-phrase scoring (issue #32)
  recall.py        recall-at-context-depth AND abstention battery, talks to the model directly (issues #31, #32)
  screen.py        short direct-mode T0 + T0.5 screen (issue #33)
toolbattery-results/  JSON artefacts from scripts/toolbattery.py, one per run; not results/, and not committed by anything else
recall-results/       JSON artefacts from scripts/recall.py, one per run; same conventions as toolbattery-results/
screen-results/       JSON artefacts from scripts/screen.py; currently no calibrated verdicts
results/           one directory per run; score.json and events-summary.json are
                   committed (issue #7 -- the derived metrics outlive the trace). The rest
                   (pi-events.jsonl, session tarballs, stderr.log, run-context.json, ...) is
                   gitignored, since it's agent-written solution code and
                   would undercut CANARY.md -- but run.sh archives it to
                   ~/.cache/oaken-bench/<label>/ (or $OAKEN_ARCHIVE) so it
                   isn't lost to a git clean. score.py also writes
                   hidden-detail.json (per-file counts plus one entry per
                   test -- a digest, not a name, for the held-out suite;
                   see docker/score_detail.py) next to score.json,
                   gitignored, never published. It needs a runner image
                   built after issue #16 (`scripts/bootstrap.sh`, or
                   `docker build -t oaken-bench:1.0 docker/`), since
                   scorer.sh and score_detail.py are baked into the image
                   at build time. score.py reads <result_dir>/workspace.tgz,
                   so re-scoring an archived run means pointing it at the
                   archive directory directly, or copying workspace.tgz
                   (and run.meta etc.) back into results/<label>/ first:
                   `./scripts/score.py ~/.cache/oaken-bench/<label>` (or
                   `$OAKEN_ARCHIVE/<label>`) works as-is if that directory
                   still has workspace.tgz. Either way this REWRITES
                   score.json (and events-summary.json, hidden-detail.json)
                   in whichever directory you point it at -- a run whose
                   workspace.tgz is gone cannot be re-scored at all
                   (issue #7)
FROZEN.sha256      hashes of every frozen input
MODELS.md          how to add and tune a model  <- start here
CANARY.md          contamination control
EVENTS.md          what the pi and dsh event streams carry, field by field
FINAL-REPORT.md    findings, the original study and the two-card Qwen runs
```

## Known spec gaps (deliberately not fixed)

The Sonnet agent that wrote the held-out suite reported five places where
`SPEC.md` is silent or under-determined. **It asserted on none of them**, so
they carry no scoring weight. They are recorded here rather than patched,
because amending the spec after the oracle was written would risk a *silent*
desync: every held-out test fails against the stubs either way, so re-running
the suite cannot detect a semantic mismatch introduced by an edit.

| # | Section | Gap |
|---|---|---|
| 1 | 7.8 | Whether `start_of_combat` is emitted for a side with no such items (multiplier 1.0) |
| 2 | 7.8 | The `end` event's `side` field has no specified value; the outcome lives in `result` |
| 3 | 7.4 | Whether a `heal` event's `amount` reports the raw or the post-`maxHp`-clamp value |
| 4 | 5 | Whether `frozen` persists past a skipped day-start reroll, or is consumed by it |
| 5 | 7.2 / 7.4 | **The once-per-frame trigger cap is unreachable with the frozen item pool** — every item's cooldown cadence (>= 10 frames) exceeds its multicast span (<= 2 frames), so no natural collision exists |

Gap 5 is a task-design limitation rather than an ambiguity: the trigger cap is
specified and must be implemented, but no test can force it to bind. It is
covered only indirectly, by an invariant that no item ever triggers twice in one
frame. Fixing it would mean adding a fast-cooldown, high-multicast item to
`data/items.json` — which would shift every shop roll and invalidate every
hand-derived expectation in the held-out suite. Not worth it.

## Results

### Ceiling probe (viability gate) — PASSED

| | |
|---|---|
| Runner | Claude Opus 5, directly (no API key available for harness-mediated control) |
| Held-out | **129/132 (97.7%)** — bar is 80% |
| Visible | 52/52 (100%) |
| Overfit gap | +2.3 pts |
| Typecheck | clean |
| Frozen files | untampered |
| Wall clock | **5m 22s** of a 60-minute cap |

The task is doable and the spec is buildable from cold. Three held-out tests
failed; they were deliberately **not** inspected, because reading the failures
would leak oracle content and 97.7% against an 80% bar cannot change the gate's
verdict.

**Caveat on this number:** the probe's runner authored the spec, so
comprehension time was zero. Treat 5m22s as a floor, not an expectation, and
the 97.7% as a ceiling, not a baseline. It is also not harness-mediated, so it
cannot separate harness effect from model effect — the Q18 refinement was lost
when no API key turned out to be available.

### Timeout reduced 60 -> 30 minutes

The probe used 9% of the cap. 30 minutes is ~5.5x the probe and ~10x the
productive window observed in the 3-minute smoke runs, and halves total GPU
from ~9h to ~4.5h.

**Tripwire:** if 2 or more of the 6 Gemma runs end in `timeout` with visible
pass-rate still climbing at the cut, the cap bound the result rather than the
harness — rerun those at 60 minutes and say so.

### Qwen3.6-35B-A3B on RTX 5070 + RTX 3060 — six of six clear the bar

| run | outcome | hidden | wall | gap |
|---|---|---|---|---|
| pi-qwen35q4ks-b10751-01 | complete | 127/132 96.2 % | 529 s | +3.8 |
| pi-qwen35q4ks-b10751-02 | complete | 125/132 94.7 % | 944 s | +5.3 |
| pi-qwen35q4ks-b10751-03 | complete | 128/132 97.0 % | 500 s | +3.0 |
| dsh-qwen35q4ks-b10751-01 | complete | 124/132 93.9 % | 875 s | +6.1 |
| dsh-qwen35q4ks-b10751-02 | timeout | **131/132 99.2 %** | 1801 s | +0.8 |
| dsh-qwen35q4ks-b10751-03 | complete | 127/132 96.2 % | 1228 s | +3.8 |

All six typecheck clean, none tampered, every overfit gap +6.1 or less (Gemma's
ran +16 to +40). dsh-02 timed out still working and scored above the ceiling
probe. The model is not contaminated: it predates the suite by five months, and
asked for the canary GUID three times it produced nothing GUID-shaped.

Served by `examples/launch-qwen35moe.sh` on llama.cpp b10751 — the layout,
build requirement and measurements are in MODELS.md §4.2. **On b9716 the same
setup gave pi 0/132 twice** (llama.cpp #24807) and dsh 3.8 % and 96.2 %; those
four runs are kept and grouped separately.

### Known trap: no `@types/node`

The seed ships no `@types/node`, so `import ... from 'node:fs'` fails
type-checking. The intended route is the JSON import that `resolveJsonModule:
true` in `tsconfig.json` already enables. This is a real and discoverable
constraint, left in place deliberately; changing the seed after the probe would
mean the probe and the graded runs no longer share a task.

---

## Before you trust a number from this

These limitations apply to different tiers. Read the tier name with every
result; a probe result is not a T2 task score.

1. **T2 is one task.** It uses a single spec in a single domain. Three runs of one
   configuration in the original study spanned **0 % to 75.8 %** hidden. One run
   is not a result; report at least three and show the spread.
2. **The oracle is model-written.** The 132 held-out tests were authored blind
   by a Sonnet sub-agent that never saw a solution. The +2.3 point overfit gap
   on the Claude ceiling probe is evidence they are not trivially gameable, but
   this is model-generated ground truth and a strong Claude score may partly
   reflect shared assumptions.
3. **The 30-minute cap is often the binding constraint**, not model capability.
   Half the original runs ended at the wall clock. Anything that changes
   throughput — quantization, context size, a busy GPU, a second card — changes
   the score for reasons that have nothing to do with reasoning ability. The
   two-card Qwen runs decode at ~99 t/s where the plan budgeted 15.3; their
   scores say what that setup does, not how Qwen compares to Gemma.
4. **The published numbers are not cleanly reproducible.** `score.py` was
   patched mid-study to fix a process leak that corrupted wall-clock figures for
   runs scored after it appeared, and scoring later moved into a container.
   `summarize.py` groups the runs it considers comparable and labels what each
   mean covers; read that grouping rather than averaging the table yourself.
   Treat `FINAL-REPORT.md` as a record of what was observed, not as a reference
   scoreboard — one of its findings has since been retracted (§4.1, the
   compaction ratio), because the extractor that produced it was wrong and the
   traces needed to recompute it are gone.

   What the grouping still cannot see is the **`llama-server` configuration** a
   run was made under. Nothing in `score.json` records it, and MODELS.md §4
   documents that `--reasoning-format` alone decides whether this model's output
   reaches pi at all and whether dsh collapses. `harnessMetricsVersion` (absent
   = 1, the buggy extractor; 2 = scored after that was found) is the only
   in-band signal, and it is a proxy for when a run was scored, not for how the
   server was configured. See #14.

   The **llama.cpp build** belongs in the same sentence. It moved one
   configuration from 0 % to 96 % on pi without changing anything else.
   `examples/launch-qwen35moe.sh` writes the binary and version into the server
   log that each run's archive keeps; no earlier run recorded its build, and
   `score.json` still does not.
5. **Variance dominates, except on Qwen3.6.** Four of Gemma's six context-arm
   runs exited before 700 s, and score tracked how long a run survived far more
   closely than any parameter under test. gpt-oss spans 0-81.8 % on one harness
   and GLM 0-65.1 %. The Qwen runs on b10751 are the exception: a 5.3-point
   spread, no early exits. Expect to throw away runs.

6. **T0 and T0.5 are plaintext probes.** Their prompts, schemas, and fixed
   vocabulary are committed in the repository and can enter training data.
   T0.5 regenerates its facts and absent pairs by seed, but the probe shape
   stays fixed. These scores measure performance on these probes, not resistance
   to contamination. See [CANARY.md §3](CANARY.md#3-scriptstoolbatterypys-probes-are-a-new-contaminable-asset)
   and [§3b](CANARY.md#3b-scriptsrecallpy-scriptshaystackpy-and-scriptsabstainpy-the-same-asset-one-difference).
7. **The T0 + T0.5 screen has no calibrated verdict.** Its six thresholds
   remain pending, and its under-15-minute runtime target has not been verified
   on a live 12 GB-class run. Do not treat `go` as a calibrated model-selection
   decision until [screen-calibration.md](screen-calibration.md) records the
   required evidence.
8. **The T0.5 harness effect is not measured yet.** `harness_effect.py` can
   compare direct mode with pi and dsh, but no live comparison has run. Its
   result format and implementation do not establish whether either harness
   changes recall or abstention scores.
9. **T1, T3 and T4 have no model measurements.** T1 remains deferred after
   three archived 192k Gemma retests varied by work order and early exit.
   T3 and T4 have no-model runner checks;
   T4's unchanged reference engine passes 132/132 v1.0 tests and 2/137 v1.1
   tests. T5 has one CPU baseline measurement under its own protocol. Do not
   infer any tier's performance from T2 or the probe tiers.

Contributions that would help most: additional task instances, a task generator
(see CANARY.md), and results on hardware other than 12 GB consumer cards.
