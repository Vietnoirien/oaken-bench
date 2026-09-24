#!/usr/bin/env bash
# Qwen3.6-35B-A3B UD-Q4_K_S across RTX 5070 + RTX 3060 for oaken-bench.
#   QWEN_GGUF=/path/Qwen3.6-35B-A3B-UD-Q4_K_S.gguf ./launch-qwen35moe.sh
#
# Kept so the name the committed Qwen3.6 runs cite still works. The layout and
# every guard now live in launch-dual.sh's qwen35moe preset, unchanged; the copy
# of this script archived with each of those runs is the one they were served by.
set -u
GGUF=${QWEN_GGUF:?set QWEN_GGUF to the UD-Q4_K_S model path} \
  exec "$(dirname "$0")/launch-dual.sh" qwen35moe
