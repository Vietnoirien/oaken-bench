# Harness event schemas

## T5 behavior fields (`score.json.behaviorMetrics`, version 1)

T5 scoring reads `pi-events.jsonl` or the root session in
`dsh-sessions.tgz` from the run directory, then from
`$OAKEN_ARCHIVE/<label>/` (default `~/.cache/oaken-bench/<label>/`).
`events.py` normalizes both formats before counting. The fields are in the T5
`score.json` and its tier-specific summary row. Direct oracle runs have no
harness trace: `traceAvailable` is false and the trace-derived counts are
`null`, never zero. The T5 baseline result committed before this change is
historical and has no `behaviorMetrics` field; it has not been rescored.

| field | definition |
|---|---|
| `simulationsRun` | Successful `bash` calls whose command invokes `t5-sim play` or `t5-sim matrix`. One call counts once, regardless of matches or seeds. Failed or unfinished calls do not count. |
| `strategiesTried` | Distinct source revision states at those successful simulator calls. A successful `write` or `edit` of a JS/TS path advances the revision digest. Repeating a simulation without an intervening edit counts one strategy. |
| `visibleSeedHardCoding.visibleRate` | Mean of the last full 100-seed visible `play` result found for each of `random`, `cheapest` and `merger`, for the scored bot basename and with no explicit `--seeds` flag. Missing any one gives `null`. |
| `visibleSeedHardCoding.heldOutRate` | The sealed T5 `winRate` already in this score. |
| `visibleSeedHardCoding.visibleMinusHeldOut` | Visible rate minus sealed rate, or `null` when no complete visible triple is in the trace. |
| `visibleSeedHardCoding.sourceSeedLiteralCount` | Number of distinct integers in the public visible range 1-100 used in direct comparisons with `botSeed` in the scored bot's source; `null` if source cannot be read. |

These are behavioral signals, not a hard-coding verdict. The two rates use
different opponents and scoring protocols (live baseline matches versus fixed
snapshot completion), so the gap is not an unbiased overfitting estimate.
The source scan misses computed seeds, arrays, aliases and generated source;
ordinary threshold logic can also use a literal in that range. Source edits
made through `bash`, tests outside `t5-sim`, and simulator runs nested in a
single shell command are not reliably counted. Only counts, rates, booleans
and fixed harness names leave `events.py`; raw commands, tool results, source
and all seed values stay out of the published score. No held-out seed is read.

---

What `pi-events.jsonl` and `dsh-sessions.tgz` actually carry, and which fields
each metric in `score.json` is derived from.

This file exists because issue #1 could not be implemented without it: the
schemas are not documented by either harness, and no raw trace from the original
study survived (issue #7). Everything below was read off a live capture, not
inferred from the harnesses' source.

**Provenance.** Two 240-second runs of the seed task on 2026-09-22, against
`llama-server` serving `gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf` (65536 total ctx
over 4 slots, so 16384 per slot). pi 0.86.0, dsh 0.1.5-rc.2, image
`oaken-bench:1.0`. Both traces are archived at
`~/.cache/oaken-bench/schema-capture-{pi,dsh}/`. They are captures for schema
purposes only and carry no score: the context window was a quarter of what the
study used, and the model is not one of the models under test.

The captures gave 35 pi tool calls (4 errored) and 37 dsh tool calls (7 errored),
which is enough to pin every field below including the error paths.

---

## 1. pi — `pi-events.jsonl`

One JSON object per line. Observed types, with counts from the capture:

| type | n | carries |
|---|---|---|
| `session` | 1 | `id`, ISO `timestamp`, `cwd`, `version` |
| `agent_start` | 1 | nothing else |
| `turn_start` | 35 | nothing else — `{"type":"turn_start"}` and no more |
| `message_start` / `message_end` | 72 / 72 | `message` |
| `message_update` | 17658 | streaming deltas |
| `tool_execution_start` | 35 | `toolCallId`, `toolName`, `args` |
| `tool_execution_update` | 2 | as start, plus `partialResult` |
| `tool_execution_end` | 35 | `toolCallId`, `toolName`, `result`, `isError` |
| `turn_end` | 35 | `message`, `toolResults` |
| `compaction_start` | 6 | `reason` |
| `compaction_end` | 5 | `reason`, `result`, `aborted`, `willRetry` |

### The tool-call triple

```jsonc
{"type":"tool_execution_start","toolCallId":"Eldu9...","toolName":"read",
 "args":{"path":"SPEC.md","offset":0,"limit":10000}}

{"type":"tool_execution_end","toolCallId":"Eldu9...","toolName":"read",
 "result":{"content":[{"type":"text","text":"..."}]},"isError":false}
```

Three things follow, and they are the whole of issue #1 and issue #2:

- **`isError` is an explicit boolean and was present on 35 of 35 ends.** Success
  and failure do not have to be inferred from the result text. `toolOutcomes`
  needs no `unknown` bucket for pi, but keep the bucket: absence is a real
  possibility on other versions and must not silently become `ok`.
- **`toolCallId` pairs start to end exactly.** 35 starts, 35 ends, zero
  unmatched in either direction. Pair by id, never by order.
- **`args` is a decoded object**, not a string. Note this: dsh differs.

Error results carry the reason in `result.content[0].text`. The capture's four
failures were all `edit`, all the same shape — the model omitted a required
parameter:

```
Validation failed for tool "edit":
  - path: must have required properties path
```

That is a schema-adherence failure, which is what issue #8's battery wants to
score. It is distinguishable from an `edit` whose `oldText` did not match only
by reading this text, so any classifier finer than ok/error has to parse it.

### Turn structure

`turn_start` is empty, so turns can only be counted, not attributed. Everything
about a turn lands in `turn_end`:

```jsonc
{"type":"turn_end",
 "message":{"role":"assistant",
   "content":[{"type":"text","text":"..."},
              {"type":"toolCall","id":"Eldu9...","name":"read","arguments":{...}}],
   "usage":{"input":1613,"output":115,"cacheRead":1,"cacheWrite":0,
            "reasoning":0,"totalTokens":1729,"cost":{...}},
   "stopReason":"toolUse","rawStopReason":"tool_calls",
   "timestamp":1790082097446,
   "model":"...","provider":"local-llama","api":"openai-completions"},
 "toolResults":[{"role":"toolResult","toolCallId":"Eldu9...","toolName":"read",
                 "content":[...],"isError":false,"timestamp":1790082098696}]}
```

- **Calls per turn (issue #6)** come from counting `content[]` entries of type
  `toolCall`. In the capture the distribution was `{1: 35}` — every turn carried
  exactly one call. That reproduces the 1:1 ratio noted in issue #6 on a
  different model, so it is not a property of the models in the study.
- **Timestamps (issue #5)** are epoch milliseconds, on `message.timestamp` and
  on each `toolResults[].timestamp`. **An earlier version of this file said the
  gap between them is the tool's own execution time, and that the gap between
  one turn's result and the next turn's message is generation time. Both were
  wrong**, and the correction is what issue #5 actually turns on for pi:

  `message.timestamp` is a single creation stamp, not a start/end pair. It is
  byte-identical on `message_start` and `message_end` for the same message, and
  the next turn's assistant message is stamped ~1ms after the previous
  `toolResults[].timestamp`. So it marks the moment the assistant message was
  created -- the *start* of generation -- and therefore:

  | gap | what it actually is |
  |---|---|
  | `message.timestamp` -> `toolResults[].timestamp` | generation **and** execution, inseparable |
  | one turn's result -> the next turn's message | ~1ms of bookkeeping, not generation |

  There is no second clock to recover the split from: `tool_execution_start`
  and `tool_execution_end` carry **no timestamp at all** (their only keys are
  `toolCallId`/`toolName`/`args` and `toolCallId`/`toolName`/`result`/`isError`).

  Taken literally, the old reading gives this capture 208.65 s of "tool
  execution" out of a 239.5 s run whose tools are local file reads, and 30.88 s
  of "generation" that is really the five completed compactions -- 29 of the 34
  inter-call gaps are under 10 ms. `call_metrics()` therefore reports
  `generationSeconds` and `toolExecutionSeconds` as `null` for pi
  (`events.PER_CALL_CLOCK`). dsh times `tool/call` and `tool/result`
  independently and keeps both figures.
- `stopReason` was `toolUse` 34 times and `length` once. `length` means the
  model hit `maxTokens` mid-answer and is worth surfacing.

### Two bugs in the extractor, fixed (#9, #10)

Both were pre-existing and affect the 16 `score.json` files committed before
this fix (see "Comparability" below) — their traces are gone (#7), so those
figures cannot be recomputed, only marked. `pi_metrics()` now reads correctly:

1. **`usage` is `turn_end.message.usage`, summed once per turn.** The old code
   read `e['usage']` / `e['data']['usage']`, which matches nothing in the table
   above except `message_update` — of which there were 17658. Those are
   cumulative per-chunk snapshots, added together as though they were
   increments. On this capture that gave `input 242002, output 31721,
   cacheRead 781969` against the real total.

   Retargeting to `e['message']['usage']` is not enough by itself: the same
   message's usage appears on both `message_end` and `turn_end`, so summing it
   wherever it appears double-counts. (`message_start` carries a `usage` dict
   too, but its values are zero.) The fix reads `turn_end` only — it fires
   exactly once per assistant turn.

   | | input | output | cacheRead |
   |---|---|---|---|
   | old (`message_update` sum) | 242,002 | 31,721 | 781,969 |
   | fixed (`turn_end.message.usage` sum) | **146,608** | **17,785** | **418,442** |

   Output was overstated by 78%, input by 65%, cacheRead by 87% — and by a
   ratio that varied with how many chunks each message streamed in, so the old
   figures were not even comparable to each other across runs.

2. **Compactions count `compaction_start` only.** `if 'compact' in t.lower()`
   matched `compaction_start` *and* `compaction_end`. This capture had 6 starts
   and 5 ends and reported 11; the fix reports **6**. `compaction_end` still
   carries information worth keeping — `aborted` (and `willRetry`) mark a
   compaction that did not complete — so it is now tallied separately as
   `compactionsAborted` rather than folded in or dropped. This capture had 0
   aborted compactions.

---

## 2. dsh — `dsh-sessions.tgz`

The tarball holds `.dsh/sessions/<cwd-slug>/<session>/session.v3.jsonl.zstd`.
Decompress with `zstd -dc`. One JSON object per line. Every event carries
`type`; all but the first also carry `seq`, `time` (epoch ms) and `data`.

**The `session` header is the exception, and it matters.** It is the first
line, and it holds its fields at the TOP level with no `data` wrapper at all:

```jsonc
{"type":"session","version":3,"id":"session-80a66c44-...","createdAt":1790082353977,
 "cwd":"/work","isSeeded":false,"delegationDepth":0}
```

A subagent's header is the same shape plus `parentSession` and
`origin: "subagent"`, with `delegationDepth: 1`. Root-session selection reads
these keys off the event itself -- looking for them under `data` finds nothing
and silently classifies every session as non-root.

### The root session is picked by header, not by mtime (#11, fixed)

The capture produced **two** session files. The second was a subagent: its
`session` header carries `parentSession` and `origin`, and the trace contains a
`subagent/descriptor` event. The root session's header has
`delegationDepth: 0` and no `parentSession`.

`dsh_metrics()` used to select `sessions[-1]` after sorting by mtime. In this
capture that happened to land on the root session by four seconds — luck, not
a rule: a subagent that outlives its parent inverts the order, and the run's
metrics would then be read off the subagent. `dsh_metrics()` now selects on
`delegationDepth == 0` / absence of `parentSession`, via
`events.load_dsh_root_sessions()` — the same selection `load_dsh_events()`
already used, so there is now one implementation instead of two. If a tarball
ever holds more than one root session, that is a real ambiguity and is
reported (`rootSessionAmbiguous` / `rootSessionCount` in `harnessMetrics`), not
resolved by timestamp.

### Observed types (root session)

| type | n | carries |
|---|---|---|
| `session` | 1 | `id`, `createdAt`, `cwd`, `delegationDepth`, `version` |
| `turn/start`, `turn/end` | 1, 1 | `turn`; end also `reason.kind` |
| `step/start`, `step/end` | 38, 38 | `turn`, `step` |
| `assistant/message` | 38 | `turn`, `step`, `message`, `usage`, `stream` |
| `tool/call` | 37 | `turn`, `step`, `callId`, `name`, `arguments` |
| `tool/result` | 40 | `turn`, `step`, `message`, sometimes `meta` |
| `compaction/start`, `/end` | 2, 2 | `compactionId`, `turn` |
| `compaction/summary` | 2 | |
| `compaction/prune` | 3 | `shadowedRange`, `shadowedSeqs`, `shadowedTokenCount` |
| `todo/write` | 11 | |
| `request/header` | 5 | full tool schemas sent to the model |
| `user/message`, `system/message` | 7, 1 | |

**`turn/end` fired once for the whole run.** A dsh "turn" is the user-level
conversation turn, not an assistant step. The analogue of pi's `turn_start` is
`step/start` — which is what `dsh_metrics()` already counts, correctly.

### The tool-call pair

```jsonc
{"type":"tool/call","seq":16,"time":1790082355443,
 "data":{"turn":1,"step":1,"callId":"SFmur...","name":"read",
         "arguments":"{\"file_path\":\"SPEC.md\"}"}}

{"type":"tool/result","seq":17,"time":1790082355460,
 "sourceEventSeqs":[16],
 "data":{"turn":1,"step":1,
   "message":{"role":"user","source":{"kind":"tool","callId":"SFmur..."},
     "content":[{"type":"tool-result","toolCallId":"SFmur...",
                 "content":[{"type":"text","text":"..."}],"isError":false}]}}}
```

Differences from pi that an extractor has to handle:

- **`arguments` is a JSON string**, not an object. An argument digest (issue #2)
  must decode it and hash a canonical form, or pi and dsh digests are not
  comparable for the same call.
- **`isError` is nested** at `data.message.content[].isError`, and the `content`
  entry is typed `tool-result`. It was present on all 40 results.
- **Results outnumber calls.** 37 calls, 40 results, but zero unmatched ids in
  either direction — three `callId`s carry more than one result event. Classify
  per distinct `callId`, not per result event, or the error rate is wrong.
- `sourceEventSeqs` also links a result to its call's `seq`, which is a second
  way to pair if `callId` is ever missing.
- `data.turn` and `data.step` are on both, so calls-per-step (issue #6) needs no
  pairing at all. The capture's distribution was `{1: 37}`: one call per step,
  the same result as pi.

### Usage and compaction (#9, #10, fixed)

Usage is on `assistant/message`:

```jsonc
"usage":{"inputTokens":7128,"outputTokens":17,"totalTokens":7146,"cacheReadTokens":1}
```

`dsh_metrics()` reached this correctly, but its fallback — `u = d.get('usage')
if isinstance(d.get('usage'), dict) else d` — treated every other event's
`data` as a usage record and absorbed any stray `inputTokens`-shaped key that
appeared elsewhere in the stream. The fallback is deleted: only
`assistant/message.data.usage` is summed now. On this capture:

| | inputTokens | outputTokens | cacheReadTokens |
|---|---|---|---|
| fixed (`assistant/message.data.usage` sum) | 76,178 | 16,225 | 637,155 |

(No "old" row: the pre-fix `else d` fallback's total depends on whatever
stray shapes happened to be in the stream, and isn't a meaningful number to
carry forward — unlike pi's `usage`, dsh's old figure wasn't consistently a
multiple of the real one.)

Compactions count `compaction/start` only, and there were 2. The old heuristic
(`'compact' in json.dumps(e)[:2000].lower()`) matched 11 events on this
capture: 2 `compaction/start`, 2 `compaction/end`, 2 `compaction/summary`, 3
`compaction/prune` — and 2 `user/message` events that merely used the word.
`results/dsh-03/score.json` reported 15. See #10.

`compaction/prune` is dsh's model-free tool-result pruner, the mechanism README
describes under Fairness. It is not a compaction and is counted separately, as
`prunedToolResults` in `harnessMetrics` — a count and the sum of
`shadowedTokenCount`. This capture had 3 prunes shadowing 18,701 tokens total,
against 2 real compactions:

| | old (`compact` substring) | fixed |
|---|---|---|
| `compactions` | 11 | **2** |
| `prunedToolResults.count` | (folded into the 11 above) | **3** |

---

## 3. Field-by-field: where each metric comes from

| metric | pi | dsh |
|---|---|---|
| turns / steps | count `turn_start` | count `step/start` |
| tool calls | count `tool_execution_start` | count `tool/call` |
| tool name | `toolName` | `data.name` |
| tool arguments | `args` (object) | `data.arguments` (JSON string) |
| call id | `toolCallId` | `data.callId` |
| ok / error | `isError` on `tool_execution_end` | `data.message.content[].isError` on `tool/result` |
| call start time | — (enclosing `turn_end.message.timestamp`, which is generation start, not call start) | `time` on `tool/call` |
| call end time | `turn_end.toolResults[].timestamp` | `time` on `tool/result` |
| generation vs execution split | **not available** — see §1 | `tool/call` → `tool/result` vs result → next call |
| derived decode rate | **not available** (no generationSeconds) | output tokens / generationSeconds |
| calls per turn / parallelTurns | group by `turn` | group by `(data.turn, data.step)` |
| calls per turn | count `toolCall` blocks in `turn_end.message.content` | group `tool/call` by `(data.turn, data.step)` |
| usage | `turn_end.message.usage` (once per message) | `assistant/message.data.usage` |
| compactions | count `compaction_start` | count `compaction/start` |
| aborted compactions | `compaction_end` where `aborted` is truthy | — |
| pruned tool results | — | count `compaction/prune`, sum `data.shadowedTokenCount` |
| stop reason | `turn_end.message.stopReason` | `assistant/message.data.message.source.replayState.response.stopReason` |

## 4. Re-capturing

The schemas are pinned to pi 0.86.0 and dsh 0.1.5-rc.2, the versions in
`docker/Dockerfile`. Bumping either invalidates this file. To re-capture, run
both harnesses briefly against any registered model and survey the result:

```bash
./run.sh pi  <model-id> schema-capture-pi  240
./run.sh dsh <model-id> schema-capture-dsh 240
```

A capture needs the error paths as well as the happy path, so it needs a model
weak enough to get tool calls wrong. A strong model produces a clean trace that
pins less.

## 5. Reading the timing fields

Issue #5's caveat: a timing figure is not interpretable without the run's
throughput, because decode rate on this hardware varies with what else is
resident (MODELS.md §3 — a desktop app reopening mid-run costs ~700 MiB and
can flip a configuration into the post-OOM fallback at half speed).

So `harnessMetrics` carries `derivedDecodeTokensPerSecond` alongside the
timing fields. **Derived, not measured**: nothing in this repo records
llama-server's own `timings.predicted_per_second` per run, because the
harness makes the requests and that figure never reaches the trace. This is
output tokens divided by generation seconds, an *effective* rate whose
denominator is wall-clock between one tool result and the next call — it
includes prefill and harness overhead and reads lower than a bare decode
benchmark. On the archived dsh capture: 16225 output tokens over 208.5 s,
77.81 t/s.

It is `null` for pi, because `generationSeconds` is itself unavailable there
(§1). An absent rate is not a slow one — and a pi run's timing fields
therefore have no throughput to be read against, which is a real limitation
of pi's event stream rather than something this repo chose.
