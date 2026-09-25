/** One T5 match: two bots, one seed, lockstep days (t5/SPEC.md section 3). */
import * as E from 'oaken-engine';
import type { RunState, Snapshot } from 'oaken-engine';
import {
  CONTRACT_VERSION, DECIDE_BUDGET_MS, EvaluationVoidError, MAX_ACTIONS_PER_DAY, MAX_CALLS_PER_DAY, MAX_DAYS,
  type BotCounters, type Decide, type DecideState, type LastCombat,
} from './contract.js';
import { applyAction, parseAction } from './actions.js';
import { monotonicMs } from './harden.js';

export interface Bot {
  name: string;
  decide: Decide;
}

export interface RunSummary {
  status: RunState['status'];
  day: number;
  trophies: number;
  lives: number;
  level: number;
}

export interface MatchResult {
  seed: number;
  days: number;
  /** Points for side 0: 1 win, 0.5 draw, 0 loss (SPEC 3.5). */
  points: number;
  /** Snapshot error code per side if that side's tower was refused (SPEC 3.3). */
  invalidSnapshot: [string | null, string | null];
  runs: [RunSummary, RunSummary];
  counters: [BotCounters, BotCounters];
  /** Wall-clock facts. Kept apart from everything above because they are
   *  the only non-deterministic output: nothing in `points` depends on them. */
  timing: { maxDecideMs: [number, number]; overBudgetCalls: [number, number] };
}

export interface MatchOptions {
  /** Call decide twice per call and compare (SPEC 4.5). */
  checkPurity?: boolean;
  /** Receives copies after both towers validate, before combat. */
  onSnapshot?: (snapshot: Snapshot, side: 0 | 1) => void;
}

// Salt for botSeed. Arbitrary, distinct from the engine's three stream salts.
const BOT_SALT = 0x7f4a7c15;

/** A per-(match, side, day) uint32 for the bot's own PRNG. Derived through
 *  one mulberry32 draw rather than a plain XOR of the inputs, so it does not
 *  hand the bot the run seed the state deliberately withholds (SPEC 4.1)
 *  by a one-line inversion. It is not a secret either -- see SPEC 4.1. */
export function botSeed(seed: number, side: 0 | 1, day: number): number {
  const mixed = (seed ^ BOT_SALT ^ Math.imul(day, 0x9e3779b1) ^ Math.imul(side + 1, 0x85ebca6b)) >>> 0;
  return Math.floor(new E.Rng(mixed).next() * 4294967296) >>> 0;
}

function newCounters(): BotCounters {
  return { calls: 0, actions: 0, illegal: 0, malformed: 0, decideErrors: 0, dropped: 0, impure: 0 };
}

/** SPEC.md section 8 steps 2-4, with the shop stream held here.
 *
 *  The engine's startDay() API accepts no stream, while reroll() requires
 *  one. A caller that combines them cannot pass a single shared stream
 *  and a fresh shopRng(seed) would replay day 1's draws. One stream per run,
 *  owned by the simulator and passed to both, is the only way to keep
 *  SPEC.md 1.2's single shop stream. `selftest` in cli.ts checks this
 *  matches the engine's startDay() on a run that never rerolls. */
export function startDay(run: RunState, shopStream: E.Rng): void {
  run.gold += E.income(run.level);
  run.hp = run.maxHp;
  if (!run.shop.frozen) E.rollShop(run.shop, run.level, shopStream);
}

function viewOf(run: RunState, lastCombat: LastCombat | null, seed: number, side: 0 | 1,
                call: number, callsLeft: number, actionsLeft: number): DecideState {
  const r = structuredClone(run);
  r.seed = 0;
  r.playerId = 'self';
  const previous = lastCombat ? structuredClone(lastCombat) : null;
  if (previous) previous.opponent.playerId = 'opponent';
  return {
    contract: CONTRACT_VERSION,
    run: r,
    lastCombat: previous,
    botSeed: botSeed(seed, side, run.day),
    call, callsLeft, actionsLeft,
  };
}

/** One bot's action phase for one day (SPEC 4.2-4.3). */
export function actionPhase(bot: Bot, run: RunState, stream: E.Rng, lastCombat: LastCombat | null,
                     seed: number, side: 0 | 1, c: BotCounters,
                     t: { max: number; over: number }, opts: MatchOptions): void {
  let actions = 0;
  for (let call = 0; call < MAX_CALLS_PER_DAY && actions < MAX_ACTIONS_PER_DAY; call++) {
    const view = () => viewOf(run, lastCombat, seed, side, call, MAX_CALLS_PER_DAY - call,
                              MAX_ACTIONS_PER_DAY - actions);
    c.calls += 1;
    let out: unknown;
    const input = view();
    const t0 = monotonicMs();
    try {
      out = bot.decide(input);
    } catch {
      c.decideErrors += 1;
      return;
    } finally {
      const ms = monotonicMs() - t0;
      if (ms > t.max) t.max = ms;
      if (ms > DECIDE_BUDGET_MS) {
        t.over += 1;
        throw new EvaluationVoidError('decide budget exceeded');
      }
    }
    // Serialize before the second call: a bot may return a reused array.
    let encoded: string | undefined;
    try { encoded = JSON.stringify(out); } catch { c.malformed += 1; return; }
    if (opts.checkPurity) {
      const probeInput = view();
      let again: unknown;
      let threw = false;
      const probeStart = monotonicMs();
      try { again = bot.decide(probeInput); } catch { threw = true; }
      const probeMs = monotonicMs() - probeStart;
      t.max = Math.max(t.max, probeMs);
      if (probeMs > DECIDE_BUDGET_MS) {
        t.over += 1;
        throw new EvaluationVoidError('purity probe budget exceeded');
      }
      try {
        if (threw || JSON.stringify(again) !== encoded) c.impure += 1;
      } catch { c.impure += 1; }
    }
    if (!Array.isArray(out) || !encoded) { c.malformed += 1; return; }
    out = JSON.parse(encoded);
    const batch = out as unknown[];
    if (batch.length === 0) return;
    for (let i = 0; i < batch.length; i++) {
      if (actions >= MAX_ACTIONS_PER_DAY) { c.dropped += batch.length - i; return; }
      const a = parseAction(batch[i]);
      if (a?.type === 'end') { c.dropped += batch.length - i - 1; return; }
      actions += 1;
      c.actions += 1;
      if (a === null) {
        c.malformed += 1;
        c.dropped += batch.length - i - 1;
        break;
      }
      const res = applyAction(run, a, stream);
      if (!res.ok) {
        c.illegal += 1;
        c.dropped += batch.length - i - 1;
        break;
      }
    }
  }
}

function summary(run: RunState): RunSummary {
  return { status: run.status, day: run.day, trophies: run.trophies, lives: run.lives, level: run.level };
}

const STATUS_RANK: Record<RunState['status'], number> = { won: 2, active: 1, lost: 0 };

/** SPEC 3.5: compare (status, trophies, lives) lexicographically. */
export function comparePoints(a: RunState, b: RunState): number {
  const ka = [STATUS_RANK[a.status], a.trophies, a.lives];
  const kb = [STATUS_RANK[b.status], b.trophies, b.lives];
  for (let i = 0; i < 3; i++) {
    if (ka[i] > kb[i]) return 1;
    if (ka[i] < kb[i]) return 0;
  }
  return 0.5;
}

export function playMatch(seed: number, bots: [Bot, Bot], opts: MatchOptions = {}): MatchResult {
  if (!Number.isInteger(seed) || seed < 0 || seed > 0xffffffff) throw new Error('seed must be a uint32');
  const runs: [RunState, RunState] = [E.createRun('p0', seed), E.createRun('p1', seed)];
  // Both runs draw from their own copy of the same shop stream: day 1's
  // shop is identical for both, and diverges only through their own
  // choices (level-dependent rarity odds, rerolls).
  const streams: [E.Rng, E.Rng] = [E.shopRng(seed), E.shopRng(seed)];
  const counters: [BotCounters, BotCounters] = [newCounters(), newCounters()];
  const timing = [{ max: 0, over: 0 }, { max: 0, over: 0 }];
  const last: [LastCombat | null, LastCombat | null] = [null, null];
  const invalid: [string | null, string | null] = [null, null];
  let days = 0;

  for (let day = 1; day <= MAX_DAYS; day++) {
    days = day;
    for (const s of [0, 1] as const) startDay(runs[s], streams[s]);
    for (const s of [0, 1] as const) {
      actionPhase(bots[s], runs[s], streams[s], last[s], seed, s, counters[s], timing[s], opts);
    }
    // Both snapshots before either combat: resolveCombat() writes the
    // combat's hp back into the run, and the rival must be fought at the hp
    // it entered the day with, not at what is left after its own fight.
    //
    // ingest() validates (SPEC.md 9.1), and legal actions CAN produce a
    // tower it rejects: nine Rares merged up to a Legendary while still at
    // level 1 is SNAPSHOT_ILLEGAL_RARITY. The engine allowed every step of
    // that, so the simulator cannot refuse the actions; the server refuses
    // the tower instead, and the side it belongs to forfeits (SPEC 3.3).
    const store = new E.SnapshotStore();
    const snaps: (Snapshot | null)[] = [null, null];
    for (const s of [0, 1] as const) {
      try {
        snaps[s] = store.ingest(runs[s]);
      } catch (err) {
        if (!(err instanceof E.EngineError)) throw err;
        invalid[s] = err.code;
      }
    }
    if (invalid[0] || invalid[1]) break;
    for (const s of [0, 1] as const) opts.onSnapshot?.(structuredClone(snaps[s]!), s);
    for (const s of [0, 1] as const) {
      // The engine's resolveCombat() puts `run` on side A and derives the
      // combat seed from (run.seed, run.day) -- identical for both runs
      // here, so each side fights the other's tower with the same crit
      // stream and the same side-A slot-order advantage.
      const opp = snaps[1 - s] as Snapshot;
      const res = E.resolveCombat(runs[s], opp);
      last[s] = { opponent: structuredClone(opp), result: res.result, hpLeft: res.hpA };
    }
    if (runs[0].status !== 'active' || runs[1].status !== 'active') break;
    for (const s of [0, 1] as const) E.endDay(runs[s]);
  }

  if (days === MAX_DAYS && runs.every((r) => r.status === 'active') && invalid.every((x) => !x)) {
    throw new Error('match exceeded the day safety limit');
  }
  let points = comparePoints(runs[0], runs[1]);
  if (invalid[0] || invalid[1]) points = invalid[0] && invalid[1] ? 0.5 : invalid[0] ? 0 : 1;

  return {
    seed,
    days,
    points,
    invalidSnapshot: invalid,
    runs: [summary(runs[0]), summary(runs[1])],
    counters,
    timing: {
      maxDecideMs: [timing[0].max, timing[1].max],
      overBudgetCalls: [timing[0].over, timing[1].over],
    },
  };
}
