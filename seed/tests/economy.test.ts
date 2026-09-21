import { describe, expect, it } from 'vitest';
import { income, xpToNext, rarityOdds, maxShopRarity, livesLost,
         MAX_LEVEL, MAX_HP_CAP, START_HP, START_LIVES, TROPHIES_TO_WIN } from '../src/economy.js';

describe('constants', () => {
  it('match the spec', () => {
    expect(MAX_LEVEL).toBe(10);
    expect(MAX_HP_CAP).toBe(20000);
    expect(START_HP).toBe(1000);
    expect(START_LIVES).toBe(5);
    expect(TROPHIES_TO_WIN).toBe(10);
  });
});

describe('income', () => {
  it('is 40 + 5 * level', () => {
    expect(income(1)).toBe(45);
    expect(income(2)).toBe(50);
    expect(income(7)).toBe(75);
    expect(income(10)).toBe(90);
  });
});

describe('xpToNext', () => {
  it('is 52 + 4 * (level - 1)', () => {
    expect(xpToNext(1)).toBe(52);
    expect(xpToNext(2)).toBe(56);
    expect(xpToNext(5)).toBe(68);
  });
});

describe('rarityOdds', () => {
  it('matches the table', () => {
    expect(rarityOdds(1)).toEqual([75, 25, 0, 0]);
    expect(rarityOdds(4)).toEqual([45, 33, 20, 2]);
    expect(rarityOdds(7)).toEqual([20, 30, 30, 20]);
  });

  it('caps at level 7', () => {
    expect(rarityOdds(9)).toEqual(rarityOdds(7));
  });

  it('every row sums to 100', () => {
    for (let l = 1; l <= 10; l++) {
      expect(rarityOdds(l).reduce((a, b) => a + b, 0)).toBe(100);
    }
  });
});

describe('maxShopRarity', () => {
  it('is the highest non-zero weight', () => {
    expect(maxShopRarity(1)).toBe(1);
    expect(maxShopRarity(2)).toBe(2);
    expect(maxShopRarity(4)).toBe(3);
  });
});

describe('livesLost', () => {
  it('escalates by day', () => {
    expect(livesLost(1)).toBe(1);
    expect(livesLost(2)).toBe(1);
    expect(livesLost(3)).toBe(2);
    expect(livesLost(4)).toBe(2);
    expect(livesLost(5)).toBe(3);
    expect(livesLost(12)).toBe(3);
  });
});
