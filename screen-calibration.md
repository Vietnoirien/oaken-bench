# Screen calibration record

Status: pending. No screen run was made for the four reference models in
this revision. `nvidia-smi` could not reach the driver in the working
environment, and the supervisor confirmed that the RTX 3060 is reserved for
another project. The RTX 5070 was using about 1.7 GiB. Do not infer
thresholds from the four older `toolbattery-results/` files. They used a
different probe set and contain no recall or abstention result.

## Fixed protocol to run when the hardware is available

Use one server and one screen run at a time. Keep seed 20260924, the 16k
depth, the T0 probe selection, and the token budgets in `scripts/screen.py`
fixed across all four models. Save each screen artefact and its server log.
The calibration set is:

| Model | Existing long-horizon T2 outcome | Launcher |
|---|---|---|
| Gemma 4 12B QAT Q4_K_XL | Wide spread, 0–83.3% across 14 runs; one run cleared 80% | `examples/launch-gemma.sh` on CUDA0 |
| gpt-oss-20b MXFP4 | One pass in six runs across both harnesses | `examples/launch-dual.sh oss20b` |
| GLM-4.7-Flash UD-Q4_K_XL | No pass in six runs across both harnesses | `examples/launch-dual.sh glm47flash` |
| Qwen3.6-35B-A3B UD-Q4_K_S | Six of six pass at 131k/q8_0 | `examples/launch-dual.sh qwen35moe` |

The launchers currently bind port 8080. Before running calibration, arrange
an isolated port such as 8082 in a reviewed launcher change, and confirm
that both GPUs are available for the dual presets. Never connect the screen
to the other project's server on 8080. The Gemma run needs only the 5070,
but still requires a visible driver and enough free VRAM. Preserve the
launcher guards and record any configuration change; otherwise the four
measurements are not comparable to their existing T2 runs.

For each model, run `python3 scripts/screen.py --model <served-alias>
--base-url http://172.17.0.1:8082/v1` and record `elapsedSeconds`. Run
Gemma at least once to check the under-15-minute criterion on a 12 GB-class
model. If run-to-run variance affects the proposed boundary, collect
repeats before setting a threshold. The four single runs are a minimum,
not enough by themselves to estimate variance.

## Threshold decision record to fill

| Metric | Gemma | gpt-oss | GLM | Qwen | Chosen minimum | Reason |
|---|---:|---:|---:|---:|---:|---|
| schemaAdherence | pending | pending | pending | pending | pending | pending |
| toolSelection | pending | pending | pending | pending | pending | pending |
| shortChainsDepth | pending | pending | pending | pending | pending | pending |
| refusal | pending | pending | pending | pending | pending | pending |
| recall | pending | pending | pending | pending | pending | pending |
| abstention | pending | pending | pending | pending | pending | pending |

Use the paired T2 outcomes to decide which errors justify a no-go. Explain
each chosen minimum from the observed values, including false rejects of
Gemma or gpt-oss and any false accepts of GLM. With four models, the screen
is a local triage rule, not an estimated probability of T2 success. Commit
the four screen artefacts, the filled table, and a new revision in
`screen-thresholds.json` together. Set `status` to `calibrated`, add their
paths to `calibrationEvidence`, and replace all six `null` minimums. Each
later screen artefact then records that exact revision and minimums.
