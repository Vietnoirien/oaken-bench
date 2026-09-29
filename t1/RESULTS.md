# T1 full study — 2026-09-27

## Scope and result

The full predeclared matrix completed: eight missing modules × direct, pi, and dsh × three independent trials = 72 scored trials. The separate economy pilot added three runs. Every trial has a raw workspace archive under `$OAKEN_ARCHIVE`; the published files contain counts, digests, and run metadata. All eight restored-reference controls passed 132/132 held-out tests, 52/52 visible tests, and typecheck. The model was local Gemma 4 12B QAT Q4_K_XL with its MTP draft. No hosted API was used.

The primary question is module capability and variation. A target score is the number of tests passed in the held-out file assigned to that module. This is a file-level attribution: tests can also exercise other modules. Full module success requires all target tests, all 132 held-out tests, a clean typecheck, and no fixed-file mutation. The trial triples below are in trial order; `success` counts full module successes, not just target-file passes.

| Module | Mode | Target passes, trials 1–3 | Success | Median wall time | GPU |
|---|---|---|---:|---:|---|
| combat (35) | direct | 0, 0, 13 | 0/3 | 75 s | 5070 |
| combat (35) | pi | 14, 15, 20 | 0/3 | 391 s | 5070 |
| combat (35) | dsh | 0, 7, 0 | 0/3 | 210 s | 5070 |
| economy (12) | direct | 12, 11, 12 | 2/3 | 32 s | 5070 |
| economy (12) | pi | 9, 12, 11 | 1/3 | 120 s | 5070 |
| economy (12) | dsh | 12, 11, 12 | 2/3 | 120 s | 5070 |
| items (15) | direct | 2, 2, 2 | 0/3 | 58 s | 5070 |
| items (15) | pi | 15, 15, 15 | 0/3 | 90 s | 5070 |
| items (15) | dsh | 15, 2, 15 | 0/3 | 120 s | 5070 |
| rng (5) | direct | 5, 5, 5 | 3/3 | 21 s | 5070 |
| rng (5) | pi | 5, 5, 5 | 3/3 | 60 s | 5070 |
| rng (5) | dsh | 5, 5, 5 | 3/3 | 60 s | 5070 |
| run (12) | direct | 12, 12, 0 | 0/3 | 67 s | 5070 |
| run (12) | pi | 12, 0, 12 | 0/3 | 151 s | 5070 |
| run (12) | dsh | 11, 12, 0 | 0/3 | 240 s | 5070 |
| shop (15) | direct | 15, 15, 15 | 3/3 | 51 s | 5070 |
| shop (15) | pi | 15, 15, 0 | 1/3 | 180 s | 5070 |
| shop (15) | dsh | 11, 15, 10 | 0/3 | 270 s | 5070 |
| snapshot (24) | direct | 0, 22, 24 | 1/3 | 90 s | mixed |
| snapshot (24) | pi | 24, 24, 24 | 3/3 | 180 s | 3060 |
| snapshot (24) | dsh | 20, 24, 24 | 2/3 | 271 s | 3060 |
| tower (14) | direct | 14, 14, 14 | 0/3 | 98 s | 3060 |
| tower (14) | pi | 14, 14, 14 | 1/3 | 271 s | 3060 |
| tower (14) | dsh | 14, 14, 14 | 2/3 | 301 s | 3060 |

Across all 72 trials, 27 met full module success. RNG succeeded in every mode (9/9). Combat had no full successes and low, variable target scores. Items often passed all 15 target tests via pi or dsh, but all such runs had dirty typechecks; direct stayed at 2/15. Run frequently passed its own 12 tests while missing a full-suite test or failing typecheck, and had no full successes. Shop direct succeeded in all three trials. Snapshot recovered after a failed first direct attempt, while pi was 3/3 and dsh 2/3. Tower passed all 14 target tests and all 132 tests in every trial, yet only three trials typechecked cleanly. These distinctions matter more than a single pooled score.

## Time, model work, and hardware

The 72 matrix trials used 11,346 seconds (3 h 9 min) of recorded trial wall time. Harness times include container startup; post-run scoring is excluded. This is local compute time, not an API bill. The model server used one GPU at a time. Recorded input and output tokens are below; harness cache-read accounting differs, so token totals are workload context rather than a comparable monetary cost.

| Mode | Trials | Wall time | Median trial | Recorded input | Recorded output |
|---|---:|---:|---:|---:|---:|
| direct | 24 | 1,427 s | 62 s | 373,092 | 139,078 |
| pi | 24 | 5,232 s | 165.5 s | 971,316 | 387,075 |
| dsh | 24 | 4,687 s | 210 s | 1,064,272 | 276,307 |

The pilot and first 55 matrix trials used the RTX 5070. After `t1-snapshot-direct-01` finished, the maintainer needed that desktop GPU; the server moved to the RTX 3060 before `t1-snapshot-pi-01`. The remaining 17 matrix trials used the 3060. The model, server flags, image, prompt, and 600-second cap stayed fixed. The median short decode probe on the 5070 was 102.62 tokens/s across 38 harness probes; the first four 3060 probes ranged from 41.03 to 42.97 tokens/s. Those eight-token probes show a substantial slowdown but do not estimate full-trial speed. The 5070 segment had median trial wall time 90 s; the 3060 segment had 210 s. Different modules were run on each card, so these trial medians are not a controlled GPU comparison. Snapshot direct also spans the hardware boundary.

The 600-second cap was set from the 5070 economy pilot (direct 49 s, pi 121 s, dsh 90 s). Three matrix trials ran past 500 s: combat pi trial 2 (573 s, 5070), snapshot pi trial 2 (572 s, 3060), and tower pi trial 2 (511 s, 3060). The slower 3060 may have made the original cap restrictive; no cap or trial was changed after the switch. Treat any speed or timeout comparison across hardware as confounded.

## Failure modes and interpretation

- Six trials were classified as crashes, concentrated in combat (four), with one each in items and run. Combat direct trial 1 exhausted its output budget without code; combat dsh trial 1 took no tool action. Three trials had incomplete suites, including the first snapshot direct trial whose target module did not import. These runs remain in the matrix.
- Passing a target file did not imply a clean package. Items, run, shop, and tower include target-complete trials with typecheck failures or full-suite regressions. In particular, all nine tower trials passed 132/132 hidden tests, but six had a dirty typecheck.
- Pi and dsh differences are secondary. Three trials per module and mode give a small variation sample, not a reliable harness ranking. Module difficulty, output budgets, agent behavior, and the mid-study GPU switch all affect observed outcomes. Do not pool the modes, merge T1 with other tiers, or interpret target-file counts as exclusive module coverage.

## Provenance and publication

The [T1 protocol](README.md) gives the package format, mapping, sampling order, cap rule, and contamination controls. `pilot.json` records the cap decision; `control-results.json` records all restored-reference controls; `hardware-switch.json` records the exact trial boundary; `study-results.json` contains all 72 count-only trial records and group summaries. Each published `results/t1-*/score.json` includes the source-bundle digests and scorer SHA-256. Trial-local `run-context.json` and archived raw traces record the served model, sampler, harness/image versions, launch command, and GPU configuration. The published JSON was scanned for source and raw tool arguments. Raw prompts, tool traces, model responses, workspaces, and individual held-out tests remain outside Git.
