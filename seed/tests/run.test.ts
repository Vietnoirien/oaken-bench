import { describe, expect, it } from 'vitest';
import { createRun, startDay } from '../src/run.js';

describe('createRun', () => {
  it('starts at day 1 with spec defaults', () => {
    const r = createRun('p1', 42);
    expect(r.playerId).toBe('p1');
    expect(r.day).toBe(1);
    expect(r.level).toBe(1);
    expect(r.xp).toBe(0);
    expect(r.lives).toBe(5);
    expect(r.trophies).toBe(0);
    expect(r.maxHp).toBe(1000);
    expect(r.hp).toBe(1000);
    expect(r.status).toBe('active');
    expect(r.tower.slots).toHaveLength(10);
  });
});

describe('startDay', () => {
  it('grants income and fills the shop', () => {
    const r = startDay(createRun('p1', 42));
    expect(r.gold).toBe(45);
    expect(r.shop.offers.filter(Boolean)).toHaveLength(7);
  });

  it('restores hp to maxHp', () => {
    const r = createRun('p1', 42);
    r.hp = 10;
    expect(startDay(r).hp).toBe(r.maxHp);
  });
});
