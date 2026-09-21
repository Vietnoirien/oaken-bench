/** Shared types. This file is part of the frozen public contract: do not
 *  rename or remove any exported symbol. You may add new ones. */

export type Rarity = 0 | 1 | 2 | 3;

export type ItemType =
  | 'Sword' | 'Axe' | 'Dagger' | 'Spear' | 'Mace'
  | 'Bow' | 'Spell' | 'Artifact' | 'Shield' | 'Warfare';

export type Tag = 'Flame' | 'Frosty' | 'Storm' | 'Holy' | 'Neutral';

export type SlotType = 'Normal' | 'Damage' | 'Lifesteal' | 'Crit' | 'Magic' | 'Melee';

export type Side = 'A' | 'B';

export type StatusName = 'poison' | 'burn' | 'shock';

export type EventType =
  | 'start_of_combat' | 'trigger' | 'damage' | 'heal' | 'shield'
  | 'status' | 'dot' | 'shock_skip' | 'end';

export type CombatOutcome = 'A' | 'B' | 'draw';

export type ErrorCode =
  | 'SLOT_OUT_OF_RANGE' | 'SLOT_OCCUPIED' | 'SLOT_TYPE_MISMATCH'
  | 'INSUFFICIENT_GOLD' | 'UNKNOWN_ITEM' | 'EMPTY_SLOT'
  | 'SNAPSHOT_DUPLICATE_SLOT' | 'SNAPSHOT_SLOT_OUT_OF_RANGE'
  | 'SNAPSHOT_UNKNOWN_ITEM' | 'SNAPSHOT_INVALID_LEVEL'
  | 'SNAPSHOT_NEGATIVE_GOLD' | 'SNAPSHOT_HP_EXCEEDS_CAP'
  | 'SNAPSHOT_ILLEGAL_RARITY';

/** Thrown for every rejected operation. `code` is the normative identifier. */
export class EngineError extends Error {
  readonly code: ErrorCode;
  constructor(code: ErrorCode, message?: string) {
    super(message ?? code);
    this.name = 'EngineError';
    this.code = code;
  }
}

export interface StartOfCombatEffect {
  damageMultiplier: number;
}

export interface TierStats {
  cost: number;
  cooldown: number;
  damage: number;
  crit: number;
  multicast: number;
  shield: number;
  heal: number;
  lifesteal: number;
  poison: number;
  burn: number;
  shock: number;
  startOfCombat: StartOfCombatEffect | null;
  stormBonus: number;
  flameBonus: number;
}

export interface ItemDef {
  id: string;
  name: string;
  type: ItemType;
  tag: Tag;
  tiers: TierStats[];
}

/** One occupied slot of a tower. */
export interface TowerEntry {
  slot: number;
  itemId: string;
  rarity: Rarity;
}

export interface Tower {
  /** Exactly 10 entries, index = slot index. */
  slots: SlotType[];
  entries: TowerEntry[];
}

/** A tier's stats after slot effects and tag synergies. */
export interface EffectiveStats extends TierStats {
  itemId: string;
  rarity: Rarity;
  slot: number;
}

export interface ShopOffer {
  itemId: string;
  rarity: Rarity;
  cost: number;
}

export interface ShopState {
  /** Exactly 7 entries; a bought offer becomes null and is not refilled. */
  offers: (ShopOffer | null)[];
  frozen: boolean;
  /** Count of manual rerolls performed this run. */
  rerollCount: number;
}

export interface CombatEvent {
  frame: number;
  side: Side;
  slot: number;
  type: EventType;
  itemId?: string;
  amount?: number;
  absorbed?: number;
  toHp?: number;
  crit?: boolean;
  multicast?: boolean;
  status?: StatusName;
  stacks?: number;
  multiplier?: number;
  result?: CombatOutcome;
  hpA?: number;
  hpB?: number;
}

export interface Combatant {
  tower: Tower;
  hp: number;
  maxHp: number;
}

export interface CombatResult {
  result: CombatOutcome;
  hpA: number;
  hpB: number;
  events: CombatEvent[];
}

export type RunStatus = 'active' | 'won' | 'lost';

export interface RunState {
  playerId: string;
  seed: number;
  day: number;
  gold: number;
  level: number;
  xp: number;
  hp: number;
  maxHp: number;
  lives: number;
  trophies: number;
  status: RunStatus;
  tower: Tower;
  shop: ShopState;
}

export interface Snapshot {
  playerId: string;
  day: number;
  level: number;
  gold: number;
  hp: number;
  maxHp: number;
  entries: TowerEntry[];
  slots: SlotType[];
}
