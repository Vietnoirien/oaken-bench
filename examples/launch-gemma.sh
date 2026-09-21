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
# This script refuses to hand over a degraded server.
set -u
M=${GEMMA_GGUF:?set GEMMA_GGUF to the model path}
D=${GEMMA_MTP:?set GEMMA_MTP to the MTP draft path}
case "${1:-131k}" in
  131k) CTX=131072; ALIAS=gemma-4-12B-it-qat-UD-Q4_K_XL.gguf ;;
  192k) CTX=196608; ALIAS=gemma-ctx196k.gguf ;;
  *) echo "usage: $0 <131k|192k>" >&2; exit 64 ;;
esac
LOG=/tmp/gemma-server-$CTX.log
: > "$LOG"

nohup llama-server --model "$M" --alias "$ALIAS" \
  --jinja --gpu-layers 99 \
  --spec-type draft-mtp --spec-draft-model "$D" --spec-draft-n-max 3 \
  --ctx-size "$CTX" --cache-type-k q8_0 --cache-type-v q8_0 \
  --parallel 1 --device CUDA0 \
  --batch-size 2048 --cont-batching --no-context-shift \
  --host 172.17.0.1 --port 8080 >> "$LOG" 2>&1 &
SRV=$!
echo "llama-server pid=$SRV ctx=$CTX alias=$ALIAS log=$LOG"

for i in $(seq 1 90); do
  curl -s -m 2 http://172.17.0.1:8080/v1/models >/dev/null 2>&1 && break
  kill -0 "$SRV" 2>/dev/null || { echo "server died during load; see $LOG" >&2; exit 1; }
  sleep 4
done

# THE GUARD. A real cudaMalloc failure means the compute buffer went to host RAM.
if grep -q 'cudaMalloc failed' "$LOG"; then
  echo "REFUSING: llama.cpp fell back after a failed CUDA allocation at ctx=$CTX." >&2
  grep -m3 'cudaMalloc failed\|failed to allocate compute' "$LOG" >&2
  echo "Throughput would be ~half and the run would look normal. Use a smaller ctx." >&2
  kill -9 "$SRV" 2>/dev/null
  exit 1
fi

TPS=$(curl -s -m 300 http://172.17.0.1:8080/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"'"$ALIAS"'","messages":[{"role":"user","content":"Write a TypeScript add function."}],"max_tokens":200}' \
  | python3 -c "import sys,json;print('%.1f'%json.load(sys.stdin).get('timings',{}).get('predicted_per_second',0))" 2>/dev/null)
FREE=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits)
echo "smoke: ${TPS} t/s, ${FREE} MiB free"
# Full GPU path is ~137-147 t/s; the degraded fallback is ~70. 110 sits well clear of both.
awk -v t="${TPS:-0}" 'BEGIN{exit !(t < 110)}' && {
  echo "REFUSING: ${TPS} t/s is below the 110 t/s floor -- this looks like the degraded path." >&2
  kill -9 "$SRV" 2>/dev/null; exit 1; }
echo "OK ctx=$CTX alias=$ALIAS pid=$SRV"
