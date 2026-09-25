# T5 visible bot simulator

Write `decide(state) -> actions[]`, play it against the reference engine, and
measure against the three public baselines. The contract is [SPEC.md](SPEC.md).
The author exclusion for the next oracle is in [AUTHORSHIP.md](AUTHORSHIP.md).

These are visible development scores. They do not estimate the held-out
snapshot-pool score planned in issue #40. All original T2 assets and published
results keep their existing meaning.

## Run locally

Use Node 24+ with TypeScript support, Bash, GNU coreutils, OpenSSL and tar.
The launcher stages only the encrypted reference engine in temporary storage.
There is no npm install, GPU, network access or held-out test execution.

```bash
# Optional: this machine's /usr/bin/node lacks TypeScript support.
export OAKEN_T5_NODE=/home/viet/.nvm/versions/node/v24.14.1/bin/node

# Run from the repository root.
sha256sum -c t5/FROZEN.sha256
t5/bin/t5-sim selftest
t5/bin/t5-sim test
t5/bin/t5-sim play --bot baseline:cheapest --vs baseline:merger --json
OAKEN_T5_SIM_LOG=off t5/bin/t5-sim matrix --out /tmp/t5-matrix.json
cmp t5/baselines.visible.json /tmp/t5-matrix.json
```

Byte comparison uses the recorded Node version, v24.14.1. Other runtimes are
identified in JSON and need revalidation. A custom strategy can be a `.mjs`
file exporting `decide`, or a `.ts` file using only erasable TypeScript.
Give each strategy its own directory so its tree digest is useful.

The launcher checks budget overruns and returns a failing exit status with
no score. A never-returning bot hits the outer 120-second timeout. This local
runner is for cooperative bots; its purity checks do not isolate malicious
code. See SPEC 4.4-4.5 before using it for evaluation.

Successful play/matrix commands write `.t5-sim-log.jsonl` in your working
directory. Keep this development log untracked, or use `OAKEN_T5_SIM_LOG=off`.
The log contains timestamps; deterministic JSON scores do not.

## Recorded baseline result

[baselines.visible.json](baselines.visible.json) records 100 visible seeds,
1 through 100, with both seat assignments. Each cell uses 200 matches.
The six evaluated pairs, including self-play, total 1,200 matches.
Row bot's win rate, with draws worth half:

| Row bot | Random | Cheapest | Merger |
|---|---:|---:|---:|
| Random | 50% | 0.75% | 0.5% |
| Cheapest | 99.25% | 50% | 57% |
| Merger | 99.5% | 43% | 50% |

All three produced zero illegal actions, malformed returns, decision errors,
dropped actions and detected impurity. The stronger heuristic does not have
to beat the simpler one: the merger prioritizes copies over cheap tower
coverage, never rerolls, and can sell useful items to complete a triple.
No baseline was retuned after seeing this matrix.

Self-play is 50% by the paired construction; it is not a strength estimate.
The score stops when either run ends and compares status/trophies/lives,
so it is not the fraction of ten-trophy runs completed. The artifact records
that separate count. Rates have a ceiling of 100%; more seeds add resolution.
The two seat outcomes per seed are dependent, and this visible fixture makes
no population-confidence claim. Issue #40 owns held-out uncertainty reporting.

## Validation

The Node test suite checks transactional rejected actions, a shared shop
stream through rerolls and freezes, daily caps, malformed batches, state
copies, baseline rules, snapshot forfeits, paired scoring, fresh-process
byte equality, audit logs, timeouts and immediate voiding of slow decisions
and purity probes. It also verifies the frozen inputs and recorded matrix.

```bash
# Optional development tools, already installed on the validation host.
tsc -p t5/tsconfig.json --typeRoots /usr/share/nodejs/@types
sha256sum -c FROZEN.sha256
```

Type checking targets the public engine interfaces in `seed/src`, not decrypted
reference source. Selftest compares the two day-start paths over 218 days on
seeds 1-20. Tests use only visible fixtures and the sealed reference engine.

## Layout

- `sim/`: contract types, action adapter, match loop, paired scorer, CLI,
  module aliases and accidental nondeterminism checks.
- `bots/`: random, cheapest and merger baseline modules.
- `bin/t5-sim`: disposable engine staging and process timeout.
- `tests/simulator.test.ts`: visible regression tests.
- `seeds.visible.json`: frozen development seeds.
- `FROZEN.sha256`: contract, simulator, baselines, seeds and dependency hashes.
- `AUTHORSHIP.md`: recovered work and the oracle author exclusion.

`results/t5-*` dispatch, sealed seeds/pool and final scoring belong to #40.
Published trace-derived behavior metrics belong to #41. This patch only adds
`t5/`; it requires the #36/#37 base commits named in AUTHORSHIP.md.
