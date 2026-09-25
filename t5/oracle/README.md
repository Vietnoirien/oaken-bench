# T5 sealed snapshot oracle

This is a separate score from the visible head-to-head protocol in
`t5/SPEC.md`. Do not pool the two win rates. The frozen contract and simulator
stay pinned by `t5/FROZEN.sha256`.

The named `t5oracle` bundle contains 48 independently chosen uint32 seeds and
144 fixed pre-combat snapshots, one from each frozen baseline per seed. The
source is baseline self-play at day 3, or the last day if the match ends
earlier. The candidate starts a fresh reference-engine run for each case and
fights that same snapshot every day. It receives yesterday's combat result
through the frozen `DecideState` interface. A candidate win at 10 trophies
scores 1, a loss at 0 lives or rejected snapshot scores 0. The day-30 guard
voids the evaluation. This measures ten-trophy completion against fixed
towers; it is not the visible live-bot match score.

The denominator is 144. The 95% interval is a percentile bootstrap of the
48 per-seed means. Each resample draws whole seeds with replacement, keeping
the three baseline results together. It uses 10,000 resamples and a fixed
integer RNG state so rescoring is byte-for-byte stable. The interval describes
variation over the chosen seed clusters, not uncertainty about this sealed
pool's fixed arithmetic mean. It does not account for strategy selection
against the same oracle after scores are seen.

Use Node 24 with native TypeScript support, OpenSSL, GNU tar, Bubblewrap and
`prlimit`. The launcher decrypts and verifies the sealed reference engine in
a temporary workspace. Scoring fails if the bot sandbox cannot start.

```bash
export OAKEN_T5_NODE=/path/to/node24-with-typescript
python3 scripts/t5_oracle.py run --bot baseline:cheapest --label cheapest-01
python3 scripts/score.py results/t5-cheapest-01
```

The first command creates `results/t5-cheapest-01/run.json` and scores it.
`run.json` is ignored. The second command rescores through the tier registry.
`score.json` contains aggregates and digests only; no raw seeds, snapshots,
canary, bot actions or per-case outcomes. It also hashes the scorer and bot
source so implementation changes cannot masquerade as a comparable run.
The sealed archive and its plaintext
archive digest are `t5oracle.tar.gz.enc` and `t5oracle.sha256`. Other digests
are in `DIGESTS.json`. The plaintext directory `t5oracle/` is gitignored and
absent from commits. `scripts/hidden.sh unlock t5oracle` restores it for an
authorized audit.

The capture command refuses to replace an existing archive:

```bash
python3 scripts/t5_oracle.py capture
```

The scorer removes its temporary plaintext pool file before starting the bot.
The bot runs in a separate process inside Bubblewrap, with a private `/tmp`,
no network, no host writable paths, and read-only mounts for its own source,
the public T5 simulator API and the staged reference engine. The repository
root and encrypted oracle archive are not mounted. `prlimit` caps bot virtual
address space at 32 GiB, CPU time at 120 seconds and open files at 128;
Node's old-space heap cap is 512 MiB. The frozen 1-second decision and
5-second import budgets still apply. Bot stdout is a bounded decision channel;
stderr is discarded. The process gets only the frozen `DecideState` for each
call, which includes yesterday's opponent snapshot as specified in T5/1.

This boundary prevents normal module and filesystem access to the sealed pool.
It does not make a fixed oracle immune to adaptive overfitting. Reserve the
sealed score for held-out evaluation, and compare only runs with matching
protocol, pool, engine, simulator and bot digests.
