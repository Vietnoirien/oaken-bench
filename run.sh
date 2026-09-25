#!/bin/bash
# Run one benchmark trial in an isolated container.
#   ./run.sh <pi|dsh> <model-id> <label> [timeout-seconds]
#
# Requires llama-server listening on the docker bridge:
#   scripts/ctxprobe.sh, then your own launcher -- see MODELS.md
#
# OAKEN_IMAGE picks the image tag (default oaken-bench:1.0), same variable
# scripts/score.py already reads -- set it to a wip-<issue> tag when testing
# an unmerged image change, per AGENTS.md.
set -euo pipefail

HARNESS="${1:?usage: run.sh <pi|dsh> <model-id> <label> [timeout]}"
MODEL="${2:?model id required}"
LABEL="${3:?label required}"
TIMEOUT="${4:-1800}"

B="$(cd "$(dirname "$0")" && pwd)"
OUT="$B/results/$LABEL"

if [ -e "$OUT" ]; then echo "refusing to overwrite existing result: $OUT" >&2; exit 1; fi
mkdir -p "$OUT"

SERVER_PORT="${OAKEN_SERVER_PORT:-8080}"
if ! [[ "$SERVER_PORT" =~ ^[0-9]+$ ]] || [ "$SERVER_PORT" -lt 1 ] || [ "$SERVER_PORT" -gt 65535 ]; then
  echo "OAKEN_SERVER_PORT must be an integer from 1 to 65535" >&2
  exit 64
fi
SERVER_URL="${OAKEN_SERVER_URL:-http://172.17.0.1:$SERVER_PORT/v1}"
CONTAINER_SERVER_URL="${OAKEN_SERVER_URL:-http://llama:$SERVER_PORT/v1}"
if [[ "$SERVER_URL" != http://*/v1 && "$SERVER_URL" != https://*/v1 ]]; then
  echo "OAKEN_SERVER_URL must be an http(s) URL ending in /v1" >&2
  exit 64
fi
SERVER_ROOT="${SERVER_URL%/v1}"
if ! curl -s -m 5 "$SERVER_URL/models" >/dev/null; then
  echo "llama-server not reachable at $SERVER_URL" >&2
  exit 1
fi

# run.meta (docker/entrypoint.sh) records harness/model/timeout only -- see
# issue #14. Everything that decides whether two runs are actually
# comparable (llama-server's served config, the image's actual installed
# harness versions rather than the Dockerfile's pins, host GPUs) is only
# observable from the host, before the container starts, which is why this
# runs here rather than in the entrypoint. scripts/server_config.py is a
# library so #28's direct-mode runner (issue #26) can call the same
# function; a failed capture must not block the trial, hence `|| true` --
# a run is worth more than its provenance sidecar.
OAKEN_IMAGE="${OAKEN_IMAGE:-oaken-bench:1.0}"
python3 "$B/scripts/server_config.py" "$SERVER_ROOT" --image "$OAKEN_IMAGE" --model "$MODEL" \
  > "$OUT/run-context.json" 2>"$OUT/run-context.log" || \
  echo "warning: run-context.json capture failed -- see run-context.log" >&2

echo "=== $LABEL : $HARNESS / $MODEL / timeout ${TIMEOUT}s ==="
START=$(date -u +%s)

# `|| RC=$?` rather than a bare pipeline: under `set -e` with `pipefail`, a
# container that dies (OOM kill, image fault) would abort the script right
# here, and everything below -- wallclock, the archive -- would never run. A
# crashed run is a result, and issue #7 exists because exactly those runs are
# the ones whose traces get lost.
RC=0
RUN_START=$(date +%s)
docker run --rm \
  --name "bench-$LABEL" \
  --add-host=llama:host-gateway \
  --dns 0.0.0.0 \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  -e OAKEN_SERVER_URL="$CONTAINER_SERVER_URL" \
  -v "$OUT:/out" \
  "$OAKEN_IMAGE" "$HARNESS" "$MODEL" "$TIMEOUT" 2>&1 | tee "$OUT/docker.log" &
RUN_PID=$!
while jobs -pr | grep -qx "$RUN_PID"; do
  sleep 30
  ELAPSED=$(($(date +%s) - RUN_START))
  printf '[progress] %s %s\n' "$LABEL" "$(python3 "$B/scripts/run_progress.py" "$OUT" "$HARNESS" "$ELAPSED")"
done
wait "$RUN_PID" || RC=$?

END=$(date -u +%s)
echo "$((END - START))" > "$OUT/wallclock.seconds"
echo "=== $LABEL finished in $((END - START))s (docker rc=$RC) ==="

# results/<label>/ is gitignored wholesale and looks disposable, but it's the
# only copy of the raw trace (pi-events.jsonl, dsh-sessions.tgz, stderr.log,
# ...) that #1-#6's metrics are derived from. Copy it out to a durable,
# non-repo home so it survives a `git clean` -- see issue #7. This must run
# after wallclock.seconds is written so the archive is complete.
ARCHIVE="${OAKEN_ARCHIVE:-$HOME/.cache/oaken-bench}/$LABEL"
if mkdir -p "$ARCHIVE" && cp -r "$OUT/." "$ARCHIVE/"; then
  echo "raw trace archived to $ARCHIVE"
else
  echo "warning: failed to archive raw trace to $ARCHIVE -- results/$LABEL is the only copy" >&2
fi

# The container's own exit status is the run's, so surface it rather than
# reporting success because the archive worked.
exit "$RC"
