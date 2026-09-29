# Fresh-seed screen follow-up

The calibrated screen has one fixed chain set and one 16k recall seed. Its four
model runs do not estimate repeatability, and recall and abstention were 3/3
for all four models. This follow-up measures spread without changing
`screen-thresholds.json` or issuing a new go/no-go verdict.

## Predeclared protocol

Run `scripts/screen_followup.py` once per model against a guarded local server.
The script uses seeds `20260927` and `20260928`. For each seed it runs four
chains of lengths 3, 3, 4, and 5, then recall and abstention at 16k, 32k, and
64k tokens. Chain prompts change between seeds, as do the opaque IDs returned
by simulated tools. The tools and dependency graphs remain the same. Each
recall depth plants three facts and asks six absent questions, two of each kind.
This yields 30 chain links, 18 present questions, and 36 absent questions per
model across both seeds. Record per-chain depth, position-specific recall,
abstention kind, incomplete requests, wall time, and server environment. Do not
pool incomplete depths into a rate.

Use the same 131k q8_0 presets and llama.cpp build `b1-125c6d1` as the
September 26 calibration if available. Run one server at a time on port 8082.
Keep the guarded launcher's smoke-test throughput and GPU margin alongside the
artefact. A launch or later request with CUDA fallback is invalid. Stop the
server after the diagnostic, and archive its log privately.

```bash
python3 scripts/screen_followup.py --model <served-alias> \
  --base-url http://172.17.0.1:8082/v1
```

`--seed 20260927` or `--seed 20260928` runs one predeclared seed when a
single-server session needs a shorter bound. The script writes JSON under
`screen-results/`; it returns 2 if any seed is incomplete. It never reads T2
held-out files. The model's full 16k screen result remains the only result to
which `screen-v4-2026-09-26` thresholds apply.

## Reading the result

Compare each model's 16k recall and abstention across the original screen and
both new seeds, then inspect 32k and 64k separately. Report numerator and
denominator for each depth and abstention kind. Compare chain depth by seed and
by scenario rather than using a pooled average alone. Keep the T2 outcomes
beside these observations: Qwen passed 6/6 at 131k/q8_0, Gemma 1/14,
gpt-oss 1/6, GLM 0/6. Those T2 run counts are small and differ in setup, so
this follow-up tests stability of the screen observations, not predictive
accuracy or a causal link to T2 success.

The new chain wording and fictional facts are still public probe material;
`CANARY.md` section 3 describes the contamination limit. A ceiling at 16k
may persist. The higher depths are diagnostic evidence, not a revised gate.

## Gemma pilot, September 27

Gemma 4 12B QAT ran both predeclared seeds on llama.cpp `b1-125c6d1`, using
the guarded 131k q8_0/MTP preset on the RTX 5070. The smoke checks were
119.1 and 127.0 tokens/s, with 1013 and 1104 MiB free. Neither server log
records CUDA allocation fallback. Each single-use server stopped after its
seed; the logs are in the private archive at
`~/.cache/oaken-bench/screen-followup-20260927-gemma/server.log` and
`~/.cache/oaken-bench/screen-followup-20260928-gemma/server.log`.

| Seed | Chain depth | 16k recall / abstention | 32k | 64k recall / abstention | Wall time |
|---|---:|---:|---|---:|---:|
| 20260927 | 15/15 | 3/3, 6/6 | Incomplete: output hit 16,384-token limit | 3/3, 6/6 | 269.9 s |
| 20260928 | 15/15 | 3/3, 6/6 | 3/3, 6/6 | 3/3, 6/6 | 132.2 s |

The artifacts are `screen-results/screen-followup-gemma-seed20260927.json`
and `screen-results/screen-followup-gemma-seed20260928.json`. Five of six
depths were scored: 15/15 planted facts and 30/30 absent questions. The
first seed's 32k request is missing evidence, not a pass or a failure. The
second seed completed at that depth with the same output limit. Gemma is still
at the ceiling on every completed depth, so these two seeds do not reveal a
useful recall or abstention cutoff for this model.

## gpt-oss pilot, September 27

gpt-oss-20b MXFP4 ran both seeds on the guarded `oss20b` preset, on llama.cpp
`b1-125c6d1` at 131k/q8_0. The smoke test passed at 117.4 tokens/s with
1088 / 6826 MiB free on the 5070 / 3060. The server log has no CUDA
allocation failure and is archived privately at
`~/.cache/oaken-bench/screen-followup-20260927-gpt-oss/server.log`.

| Seed | Chain depth | 16k recall / abstention | 32k recall / abstention | 64k recall / abstention | Wall time |
|---|---:|---|---:|---|---:|
| 20260927 | 7/15 | Incomplete: server HTTP 500, peg-native format error | 2/3, 5/6 | Incomplete: output hit 16,384-token limit | 535.0 s |
| 20260928 | 5/15 | 3/3, 6/6 | 3/3, 6/6 | 3/3, 6/6 | 347.5 s |

The artifacts are `screen-results/screen-followup-gpt-oss-seed20260927.json`
and `screen-results/screen-followup-gpt-oss-seed20260928.json`. Both chain
scores are below the calibrated screen's 12/15 boundary, but these follow-up
cases do not change that boundary or produce new go/no-go verdicts. The first
seed has only one scored recall depth. Its HTTP 500 and truncated output stay
visible as missing evidence. The second seed shows that gpt-oss can answer all
18 questions at the three depths in a complete run, even while its chains
reach only 5/15.

## GLM attempt, September 27

The `glm47flash` guarded launch passed its smoke test at 87.8 tokens/s with
665 / 926 MiB free on the 5070 / 3060. Its first follow-up chain request
crashed llama-server with a CUDA illegal-memory-access error. The client got
`RemoteDisconnected` before an artifact could be written. The server stopped,
and the crash log is archived at
`~/.cache/oaken-bench/screen-followup-20260927-glm/server.log`. This attempt
measures a runtime failure, not GLM's chain or recall ability. No GLM
follow-up score exists for these seeds.

## Qwen pilot, September 27

Qwen3.6-35B-A3B ran both seeds on the guarded `qwen35moe-desktop` preset, on
llama.cpp `b1-125c6d1` at 131k/q8_0. The smoke test passed at 96.1 tokens/s
with 818 / 322 MiB free on the 5070 / 3060. The server log has no CUDA
allocation failure and is archived privately at
`~/.cache/oaken-bench/screen-followup-20260927-qwen/server.log`. The server
stopped after both seeds.

| Seed | Chain depth | 16k recall / abstention | 32k recall / abstention | 64k recall / abstention | Wall time |
|---|---:|---:|---:|---:|---:|
| 20260927 | 15/15 | 3/3, 6/6 | 3/3, 6/6 | 3/3, 6/6 | 296.3 s |
| 20260928 | 15/15 | 3/3, 6/6 | 3/3, 6/6 | 3/3, 6/6 | 280.7 s |

The artifacts are `screen-results/screen-followup-qwen-seed20260927.json`
and `screen-results/screen-followup-qwen-seed20260928.json`. Across both seeds,
Qwen reached 30/30 chain links, recalled 18/18 planted facts, and correctly
abstained on 36/36 absent questions. The new cases did not reveal a lower
recall or abstention boundary for Qwen at 64k. Its desktop expert split is
the same one used by the calibrated short screen and differs from its older
T2 server layout, so these results do not establish a T2 prediction.
