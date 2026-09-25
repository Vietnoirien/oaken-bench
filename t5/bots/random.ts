/** Baseline 1: random (t5/SPEC.md section 5.1).
 *
 *  Uniform over action TYPES first, then uniform over that type's legal
 *  instances. Uniform over all legal actions directly would be ~90% buys
 *  (7 offers x 10 slots) and would end its turn almost never, which is a
 *  stronger bot than "random" is meant to name. */
import { Rng } from 'oaken-engine';
import { legalActions, type Action, type ActionType, type DecideState } from 'oaken-t5';

const ORDER: readonly ActionType[] = ['buy', 'sell', 'reroll', 'freeze', 'buy_xp', 'end'];

export function decide(state: DecideState): Action[] {
  // botSeed is fixed for the whole day, so the call index is mixed in:
  // without it every call of a day would draw the same first value.
  const rng = new Rng((state.botSeed ^ Math.imul(state.call + 1, 0x9e3779b1)) >>> 0);
  const byType = new Map<ActionType, Action[]>();
  for (const a of legalActions(state.run)) {
    const list = byType.get(a.type);
    if (list) list.push(a);
    else byType.set(a.type, [a]);
  }
  const types = ORDER.filter((t) => byType.has(t));
  const pick = rng.pick(byType.get(rng.pick(types)) as Action[]);
  return [pick];
}
