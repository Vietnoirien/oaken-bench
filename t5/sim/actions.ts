/** Action validation, legal-move enumeration and atomic application
 *  (t5/SPEC.md section 4.2). */
import * as E from 'oaken-engine';
import type { RunState } from 'oaken-engine';
import type { Action, ActionType } from './contract.js';

const TYPES: readonly ActionType[] = ['buy', 'sell', 'reroll', 'freeze', 'buy_xp', 'end'];

/** Returns the action if its SHAPE is valid, else null. Range checks are
 *  left to the engine, which reports them with its own error codes.
 *
 *  Integer-ness is checked here and not left to the engine because the
 *  engine does not check it: `place(tower, 1.5, ...)` passes the range test,
 *  finds no entry at slot 1.5, and pushes one -- a tower with a fractional
 *  slot that every later lookup silently misses. */
export function parseAction(raw: unknown): Action | null {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null;
  const a = raw as Record<string, unknown>;
  if (typeof a.type !== 'string' || !TYPES.includes(a.type as ActionType)) return null;
  switch (a.type) {
    case 'buy':
      if (!Number.isInteger(a.offer) || !Number.isInteger(a.slot)) return null;
      return { type: 'buy', offer: a.offer as number, slot: a.slot as number };
    case 'sell':
      if (!Number.isInteger(a.slot)) return null;
      return { type: 'sell', slot: a.slot as number };
    default:
      return { type: a.type as 'reroll' | 'freeze' | 'buy_xp' | 'end' };
  }
}

/** Every action the engine would accept right now, in a fixed order:
 *  buys (offer, then slot, ascending), sells (slot ascending), reroll,
 *  freeze, buy_xp, end. Mirrors the engine's own acceptance rules, not a
 *  judgement of what is sensible: buy_xp is listed at max level because the
 *  engine takes the gold there too (SPEC.md 4: XP at max level is
 *  discarded). */
export function legalActions(run: RunState): Action[] {
  const out: Action[] = [];
  run.shop.offers.forEach((offer, i) => {
    if (!offer || offer.cost > run.gold) return;
    for (let s = 0; s < run.tower.slots.length; s++) {
      if (E.canPlace(run.tower, s, offer.itemId)) out.push({ type: 'buy', offer: i, slot: s });
    }
  });
  for (const e of [...run.tower.entries].sort((x, y) => x.slot - y.slot)) {
    out.push({ type: 'sell', slot: e.slot });
  }
  if (run.gold >= E.rerollCost(run.shop.rerollCount + 1)) out.push({ type: 'reroll' });
  out.push({ type: 'freeze' });
  if (run.gold >= 4) out.push({ type: 'buy_xp' });
  out.push({ type: 'end' });
  return out;
}

export type ApplyOutcome = { ok: true } | { ok: false; code: string };

/** Applies one parsed, non-`end` action to `run`, all or nothing.
 *
 *  The engine is not atomic on rejection: `buy` deducts the gold before
 *  `place` checks the slot, so a buy into an occupied slot throws
 *  SLOT_OCCUPIED with the gold already gone. Restoring from a copy is what
 *  makes "an illegal action changes nothing" (SPEC 4.2) true. The shop
 *  stream is not part of `run`, but no engine call draws from it before its
 *  own checks pass, so it needs no restore. */
export function applyAction(run: RunState, action: Action, shopStream: E.Rng): ApplyOutcome {
  const backup = structuredClone(run);
  try {
    switch (action.type) {
      case 'buy': E.buy(run, action.offer, action.slot); break;
      case 'sell': E.sell(run, action.slot); break;
      case 'reroll': E.reroll(run, shopStream); break;
      case 'freeze': E.toggleFreeze(run); break;
      case 'buy_xp': E.buyXp(run); break;
      case 'end': throw new Error('applyAction: `end` is handled by the caller');
    }
    return { ok: true };
  } catch (err) {
    for (const k of Object.keys(backup) as (keyof RunState)[]) {
      (run as unknown as Record<string, unknown>)[k] = backup[k];
    }
    if (!(err instanceof E.EngineError)) throw err;
    return { ok: false, code: err.code };
  }
}
