# Tower auto-battler: engine specification v1.1

v1.1 extends the v1.0 engine with encounters, perks and a fast-cooldown item
whose multicast makes the per-frame trigger cap bind. Encounters are PvE
fights against fixed towers. Perks are passive modifiers held by a player.

In the repository, v1.0 is `seed/SPEC.md`. In a T4 implementation workspace,
it is `SPEC.md`, beside this document as `SPEC-v1.1.md`.

`SPEC.md` v1.0 stays normative and unchanged. Every rule in it still holds
unless a section below names the v1.0 rule and says how v1.1 extends it.
References of the form "v1.0 §7.2" point into `SPEC.md`. Section numbers
without a prefix point into this document.

As in v1.0, every rule is normative, integer arithmetic uses `Math.floor`
unless stated otherwise, and there is no ambient entropy. `round` and `ceil`
mean JavaScript `Math.round` and `Math.ceil`.

This is the spec-only deliverable for issue #43, based on the reference-engine
commit `56a5820`. The implementation and independent oracle belong to later
work. T4 reports v1.0 and v1.1 pass counts separately, never summed. The
input limits in section 11 keep unresolved v1.0 behavior out of new assertions.

---

## 1. The v1.0 invariant

**v1.1 changes no observable v1.0 behaviour for v1.0 inputs.** The T4 score
includes the full v1.0 suite, so a v1.1 engine that breaks any v1.0 behaviour
reports those failures in its v1.0 counts.

### 1.1 What counts as a v1.0 input

A call is a **v1.0 input** when all of the following hold:

1. It calls a function or reads a constant that `SPEC.md` v1.0 or the seed's
   `src/*.ts` stubs already export.
2. No argument, and no object reachable from an argument, contains an item id
   listed in `data/items-v1.1.json`.
3. No `RunState` argument has a `perks` or `encountersCleared` key, and no
   `Combatant` argument has a `perks` key holding a non-empty array. (A
   `Combatant.perks` of `[]` behaves exactly like an absent key.)
4. Any new optional `perks` argument to `effectiveStats` or `effectiveTower`
   is omitted or `[]`. A non-empty argument makes the call a v1.1 input.
5. No v1.1 function in section 9 has been called on any object the call reads.

### 1.2 What must be identical

For every v1.0 input, a v1.1 engine produces exactly what the v1.0 engine it
was built from produces:

- the same return value, compared by deep equality, including key sets (no
  new keys appear on any returned or mutated object);
- the same mutations of its arguments;
- the same thrown `EngineError` code, or the same absence of a throw;
- the same combat event log, event by event and field by field;
- the same number and order of draws from every RNG stream (v1.0 §1.2).

In particular:

- `loadItems()` returns exactly the 20 entries of `data/items.json`, in file
  order. It never includes v1.1 items.
- The shop pool (v1.0 §5, `pick(allItems)`) is exactly `loadItems()`. v1.1
  items are **never** offered by the shop, so no shop roll anywhere changes.
- `createRun()` returns an object with exactly the v1.0 keys. The keys `perks`
  and `encountersCleared` are absent until a v1.1 function first writes them.
- `SnapshotStore.ingest()` derives a snapshot with exactly the v1.0 keys, for
  every run, including runs that hold perks (section 8.4).
- All v1.0 constants keep their values. `cooldownFrames()` is unchanged:
  it does not take perks and never applies quickdraw.

### 1.3 Why v1.0 cannot observe the per-frame cap rule

Section 3 pins down how the per-frame cap resolves a collision. That rule
applies to every item, v1.0 items included, yet it cannot change a v1.0 result.
Every v1.0 item has an effective cooldown of at least 1.0 s, so its primary
triggers are at least 10 frames apart. Its multicast is at most 2, so its
multicast triggers land on the 1 or 2 frames right after a primary. For a
collision, a multicast trigger would have to reach the next primary frame,
which needs `multicast >= cadence >= 10`. No v1.0 item comes close, so no v1.0
item ever has two candidate triggers on one frame (README gap 5), and the tie-break never runs.

---

## 2. Data files

v1.1 adds two frozen data files. They sit next to `data/items.json` in the
T4 workspace. In this repository they live in `t4/data/`.

| File | Contents |
|---|---|
| `data/items-v1.1.json` | New item definitions, same schema as `data/items.json` (v1.0 §2) |
| `data/encounters-v1.1.json` | Encounter definitions (section 6.1) |

Neither file may be edited, and `data/items.json` is not edited either.

### 2.1 The item pool and the shop pool

- The **item pool** is the items of `data/items.json` followed by the items of
  `data/items-v1.1.json`. No id appears in both files.
- `getItem(id)` (v1.0 §2, `src/items.ts`) resolves ids from the whole item pool.
  It still throws `UNKNOWN_ITEM` for an id in neither file.
- Every v1.0 rule that looks items up works across the whole item pool:
  placement and slot restrictions (v1.0 §3.1), triple merge (v1.0 §3.2),
  selling (v1.0 §5), effective stats and tag synergies (v1.0 §3.1, §6),
  combat (v1.0 §7), and snapshot validation (v1.0 §9.1: `SNAPSHOT_UNKNOWN_ITEM`
  is raised only for an id outside the whole item pool). Rarity legality
  (v1.0 §9.2) applies to v1.1 items exactly as to v1.0 items.
- The **shop pool** is `loadItems()` only (section 1.2). A v1.1 item enters a
  tower through `place()` directly, or as an encounter reward (section 6.4).
- `loadItemsV11(): ItemDef[]` (`src/items.ts`) returns the entries of
  `data/items-v1.1.json` in file order.

---

## 3. The per-frame trigger cap, fully specified

v1.0 §7.2 states the cap (at most one trigger per item per frame, extra
triggers dropped, not deferred) but never says which trigger survives a
collision, because no v1.0 item can produce one. v1.1 fixes it.

### 3.1 Primary and multicast triggers

For one item with effective cooldown `C` (after the v1.0 1.0 s floor) and
effective multicast `M`, let `cadence = round(C * 10)`.

- The item's **primary frames** are `P = [p0, p0 + cadence, p0 + 2*cadence, ...]`,
  keeping only frames `<= 600`. Without quickdraw, `p0 = cadence`, so `P`
  equals `cooldownFrames(C)`. With quickdraw, `p0` changes (section 5.3).
- Each primary frame `p` produces one **candidate primary trigger** at `p`, and
  `M` **candidate multicast triggers**, one at each of `p + 1, ..., p + M`.
  A candidate at a frame `> 600` is discarded.
- Multicast candidates come only from primary frames. A multicast trigger never
  spawns further multicast triggers.

### 3.2 Collision resolution

The cap is per item (per occupied slot). Two different items, even two copies
of the same item in different slots, never collide with each other.

For each frame `f` and each item, collect that item's candidates at `f`:

1. If a candidate primary trigger is at `f`, the item triggers once at `f` as a
   primary trigger (`multicast: false` in its `trigger` event).
2. Otherwise, if at least one candidate multicast trigger is at `f`, the item
   triggers once at `f` as a multicast trigger (`multicast: true`).
3. Otherwise the item does not trigger at `f`.

Every other candidate at `f` is **dropped**. A dropped candidate draws nothing
from any RNG stream, emits no event, and has no effect. It is not moved to any
other frame.

The resulting schedule is fixed at the start of combat. It depends only on
`C`, `M` and the owner's perks, never on anything that happens during combat.

### 3.3 `triggerSchedule`

`src/combat.ts` exports:

```ts
triggerSchedule(cooldownSeconds: number, multicast: number,
                perks?: readonly PerkId[]): ScheduledTrigger[]
// ScheduledTrigger = { frame: number; multicast: boolean }   (src/types.ts)
```

It returns the surviving triggers from 3.1 and 3.2 for one item, one entry per
frame, in ascending `frame` order. `perks` defaults to `[]`, and only
`quickdraw` changes the result. Callers pass `cooldownSeconds > 0` and an
integer `multicast >= 0`, both finite. Apply the 1.0 s cooldown floor before
computing `cadence`, even for a direct call with a value below 1.0. Other
numeric inputs are undefined in v1.1. An unknown id in
`perks` throws `UNKNOWN_PERK`.

### 3.4 How `simulate` uses the schedule

In a combat where **no item on either side has effective `shock > 0`**, the
following holds for every item: the frames on which `simulate` emits a
`trigger` event for that item are exactly the frames of
`triggerSchedule(effective cooldown, effective multicast, owner's perks)`
up to the frame the combat ends, and each `trigger` event's `multicast` field
equals the matching entry's `multicast`. Within a frame, v1.0 §7.3's order
(side A ascending slot, then side B ascending slot) is unchanged.

For v1.0 inputs, preserve the reference engine's shock behavior. The
interaction of shock with v1.1 combat features is outside the input domain
specified here. See section 11.1.

---

## 4. The fast-cooldown item: Hummingbird Needle

`data/items-v1.1.json` holds one item:

| field | value |
|---|---|
| `id` | `hummingbird_needle` |
| `name` | `Hummingbird Needle` |
| `type` | `Dagger` (Melee, v1.0 §2.1) |
| `tag` | `Neutral` |

| rarity | cost | cooldown | damage | multicast |
|---|---|---|---|---|
| 0 Common | 30 | 1.0 | 2 | 3 |
| 1 Rare | 72 | 1.0 | 3 | 6 |
| 2 Epic | 144 | 1.0 | 4 | 10 |
| 3 Legendary | 360 | 1.0 | 6 | 15 |

Every other tier field is `0`, or `null` for `startOfCombat`. The data file
is authoritative. The table above restates it.

Its cooldown is the v1.0 floor, so its cadence is 10 frames. Common and Rare
multicast stay below the cadence and never collide. Epic (`10`) is the
smallest multicast at which a multicast trigger lands on the next primary
frame. Legendary (`15`) overlaps both the next primary and the next primary's
own multicast window.

The needle is an ordinary item in every other respect. It takes slot effects
(a `Crit` slot gives it `crit = 15`, so each surviving trigger draws one crit
value), it merges (three needles of one rarity become one of the next rarity),
it sells for `floor(cost / 2)`, and a `Magic` slot rejects it with
`SLOT_TYPE_MISMATCH`.

---

## 5. Perks

A perk is a passive modifier held by a player for the whole run. v1.1 has four
perks, with these ids and this canonical order:

| id | stage | effect |
|---|---|---|
| `whetstone` | effective stats | every item with effective `damage > 0` gets `damage += 5` |
| `quickdraw` | trigger schedule | first primary trigger at `ceil(cadence / 2)` instead of `cadence` |
| `bulwark` | combat start | the owner's shield pool starts combat at `100` instead of `0` |
| `fortune` | day start | `startDay` grants `10` extra gold |

`src/types.ts` exports `type PerkId = 'whetstone' | 'quickdraw' | 'bulwark' | 'fortune'`.
`src/perks.ts` exports `PERK_IDS` (the four ids in canonical order) and the
constants `WHETSTONE_DAMAGE = 5`, `BULWARK_SHIELD = 100`, `FORTUNE_GOLD = 10`.

Each perk acts at the stage shown above. The order of ids in a `perks` array
has no effect. An id that appears more than once in a `Combatant.perks` or
effective-stat argument applies once.

### 5.1 Holding perks

- `RunState` gains the optional key `perks?: PerkId[]`, the perks the run
  holds, in the order they were granted. It is absent until the first perk is
  granted.
- `Combatant` gains the optional key `perks?: readonly PerkId[]`. `simulate`
  applies a side's perks to that side only. Absent means `[]`.
- `grantPerk(run: RunState, perk: PerkId): RunState` (`src/perks.ts`):
  1. If `perk` is not one of the four ids, throw `UNKNOWN_PERK`.
  2. If `run.perks` already contains `perk`, throw `PERK_ALREADY_HELD`.
  3. Otherwise create `run.perks = []` if the key is absent, append `perk`,
     and return `run` (the same object, mutated).

  `grantPerk` does not check `run.status` and changes nothing else. Either
  error leaves the run unchanged, including whether `perks` is absent.
- `RunState` also gains `encountersCleared?: string[]`, in victory order.
  It is absent until the first encounter win.
- `startDay`, `endDay` and `resolveCombat` preserve both new arrays, including
  their order and whether either key is absent. A perk applies only to future
  operations. Granting `fortune` after `startDay` does not grant gold for that
  day retroactively; it applies on the next call to `startDay`.
- A run's perks apply to the run's own side only. Snapshots do not carry
  perks (section 8.4), so an opponent snapshot always fights without perks.

### 5.2 whetstone: effective stats

v1.0 §3.1 computes effective stats as base tier stats, then slot effect, then
tag synergy (v1.0 §6). v1.1 appends a fourth stage: **perks**.

With `whetstone`, after the tag-synergy stage: if `damage > 0`, then
`damage += 5`. An item whose damage is `0` after synergy (a shield, `ember_orb`)
is unchanged. No other stat changes.

`effectiveStats` and `effectiveTower` (`src/items.ts`) take an optional last
argument:

```ts
effectiveStats(tower: Tower, slot: number, perks?: readonly PerkId[]): EffectiveStats
effectiveTower(tower: Tower, perks?: readonly PerkId[]): EffectiveStats[]
```

Omitted or `[]` gives exactly the v1.0 result. An unknown id in `perks` throws
`UNKNOWN_PERK`, checked before anything else (before `EMPTY_SLOT`).
`simulate` computes each side's items with that side's perks. Of the four perks
only `whetstone` changes effective stats, and no perk changes `cooldown`,
`multicast`, `crit` or the side damage multiplier (v1.0 §7.1).

### 5.3 quickdraw: trigger schedule

With `quickdraw`, every item on that side has
`p0 = ceil(cadence / 2)` (section 3.1) instead of `p0 = cadence`. Later primary
frames stay `cadence` apart, and multicast and the cap work as in section 3.

| cooldown (s) | 1.0 | 2.0 | 2.5 | 3.0 | 3.5 | 4.0 | 5.0 | 6.0 | 8.0 |
|---|---|---|---|---|---|---|---|---|---|
| cadence | 10 | 20 | 25 | 30 | 35 | 40 | 50 | 60 | 80 |
| first three primaries | 5, 15, 25 | 10, 30, 50 | 13, 38, 63 | 15, 45, 75 | 18, 53, 88 | 20, 60, 100 | 25, 75, 125 | 30, 90, 150 | 40, 120, 200 |

quickdraw does not affect `cooldownFrames()` (which takes no perks) or frame 0
start-of-combat resolution (v1.0 §7.1).

### 5.4 bulwark: combat start

With `bulwark`, that side's shield pool is `100` when frame 1 begins, instead
of `0`. No event is emitted for it. The pool then behaves exactly as v1.0 §7.7
says: damage is absorbed from it first, and shield items add to it.

### 5.5 fortune: day start

With `fortune` in `run.perks`, v1.0 §8 step 2 grants `income(level) + 10` gold
instead of `income(level)`. `income()` itself is unchanged. It still returns
`40 + 5 * level`.

---

## 6. Encounters

An encounter is a PvE combat against a fixed tower defined in
`data/encounters-v1.1.json`. Beating it grants a one-time reward. An encounter
never changes lives, trophies, XP, level, day, status, `hp` or `maxHp`.

### 6.1 Encounter data

`data/encounters-v1.1.json` is an array of:

```jsonc
{
  "id": "goblin_ambush",
  "name": "Goblin Ambush",
  "hp": 300,                 // the encounter's starting hp AND maxHp
  "slots": ["Normal", ...],  // exactly 10 SlotType values
  "entries": [ { "slot": 0, "itemId": "bone_club", "rarity": 0 }, ... ],
  "reward": {
    "gold": 20,
    "perk": null,            // a PerkId, or null
    "item": { "itemId": "hummingbird_needle", "rarity": 0 }   // or null
  }
}
```

`src/types.ts` exports these interfaces, using the existing `Rarity`,
`SlotType` and `TowerEntry` types:

```ts
export interface EncounterReward {
  gold: number;
  perk: PerkId | null;
  item: { itemId: string; rarity: Rarity } | null;
}

export interface EncounterDef {
  id: string;
  name: string;
  hp: number;
  slots: SlotType[];
  entries: TowerEntry[];
  reward: EncounterReward;
}

export interface ScheduledTrigger {
  frame: number;
  multicast: boolean;
}
```

The file holds five encounters. The data file is authoritative. This table
restates it (rarity in parentheses, slot types `Normal` unless noted):

| id | hp | tower | reward gold | reward perk | reward item |
|---|---|---|---|---|---|
| `goblin_ambush` | 300 | 0: bone_club (0), 1: iron_sword (0) | 20 | none | hummingbird_needle (0) |
| `needle_swarm` | 400 | 0: hummingbird_needle (2), 1: tower_shield (0) | 30 | quickdraw | none |
| `iron_warden` | 1200 | 0 (`Damage` slot): war_axe (1), 1: glacier_shield (1), 2: bone_club (1) | 40 | bulwark | none |
| `ember_court` | 900 | 0: flame_brand (1), 1: pyre_staff (1), 2: ember_orb (1) | 40 | whetstone | none |
| `gilded_hoard` | 1500 | 0: long_spear (1), 1: hunters_bow (1), 2: tower_shield (1) | 60 | fortune | none |

No encounter tower holds an item with `shock`, `heal` or `lifesteal`.

`src/encounters.ts` exports:

- `loadEncounters(): EncounterDef[]`: the file's entries in file order.
- `getEncounter(id: string): EncounterDef`: throws `UNKNOWN_ENCOUNTER` for an
  id not in the file.
- `ENCOUNTER_SEED_SALT = 0x68E31DA4`.
- `encounterSeed(run: RunState): number` = `(run.seed ^ ENCOUNTER_SEED_SALT ^ run.day) >>> 0`.
- `resolveEncounter(run: RunState, encounterId: string): CombatResult`.

### 6.2 `resolveEncounter`: checks

In this order, the first failure is thrown and nothing is changed:

1. `encounterId` is not in the file: `UNKNOWN_ENCOUNTER`.
2. `run.status !== 'active'`: `RUN_NOT_ACTIVE`.
3. `run.encountersCleared` contains `encounterId`: `ENCOUNTER_ALREADY_CLEARED`.

### 6.3 `resolveEncounter`: the combat

1. Side A is the run: `{ tower: run.tower, hp: run.hp, maxHp: run.maxHp, perks: run.perks ?? [] }`.
2. Side B is the encounter: its `slots` and `entries` as the tower, `hp` and
   `maxHp` both equal to the encounter's `hp`, and no perks.
3. Call `simulate(A, B, encounterSeed(run))`. All of v1.0 §7 and sections 3 to
   5 apply. The combat stream is seeded from this value as v1.0 §1.2 says:
   `encounterSeed(run) ^ 0x1B873593`.
4. The encounter reads `run.hp` as it is. `resolveEncounter` does not restore
   it first, and does not write it afterwards: `run.hp` is the same before and
   after the call, whatever `hpA` the combat ends with.
5. The encounter's own data is never mutated.

`resolveEncounter` returns the `CombatResult` from `simulate`, unmodified.

### 6.4 `resolveEncounter`: the outcome

If the combat's `result` is `'A'` (the run won), apply in this order:

1. `run.gold += reward.gold`.
2. Create `run.encountersCleared = []` if absent, then append `encounterId`.
3. If `reward.perk` is not `null` and `run.perks` does not already contain it,
   create `run.perks = []` if absent and append the perk. If the run already
   holds it, nothing happens (no error, no substitute).
4. If `reward.item` is not `null`, find the lowest slot index `s` in `0..9`
   that is empty and whose slot type accepts the item (v1.0 §3.1: `Magic`
   accepts only Magic items, `Melee` accepts only Melee items, every other
   type accepts anything).
   - If there is one, place the item there at `reward.item.rarity`, then apply
     triple merges to fixpoint (v1.0 §3.2), exactly as `place()` does.
   - If there is none, the item is not placed, and
     `run.gold += floor(cost / 2)` where `cost` is the reward item's tier cost
     (the v1.0 §5 sell refund).

If `result` is `'B'` or `'draw'`, the run is not changed at all. The encounter
is not cleared and may be fought again. With the same tower, perks, `hp`,
`maxHp`, `seed` and `day`, a repeat gives the same result.

Apart from the steps above, `resolveEncounter` changes nothing on `run`:
not `hp`, `maxHp`, `xp`, `level`, `lives`, `trophies`, `day`, `status`, `shop`,
nor any RNG stream the run owns. It draws nothing from the shop or match
streams.

Nothing forces an order between encounters and the rest of the day (v1.0 §8).
An encounter can be fought at any point while the run is active. If it is
fought after `resolveCombat` on a day the run lost, `run.hp` may already be
`<= 0`, and the encounter combat then ends at frame 1 with side A dead
(v1.0 §7.6).

---

## 7. Changes to v1.0 functions

Each of these keeps its v1.0 behaviour for v1.0 inputs (section 1).

| function | v1.1 change |
|---|---|
| `getItem(id)` | also resolves ids from `data/items-v1.1.json` (section 2.1) |
| `effectiveStats`, `effectiveTower` | optional `perks` argument (section 5.2) |
| `simulate(a, b, matchSeed)` | reads `a.perks` and `b.perks`, and throws `UNKNOWN_PERK` for an unknown id in either before emitting any event; perks apply per sections 5.2 to 5.4; the cap resolves per section 3 |
| `startDay(run)` | adds `FORTUNE_GOLD` when `run.perks` contains `fortune` (section 5.5) |
| `resolveCombat(run, opponent)` | side A gets `perks: run.perks ?? []`; side B has no perks; everything else as v1.0 |
| `SnapshotStore.validate` / `ingest` | item pool extended (section 2.1); see 8.4 |

No other v1.0 function changes behaviour.

---

## 8. Interactions, stated explicitly

### 8.1 Perks and v1.0 §7.1 start of combat

No perk adds, removes or changes a `start_of_combat` event or a side damage
multiplier.

### 8.2 whetstone and the side multiplier or crit

Let `D` be damage after slot effects and synergy, before perks. When `D > 0`,
whetstone makes effective damage `D + 5`. v1.0 §7.4 step 2 then computes
`dealt = floor((D + 5) * (crit ? 2 : 1) * sideDamageMultiplier)`.
The `+5` applies once. When `D == 0`, whetstone adds nothing.
A shield item in a `Damage` slot has `D = 10`, so whetstone raises it to 15.

### 8.3 bulwark and shield items

The initial 100 is part of the same pool that shield items add to. Nothing
separates or resets it during combat.

### 8.4 Snapshots

`SnapshotStore.ingest(run)` derives the snapshot from the same fields as v1.0.
It never copies `perks` or `encountersCleared`, and the snapshot never has a
`perks` key. A snapshot may hold v1.1 items. They validate like any other item
(v1.0 §9.1, §9.2).

### 8.5 Shop

Nothing in v1.1 changes the shop, its rolls, its costs, or its stream.
v1.1 items are never offered.

---

## 9. New exports

All of these are also re-exported from `src/index.ts`.

| module | export |
|---|---|
| `src/types.ts` | `PerkId`, `EncounterDef`, `EncounterReward`, `ScheduledTrigger`; optional keys `RunState.perks`, `RunState.encountersCleared`, `Combatant.perks`; the error codes of section 10 |
| `src/items.ts` | `loadItemsV11()` |
| `src/combat.ts` | `triggerSchedule()` |
| `src/perks.ts` | `PERK_IDS`, `WHETSTONE_DAMAGE`, `BULWARK_SHIELD`, `FORTUNE_GOLD`, `grantPerk()` |
| `src/encounters.ts` | `ENCOUNTER_SEED_SALT`, `loadEncounters()`, `getEncounter()`, `encounterSeed()`, `resolveEncounter()` |

v1.0's rule for `src/types.ts` still holds: no exported symbol is renamed or
removed.

---

## 10. New error codes

Added to the `ErrorCode` union and thrown as `EngineError`:

| code | thrown by |
|---|---|
| `UNKNOWN_PERK` | `grantPerk`, `effectiveStats`, `effectiveTower`, `triggerSchedule`, `simulate` |
| `PERK_ALREADY_HELD` | `grantPerk` |
| `UNKNOWN_ENCOUNTER` | `getEncounter`, `resolveEncounter` |
| `RUN_NOT_ACTIVE` | `resolveEncounter` |
| `ENCOUNTER_ALREADY_CLEARED` | `resolveEncounter` |

---

## 11. Known v1.0 ambiguities v1.1 does not rely on

These are places where `SPEC.md` v1.0 is silent or under-determined. v1.1 does
not resolve them, and no v1.1 rule depends on how an engine resolves them.

1. **Shock and multicast (v1.0 §7.2, §7.4).** v1.0 says that player statuses
   apply immediately and shock skips a trigger entirely. It does not explicitly
   say whether a skipped primary schedules multicast, or how a skipped primary
   interacts with colliding multicast candidates. Section 3.4 covers combats
   with no effective `shock > 0` on either side. No encounter holds a shock item;
   the player's tower must also meet that condition for v1.1 combat assertions.
2. **README gap 1.** Whether `start_of_combat` is emitted for a side with no
   start-of-combat items.
3. **README gap 2.** The `end` event's `side` field.
4. **README gap 3.** Whether a `heal` event reports the raw or the clamped
   amount. v1.1 adds no heal or lifesteal source.
5. **README gap 4.** Whether `frozen` persists past a skipped day-start reroll.
6. **The per-day match seed of `resolveCombat`.** v1.0 fixes the combat
   stream's salt but not how `resolveCombat` derives `matchSeed` from the run.
   v1.1 does not define it either. Encounters use `encounterSeed` (section 6.1)
   instead.

### 11.1 Constraints on v1.1 test inputs

Unless a rule explicitly tests invalid input, callers supply values matching
`src/types.ts`, valid towers with unique slots and known item ids, and runs
created by `createRun`. New arrays contain known ids; `run.perks` has no
duplicates, and `run.encountersCleared` has unique encounter ids. Passing an
unknown perk to the functions in section 10 is the specified exception.
Duplicate perks in direct combat and effective-stat arguments are allowed as
section 5 states; `triggerSchedule` also treats duplicates as one perk.

Callers do not mutate definitions returned by `loadItemsV11`, `loadEncounters`
or `getEncounter`. Whether those functions return shared objects or copies is
unspecified. They consume no RNG values. `triggerSchedule`, `encounterSeed`
and effective-stat queries do not mutate their arguments or draw RNG values.
On v1.1 inputs, `simulate` does not mutate its combatants or their towers.

Given the limits above, a v1.1 test asserts only on behavior this document defines:

- no item with effective `shock > 0` on either side of a combat whose result,
  log or schedule it asserts on;
- no assertion on the presence, count or position of `start_of_combat` events,
  on the `end` event's `side`, or on a `heal` event's `amount` where the
  `maxHp` clamp binds; filter the log to the event types under test instead;
- no assertion on whether a frozen shop flag persists after a skipped
  day-start reroll;
- no assertion on `resolveCombat` output that depends on its match seed (use
  towers without crit when asserting on `resolveCombat` with perks, or call
  `simulate` directly with an explicit seed).

---

## 12. Worked examples

Every number below follows from the rules above and `SPEC.md` v1.0.
`N` stands for a tower of 10 `Normal` slots. "Needle (r)" is
`hummingbird_needle` at rarity `r`. Where an example calls `simulate`,
`hp = maxHp`.

### 12.1 Needle schedules (section 3)

`triggerSchedule(1.0, M, perks)` for each needle tier. "Candidates" counts
every candidate from section 3.1 at frames `<= 600`. "Dropped" is candidates
minus survivors.

| tier | M | perks | candidates | entries | dropped | `multicast: true` entries |
|---|---|---|---|---|---|---|
| Common | 3 | none | 237 | 237 | 0 | 177 |
| Common | 3 | quickdraw | 240 | 240 | 0 | 180 |
| Rare | 6 | none | 414 | 414 | 0 | 354 |
| Rare | 6 | quickdraw | 419 | 419 | 0 | 359 |
| Epic | 10 | none | 650 | 591 | 59 | 531 |
| Epic | 10 | quickdraw | 655 | 596 | 59 | 536 |
| Legendary | 15 | none | 940 | 591 | 349 | 531 |
| Legendary | 15 | quickdraw | 950 | 596 | 354 | 536 |

Details:

- Common, no perks: `10 P, 11 m, 12 m, 13 m, 20 P, 21 m, ...`
  (`P` = `multicast: false`, `m` = `multicast: true`). The last entries are
  `590 P, 591 m, 592 m, 593 m, 600 P`. Candidates from primary 600 at
  601 to 603 are discarded.
- Epic, no perks: exactly one entry at every frame `10..600`. The entry is
  `multicast: false` at every multiple of 10 and `true` at every other frame.
  At frame 20 the primary and the multicast from 10 (`10 + 10`) collide. The
  primary wins, so `{frame: 20, multicast: false}`.
- Legendary, no perks: the same frames and flags as Epic. At frame 21 the
  multicasts from 10 (`+11`) and 20 (`+1`) collide, and one survives as
  `{frame: 21, multicast: true}`.
- Epic with quickdraw: primaries at `5, 15, ..., 595`. One entry at every
  frame `5..600`. The entry at 600 is `{frame: 600, multicast: true}` (from
  `595 + 5`).
- A v1.0 item, for contrast: `triggerSchedule(1.0, 2)` (swift_dagger Epic or
  Legendary) has 178 entries: 60 primaries at `10, 20, ..., 600` and 118
  multicasts at `p + 1` and `p + 2` for `p <= 590`. Nothing is dropped.
  With quickdraw: `5 P, 6 m, 7 m, 15 P, 16 m, 17 m, 25 P, ...`.

### 12.2 The cap in combat

**(a)** A: tower `N`, needle (2) in slot 0, hp 1000. B: empty tower `N`, hp 1000.
`simulate(A, B, 7)`:

- A triggers on every frame from 10, dealing 4 per trigger. After 250 triggers
  (frames `10..259`) B has taken 1000.
- The combat ends at frame 259: `result: 'A'`, `hpA: 1000`, `hpB: 0`.
- A's `trigger` events: 250, of which 25 have `multicast: false` (frames
  `10, 20, ..., 250`) and 225 have `multicast: true`.

**(b)** Dropped triggers draw nothing. A: slot 0 is a `Crit` slot, other
slots `Normal`, needle (2) in slot 0 (effective `crit = 15`), hp 1000. B: empty
tower `N`, hp 100000. `simulate(A, B, 12345)`:

- The combat runs to frame 600: `result: 'draw'`.
- A has 591 `trigger` events and the combat stream supplies exactly 591 crit
  draws, one per surviving trigger, in frame order. Of those, 76 are crits
  (`c < 15`). The first five crits are at frames 11, 24, 25, 43, 50.
- Damage: `(591 - 76) * 4 + 76 * 8 = 2668`, so `hpB = 97332`.

An engine that let the 59 dropped candidates draw would consume 650 values
and shift every later crit.

**(c)** The cap is per item. A: tower `N`, needle (3) in slots 0 and 4, hp 1000.
B: empty tower `N`, hp 1000. `simulate(A, B, 3)`:

- At frame 10 both needles trigger (`slot 0` then `slot 4`, both
  `multicast: false`), then both at 11 (`multicast: true`), and so on.
- Each frame from 10 deals 12. The combat ends at frame 93 with 168 `trigger`
  events on side A, `result: 'A'`, `hpB: -8` (84 frames * 12 = 1008).

### 12.3 quickdraw in combat

A: tower `N`, iron_sword (0) in slot 0 (damage 22, cooldown 2.5, cadence 25),
perks `['quickdraw']`, hp 1000. B: empty tower `N`, hp 100.
`simulate(A, B, 3)`: A triggers at frames 13, 38, 63, 88, 113. After the fifth
hit B has taken 110. The combat ends at frame 113: `result: 'A'`, `hpB: -10`.

### 12.4 whetstone effective stats

Tower slots `[Damage, Melee, Magic, Normal, Normal, Normal, Normal, Normal, Normal, Normal]`,
holding iron_sword (0) in slot 0, iron_sword (1) in slot 1, ember_orb (0) in
slot 2, storm_bow (0) in slot 3, tempest_blade (0) in slot 4, glacier_shield (0)
in slot 5.

| slot | item | v1.0 damage | with `['whetstone']` | why |
|---|---|---|---|---|
| 0 | iron_sword (0) | 32 | 37 | `22 + 10` (Damage slot), then `+5` |
| 1 | iron_sword (1) | 52 | 57 | `floor(44 * 1.2)`, then `+5` |
| 2 | ember_orb (0) | 0 | 0 | damage 0, whetstone does not apply (burn stays `6 + 3*0 = 6`) |
| 3 | storm_bow (0) | 17 | 22 | `14 + 3 * 1` (one other Storm item), then `+5` |
| 4 | tempest_blade (0) | 24 | 29 | `20 + 4 * 1`, then `+5` |
| 5 | glacier_shield (0) | 0 | 0 | damage 0 |

`effectiveTower(tower)` and `effectiveTower(tower, [])` both give the v1.0
column.

### 12.5 bulwark in combat

A: empty tower `N`, perks `['bulwark']`, hp 1000. B: tower `N`, bone_club (0)
in slot 0 (25 damage every 30 frames), hp 1000. `simulate(A, B, 1)`, B's
`damage` events as `(frame, amount, absorbed, toHp)`:
`(30, 25, 25, 0), (60, 25, 25, 0), (90, 25, 25, 0), (120, 25, 25, 0), (150, 25, 0, 25), (180, 25, 0, 25), ...`.
The combat runs to frame 600: 20 hits, the first 4 absorbed, 16 * 25 = 400 to
HP, so `hpA = 600` and `result: 'draw'`. No event marks the initial shield.

### 12.6 fortune

`run = createRun('f', 5)`; `grantPerk(run, 'fortune')`; `startDay(run)`:
`run.gold = 45 + 10 = 55`. Before `grantPerk`, `Object.keys(run)` is the 13
v1.0 keys. After it, `perks` is added as a 14th key, with value `['fortune']`.
`grantPerk(run, 'fortune')` again throws `PERK_ALREADY_HELD`.

### 12.7 Encounters

Common setup for (a) to (c): `run = createRun('p1', 42)`, `startDay(run)`
(gold 45), then `place(run.tower, 0, 'war_axe', 1)` and
`place(run.tower, 1, 'iron_sword', 1)`. `encounterSeed(run)` is
`(42 ^ 0x68E31DA4 ^ 1) >>> 0 = 1759714703`.

**(a) A win.** `resolveEncounter(run, 'goblin_ambush')`:

- A: war_axe (1), 120 damage at 50, 100, ...; iron_sword (1), 44 damage at
  25, 50, 75, 100, .... B (hp 300): bone_club (0), 25 at 30, 60, 90, ...;
  iron_sword (0), 22 at 25, 50, 75, 100, ....
- `damage` events as `(frame, side, slot, amount)`: `(25,A,1,44) (25,B,1,22)
  (30,B,0,25) (50,A,0,120) (50,A,1,44) (50,B,1,22) (60,B,0,25) (75,A,1,44)
  (75,B,1,22) (90,B,0,25) (100,A,0,120) (100,A,1,44) (100,B,1,22)`.
  B still triggers at frame 100 after its hp drops below 0, because the end
  condition is checked at the end of the frame (v1.0 §7.6).
- Result: `result: 'A'`, `hpA: 837`, `hpB: -116`, ended at frame 100.
- The run afterwards: `gold: 65` (45 + 20), `encountersCleared: ['goblin_ambush']`,
  no `perks` key (the reward has no perk), and the needle (0) placed in slot 2,
  the lowest empty slot. `hp: 1000` (unchanged, not 837), `lives: 5`, `xp: 0`,
  `trophies: 0`, `day: 1`.
- A second `resolveEncounter(run, 'goblin_ambush')` throws
  `ENCOUNTER_ALREADY_CLEARED`.

**(b) A loss.** Same setup but with bone_club (0) in slot 0 only.
`resolveEncounter(run, 'iron_warden')`: B's war_axe sits in a `Damage` slot
(130 damage at 50, 100, ...), its bone_club (1) deals 50 at 30, 60, ...,
and its glacier_shield (1) adds 80 shield at 30, 60, .... A's single hit at
frame 30 lands before B's first shield (side A resolves first), so B loses 25
hp. Every later A hit is absorbed. The combat ends at frame 250:
`result: 'B'`, `hpA: -50`, `hpB: 1175`. The run is unchanged: `gold: 45`,
`hp: 1000`, no `encountersCleared` key, no `perks` key.

**(c) A perk reward.** With the common setup,
`resolveEncounter(run, 'needle_swarm')` ends at frame 150, `result: 'A'`,
`hpA: 436`, `hpB: -74`. Afterwards `gold: 75`, `perks: ['quickdraw']`,
`encountersCleared: ['needle_swarm']`, and the tower is unchanged.

**(d) Reward item with no legal slot.** `run = createRun('p1', 42)`,
`startDay(run)`, then `run.tower = createTower(<10 Magic slots>)`,
`place(run.tower, 0, 'pyre_staff', 1)`, `place(run.tower, 1, 'pyre_staff', 3)`.
`resolveEncounter(run, 'goblin_ambush')` is won. The needle is a Dagger, so no
`Magic` slot accepts it, and the run gets its sell value instead:
`gold = 45 + 20 + floor(30 / 2) = 80`. The tower is unchanged.

**(e) Reward item that completes a merge.** `run = createRun('p1', 42)`,
`startDay(run)`, then place war_axe (1) in slot 0, needle (0) in slot 3 and
needle (0) in slot 5. `resolveEncounter(run, 'goblin_ambush')` is won. The
reward needle goes to slot 1 (the lowest empty slot), which completes a triple.
The three merge into needle (1) at slot 1, the lowest of `{1, 3, 5}`.
Final entries: war_axe (1) at 0, needle (1) at 1. `gold: 65`.

### 12.8 Errors

- `grantPerk(run, 'haste')`: `UNKNOWN_PERK`.
- `resolveEncounter(run, 'dragon')`: `UNKNOWN_ENCOUNTER`, even if
  `run.status` is `'lost'` (check 1 comes before check 2).
- `resolveEncounter(run, 'goblin_ambush')` on a run with `status: 'won'`:
  `RUN_NOT_ACTIVE`.
- `effectiveStats(tower, 9, ['haste'])` on a tower whose slot 9 is empty:
  `UNKNOWN_PERK`, not `EMPTY_SLOT`.
- `simulate({ ..., perks: ['haste'] }, b, 1)`: `UNKNOWN_PERK`.
- `place(createTower(Array(10).fill('Magic')), 0, 'hummingbird_needle', 0)`:
  `SLOT_TYPE_MISMATCH`.

---

## 13. Still out of scope

v1.0 §10 still applies, less encounters and perks: seasons, Duos mode,
cosmetics, ranked elo, rendering, HTTP servers, persistence to disk, and a CLI
remain out of scope. Encounter choice, encounter scheduling by day, and perk
drafting are also out of scope. Encounters and perks exist only as specified
above.

---

## 14. Freeze

This document and the two data files in section 2 are frozen by
`t4/FROZEN.sha256`. Verify it from the repository root with
`sha256sum -c t4/FROZEN.sha256`. The root `FROZEN.sha256` remains unchanged.

Draft author: the stopped Claude task for issue #43, worktree
`agent-a866a1f49ac4d5151`. Final spec author and freeze validator: OpenAI Codex,
2026-09-25, task `01a0d7fe-c4bb-75b0-990c-c17667872cfb`. The model identity of
the draft author was not recorded in the recovered files.

Neither spec author may author the T4 oracle. Issue #44 assigns that work to
a separate author after this freeze. The final author read only public specs,
stubs and data, repository guidance, issue bodies and totals-only provenance.
No held-out suite or reference-engine implementation was opened or decrypted.

Do not amend these three frozen files after handoff to the oracle author.
Record any later gap next to the files instead of editing them. `t4/README.md`
records the handoff and the limits of author-side validation.
