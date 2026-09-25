// Only DecideState values cross this process boundary. The oracle's sealed
// pool, seed list, result files and repository root are not mounted here.
import { readSync, writeSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { harden } from '../sim/harden.ts';

const send = (value) => writeSync(1, JSON.stringify(value) + '\n');
try {
  harden();
  const module = await import(pathToFileURL(process.argv[2]).href);
  if (typeof module.decide !== 'function') throw new Error('bot must export decide');
  send({ ready: true });
  let buffer = '';
  const bytes = Buffer.alloc(65536);
  while (true) {
    const count = readSync(0, bytes, 0, bytes.length, null);
    if (count === 0) break;
    buffer += bytes.toString('utf8', 0, count);
    if (buffer.length > 4 * 1024 * 1024) throw new Error('decision input too large');
    for (let cut; (cut = buffer.indexOf('\n')) !== -1;) {
      const line = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 1);
      try {
        const result = module.decide(JSON.parse(line));
        let encoded;
        try { encoded = JSON.stringify(result); }
        catch { send({ unserializable: true }); continue; }
        send({ encoded: encoded ?? null });
      } catch {
        send({ error: true });
      }
    }
  }
} catch {
  send({ ready: false });
  process.exitCode = 2;
}
