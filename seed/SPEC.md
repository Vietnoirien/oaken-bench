# Tower Auto-Battler — Engine Specification v1.0

A headless, deterministic engine for an asynchronous PvP auto-battler.
No UI. No network. No new dependencies.

Every rule below is normative. Where a formula is given, implement it exactly.
Integer arithmetic uses `Math.floor` unless stated otherwise.

---

## 1. Determinism

The entire engine is deterministic given a seed. There are **no** calls to
`Math.random`, `Date.now`, or any other ambient source of entropy.

### 1.1 PRNG

Implement `mulberry32` exactly:

```
state = (seed >>> 0)
next():
  state = (state + 0x6D2B79F5) >>> 0
  t = state
  t = Math.imul(t ^ (t >>> 15), t | 1)
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296
```

`next()` returns a float in `[0, 1)`.

- `nextInt(maxExclusive)` = `Math.floor(next() * maxExclusive)`.
- `pick(array)` = `array[nextInt(array.length)]`.

### 1.2 RNG streams

Streams are independent and consumed in a strictly defined order:

- **Shop stream** — seeded `seed ^ 0x51ED2701`. Consumed by shop rolls and rerolls.
- **Combat stream** — seeded `matchSeed ^ 0x1B873593`. Consumed by crit rolls only,
  in strict `frame -> side -> slotIndex -> triggerOrdinal` order.
- **Match stream** — seeded `seed ^ 0x2545F491`. Consumed by opponent selection.

A stream advances **only** when a value is drawn. Do not pre-draw or look ahead.

---

## 2. Item data

`data/items.json` is **frozen input data**. Do not edit it, do not generate items.
It is an array of item definitions:

```jsonc
{
  "id": "bone_club",
  "name": "Bone Club",
  "type": "Mace",              // Sword|Axe|Dagger|Spear|Mace|Bow|Spell|Artifact|Shield|Warfare
  "tag": "Neutral",            // Flame|Frosty|Storm|Holy|Neutral
  "tiers": [                   // exactly 4, indexed by rarity 0..3
    { "cost": 15, "cooldown": 3.0, "damage": 25,  "crit": 0,  "multicast": 0,
      "shield": 0, "heal": 0, "lifesteal": 0,
      "poison": 0, "burn": 0, "shock": 0,
      "startOfCombat": null, "stormBonus": 0, "flameBonus": 0 }
    // ... rare, epic, legendary
  ]
}
```

Rarity indices: `0 = Common, 1 = Rare, 2 = Epic, 3 = Legendary`.

`startOfCombat` is either `null` or `{ "damageMultiplier": <number> }`.

### 2.1 Type categories

- **Melee** = `Sword, Axe, Dagger, Spear, Mace`
- **Magic** = `Spell, Artifact`
- **Ranged** = `Bow`
- **Other** = `Shield, Warfare`

---

## 3. Tower and slots

A tower has exactly **10 slots**, indices `0..9`, ordered bottom to top.
Slot index is the canonical processing order everywhere in this spec.

### 3.1 Slot types

| Slot | Restriction | Effect on the occupying item |
|---|---|---|
| `Normal` | none | none |
| `Damage` | none | `damage += 10` |
| `Lifesteal` | none | `lifesteal += 10` (percentage points) |
| `Crit` | none | `crit += 15` (percentage points) |
| `Magic` | Magic items only | `damage = floor(damage * 1.2)` |
| `Melee` | Melee items only | `damage = floor(damage * 1.2)` |

Placing an item into a restricted slot it does not match is rejected with
`SLOT_TYPE_MISMATCH`. Placing into an occupied slot is rejected with
`SLOT_OCCUPIED`. An out-of-range index is rejected with `SLOT_OUT_OF_RANGE`.

Slot effects are applied when computing an item's **effective stats**, in the
order: base tier stats -> slot effect -> tag synergy. Additive slot effects
(`Damage`, `Lifesteal`, `Crit`) apply before multiplicative ones (`Magic`, `Melee`).

### 3.2 Triple merge

Whenever three items sharing the same `id` **and** the same rarity occupy the
tower, they merge immediately:

- They are replaced by **one** item of the same `id` at `rarity + 1`.
- The merged item occupies the **lowest** slot index of the three.
- The other two slots become empty.
- Legendary (rarity 3) items never merge.
- Merging is applied repeatedly until no further merge is possible.

Merging is checked after every placement.

---

## 4. Economy

- `income(level)` = `40 + 5 * level`
- `xpToNext(level)` = `52 + 4 * (level - 1)`
- `maxLevel` = `10`
- Buying XP: `4` gold grants `4` XP. Rejected with `INSUFFICIENT_GOLD` if unaffordable.
- XP from combat: **win +12**, **loss +8**, **draw +8**.
- Level-ups apply repeatedly while `xp >= xpToNext(level)`, subtracting the cost each time.
  Excess XP carries over. XP gained at `maxLevel` is discarded.

### 4.1 Health

- Starting `maxHp` = `1000`, starting `hp` = `maxHp`.
- On each level-up: `maxHp += floor(maxHp * 0.10)`, then clamp `maxHp` to `20000`.
- `hp` is restored to `maxHp` at the start of every day.

### 4.2 Lives and trophies

- Starting `lives` = `5`, starting `trophies` = `0`.
- A **win** grants `+1` trophy and costs no lives.
- A **loss or draw** costs lives by day: day `1-2` -> `1`, day `3-4` -> `2`, day `>= 5` -> `3`.
- The run ends in `won` at `trophies >= 10`, or `lost` at `lives <= 0`.
  If both conditions hold after the same combat, `won` takes precedence.

### 4.3 Shop rarity odds

Percentages by level, `[common, rare, epic, legendary]`:

| Level | C | R | E | L |
|---|---|---|---|---|
| 1 | 75 | 25 | 0 | 0 |
| 2 | 65 | 30 | 5 | 0 |
| 3 | 55 | 32 | 13 | 0 |
| 4 | 45 | 33 | 20 | 2 |
| 5 | 35 | 33 | 25 | 7 |
| 6 | 27 | 32 | 28 | 13 |
| >= 7 | 20 | 30 | 30 | 20 |

Rolling a rarity: draw `r = nextInt(100)` from the shop stream, then walk the
table in order `common, rare, epic, legendary`, subtracting each weight from `r`
until `r < weight`. That rarity is selected.

---

## 5. Shop

- The shop holds exactly **7** offers. An offer is `{ itemId, rarity, cost }`,
  where `cost` is the tier's `cost`.
- Rolling one offer draws the rarity first (5.4 above), then the item:
  `pick(allItems)` from the shop stream. Offers are rolled in order `0..6`.
- At the start of each day the shop is re-rolled **unless it is frozen**.
  Re-rolling at day start clears the frozen flag and does **not** increment the
  reroll counter.
- `freeze()` toggles the frozen flag. It is free.
- Manual reroll cost, where `n` is the 1-indexed ordinal of the reroll within
  the run: `n <= 15` -> `3` gold; `n > 15` -> `3 + 2 * (n - 15)` gold.
  A manual reroll clears the frozen flag.
- Buying an offer removes it from the shop (the slot becomes empty, it is not
  refilled) and deducts its cost. Rejected with `INSUFFICIENT_GOLD` if unaffordable.
- Selling an item held in the tower refunds `floor(cost / 2)` gold and empties the slot.

---

## 6. Tag synergies

Computed over the **effective tower** at the start of combat.

- **Storm**: an item with `stormBonus > 0` gains
  `damage += stormBonus * (number of OTHER items in the same tower with tag "Storm")`.
- **Flame**: an item with `flameBonus > 0` gains
  `burn += flameBonus * (number of OTHER items in the same tower with tag "Flame")`.

"Other" excludes the item itself, whether or not it is itself tagged.

---

## 7. Combat simulation

Combat is a deterministic frame simulation between two towers, **side A** and
**side B**.

- `FRAME_MS = 100`. Ten frames per second.
- `MAX_FRAMES = 600` (60 seconds).
- Frames are numbered `1..600`. Frame `0` does not tick; it is reserved for
  start-of-combat resolution.

### 7.1 Start of combat

Before frame 1, resolve start-of-combat effects: **all of side A in ascending
slot index, then all of side B in ascending slot index**.

- Start-of-combat effects ignore the cooldown floor and the per-frame trigger cap.
- `damageMultiplier` values **stack multiplicatively** on the side that owns them:
  two items each with `damageMultiplier: 1.5` produce a side-wide damage
  multiplier of `2.25`. The side multiplier starts at `1.0`.
- The side damage multiplier applies to all outgoing damage from that side for
  the whole combat, applied after crit.

### 7.2 Cooldowns and triggering

- An item's effective cooldown is its tier `cooldown`, clamped to a floor of
  `1.0` seconds.
- Cooldown is both the initial delay and the repeat interval. An item with
  cooldown `C` triggers first at frame `round(C * 10)`, then every `round(C * 10)`
  frames thereafter.
- **Per-frame cap**: an item may trigger at most **once per frame**. This is the
  10-triggers-per-second cap. Triggers beyond the cap are **dropped, not deferred**.
- **Multicast**: when an item triggers from its cooldown with `multicast = M`,
  it schedules `M` additional triggers, one on each of the next `M` frames.
  These are spread across time, never simultaneous, and are themselves subject
  to the per-frame cap.

### 7.3 Frame order

Each frame `f` from 1 to 600, in this exact order:

1. Apply the **deferred queue** built during frame `f - 1`.
2. Process side A items in ascending slot index, then side B items in ascending
   slot index. For each item scheduled to trigger this frame, resolve its trigger.
3. Apply end-of-second damage-over-time, if `f % 10 == 0` (see 7.5).
4. Check the end condition (7.6).

### 7.4 Resolving a trigger

If the item's owner has `shock > 0`, decrement `shock` by 1 and **skip this
trigger entirely** (it is consumed, not deferred).

Otherwise, in order:

1. **Crit roll** — if `crit > 0`, draw `c = nextInt(100)` from the combat stream.
   The trigger is a crit if `c < crit`. If `crit == 0`, **no value is drawn**.
2. **Damage** — if `damage > 0`:
   `dealt = floor(damage * (crit ? 2 : 1) * sideDamageMultiplier)`.
   Apply to the opposing player via 7.7.
3. **Lifesteal** — if `lifesteal > 0` and damage was dealt:
   heal the owner by `floor(dealtToHp * lifesteal / 100)`, where `dealtToHp` is
   the damage that reached HP after shields. Healing cannot exceed `maxHp`.
4. **Shield** — if `shield > 0`, add `shield` to the owner's shield pool.
5. **Heal** — if `heal > 0`, heal the owner by `heal`, capped at `maxHp`.
6. **Status application** — apply `poison`, `burn`, `shock` stacks to the
   **opponent**. These are cross-side effects on the opposing *player*, and apply
   **immediately**.

**In-frame vs deferred.** Effects that modify the triggering item itself
(permanent stat gains) apply **immediately** and are visible to items processed
later in the same frame. Effects that modify **another item** are appended to the
deferred queue and applied at the start of the next frame. Damage, healing,
shields and statuses target a **player**, not an item, and always apply immediately.

### 7.5 Statuses

Resolved at the end of each full second (`f % 10 == 0`), side A before side B:

- **Poison**: deal `poison` damage to HP, **ignoring shields**. Stacks do not decay.
- **Burn**: deal `burn` damage to HP, **ignoring shields**. Then `burn = floor(burn / 2)`.
- **Shock**: not a damage-over-time. Consumed by trigger skipping (7.4).

### 7.6 End condition

Checked at the end of every frame, after all effects:

- If exactly one side has `hp <= 0`, the other side **wins**.
- If both sides have `hp <= 0` in the same frame, the result is a **draw**.
- If frame 600 completes with both sides alive, the result is a **draw**.

### 7.7 Damage application

Damage is absorbed by the shield pool first:
`absorbed = min(shield, amount)`, `shield -= absorbed`, `hp -= (amount - absorbed)`.
`hp` may go negative. Poison and burn bypass shields entirely.

### 7.8 Event log

The simulation returns an ordered event log. Every event has
`{ frame, side, slot, type }` plus the fields listed:

| `type` | Extra fields |
|---|---|
| `start_of_combat` | `multiplier` (the resulting side multiplier) |
| `trigger` | `itemId`, `crit` (boolean), `multicast` (boolean — true if this trigger came from multicast) |
| `damage` | `itemId`, `amount`, `absorbed`, `toHp` |
| `heal` | `amount` |
| `shield` | `amount` |
| `status` | `status` (`"poison"`\|`"burn"`\|`"shock"`), `stacks` |
| `dot` | `status` (`"poison"`\|`"burn"`), `amount` |
| `shock_skip` | `itemId` |
| `end` | `result` (`"A"`\|`"B"`\|`"draw"`), `hpA`, `hpB` |

For `start_of_combat` and `end`, `slot` is `-1`. For `dot` events, `side` is the
side **taking** the damage and `slot` is `-1`.

Events are appended in resolution order. The log always ends with exactly one
`end` event.

---

## 8. Run loop

A run proceeds in days, starting at day 1.

Each day, in order:

1. `day += 1` is **not** applied on the first day; the run starts at day 1.
2. Grant `income(level)` gold.
3. Restore `hp` to `maxHp`, clear shields and statuses.
4. Re-roll the shop unless frozen (see section 5).
5. The player acts: buy, sell, place, reroll, freeze, buy XP. (Driven externally.)
6. Combat resolves against a matched opponent snapshot.
7. Apply XP, trophies, lives (section 4.2).
8. If the run is over, stop. Otherwise advance to the next day.

---

## 9. Snapshot store

The store is the server-authoritative record of opponent towers.

**The client never supplies a snapshot.** `ingest` accepts server-tracked
`RunState` only, derives the snapshot from it, and validates it. A snapshot that
fails validation is **rejected and not stored**.

### 9.1 Validation rules

Checked in this order; the first failure is reported:

| Code | Condition |
|---|---|
| `SNAPSHOT_DUPLICATE_SLOT` | two entries share a slot index |
| `SNAPSHOT_SLOT_OUT_OF_RANGE` | a slot index is outside `0..9` |
| `SNAPSHOT_UNKNOWN_ITEM` | an `itemId` is absent from the item pool |
| `SNAPSHOT_INVALID_LEVEL` | `level` outside `1..10` |
| `SNAPSHOT_NEGATIVE_GOLD` | `gold < 0` |
| `SNAPSHOT_HP_EXCEEDS_CAP` | `hp > maxHp` or `maxHp > 20000` |
| `SNAPSHOT_ILLEGAL_RARITY` | see 9.2 |

### 9.2 Rarity legality

An item of rarity `R` is legal at `level` only if
`R <= min(3, maxShopRarity(level) + 1)`, where `maxShopRarity(level)` is the
highest rarity index with a non-zero weight in the level's odds row (section 4.3).
The `+ 1` accounts for triple merges.

This yields: level 1 -> max rarity `2`; level 2 or 3 -> `3`; level `>= 4` -> `3`.

### 9.3 Matching

`match(playerId, seed)` returns a stored snapshot, or `null` if none is eligible.

- Snapshots belonging to `playerId` are excluded.
- Selection draws `nextInt(candidates.length)` from the match stream, where
  `candidates` is the eligible list **in insertion order**.

---

## 10. Out of scope

Do not implement: encounters, perks, seasons, Duos mode, cosmetics, ranked elo,
rendering, HTTP servers, persistence to disk, or a CLI.
