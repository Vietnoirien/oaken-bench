#!/bin/bash
# Six graded Gemma runs, alternating harnesses so an early stop leaves
# balanced data. Each run is scored immediately after it finishes.
set -u
B="$(cd "$(dirname "$0")/.." && pwd)"
MODEL=gemma-4-12B-it-qat-UD-Q4_K_XL.gguf
LOG="$B/results/batch.log"
: > "$LOG"

for i in 1 2 3; do
  for H in pi dsh; do
    LABEL="$H-0$i"
    echo "[$(date '+%H:%M:%S')] === starting $LABEL ===" | tee -a "$LOG"
    "$B/run.sh" "$H" "$MODEL" "$LABEL" 1800 >>"$LOG" 2>&1
    echo "[$(date '+%H:%M:%S')] === scoring $LABEL ===" | tee -a "$LOG"
    python3 "$B/scripts/score.py" "$B/results/$LABEL" >>"$LOG" 2>&1
    echo "[$(date '+%H:%M:%S')] === done $LABEL ===" | tee -a "$LOG"
  done
done
echo "[$(date '+%H:%M:%S')] BATCH COMPLETE" | tee -a "$LOG"
