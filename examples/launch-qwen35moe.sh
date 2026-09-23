#!/usr/bin/env bash
# Qwen3.6-35B-A3B UD-Q4_K_S across RTX 5070 (CUDA0) + RTX 3060 (CUDA1) for oaken-bench.
#   QWEN_GGUF=/path/Qwen3.6-35B-A3B-UD-Q4_K_S.gguf ./launch-qwen35moe.sh
#
# MEASURED 2026-09-23, q8_0 KV, ctx 131072, desktop ~0.8 GiB on the 5070:
#   short prompt       ~99 t/s decode          663 / 770 MiB free (5070 / 3060)
#   115k-token prompt  953 t/s prompt, 57.6 t/s decode, peak 11248 / 11197 MiB used
#
# Layout: attention, KV and the experts of blocks 0-14 on the 5070; experts of
# blocks 15-39 on the 3060. The 19.45 GiB of weights leave under 2 GiB across both
# cards, so the split point is load-bearing -- one block later (16) leaves 226 MiB
# on the 5070 at 131k, and a desktop app reopening costs ~700. See MODELS.md 4.2.
#
# Refuses to hand over a server that is not the one measured above.
set -u
M=${QWEN_GGUF:?set QWEN_GGUF to the UD-Q4_K_S model path}
CTX=131072
ALIAS=Qwen3.6-35B-A3B-UD-Q4_K_S.gguf
LOG=/tmp/qwen35moe-server-$CTX.log
: > "$LOG"

# The build is part of the result: conda-forge b9716 carries llama.cpp #24807,
# under which this model's malformed tool calls abort pi's stream; b9754+ does
# not. Nothing else records which binary a run was served by, so the log does.
echo "llama-server: $(command -v llama-server)" | tee -a "$LOG"
llama-server --version 2>&1 | grep -E 'version|built' | tee -a "$LOG"

free_mib() { nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | sed -n "$(( $1 + 1 ))p"; }
used_mib() { nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | sed -n "$(( $1 + 1 ))p"; }

# GUARD 1, before loading: the margin on the 5070 is ~650 MiB at 131k. A desktop
# holding more than the ~0.8 GiB it held when this was measured eats it, and the
# failure then is a lazy allocation mid-run, not a clean refusal here.
U0=$(used_mib 0); U1=$(used_mib 1)
if [ "$U0" -gt 1200 ] || [ "$U1" -gt 200 ]; then
  echo "REFUSING: cards already hold ${U0} / ${U1} MiB (5070 / 3060); measured with ~800 / ~15." >&2
  echo "Close GPU applications, or re-probe with scripts/ctxprobe.sh before trusting this layout." >&2
  exit 1
fi

nohup llama-server --model "$M" --alias "$ALIAS" \
  --jinja --gpu-layers 99 \
  --ctx-size "$CTX" --cache-type-k q8_0 --cache-type-v q8_0 \
  --parallel 1 --device CUDA0,CUDA1 --tensor-split 1,0 \
  -ot 'blk\.(1[5-9]|[23][0-9])\.ffn_.*_exps\.=CUDA1' \
  --batch-size 512 --ubatch-size 256 \
  --cont-batching --no-context-shift \
  --host 172.17.0.1 --port 8080 >> "$LOG" 2>&1 &
SRV=$!
echo "llama-server pid=$SRV ctx=$CTX alias=$ALIAS log=$LOG"

# /health, not /v1/models: the latter answers 503 "Loading model" during the load,
# and a plain `curl -s` counts that as up.
for i in $(seq 1 90); do
  curl -sf -m 2 http://172.17.0.1:8080/health >/dev/null 2>&1 && break
  kill -0 "$SRV" 2>/dev/null || { echo "server died during load; see $LOG" >&2; exit 1; }
  sleep 4
done

refuse() { echo "REFUSING: $*" >&2; kill -9 "$SRV" 2>/dev/null; exit 1; }

# GUARD 2: a failed CUDA allocation means a buffer went to host RAM (MODELS.md 4.1).
grep -q 'cudaMalloc failed' "$LOG" && refuse "a CUDA allocation failed at ctx=$CTX; see $LOG"

# GUARD 3: the smoke test from MODELS.md 5 -- a real tool call, not a load.
# max_tokens is generous because the model thinks first, into reasoning_content.
R=$(curl -s -m 300 http://172.17.0.1:8080/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "'"$ALIAS"'",
  "messages": [{"role":"user","content":"Create add.ts with an add function."}],
  "tools": [{"type":"function","function":{"name":"write","parameters":{
      "type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},
      "required":["path","content"]}}}],
  "max_tokens": 4096}')
CHECK=$(printf '%s' "$R" | python3 -c '
import sys, json
d = json.load(sys.stdin)
ch = d["choices"][0]; calls = ch["message"].get("tool_calls") or []
ok = ch["finish_reason"] == "tool_calls" and calls and calls[0]["function"]["name"] == "write"
json.loads(calls[0]["function"]["arguments"]) if ok else None
print("%s %s %.1f" % ("ok" if ok else "bad:" + str(ch["finish_reason"]), d["model"],
                      d.get("timings", {}).get("predicted_per_second", 0)))' 2>/dev/null)
read -r STATUS MODEL_ID TPS <<<"${CHECK:-bad:unparseable - 0}"
[ "$STATUS" = ok ]       || refuse "smoke test did not return a well-formed write() tool call ($STATUS)"
[ "$MODEL_ID" = "$ALIAS" ] || refuse "server advertises '$MODEL_ID', harness configs expect '$ALIAS'"

# GUARD 4: throughput. The clean path is ~99 t/s; experts on CPU measured ~50.
awk -v t="$TPS" 'BEGIN{exit !(t < 75)}' && refuse "${TPS} t/s is below the 75 t/s floor -- not the measured GPU path"

F0=$(free_mib 0); F1=$(free_mib 1)
echo "smoke: write() tool call ok, ${TPS} t/s, free ${F0} / ${F1} MiB (5070 / 3060)"
echo "OK ctx=$CTX alias=$ALIAS pid=$SRV"
