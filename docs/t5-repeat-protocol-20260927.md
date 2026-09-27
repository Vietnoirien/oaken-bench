# T5 repeat measurement

The 2026-09-26 pilot is a runner observation. Its T5 and reference-engine
canary checks happened after scoring. Do not count it as one of the repeats.

Run three new, independent Pi trials of the same Qwen3.6-35B-A3B UD-Q4_K_S
configuration. Keep `qwen35moe-desktop`, llama.cpp b10751 or a separately
reported build, 131072 context, q8_0 K/V, one parallel slot, the same Docker
image, and a 3600-second cap. Do not serve other model traffic during a trial.
Do not tune the prompt, bot, simulator, baseline pool, or sealed oracle between
trials. Use fresh labels so each run gets a fresh container and workspace.

1. Start `GGUF=/path/to/Qwen3.6-35B-A3B-UD-Q4_K_S.gguf
   examples/launch-dual.sh qwen35moe-desktop`. Its guards check memory, a
   real tool call and throughput.
   Confirm the server's build and alias before proceeding.
2. Run `OAKEN_T5_NODE=/home/viet/.nvm/versions/node/v24.14.1/bin/node ./bench.sh
   pi Qwen3.6-35B-A3B-UD-Q4_K_S.gguf t5-qwen35moe-desktop-pi-repeat-01 3600`,
   then repeat with `-02` and `-03`. `run.sh` checks both canaries before each
   container starts. A failed, incomplete or matching check stops that trial.
3. For each run, verify `canary-preflight.json` predates `start.epoch`, both
   checks have `matched: false`, `score.json` and `visible-score.json` exist,
   and the raw trace/workspace archive exists under `$OAKEN_ARCHIVE` (default
   `~/.cache/oaken-bench/<label>/`). Compare recorded image, prompt, frozen
   input, server configuration, oracle digests, and Node versions across runs.

Report all three sealed wins out of 144, the range and median, and the visible
rates separately by baseline. Include timeout/exit status, invalid snapshots,
illegal actions and failed decisions. The 48 seed clusters are fixed across
trials, so each score's seed bootstrap interval is not a confidence interval
for run-to-run variation. Three trials describe repeatability for this model
and configuration; they do not estimate a harness effect or a new-seed
population. Keep the pilot's 129/144 visible as context, with its canary-timing
limitation attached.
