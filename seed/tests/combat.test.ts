import { describe, expect, it } from 'vitest';
import { cooldownFrames, startOfCombatMultiplier, simulate,
         FRAME_MS, MAX_FRAMES, MIN_COOLDOWN_SECONDS } from '../src/combat.js';
import { createTower, place } from '../src/tower.js';

describe('constants', () => {
  it('match the spec', () => {
    expect(FRAME_MS).toBe(100);
    expect(MAX_FRAMES).toBe(600);
    expect(MIN_COOLDOWN_SECONDS).toBe(1.0);
  });
});

describe('cooldownFrames', () => {
  it('fires every cooldown * 10 frames', () => {
    const f = cooldownFrames(3.0);
    expect(f[0]).toBe(30);
    expect(f[1]).toBe(60);
    expect(f[f.length - 1]).toBeLessThanOrEqual(600);
  });

  it('clamps to the 1 second floor', () => {
    expect(cooldownFrames(0.4)[0]).toBe(10);
    expect(cooldownFrames(1.0)[0]).toBe(10);
  });
});

describe('startOfCombatMultiplier', () => {
  it('is 1.0 with no start-of-combat items', () => {
    expect(startOfCombatMultiplier(place(createTower(), 0, 'bone_club', 0))).toBe(1.0);
  });

  it('stacks multiplicatively', () => {
    let t = createTower();
    t = place(t, 0, 'warhorn', 0);
    t = place(t, 1, 'warhorn', 0);
    expect(startOfCombatMultiplier(t)).toBeCloseTo(1.44, 6);
  });
});

describe('simulate', () => {
  it('ends with exactly one end event', () => {
    const a = { tower: place(createTower(), 0, 'war_axe', 0), hp: 1000, maxHp: 1000 };
    const b = { tower: place(createTower(), 0, 'bone_club', 0), hp: 1000, maxHp: 1000 };
    const r = simulate(a, b, 777);
    const ends = r.events.filter((e) => e.type === 'end');
    expect(ends).toHaveLength(1);
    expect(r.events[r.events.length - 1].type).toBe('end');
    expect(['A', 'B', 'draw']).toContain(r.result);
  });

  it('is deterministic for the same seed', () => {
    const mk = () => ({ tower: place(createTower(), 0, 'hunters_bow', 0), hp: 1000, maxHp: 1000 });
    const r1 = simulate(mk(), mk(), 4242);
    const r2 = simulate(mk(), mk(), 4242);
    expect(r1.events).toEqual(r2.events);
  });

  it('two empty towers draw at the frame limit', () => {
    const a = { tower: createTower(), hp: 1000, maxHp: 1000 };
    const b = { tower: createTower(), hp: 1000, maxHp: 1000 };
    const r = simulate(a, b, 1);
    expect(r.result).toBe('draw');
    expect(r.hpA).toBe(1000);
    expect(r.hpB).toBe(1000);
  });
});
