/** Visible head-to-head scoring. The sealed snapshot-pool score is a separate
 * protocol (#40); these totals must never be substituted for it. */
import { playMatch, type Bot, type MatchResult } from './match.js';

export function stableMatch({ timing: _, ...result }: MatchResult) { return result; }

export function requireValid(results: MatchResult[]): void {
  for (const r of results) {
    if (r.counters.some((c) => c.impure > 0)) throw new Error('impure bot: evaluation has no score');
    if (r.timing.overBudgetCalls.some((n) => n > 0)) throw new Error('decide budget exceeded: evaluation has no score');
  }
}

/** Each seed gets both seat assignments. Return every row from a's point
 * of view, including the counters, so aggregates cannot mix bot identities. */
export function playPair(seeds: number[], a: Bot, b: Bot): MatchResult[] {
  const results: MatchResult[] = [];
  for (const seed of seeds) {
    results.push(playMatch(seed, [a, b], { checkPurity: true }));
    const r = playMatch(seed, [b, a], { checkPurity: true });
    results.push({
      ...r, points: 1 - r.points,
      invalidSnapshot: [r.invalidSnapshot[1], r.invalidSnapshot[0]],
      runs: [r.runs[1], r.runs[0]], counters: [r.counters[1], r.counters[0]],
      timing: {
        maxDecideMs: [r.timing.maxDecideMs[1], r.timing.maxDecideMs[0]],
        overBudgetCalls: [r.timing.overBudgetCalls[1], r.timing.overBudgetCalls[0]],
      },
    });
  }
  requireValid(results);
  return results;
}

export const round4 = (x: number) => Number(x.toFixed(4));

export function pairStats(a: string, b: string, results: MatchResult[]) {
  const n = results.length;
  if (!n) throw new Error('at least one seed is required');
  const count = (f: (r: MatchResult) => boolean) => results.filter(f).length;
  const points = results.reduce((s, r) => s + r.points, 0);
  const mean = (f: (r: MatchResult) => number) => round4(results.reduce((s, r) => s + f(r), 0) / n);
  return {
    a, b, matches: n,
    wins: count((r) => r.points === 1), draws: count((r) => r.points === 0.5),
    losses: count((r) => r.points === 0), points, winRate: round4(points / n),
    meanDays: mean((r) => r.days),
    meanTrophies: [mean((r) => r.runs[0].trophies), mean((r) => r.runs[1].trophies)],
    runsWon: [count((r) => r.runs[0].status === 'won'), count((r) => r.runs[1].status === 'won')],
    invalidSnapshots: [count((r) => r.invalidSnapshot[0] !== null), count((r) => r.invalidSnapshot[1] !== null)],
  };
}
