# Adding and tuning a model

Everything here was measured on an **RTX 5070 12 GB** while running this
benchmark. The numbers are hardware-specific; the failure modes are not.

The short version, and the single most expensive lesson in this repo:

> **A model that loads is not a model that works.** `llama-server` will happily
> allocate a 64k context, report itself ready, and then CUDA-OOM on the first
> request. Always smoke-test an actual completion before starting a run.

And the harder one, which cost a corrected conclusion in this study:

> **A model that answers is not a model that works either.** When a CUDA
> allocation fails, llama.cpp does not exit -- it falls back to host memory and
> keeps serving at roughly **half speed**, with no error on the API. A benchmark
> run in that state produces data that looks completely normal and is wrong.
> See §4.1.

---

## 1. Register the model in both harnesses

Two files, one entry each. The `id` must match the `--alias` you pass to
`llama-server`, and it is what you hand to `./run.sh`.

**`docker/config/pi/models.json`**

```json
{
  "id": "your-model.gguf",
  "baseUrl": "http://llama:8080/v1",
  "apiKey": "local",
  "contextWindow": 131072,
  "maxTokens": 16384,
  "reasoning": false
}
```

**`docker/config/dsh/settings.yaml`**

```yaml
- id: your-model.gguf
  baseUrl: http://llama:8080/v1
  apiKeyEnv: LOCAL_LLAMA_KEY
  contextWindow: 131072
  maxTokens: 16384
```

`llama` is a `--add-host=llama:host-gateway` alias, not a real hostname. The
container has `--dns 0.0.0.0`; it can reach the host gateway and nothing else.

Rebuild after editing: `docker build -t oaken-bench:1.0 docker/`
**The build context is `docker/`, not the repo root.**

---

## 2. Check the compaction arithmetic before you run anything

Both harnesses compact on a threshold derived from `contextWindow`. Set it
wrong and the agent enters a degenerate loop that looks like a model failure
and is not.

| harness | compaction trigger | retains |
|---|---|---|
| pi | `contextWindow - 16384` | `keepRecentTokens` = 20000, fixed |
| dsh | `contextWindow * 0.8` | `contextWindow * 0.16` |

**The pi trap.** At `contextWindow: 32768` the trigger is 16384 while
`keepRecentTokens` is 20000. pi cannot reduce the context below its own trigger,
so it compacts, fails to get under the line, and compacts again. At
`contextWindow: 16384` the trigger is zero. Anything below ~48k is unusable.

**The dsh trap.** dsh retains 16 % of the window. `SPEC.md` is ~10 300 tokens.
Below `contextWindow: 65536`, dsh retains less than the spec, so after the first
compaction the agent no longer holds the document it is implementing and starts
re-reading it forever. Observed directly: 25 compactions in one 30-minute run.

Sanity check before every new configuration:

```bash
python3 - <<'EOF'
CTX = 131072
print(f"pi  trigger {CTX-16384:>7d}  keepRecent 20000  -> {'OK' if CTX-16384 > 20000 else 'BROKEN'}")
print(f"dsh trigger {int(CTX*0.8):>7d}  retains {int(CTX*0.16):>6d} tok  (SPEC.md = 10302)")
EOF
```

---

## 3. Find the real context ceiling

`scripts/ctxprobe.sh` tries each context size you give it and reports which ones
**serve a completion**, not merely which ones load. Use it. Do not estimate.

```bash
scripts/ctxprobe.sh /path/to/model.gguf q4_0  65536 98304 131072 163840
#   ctx=65536   SERVES 50.8 t/s        free_after_load=3210 MiB
#   ctx=131072  SERVES 50.1 t/s        free_after_load=1077 MiB
#   ctx=163840  SERVES 49.4 t/s        free_after_load=352 MiB
#   ctx=196608  OOM_LOAD               free_after_load=...
```

It distinguishes three outcomes: `OOM_LOAD` (never came up), `OOM_INFERENCE`
(came up, died on the first request — the failure mode that matters), and
`SERVES`. Each probe binds port 8099 on localhost and is killed before the next,
so run it with no other model resident.

If you want to estimate first, read the KV geometry out of the GGUF rather than
guessing — architectures differ by more than an order of magnitude in how KV
scales with context. Gemma 4 12B, for example, caps 40 of its 48 layers at a
1024-token sliding window and gives the 8 global layers a single KV head, so its
full 262144-token KV is only ~2.4 GiB at `q8_0`. A conventional GQA model of the
same size is far more expensive.

Measured ceilings from this study, RTX 5070 12 GB, desktop at ~1.0 GiB:

| model | KV | serves | free |
|---|---|---|---|
| Qwen3.8-27B UD-Q2_K_XL (9.15 GiB) | q4_0 | **49152** | 421 MiB |
| " | q4_0 | 65536 — **loads, then OOMs on first request** | — |
| Qwen3.8-27B UD-IQ2_XXS (6.77 GiB) | q4_0 | **163840** | 352 MiB |
| " | q4_0 | 131072 | 1077 MiB |
| " | q8_0 | **90112** | 492 MiB |
| " | q8_0 | 98304 — **loads, then OOMs** | — |

A desktop application reopening mid-run costs ~700 MiB and turns the top row of
that table into an OOM. Close things, or leave a margin.

---

## 4. `llama-server` flags that actually matter

```bash
llama-server --model model.gguf \
  --alias your-model.gguf \        # MUST match the harness config id
  --jinja --gpu-layers 99 \
  --ctx-size 131072 \
  --cache-type-k q4_0 --cache-type-v q4_0 \
  --parallel 1 \                   # see below -- not optional
  --device CUDA0 \
  --batch-size 512 --ubatch-size 512 \
  --cont-batching --no-context-shift \
  --host 172.17.0.1 --port 8080    # docker bridge, NOT 127.0.0.1
```

**`--parallel 1`.** Without it `llama-server` auto-selected **4** slots and
allocated 4x the KV cache *and* 4x the recurrent-state cache. This is the single
biggest cause of "it fits in llama-bench but OOMs in the server" — `llama-bench`
does not allocate parallel slots, so its numbers do not transfer.

**`--batch-size 512 --ubatch-size 512`.** The default 2048 needs a much larger
cuBLAS workspace, allocated lazily *at first inference*. That is the mechanism
behind load-succeeds-then-OOM. Dropping `--no-mmap` helps for the same reason.

**`--host 172.17.0.1`.** Binding to `127.0.0.1` makes the server unreachable
from the container. `run.sh` checks for this and refuses to start.

**Reasoning channel.** `--reasoning-format none` keeps thinking inline.
`--reasoning-budget -1` leaves it unbounded. **Do not use
`--reasoning off --reasoning-budget 0`** — it force-injects an
end-of-thinking sequence that breaks llama-server's own response grammar. pi
died in 5 seconds, every time.

### 4.1 The silent post-OOM fallback

Measured on Gemma 4 12B QAT + MTP draft, q8_0 KV, RTX 5070 12 GB:

| ctx | CUDA path | decode | free VRAM |
|---|---|---|---|
| 131072 | clean | ~137 t/s | 1560 MiB |
| 196608 | clean | ~147 t/s | 646 MiB |
| 229376 | clean | ~142 t/s | 156 MiB |
| 245760 | **failed alloc → fallback** | **~70 t/s** | 705 MiB |
| 262144 | **failed alloc → fallback** | **~70 t/s** | 529 MiB |

Note the trap in the last column: the degraded configurations report **more**
free VRAM than the working one just below them, because the compute buffer moved
to host RAM. Free VRAM going *up* as context goes *up* is the signature.

The log tells you plainly, if you look:

```
ggml_backend_cuda_buffer_type_alloc_buffer: allocating 769.78 MiB on device 0:
    cudaMalloc failed: out of memory
ggml_gallocr_reserve_n_impl: failed to allocate CUDA0 buffer of size 807176320
graph_reserve: failed to allocate compute buffers
```

Do not confuse this with the benign line that appears at *every* context size:

```
llama_init_from_model: failed to initialize the context: Gemma4Assistant
    requires ctx_other to be set (this warning is normal during memory fitting)
```

One line containing "failed" is normal. Grep for `cudaMalloc failed`
specifically.

**Guard your launcher.** Refusing to start beats discovering it in the results:

```bash
if grep -q 'cudaMalloc failed' "$LOG"; then
  echo "REFUSING: fell back after a failed CUDA allocation at ctx=$CTX" >&2
  kill -9 "$SRV"; exit 1
fi
# and a throughput floor, since the fallback is ~half speed
awk -v t="$TPS" 'BEGIN{exit !(t < 110)}' && { echo "REFUSING: ${TPS} t/s" >&2; exit 1; }
```

A worked example is in `examples/launch-gemma.sh`.

---

## 5. Smoke-test before committing 30 minutes

Loading proves nothing. This proves the model will serve a tool call:

```bash
curl -s http://172.17.0.1:8080/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "your-model.gguf",
  "messages": [{"role":"user","content":"Create add.ts with an add function."}],
  "tools": [{"type":"function","function":{"name":"write","parameters":{
      "type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},
      "required":["path","content"]}}}],
  "max_tokens": 256
}' | python3 -m json.tool | tail -30
```

Check three things: the advertised `model` id matches your `--alias`, the
response contains a `tool_calls` entry with well-formed arguments, and
`finish_reason` is `tool_calls`. Then check `nvidia-smi` for free VRAM and the
server log for CUDA errors. If any of those is wrong, fix it now.

---

## 6. Known harness failure modes

These are harness bugs, not model failures. Recognise them so you do not
misattribute a result.

**pi exits 0 on a fatal parse error.** When `llama-server` emits a response its
`peg-native` parser cannot read, pi treats it as fatal, aborts in 3–16 seconds,
and **exits with status 0** — which reads as success to any wrapper script.
Observed 17 aborts across 21 attempts on one model. dsh survives the identical
responses. **Always check `wallclock.seconds`**: anything under ~20 s did not
engage with the task. `scripts/batch.sh` retries on this.

**Non-terminating generated code hangs the scorer.** An agent can write code
that never terminates. `vitest` forks a worker pool, and killing the parent
leaves the pool running. An early version of `score.py` leaked ~85 orphan
processes pinning four cores for seven hours, which quietly corrupted the
wall-clock figures of every run scored afterwards. `score.py` now runs each
command in its own process group and reaps the group on both the timeout and
the normal-exit path. If you modify it, keep that.

**Degenerate repetition** — the same paragraphs emitted verbatim until the token
budget cuts them off — is an over-quantization signature, not a prompt problem.
Observed at IQ2_XXS and not at Q2_K_XL on the same model.

---

## 7. Interpreting your numbers

- **`3/52 visible` is the stub baseline.** Three visible tests assert
  pre-declared constants and pass against completely unimplemented code. A run
  scoring 3/52 wrote nothing. Confirm with `diff.stat` — 0 bytes means the seed
  was never touched.
- **A clean typecheck can mean nothing was written.** Check `diff.stat` first.
- **The overfit gap is the interesting number**, not the pass rate. One run here
  scored 52/52 visible and 100/132 hidden. Under visible-only scoring it looks
  flawless.
- **One run is not a result.** Three runs of one configuration in this study
  spanned 0 % to 75.8 % hidden. Run at least three and report the spread. The
  30-minute cap is frequently the binding constraint rather than model capability,
  so anything that changes throughput changes the score for reasons unrelated to
  what you are trying to measure.

---

## 8. Scoring runs in a container, on purpose

`scripts/score.py` does not run the test suites on your machine. It calls
`docker run --rm --network none` against the same pinned image the trials use.

Three reasons, all learned the hard way:

1. **The held-out suite is decrypted inside the container only.** Plaintext
   never touches your filesystem, so it cannot be indexed, committed by
   accident, or swept into a dataset. `/out` receives counts, never test source
   -- the test *names* are benchmark data too.
2. **Agent-generated code can fail to terminate.** vitest forks a worker pool;
   killing the parent on the host once left ~85 orphans pinning four cores for
   seven hours, which silently corrupted the wall-clock figures of every run
   scored afterwards. `--rm` reaps the whole tree by construction.
3. **node and vitest are pinned by the image**, so scores do not drift with
   whatever your host toolchain happens to be.

The passphrase reaches the container through `OAKEN_HIDDEN_PASS`; override it if
you re-encrypted with your own. `OAKEN_IMAGE` selects a different image tag.

If you change the scorer, re-validate it against a known result before trusting
new numbers. The containerised scorer was accepted only after reproducing three
previously host-scored runs exactly, including typecheck state and tamper flags.
