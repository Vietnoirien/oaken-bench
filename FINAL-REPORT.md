# oaken-bench — final report

**Task:** implement a deterministic PvP auto-battler engine from a 378-line normative spec
(`SPEC.md`, ~10 300 tokens) into 10 pre-declared TypeScript modules.
**Grading:** 52 visible tests (in-repo) + **132 hidden tests** (written blind by a Sonnet
sub-agent, never read by the implementing agent). **Bar: 80 % hidden.**
**Conditions:** sealed prompt, no human input, network off, container per run, 30-min cap.
**Hardware:** RTX 5070 12 GB, i9-9900KF, 128 GB RAM. From §3.6 on, an RTX 3060 12 GB beside it.

---

## 1. Headline

| | |
|---|---|
| **Task is solvable** | Claude reached **129/132 (97.7 %)** in **5 m 22 s** |
| **Best local configuration** | Qwen3.6-35B-A3B UD-Q4_K_S on 5070 + 3060, llama.cpp b10751 — **6/6 runs clear the bar**, 93.9-99.2 % |
| **Best single-card model** | Gemma 4 12B, **110/132 (83.3 %)** — clears the bar once in 14 runs |
| **Qwen3.8-27B @ Q2_K_XL** | **0/132 across 5 configurations** — never wrote a line |
| **Qwen3.8-27B @ IQ2_XXS** | **0/132** at full 131 k ctx — same behaviour, no quant cliff |
| **Same cards, same build, two other MoEs** | gpt-oss-20b 1/6 clear the bar (pi 30.8 %, dsh 0 %); GLM-4.7-Flash 0/6 (pi 29.5 %, dsh 29.8 %) |
| **Runs clearing 80 %** | **10** — the Claude ceiling probe, one Gemma run, seven Qwen3.6 runs, one gpt-oss run |

The benchmark and its oracle are sound: a 97.7 % result in under six minutes proves the task is
well-specified and the hidden suite is fair.

**A local configuration now clears the bar reliably.** Qwen3.6-35B-A3B split across the 5070 and
a 3060 scored between 124 and 131 of 132 in all six graded runs on llama.cpp b10751, three per
harness, with overfit gaps of +0.8 to +6.1 — the Claude probe's is +2.3. One dsh run scored
131/132, above the probe. See §3.6. It is not comparable with anything single-card: it decodes
at ~99 t/s, and the cap binds less at that speed (README limitation 3).

**That result belongs to the model, not the setup.** Two other MoEs ran the same protocol on the
same cards, llama.cpp build, context window, KV precision and harness configs, n=3 per harness
(§3.7, §3.8). gpt-oss-20b cleared the bar once in six; GLM-4.7-Flash never did, despite
vendor-reported agentic scores well above gpt-oss's. Neither difference is clean — GLM decodes at
less than half Qwen3.6's rate at depth and ran into the cap five times in six, and dsh could not
run gpt-oss at all — but no reading of these twelve runs puts either model near Qwen3.6.

**On one card, the bar was cleared once.** `dsh-gemma192k-03` scored 110/132 (83.3 %) with a
clean typecheck, 52/52 visible and an overfit gap of +16.7 — the tightest of any Gemma run. It is
**one run out of six**, and the same configuration also produced a 23/132, so this says the task
is reachable by a 12 GB-class model, **not** that Gemma reaches it reliably. See §3.5.

---

## 2. All scored runs

| run | harness | model | outcome | hidden | visible | wall | tools | cmp | tc |
|---|---|---|---|---|---|---|---|---|---|
| ceiling-claude | — | Claude | complete | **129/132 97.7 %** | 52/52 | 322 s | — | — | ok |
| dsh-gemma192k-03 | dsh | Gemma 12B @196 k | **complete** | **110/132 83.3 %** | 52/52 | 1458 s | 106 | 4 | ok |
| pi-03 | pi | Gemma 12B | below bar | **100/132 75.8 %** | 52/52 | 896 s | 60 | 2 | ✗ |
| dsh-gemma192k-01 | dsh | Gemma 12B @196 k | below bar | 98/132 74.2 % | 52/52 | 687 s | 43 | 0 | ok |
| dsh-03 | dsh | Gemma 12B | timeout | **98/132 74.2 %** | 51/52 | 1801 s | 92 | 15 | ✗ |
| dsh-02 | dsh | Gemma 12B | crash | 66/132 50.0 % | 44/52 | 1241 s | 58 | 10 | ✗ |
| pi-01 | pi | Gemma 12B | timeout | 36/132 27.3 % | 35/52 | 1801 s | 327 | 0 | ✗ |
| pi-02 | pi | Gemma 12B | hang | 0/132 | 0/52 | 1801 s | 76 | 4 | ✗ |
| dsh-01 | dsh | Gemma 12B | declared done | 0/132 | 2/52 | 46 s | 15 | 0 | ✗ |
| pi-qwen32k-01 | pi | Qwen 27B Q2 | declared done | 0/132 | 3/52 | 103 s | 24 | 0 | ok |
| pi-qwen48k-04 | pi | Qwen 27B Q2 | declared done | 0/132 | 3/52 | 166 s | 23 | 0 | ok |
| pi-q48kB-02 | pi | Qwen 27B Q2 | declared done | 0/132 | 3/52 | 556 s | 24 | 2 | ok |
| dsh-qwen32k-01 | dsh | Qwen 27B Q2 | timeout | 0/132 | 3/52 | 1801 s | 37 | 25 | ok |
| dsh-qwen48k-01 | dsh | Qwen 27B Q2 | crash | 0/132 | 3/52 | 238 s | 27 | 2 | ok |
| dsh-q48kB-01 | dsh | Qwen 27B Q2 | crash | 0/132 | 3/52 | 215 s | 21 | 0 | ok |
| dsh-q48kC-01 | dsh | Qwen 27B Q2 | crash | 0/132 | 3/52 | 199 s | 22 | 0 | ok |
| dsh-gemma131k-03 | dsh | Gemma 12B @131 k | crash | 95/132 72.0 % | 48/52 | 1515 s | 86 | 9 | ✗ |
| dsh-gemma131k-01 | dsh | Gemma 12B @131 k | crash | 58/132 43.9 % | 43/52 | 694 s | 53 | 0 | ✗ |
| dsh-gemma131k-02 | dsh | Gemma 12B @131 k | crash | 34/132 25.8 % | 33/52 | 343 s | 24 | 0 | ✗ |
| dsh-gemma192k-02 | dsh | Gemma 12B @196 k | crash | 23/132 17.4 % | 30/52 | 369 s | 27 | 0 | ✗ |
| dsh-xxs128k-01 | dsh | Qwen 27B **IQ2_XXS** | crash | 0/132 | 3/52 | 568 s | 24 | 0 | ok |
| dsh-qwen35q4ks-b10751-02 | dsh | Qwen3.6 35B-A3B, b10751 | timeout | **131/132 99.2 %** | 52/52 | 1801 s | 100 | 1 | ok |
| pi-qwen35q4ks-b10751-03 | pi | Qwen3.6 35B-A3B, b10751 | complete | 128/132 97.0 % | 52/52 | 500 s | 58 | 0 | ok |
| pi-qwen35q4ks-b10751-01 | pi | Qwen3.6 35B-A3B, b10751 | complete | 127/132 96.2 % | 52/52 | 529 s | 73 | 0 | ok |
| dsh-qwen35q4ks-b10751-03 | dsh | Qwen3.6 35B-A3B, b10751 | complete | 127/132 96.2 % | 52/52 | 1228 s | 77 | 1 | ok |
| pi-qwen35q4ks-b10751-02 | pi | Qwen3.6 35B-A3B, b10751 | complete | 125/132 94.7 % | 52/52 | 944 s | 91 | 1 | ok |
| dsh-qwen35q4ks-b10751-01 | dsh | Qwen3.6 35B-A3B, b10751 | complete | 124/132 93.9 % | 52/52 | 875 s | 60 | 0 | ok |
| dsh-qwen35q4ks-02 | dsh | Qwen3.6 35B-A3B, b9716 | complete | 127/132 96.2 % | 52/52 | 1479 s | 113 | 1 | ok |
| dsh-qwen35q4ks-01 | dsh | Qwen3.6 35B-A3B, b9716 | crash | 5/132 3.8 % | 14/52 | 577 s | 31 | 0 | ✗ |
| pi-qwen35q4ks-01 | pi | Qwen3.6 35B-A3B, b9716 | no engagement | 0/132 | 3/52 | 5 s | 0 | 0 | ok |
| pi-qwen35q4ks-02 | pi | Qwen3.6 35B-A3B, b9716 | no engagement | 0/132 | 3/52 | 5 s | 0 | 0 | ok |
| pi-oss20b-02 | pi | gpt-oss-20b | timeout | **108/132 81.8 %** | 52/52 | 1801 s | 64 | 1 | ✗ |
| pi-oss20b-03 | pi | gpt-oss-20b | declared done | 14/132 10.6 % | 28/52 | 141 s | 54 | 0 | ✗ |
| pi-oss20b-01 | pi | gpt-oss-20b | timeout | 0/132 | 3/52 | 1802 s | 21 | 0 | ok |
| dsh-oss20b-01 | dsh | gpt-oss-20b | malformed tool calls | 0/132 | 3/52 | 19 s | 18 | 0 | ok |
| dsh-oss20b-02 | dsh | gpt-oss-20b | malformed tool calls | 0/132 | 3/52 | 12 s | 8 | 0 | ok |
| dsh-oss20b-03 | dsh | gpt-oss-20b | malformed tool calls | 0/132 | 3/52 | 10 s | 6 | 0 | ok |
| dsh-glm47flash-01 | dsh | GLM-4.7-Flash | timeout | 86/132 65.1 % | 49/52 | 1801 s | 198 | 0 | ✗ |
| pi-glm47flash-02 | pi | GLM-4.7-Flash | timeout | 72/132 54.5 % | 51/52 | 1900 s | 174 | 1 | ✗ |
| pi-glm47flash-03 | pi | GLM-4.7-Flash | timeout | 45/132 34.1 % | 38/52 | 1801 s | 115 | 1 | ✗ |
| dsh-glm47flash-02 | dsh | GLM-4.7-Flash | declared done | 31/132 23.5 % | 36/52 | 933 s | 96 | 0 | ✗ |
| dsh-glm47flash-03 | dsh | GLM-4.7-Flash | timeout | 1/132 0.8 % | 14/52 | 1802 s | 72 | 0 | ✗ |
| pi-glm47flash-01 | pi | GLM-4.7-Flash | hang | 0/132 | 0/52 | 1801 s | 54 | 0 | ✗ |

`3/52 visible` is the **stub baseline** — three tests assert pre-declared constants and pass
against unimplemented code. Every Qwen typecheck is "clean" only because **the seed was never
modified**.

Not in the table: **17 pi runs that aborted in 3–16 s** on a llama-server parse error (§4.3).
The two 5 s Qwen3.6 pi runs are in it, because their cause is now known (§3.6).

Every Qwen3.6, gpt-oss and GLM run is on the 5070 + 3060; every other local run is on the 5070
alone. gpt-oss and GLM ran on llama.cpp b10751 only.

---

## 3. Model findings

### 3.1 Claude (ceiling probe) — 129/132, 322 s

Establishes the task is achievable and the hidden suite is not adversarial. Overfit gap **+2.3
points** (visible 100 % vs hidden 97.7 %) — it implemented the spec rather than the tests.

### 3.2 Gemma 4 12B — plateaus at ~75 %, overfits ~10× more

Best runs: pi-03 **75.8 %**, dsh-03 **74.2 %** — effectively tied, both below the bar.

**The most robust finding in the whole study is the overfit gap:**

| | visible | hidden | gap |
|---|---|---|---|
| Claude | 100 % | 97.7 % | **+2.3** |
| Gemma pi-03 | 100 % | 75.8 % | **+24.2** |
| Gemma dsh-03 | 98.1 % | 74.2 % | **+23.8** |
| Gemma dsh-02 | 84.6 % | 50.0 % | **+34.6** |
| Gemma pi-01 | 67.3 % | 27.3 % | **+40.0** |

pi-03 scored a perfect 52/52 visible and only 100/132 hidden. **Under visible-only scoring it
would look flawless.** The split-suite design (decision Q11) is what exposes this; without it the
entire study would have reported a false result.

No Gemma run produced a clean typecheck. The ceiling probe did.

### 3.3 Qwen3.8-27B UD-Q2_K_XL — 0/132, five configurations

The model is **fast and fits comfortably**: 9.15 GiB, **45.7 t/s** decode, ~1 100 t/s prefill,
arch `qwen35`, 65 blocks, 4 KV heads (6:1 GQA). Speed was never the constraint.

It also never wrote a single line of code, in any configuration:

| # | context | maxTokens | reasoning channel | pi | dsh | writes |
|---|---|---|---|---|---|---|
| 1 | 32 k | 4096 | inline | declared done | timeout | **0** |
| 2 | 32 k | 8192 | inline | all aborted | — | **0** |
| 3 | 48 k | 4096 | inline | declared done | crash | **0** |
| 4 | 48 k | 16384 | inline | declared done | crash | **0** |
| 5 | 48 k | 16384 | **separate** | all 5 aborted | crash | **0** |

**Observed behaviour, identical in both harnesses:** exhaustive reading (42 reads, 10 bash), then
an extremely long design monologue that is never followed by action. pi's final turn, truncated
mid-sentence at 16 384 output tokens:

> `<think>…Let me carefully think through the design. **1.1 PRNG** — mulberry32 exactly as-is.
> **1.2 RNG streams:** Shop stream: seed ^ 0x51ED2701…`

dsh's final text, same shape: *"Let me think about the order of checks in `place`. The spec
section 3.1:"* — cut off.

Of pi's 57 tool calls in the longest run, **all were `read` or exploratory `bash`** (`ls`,
`cat items.json`, a `node -e` to inspect tiers). It never attempted a write by any route,
including shell redirection.

**Conclusion:** Qwen3.8-27B **at this quantization** does not transition from planning to
implementation on a spec of this size. Context window, output budget and reasoning channel have
all been controlled for and the behaviour is invariant.

**Not established:** whether this is caused by the model itself or by prompt shape. There is no
upward control — Q4_K_M (17 GB) does not fit this card.

### 3.4 Qwen3.8-27B UD-IQ2_XXS @ 131 072 — the downward control, also 0/132

Run as the experiment §7.1 called for: the *smaller* quant (6.77 GiB) at the **full 131 072
context, matching the Gemma baseline exactly**. This is the only Qwen configuration in which
dsh's compaction arithmetic is healthy — it retains ~16 777 tokens, more than SPEC.md's 10 302,
so for the first time the model could hold the entire spec across a compaction. It never needed
to: **0 compactions**, the run ended before one was due.

| | |
|---|---|
| tool calls | **24 — 23 `read`, 1 `bash`, 0 writes** |
| steps | 8 |
| compactions | 0 |
| visible / hidden | 3/52 (stub baseline) / **0/132** |
| wallclock | 568 s, exit 1 |
| decode | 50.8 t/s (vs 45.7 for Q2_K_XL) |

`diff.stat` is **0 bytes** — the seed was never touched, confirmed independently of the tool log.

**New failure mode.** Q2_K_XL produced a long but *forward-moving* design monologue. IQ2_XXS
produced a **verbatim degenerate loop**: the same six paragraphs repeated until the 16 384-token
budget cut it off mid-word.

> *"So the hidden suite probably tests shop rerolls across days. The shop stream has to be a
> persistent object… Hmm wait, actually — maybe the shop's Rng is held per run… OK, let me think
> about it from yet another angle…"* — then the identical text again, five times.

Exact repetition with no state change is a classic over-quantization signature, and it is the one
thing here that *is* attributable to the quant. But it changed no outcome.

**Conclusion — this settles §7.1: there is no quantization cliff between Q2_K_XL and IQ2_XXS.**
Halving the effective bit-width and quadrupling the context moved the score by zero. Both quants
read exhaustively and write nothing. **Every one of the seven scored Qwen runs lands on exactly
3/52 visible, 0/132 hidden, 0 writes** — across two harnesses, two quantizations and four context
sizes. That invariance is now the strongest single fact about this model on this task, and it
points away from quantization as the cause.

### 3.5 Gemma 4 12B, paired context arm — the null result that produced the best run

Six dsh runs, three at `contextWindow` 131072 and three at 196608. Same weights, same
`maxTokens` (8192), same 30-minute cap, same containerised scorer. Context was the only variable;
it moves dsh's post-compaction retention from 20 971 to 31 457 tokens against a 10 302-token spec.

| ctx | hidden counts | mean | best |
|---|---|---|---|
| 131072 | 34, 58, 95 | 62.3/132 (47.2 %) | 95 (72.0 %) |
| 196608 | 23, 98, **110** | 77.0/132 (58.3 %) | **110 (83.3 %)** |

**The context effect is not significant.** Exact two-sided Mann-Whitney on n=3 per arm gives
**p = 0.70**. The ranges are 61 and 87 tests wide and overlap almost completely; 196 k's worst run
(23) is below 131 k's worst (34). The +11.1-point mean difference is noise at this sample size.

**What the data does point at is survival.** Four of six runs exited early:

| wall clock | hidden |
|---|---|
| 343 s | 34 |
| 369 s | 23 |
| 687 s | 98 |
| 694 s | 58 |
| 1458 s | **110** |
| 1515 s | 95 |

In both arms the longest-surviving run scored best and the two shortest scored worst. Score tracks
**how long the run stayed alive**, not how much context it had. This is the third time in this
study that a structural hypothesis — context window, quantization depth, and now context again —
has failed to explain the variance, and the second time the 30-minute cap has turned out to be the
thing actually binding.

**The open question is no longer "does more context help" but "why do four runs in six die early".**

### 3.6 Qwen3.6-35B-A3B UD-Q4_K_S on two cards — six of six clear the bar

The model is a MoE: 256 experts with 8 active, full attention on only every 4th of 40 blocks with
2 KV heads, so KV is ~10.9 KB/token at q8_0. Its UD-Q4_K_XL (21.3 GiB) could not hold 64k context
across both cards under any placement tried; UD-Q4_K_S (19.45 GiB) serves 131072 with attention,
KV and the first 15 blocks' experts on the 5070 and the other 25 blocks' experts on the 3060.
~99 t/s decode on a short prompt, 57.6 t/s with 115k tokens filled. Layout, guards and the traps
found on the way are in MODELS.md §4.2.

**On llama.cpp b10751, n=3 per harness:**

| | runs | hidden | mean | wall |
|---|---|---|---|---|
| pi | 3 | 127, 125, 128 | **96.0 %** | 529, 944, 500 s |
| dsh | 3 | 124, 131, 127 | **96.5 %** | 875, 1801, 1228 s |

All six typecheck clean, none is tampered, visible is 52/52 every time, and the overfit gap runs
+0.8 to +6.1. Gemma's ran +16 to +40, so this is the finding of §3.2 turned round: the model
implements the spec rather than the visible tests, about as well as Claude did. Zero
`cudaMalloc failed` and zero parse errors across all six. One run timed out, still working, at
131/132; the other five ended on their own.

**Contamination is ruled out twice.** By date: the suite's first commit is 2026-09-21, and this
model's GGUFs were public by April 2026. By CANARY.md's check: asked three times to complete the
canary GUID, it produced nothing GUID-shaped.

**The same configuration on b9716 was a different result.** Four runs:

| run | outcome | hidden | what happened |
|---|---|---|---|
| pi-qwen35q4ks-01 | no engagement | 0/132 | 5 s — see below |
| pi-qwen35q4ks-02 | no engagement | 0/132 | 5 s, identical |
| dsh-qwen35q4ks-01 | crash | 5/132 | wrote 161 lines, then looped inside one `write()` until `maxTokens` |
| dsh-qwen35q4ks-02 | complete | 127/132 | the first local run above 95 % |

Both pi runs died on the model's opening `read` of SPEC.md, which came out with a doubled
`</parameter>`. llama-server logged `unparsed peg-native output`, dropped the call and aborted the
stream; pi treated that as fatal and exited 0. That is llama.cpp
[#24807](https://github.com/ggml-org/llama.cpp/issues/24807), fixed after b9754. On b10751 pi never
hit it. **A parser bug in the server, not the model and not the harness, is what produced pi's 0 %
on this configuration** — which is why these four runs are kept and grouped apart, not averaged in.

dsh-01's loop is the other open item. 32 768 tokens of the same two statements inside a file write,
472 s of a 577 s run, is MODELS.md §6's over-quantization signature. It did not recur in the three
b10751 dsh runs, so across five Qwen3.6 dsh runs it is one event, not a rate.

**What this does and does not say.** It says a MoE model at ~4 bits, on two consumer cards, does
this task reliably. It does not say Qwen is a better model than Gemma: the Qwen runs have ~6× the
throughput, twice the VRAM and a newer llama.cpp, and each of those moves the score on its own.
Nor does it answer §7.5 for Gemma. That no b10751 Qwen run exited early is weak evidence that
the harnesses alone do not cause early exits — weak, because a different model on different
hardware is exactly what §6.3 says not to compare across.

---

## 4. Harness findings (pi vs dsh)

### 4.1 On Gemma — inconclusive on score, robust on behaviour

Means (pi 34.3 %, dsh 41.4 %) are dominated by one catastrophic run each; spreads are 0–75.8 and
0–74.2. **Best-run is a dead heat (75.8 vs 74.2).** At n=3 there is no winner, and any ranking
would be noise.

What *is* robust is that they work differently, consistently:

| | pi | dsh |
|---|---|---|
| tool calls (3 Gemma runs) | **463** | **165** |
| compactions | **6** | **25** |
| failure mode | churns, hangs, times out | quits early or crashes |

pi makes ~2.8× more tool calls in smaller steps; dsh compacts ~4× more often, exactly as its
lower trigger (`0.8 × ctx` vs `ctx − 16384`) predicts. Decision Q14 — equalise bash timeout,
leave compaction as shipped — is what preserved this difference as a measurable signal.

> **Retracted: the compaction row, and the ~4× claim that rests on it.** Both figures came
> from the version-1 extractor, which counted compactions wrongly on both harnesses and
> wrongly *in different ways* (issue #10, fixed; see EVENTS.md §1–2). pi's count included
> every `compaction_end` as well as its `compaction_start`. dsh's was a substring test for
> the word "compact" against each serialised event, which swept in `compaction/end`,
> `compaction/summary`, the model-free `compaction/prune` pruner — a different mechanism
> entirely — and ordinary prose that happened to use the word.
>
> On the one capture where both could be measured against ground truth, pi was overstated
> 1.8× (11 counted, 6 real) and dsh 5.5× (11 counted, 2 real). Those factors cannot be
> applied to the runs above: the dsh factor in particular depends on how many prune events
> fired and how often the word appeared in that run's prose, neither of which is constant.
> And these runs' raw traces no longer exist (issue #7), so the real counts cannot be
> recovered at all.
>
> What survives: dsh's trigger *is* lower (`0.8 × ctx` vs `ctx − 16384`), so it should
> compact more often, and the mechanism argument in §4.2 stands on its own. What does not
> survive is the measurement — the error is larger on the dsh side, which is the side the
> claim needs. **“~4×” is not supported by anything now in this repo, and the tool-call
> row (463 vs 165) is the only quantitative half of this table still standing.**

### 4.2 pi's compaction is degenerate below a 32 k window

| pi setting | value |
|---|---|
| `reserveTokens` | 16384 → trigger = `contextWindow − 16384` |
| `keepRecentTokens` | 20000 |

At a 32 k window the trigger is 16 384 while it tries to retain 20 000 — **it cannot reduce
context below its own trigger.** At 48 k the trigger becomes 32 768 and the defect disappears.
A declared 16 k window would set the trigger to **zero**.

This is a real structural limit on running pi against small-context local models, and it is not
documented.

### 4.3 pi aborts on a single malformed response — 11 times

pi treats one unparseable model response as fatal, kills the run, and **exits 0** — which reads
as success to any wrapper that only checks exit codes.

The error originates in llama-server, not pi (`peg-native` appears nowhere in pi's code); pi
merely relays it. The response pi rejected was well-formed — `<think>` text plus a valid `read`
tool call.

| config | engaged | aborted |
|---|---|---|
| 32 k / 4096 inline | 2 | 0 |
| 32 k / 8192 inline | 0 | 2 |
| 48 k / 4096 inline | 1 | 3 |
| 48 k / 16384 inline | 1 | 1 |
| 48 k / 16384 **separate** | **0** | **5** |
| **total** | **4** | **11** |

The separate-reasoning column is suggestive (5/5, p≈0.08 against the ~60 % baseline) but **not
conclusive**. dsh survived every one of these responses.

---

### 3.7 gpt-oss-20b MXFP4 on two cards — one pass, and dsh cannot run it

Native MXFP4, 11.28 GiB, 131072 context declared (YaRN from 4096 — the "32k cap" this study
excluded it for was wrong). Layer split 65/35 toward the 5070: 119 t/s on a short prompt; with
98,931 tokens filled, 4315 t/s prompt but 42.6 t/s decode — a steeper fall with depth than
Qwen3.6's 99 → 60.7. Tool battery straight at the endpoint: 15/15.

| run | outcome | hidden | wall | what happened |
|---|---|---|---|---|
| pi-oss20b-02 | timeout | **108/132 81.8 %** | 1801 s | clears the bar, still working at the cap; gap +18.2 |
| pi-oss20b-03 | declared done | 14/132 | 141 s | declared done after 54 calls with 28/52 visible |
| pi-oss20b-01 | timeout | 0/132 | 1802 s | 21 calls, 0 writes — see below |
| dsh-oss20b-01..03 | malformed tool calls | 0/132 | 10-19 s | see below |

**dsh cannot run this model.** All three dsh runs ended inside 20 seconds, after 6-18 tool calls, on
the same error: the model emitted a garbled Harmony header —
`<|channel|>functions.read<|channel|>commentary to=assistant json` instead of
`<|channel|>commentary to=functions.read` — llama-server logged `unparsed peg-native output` and
errored the turn, and dsh exited 1. That is llama.cpp
[#27720](https://github.com/ggml-org/llama.cpp/issues/27720), which its maintainer closed as model
output no parser can recover, pointing at round-tripping the reasoning as the fix (a reporter
measured ~4 % of turns without it). **That fix does not explain dsh here**: dsh's stored history
carries the reasoning with the `reasoning_content` replay signature on 16 of 18 assistant messages,
and the bundled pi-ai library replays it. pi hit the error zero times in three runs. Why dsh hits
it every time is open.

**pi-oss20b-01 never ran out of ideas; it ran out of a shell.** Its 21st call,
`grep -R ... ..` from `/work`, walks the container's filesystem from `/` down, `/proc` included,
and never returned. The call carried `"timeout": 10000`; if pi reads that as seconds, it replaced
the 600 s cap this study set for fairness. Unconfirmed — pi's unit was not checked.

### 3.8 GLM-4.7-Flash UD-Q4_K_XL on two cards — works, writes, too slow

31.2B total, ~3B active, arch `deepseek2` in llama.cpp (MLA, 47 blocks). Its MLA cache is
~3.5 GiB at 131k — every block caches, where Qwen3.6 caches one in four — so the layout moves
fewer experts to the 5070: blocks 14-46's experts on the 3060, 1187 / 948 MiB free. ~92 t/s on a
short prompt; **with 98,735 tokens filled, 402 t/s prompt and 26.6 t/s decode**. Tool battery
14/15.

| | runs | hidden | mean | outcomes |
|---|---|---|---|---|
| pi | 3 | 0, 72, 45 | 29.5 % | hang, timeout, timeout |
| dsh | 3 | 86, 31, 1 | 29.8 % | timeout, declared done, timeout |

The format was never the problem: zero parse errors in six runs, 921-1954 lines inserted per run
across 9-11 files. **Five of six ran into the 30-minute cap still working**, and
the one that stopped early declared done at 31/132. The best, dsh-01 at 86/132, made 198 calls.
Every typecheck is dirty and the overfit gaps run +26 to +46 — Gemma's territory (§3.2), not
Qwen3.6's.

Z.ai reports SWE-bench Verified 59.2 and τ²-Bench 79.5 for this model, against gpt-oss-20b's 34.0
and 47.7. Here GLM's means (pi 29.5 %, dsh 29.8 %) sit level with gpt-oss's pi mean (30.8 %), and
gpt-oss has the only pass of the two. **At this speed on this hardware, the vendor ranking did not survive contact with a
30-minute long-horizon task** — which says as much about the cap as about the model, since at
~26 t/s at depth GLM gets under half the tokens Qwen3.6 does in the same half hour (README
limitation 3).

pi-glm47flash-01 wrote 1954 lines that never terminate under the test runner (`hang`); the scorer
took 14 minutes to give up on it. pi-glm47flash-02's 1900 s is container overhead around an
1800 s in-container run.

### 4.4 On Qwen3.6-35B-A3B — indistinguishable on score, pi faster

At n=3 each on b10751, pi's hidden range is 94.7-97.0 % and dsh's 93.9-99.2 %. They overlap; the
means differ by half a point. Tool calls are close too (pi 222, dsh 237 across three runs each) —
unlike Gemma's 463 against 165. The one consistent difference is wall clock: pi's runs took a
median 529 s, dsh's 1228 s, and dsh's slowest ran out the cap. Whether that is dsh's heavier
compaction or its longer steps is not separated by these runs.

pi's abort-on-parse-error (§4.3) is still pi's behaviour. What changed is that on b10751 nothing
triggered it.

### 4.5 Across the three two-card models — the harness mattered once, completely

| model | pi hidden | dsh hidden | where they differ |
|---|---|---|---|
| Qwen3.6-35B-A3B | 94.7-97.0 % | 93.9-99.2 % | pi faster (median 529 s vs 1228 s) |
| GLM-4.7-Flash | 0-54.5 % | 0.8-65.1 % | nowhere measurable |
| gpt-oss-20b | 0-81.8 % | 0 %, 0 %, 0 % | dsh fails on the model's Harmony output in <20 s |

On two models of three the harness makes no difference this study can see. On the third it makes
all of it, and not through context strategy — through how a harness survives one malformed turn.
pi relays llama-server's parse error as fatal (§4.3), and yet on gpt-oss it is dsh that never gets
past the first minute. **The failure mode that decides a harness comparison on a local model is
tool-format robustness, not compaction**, and it is model-specific.

## 5. Hardware and quantization

### 5.1 What fits on 12 GB

| quant | size | status |
|---|---|---|
| Q4_K_M | 17 GB | does not fit |
| UD-IQ3_S | 11.21 GiB | no room for KV |
| **UD-Q2_K_XL** | **9.15 GiB** | **tested — 48 k ctx, 45.7 t/s** |
| UD-IQ2_S | 7.80 GiB | untested |
| UD-IQ2_XXS | 6.77 GiB | **tested — 131 072 ctx, 50.8 t/s, 0/132** |
| UD-IQ1_M / IQ1_S | 6.27 / 5.77 GiB | **ruled out** — see below |

Measured context ceiling for Q2_K_XL (q4_0 KV):

```
32768  serves OK          40960  serves OK  (605 MiB free)
49152  serves OK  <-- ceiling (421 MiB free)
65536  LOADS, then CUDA-OOM on first inference
98304  OOM at load
```

**Loading is not the gate.** A context size can load and then OOM when the cuBLAS workspace is
allocated on the first request. Always smoke-test inference.

### 5.2 Published quality data

Independent KLD benchmark vs a Q8_0 reference (PPL 6.7500):

| quant | PPL | vs Q8 | mean KLD | top-1 agreement |
|---|---|---|---|---|
| UD-IQ2_XXS | 7.6528 | 1.13× | 0.146 | **82.98 %** |
| bartowski IQ2_XXS | 8.5352 | 1.26× | 0.301 | 76.53 % |
| stock IQ1_M | 12.0328 | 1.78× | 0.659 | 65.51 % |
| stock IQ1_S | 14.9721 | **2.22×** | 0.896 | 60.49 % |

IQ1 is ruled out on two independent sources (this table, plus Quesma measuring UD-IQ1_S at
~random chance on GPQA Diamond). *Caveat:* that benchmark lists UD-IQ2_XXS at 8.39 GiB; the
current file is 6.77 GiB, so it describes a different revision — treat the figures as indicative.

Quesma's agentic benchmark found UD-Q2_K_XL at **~74 % Terminal-Bench 2.1 vs ~76 % BF16** —
within noise at n=89. That is *not* what we observed here, but Terminal-Bench tasks are short and
this task is long-horizon.

### 5.3 Two cards: what the RTX 3060 buys

For a dense model that fits one card, nothing: a layer split runs at the 3060's pace (Gemma 12B
73.0 t/s alone, 50.9 at 50/50, 58.4 at 3:1). For a MoE, the difference between not fitting and
99 t/s at 131k — provided no expert lands on the CPU, where four blocks' worth halves decode. The
measurements are in MODELS.md §4.2.

### 5.4 llama-server configuration traps

- **`n_parallel` defaults to 4** — allocates 4× KV *and* 4× the recurrent-state cache for the
  Gated DeltaNet layers. `--parallel 1` is mandatory on 12 GB. This is why `llama-bench` numbers
  did not transfer to the server.
- **`--batch-size 2048` + `--no-mmap`** cost several hundred MiB of compute buffer.
- **`--reasoning-budget 0`** force-injects an end-of-thinking sequence that breaks llama-server's
  own peg-native grammar.
- **Desktop VRAM is a real budget line.** Discord alone grew to 699 MiB and was the difference
  between 32 k and not loading at all.
- **The llama.cpp build is part of the configuration.** It moved Qwen3.6 on pi from 0 % to 96 %
  (§3.6). No run before `examples/launch-qwen35moe.sh` recorded its build, including every
  Qwen3.8 and Gemma run in this report.

---

## 6. Methodology failures

Recorded because they affect how much weight the numbers above can carry.

### 6.1 Orphaned test workers — 4 cores for 6–7 hours (fixed)

`score.py`'s timeout path killed the Python child but never reaped the vitest worker tree. Four
orphan trees (~85 processes) from the `pi-02` hang burned **399.6 % CPU** across the Gemma
re-scoring *and* every Qwen run.

- Does **not** affect the 0/132 conclusion (that follows from tool-call traces, not timing).
- **Does** taint wall-clock figures. `dsh-qwen32k-01`'s 1801 s timeout ran on a machine with half
  its cores stolen.

**Fixed:** `sh()` now uses `Popen(start_new_session=True)` and reaps the process group on both
the timeout and normal-exit paths. Verified differentially — new code leaves 0 orphans, old code
leaves 2 on the same probe.

### 6.2 Denominator inflation (fixed earlier)

When agent code breaks a module, vitest fails to *collect* the importing test files and they
vanish from its own total, silently inflating the pass rate. pi-01 recorded 36/97 hidden against
a real suite of 132 — 37.1 % reported vs 27.3 % true. Fixed with canonical denominators computed
against the pristine seed, plus an `uncollected` field.

### 6.3 Cross-batch comparability

Four variables changed between the Gemma and Qwen batches — context (131 k → 48 k), KV precision
(q8 → q4), weights (Q4_K_XL → Q2_K_XL), reasoning (none → hybrid). **Cross-batch harness
comparison is invalid.** Within-batch comparisons are clean.

### 6.4 Wrong root causes asserted during the investigation

Stated as root cause and later disproved by experiment:

1. **"Context window is the binding constraint."** 48 k fixed the compaction thrash (dsh 25 → 2
   compactions, no more timeouts) and changed the score by **zero**.
2. **"The pi abort is a reproducible `maxTokens` dependency."** Over-fitted to four samples; the
   48 k/4096 run aborted at the value that had previously worked. It is intermittent.

Both were stated with more confidence than the evidence supported. The corrected position is in
§3.3: context and output budget are *contributing* factors, neither is causal.

---

## 7. What would actually settle the open question

1. ~~**Run UD-IQ2_XXS** as a downward control.~~ **Done — §3.4. It wrote nothing. The failure is
   not a quantization cliff between Q2 and IQ2.**
2. **Find an upward control.** Q4_K_M needs ~17 GB and does not fit one card. The 3060 is now
   installed, so UD-Q4_K_M (15.33 GiB) split across both is possible — at roughly 20 t/s by
   extrapolation from Q2_K_XL's split rate, not measured. **Not yet run, so "is this Q2's fault?"
   is still open.** Rerun on a llama.cpp after b9754 either way: the Qwen3.8 runs were made on a
   build nothing recorded, and §3.6 shows a build alone can decide a pi result.
3. **Shorten the spec** to test whether deliberation length scales with spec size.
4. ~~**Re-run the Gemma baseline** with the fixed scorer.~~ **Done — §3.5**, six runs under the
   containerised scorer. It produced the first local run to clear the bar and a null result on
   context.
5. **Find out why four Gemma runs in six exit early.** This is the largest source of variance in
   the Gemma data. `exit=1` before 700 s, with the agent mid-task, is not yet explained. §3.6 is
   one data point against: none of six Qwen runs on b10751 exited early under the same harnesses.
6. ~~**Run a second model on the two-card setup.**~~ **Done — §3.7, §3.8.** gpt-oss-20b 1/6,
   GLM-4.7-Flash 0/6 on identical hardware, build and configs. The Qwen3.6 result is the model's.
7. **Why dsh fails on gpt-oss every time and pi never does.** Not the reasoning round-trip #27720
   points at (§3.7). Capturing the request bodies dsh sends on its failing turn, against pi's on
   the same turn, would separate prompt shape from sampling.
8. **Give GLM-4.7-Flash a clock it can use.** Five of six runs hit the cap at ~26 t/s. A 60-minute
   rerun, labelled apart, would say whether it is slow or incapable; MTP speculative decoding (its
   GGUF carries the head) might close part of the gap first.

Until (2) exists, the honest statement is: **Qwen3.8-27B does not perform this task on one card at
either quantization tested, and the IQ2_XXS control makes quantization the *less* likely
explanation — but it cannot be ruled out, because both tested quants are 2-bit, and a llama.cpp
parser bug of the §3.6 kind cannot be ruled out either, because nothing recorded the build.**

**Qwen3.6-35B-A3B does perform it**, six times out of six, on two cards and a current llama.cpp.
That is the strongest positive result in this report and it rests on n=3 per harness. Two other
MoEs on the same setup managed one pass in twelve runs between them, so the result is the model's —
with the caveat that "the model" includes its speed, and that one of the two comparisons was
decided by a harness that could not run the other model at all.
