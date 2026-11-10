// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The run-time read map of the UI suite (#1268).
//
//   node run-tests.mjs --record-reads <out.json> [<file-or-filter> ...]
//
// runs the test files with this module preloaded in every child (`--import`),
// which wraps node:fs and notes each path the test opens, then reports, per
// test file, the files it read outside its own import closure and which of
// them its `// disk-reads:` line (see selectTests.mjs) does not cover. A run
// with any uncovered read exits 1 naming it: a declaration the selector trusts
// is only as good as the reads it names, and this is what keeps it honest. A
// full run takes about as long as `npm test`, so it is a documented command and
// not part of the suite; the checks it applies are graded in
// src/__tests__/w8_9RecordReads.test.ts.
//
// WHY A PRELOAD AND WHY IT SEES HELPERS. The runner already gives each child
// `--import` hooks (the CSS stub). This module is one more of them, loaded
// first. It replaces the functions on the node:fs object and then calls
// syncBuiltinESMExports, so a later `import { readFileSync } from "node:fs"`
// in any module of the child, the test file or a helper it imports, binds the
// wrapper. The wrapper is process wide: it does not care which module calls.
// What it does skip is the module loader's own reads (tsx looking for a
// package.json or a file extension): see startedByLoader.
//
// Off by default: the preload does nothing unless both environment variables
// below are set, and the runner sets them only under --record-reads.
//
// The tool uses the Node standard library only.

import fs from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import { createHash } from "node:crypto";
import { isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const RECORD_DIR_ENV = "ASTRODECK_RECORD_READS_DIR";
export const RECORD_TEST_ENV = "ASTRODECK_RECORD_READS_TEST";

const WRAPPED = [
  "readFileSync", "readFile", "readdirSync", "readdir", "statSync", "stat", "lstatSync", "lstat",
  "existsSync", "openSync", "open", "opendirSync", "opendir", "accessSync", "access",
  "realpathSync", "createReadStream",
];
const WRAPPED_PROMISES = ["readFile", "readdir", "stat", "lstat", "access", "open", "opendir"];

const toPosix = (p) => p.split("\\").join("/");

/** The absolute path a call's first argument names, or null for a file
 *  descriptor or anything that is not a path. */
function pathOf(arg) {
  if (typeof arg === "string") return arg.startsWith("file:") ? fileURLToPath(arg) : resolve(arg);
  if (arg instanceof URL) return fileURLToPath(arg);
  if (Buffer.isBuffer(arg)) return resolve(arg.toString());
  return null;
}

/** Whether the call being made was started by the module loader (tsx probing
 *  for a package.json or a file extension, reading a module to transpile it)
 *  and not by test code: the first stack frame that is not Node's own is in
 *  node_modules, or there is none. Those reads are imports, which the import
 *  graph already sees. */
function startedByLoader() {
  const frames = (new Error().stack ?? "").split("\n").slice(1);
  for (const frame of frames) {
    if (frame.includes("recordReads.mjs") || frame.includes("node:")) continue;
    return frame.includes("node_modules");
  }
  return true;
}

function wrap(owner, name, note) {
  const original = owner[name];
  if (typeof original !== "function") return;
  const wrapper = function (...args) {
    try {
      const path = pathOf(args[0]);
      if (path !== null && !startedByLoader()) note(path);
    } catch { /* a path that cannot be read as one is not a read to record */ }
    return original.apply(this, args);
  };
  owner[name] = Object.assign(wrapper, original);
}

/** Wrap node:fs so every path opened is passed to `note`. */
export function installRecorder(note) {
  for (const name of WRAPPED) wrap(fs, name, note);
  for (const name of WRAPPED_PROMISES) wrap(fs.promises, name, note);
  syncBuiltinESMExports();
}

/** Where one test's reads are written: a name that cannot collide with another
 *  test's (the hash covers the whole path) and still says whose it is. */
export function recordFilePath(dir, testFile) {
  const base = toPosix(testFile).split("/").pop();
  const hash = createHash("sha1").update(toPosix(testFile)).digest("hex").slice(0, 10);
  return `${toPosix(dir)}/${base}-${hash}.json`;
}

/** The files `test` imports, directly or not, and the test itself. `forward`
 *  (file -> what it imports) may be passed in when many tests are asked about. */
export function importClosure(graph, test, forward = forwardEdges(graph)) {
  const seen = new Set([test]);
  const stack = [test];
  while (stack.length > 0) {
    for (const next of forward.get(stack.pop()) ?? []) {
      if (!seen.has(next)) {
        seen.add(next);
        stack.push(next);
      }
    }
  }
  return seen;
}

function forwardEdges(graph) {
  const forward = new Map();
  for (const [file, importers] of graph.reverse) {
    for (const importer of importers) {
      if (!forward.has(importer)) forward.set(importer, new Set());
      forward.get(importer).add(file);
    }
  }
  return forward;
}

/** The repo-root-relative path of a recorded read, or null when it is outside
 *  the repository (a temporary directory) or under node_modules. */
function repoRelative(entry, repoRoot) {
  const abs = entry.startsWith("file:") ? fileURLToPath(entry) : entry;
  const rel = toPosix(relative(repoRoot, abs));
  if (rel === "" || rel.startsWith("..") || isAbsolute(rel)) return null;
  return rel.split("/").includes("node_modules") ? null : rel;
}

/** Everything a test opened in the repository except the test file itself, as
 *  sorted repo-root-relative paths: what a declaration may truthfully name. */
export function repoReads(raw, { repoRoot, test }) {
  const kept = new Set();
  for (const entry of raw) {
    const rel = repoRelative(entry, repoRoot);
    if (rel !== null && rel !== test) kept.add(rel);
  }
  return [...kept].sort();
}

/** The reads that need a declaration, as sorted repo-root-relative paths, out of
 *  the raw absolute paths a child recorded. Dropped: what is outside the
 *  repository (a temporary directory), node_modules, the test's own import
 *  closure (a change there selects the test by import), and what is under ui/
 *  but not under ui/src (the package files, the tsconfigs, the runner and its
 *  tools: a change there already runs the whole suite, see failOpen). */
export function readsOutsideImports(raw, { repoRoot, closure }) {
  const kept = new Set();
  for (const entry of raw) {
    const rel = repoRelative(entry, repoRoot);
    if (rel === null || closure.has(rel)) continue;
    // Compared without case: on Windows the loader also touches the repository's
    // own directories spelled in whatever case it resolved them in.
    const lower = rel.toLowerCase();
    if ((lower === "ui" || lower.startsWith("ui/")) && !lower.startsWith("ui/src/")) continue;
    kept.add(rel);
  }
  return [...kept].sort();
}

const covers = (declared, read) =>
  declared.some((d) => (d.endsWith("/") ? `${read}/`.startsWith(d) : read === d));

/** The reads no declaration covers. A path is covered by naming it, or by a
 *  directory with a trailing "/" that holds it (or is it). */
export function undeclaredReads(reads, declared) {
  return reads.filter((r) => !covers(declared, r));
}

/** For each test file that has a record in `dir`: the reads that need a
 *  declaration, every read in the repository (`inRepo`, for writing one), what
 *  the test declares, and the reads it does not cover. */
export function readMap(dir, graph, repoRoot) {
  const forward = forwardEdges(graph);
  const map = {};
  for (const test of graph.tests) {
    const file = recordFilePath(dir, test);
    if (!fs.existsSync(file)) continue;
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    const reads = readsOutsideImports(raw, { repoRoot, closure: importClosure(graph, test, forward) });
    const declared = graph.declared.get(test) ?? [];
    map[test] = {
      reads, inRepo: repoReads(raw, { repoRoot, test }), declared,
      undeclared: undeclaredReads(reads, declared),
    };
  }
  return map;
}

function recordFromEnvironment() {
  const dir = process.env[RECORD_DIR_ENV];
  const test = process.env[RECORD_TEST_ENV];
  if (!dir || !test) return;
  const seen = new Set();
  const { writeFileSync, mkdirSync } = fs;
  installRecorder((path) => seen.add(path));
  process.on("exit", () => {
    mkdirSync(dir, { recursive: true });
    writeFileSync(recordFilePath(dir, test), JSON.stringify([...seen].sort()), "utf8");
  });
}

recordFromEnvironment();
