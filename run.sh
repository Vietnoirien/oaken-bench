#!/bin/bash
# Run one benchmark trial in an isolated container.
#   ./run.sh <pi|dsh> <model-id> <label> [timeout-seconds]
#
# Requires llama-server listening on the docker bridge:
#   scripts/ctxprobe.sh, then your own launcher -- see MODELS.md
set -euo pipefail

HARNESS="${1:?usage: run.sh <pi|dsh> <model-id> <label> [timeout]}"
MODEL="${2:?model id required}"
LABEL="${3:?label required}"
TIMEOUT="${4:-1800}"

B="$(cd "$(dirname "$0")" && pwd)"
OUT="$B/results/$LABEL"

if [ -e "$OUT" ]; then echo "refusing to overwrite existing result: $OUT" >&2; exit 1; fi
mkdir -p "$OUT"

if ! curl -s -m 5 http://172.17.0.1:8080/v1/models >/dev/null; then
  echo "llama-server not reachable on 172.17.0.1:8080 -- start it with HOST=172.17.0.1" >&2
  exit 1
fi

echo "=== $LABEL : $HARNESS / $MODEL / timeout ${TIMEOUT}s ==="
START=$(date -u +%s)

docker run --rm \
  --name "bench-$LABEL" \
  --add-host=llama:host-gateway \
  --dns 0.0.0.0 \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  -v "$OUT:/out" \
  oaken-bench:1.0 "$HARNESS" "$MODEL" "$TIMEOUT" 2>&1 | tee "$OUT/docker.log"

END=$(date -u +%s)
echo "$((END - START))" > "$OUT/wallclock.seconds"
echo "=== $LABEL finished in $((END - START))s ==="
