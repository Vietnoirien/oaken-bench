#!/bin/bash
# One benchmark trial, scored. Issue #14: run.sh and scripts/score.py are two
# commands (scripts/batch.sh:14,16 shows the pairing by hand), and a run
# invoked without the second produces no score.json -- silently, since
# nothing fails, the run just never gets scored until someone remembers.
#   ./bench.sh <pi|dsh> <model-id> <label> [timeout-seconds]
set -euo pipefail

B="$(cd "$(dirname "$0")" && pwd)"

"$B/run.sh" "$@"
python3 "$B/scripts/score.py" "$B/results/${3:?label required}"
