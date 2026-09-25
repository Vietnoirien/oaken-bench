# T5 bot contract v1

Contract ID: `t5/1`. Visible scoring protocol: `t5-visible-head-to-head/1`.
This specification, the simulator, three baselines and visible seeds are frozen
by `t5/FROZEN.sha256`. Verify it from the repository root.

The task is to write a bot for the v1.0 tower auto-battler. Its only output is
an array of actions. The supplied reference engine runs the game. Read
`seed/SPEC.md` for combat, items and economy rules, and `seed/src/types.ts` for
the public types. Do not modify those frozen inputs.

The visible score below measures play against live baseline bots. Issue #40
will separately define the held-out score against a fixed pool of baseline
snapshots. These protocols are different and their scores cannot be pooled.
No held-out seeds or opponent pool are defined here.

## 1. Deliverable and authority

Provide an ES module exporting a synchronous function:

```ts
import type { Action, DecideState } from 'oaken-t5';
export function decide(state: DecideState): Action[] {
  return [{ type: 'end' }];
}
```

TypeScript must run through Node's type stripping, with no compilation-only
features such as enums or parameter properties. JavaScript `.mjs` also works.
The reference engine is the sealed `refengine` bundle from issue #37, pinned
by `refengine.sha256`, with the original frozen item data. Engine changes,
item changes, different simulator versions or different seed lists define
different experiments. Never silently replace a recorded result.

This document governs bot decisions, action processing and visible scoring.
The frozen simulator resolves any remaining implementation detail. If it
contradicts this document, withdraw affected scores and issue a new contract
version before changing either. Do not reinterpret old scores.

## 2. Local development

### 2.1 What the agent may do

Read this spec, the public v1.0 spec and interfaces, item data, visible tests,
simulator and baseline code. Run the simulator, inspect its output, write
unit tests and try arbitrary public seeds. Evaluate locally without a GPU,
network connection, package installation or model server. The visible seeds
are development examples and provide no evidence of generalization.

The supplied engine is callable through `oaken-engine`. It may be used for
local simulations on copies of visible state. Its plaintext is a complete T2
solution, so keep it in disposable or ignored storage. Never commit or copy
its source into bot code, tests, logs or documentation. Do not open `hidden/`,
unlock the held-out suite, or inspect any held-out T5 seed or pool bundle.

### 2.2 Purity and imports

A valid bot's output depends only on its argument and frozen public assets.
No mutable state across calls, module-load randomness, clocks, unseeded RNG,
filesystem or network I/O during evaluation, subprocesses, logging, reading
simulator internals, or changes to shared engine objects or global prototypes.
Imports may contain constants and pure helpers. Importing the same module for
both seats may share an instance; bot behavior must not depend on that fact.
Return a finite, JSON-serializable array of plain action objects. Promises,
getters, `toJSON` hooks and side effects during serialization are outside the
contract. JSON serialization defines the value delivered to the action parser.

Use `new Rng(state.botSeed)` from `oaken-engine` for randomness. Mix in `call`
when choosing different actions on successive calls during a day. Pure local
search within the decision budget is allowed. Do not specialize a strategy
to seed identities, seat identities or known visible test outcomes.

### 2.3 Public helpers

`oaken-engine` exports the supplied engine's public v1.0 API.
`oaken-t5` exports `Action`, `DecideState`, the constants and types in
`sim/contract.ts`, `parseAction`, `legalActions`, `startDay` and `playMatch`.
Helpers do not make it legal to access another live bot's state.

`playMatch(seed, [a, b], options)` is a development API. Each bot is
`{name, decide}`. It returns per-match deterministic facts plus a separate
`timing` object. `checkPurity: true` enables the repeat-call check.
`onSnapshot(snapshot, side)` receives a deep copy of each validated pre-combat
snapshot, in seat order. The independent oracle author can use this to capture
baseline snapshots; changes to callback arguments cannot change the match.
Raw `playMatch` output is diagnostic. Use the CLI for scored evaluations.

## 3. Visible match

### 3.1 Initial conditions and streams

A match has two fresh runs, seats 0 and 1, created by the supplied engine with
player IDs `p0` and `p1` and the same uint32 match seed. Both start on day 1,
level 1, with 0 gold, 0 XP, 0 trophies, 5 lives, 1000 HP and ten Normal slots.
Each owns a separate `shopRng(seed)` stream. The streams initially agree and
advance only through that run's shop rolls and rerolls. Bot randomness never
consumes engine randomness. No match-selection stream is used: the rival is
explicitly the other bot's snapshot.

### 3.2 Day order

1. For each run, grant `income(level)`, restore HP to max HP, and roll its
   shop unless frozen, using its persistent shop stream. Frozen shops retain
   their offers, including empty bought slots, and stay frozen until toggled
   or manually rerolled. The reroll counter persists across days.
2. Run seat 0's action phase, then seat 1's. Neither sees the other's current
   actions or current tower. A buy includes placement and automatic merging.
3. Derive both snapshots through `SnapshotStore.ingest` before either combat.
4. Each run independently calls the reference engine's `resolveCombat` against
   the other pre-combat snapshot. Each is combat side A in its own fight. Use
   the reference engine's seeded combat and outcome progression unchanged.
   The two fights need not have complementary outcomes because frame order
   favors side A in some towers. This is asynchronous PvP, as in v1.0.
5. Record each run's own result and opponent snapshot. If either run ends,
   stop the match. Otherwise call `endDay` on both and repeat.

The simulator owns the shop stream because `startDay(run)` in the public
engine API does not accept the stream needed by `reroll(run, rng)`. A visible
self-test compares the simulator's day start with the engine's on runs without
rerolls. Combat, purchases, sales, XP and snapshot validation use engine calls.

### 3.3 Snapshot rejection

Legal placements can merge into a rarity that snapshot validation rejects.
For example, a legendary at level 1 fails the public v1.0 rarity rule. Actions
still apply under the engine's placement rules. At snapshot ingestion, one
rejected tower forfeits the match; two rejected towers draw. Neither fight
runs that day. Record the error codes. Do not turn an unexpected engine
exception into an ordinary loss or an illegal action: abort the evaluation.

### 3.4 Termination

A run wins at 10 trophies and loses at 0 lives, as implemented by the reference
engine, which clamps exhausted lives to zero. Wins take precedence over loss.
Every combat grants a trophy or costs at least one life, so a run cannot remain
active indefinitely. A 30-day guard aborts the evaluation if both remain active;
it is an engine-fault guard, not a normal draw or scoring cap.

### 3.5 Match points

After the first day on which either run ends, compare the tuples
`(statusRank, trophies, lives)` lexicographically, where won=2, active=1,
lost=0. The greater tuple wins. Equal tuples draw. Remaining HP, gold, level,
XP and wall time are not tie-breakers. Snapshot forfeits override this tuple.
A win scores 1 point, a draw 0.5 and a loss 0.

Stopping when either run ends prevents a survivor from farming a stale tower.
These are match points, not a claim that the winning bot completed a ten-trophy
run. The output separately counts completed ten-trophy runs as `runsWon`.

## 4. Decisions and actions

### 4.1 State

Each call receives a fresh deep copy of:

| Field | Meaning |
|---|---|
| `contract` | Literal `t5/1` |
| `run` | Own `RunState`; seed replaced with 0, playerId with `self` |
| `lastCombat` | Null on day 1; otherwise yesterday's opponent snapshot, result and own HP left |
| `botSeed` | uint32 bot seed, constant within a day |
| `call` | Zero-based decision index this day |
| `callsLeft` | `32 - call`, including this call |
| `actionsLeft` | `64 - attempted actions so far this day` |

In `lastCombat`, `result` is `A` for an own win, `B` for an own loss or `draw`.
The snapshot player ID is replaced with `opponent`. The current opponent is
never supplied. Mutating the input has no effect on the engine.

For match seed `seed`, seat `side` and current `day`, compute:

```ts
mixed = (seed ^ 0x7f4a7c15 ^ Math.imul(day, 0x9e3779b1)
         ^ Math.imul(side + 1, 0x85ebca6b)) >>> 0;
botSeed = Math.floor(new Rng(mixed).next() * 4294967296) >>> 0;
```

This removes direct dependence on raw engine-seed fields. It does not hide
seed identity cryptographically or prevent recognizing shops. Visible-seed
hard-coding remains a measurement risk for issue #41.

### 4.2 Action processing

| Action | Fields | Effect |
|---|---|---|
| `buy` | integer `offer`, integer `slot` | Buy the indexed offer into an empty compatible slot; merge immediately |
| `sell` | integer `slot` | Sell the occupying item for the engine's refund |
| `reroll` | none | Pay the run's next reroll cost and draw seven offers |
| `freeze` | none | Toggle the frozen flag |
| `buy_xp` | none | Spend 4 gold for 4 XP; allowed even at max level |
| `end` | none | End today's action phase |

No separate move, place, combat or end-day action exists. The tower has ten
Normal slots; buying determines placement. Offer indices are 0..6 and slot
indices 0..9. Non-integers or missing fields are malformed. Integer values
outside the engine's accepted range are illegal. Extra object fields are
ignored. Unknown action types are malformed.

Process the returned array in order against the evolving run. Each non-`end`
element consumes one action allowance, including a malformed or illegal one.
Successful prefix actions remain applied. An illegal action changes nothing:
restore run state if the engine rejects after partial mutation. A rejected
engine action does not draw from the shop stream.

At the first malformed or illegal element, discard the remaining batch and
request a fresh decision if allowances remain. `end` discards the tail and
ends the phase without consuming an action. An empty array ends the phase.
A thrown decision, non-array return or unserializable return ends the phase
and increments its diagnostic counter. These failures do not forfeit the
match; the tower fights with the actions already accepted.

`legalActions(run)` lists buys by offer then slot, sells by slot, then reroll,
freeze, buy_xp, end, omitting unaffordable or impossible moves. It changes no
state and draws no RNG values. It expresses legality, not strategic value.

### 4.3 Daily limits

Each bot gets at most 32 decision calls and 64 attempted actions per day.
Allowances reset next day. Hitting either limit ends the action phase before
another call. If the action limit is reached mid-batch, discard the entire
remaining tail, even if the next element is `end`. Free actions count too.
These deterministic limits bound a bot that repeatedly toggles freeze.

### 4.4 Time limits and void evaluations

The module import budget is 5000 ms. Each synchronous invocation of `decide`,
including a purity probe, has a 1000 ms wall-clock budget. Input copying and
output serialization are outside the decision timer. If a decision returns
or throws after that budget, `actionPhase` immediately throws
`EvaluationVoidError`, before applying its returned actions. The whole
evaluation has no score; prior completed matches do not make a partial score.
A late module import also voids the evaluation. CLI failures exit nonzero and
emit no score JSON. Always check the exit status; an existing `--out` file
from an older invocation is not replaced on failure.

The local runner measures returning calls; it cannot interrupt synchronous
JavaScript at the exact per-call deadline. A process-level timeout of 120
seconds, followed by a kill after five seconds, stops non-returning bots or
imports. `OAKEN_T5_TIMEOUT` changes this development fail-safe. Timeout exits
124, or 137 if killing is needed, and yields no score. It does not turn a slow
bot into a loss, which would make the score depend on machine speed.

Timing is diagnostic only. Scored JSON and its digests contain no measured
wall-clock values, timestamps or over-budget counts. Record hardware and
runtime separately when comparing budget acceptance. Use the same runtime
version for byte-for-byte reproduction.

### 4.5 Purity checks and their limits

CLI scoring calls each returning, serializable decision twice with independent
copies of the same input and compares the JSON encodings. Encode the first
output before the second call so reuse of a mutable array cannot conceal a
change. A mismatch or a second-call exception marks the evaluation impure;
the CLI emits no score. Probe calls do not consume game call/action allowances.

The CLI disables `Math.random` and `Date.now`, freezes item definitions and
exported engine classes/constants before importing bots. Those measures catch
common mistakes. They are not a security sandbox or proof of purity. Bots
share a process and can still access ambient APIs if they violate the contract.
A bot may also defeat a two-call check. The independently authored held-out
runner needs process isolation and a fixed resource policy for untrusted code.

## 5. Baselines and visible score

### 5.1 Random

Enumerate legal actions, group by type in buy/sell/reroll/freeze/buy_xp/end
order, choose uniformly among available types, then uniformly among that
type's instances. Return one action per call. Its RNG seed is
`(botSeed ^ Math.imul(call + 1, 0x9e3779b1)) >>> 0`.
Uniform choice among all action instances would heavily weight buys because
there are up to 70 placements. This baseline deliberately weights types.

### 5.2 Cheapest

Among affordable, placeable offers, buy the cheapest. Break cost ties by
lowest offer index and use the lowest compatible free slot. Return one buy
per call and recompute on fresh state so merges cannot leave stale slots.
End when no purchase is possible. Never sell, reroll, freeze or buy XP.

### 5.3 Merger

Rank nonempty offers by copies already held of the same ID and rarity:
completes a triple first, makes a pair second, unmatched third. Legendary
offers always rank unmatched, because legendary copies never merge. Break
ties by cost then offer index. Walk this order until a feasible purchase:

- If affordable with a compatible free slot, buy at the lowest such slot.
- If it completes a triple but has no free slot, choose the cheapest unrelated
  occupying item whose sale makes a compatible slot. Rank victims by refund,
  then slot. If that victim's refund makes the offer affordable, return its
  sale followed by the purchase at that slot. Preserve the two matching copies.
- Otherwise consider the next ranked offer. End if none qualifies.

This heuristic may miss a purchase made affordable by selling a more valuable
victim. That weakness is fixed baseline behavior. It never sells merely for
a pair and never rerolls, freezes or buys XP.

### 5.4 Pairing and aggregation

The frozen visible list is the integers 1 through 100 in ascending order.
No seeds were selected based on outcomes. For every seed, run `(a,b)` and
then `(b,a)` with fresh runs. Express both results from bot a's perspective.
The random bot gets both seat seeds; changing bot order cannot change the
paired score. `play --json` returns rows in seed order, first normal then
swapped. All per-side arrays in those rows are normalized to `[a,b]`.

`winRate = sum(match points) / (2 * number of seeds)`. Draws count as half.
This is a bounded score in [0,1], not an uncapped metric. More seeds improve
resolution; they do not remove the ceiling. Report wins/draws/losses and the
actual denominator. Rates and means round to four decimal places using
`Number(value.toFixed(4))`. Integer counts and half-integer points are exact.

The matrix evaluates each unordered pair, including self-play, in supplied
bot-list order. Its reverse entry is one minus the forward rate. Self-play
averages 0.5 because the two seat results reverse, even when individual
random-bot games do not draw. The diagonal does not estimate bot strength.
Counters aggregate both participants of every evaluated pair, including both
participants of self-play. They do not count an extra reversed matrix cell.

Counters are `calls`, `actions`, `illegal`, `malformed`, `decideErrors`,
`dropped` batch-tail elements, and `impure` mismatches. A non-array or
unserializable return adds one malformed count and no action count. Dropped
tails consume no action allowance. These counters are separate from points.

## 6. CLI and artifacts

### 6.1 Commands

```bash
t5/bin/t5-sim play --bot baseline:random --vs baseline:merger --json
t5/bin/t5-sim play --bot ./my-bot.mjs --vs baseline:cheapest --seeds 1-20
t5/bin/t5-sim matrix --out /tmp/t5-matrix.json
t5/bin/t5-sim selftest
t5/bin/t5-sim test
```

Baseline names are `random`, `cheapest`, `merger`. `--seeds` accepts `visible`,
an inclusive range, comma-separated uint32 integers, or a JSON filename
containing an array or an object with a `seeds` array. Reject empty lists,
duplicates, invalid seeds and lists longer than 10,000 before evaluation.
Unknown or repeated options fail. Custom seeds stay labeled by source, list
hash and whether every value belongs to the frozen visible list.

JSON includes protocol/contract/schema versions, runtime version, source
hashes for simulator and bots, engine-content hash, seed-list hash and score
counts. `play --json` adds deterministic per-match records; matrix aggregates
them. Human-readable `play` also reports timing and is not a deterministic
artifact. `baselines.visible.json` records the frozen default matrix.

Compare only identical protocol, engine, simulator, bot and seed digests.
Runtime changes require revalidation. Different bot source paths change the
provenance text; byte equality assumes identical command arguments too.

### 6.2 Invocation log

Successful play/matrix calls append one JSON line to `.t5-sim-log.jsonl` in
the working directory. `OAKEN_T5_SIM_LOG` selects a path or `off` disables it.
Records contain invocation arguments, hashes, counts and totals, never bot
source or decision arguments. Timestamps belong only to this local audit log.
Selftest/test calls and failed evaluations do not create completed-score log
entries. Failure to append warns on stderr without changing game results.
This is development instrumentation; issue #41 owns published behavior metrics.

Bot `treeSha256` hashes sorted relative `.ts/.mts/.js/.mjs/.json` files under
the entry's directory, excluding dot directories, node_modules, hidden,
refengine and symlinks. Keep each strategy in its own directory and outputs
elsewhere. Imports outside that directory are not covered, so this hash is
not an attestation of an arbitrary module dependency graph.

## 7. Runtime and blinding

The launcher needs Linux, Bash, GNU coreutils, tar, OpenSSL and Node 24+ built
with TypeScript support. `OAKEN_T5_NODE` selects the executable. It checks the
sealed reference archive's decrypted digest, extracts only that bundle into
a temporary directory, stages public item data, and removes temporary files
on exit. It never decrypts or executes the held-out suite. No npm dependency
is needed at runtime.

`OAKEN_T5_ENGINE` may select an existing engine workspace with `src/index.ts`,
`data/items.json` and ES-module package metadata for development. Such outputs
carry that engine's content hash; an override is not automatically the pinned
reference engine. Type checking uses public stubs through `t5/tsconfig.json`.

Authorship and recovery provenance are in `t5/AUTHORSHIP.md`. The authors of
this contract must not author the T5 oracle. Issue #40 chooses and seals the
held-out seeds, baseline snapshot pool, scoring details and confidence
interval method under a separate protocol. It owns `results/t5-*` registration.
This visible tool does not write published T2 fields or register a substitute
T5 held-out scorer.
