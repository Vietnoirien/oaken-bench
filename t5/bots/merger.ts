/** Baseline 3: merge whenever possible (t5/SPEC.md section 5.3).
 *
 *  Per call, among affordable offers, the first rule that matches wins:
 *    1. an offer that completes a triple (two copies of the same id AND
 *       rarity already in the tower),
 *    2. an offer that makes a pair (one copy already in the tower),
 *    3. the cheapest offer, so an empty tower still gets built.
 *  Ties within a rule go to the cheaper offer, then the lower index.
 *
 *  A triple is the only case worth selling for: with the tower full, the
 *  third copy has nowhere to land before it merges (the engine places, then
 *  merges), so rule 1 sells the cheapest item that is not one of the two
 *  copies -- if the refund still leaves the offer affordable. Rules 2 and 3
 *  never sell. Never rerolls, freezes or buys XP. */
import { canPlace, getItem } from 'oaken-engine';
import type { Action, DecideState } from 'oaken-t5';
import type { ShopOffer, TowerEntry } from 'oaken-engine';

function copies(entries: TowerEntry[], o: ShopOffer): number {
  return entries.filter((e) => e.itemId === o.itemId && e.rarity === o.rarity).length;
}

function freeSlot(state: DecideState, itemId: string): number {
  const { tower } = state.run;
  for (let s = 0; s < tower.slots.length; s++) if (canPlace(tower, s, itemId)) return s;
  return -1;
}

export function decide(state: DecideState): Action[] {
  const { gold, shop, tower } = state.run;
  const ranked = shop.offers
    .map((o, i) => ({ o, i }))
    .filter((x): x is { o: ShopOffer; i: number } => x.o !== null)
    .map((x) => ({ ...x, rule: x.o.rarity === 3 ? 0 : Math.min(copies(tower.entries, x.o), 2) }))
    // Higher rule first (2 = completes a triple), then cheaper, then index.
    .sort((a, b) => b.rule - a.rule || a.o.cost - b.o.cost || a.i - b.i);

  for (const { o, i, rule } of ranked) {
    const slot = freeSlot(state, o.itemId);
    if (o.cost <= gold && slot >= 0) return [{ type: 'buy', offer: i, slot }];
    if (rule === 2 && slot < 0) {
      const victims = tower.entries
        .filter((e) => !(e.itemId === o.itemId && e.rarity === o.rarity))
        .filter((e) => canPlace({ ...tower, entries: tower.entries.filter((v) => v !== e) }, e.slot, o.itemId))
        .map((e) => ({ e, refund: Math.floor(getItem(e.itemId).tiers[e.rarity].cost / 2) }))
        .sort((a, b) => a.refund - b.refund || a.e.slot - b.e.slot);
      const v = victims[0];
      if (v && o.cost <= gold + v.refund) {
        return [{ type: 'sell', slot: v.e.slot }, { type: 'buy', offer: i, slot: v.e.slot }];
      }
    }
  }
  return [{ type: 'end' }];
}
