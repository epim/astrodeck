// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w7NoLocalRafPolyfill.test.ts - the one requestAnimationFrame/
// cancelAnimationFrame polyfill lives in testing/rafPolyfill.ts, and nowhere
// else (#652; WP-79).
//
//   Run directly:  npx tsx src/testing/__tests__/w7NoLocalRafPolyfill.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY. #652 found the shim `g.requestAnimationFrame = (cb) => setTimeout(()
// => cb(0), 0)` hand-copied into 27 UI test files, each one a possible
// intermittent "export never resolves" hang on a loaded CI runner (#614's
// suspected mechanism -- see rafPolyfill.ts's own header for the full story).
// This file walks every `*.test.ts`/`*.test.tsx` under ui/src and fails if any
// of them still assigns `requestAnimationFrame` an inline function literal of
// its own, rather than going through installAutoRaf/createManualRaf.
//
// WHAT COUNTS AS "ITS OWN": an assignment whose right-hand side is a function
// literal -- `requestAnimationFrame = (cb) => ...` or `= function(cb) {...}`,
// on either `=` (a property assignment) or `:` (an object-literal key). A file
// that WIRES the shared module in instead -- `win.requestAnimationFrame =
// rafQueue.request` (holdButtonDom, w5PolarEasedScaleFinite: their manual
// frame queues cannot be expressed as a zero-argument install() call, so they
// assign the queue's own `request` method) -- is not a copy: its right-hand
// side is a reference, not a function body, and the matcher below is built to
// tell the two apart (see its own known-positive/known-negative case).
//
// NAMED MUTANT "one copy put back" (flowLibraryDom.test.tsx: its
// `installAutoRaf(g);` line replaced with the retired inline copy,
// `g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() =>
// cb(0), 0);`, run from a byte backup and restored with its sha256 checked).
// Observed ("w7NoLocalRafPolyfill.test: 2/3 passed"):
//   x no test file under ui/src defines its own requestAnimationFrame: a test
//   file assigns requestAnimationFrame an inline copy instead of importing
//   testing/rafPolyfill.ts:
//   expected []
//   got      ["components/flows/__tests__/flowLibraryDom.test.tsx:50"]

const { readFileSync, readdirSync, statSync } = await import("node:fs");
const toPath = (u: URL): string => decodeURIComponent(u.pathname).replace(/^\/([A-Za-z]:)/, "$1");
const SRC = toPath(new URL("../../", import.meta.url)).replace(/[\\/]+$/, "");
// This file's own path, relative to SRC: its prose above quotes the retired
// shape verbatim, so it must be walked (the walk case below checks that) but
// never scanned (the matcher's own line would otherwise self-match -- see
// rafPolyfill.ts's module comment for why that particular wording does not,
// but this file names the shape in plain prose too, which could).
const SELF_REL = "testing/__tests__/w7NoLocalRafPolyfill.test.ts";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

// ==================================================================== walk
const isTestFile = (name: string): boolean => /\.test\.(ts|tsx)$/.test(name);

function walk(dir: string, rel: string, out: string[]): void {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules") continue;
    const abs = `${dir}/${name}`;
    const r = rel ? `${rel}/${name}` : name;
    if (statSync(abs).isDirectory()) walk(abs, r, out);
    else if (isTestFile(name)) out.push(r);
  }
}
const TEST_FILES: string[] = [];
walk(SRC, "", TEST_FILES);

// A function-literal assignment to requestAnimationFrame, on "=" or the
// object-literal ":" -- the exact shape every one of the 27 copies had. A
// reference assignment (`= rafQueue.request`, `= installedHandle...`) does
// not match: its right-hand side starts with an identifier, not "(" or
// "function".
const INLINE_RAF = /\brequestAnimationFrame\s*[=:]\s*(\(|function\b)/;

function scan(): Array<{ file: string; line: number }> {
  const hits: Array<{ file: string; line: number }> = [];
  for (const rel of TEST_FILES) {
    if (rel === SELF_REL) continue;
    const lines = readFileSync(`${SRC}/${rel}`, "utf8").split("\n");
    lines.forEach((line, i) => {
      if (INLINE_RAF.test(line)) hits.push({ file: rel, line: i + 1 });
    });
  }
  return hits;
}

test("the walk reaches ui/src's test files, so a clean scan is not reading nothing", () => {
  assert(TEST_FILES.length > 400,
    `the walk found ${TEST_FILES.length} test files under ${SRC} - it is not reading ui/src`);
  assert(TEST_FILES.includes("components/flows/__tests__/flowInspectorNotes.test.tsx"),
    "the walk missed a file known to use the shared polyfill");
  assert(TEST_FILES.includes(SELF_REL),
    "the walk never reached this file, so its own exclusion from the scan is untested");
});

test("the matcher tells a copy from a reference (known positives and negatives)", () => {
  // The exact lines the 27 retired copies carried, verbatim.
  assert(INLINE_RAF.test("g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);"),
    "the matcher does not see the shape it exists to catch");
  assert(INLINE_RAF.test("win.requestAnimationFrame = ((cb: FrameRequestCallback) => {"),
    "the matcher misses the double-paren function-expression shape (holdButtonDom's former copy)");
  assert(INLINE_RAF.test("  gl.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);"),
    "the matcher misses an indented copy inside a block (tonightTimeline's former shape)");
  assert(INLINE_RAF.test('requestAnimationFrame: (cb) => setTimeout(() => cb(0), 0),'),
    "the matcher misses the object-literal key form");
  // The lines that replaced them: a reference to the shared module, not a
  // function body. If these ever matched, every file this change touched
  // would fail its own guard.
  assert(!INLINE_RAF.test("g.requestAnimationFrame = rafQueue.request;"),
    "the matcher flags wiring the shared manual queue's own request method in -- that is not a copy");
  assert(!INLINE_RAF.test("win.requestAnimationFrame = rafQueue.request as any;"),
    "the matcher flags holdButtonDom's legitimate wiring line");
  assert(!INLINE_RAF.test("const { requestAnimationFrame } = window;"),
    "the matcher flags a destructuring read, not an assignment");
});

test("no test file under ui/src defines its own requestAnimationFrame", () => {
  const hits = scan();
  eq(hits.map((h) => `${h.file}:${h.line}`), [],
    "a test file assigns requestAnimationFrame an inline copy instead of importing testing/rafPolyfill.ts:");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w7NoLocalRafPolyfill.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
