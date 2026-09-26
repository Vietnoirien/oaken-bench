# Screen calibration record

Status: calibrated at threshold revision `screen-v4-2026-09-26`. The rule is a
local triage rule for these four models, not an estimated probability of T2
success. One GLM chain diagnostic would pass the chosen cutoff, and gpt-oss,
which passed T2 once in six runs, is rejected. Those errors are visible below.

## Protocol and hardware

`scripts/screen.py` runs five schema cases, five tool-selection cases, all four
dependent chains (15 steps total), four refusal cases, and one 16k-token
recall/abstention depth with three present and three absent questions. Seed
20260924, T0 output limit 8192, T0 request timeout 180 s, T0.5 output limit
16384, T0.5 timeout 360 s. Every model used protocol version 4 and
llama.cpp build `b1-125c6d1`. One server ran at a time on port 8082. The
launchers' smoke tests passed before the screens.

Gemma used `examples/launch-gemma.sh 131k`, its guarded single-card q8_0/MTP
preset. Issue #33 names `launch-dual.sh`, but Gemma's documented preset runs
on the 5070 alone. gpt-oss and GLM used their 131k q8_0
`examples/launch-dual.sh` presets. Qwen used the separate
`qwen35moe-desktop` preset: the same 131k q8_0 weights as the T2 run, but
expert block 14 moved to the RTX 3060 because the original block-15 split
left only 144 MiB free on the RTX 5070 while Showtime was open. Its guarded
smoke check left 446 / 322 MiB free on the 5070 / 3060. This Qwen layout is
not identical to its T2 server configuration. The GLM smoke check left
527 / 926 MiB, gpt-oss left 922 / 6826 MiB, and Gemma left 1185 MiB on
the 5070. Do not infer those margins will hold under a different desktop load.

The final screen artefacts are:

| Model | Artefact | Complete | Wall time | Chain depth | Recall | Abstention | Verdict at this revision | Known T2 outcome |
|---|---|---:|---:|---:|---:|---:|---|---|
| Gemma 4 12B | `screen-results/screen-gemma-4-12B-it-qat-UD-Q4_K_XL.gguf-20260926T103513Z.json` | yes | 54.0 s | 15/15 | 3/3 | 3/3 | go | One pass in 14 runs; wide spread |
| gpt-oss-20b | `screen-results/screen-gpt-oss-20b-MXFP4.gguf-20260926T102355Z.json` | yes | 181.5 s | 8/15 | 3/3 | 3/3 | no-go | One pass in six runs |
| GLM-4.7-Flash | `screen-results/screen-GLM-4.7-Flash-UD-Q4_K_XL.gguf-20260926T103321Z.json` | yes | 73.2 s | 11/15 | 3/3 | 3/3 | no-go | No pass in six runs |
| Qwen3.6-35B-A3B | `screen-results/screen-Qwen3.6-35B-A3B-UD-Q4_K_S.gguf-20260926T103843Z.json` | yes | 148.5 s | 12/15 | 3/3 | 3/3 | go | Six passes in six runs at 131k/q8_0 |

Gemma's 54.0 s is the measured 12 GB-class wall time for this command, below
the issue's 15-minute target. Each artefact records the calibrated revision
and verdict in its primary fields. `collectionAssessment` preserves the
original provisional revision and `unverified` verdict from collection.
The metrics and raw battery records are unchanged.

One earlier protocol-4 GLM artefact,
`screen-results/screen-GLM-4.7-Flash-UD-Q4_K_XL.gguf-20260926T103155Z.json`,
used all 16384 recall output tokens without a tool call. It is marked
`incomplete`; its 13/15 chain depth is evidence about the chain, not a
full-screen verdict. Earlier protocol versions used output limits too small
for reasoning models. Their artefacts and raw diagnostic runs are kept under
`~/.cache/oaken-bench/screen-issue-33/diagnostic-artifacts/`, not used in
this threshold decision. Server logs are in the parent archive directory.

## Where the chain threshold comes from

`screen-results/screen-chain-calibration-20260926.json` records three
independent runs of the same four chains at 8192 output tokens per model.
The full protocol-4 run is shown in the last column. Every diagnostic
completed without a transport error. The gpt-oss missed calls inspected in
one additional run ended with `finish_reason=stop`, not a token cutoff.

| Model | Three chain diagnostics | Full screen | T2 passes |
|---|---|---:|---:|
| Gemma | 15/15, 15/15, 15/15 | 15/15 | 1/14 |
| gpt-oss | 8/15, 6/15, 8/15 | 8/15 | 1/6 |
| GLM | 11/15, 13/15, 11/15 | 11/15 | 0/6 |
| Qwen | 15/15, 15/15, 13/15 | 12/15 | 6/6 |

The chain minimum is 12/15 (0.8), the lowest Qwen result observed across
these runs. It rejects every gpt-oss chain result and three of four GLM
chain results, while retaining every observed Qwen and Gemma result. It
falsely accepts GLM's 13/15 diagnostic. A cutoff above 12/15 would falsely
reject Qwen's complete screen. The overlap cannot be removed by choosing a
number between the observed values.

The other minimums guard against large failures in dimensions where all
four final screens scored 1.0, so the four-model set cannot estimate an
optimal boundary for them:

| Metric | Minimum | Interpretation |
|---|---:|---|
| schemaAdherence | 4/5 (0.8) | Tolerate one malformed or missing schema case. |
| toolSelection | 4/5 (0.8) | Tolerate one wrong tool among five cases. |
| shortChainsDepth | 12/15 (0.8) | Empirical separator above, with one GLM false accept. |
| refusal | 3/4 (0.75) | Require most no-tool prompts to be refused. |
| recall | 2/3 (0.6667) | Require at least two planted facts at 16k. |
| abstention | 2/3 (0.6667) | Require at least two absent questions to be refused. |

This screen prefers a false accept over a false reject at the chain boundary.
It still rejects gpt-oss even though one T2 pi run passed; that is a known
false reject against the loose outcome "ever passed T2". A single fixed seed,
one 16k recall depth, and a few public plaintext probes cannot establish
predictive accuracy. The Qwen layout and llama.cpp build also differ from
the older T2 runs. Treat this as a way to avoid obvious long-run failures,
not a replacement for the full ladder.

Run with one guarded local server on port 8082:

```bash
python3 scripts/screen.py --model <served-alias> --base-url http://172.17.0.1:8082/v1
```

A complete run returns `go` or `no_go` and writes an artefact with its
threshold revision. A truncated recall or chain response returns
`incomplete`, never a scored abstention or a no-go verdict. Raw server logs
and superseded diagnostic artefacts remain in the private archive for
reinspection. None of these screens used the T2 held-out plaintext suite.
