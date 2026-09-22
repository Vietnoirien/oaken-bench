#!/bin/bash
# Paired dsh arm: Gemma 4 12B QAT at 131072 vs 196608 context.
# Same weights, same maxTokens (8192), same 30-min cap, same scorer.
# The ONLY variable is contextWindow, which moves dsh's post-compaction
# retention from 20971 to 31457 tokens (SPEC.md is 10302).
B="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
L="${GEMMA_LAUNCHER:-$B/examples/launch-gemma.sh}"
LOG="$B/results/batch-gemma-ctx.log"

kill_server() {
  local p
  p=$(ps -eo pid,args --no-headers | awk '/llama-server/ && /port 8080/ && !/awk/ {print $1; exit}')
  [ -n "$p" ] && { kill -TERM "$p" 2>/dev/null; sleep 6; kill -9 "$p" 2>/dev/null; sleep 4; }
}

{
  echo "=== gemma paired context arm $(date -uIs) ==="
  for ARM in 131k 192k; do
    case "$ARM" in
      131k) MODEL=gemma-4-12B-it-qat-UD-Q4_K_XL.gguf ;;
      192k) MODEL=gemma-ctx196k.gguf ;;
    esac
    kill_server
    echo "--- arm $ARM : starting server ---"
    if ! "$L" "$ARM"; then
      echo "!!! arm $ARM REFUSED by the launch guard -- skipping, no data produced"
      continue
    fi
    for n in 01 02 03; do
      lbl="dsh-gemma$ARM-$n"
      [ -e "$B/results/$lbl" ] && { echo "  $lbl exists, skipping"; continue; }
      "$B/run.sh" dsh "$MODEL" "$lbl" 1800 || true
      w=$(cat "$B/results/$lbl/wallclock.seconds" 2>/dev/null || echo 0)
      echo "  $lbl wallclock=${w}s"
      # Re-check the guard: a mid-batch fallback would silently halve throughput.
      if grep -q 'cudaMalloc failed' /tmp/gemma-server-*.log 2>/dev/null; then
        echo "!!! CUDA fallback detected during arm $ARM -- aborting arm"; break
      fi
    done
  done
  kill_server
  echo "=== runs done $(date -uIs), scoring ==="
  for lbl in $(ls "$B/results" | grep '^dsh-gemma'); do
    [ -f "$B/results/$lbl/score.json" ] && continue
    python3 "$B/scripts/score.py" "$B/results/$lbl" 2>&1 | sed -n '1,9p'
  done
  echo "=== all done $(date -uIs) ==="
} >> "$LOG" 2>&1
