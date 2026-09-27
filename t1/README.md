# T1 protocol v1

Status on 2026-09-27: package and scorer controls passed, and the pilot and
all 72 matrix trials completed. See [the results](RESULTS.md). The maintainer
authorized the full study on #45 and #46; this replaces the earlier deferral
in `docs/t1-decision-options.md`.

## Question and primary result

T1 measures whether Gemma 4 12B can implement each v1.0 TypeScript module when
the other modules are complete. Variation across three independent trials per
module and mode is part of the result. The pi/dsh comparison is secondary.

The primary score is passing tests in the held-out file with the same module
name. Report the numerator and fixed denominator, not only a percentage. A
test in `run.test.ts` may also exercise economy or combat, so this grouping is
an attribution by test file, not a claim that a test calls only one module.
Report 132-test full-suite pass count, typecheck, visible count, untouched-source
validation, wall time, tokens, and outcome next to the primary score. A missing
test file counts as uncollected and earns zero for those tests.
`moduleSuccess` requires all target tests, all 132 full-suite tests, a clean
typecheck, and no fixed-file mutation. The target count remains the primary
measure because a near pass can still reveal module capability.

| Missing module | Held-out test file | Tests |
|---|---|---:|
| combat | combat.test.ts | 35 |
| economy | economy.test.ts | 12 |
| items | items.test.ts | 15 |
| rng | rng.test.ts | 5 |
| run | run.test.ts | 12 |
| shop | shop.test.ts | 15 |
| snapshot | snapshot.test.ts | 24 |
| tower | tower.test.ts | 14 |

These counts total 132. The test-file names and counts are public. Individual
held-out test names and source remain sealed. `hidden-detail.json` is local and
gitignored; `score.json` contains only suite and file counts. The existing
`docker/score_detail.py` hashes individual hidden test names when detail is
requested. T1 does not publish those digests because file-level counts suffice
for the primary result.

## Package format

`scripts/t1.py assemble <module> <new-directory>` writes `workspace.tgz`,
`manifest.json`, and `PROMPT.txt`. The archive contains the frozen seed task and
seven reference implementations. The target file remains the frozen seed stub.
`index.ts` and `types.ts` also come from the reference engine. The archive never
contains the held-out suite or the omitted reference implementation. Generated
packages, direct replies, and model workspaces stay out of Git. The manifest
records bundle digests and hashes of every fixed source file so the scorer can
reject edits outside the target module. It contains no reference source.

`scripts/t1_controls.py` restores each omitted module and scores the result in
the network-disabled Docker scorer. All eight controls passed clean typecheck,
52/52 visible, and 132/132 held-out on 2026-09-27. Counts are in
`control-results.json`. They do not constitute model trials.

## Predeclared trial matrix

Model: `gemma-ctx196k.gguf`, Gemma 4 12B QAT Q4_K_XL plus its MTP draft. The
pilot and first matrix segment used the RTX 5070. The context is
196608 and KV is q8_0. The initial 5070 decode smoke cleared the launcher's
90 t/s floor without a CUDA allocation failure. Record actual launcher flags,
build, GPU memory, and run image in `run-context.json` for every trial. The
Docker image pins pi 0.86.0 and dsh 0.1.5-rc.2. Native sampling stays on.

One pilot module, economy, runs once in direct, pi, and dsh, in that order.
Pilot runs have a 600-second cap and are not part of the matrix. Inspect only
counts, elapsed time, outcomes, archive presence, and canary status. If all
three paths work, set the matrix cap to the next multiple of 60 seconds above
1.5 times the longest pilot, clamped to 600–1200 seconds. Record that cap and
the three pilot durations before the first matrix trial. A pilot failure is a
protocol failure to fix and rerun before the matrix, not a zero capability
score. A model/harness incompatibility must be documented, not masked by
changing server flags between modes.

After `t1-snapshot-direct-01` finished, the maintainer requested the desktop
RTX 5070 for other work. The server moved to the RTX 3060 before
`t1-snapshot-pi-01`; `hardware-switch.json` records the boundary. The model,
alias, server flags, image, prompt, and 600-second cap stayed fixed. The 3060
is slower in the recorded short decode probes: the 5070 median over 38
harness probes was 102.62 tokens/s; the first four 3060 probes ranged from
41.03 to 42.97 tokens/s. These eight-token probes are indicative, not a
trial-level speed estimate. Analyze elapsed time and timeout rates by hardware
segment. Snapshot spans the switch, so cross-mode timing for that module is
confounded. The original pilot cap was measured on the 5070 and may be too
short for the 3060; report that limitation with any capped trials.

Then run eight modules × three modes × three trials, 72 scored trials. Direct
mode is one local `/v1/chat/completions` call using `scripts/direct.py` with the
same spec and supplied source. It receives 8192 output tokens and writes only
the target file. Pi and dsh use the same package, model endpoint, prompt,
served configuration, and wall-clock cap. Their tool loops and compaction are
the harness behaviors under measurement. Run modes in the same order within
each replicate, rotating that order over replicates: direct/pi/dsh,
pi/dsh/direct, dsh/direct/pi. Iterate modules in the table order. Do not retry
a scored timeout or model failure. An infrastructure failure can be repeated
with a new label, retaining and marking the original void.

Each trial gets a fresh package and result directory. Check both held-out and
reference canaries before the model sees the package. A matched or incomplete
canary check stops that trial. Direct mode accepts only a local endpoint; no
reference source goes to a hosted provider. Raw replies, traces, prompts,
workspaces, and private detail remain gitignored and are copied to
`$OAKEN_ARCHIVE/<label>/`. Keep this archive for later re-scoring.

Score with the current T1 scorer version 1 and the existing v1.0 suite identified
by `hidden.sha256`. Package source is identified by `refengine.sha256` and the
source hashes in the local manifest. Do not change frozen task files or the
oracle. Publish only counts, safe metrics, and digests. Summaries show all three
trials per module and mode, range and median target score, full-suite score,
wall time, failure modes, and raw archive completeness. Report direct first;
describe mode differences with n=3 and without pooling across modules.
