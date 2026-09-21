import type { Combatant, CombatResult, Tower } from './types.js';

export const FRAME_MS = 100;
export const FRAMES_PER_SECOND = 10;
export const MAX_FRAMES = 600;
export const MIN_COOLDOWN_SECONDS = 1.0;

/** Runs a full deterministic combat. See SPEC.md section 7.
 *  The returned event log always ends with exactly one 'end' event. */
export function simulate(a: Combatant, b: Combatant, matchSeed: number): CombatResult {
  throw new Error('not implemented');
}

/** Side-wide damage multiplier from start-of-combat effects (multiplicative). */
export function startOfCombatMultiplier(tower: Tower): number {
  throw new Error('not implemented');
}

/** Frame indices (1..MAX_FRAMES) on which this cooldown fires. */
export function cooldownFrames(cooldownSeconds: number): number[] {
  throw new Error('not implemented');
}
