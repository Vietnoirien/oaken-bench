import { describe, expect, it } from 'vitest';
import { mulberry32, Rng, shopRng, combatRng, matchRng,
         SHOP_STREAM_SALT, COMBAT_STREAM_SALT, MATCH_STREAM_SALT } from '../src/rng.js';

describe('mulberry32', () => {
  it('produces the reference sequence for seed 42', () => {
    const r = mulberry32(42);
    expect(r()).toBeCloseTo(0.6011037519201636, 12);
    expect(r()).toBeCloseTo(0.4482905589975417, 12);
    expect(r()).toBeCloseTo(0.8524657934904099, 12);
  });

  it('produces the reference sequence for seed 1', () => {
    const r = mulberry32(1);
    expect(r()).toBeCloseTo(0.6270739405881613, 12);
    expect(r()).toBeCloseTo(0.0027357211802155, 12);
  });

  it('returns values in [0, 1)', () => {
    const r = mulberry32(7);
    for (let i = 0; i < 500; i++) {
      const v = r();
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });
});

describe('Rng', () => {
  it('nextInt is floor(next() * max)', () => {
    const a = new Rng(42);
    const b = mulberry32(42);
    for (let i = 0; i < 20; i++) expect(a.nextInt(100)).toBe(Math.floor(b() * 100));
  });

  it('pick indexes by nextInt', () => {
    const arr = ['a', 'b', 'c', 'd'];
    const a = new Rng(99);
    const b = mulberry32(99);
    for (let i = 0; i < 20; i++) expect(a.pick(arr)).toBe(arr[Math.floor(b() * 4)]);
  });

  it('two generators with the same seed agree', () => {
    const a = new Rng(1234), b = new Rng(1234);
    for (let i = 0; i < 50; i++) expect(a.next()).toBe(b.next());
  });
});

describe('streams', () => {
  it('uses the declared salts', () => {
    expect(SHOP_STREAM_SALT).toBe(0x51ed2701);
    expect(COMBAT_STREAM_SALT).toBe(0x1b873593);
    expect(MATCH_STREAM_SALT).toBe(0x2545f491);
  });

  it('shop stream for seed 12345 matches the reference', () => {
    const r = shopRng(12345);
    expect(r.nextInt(100)).toBe(51);
    expect(r.nextInt(100)).toBe(37);
    expect(r.nextInt(100)).toBe(82);
  });

  it('the three streams differ for the same seed', () => {
    const s = shopRng(5).next(), c = combatRng(5).next(), m = matchRng(5).next();
    expect(new Set([s, c, m]).size).toBe(3);
  });
});
