import type { CombatResult, RunState, Snapshot } from './types.js';

export function createRun(playerId: string, seed: number): RunState {
  throw new Error('not implemented');
}

/** Grants income, restores hp, re-rolls the shop unless frozen. SPEC.md 8. */
export function startDay(run: RunState): RunState {
  throw new Error('not implemented');
}

/** Simulates against the opponent, then applies XP, trophies and lives,
 *  and updates `status`. SPEC.md 8 steps 6-8. */
export function resolveCombat(run: RunState, opponent: Snapshot): CombatResult {
  throw new Error('not implemented');
}

/** Advances to the next day if the run is still active. */
export function endDay(run: RunState): RunState {
  throw new Error('not implemented');
}
