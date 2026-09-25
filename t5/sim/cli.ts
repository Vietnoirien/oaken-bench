/** T5 simulator CLI. Invoked through t5/bin/t5-sim; see t5/SPEC.md section 6.
 *
 *   t5-sim play   --bot <bot> --vs <bot> [--seeds <seeds>] [--json]
 *   t5-sim matrix --bots <bot>,<bot>,... [--seeds <seeds>] [--out <file>]
 *   t5-sim selftest
 *
 * <bot>   a path to a module exporting decide(), or baseline:<name>
 *         (random | cheapest | merger).
 * <seeds> visible (default) | <a>-<b> | <a>,<b>,... | <file.json>
 *
 * Successful play/matrix invocations append to the log (SPEC 6.2) unless
 * OAKEN_T5_SIM_LOG=off. */
import * as E from 'oaken-engine';
import { createHash } from 'node:crypto';
import { appendFileSync, readFileSync, readdirSync, lstatSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { CONTRACT_VERSION, LOAD_BUDGET_MS, type BotCounters, type Decide } from './contract.js';
import { harden, monotonicMs, wallClockMs } from './harden.js';
import { startDay, type Bot } from './match.js';
import { playPair, pairStats, round4, stableMatch } from './evaluate.js';
import { applyAction } from './actions.js';

const T5_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const VISIBLE_SEEDS = path.join(T5_DIR, 'seeds.visible.json');
const BASELINES = ['random', 'cheapest', 'merger'];

const sha256 = (b: string | Buffer) => createHash('sha256').update(b).digest('hex');

function die(msg: string): never {
  process.stderr.write(`t5-sim: ${msg}\n`);
  process.exit(2);
}

function parseArgs(argv: string[]): { cmd: string; flags: Map<string, string> } {
  const [cmd = '', ...rest] = argv;
  const flags = new Map<string, string>();
  for (let i = 0; i < rest.length; i++) {
    const k = rest[i];
    if (!k.startsWith('--')) die(`unexpected argument ${k}`);
    const allowed: Record<string, string[]> = {
      play: ['--bot', '--vs', '--seeds', '--json'],
      matrix: ['--bots', '--seeds', '--out'], selftest: [],
    };
    if (!allowed[cmd]?.includes(k) || flags.has(k.slice(2))) die(`unknown or repeated option ${k}`);
    const boolean = k === '--json';
    flags.set(k.slice(2), boolean ? 'true' : (rest[++i] ?? die(`${k} needs a value`)));
  }
  return { cmd, flags };
}

function readSeedFile(file: string): number[] {
  const v = JSON.parse(readFileSync(file, 'utf8'));
  const list = Array.isArray(v) ? v : v?.seeds;
  if (!Array.isArray(list)) die(`${file}: expected an array of seeds or {"seeds": [...]}`);
  return list;
}

function parseSeeds(spec: string): { source: string; seeds: number[] } {
  let seeds: number[];
  if (spec === 'visible') seeds = readSeedFile(VISIBLE_SEEDS);
  else if (spec.endsWith('.json')) seeds = readSeedFile(spec);
  else if (/^\d+-\d+$/.test(spec)) {
    const [a, b] = spec.split('-').map(Number);
    if (b < a || a < 0 || b > 0xffffffff || b - a >= 10000) die(`invalid or oversized seed range ${spec}`);
    seeds = Array.from({ length: b - a + 1 }, (_, i) => a + i);
  } else if (/^\d+(,\d+)*$/.test(spec)) seeds = spec.split(',').map(Number);
  else die(`cannot parse --seeds ${spec}`);
  if (!seeds.length || seeds.length > 10000 || new Set(seeds).size !== seeds.length) {
    die('supply 1-10000 distinct seeds');
  }
  for (const s of seeds) {
    if (!Number.isInteger(s) || s < 0 || s > 0xffffffff) die(`seed ${s} is not a uint32`);
  }
  return { source: spec, seeds };
}

function botPath(spec: string): string {
  if (spec.startsWith('baseline:')) {
    const name = spec.slice('baseline:'.length);
    if (!BASELINES.includes(name)) die(`unknown baseline ${name} (have: ${BASELINES.join(', ')})`);
    return path.join(T5_DIR, 'bots', `${name}.ts`);
  }
  return path.resolve(spec);
}

/** Digest of every source file under a directory. The entry file alone
 *  would miss a strategy change made in a helper module it imports -- and
 *  "how many distinct strategies were tried" (#41) is read off these. */
function treeDigest(dir: string): string {
  const files: string[] = [];
  const walk = (d: string) => {
    for (const n of readdirSync(d).sort()) {
      if (n.startsWith('.') || ['node_modules', 'hidden', 'refengine'].includes(n)) continue;
      const p = path.join(d, n);
      if (lstatSync(p).isSymbolicLink()) continue;
      if (lstatSync(p).isDirectory()) walk(p);
      else if (/\.(ts|mts|js|mjs|json)$/.test(n)) files.push(p);
    }
  };
  walk(dir);
  return sha256(files.map((f) => `${path.relative(dir, f)}\0${sha256(readFileSync(f))}\n`).join(''));
}

interface LoadedBot extends Bot {
  spec: string;
  sha256: string;
  treeSha256: string;
  loadMs: number;
}

async function loadBot(spec: string): Promise<LoadedBot> {
  const file = botPath(spec);
  let src: Buffer;
  try { src = readFileSync(file); } catch { die(`cannot read bot ${file}`); }
  const t0 = monotonicMs();
  const mod = await import(pathToFileURL(file).href);
  const loadMs = monotonicMs() - t0;
  if (loadMs > LOAD_BUDGET_MS) die('module load budget exceeded: evaluation has no score');
  if (typeof mod.decide !== 'function') die(`${file} does not export a decide() function`);
  return {
    name: spec.startsWith('baseline:') ? spec.slice('baseline:'.length) : path.basename(file),
    decide: mod.decide as Decide,
    spec,
    sha256: sha256(src),
    treeSha256: treeDigest(path.dirname(file)),
    loadMs,
  };
}

function engineDigest(): string {
  const root = process.env.OAKEN_T5_ENGINE as string;
  const src = path.join(root, 'src');
  const parts = readdirSync(src).filter((n) => n.endsWith('.ts')).sort()
    .map((n) => `src/${n}\0${sha256(readFileSync(path.join(src, n)))}\n`);
  parts.push(`data/items.json\0${sha256(readFileSync(path.join(root, 'data', 'items.json')))}\n`);
  return sha256(parts.join(''));
}

function logInvocation(entry: Record<string, unknown>): void {
  const target = process.env.OAKEN_T5_SIM_LOG ?? path.resolve('.t5-sim-log.jsonl');
  if (target === 'off') return;
  try {
    appendFileSync(target, JSON.stringify({ ts: new Date(wallClockMs()).toISOString(), ...entry }) + '\n');
  } catch (err) {
    process.stderr.write(`t5-sim: warning: could not append to ${target}: ${err}\n`);
  }
}

function sumCounters(into: BotCounters, c: BotCounters): void {
  for (const k of Object.keys(c) as (keyof BotCounters)[]) into[k] += c[k];
}

function seedInfo(source: string, seeds: number[]) {
  const visible = new Set(readSeedFile(VISIBLE_SEEDS));
  return {
    source, count: seeds.length, sha256: sha256(JSON.stringify(seeds)),
    allVisible: seeds.every((s) => visible.has(s)),
  };
}

const botInfo = (b: LoadedBot) => ({ name: b.name, spec: b.spec, sha256: b.sha256, treeSha256: b.treeSha256 });

function provenance(source: string, seeds: number[], bots: LoadedBot[]) {
  return {
    schemaVersion: 1, protocol: 't5-visible-head-to-head/1', contract: CONTRACT_VERSION,
    runtime: process.version,
    spec: { sha256: sha256(readFileSync(path.join(T5_DIR, 'SPEC.md'))) },
    simulator: { sha256: treeDigest(path.join(T5_DIR, 'sim')) },
    engine: { sha256: engineDigest() },
    seeds: seedInfo(source, seeds), bots: bots.map(botInfo),
  };
}

async function cmdPlay(flags: Map<string, string>, argv: string[]): Promise<void> {
  const specA = flags.get('bot') ?? die('play needs --bot');
  const specB = flags.get('vs') ?? die('play needs --vs');
  const { source, seeds } = parseSeeds(flags.get('seeds') ?? 'visible');
  harden();
  const a = await loadBot(specA);
  const b = await loadBot(specB);
  const results = playPair(seeds, a, b);
  const stats = pairStats(a.name, b.name, results);
  const counters = [0, 1].map((side) => {
    const c: BotCounters = { calls: 0, actions: 0, illegal: 0, malformed: 0, decideErrors: 0, dropped: 0, impure: 0 };
    for (const r of results) sumCounters(c, r.counters[side]);
    return c;
  });
  const over = results.reduce((s, r) => s + r.timing.overBudgetCalls[0], 0);
  const maxMs = results.reduce((m, r) => Math.max(m, r.timing.maxDecideMs[0]), 0);
  logInvocation({
    cmd: 'play', argv, contract: CONTRACT_VERSION, engine: engineDigest(),
    bots: [botInfo(a), botInfo(b)], seeds: seedInfo(source, seeds),
    matches: results.length, points: stats.points, winRate: stats.winRate,
  });
  if (flags.has('json')) {
    process.stdout.write(JSON.stringify({ ...provenance(source, seeds, [a, b]), stats, counters, results: results.map(stableMatch) }, null, 2) + '\n');
    return;
  }
  for (const r of results) {
    const [ra, rb] = r.runs;
    const verdict = r.points === 1 ? 'WIN ' : r.points === 0 ? 'LOSS' : 'DRAW';
    const inv = r.invalidSnapshot.some((x) => x) ? `  invalid-snapshot=${r.invalidSnapshot.join('/')}` : '';
    process.stdout.write(
      `seed ${String(r.seed).padStart(10)}  ${verdict}  day ${String(r.days).padStart(2)}  ` +
      `${a.name} ${ra.status}/${ra.trophies}T/${ra.lives}L  vs  ${b.name} ${rb.status}/${rb.trophies}T/${rb.lives}L${inv}\n`);
  }
  process.stdout.write(
    `\n${a.name} vs ${b.name}: ${stats.wins}W ${stats.draws}D ${stats.losses}L over ${stats.matches} seeds, ` +
    `win rate ${stats.winRate} (draw = 0.5)\n` +
    `${a.name}: ${JSON.stringify(counters[0])}\n` +
    `${a.name}: slowest decide ${maxMs.toFixed(1)} ms, over budget ${over}, module load ${a.loadMs.toFixed(0)} ms` +
    `${a.loadMs > LOAD_BUDGET_MS ? ' (OVER BUDGET)' : ''}\n`);
}

async function cmdMatrix(flags: Map<string, string>, argv: string[]): Promise<void> {
  const specs = (flags.get('bots') ?? BASELINES.map((b) => `baseline:${b}`).join(',')).split(',');
  const { source, seeds } = parseSeeds(flags.get('seeds') ?? 'visible');
  harden();
  const bots: LoadedBot[] = [];
  for (const s of specs) bots.push(await loadBot(s));
  const names = bots.map((b) => b.name);
  if (new Set(names).size !== names.length) die(`bot names must be distinct: ${names.join(', ')}`);

  const pairs: ReturnType<typeof pairStats>[] = [];
  const counters: Record<string, BotCounters> = {};
  for (const n of names) {
    counters[n] = { calls: 0, actions: 0, illegal: 0, malformed: 0, decideErrors: 0, dropped: 0, impure: 0 };
  }
  const winRate: Record<string, Record<string, number>> = {};
  for (const n of names) winRate[n] = {};

  // Both seat assignments for every pair, including self-play.
  for (let i = 0; i < bots.length; i++) {
    for (let j = i; j < bots.length; j++) {
      const results = playPair(seeds, bots[i], bots[j]);
      const st = pairStats(names[i], names[j], results);
      pairs.push(st);
      winRate[names[i]][names[j]] = st.winRate;
      winRate[names[j]][names[i]] = round4(1 - st.winRate);
      for (const r of results) {
        for (const side of [0, 1] as const) {
          const n = names[side === 0 ? i : j];
          sumCounters(counters[n], r.counters[side]);
        }
      }
    }
  }
  const out = {
    ...provenance(source, seeds, bots),
    winRate,
    pairs,
    counters,
  };
  const text = JSON.stringify(out, null, 2) + '\n';
  logInvocation({
    cmd: 'matrix', argv, contract: CONTRACT_VERSION, engine: out.engine.sha256,
    bots: out.bots, seeds: out.seeds, matches: pairs.reduce((s, p) => s + p.matches, 0), winRate,
  });
  const dest = flags.get('out');
  if (dest) writeFileSync(dest, text);
  else process.stdout.write(text);
}

/** match.ts owns the shop stream instead of calling the engine's
 *  startDay(); this checks the two agree on runs that never reroll, which
 *  is the only case where the engine's version is usable at all. */
async function cmdSelftest(): Promise<void> {
  harden();
  const cheapest = await loadBot('baseline:cheapest');
  const empty: E.Snapshot = {
    playerId: 'selftest', day: 1, level: 1, gold: 0, hp: 1000, maxHp: 1000,
    entries: [], slots: E.createTower().slots,
  };
  let days = 0;
  for (let seed = 1; seed <= 20; seed++) {
    const mine = E.createRun('x', seed);
    const theirs = E.createRun('x', seed);
    const stream = E.shopRng(seed);
    const unused = E.shopRng(0);
    while (mine.status === 'active' && mine.day <= 30) {
      startDay(mine, stream);
      E.startDay(theirs);
      for (let call = 0; call < 32; call++) {
        const view = structuredClone(mine);
        const [a] = cheapest.decide({
          contract: CONTRACT_VERSION, run: view, lastCombat: null, botSeed: 0, call,
          callsLeft: 32 - call, actionsLeft: 64,
        });
        if (a.type === 'end') break;
        applyAction(mine, a, stream);
        applyAction(theirs, a, unused);
      }
      E.resolveCombat(mine, empty);
      E.resolveCombat(theirs, empty);
      if (JSON.stringify(mine) !== JSON.stringify(theirs)) {
        die(`selftest FAILED: seed ${seed} day ${mine.day}: simulator day start diverges from the engine's`);
      }
      E.endDay(mine);
      E.endDay(theirs);
      days++;
    }
  }
  process.stdout.write(`selftest ok: simulator day start matches engine startDay() over ${days} days, seeds 1-20\n`);
}

const argv = process.argv.slice(2);
const { cmd, flags } = parseArgs(argv);
try {
switch (cmd) {
  case 'play': await cmdPlay(flags, argv); break;
  case 'matrix': await cmdMatrix(flags, argv); break;
  case 'selftest': await cmdSelftest(); break;
  default:
    die('usage: t5-sim play --bot <bot> --vs <bot> [--seeds <seeds>] [--json]\n' +
        '       t5-sim matrix [--bots <bot>,...] [--seeds <seeds>] [--out <file>]\n' +
        '       t5-sim selftest');
}

} catch (err) { die(err instanceof Error ? err.message : "evaluation failed"); }
