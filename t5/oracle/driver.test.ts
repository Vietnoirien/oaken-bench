/** Runs under the frozen t5-sim launcher so the engine is always its checked,
 * sealed reference bundle. This file is an oracle worker, not a public test. */
import test from 'node:test';
import { spawn } from 'node:child_process';
import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { lstatSync, readFileSync, readSync, readdirSync, realpathSync, unlinkSync, writeFileSync, writeSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import * as E from 'oaken-engine';
import type { Snapshot } from 'oaken-engine';
import { actionPhase, playMatch, startDay, type Bot } from '../sim/match.js';
import { CONTRACT_VERSION, DECIDE_BUDGET_MS, LOAD_BUDGET_MS, MAX_DAYS,
  type BotCounters, type DecideState, type LastCombat } from '../sim/contract.js';
import { harden, monotonicMs } from '../sim/harden.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const baselines = ['random', 'cheapest', 'merger'] as const;
const sha = (data: string | Buffer) => createHash('sha256').update(data).digest('hex');
const round4 = (n: number) => Number(n.toFixed(4));
const blank = (): BotCounters => ({ calls: 0, actions: 0, illegal: 0, malformed: 0, decideErrors: 0, dropped: 0, impure: 0 });

function treeDigest(directory: string): string {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir).sort()) {
      if (name.startsWith('.') || ['node_modules', 'hidden', 'refengine', 't5oracle'].includes(name)) continue;
      const file = path.join(dir, name);
      const stat = lstatSync(file);
      if (stat.isSymbolicLink()) continue;
      if (stat.isDirectory()) walk(file);
      else if (/\.(ts|mts|js|mjs|json)$/.test(name)) files.push(file);
    }
  };
  walk(directory);
  return sha(files.map((file) => `${path.relative(directory, file)}\0${sha(readFileSync(file))}\n`).join(''));
}

function botFile(spec: string): string {
  const file = spec.startsWith('baseline:')
    ? path.join(root, 't5/bots', `${spec.slice(9)}.ts`) : path.resolve(spec);
  if (spec.startsWith('baseline:') && !baselines.includes(spec.slice(9) as typeof baselines[number]))
    throw new Error('unknown baseline');
  return realpathSync(file);
}

function describe(spec: string) {
  const file = botFile(spec);
  const source = readFileSync(file);
  return { name: spec.startsWith('baseline:') ? spec : path.basename(file), spec,
    digest: sha(source), treeDigest: treeDigest(path.dirname(file)), file };
}

async function load(spec: string): Promise<Bot & { digest: string; treeDigest: string; spec: string }> {
  const info = describe(spec);
  const before = monotonicMs();
  const mod = await import(pathToFileURL(info.file).href);
  if (monotonicMs() - before > LOAD_BUDGET_MS) throw new Error('module import budget exceeded: no score');
  if (typeof mod.decide !== 'function') throw new Error('bot must export decide');
  return { ...info, decide: mod.decide };
}

function sandboxedBot(spec: string) {
  const info = describe(spec);
  const botDir = path.dirname(info.file);
  const relative = path.relative(botDir, root);
  if (relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative)))
    throw new Error('bot directory would expose the sealed archive; put the strategy in its own directory');
  const engine = realpathSync(process.env.OAKEN_T5_ENGINE!);
  const sim = path.join(root, 't5/sim');
  const worker = path.join(root, 't5/oracle/bot_worker.mjs');
  const bwrap = process.env.OAKEN_T5_ORACLE_BWRAP!;
  const prlimit = process.env.OAKEN_T5_ORACLE_PRLIMIT!;
  if (!bwrap || !prlimit) throw new Error('Bubblewrap and prlimit are required for held-out scoring');
  const args = ['--unshare-all', '--die-with-parent', '--new-session',
    '--ro-bind', '/usr', '/usr', '--ro-bind', '/bin', '/bin',
    '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64',
    '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp',
    '--ro-bind', process.execPath, process.execPath,
    '--ro-bind', engine, engine, '--ro-bind', sim, sim,
    '--ro-bind', botDir, botDir, '--ro-bind', worker, worker,
    '--clearenv', '--setenv', 'OAKEN_T5_ENGINE', engine,
    '--setenv', 'HOME', '/tmp', '--setenv', 'PATH', '/usr/bin:/bin',
    '--', process.execPath, '--max-old-space-size=512', '--experimental-strip-types',
    '--import', path.join(sim, 'register.mjs'), worker, info.file];
  const child = spawn(prlimit, ['--as=34359738368', '--cpu=120', '--nofile=128',
    '--', bwrap, ...args], { stdio: ['pipe', 'pipe', 'ignore'] });
  let output = '';
  let fatal = false;
  const sleep = new Int32Array(new SharedArrayBuffer(4));
  const pause = () => Atomics.wait(sleep, 0, 0, 1);
  const retryable = (error: unknown) => (error as NodeJS.ErrnoException).code === 'EAGAIN';
  const inputFd = () => (child.stdin as any)._handle?.fd as number | undefined;
  const outputFd = () => (child.stdout as any)._handle?.fd as number | undefined;
  const line = (deadline: number): any => {
    const bytes = Buffer.alloc(65536);
    while (monotonicMs() < deadline) {
      const cut = output.indexOf('\n');
      if (cut >= 0) {
        const item = output.slice(0, cut);
        output = output.slice(cut + 1);
        try { return JSON.parse(item); } catch { fatal = true; throw new Error('invalid bot response'); }
      }
      const fd = outputFd();
      if (fd === undefined) { pause(); continue; }
      try {
        const count = readSync(fd, bytes, 0, bytes.length, null);
        if (count === 0) { fatal = true; throw new Error('bot process closed'); }
        output += bytes.toString('utf8', 0, count);
        if (output.length > 1024 * 1024) { fatal = true; throw new Error('bot response too large'); }
      } catch (error) { if (!retryable(error)) { fatal = true; throw error; } pause(); }
    }
    fatal = true;
    throw new Error('bot response exceeded decision budget');
  };
  const write = (value: string, deadline: number) => {
    const bytes = Buffer.from(value + '\n');
    let offset = 0;
    while (offset < bytes.length && monotonicMs() < deadline) {
      const fd = inputFd();
      if (fd === undefined) { pause(); continue; }
      try { offset += writeSync(fd, bytes, offset, bytes.length - offset); }
      catch (error) { if (!retryable(error)) { fatal = true; throw error; } pause(); }
    }
    if (offset < bytes.length) { fatal = true; throw new Error('bot input timeout'); }
  };
  const ready = line(monotonicMs() + LOAD_BUDGET_MS);
  if (ready?.ready !== true) { child.kill('SIGKILL'); throw new Error('sandboxed bot import failed'); }
  const bot: Bot = { name: info.name, decide(state: DecideState) {
    const deadline = monotonicMs() + DECIDE_BUDGET_MS;
    write(JSON.stringify(state), deadline);
    const reply = line(deadline);
    if (reply?.error) throw new Error('bot decide failed');
    if (reply?.unserializable) { const cycle: any[] = []; cycle.push(cycle); return cycle as any; }
    if (!reply || !Object.hasOwn(reply, 'encoded')) { fatal = true; throw new Error('invalid bot response'); }
    return reply.encoded === null ? undefined as any : JSON.parse(reply.encoded);
  } };
  return { ...info, bot, failed: () => fatal, close: () => child.kill('SIGKILL') };
}

function seeds(): number[] {
  const seen = new Set<number>();
  while (seen.size < 48) {
    const n = randomBytes(4).readUInt32LE(0);
    if (n > 100) seen.add(n);
  }
  return [...seen];
}

type Case = { seed: number; baseline: string; sourceDay: number; snapshot: Snapshot };

async function capture(): Promise<void> {
  harden();
  const bots = await Promise.all(baselines.map((name) => load(`baseline:${name}`)));
  const chosen = seeds();
  const cases: Case[] = [];
  for (const seed of chosen) for (const bot of bots) {
    const shots: Snapshot[] = [];
    playMatch(seed, [bot, bot], { checkPurity: true,
      onSnapshot: (snapshot, side) => { if (side === 0) shots.push(snapshot); },
    });
    if (!shots.length) throw new Error('baseline produced no valid snapshot');
    const snapshot = shots[Math.min(2, shots.length - 1)];
    cases.push({ seed, baseline: bot.name.slice(9), sourceDay: snapshot.day, snapshot });
  }
  const canary = randomUUID();
  const pool = { protocol: 't5-sealed-snapshot/1', contract: CONTRACT_VERSION,
    canary, seeds: chosen, cases,
    sources: {
      frozen: sha(readFileSync(path.join(root, 't5/FROZEN.sha256'))),
      refengine: readFileSync(path.join(root, 'refengine.sha256'), 'utf8').trim(),
      baselines: Object.fromEntries(bots.map((b) => [b.name.slice(9), b.digest])),
    },
  };
  writeFileSync(process.env.OAKEN_T5_ORACLE_OUTPUT!, JSON.stringify(pool) + '\n', { flag: 'wx', mode: 0o600 });
  writeFileSync(process.env.OAKEN_T5_ORACLE_META!, JSON.stringify({
    protocol: pool.protocol, seedsSha256: sha(JSON.stringify(chosen)),
    poolSha256: sha(JSON.stringify(cases)), canarySha256: sha(canary),
    seedCount: chosen.length, caseCount: cases.length, sources: pool.sources,
  }, null, 2) + '\n', { flag: 'wx' });
}

function checkPool(p: any): asserts p is { seeds: number[]; cases: Case[]; sources: Record<string, any> } {
  if (p.protocol !== 't5-sealed-snapshot/1' || p.contract !== CONTRACT_VERSION ||
      !Array.isArray(p.seeds) || p.seeds.length !== 48 || !Array.isArray(p.cases) ||
      p.cases.length !== p.seeds.length * baselines.length ||
      new Set(p.seeds).size !== p.seeds.length ||
      p.seeds.some((s: number) => !Number.isInteger(s) || s <= 100 || s > 0xffffffff) ||
      p.sources.frozen !== sha(readFileSync(path.join(root, 't5/FROZEN.sha256'))) ||
      p.sources.refengine !== readFileSync(path.join(root, 'refengine.sha256'), 'utf8').trim())
    throw new Error('sealed pool provenance mismatch');
  for (let i = 0; i < p.cases.length; i++) {
    const c = p.cases[i];
    if (c.seed !== p.seeds[Math.floor(i / 3)] || c.baseline !== baselines[i % 3] ||
        c.snapshot?.day !== c.sourceDay || !Array.isArray(c.snapshot?.entries))
      throw new Error('sealed pool shape mismatch');
  }
  for (const name of baselines) {
    if (p.sources.baselines[name] !== sha(readFileSync(path.join(root, 't5/bots', `${name}.ts`))))
      throw new Error('baseline source changed');
  }
}

function evaluate(seed: number, snapshot: Snapshot, bot: Bot, failed: () => boolean): { point: number; counters: BotCounters; days: number; invalid: boolean } {
  const run = E.createRun('self', seed);
  const stream = E.shopRng(seed);
  const counters = blank();
  const time = { max: 0, over: 0 };
  let last: LastCombat | null = null;
  let days = 0;
  for (let day = 1; day <= MAX_DAYS; day++) {
    days = day;
    startDay(run, stream);
    actionPhase(bot, run, stream, last, seed, 0, counters, time, { checkPurity: true });
    if (failed()) throw new Error('sandboxed bot process failed: no score');
    if (counters.impure || time.over) throw new Error('impure or slow bot: no score');
    try { new E.SnapshotStore().ingest(run); }
    catch (err) {
      if (err instanceof E.EngineError) return { point: 0, counters, days, invalid: true };
      throw err;
    }
    const opponent = structuredClone(snapshot);
    opponent.playerId = 'opponent';
    const result = E.resolveCombat(run, opponent);
    last = { opponent, result: result.result, hpLeft: result.hpA };
    if (run.status === 'won' || run.status === 'lost')
      return { point: run.status === 'won' ? 1 : 0, counters, days, invalid: false };
    E.endDay(run);
  }
  throw new Error('candidate exceeded day safety limit: no score');
}

function interval(clusters: number[]): [number, number] {
  // Fixed bootstrap stream makes rescoring byte-for-byte stable. Each draw
  // resamples whole seed clusters, preserving dependence across baselines.
  let state = 0x40a5f00d;
  const next = () => ((state = (Math.imul(state, 1664525) + 1013904223) >>> 0) / 4294967296);
  const means: number[] = [];
  for (let rep = 0; rep < 10000; rep++) {
    let sum = 0;
    for (let i = 0; i < clusters.length; i++) sum += clusters[Math.floor(next() * clusters.length)];
    means.push(sum / clusters.length);
  }
  means.sort((a, b) => a - b);
  return [round4(means[249]), round4(means[9749])];
}

async function score(): Promise<void> {
  const poolPath = process.env.OAKEN_T5_ORACLE_POOL!;
  const pool = JSON.parse(readFileSync(poolPath, 'utf8'));
  checkPool(pool);
  // The bot contract forbids filesystem reads. Remove the plaintext before
  // importing any bot so an accidental or opportunistic read cannot get it.
  unlinkSync(poolPath);
  delete process.env.OAKEN_T5_ORACLE_POOL;
  harden();
  const isolated = sandboxedBot(process.env.OAKEN_T5_ORACLE_BOT!);
  const { bot } = isolated;
  const points: number[] = [];
  let wins = 0, losses = 0, invalidSnapshots = 0, totalDays = 0;
  const counters = blank();
  try { for (const c of pool.cases) {
    const result = evaluate(c.seed, c.snapshot, bot, isolated.failed);
    points.push(result.point);
    wins += result.point;
    losses += 1 - result.point;
    invalidSnapshots += Number(result.invalid);
    totalDays += result.days;
    for (const k of Object.keys(counters) as (keyof BotCounters)[]) counters[k] += result.counters[k];
  } } finally { isolated.close(); }
  const clusters = pool.seeds.map((_: number, i: number) =>
    (points[3 * i] + points[3 * i + 1] + points[3 * i + 2]) / 3);
  const [lower, upper] = interval(clusters);
  const report = {
    schemaVersion: 1, tier: 't5', protocol: pool.protocol, contract: CONTRACT_VERSION,
    runtime: process.version,
    oracle: { archiveSha256: process.env.OAKEN_T5_ORACLE_ARCHIVE_SHA,
      seedsSha256: sha(JSON.stringify(pool.seeds)), poolSha256: sha(JSON.stringify(pool.cases)),
      frozenSha256: pool.sources.frozen, refengineSha256: pool.sources.refengine,
      scorerSha256: sha(['scripts/t5_oracle.py', 't5/oracle/bot_worker.mjs', 't5/oracle/driver.test.ts']
        .map((name) => `${name}\0${sha(readFileSync(path.join(root, name)))}\n`).join('')) },
    bot: { spec: isolated.spec.startsWith('baseline:') ? isolated.spec : path.basename(isolated.spec),
      sha256: isolated.digest, treeSha256: isolated.treeDigest },
    seedCount: pool.seeds.length, matches: points.length,
    wins, losses, winRate: round4(wins / points.length),
    confidenceInterval: { method: 'seed-cluster-percentile-bootstrap', level: 0.95,
      replicates: 10000, lower, upper },
    invalidSnapshots, meanDays: round4(totalDays / points.length), counters,
  };
  writeFileSync(process.env.OAKEN_T5_ORACLE_OUTPUT!, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
}

test('t5 oracle driver', async () => {
  if (process.env.OAKEN_T5_ORACLE_MODE === 'capture') await capture();
  else if (process.env.OAKEN_T5_ORACLE_MODE === 'score') await score();
  else throw new Error('oracle mode required');
});
