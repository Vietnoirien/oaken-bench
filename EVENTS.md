# Harness event schemas

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
  on each `toolResults[].timestamp`. The gap between them is the tool's own
  execution time; the gap between one turn's result and the next turn's message
  is generation time.
- `stopReason` was `toolUse` 34 times and `length` once. `length` means the
  model hit `maxTokens` mid-answer and is worth surfacing.

### Two bugs in the current extractor, found while reading this

Both are pre-existing and affect every committed `score.json`. Neither is fixed
here; they are recorded so the numbers are not read as sound.

1. **`usage` is summed from streaming partials.** `pi_metrics()` reads
   `e['usage']` / `e['data']['usage']`, which matches nothing in the table above
   except `message_update` — of which there were 17658. The real per-message
   figure lives at `e['message']['usage']`, and it appears three times for the
   same message (`message_start`, `message_end`, `turn_end`), so summing that
   naively triple-counts instead. On the capture the two methods gave
   `input 45786` and `input 48934` for the same run. Neither is the right
   number.
2. **Compactions are double-counted.** `if 'compact' in t.lower()` matches
   `compaction_start` *and* `compaction_end`. The capture had 6 starts and 5
   ends and would report 11. `results/pi-03/score.json` says `compactions: 2`
   for a run whose trace held one start and one end.

---

## 2. dsh — `dsh-sessions.tgz`

The tarball holds `.dsh/sessions/<cwd-slug>/<session>/session.v3.jsonl.zstd`.
Decompress with `zstd -dc`. One JSON object per line; every event carries
`type`, `seq`, `time` (epoch ms) and `data`.

### Pick the root session, not the newest file

The capture produced **two** session files. The second was a subagent: its
`session` header carries `parentSession` and `origin`, and the trace contains a
`subagent/descriptor` event. The root session's header has
`delegationDepth: 0` and no `parentSession`.

`dsh_metrics()` currently selects `sessions[-1]` after sorting by mtime. In this
capture that happened to land on the root session by four seconds. It is luck:
a subagent that outlives its parent inverts the order and the run's metrics are
then read off the subagent. Select on `delegationDepth == 0` / absence of
`parentSession`.

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

### Usage and compaction

Usage is on `assistant/message`:

```jsonc
"usage":{"inputTokens":7128,"outputTokens":17,"totalTokens":7146,"cacheReadTokens":1}
```

`dsh_metrics()` reaches this correctly, but its fallback — `u = d.get('usage')
if isinstance(d.get('usage'), dict) else d` — treats every other event's `data`
as a usage record and will absorb any stray `inputTokens` key that appears
elsewhere.

Compactions are `compaction/start`, and there were 2. The current heuristic
(`'compact' in json.dumps(e)[:2000].lower()`) also matches `compaction/end`,
`compaction/summary` and `compaction/prune`, and any assistant message that uses
the word — 9 events here before counting prose. `results/dsh-03/score.json`
reports 15.

`compaction/prune` is dsh's model-free tool-result pruner, the mechanism README
describes under Fairness. It is not a compaction and should be counted
separately if it is counted at all.

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
| call start time | — (use enclosing `turn_end.message.timestamp`) | `time` on `tool/call` |
| call end time | `turn_end.toolResults[].timestamp` | `time` on `tool/result` |
| calls per turn | count `toolCall` blocks in `turn_end.message.content` | group `tool/call` by `(data.turn, data.step)` |
| usage | `turn_end.message.usage` (once per message) | `assistant/message.data.usage` |
| compactions | count `compaction_start` | count `compaction/start` |
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
