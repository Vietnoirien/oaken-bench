import { describe, expect, it } from 'vitest';
import { rerollCost, createShop, SHOP_SIZE } from '../src/shop.js';

describe('rerollCost', () => {
  it('is 3 for the first 15 rerolls', () => {
    expect(rerollCost(1)).toBe(3);
    expect(rerollCost(15)).toBe(3);
  });

  it('escalates by 2 after 15', () => {
    expect(rerollCost(16)).toBe(5);
    expect(rerollCost(17)).toBe(7);
    expect(rerollCost(20)).toBe(13);
  });
});

describe('createShop', () => {
  it('starts with 7 empty offers, unfrozen', () => {
    const s = createShop();
    expect(SHOP_SIZE).toBe(7);
    expect(s.offers).toHaveLength(7);
    expect(s.frozen).toBe(false);
    expect(s.rerollCount).toBe(0);
  });
});
