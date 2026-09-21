import type { Rarity, SlotType, Tower } from './types.js';

export const TOWER_SLOTS = 10;

/** All slots default to 'Normal' unless overridden. */
export function createTower(slots?: SlotType[]): Tower {
  throw new Error('not implemented');
}

/** Places an item, then applies merges to fixpoint. Mutates and returns `tower`.
 *  Throws EngineError with SLOT_OUT_OF_RANGE | SLOT_OCCUPIED | SLOT_TYPE_MISMATCH. */
export function place(tower: Tower, slot: number, itemId: string, rarity: Rarity): Tower {
  throw new Error('not implemented');
}

/** Empties a slot. Throws EngineError('EMPTY_SLOT') if already empty. */
export function removeAt(tower: Tower, slot: number): Tower {
  throw new Error('not implemented');
}

/** Applies triple merges repeatedly until no further merge is possible.
 *  See SPEC.md section 3.2. */
export function applyMerges(tower: Tower): Tower {
  throw new Error('not implemented');
}

export function canPlace(tower: Tower, slot: number, itemId: string): boolean {
  throw new Error('not implemented');
}

export function entryAt(tower: Tower, slot: number) {
  throw new Error('not implemented');
}
