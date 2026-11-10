// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// selectTestsRatchet.test.ts - the ratchet behind `run-tests.mjs --changed`
// (#1258): a test file that reads from disk is invisible to the import graph,
// so it must always run, until it declares what it reads.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/__tests__/selectTestsRatchet.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT FAILS HERE, AND WHAT TO DO.
//
//   "undeclared disk readers": a test file you added (or changed) reads a file
//   by an fs call or by a path built from import.meta.url, __dirname or
//   process.cwd(). Add a line to it naming what it reads, with repo-root-relative
//   paths:
//
//       // disk-reads: ui/src/components/Foo.tsx, server/tests/fixtures/
//
//   (a trailing "/" means every file under that directory; a test that walks a
//   tree names the tree). The test then runs when one of those paths changes,
//   and not on every change.
//
//   "declared paths that do not exist": a `disk-reads:` line names a path under
//   ui/ that is not in the tree; fix the path, because a declaration that names
//   nothing keeps the test from running when the thing it really reads changes.
//   The paths outside ui/ are checked by tools/ci/test_changed_areas.py, which
//   also derives the `ui` area from them: this file must not stat them, or it
//   would itself read every one and run for every server change.
//
// THE DECLARATION IS A PROMISE, AND THIS FILE ONLY CHECKS ITS FORM. Whether a
// test opens a path it did not declare is known only when it runs, so the check
// is a command, taken after any change to a test's reads:
//
//       node run-tests.mjs --record-reads reads.json
//
// (from ui/; about as long as `npm test`). It runs every file with
// tools/recordReads.mjs preloaded, writes reads.json (per test file: what it
// read outside its imports, what it declares, what it read without declaring)
// and exits 1 naming each test that read an undeclared path. Give it file names
// to check some of the suite. src/__tests__/w8_9RecordReads.test.ts grades the
// recorder itself and runs one real test file under it. A read under ui/ but
// outside ui/src needs no declaration: a change there already runs everything.
//
// Every disk reader declares: the 144 that did not, at WP 7.1, were given their
// declarations at WP 8.9 from the first full run of that command, and the same
// command run again afterwards reported none outside them. tools/ci/areas.json
// derives from the declarations (tools/ci/test_changed_areas.py checks it): the
// `ui` area is ui/** plus the paths outside ui/ named here.
//
// Whether the rule finds a reader at all, and what a declaration does, is
// graded on a made-up tree in selectTests.test.ts; this file grades the real one.
//
// disk-reads: ui/src/, ui/tools/selectTests.mjs

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

interface Graph {
  tests: string[];
  declared: Map<string, string[]>;
}
interface SelectModule {
  buildGraph(root: string): Graph;
  undeclaredReaders(graph: Graph): string[];
  missingDeclaredPaths(graph: Graph, root: string): string[];
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const { buildGraph, undeclaredReaders, missingDeclaredPaths } =
  (await nodeImport(new URL("../../tools/selectTests.mjs", import.meta.url).href)) as SelectModule;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const REPO_ROOT = dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url))))).split("\\").join("/");
const graph = buildGraph(REPO_ROOT);
const readers = undeclaredReaders(graph);

test("the tree has the test files the ratchet expects to see", () => {
  assert(graph.tests.length > 700, `only ${graph.tests.length} test files found: the walk is broken`);
});

test("no test file reads from disk without declaring it", () => {
  assert(readers.length === 0,
    `undeclared disk readers: ${readers.join(", ")}. `
    + "Add `// disk-reads: <repo-root-relative paths>` to each (see the top of this file).");
});

test("every path a test declares under ui/ exists", () => {
  const insideUi = new Map([...graph.declared].map(([t, paths]) => [t, paths.filter((p) => p.startsWith("ui/"))]));
  const missing = missingDeclaredPaths({ ...graph, declared: insideUi }, REPO_ROOT);
  assert(missing.length === 0, `declared paths that do not exist: ${missing.join("; ")}`);
});

test("at least three test files declare their reads", () => {
  assert(graph.declared.size >= 3, `only ${graph.declared.size} declare their reads`);
});

console.log(`selectTestsRatchet.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
