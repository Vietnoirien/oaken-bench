# T5 Qwen repeat, 2026-09-27

Three fresh Pi runs completed the T5 bot task with Qwen3.6-35B-A3B
UD-Q4_K_S on the guarded `qwen35moe-desktop` server preset. All three passed
both canary checks before the model received the task. These runs show a
sealed-score spread of 125–134 wins out of 144. They do not compare harnesses.

## Sealed snapshot score

The `t5-sealed-snapshot/1` oracle uses 48 fixed seed clusters and 144 fixed
baseline snapshots. The score is the number of wins against those snapshots.

| Run | Wins | Win rate | 95% seed-cluster interval | Run wall time |
|---|---:|---:|---:|---:|
| [`-02`](../results/t5-qwen35moe-desktop-pi-repeat-02/score.json) | 134/144 | 93.06% | 86.11–98.61% | 1,236 s |
| [`-03`](../results/t5-qwen35moe-desktop-pi-repeat-03/score.json) | 125/144 | 86.81% | 79.17–93.75% | 2,023 s |
| [`-04`](../results/t5-qwen35moe-desktop-pi-repeat-04/score.json) | 126/144 | 87.50% | 79.17–94.44% | 1,872 s |

The range is 86.81–93.06%; the median is 87.50%. Every run finished with
container exit 0 before its 3,600-second cap. Each had zero invalid snapshots,
illegal actions, malformed returns, decision errors, dropped actions and
detected impurity. The intervals resample the fixed seed clusters within one
run. They do not describe variation across independent model runs.

## Visible live-bot comparisons

The visible `t5-visible-head-to-head/1` protocol plays each submitted bot
against three public baselines on 100 development seeds, both seats per seed.
Each cell below has 200 matches. A draw earns half a win.

| Run | Random | Cheapest | Merger |
|---|---:|---:|---:|
| [`-02`](../results/t5-qwen35moe-desktop-pi-repeat-02/visible-score.json) | 99.25% | 66% | 72% |
| [`-03`](../results/t5-qwen35moe-desktop-pi-repeat-03/visible-score.json) | 99.25% | 35% | 44% |
| [`-04`](../results/t5-qwen35moe-desktop-pi-repeat-04/visible-score.json) | 99.25% | 50% | 57% |

These rates are for live games against visible opponents. They measure a
different outcome from the sealed snapshot wins and must stay separate.
The `-04` bot drew all 200 matches against Cheapest; its 50% is draw points,
not 100 wins.

## Checks and provenance

The [`-02`](../results/t5-qwen35moe-desktop-pi-repeat-02/canary-preflight.json),
[`-03`](../results/t5-qwen35moe-desktop-pi-repeat-03/canary-preflight.json) and
[`-04`](../results/t5-qwen35moe-desktop-pi-repeat-04/canary-preflight.json)
preflight records are timestamped before their model starts.
Both the T5 oracle and reference-engine checks ended with `status: passed`
and no matching GUID digest. The first 1,024-token answer was truncated for
four of the six checks; each completed on its one 8,192-token retry. The other
two completed without a retry. These negative checks are evidence against
literal canary recall, not proof that the model never saw the assets.

All three scores record the same model, Pi harness, source revision
`fc14a947c55b7ce9c446fc162dbc8e2780b29f43`, prompt, frozen inputs,
oracle digests and image ID
`sha256:db70dc755e2d68747eabb681ebcd89fd1d46ecc443f114b44cb6698277e874f6`.
The server used llama.cpp package b10751, 131,072 context, q8_0 K/V, one
parallel slot, and the desktop split across the RTX 5070 and RTX 3060.
Sealed scoring used host Node v24.14.1; visible scoring used image Node
v24.21.0. For each run, the local and archived copies of the preflight,
workspace, Pi trace, server context and both scores matched byte for byte.
The complete raw artifacts are under
`~/.cache/oaken-bench/t5-qwen35moe-desktop-pi-repeat-0{2,3,4}/`.

## Limits

The same 48 sealed seeds and opponent pool were reused in all three runs.
Three trials give a first view of run-to-run variation for this one model and
server configuration. They do not estimate performance on new seeds, and
there is no harness comparison here. The [2026-09-26 pilot](t5-pilot-20260926.md)
remains separate because its canary checks happened after scoring. The
`repeat-01` attempt stopped at an incomplete canary preflight before Docker
started; it has no graded score and is not counted in this set.
