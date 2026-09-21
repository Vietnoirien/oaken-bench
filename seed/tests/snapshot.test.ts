import { describe, expect, it } from 'vitest';
import { SnapshotStore, maxLegalRarity } from '../src/snapshot.js';
import { createRun } from '../src/run.js';

describe('maxLegalRarity', () => {
  it('accounts for one merge above the shop ceiling', () => {
    expect(maxLegalRarity(1)).toBe(2);
    expect(maxLegalRarity(2)).toBe(3);
    expect(maxLegalRarity(3)).toBe(3);
    expect(maxLegalRarity(4)).toBe(3);
    expect(maxLegalRarity(10)).toBe(3);
  });
});

describe('SnapshotStore', () => {
  it('starts empty', () => {
    const s = new SnapshotStore();
    expect(s.size()).toBe(0);
    expect(s.all()).toEqual([]);
  });

  it('ingests a fresh run', () => {
    const s = new SnapshotStore();
    const snap = s.ingest(createRun('p1', 1));
    expect(snap.playerId).toBe('p1');
    expect(s.size()).toBe(1);
  });

  it('never matches the requesting player against themselves', () => {
    const s = new SnapshotStore();
    s.ingest(createRun('p1', 1));
    expect(s.match('p1', 99)).toBeNull();
  });

  it('matches a different player', () => {
    const s = new SnapshotStore();
    s.ingest(createRun('p1', 1));
    s.ingest(createRun('p2', 2));
    expect(s.match('p1', 99)?.playerId).toBe('p2');
  });
});
