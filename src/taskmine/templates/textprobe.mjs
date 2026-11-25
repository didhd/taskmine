// taskmine text probe: a Node preload used only during validation (never shipped in a task).
//
// Question it answers, per test: does this fail-to-pass test pass because of what the changed code
// DOES, or because of how the changed source TEXT is spelled? It runs the gold state with one
// substitution: when TEST CODE reads (as a file) a source file the change modified, it gets the
// base commit's version of that file. Module loading is untouched, so imported code is still the
// gold code. A behavioural test still passes; a test that greps the source for the fix fails.
//
// "Test code" means a call whose stack has a frame in a test-side file (TASKMINE_TEST_GLOBS),
// outside node_modules. Loaded with NODE_OPTIONS="--import <this file>", so it also reaches the
// per-file child processes `node --test` starts.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { syncBuiltinESMExports } from 'node:module';

const real = (p) => { try { return fs.realpathSync(p); } catch { return p; } };
// Real paths on both sides: stack frames name real paths (macOS /var is /private/var).
const APP = real(path.resolve(process.env.TASKMINE_APP_DIR || '/app'));
const LOG = process.env.TASKMINE_PROBE_LOG || '';
const toRegex = (g) => {
  let out = '';
  for (let i = 0; i < g.length; ) {
    if (g.startsWith('**/', i)) { out += '(?:.*/)?'; i += 3; }
    else if (g.startsWith('/**', i) && i + 3 === g.length) { out += '(?:/.*)?'; i += 3; }
    else if (g.startsWith('**', i)) { out += '.*'; i += 2; }
    else if (g[i] === '*') { out += '[^/]*'; i += 1; }
    else if (g[i] === '?') { out += '[^/]'; i += 1; }
    else { out += g[i].replace(/[.+^${}()|[\]\\]/g, '\\$&'); i += 1; }
  }
  return new RegExp('^' + out + '$');
};
const testGlobs = JSON.parse(process.env.TASKMINE_TEST_GLOBS || '[]').map(toRegex);
const isTestSide = (rel) => testGlobs.some((r) => r.test(rel));

const orig = {
  readFileSync: fs.readFileSync,
  readFile: fs.readFile,
  promisesReadFile: fs.promises.readFile,
  appendFileSync: fs.appendFileSync,
};

let changed = new Set();
try {
  const out = execFileSync('git', ['-C', APP, 'diff', '--name-only', '--no-renames', 'taskmine-base'], { encoding: 'utf8' });
  changed = new Set(out.split('\n').filter((p) => p && !isTestSide(p)));
} catch (e) {
  // Fail closed: without the changed files the probe would substitute nothing, and a source-text
  // test would pass as if it tested behaviour. The validator rejects the draft on this line.
  changed = new Set();
  if (LOG) try { fs.appendFileSync(LOG, '!error ' + String(e.message || e).split('\n')[0] + '\n'); } catch {}
}

const relOf = (p) => {
  if (typeof p === 'number') return null;
  let s = p;
  if (p instanceof URL) s = fileURLToPath(p);
  else if (Buffer.isBuffer(p)) s = p.toString();
  if (typeof s !== 'string') return null;
  if (s.startsWith('file://')) s = fileURLToPath(s);
  const abs = real(path.resolve(s));
  return abs.startsWith(APP + path.sep) ? abs.slice(APP.length + 1) : null;
};

const FRAME = /(?:file:\/\/)?(\/[^\s():]+):\d+:\d+/g;
const calledFromTest = () => {
  const limit = Error.stackTraceLimit;
  Error.stackTraceLimit = 60;
  const stack = new Error().stack || '';
  Error.stackTraceLimit = limit;
  for (const m of stack.matchAll(FRAME)) {
    const file = real(m[1]);
    if (!file.startsWith(APP + '/') || file.includes('/node_modules/')) continue;
    if (isTestSide(file.slice(APP.length + 1))) return true;
  }
  return false;
};

const baseContent = (rel) => {
  try {
    return execFileSync('git', ['-C', APP, 'show', `taskmine-base:${rel}`], { maxBuffer: 1 << 28 });
  } catch {
    const e = new Error(`ENOENT: no such file or directory, open '${path.join(APP, rel)}'`);
    e.code = 'ENOENT';
    throw e;
  }
};

const encodingOf = (opts) => (typeof opts === 'string' ? opts : opts && opts.encoding) || null;
const note = (rel) => {
  if (LOG) try { orig.appendFileSync.call(fs, LOG, rel + '\n'); } catch {}
};
const substitute = (p) => {
  const rel = relOf(p);
  if (!rel || !changed.has(rel) || !calledFromTest()) return null;
  note(rel);
  return baseContent(rel);
};

fs.readFileSync = function readFileSync(p, opts) {
  const sub = substitute(p);
  if (sub === null) return orig.readFileSync.apply(this, arguments);
  const enc = encodingOf(opts);
  return enc ? sub.toString(enc) : sub;
};

fs.readFile = function readFile(p, opts, cb) {
  const callback = typeof opts === 'function' ? opts : cb;
  let sub;
  try { sub = substitute(p); } catch (e) { process.nextTick(callback, e); return; }
  if (sub === null) return orig.readFile.apply(this, arguments);
  const enc = encodingOf(typeof opts === 'function' ? null : opts);
  process.nextTick(callback, null, enc ? sub.toString(enc) : sub);
};

fs.promises.readFile = async function readFile(p, opts) {
  const sub = substitute(p);
  if (sub === null) return orig.promisesReadFile.apply(this, arguments);
  const enc = encodingOf(opts);
  return enc ? sub.toString(enc) : sub;
};

syncBuiltinESMExports();
