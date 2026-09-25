import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as E from 'oaken-engine';
import { parseAction, legalActions, applyAction } from '../sim/actions.js';
import { actionPhase, botSeed, comparePoints, playMatch, startDay } from '../sim/match.js';
import { playPair, pairStats, stableMatch, requireValid } from '../sim/evaluate.js';
import { CONTRACT_VERSION, DECIDE_BUDGET_MS, EvaluationVoidError, type BotCounters, type DecideState, type Decide } from '../sim/contract.js';
import { decide as random } from '../bots/random.js';
import { decide as cheapest } from '../bots/cheapest.js';
import { decide as merger } from '../bots/merger.js';

const childEnv = { ...process.env };
delete childEnv.NODE_TEST_CONTEXT;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const end: Decide = () => [{ type: 'end' }];
const bot = (decide: Decide, name = 'test') => ({ name, decide });
const counters = (): BotCounters => ({ calls: 0, actions: 0, illegal: 0, malformed: 0, decideErrors: 0, dropped: 0, impure: 0 });
function phase(decide: Decide, run = E.createRun('p', 1), purity = false) {
  const c = counters();
  actionPhase(bot(decide), run, E.shopRng(1), null, 1, 0, c, { max: 0, over: 0 }, { checkPurity: purity });
  return { c, run };
}
function view(run = E.createRun('self', 1)): DecideState {
  return { contract: CONTRACT_VERSION, run, lastCombat: null, botSeed: 123, call: 0, callsLeft: 32, actionsLeft: 64 };
}
function offer(id: string, rarity: E.Rarity = 0): E.ShopOffer {
  return { itemId: id, rarity, cost: E.getItem(id).tiers[rarity].cost };
}
function cli(args: string[], env: Record<string, string> = {}) {
  return spawnSync(process.execPath, ['--experimental-strip-types', '--import', path.join(root, 't5/sim/register.mjs'), path.join(root, 't5/sim/cli.ts'), ...args], {
    encoding: 'utf8', timeout: 20000, env: { ...childEnv, OAKEN_T5_SIM_LOG: 'off', ...env },
  });
}

test('action shapes reject fractional, missing, nonfinite and unknown values', () => {
  for (const raw of [null, [], {}, { type: 'move' }, { type: 'buy', offer: 1.5, slot: 0 },
    { type: 'sell', slot: NaN }, { type: 'buy', offer: 0 }, { type: 'sell', slot: Infinity }]) {
    assert.equal(parseAction(raw), null);
  }
  assert.deepEqual(parseAction({ type: 'buy', offer: -1, slot: 99, extra: true }), { type: 'buy', offer: -1, slot: 99 });
});

test('rejected buys roll back gold, offer and tower; next legal buy still works', () => {
  const run = E.createRun('p', 1), stream = E.shopRng(1);
  run.gold = 100;
  run.shop.offers[0] = offer('bone_club');
  E.place(run.tower, 0, 'bone_club', 0);
  const before = structuredClone(run);
  assert.deepEqual(applyAction(run, { type: 'buy', offer: 0, slot: 0 }, stream), { ok: false, code: 'SLOT_OCCUPIED' });
  assert.deepEqual(run, before);
  assert.equal(applyAction(run, { type: 'buy', offer: 0, slot: 1 }, stream).ok, true);
});

test('legalActions enumerates accepted moves without mutating state', () => {
  const run = E.createRun('p', 31), stream = E.shopRng(31);
  startDay(run, stream);
  E.place(run.tower, 3, 'bone_club', 0);
  const before = structuredClone(run);
  const actions = legalActions(run);
  for (const action of actions.filter((a) => a.type !== 'end')) {
    assert.equal(applyAction(structuredClone(run), action, E.shopRng(31)).ok, true);
  }
  assert.deepEqual(run, before);
  assert.equal(actions.at(-1)?.type, 'end');
});

test('day start and reroll share one persistent shop stream; freeze consumes none', () => {
  const run = E.createRun('p', 55), stream = E.shopRng(55), expectedStream = E.shopRng(55);
  const shop = E.createShop();
  startDay(run, stream);
  E.rollShop(shop, 1, expectedStream);
  assert.deepEqual(run.shop, shop);
  assert.equal(applyAction(run, { type: 'reroll' }, stream).ok, true);
  E.rollShop(shop, 1, expectedStream);
  assert.deepEqual(run.shop.offers, shop.offers);
  run.shop.frozen = true;
  const frozen = structuredClone(run.shop);
  startDay(run, stream);
  assert.deepEqual(run.shop, frozen);
  run.shop.frozen = false;
  startDay(run, stream);
  E.rollShop(shop, 1, expectedStream);
  assert.deepEqual(run.shop.offers, shop.offers);
});

test('daily caps bind: free actions cannot loop; no extra decide after action cap', () => {
  const one = phase(() => [{ type: 'freeze' }]);
  assert.equal(one.c.calls, 32); assert.equal(one.c.actions, 32);
  const many = phase(() => Array.from({ length: 70 }, () => ({ type: 'freeze' })));
  assert.equal(many.c.calls, 1); assert.equal(many.c.actions, 64); assert.equal(many.c.dropped, 6);
});

test('malformed and illegal actions drop the batch tail, retain prefix, then re-decide', () => {
  for (const bad of [{ type: 'sell', slot: 99 }, { type: 'buy', offer: 0.5, slot: 1 }]) {
    const { c, run } = phase((s) => s.call === 0 ? [{ type: 'freeze' }, bad, { type: 'freeze' }] as any : [{ type: 'end' }]);
    assert.equal(c.calls, 2); assert.equal(c.actions, 2); assert.equal(c.dropped, 1);
    assert.equal(c.illegal + c.malformed, 1); assert.equal(run.shop.frozen, true);
  }
});

test('end, empty, throws and non-array returns terminate a phase', () => {
  const ended = phase(() => [{ type: 'end' }, { type: 'freeze' }]);
  assert.equal(ended.c.actions, 0); assert.equal(ended.c.dropped, 1);
  for (const f of [() => [], () => { throw new Error('bot'); }, () => ({ type: 'freeze' })]) {
    const { c } = phase(f as Decide); assert.equal(c.calls, 1); assert.equal(c.actions, 0);
  }
});

test('cyclic returns are malformed; input mutation never reaches the engine', () => {
  const a: any[] = []; a.push(a);
  assert.equal(phase(() => a).c.malformed, 1);
  const { run } = phase((s) => { s.run.gold = 999999; s.run.tower.entries.push({ slot: 0, itemId: 'bone_club', rarity: 3 }); return []; });
  assert.equal(run.gold, 0); assert.deepEqual(run.tower.entries, []);
});

test('purity probe catches a shared array changed by the second call', () => {
  const a: any[] = []; let n = 0;
  const { c } = phase(() => { a[0] = { type: 'sell', slot: n++ }; return a; }, undefined, true);
  assert.equal(c.impure, 32);
});

test('bot views redact identity and engine seed and report exact allowances', () => {
  const seen: DecideState[] = [];
  playMatch(987, [bot((s) => { seen.push(s); return s.call === 0 ? [{ type: 'freeze' }] : []; }), bot(end)]);
  assert.ok(seen.length > 2);
  for (const s of seen) {
    assert.equal(s.run.seed, 0); assert.equal(s.run.playerId, 'self');
    assert.equal(s.callsLeft, 32 - s.call); assert.equal(s.actionsLeft, 64 - s.call);
    if (s.lastCombat) assert.equal(s.lastCombat.opponent.playerId, 'opponent');
  }
  assert.notEqual(botSeed(987, 0, 1), botSeed(987, 1, 1));
  assert.notEqual(botSeed(987, 0, 1), botSeed(987, 0, 2));
});

test('empty towers draw, lose lives and terminate on day four', () => {
  const r = playMatch(0, [bot(end), bot(end)]);
  assert.equal(r.days, 4); assert.equal(r.points, 0.5);
  assert.deepEqual(r.runs.map((r) => [r.status, r.lives]), [['lost', 0], ['lost', 0]]);
});

test('both snapshots precede combat; capture callback receives defensive copies', () => {
  const seen: E.Snapshot[] = [];
  const r = playMatch(2, [bot(cheapest), bot(cheapest)], { onSnapshot: (s) => {
    seen.push(structuredClone(s)); s.hp = -999; s.entries.length = 0;
  } });
  assert.deepEqual(stableMatch(r), stableMatch(playMatch(2, [bot(cheapest), bot(cheapest)])));
  for (let i = 0; i < seen.length; i += 2) {
    assert.equal(seen[i].hp, seen[i].maxHp); assert.equal(seen[i + 1].hp, seen[i + 1].maxHp);
  }
});

test('comparison uses status, trophies, then lives, never remaining combat hp', () => {
  const a = E.createRun('a', 1), b = E.createRun('b', 1);
  a.hp = 0; assert.equal(comparePoints(a, b), 0.5);
  a.lives++; assert.equal(comparePoints(a, b), 1);
  b.trophies++; assert.equal(comparePoints(a, b), 0);
  a.status = 'won'; assert.equal(comparePoints(a, b), 1);
});

test('cheapest ties use offer index then lowest placeable slot', () => {
  const s = view(); s.run.gold = 100; s.run.shop.offers = [offer('bone_club'), offer('bone_club'), null, null, null, null, null];
  E.place(s.run.tower, 0, 'bone_club', 0);
  assert.deepEqual(cheapest(s), [{ type: 'buy', offer: 0, slot: 1 }]);
  s.run.gold = 0; assert.deepEqual(cheapest(s), [{ type: 'end' }]);
});

test('cheapest skips an unplaceable cheaper offer', () => {
  const s = view(); s.run.gold = 1000; s.run.tower = E.createTower(Array(10).fill('Magic'));
  const magic = E.loadItems().filter((i) => ['Spell', 'Artifact'].includes(i.type)).sort((a, b) => b.tiers[0].cost - a.tiers[0].cost)[0];
  s.run.shop.offers = [offer('bone_club'), offer(magic.id), null, null, null, null, null];
  assert.deepEqual(cheapest(s), [{ type: 'buy', offer: 1, slot: 0 }]);
});

test('merger buys a triple before cheaper unmatched items and actually merges', () => {
  const s = view(); s.run.gold = 1000;
  E.place(s.run.tower, 0, 'bone_club', 0); E.place(s.run.tower, 1, 'bone_club', 0);
  s.run.shop.offers = [offer('bone_club'), null, null, null, null, null, null];
  const [a] = merger(s);
  assert.deepEqual(a, { type: 'buy', offer: 0, slot: 2 });
  assert.equal(applyAction(s.run, a, E.shopRng(1)).ok, true);
  assert.deepEqual(s.run.tower.entries, [{ slot: 0, itemId: 'bone_club', rarity: 1 }]);
});

test('merger sells an unrelated item to make space for a triple, not for legendary copies', () => {
  for (const rarity of [0, 3] as const) {
    const s = view(); s.run.gold = 10000;
    const other = E.loadItems().filter((x) => x.id !== 'bone_club');
    s.run.tower.entries = Array.from({ length: 10 }, (_, slot) => ({ slot, itemId: slot < 2 ? 'bone_club' : other[slot - 2].id, rarity }));
    s.run.shop.offers = [offer('bone_club', rarity), null, null, null, null, null, null];
    const actions = merger(s);
    if (rarity === 3) assert.deepEqual(actions, [{ type: 'end' }]);
    else {
      assert.equal(actions.length, 2); assert.equal(actions[0].type, 'sell');
      for (const a of actions) assert.equal(applyAction(s.run, a, E.shopRng(1)).ok, true);
      assert.equal(s.run.tower.entries.length, 8);
    }
  }
});

test('random is a pure seeded choice among legal actions', () => {
  const s = view(); startDay(s.run, E.shopRng(1));
  const before = structuredClone(s);
  for (let call = 0; call < 32; call++) {
    s.call = call; const a = random(s);
    assert.deepEqual(a, random(structuredClone(s)));
    assert.ok(legalActions(s.run).some((x) => JSON.stringify(x) === JSON.stringify(a[0])));
  }
  assert.deepEqual(s.run, before.run);
});

test('seat-balanced paired scores reverse exactly and self-play averages one half', () => {
  const a = bot(random, 'random'), b = bot(cheapest, 'cheapest');
  const ab = pairStats(a.name, b.name, playPair([0, 7, 0xffffffff], a, b));
  const ba = pairStats(b.name, a.name, playPair([0, 7, 0xffffffff], b, a));
  assert.equal(ab.matches, 6); assert.equal(ab.points + ba.points, 6);
  assert.equal(pairStats(a.name, a.name, playPair([1, 9], a, a)).winRate, 0.5);
});

test('deterministic records omit timing and invalid evaluations cannot become scores', () => {
  const r = playMatch(1, [bot(random), bot(merger)]);
  assert.deepEqual(stableMatch(r), stableMatch(playMatch(1, [bot(random), bot(merger)])));
  r.timing.overBudgetCalls[1] = 1; assert.throws(() => requireValid([r]), /budget/);
  r.timing.overBudgetCalls[1] = 0; r.counters[0].impure = 1; assert.throws(() => requireValid([r]), /impure/);
  for (const seed of [-1, 0.5, 4294967296, NaN]) assert.throws(() => playMatch(seed, [bot(end), bot(end)]), /uint32/);
});

test('CLI matrix and play JSON repeat byte for byte in fresh processes', () => {
  for (const args of [['matrix', '--seeds', '1-3'], ['play', '--bot', 'baseline:random', '--vs', 'baseline:merger', '--seeds', '1-3', '--json']]) {
    const a = cli(args), b = cli(args);
    assert.equal(a.status, 0, a.stderr); assert.equal(b.status, 0, b.stderr);
    assert.equal(a.stdout, b.stdout);
    assert.equal(a.error, undefined); assert.equal(b.error, undefined);
    assert.ok(a.stdout);
    const doc = JSON.parse(a.stdout); assert.equal(doc.protocol, 't5-visible-head-to-head/1');
    assert.ok(!a.stdout.includes('maxDecideMs'));
    if (doc.pairs) for (const p of doc.pairs) {
      assert.equal(doc.winRate[p.a][p.b], p.winRate);
      assert.equal(p.matches, 6);
    }
  }
});

test('CLI rejects empty, duplicate, invalid and oversized seeds and unknown options', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 't5-input-'));
  try {
    const file = path.join(dir, 'seeds.json'); writeFileSync(file, '[]');
    for (const spec of [file, '1,1', '4294967296', '0-4294967295', '3-1', '-1']) {
      const r = cli(['matrix', '--seeds', spec]); assert.notEqual(r.status, 0); assert.equal(r.stdout, '');
    }
    assert.notEqual(cli(['matrix', '--typo', 'visible']).status, 0);
    assert.notEqual(cli(['matrix', '--seeds', '1', '--seeds', '2']).status, 0);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('CLI detects stateful bots without emitting scores; launcher kills nontermination', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 't5-bot-'));
  try {
    const file = path.join(dir, 'bot.mjs');
    writeFileSync(file, 'let n = 0; export function decide() { return [{type:"sell",slot:n++}]; }');
    const r = cli(['play', '--bot', file, '--vs', 'baseline:cheapest', '--seeds', '1', '--json']);
    assert.equal(r.status, 2); assert.match(r.stderr, /impure/); assert.equal(r.stdout, '');
    writeFileSync(file, 'export function decide() { while(true) {} }');
    const hung = spawnSync(path.join(root, 't5/bin/t5-sim'), ['play', '--bot', file, '--vs', 'baseline:cheapest', '--seeds', '1', '--json'], {
      encoding: 'utf8', timeout: 10000,
      env: { ...childEnv, OAKEN_T5_NODE: process.execPath, OAKEN_T5_SIM_LOG: 'off', OAKEN_T5_TIMEOUT: '1' },
    });
    assert.equal(hung.status, 124, hung.stderr); assert.equal(hung.stdout, '');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('CLI audit log records digests and totals, without decision arguments', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 't5-log-'));
  try {
    const log = path.join(dir, 'log.jsonl');
    const r = cli(['matrix', '--seeds', '1'], { OAKEN_T5_SIM_LOG: log });
    assert.equal(r.status, 0, r.stderr);
    const record = JSON.parse(readFileSync(log, 'utf8'));
    assert.equal(record.matches, 12); assert.match(record.bots[0].sha256, /^[a-f0-9]{64}$/);
    assert.equal(record.actions, undefined); assert.equal(record.results, undefined);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});


test('decision and purity-probe overruns void immediately, before any action applies', () => {
  for (const slowCall of [1, 2]) {
    let calls = 0;
    const run = E.createRun('p', 1);
    const c = counters(), timing = { max: 0, over: 0 };
    assert.throws(() => actionPhase(bot(() => {
      if (++calls === slowCall) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, DECIDE_BUDGET_MS + 50);
      return [{ type: 'freeze' }];
    }), run, E.shopRng(1), null, 1, 0, c, timing, { checkPurity: true }), EvaluationVoidError);
    assert.equal(calls, slowCall); assert.equal(c.actions, 0);
    assert.equal(run.shop.frozen, false); assert.equal(timing.over, 1);
  }
});

test('CLI over-budget decision returns failure with no score JSON', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 't5-slow-'));
  try {
    const file = path.join(dir, 'slow.mjs');
    writeFileSync(file, `export function decide() {
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ${DECIDE_BUDGET_MS + 50});
      return [];
    }`);
    const r = cli(['play', '--bot', file, '--vs', 'baseline:cheapest', '--seeds', '1', '--json']);
    assert.equal(r.status, 2, r.stderr); assert.match(r.stderr, /budget exceeded.*no score/);
    assert.equal(r.stdout, '');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('snapshot rejection forfeits the offending side; unexpected engine faults abort', () => {
  const ingest = E.SnapshotStore.prototype.ingest;
  for (const rejected of [['p0'], ['p1'], ['p0', 'p1']]) {
    const stub = mock.method(E.SnapshotStore.prototype, 'ingest', function(this: E.SnapshotStore, run: E.RunState) {
      if (rejected.includes(run.playerId)) throw new E.EngineError('SNAPSHOT_ILLEGAL_RARITY');
      return ingest.call(this, run);
    });
    try {
      const r = playMatch(1, [bot(end), bot(end)]);
      assert.equal(r.points, rejected.length === 2 ? 0.5 : rejected[0] === 'p0' ? 0 : 1);
      assert.equal(r.days, 1); assert.equal(r.runs[0].trophies, 0);
    } finally { stub.mock.restore(); }
  }
  const stub = mock.method(E.SnapshotStore.prototype, 'ingest', () => { throw new Error('engine fault'); });
  try { assert.throws(() => playMatch(1, [bot(end), bot(end)]), /engine fault/); }
  finally { stub.mock.restore(); }
});

test('frozen contract verifies and full visible matrix matches the recorded fixture', () => {
  const frozen = spawnSync('sha256sum', ['-c', 't5/FROZEN.sha256'], { cwd: root, encoding: 'utf8' });
  assert.equal(frozen.error, undefined); assert.equal(frozen.status, 0, frozen.stdout + frozen.stderr);
  const r = cli(['matrix']);
  assert.equal(r.error, undefined); assert.equal(r.status, 0, r.stderr);
  const actual = JSON.parse(r.stdout);
  const expected = JSON.parse(readFileSync(path.join(root, 't5/baselines.visible.json'), 'utf8'));
  // Runtime is reported, but the game/provenance fields must still agree.
  assert.equal(actual.runtime, process.version);
  expected.runtime = actual.runtime;
  assert.deepEqual(actual, expected);
});
