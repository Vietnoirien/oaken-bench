/** Deterministic PRNG. See SPEC.md section 1. */

/** Returns a mulberry32 generator producing floats in [0, 1). */
export function mulberry32(seed: number): () => number {
  throw new Error('not implemented');
}

export class Rng {
  constructor(seed: number) {
    throw new Error('not implemented');
  }
  next(): number {
    throw new Error('not implemented');
  }
  nextInt(maxExclusive: number): number {
    throw new Error('not implemented');
  }
  pick<T>(arr: readonly T[]): T {
    throw new Error('not implemented');
  }
}

export const SHOP_STREAM_SALT = 0x51ed2701;
export const COMBAT_STREAM_SALT = 0x1b873593;
export const MATCH_STREAM_SALT = 0x2545f491;

export function shopRng(seed: number): Rng {
  throw new Error('not implemented');
}
export function combatRng(matchSeed: number): Rng {
  throw new Error('not implemented');
}
export function matchRng(seed: number): Rng {
  throw new Error('not implemented');
}
