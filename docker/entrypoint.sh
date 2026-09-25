#!/bin/bash
# Usage: entrypoint.sh <harness: pi|dsh> <model-id> <timeout-seconds>
#        entrypoint.sh score [--detail]        (scoring pass; see scorer.sh)
set -u

# Scoring runs in this same image so that node and vitest are pinned, the
# held-out suite can be decrypted without ever touching the host filesystem,
# and a non-terminating test tree is reaped by container teardown.
if [ "${1:-}" = "score" ]; then shift; exec /usr/local/bin/scorer.sh "$@"; fi

HARNESS="$1"; MODEL="$2"; TIMEOUT="${3:-3600}"
OUT=/out

mkdir -p "$OUT"
rm -rf /work && cp -r /opt/seed /work && cd /work

# The host-side port/URL override must also reach both harnesses' copied
# configs. The default stays llama:8080 for existing model registrations.
OAKEN_SERVER_URL="${OAKEN_SERVER_URL:-http://llama:8080/v1}" \
  python3 /usr/local/bin/configure_server.py /root || {
    echo "server endpoint configuration failed; refusing to start the harness" >&2
    exit 78
  }

# Baseline commit so the run's diff is recoverable at scoring time.
git init -q . && git add -A && git -c user.email=b@x -c user.name=bench commit -qm baseline

# A claude-* model id selects the hosted Anthropic provider (ceiling probe);
# anything else is the local llama-server.
case "$MODEL" in
  claude-*) PROVIDER=anthropic ;;
  *)        PROVIDER=local-llama ;;
esac

# dsh picks provider+model from settings.yaml; rewrite both for this run.
sed -i "s#^  provider: .*#  provider: ${PROVIDER}#" /root/.dsh/settings.yaml
sed -i "s#^  model: .*#  model: ${MODEL}#" /root/.dsh/settings.yaml

echo "harness=$HARNESS model=$MODEL timeout=$TIMEOUT" > "$OUT/run.meta"
date -u +%s > "$OUT/start.epoch"

PROMPT="$(cat /opt/PROMPT.txt)"
set +e
case "$HARNESS" in
  pi)
    if [ "$PROVIDER" = anthropic ]; then
      PI_AUTH=()
    else
      PI_AUTH=(--api-key local)
    fi
    timeout -s TERM "$TIMEOUT" \
      pi --provider "$PROVIDER" --model "$MODEL" "${PI_AUTH[@]}" \
         -p --mode json "$PROMPT" </dev/null \
         >"$OUT/pi-events.jsonl" 2>"$OUT/stderr.log"
    ;;
  dsh)
    LOCAL_LLAMA_KEY=local timeout -s TERM "$TIMEOUT" \
      dsh --profile headless "$PROMPT" </dev/null \
         >"$OUT/dsh-stdout.log" 2>"$OUT/stderr.log"
    ;;
  *) echo "unknown harness: $HARNESS" >&2; exit 64 ;;
esac
RC=$?
set -e

date -u +%s > "$OUT/end.epoch"
echo "$RC" > "$OUT/exit.code"

# Preserve the workspace and the run's diff for scoring.
git -C /work add -A 2>/dev/null || true
git -C /work diff --cached --stat > "$OUT/diff.stat" 2>/dev/null || true
tar czf "$OUT/workspace.tgz" -C / --exclude='work/node_modules' --exclude='work/.git' work

# dsh session logs live under ~/.dsh/sessions
tar czf "$OUT/dsh-sessions.tgz" -C /root .dsh/sessions 2>/dev/null || true
tar czf "$OUT/pi-sessions.tgz"  -C /root .pi/agent/sessions 2>/dev/null || true

if [ -n "${HOST_UID:-}" ]; then chown -R "${HOST_UID}:${HOST_GID:-$HOST_UID}" "$OUT" || true; fi
echo "exit=$RC"
exit "$RC"
