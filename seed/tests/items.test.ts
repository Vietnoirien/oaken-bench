import { describe, expect, it } from 'vitest';
import { loadItems, getItem, isMelee, isMagic, effectiveStats } from '../src/items.js';
import { createTower, place } from '../src/tower.js';
import { EngineError } from '../src/types.js';

describe('loadItems', () => {
  it('loads the frozen pool of 20 items', () => {
    const items = loadItems();
    expect(items).toHaveLength(20);
    expect(items.every((i) => i.tiers.length === 4)).toBe(true);
  });
});

describe('getItem', () => {
  it('returns a known item', () => {
    const it0 = getItem('bone_club');
    expect(it0.type).toBe('Mace');
    expect(it0.tiers[0].damage).toBe(25);
    expect(it0.tiers[0].cost).toBe(15);
  });

  it('throws UNKNOWN_ITEM for an absent id', () => {
    try { getItem('nope'); expect.unreachable(); }
    catch (e) { expect((e as EngineError).code).toBe('UNKNOWN_ITEM'); }
  });
});

describe('categories', () => {
  it('classifies melee and magic', () => {
    expect(isMelee('Sword')).toBe(true);
    expect(isMelee('Bow')).toBe(false);
    expect(isMagic('Spell')).toBe(true);
    expect(isMagic('Artifact')).toBe(true);
    expect(isMagic('Shield')).toBe(false);
  });
});

describe('slot effects', () => {
  it('a Damage slot adds 10 damage', () => {
    const slots = createTower().slots.slice();
    slots[0] = 'Damage';
    const t = place(createTower(slots), 0, 'bone_club', 0);
    expect(effectiveStats(t, 0).damage).toBe(35);
  });

  it('a Normal slot changes nothing', () => {
    const t = place(createTower(), 0, 'bone_club', 0);
    expect(effectiveStats(t, 0).damage).toBe(25);
  });
});
