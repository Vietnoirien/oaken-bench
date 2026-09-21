import type { EffectiveStats, ItemDef, ItemType, Rarity, Tower } from './types.js';

/** Loads and caches data/items.json. */
export function loadItems(): ItemDef[] {
  throw new Error('not implemented');
}

/** Throws EngineError('UNKNOWN_ITEM') if absent. */
export function getItem(id: string): ItemDef {
  throw new Error('not implemented');
}

export const MELEE_TYPES: readonly ItemType[] = ['Sword', 'Axe', 'Dagger', 'Spear', 'Mace'];
export const MAGIC_TYPES: readonly ItemType[] = ['Spell', 'Artifact'];
export const RANGED_TYPES: readonly ItemType[] = ['Bow'];

export function isMelee(type: ItemType): boolean {
  throw new Error('not implemented');
}
export function isMagic(type: ItemType): boolean {
  throw new Error('not implemented');
}

/** Effective stats for every occupied slot, in ascending slot order.
 *  Applies slot effects then tag synergies. See SPEC.md sections 3.1 and 6. */
export function effectiveTower(tower: Tower): EffectiveStats[] {
  throw new Error('not implemented');
}

/** Effective stats for a single entry within its tower context. */
export function effectiveStats(tower: Tower, slot: number): EffectiveStats {
  throw new Error('not implemented');
}
