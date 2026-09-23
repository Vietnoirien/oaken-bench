#!/usr/bin/env bash
# Find max usable ctx: must LOAD *and* SERVE a real completion.
# usage: ctxprobe.sh <model.gguf> <kv-type> <ctx>[,<ctx>...] [-- extra llama-server flags]
# The extra flags matter: probe with the SAME flags you will serve with, or the
# measurement does not transfer (a draft model, for instance, costs real VRAM).
# OAKEN_DEVICE picks the card(s), default CUDA0. With two, e.g. CUDA0,CUDA1, free
# VRAM is reported per card: the probe fails on whichever one fills first, and a
# summed figure would hide which.
M="$1"; shift
KV="$1"; shift
DEV=${OAKEN_DEVICE:-CUDA0}
free_vram() { nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | paste -sd/; }
CTXS=(); EXTRA=()
while [ $# -gt 0 ]; do
  if [ "$1" = "--" ]; then shift; EXTRA=("$@"); break; fi
  CTXS+=("$1"); shift
done
for CTX in "${CTXS[@]}"; do
  nohup llama-server --model "$M" --alias probe --jinja --gpu-layers 99 \
    --ctx-size "$CTX" --cache-type-k "$KV" --cache-type-v "$KV" \
    --parallel 1 --device "$DEV" --batch-size 512 --ubatch-size 512 \
    --no-context-shift --host 127.0.0.1 --port 8099 \
    "${EXTRA[@]}" > "/tmp/cp-$CTX.log" 2>&1 &
  disown
  st=""
  for i in $(seq 1 90); do   # 90 x 4s = 6 min; a 200k+ ctx load is slow
    # /health, and -f: while loading, the server already answers with a 503 that
    # plain `curl -s` counts as success, and the probe fires before the model is up.
    curl -sf -m 2 http://127.0.0.1:8099/health >/dev/null 2>&1 && { st=loaded; break; }
    grep -q 'out of memory\|failed to create context\|CUDA error' "/tmp/cp-$CTX.log" 2>/dev/null && { st=OOM_LOAD; break; }
    sleep 4
  done
  free_after_load=$(free_vram)
  free_after_infer="-"
  if [ "$st" = loaded ]; then
    # Allocation of the KV and compute buffers is lazy: /v1/models answers before
    # the memory is actually taken. Peak usage is only observable AFTER a real
    # completion, so that is where the measurement has to happen.
    body=$(curl -s -m 300 http://127.0.0.1:8099/v1/chat/completions -H 'Content-Type: application/json' \
      -d '{"model":"probe","messages":[{"role":"user","content":"Write a TypeScript add function and explain it in 8 lines."}],"max_tokens":400}' 2>/dev/null)
    free_after_infer=$(free_vram)
    st=$(printf '%s' "$body" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
except Exception:
    print('OOM_INFERENCE'); raise SystemExit
if 'error' in d:
    print('ERROR:' + str(d['error'])[:40]); raise SystemExit
ch=(d.get('choices') or [{}])[0]
txt=(ch.get('message') or {}).get('content') or ''
tps=d.get('timings',{}).get('predicted_per_second',0) or 0
n=d.get('usage',{}).get('completion_tokens',0) or 0
# A response with no tokens is not a served response, whatever the HTTP status.
if n<=0 or tps<=0:
    print('NO_TOKENS n=%d tps=%.1f' % (n,tps))
else:
    empty=' [no content field]' if not txt.strip() else ''
    print('SERVES %.1f t/s (%d tok)%s' % (tps,n,empty))
" 2>/dev/null)
    [ -n "$st" ] || st=OOM_INFERENCE
  fi
  printf '  ctx=%-7s %-26s free_load=%-6s free_infer=%s MiB\n' \
         "$CTX" "$st" "$free_after_load" "$free_after_infer"
  p=$(ps -eo pid,cmd --no-headers | awk '/port 8099/ && !/awk/ {print $1; exit}')
  [ -n "$p" ] && { kill -9 "$p" 2>/dev/null; sleep 5; }
done
