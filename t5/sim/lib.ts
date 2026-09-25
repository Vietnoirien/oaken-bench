/** What a bot may import as `oaken-t5` (t5/SPEC.md section 2.3). */
export * from './contract.js';
export { legalActions, parseAction } from './actions.js';
export { playMatch, startDay, type Bot, type MatchOptions, type MatchResult } from './match.js';
