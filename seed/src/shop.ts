import type { RunState, ShopState } from './types.js';
import type { Rng } from './rng.js';

export const SHOP_SIZE = 7;

export function createShop(): ShopState {
  throw new Error('not implemented');
}

/** Rolls all 7 offers in order 0..6 from the shop stream. SPEC.md 5. */
export function rollShop(shop: ShopState, level: number, rng: Rng): ShopState {
  throw new Error('not implemented');
}

/** Cost of the nth manual reroll (1-indexed). SPEC.md 5. */
export function rerollCost(n: number): number {
  throw new Error('not implemented');
}

/** Charges gold, increments the counter, clears frozen, re-rolls. */
export function reroll(run: RunState, rng: Rng): RunState {
  throw new Error('not implemented');
}

export function toggleFreeze(run: RunState): RunState {
  throw new Error('not implemented');
}

/** Buys offer `index` and places it at `slot`. Removes the offer without refill. */
export function buy(run: RunState, index: number, slot: number, rng?: Rng): RunState {
  throw new Error('not implemented');
}

/** Refunds floor(cost / 2) and empties the slot. */
export function sell(run: RunState, slot: number): RunState {
  throw new Error('not implemented');
}
