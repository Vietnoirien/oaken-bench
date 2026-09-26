#!/usr/bin/env bash
# One guarded launcher for every model on the RTX 5070 (CUDA0) + RTX 3060 (CUDA1).
#   GGUF=/path/model.gguf ./launch-dual.sh <qwen35moe|qwen35moe-desktop|qwen35moe-kvq4-262k|oss20b|glm47flash>
#
# The original presets were probed with scripts/ctxprobe.sh and a filled
# ~100k-token context on llama.cpp b10751 (MODELS.md 4.2). qwen35moe-desktop
# is a separate 131k layout for active desktop use; do not pool its results
# with the original preset. Change a flag and the measured margins no longer
# describe the server you get.
set -u
PRESET=${1:?usage: GGUF=/path/model.gguf $0 <qwen35moe|qwen35moe-desktop|qwen35moe-kvq4-262k|oss20b|glm47flash>}
M=${GGUF:?set GGUF to the model path}
PORT=${OAKEN_SERVER_PORT:-8080}
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "invalid OAKEN_SERVER_PORT: $PORT" >&2
  exit 64
fi
CTX=131072 KV=q8_0   # every preset but one; it says so

case "$PRESET" in
  qwen35moe)
    # Qwen3.6-35B-A3B UD-Q4_K_S. Attention, KV and blocks 0-14's experts on the
    # 5070, blocks 15-39's experts on the 3060. Measured: ~99 t/s short prompt,
    # 57.6 t/s decode at 115k filled, 663 / 770 MiB free. Splitting at block 16
    # leaves 226 MiB on the 5070 at 131k.
    ALIAS=Qwen3.6-35B-A3B-UD-Q4_K_S.gguf
    FLAGS=(--tensor-split 1,0 -ot 'blk\.(1[5-9]|[23][0-9])\.ffn_.*_exps\.=CUDA1' --ubatch-size 256)
    FLOOR=75 MAX_U0=1200 MIN_FREE0=250 ;;      # clean path ~99; experts on CPU measured ~50
  qwen35moe-desktop)
    # Same 131k/q8_0 model as qwen35moe, but one more expert block on the 3060.
    # The standard split left only 144 MiB free with 1.3 GiB of desktop use.
    # Keep this distinct from the T2 preset so the layout change stays visible.
    ALIAS=Qwen3.6-35B-A3B-UD-Q4_K_S.gguf
    FLAGS=(--tensor-split 1,0 -ot 'blk\.(1[4-9]|[23][0-9])\.ffn_.*_exps\.=CUDA1' --ubatch-size 256)
    FLOOR=75 MAX_U0=1500 MIN_FREE0=300 ;;
  qwen35moe-kvq4-262k)
    # Same weights at the model's full native 262144, q4_0 KV. One more block's
    # experts (14) go to the 3060, which has no desktop to grow into its margin.
    # Measured: 99.6 t/s short prompt, 588 / 328 MiB free; with 258,115 tokens
    # filled, 817 t/s prompt, 35.6 t/s decode, peaks 11408 / 11639 MiB. At block 15
    # the 5070 keeps 156 MiB. q4_0 KV costs Qwen 3.6 measurable KL in long documents
    # and tool calling (localbench) -- this preset trades that for context.
    ALIAS=Qwen3.6-35B-A3B-UD-Q4_K_S-kvq4-262k.gguf
    CTX=262144 KV=q4_0
    FLAGS=(--tensor-split 1,0 -ot 'blk\.(1[4-9]|[23][0-9])\.ffn_.*_exps\.=CUDA1' --ubatch-size 256)
    FLOOR=75 MAX_U0=1200 MIN_FREE0=250 ;;
  oss20b)
    # gpt-oss-20b MXFP4 (native quant). Plain layer split, weighted to the faster
    # card. Measured: 119 t/s short prompt, 42.6 t/s decode and 4315 t/s prompt at
    # 99k filled, 1396 / 6831 MiB free. 72/28 buys 1.5 t/s for 830 MiB of margin.
    ALIAS=gpt-oss-20b-MXFP4.gguf
    FLAGS=(--tensor-split 65,35)
    FLOOR=90 MAX_U0=1500 MIN_FREE0=500 ;;      # clean path ~119; 50/50 served 113.7
  glm47flash)
    # GLM-4.7-Flash UD-Q4_K_XL, arch deepseek2 (MLA, 47 blocks, experts in 1-46).
    # Attention, the ~3.5 GiB MLA cache and blocks 1-13's experts on the 5070,
    # blocks 14-46's experts on the 3060. Measured at 131k: ~92 t/s short prompt,
    # 1187 / 948 MiB free; with 98,735 tokens filled 402 t/s prompt, 26.6 t/s
    # decode, peaks 10783 / 11041 MiB. Block 15 leaves 863 / 1272, block 16
    # 539 / 1596. -ub 256 cost 12 % of prompt speed for ~110 MiB.
    ALIAS=GLM-4.7-Flash-UD-Q4_K_XL.gguf
    FLAGS=(--tensor-split 1,0 -ot 'blk\.(1[4-9]|[23][0-9]|4[0-6])\.ffn_.*_exps\.=CUDA1')
    FLOOR=70 MAX_U0=1500 MIN_FREE0=400 ;;      # clean path ~92
  *) echo "unknown preset '$PRESET'" >&2; exit 64 ;;
esac

LISTENERS=$(ss -ltn) || {
  echo "cannot check listening ports; refusing to start a server" >&2
  exit 1
}
if printf '%s\n' "$LISTENERS" | grep -Eq ":${PORT}([[:space:]]|$)"; then
  echo "port $PORT is already listening; refusing to probe or replace it" >&2
  exit 70
fi
LOG=/tmp/dual-$PRESET-$CTX-$PORT.log
: > "$LOG"

# The build is part of the result: conda-forge b9716 carries llama.cpp #24807,
# under which Qwen3.6's malformed tool calls abort pi's stream; b9754+ does not.
# Nothing else records which binary a run was served by, so the log does.
echo "preset: $PRESET ctx=$CTX kv=$KV port=$PORT" | tee -a "$LOG"
echo "llama-server: $(command -v llama-server)" | tee -a "$LOG"
llama-server --version 2>&1 | grep -E 'version|built' | tee -a "$LOG"

free_mib() { nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | sed -n "$(( $1 + 1 ))p"; }
used_mib() { nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | sed -n "$(( $1 + 1 ))p"; }

# GUARD 1, before loading: the measured margins differ by preset. The Qwen
# layouts leave much less room than gpt-oss and GLM. Check actual free VRAM
# after the smoke test so current desktop use is included in the margin.
U0=$(used_mib 0); U1=$(used_mib 1)
if [ "$U0" -gt "$MAX_U0" ] || [ "$U1" -gt 200 ]; then
  echo "REFUSING: cards already hold ${U0} / ${U1} MiB (5070 / 3060); $PRESET allows at most ${MAX_U0} / 200 before loading." >&2
  echo "Close GPU applications, or re-probe with scripts/ctxprobe.sh before trusting this preset." >&2
  exit 1
fi

nohup llama-server --model "$M" --alias "$ALIAS" \
  --jinja --gpu-layers 99 \
  --ctx-size "$CTX" --cache-type-k "$KV" --cache-type-v "$KV" \
  --parallel 1 --device CUDA0,CUDA1 "${FLAGS[@]}" \
  --batch-size 512 --cont-batching --no-context-shift \
  --host 172.17.0.1 --port "$PORT" >> "$LOG" 2>&1 &
SRV=$!
echo "llama-server pid=$SRV ctx=$CTX alias=$ALIAS log=$LOG"

# /health, not /v1/models: the latter answers 503 "Loading model" during the load,
# and a plain `curl -s` counts that as up.
for i in $(seq 1 90); do
  curl -sf -m 2 "http://172.17.0.1:$PORT/health" >/dev/null 2>&1 && break
  kill -0 "$SRV" 2>/dev/null || { echo "server died during load; see $LOG" >&2; exit 1; }
  sleep 4
done

refuse() { echo "REFUSING: $*" >&2; kill -9 "$SRV" 2>/dev/null; exit 1; }

# GUARD 2: a failed CUDA allocation means a buffer went to host RAM (MODELS.md 4.1).
grep -q 'cudaMalloc failed' "$LOG" && refuse "a CUDA allocation failed at ctx=$CTX; see $LOG"

# GUARD 3: the smoke test from MODELS.md 5 -- a real tool call, not a load.
# max_tokens is generous because every preset's model thinks first.
R=$(curl -s -m 300 "http://172.17.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' -d '{
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
[ "$STATUS" = ok ]         || refuse "smoke test did not return a well-formed write() tool call ($STATUS)"
[ "$MODEL_ID" = "$ALIAS" ] || refuse "server advertises '$MODEL_ID', harness configs expect '$ALIAS'"

# GUARD 4: throughput, against this preset's measured clean path.
awk -v t="$TPS" -v f="$FLOOR" 'BEGIN{exit !(t < f)}' && refuse "${TPS} t/s is below this preset's ${FLOOR} t/s floor"

F0=$(free_mib 0); F1=$(free_mib 1)
if [ "$U0" -gt 1200 ] && [ "$F0" -lt "$MIN_FREE0" ]; then
  refuse "only ${F0} MiB remains on the 5070; $PRESET needs ${MIN_FREE0} MiB after loading"
fi
echo "smoke: write() tool call ok, ${TPS} t/s, free ${F0} / ${F1} MiB (5070 / 3060)"
echo "OK preset=$PRESET ctx=$CTX alias=$ALIAS pid=$SRV port=$PORT"
