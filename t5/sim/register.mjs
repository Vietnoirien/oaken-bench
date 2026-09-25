// Module resolution for the T5 simulator (t5/SPEC.md section 7).
//
// Loaded with `node --import <this file>`. Two jobs, both in the resolve hook:
//
// 1. Bare specifiers `oaken-engine` and `oaken-t5`. A bot imports the engine
//    and the simulator helpers by these names, never by a relative path, so
//    the same bot file runs unchanged in this repo, in a scratch staging dir
//    and in the T5 container, whose layouts differ. It also guarantees the
//    simulator and every bot share ONE engine module instance: a second copy
//    loaded through a different relative path would have its own (unfrozen)
//    item table, and the hardening in harden.ts would only cover one of them.
//
// 2. `./x.js` -> `./x.ts`. The engine (like seed/src) writes its relative
//    imports with a `.js` suffix for tsc's Bundler resolution. Node's own
//    TypeScript support runs the .ts file but does not rewrite the suffix, so
//    without this every engine import fails with ERR_MODULE_NOT_FOUND. The
//    fallback only fires when the .js file does not exist, so a real .js
//    file always wins.
//
// No npm dependency (tsx, vite-node) on purpose: runs execute with the network
// off, and node 24 is the one runtime both the host and oaken-bench:1.0 have.
import { registerHooks } from 'node:module';
import { existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));

function engineIndex() {
  const root = process.env.OAKEN_T5_ENGINE;
  if (!root) {
    // Fail with the fix in the message: the default would otherwise be some
    // guessed path, and a guess that happens to exist (seed/'s stubs) would
    // load an engine whose every function throws 'not implemented'.
    throw new Error(
      'OAKEN_T5_ENGINE is not set: point it at an engine root holding ' +
      'src/index.ts and data/items.json (see t5/SPEC.md section 7)');
  }
  return pathToFileURL(path.resolve(root, 'src', 'index.ts')).href;
}

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === 'oaken-engine') {
      return { url: engineIndex(), shortCircuit: true };
    }
    if (specifier === 'oaken-t5') {
      return { url: pathToFileURL(path.join(here, 'lib.ts')).href, shortCircuit: true };
    }
    if ((specifier.startsWith('./') || specifier.startsWith('../')) &&
        specifier.endsWith('.js') && context.parentURL?.startsWith('file:')) {
      const asJs = new URL(specifier, context.parentURL);
      if (!existsSync(fileURLToPath(asJs))) {
        const asTs = new URL(specifier.slice(0, -3) + '.ts', context.parentURL);
        if (existsSync(fileURLToPath(asTs))) {
          return { url: asTs.href, shortCircuit: true };
        }
      }
    }
    return nextResolve(specifier, context);
  },
});
