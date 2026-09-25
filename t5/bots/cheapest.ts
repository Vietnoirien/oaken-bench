/** Baseline 2: buy the cheapest item (t5/SPEC.md section 5.2).
 *
 *  One buy per call, re-deciding on the fresh state each time: a buy can
 *  trigger a merge that frees two slots, so a slot chosen before the buy
 *  would be stale after it. Never sells, rerolls, freezes or buys XP. */
import { canPlace } from 'oaken-engine';
import type { Action, DecideState } from 'oaken-t5';

export function decide(state: DecideState): Action[] {
  const { gold, shop, tower } = state.run;
  let best = -1;
  shop.offers.forEach((o, i) => {
    if (!o || o.cost > gold || !tower.slots.some((_, s) => canPlace(tower, s, o.itemId))) return;
    // Strict <, so the lowest offer index wins a tie.
    if (best < 0 || o.cost < (shop.offers[best]?.cost ?? Infinity)) best = i;
  });
  if (best < 0) return [{ type: 'end' }];
  const offer = shop.offers[best]!;
  for (let s = 0; s < tower.slots.length; s++) {
    if (canPlace(tower, s, offer.itemId)) return [{ type: 'buy', offer: best, slot: s }];
  }
  return [{ type: 'end' }];
}
