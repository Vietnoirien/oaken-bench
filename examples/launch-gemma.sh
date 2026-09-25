#!/usr/bin/env bash
# Gemma 4 12B QAT Q4_K_XL + MTP for oaken-bench.
#   ./launch-gemma.sh <131k|192k>
#
# MEASURED on RTX 5070 12GB, desktop ~1.0 GiB, q8_0 KV, MTP draft loaded:
#   131072  clean CUDA path  ~137 t/s  1560 MiB free
#   196608  clean CUDA path  ~147 t/s   646 MiB free
#   229376  clean CUDA path  ~142 t/s   156 MiB free   <- no safety margin
#   245760  cudaMalloc FAILS on a 769.78 MiB compute buffer, falls back to a
#   262144  degraded path: still answers, at HALF SPEED (~70 t/s).
#
# That fallback is the dangerous case: llama.cpp does not exit, it silently
# halves throughput. A benchmark run on it produces valid-looking, wrong data.
# With ~1.5 GiB of desktop use, the 192k preset needed a smaller inference
# batch: 512/256 served three archived DSH runs on 2026-09-25, with 337-460 MiB
# free on the 5070 and no CUDA allocation failure. Keep q8_0 KV for comparison
# with the earlier 192k runs. The 3060 is occupied by another project.
# This script refuses to hand over a degraded server.
set -u
M=${GEMMA_GGUF:?set GEMMA_GGUF to the model path}
D=${GEMMA_MTP:?set GEMMA_MTP to the MTP draft path}
PORT=${OAKEN_SERVER_PORT:-8082}
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "invalid OAKEN_SERVER_PORT: $PORT" >&2
  exit 64
fi
# A visible Vulkan device can still be selected for the draft model even when
# --device CUDA0 selects the main model. Hide the occupied second card from both.
export CUDA_VISIBLE_DEVICES=0
export GGML_VK_VISIBLE_DEVICES=0
case "${1:-131k}" in
  131k) CTX=131072; ALIAS=gemma-4-12B-it-qat-UD-Q4_K_XL.gguf; BATCH=2048; UBATCH=512; TPS_FLOOR=110 ;;
  192k) CTX=196608; ALIAS=gemma-ctx196k.gguf; BATCH=512; UBATCH=256; TPS_FLOOR=90 ;;
  *) echo "usage: $0 <131k|192k>" >&2; exit 64 ;;
esac
LOG=/tmp/gemma-server-$CTX-$PORT.log
LISTENERS=$(ss -ltn) || {
  echo "cannot check listening ports; refusing to start a server" >&2
  exit 1
}
if printf '%s\n' "$LISTENERS" | grep -Eq ":${PORT}([[:space:]]|$)"; then
  echo "port $PORT is already listening; refusing to probe or replace it" >&2
  exit 70
fi
: > "$LOG"

nohup llama-server --model "$M" --alias "$ALIAS" \
  --jinja --gpu-layers 99 \
  --spec-type draft-mtp --spec-draft-model "$D" --spec-draft-n-max 3 \
  --ctx-size "$CTX" --cache-type-k q8_0 --cache-type-v q8_0 \
  --parallel 1 --device CUDA0 --flash-attn on \
  --batch-size "$BATCH" --ubatch-size "$UBATCH" --cont-batching --no-context-shift \
  --host 172.17.0.1 --port "$PORT" >> "$LOG" 2>&1 &
SRV=$!
echo "llama-server pid=$SRV ctx=$CTX alias=$ALIAS log=$LOG"

READY=0
for i in $(seq 1 90); do
  if curl -fsS -m 2 "http://172.17.0.1:$PORT/health" 2>/dev/null | grep -q '"status":"ok"'; then
    READY=1
    break
  fi
  kill -0 "$SRV" 2>/dev/null || { echo "server died during load; see $LOG" >&2; exit 1; }
  sleep 4
done
if [ "$READY" -ne 1 ]; then
  echo "server did not become healthy; see $LOG" >&2
  kill "$SRV" 2>/dev/null
  exit 1
fi

# THE GUARD. A real cudaMalloc failure means the compute buffer went to host RAM.
if grep -q 'cudaMalloc failed' "$LOG"; then
  echo "REFUSING: llama.cpp fell back after a failed CUDA allocation at ctx=$CTX." >&2
  grep -m3 'cudaMalloc failed\|failed to allocate compute' "$LOG" >&2
  echo "Throughput would be ~half and the run would look normal. Use a smaller ctx." >&2
  kill -9 "$SRV" 2>/dev/null
  exit 1
fi

TPS=$(curl -s -m 300 "http://172.17.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' \
  -d '{"model":"'"$ALIAS"'","messages":[{"role":"user","content":"Write a TypeScript add function."}],"max_tokens":200}' \
  | python3 -c "import sys,json;print('%.1f'%json.load(sys.stdin).get('timings',{}).get('predicted_per_second',0))" 2>/dev/null)
FREE=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
echo "smoke: ${TPS} t/s, ${FREE} MiB free on GPU 0"
# The 131k preset retains its original 110 t/s guard. The 192k preset has a
# lower floor because its smaller batch decodes more slowly on the full GPU path.
awk -v t="${TPS:-0}" -v floor="$TPS_FLOOR" 'BEGIN{exit !(t < floor)}' && {
  echo "REFUSING: ${TPS} t/s is below the ${TPS_FLOOR} t/s floor -- this looks like the degraded path." >&2
  kill -9 "$SRV" 2>/dev/null; exit 1; }
echo "OK ctx=$CTX alias=$ALIAS pid=$SRV port=$PORT"
echo "For trials: OAKEN_SERVER_PORT=$PORT ./run.sh dsh $ALIAS <label>"
