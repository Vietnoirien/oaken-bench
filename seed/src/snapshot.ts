import type { RunState, Snapshot } from './types.js';

/** Server-authoritative store of opponent towers.
 *  Snapshots are DERIVED from server-tracked RunState, never accepted from a
 *  caller. See SPEC.md section 9. */
export class SnapshotStore {
  /** Derives, validates and stores a snapshot. Throws EngineError on an
   *  invalid run state; nothing is stored in that case. Returns the snapshot. */
  ingest(run: RunState): Snapshot {
    throw new Error('not implemented');
  }

  /** Validates without storing. Throws EngineError on the first failure,
   *  in the order given in SPEC.md 9.1. */
  validate(snapshot: Snapshot): void {
    throw new Error('not implemented');
  }

  /** Returns a stored snapshot not belonging to `playerId`, or null. */
  match(playerId: string, seed: number): Snapshot | null {
    throw new Error('not implemented');
  }

  /** Stored snapshots in insertion order. */
  all(): Snapshot[] {
    throw new Error('not implemented');
  }

  size(): number {
    throw new Error('not implemented');
  }
}

/** Highest legal rarity index at a level, accounting for merges. SPEC.md 9.2. */
export function maxLegalRarity(level: number): number {
  throw new Error('not implemented');
}
