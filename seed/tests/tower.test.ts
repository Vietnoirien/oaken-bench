import { describe, expect, it } from 'vitest';
import { createTower, place, removeAt, TOWER_SLOTS } from '../src/tower.js';
import { EngineError } from '../src/types.js';

describe('createTower', () => {
  it('has 10 Normal slots by default', () => {
    const t = createTower();
    expect(TOWER_SLOTS).toBe(10);
    expect(t.slots).toHaveLength(10);
    expect(t.slots.every((s) => s === 'Normal')).toBe(true);
    expect(t.entries).toHaveLength(0);
  });
});

describe('place', () => {
  it('places an item', () => {
    const t = place(createTower(), 3, 'bone_club', 0);
    expect(t.entries).toEqual([{ slot: 3, itemId: 'bone_club', rarity: 0 }]);
  });

  it('rejects an out-of-range slot', () => {
    expect(() => place(createTower(), 10, 'bone_club', 0)).toThrow(EngineError);
    try { place(createTower(), -1, 'bone_club', 0); }
    catch (e) { expect((e as EngineError).code).toBe('SLOT_OUT_OF_RANGE'); }
  });

  it('rejects an occupied slot', () => {
    const t = place(createTower(), 0, 'bone_club', 0);
    try { place(t, 0, 'iron_sword', 0); expect.unreachable(); }
    catch (e) { expect((e as EngineError).code).toBe('SLOT_OCCUPIED'); }
  });

  it('rejects a type-restricted slot mismatch', () => {
    const slots = createTower().slots.slice();
    slots[0] = 'Magic';
    try { place(createTower(slots), 0, 'bone_club', 0); expect.unreachable(); }
    catch (e) { expect((e as EngineError).code).toBe('SLOT_TYPE_MISMATCH'); }
  });

  it('accepts a Magic item in a Magic slot', () => {
    const slots = createTower().slots.slice();
    slots[0] = 'Magic';
    const t = place(createTower(slots), 0, 'pyre_staff', 0);
    expect(t.entries).toHaveLength(1);
  });
});

describe('merging', () => {
  it('merges three identical commons into one rare at the lowest slot', () => {
    let t = createTower();
    t = place(t, 2, 'bone_club', 0);
    t = place(t, 5, 'bone_club', 0);
    t = place(t, 7, 'bone_club', 0);
    expect(t.entries).toEqual([{ slot: 2, itemId: 'bone_club', rarity: 1 }]);
  });

  it('does not merge different rarities', () => {
    let t = createTower();
    t = place(t, 0, 'bone_club', 0);
    t = place(t, 1, 'bone_club', 0);
    t = place(t, 2, 'bone_club', 1);
    expect(t.entries).toHaveLength(3);
  });
});

describe('removeAt', () => {
  it('empties a slot', () => {
    const t = removeAt(place(createTower(), 1, 'bone_club', 0), 1);
    expect(t.entries).toHaveLength(0);
  });

  it('rejects an empty slot', () => {
    try { removeAt(createTower(), 1); expect.unreachable(); }
    catch (e) { expect((e as EngineError).code).toBe('EMPTY_SLOT'); }
  });
});
