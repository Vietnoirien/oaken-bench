/** The T5 bot contract, as types and constants. t5/SPEC.md is normative;
 *  this file is the same contract in a form a bot can import (via
 *  `oaken-t5`), and it is frozen with the spec in t5/FROZEN.sha256.
 *
 *  Engine types are imported type-only, so this file loads without an
 *  engine present -- the scorer's own tests rely on that. */
import type { CombatOutcome, RunState, Snapshot } from 'oaken-engine';

export const CONTRACT_VERSION = 't5/1';

/** Decide calls per bot per day (SPEC section 4.3). */
export const MAX_CALLS_PER_DAY = 32;
/** Attempted actions per bot per day, illegal/malformed elements included, `end` excluded. */
export const MAX_ACTIONS_PER_DAY = 64;
/* Wall-clock budget per synchronous decide call, including the purity probe.
 * A returning overrun throws EvaluationVoidError immediately, before actions
 * apply. A never-returning call is stopped by the CLI process timeout. */
export const DECIDE_BUDGET_MS = 1000;
/** Wall-clock budget for importing a bot module once. */
export const LOAD_BUDGET_MS = 5000;
/** Days per match before the match is called. Unreachable by the rules
 *  (SPEC section 3.4) and kept only so a simulator bug cannot loop. */
export const MAX_DAYS = 30;

export type Action =
  | { type: 'buy'; offer: number; slot: number }
  | { type: 'sell'; slot: number }
  | { type: 'reroll' }
  | { type: 'freeze' }
  | { type: 'buy_xp' }
  | { type: 'end' };

export type ActionType = Action['type'];

export interface LastCombat {
  /** The rival tower this bot fought yesterday, as the engine's Snapshot. */
  opponent: Snapshot;
  /** From this bot's side: 'A' = this bot won, 'B' = it lost. */
  result: CombatOutcome;
  /** This bot's hp when that combat ended. */
  hpLeft: number;
}

/** What decide() receives. A fresh deep copy on every call: mutating it
 *  changes nothing, and nothing a bot writes into it is read back. */
export interface DecideState {
  contract: typeof CONTRACT_VERSION;
  /** The bot's own run. `seed` is 0 and `playerId` is 'self' (SPEC 4.1). */
  run: RunState;
  /** Yesterday's combat, or null on day 1. */
  lastCombat: LastCombat | null;
  /** A uint32 for the bot's own PRNG, distinct per (match, side, day). */
  botSeed: number;
  /** 0-based index of this call within the current day. */
  call: number;
  /** What is left of today's allowances when this call starts. */
  callsLeft: number;
  actionsLeft: number;
}

export type Decide = (state: DecideState) => Action[];

/** Per-bot, per-match counters (SPEC section 5). All deterministic. */
export interface BotCounters {
  calls: number;
  actions: number;
  illegal: number;
  malformed: number;
  decideErrors: number;
  dropped: number;
  impure: number;
}

/** A failed evaluation has no win rate, even if earlier matches completed. */
export class EvaluationVoidError extends Error {
  constructor(reason: string) {
    super(`${reason}: evaluation has no score`);
    this.name = 'EvaluationVoidError';
  }
}
