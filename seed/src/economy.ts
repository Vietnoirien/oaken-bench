import type { Rarity, RunState } from './types.js';
import type { Rng } from './rng.js';

export const MAX_LEVEL = 10;
export const MAX_HP_CAP = 20000;
export const START_HP = 1000;
export const START_LIVES = 5;
export const TROPHIES_TO_WIN = 10;

export function income(level: number): number {
  throw new Error('not implemented');
}

export function xpToNext(level: number): number {
  throw new Error('not implemented');
}

/** Rarity weights [common, rare, epic, legendary] for a level. SPEC.md 4.3. */
export function rarityOdds(level: number): [number, number, number, number] {
  throw new Error('not implemented');
}

/** Draws one rarity from the given stream. SPEC.md 4.3. */
export function rollRarity(level: number, rng: Rng): Rarity {
  throw new Error('not implemented');
}

/** Highest rarity index with non-zero shop weight at this level. */
export function maxShopRarity(level: number): Rarity {
  throw new Error('not implemented');
}

/** Adds XP and applies level-ups to fixpoint, raising maxHp. Mutates `run`. */
export function addXp(run: RunState, amount: number): RunState {
  throw new Error('not implemented');
}

/** Lives lost on a loss or draw, by day. SPEC.md 4.2. */
export function livesLost(day: number): number {
  throw new Error('not implemented');
}

export function buyXp(run: RunState): RunState {
  throw new Error('not implemented');
}
