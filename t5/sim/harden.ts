/** Makes the easy ways for a bot to break determinism, or to change the game
 *  it is being scored on, fail loudly (t5/SPEC.md section 4.5).
 *
 *  This is NOT a sandbox. A bot shares the simulator's process, realm and
 *  engine instance, and can still reach node:fs, performance.now, new Date()
 *  or Array.prototype. What this closes is the accidental path (a bot that
 *  calls Math.random because that is what bots do) and the cheap cheat
 *  (editing an item's damage in the shared, cached item table, which every
 *  later combat of BOTH sides would then read). The rest is contract, and a
 *  held-out evaluation should run each bot in its own process. */
import * as E from 'oaken-engine';

let done = false;

function deepFreeze(v: unknown, seen = new Set<unknown>()): void {
  if ((typeof v !== 'object' && typeof v !== 'function') || v === null || seen.has(v)) return;
  seen.add(v);
  for (const k of Reflect.ownKeys(v as object)) {
    const d = Object.getOwnPropertyDescriptor(v as object, k);
    if (d && 'value' in d) deepFreeze(d.value, seen);
  }
  Object.freeze(v);
}

function forbid(name: string): () => never {
  return () => {
    throw new Error(`${name} is forbidden during a T5 evaluation: decide() must be ` +
      `a deterministic function of its argument (t5/SPEC.md 4.5). Use state.botSeed ` +
      `with the engine's Rng instead.`);
  };
}

/** Wall clock, captured before Date.now is replaced. The simulator's own
 *  timing and log timestamps go through this; bots must not. */
export const wallClockMs: () => number = Date.now.bind(Date);
export const monotonicMs: () => number = performance.now.bind(performance);

export function harden(): void {
  if (done) return;
  done = true;
  Math.random = forbid('Math.random');
  Date.now = forbid('Date.now');
  Object.freeze(Math);
  // The item table is loaded once and returned by reference on every
  // loadItems()/getItem() call -- one mutable object for both bots and the
  // simulator. Frozen, a write to it throws (ES modules are strict mode).
  deepFreeze(E.loadItems());
  // Exported classes and constants: a patched Rng.prototype.nextInt would
  // change every crit roll in the match, not only the patching bot's.
  for (const v of Object.values(E)) {
    if (typeof v === 'function') {
      Object.freeze(v);
      if (v.prototype) Object.freeze(v.prototype);
    } else {
      deepFreeze(v);
    }
  }
}
