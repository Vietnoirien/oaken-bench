# oaken-bench — final report

**Task:** implement a deterministic PvP auto-battler engine from a 378-line normative spec
(`SPEC.md`, ~10 300 tokens) into 10 pre-declared TypeScript modules.
**Grading:** 52 visible tests (in-repo) + **132 hidden tests** (written blind by a Sonnet
sub-agent, never read by the implementing agent). **Bar: 80 % hidden.**
**Conditions:** sealed prompt, no human input, network off, container per run, 30-min cap.
**Hardware:** RTX 5070 12 GB, i9-9900KF, 128 GB RAM.

---

## 1. Headline

| | |
|---|---|
| **Task is solvable** | Claude reached **129/132 (97.7 %)** in **5 m 22 s** |
| **Best local model** | Gemma 4 12B, **110/132 (83.3 %)** — **clears the bar** |
| **Qwen3.8-27B @ Q2_K_XL** | **0/132 across 5 configurations** — never wrote a line |
| **Qwen3.8-27B @ IQ2_XXS** | **0/132** at full 131 k ctx — same behaviour, no quant cliff |
| **Runs clearing 80 %** | **2** (the Claude ceiling probe, and one Gemma run) |

The benchmark and its oracle are sound: a 97.7 % result in under six minutes proves the task is
well-specified and the hidden suite is fair.

**A local model has now cleared the bar.** `dsh-gemma192k-03` scored 110/132 (83.3 %) with a
clean typecheck, 52/52 visible and an overfit gap of +16.7 — the tightest of any local run. It is
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

`3/52 visible` is the **stub baseline** — three tests assert pre-declared constants and pass
against unimplemented code. Every Qwen typecheck is "clean" only because **the seed was never
modified**.

Not in the table: **17 pi runs that aborted in 3–16 s** on a llama-server parse error (§5).

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

### 5.3 llama-server configuration traps

- **`n_parallel` defaults to 4** — allocates 4× KV *and* 4× the recurrent-state cache for the
  Gated DeltaNet layers. `--parallel 1` is mandatory on 12 GB. This is why `llama-bench` numbers
  did not transfer to the server.
- **`--batch-size 2048` + `--no-mmap`** cost several hundred MiB of compute buffer.
- **`--reasoning-budget 0`** force-injects an end-of-thinking sequence that breaks llama-server's
  own peg-native grammar.
- **Desktop VRAM is a real budget line.** Discord alone grew to 699 MiB and was the difference
  between 32 k and not loading at all.

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
2. **Find an upward control.** Q4_K_M needs ~17 GB and does not fit. This requires either the
   spare 3060 (layer split, `--tensor-split 65,35`) or a different box. **Without it, "is this
   Q2's fault?" cannot be answered on this hardware.**
3. **Shorten the spec** to test whether deliberation length scales with spec size.
4. ~~**Re-run the Gemma baseline** with the fixed scorer.~~ **Done — §3.5**, six runs under the
   containerised scorer. It produced the first local run to clear the bar and a null result on
   context.
5. **Find out why four runs in six exit early.** This is now the largest source of variance in the
   study and the most likely route to a reliable pass, ahead of any model or context change.
   `exit=1` before 700 s, with the agent mid-task, is not yet explained.

Until (2) exists, the honest statement is: **Qwen3.8-27B does not perform this task on this
hardware at either quantization tested, and the IQ2_XXS control makes quantization the *less*
likely explanation — but it cannot be ruled out, because both tested quants are 2-bit.**
