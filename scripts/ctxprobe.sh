#!/usr/bin/env bash
# Find max usable ctx: must LOAD *and* SERVE a real completion.
M="$1"; shift
KV="$1"; shift
for CTX in "$@"; do
  nohup llama-server --model "$M" --alias probe --jinja --gpu-layers 99 \
    --ctx-size "$CTX" --cache-type-k "$KV" --cache-type-v "$KV" \
    --parallel 1 --device CUDA0 --batch-size 512 --ubatch-size 512 \
    --no-context-shift --host 127.0.0.1 --port 8099 > "/tmp/cp-$CTX.log" 2>&1 &
  disown
  st=""
  for i in $(seq 1 60); do
    curl -s -m 2 http://127.0.0.1:8099/v1/models >/dev/null 2>&1 && { st=loaded; break; }
    grep -q 'out of memory\|failed to create context\|CUDA error' "/tmp/cp-$CTX.log" 2>/dev/null && { st=OOM_LOAD; break; }
    sleep 4
  done
  free_after_load=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader)
  if [ "$st" = loaded ]; then
    body=$(curl -s -m 300 http://127.0.0.1:8099/v1/chat/completions -H 'Content-Type: application/json' \
      -d '{"model":"probe","messages":[{"role":"user","content":"Write a TypeScript add function and explain it in 8 lines."}],"max_tokens":400}' 2>/dev/null)
    tok=$(printf '%s' "$body" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin); print('%.1f' % d.get('timings',{}).get('predicted_per_second',0))
except Exception: print('FAIL')
" 2>/dev/null)
    [ "$tok" != "FAIL" ] && [ -n "$tok" ] && st="SERVES ${tok} t/s" || st="OOM_INFERENCE"
  fi
  printf '  ctx=%-7s %-22s free_after_load=%s\n' "$CTX" "$st" "$free_after_load"
  p=$(ps -eo pid,cmd --no-headers | awk '/port 8099/ && !/awk/ {print $1; exit}')
  [ -n "$p" ] && { kill -9 "$p" 2>/dev/null; sleep 5; }
done
