// runTestsCss.test.ts - guards the runner's own ".css" stub wiring (#50),
// on BOTH registration mechanisms test-css-stub.mjs can take (issue #61).
//
// The plain-Node runner (run-tests.mjs) spawns each test file in its own
// child process. Node's ESM loader has no handler for a raw ".css"
// specifier: it throws ERR_UNKNOWN_FILE_EXTENSION before a single test in
// the importing file runs, and the runner reports the whole file as
// "no pass/fail tally in its output - cannot be scored" rather than one
// failing assertion. That crash is not hypothetical: it hit five real DOM
// test files once horizon.tsx started importing PhotosphereDome, which
// imports its own sheets.css per wave R7's every-area-owns-its-stylesheet
// rule (see issue #50).
//
// test-css-stub.mjs has two branches: Node's synchronous `registerHooks()`
// (available on this machine, Node 24) and the older, asynchronous
// `module.register()` that CI's pinned Node 20 falls back to, because
// registerHooks does not exist there (issue #61 -- the first version of
// this fix used registerHooks unconditionally and crashed CI outright).
// Both branches are exercised here: once letting this Node choose its own
// path (registerHooks, since this machine has it), and once forcing the
// module.register branch via ASTRODECK_CSS_STUB_FORCE_ASYNC=1, so the path
// CI's Node 20 will actually take is proven green without needing a Node 20
// install on this machine (there isn't one here - see task-7-report.md,
// "Fix round 1").
//
//   Run directly:  node --import tsx src/__tests__/runTestsCss.test.ts
//
// MUTATION 1 (shared wiring / default path): remove
// `"--import", CSS_STUB_URL,` from run-tests.mjs's `runOne` args (or delete
// test-css-stub.mjs) -> BOTH runs below go red with
// ERR_UNKNOWN_FILE_EXTENSION, since neither branch is even loaded.
// MUTATION 2 (async path only): with ASTRODECK_CSS_STUB_FORCE_ASYNC=1 still
// set, remove the `register(HOOKS_URL, import.meta.url)` call from
// test-css-stub.mjs's `else` branch (leaving it a no-op) -> only the
// forced-async run below goes red with ERR_UNKNOWN_FILE_EXTENSION; the
// default (registerHooks) run stays green, since that branch is untouched.
// Both confirmed by hand - see task-7-report.md, "Fix round 1".

import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// run-tests.mjs is a plain untyped .mjs script outside tsconfig's `include`
// (it is the test RUNNER, not UI source) - a static `import` of it fails
// `tsc` with "could not find a declaration file". Same dynamic-import trick
// src/__tests__/runTests.test.ts already uses for the same file.
interface RunOneCounts {
  passed: number;
  failed: number;
  total: number;
}
interface RunOneResult {
  file: string;
  counts: RunOneCounts | null;
  byExit: boolean;
  ok: boolean;
  timedOut: boolean;
  output: string;
}
interface RunTestsModule {
  runOne(file: string): Promise<RunOneResult>;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const RUN_TESTS_URL = new URL("../../run-tests.mjs", import.meta.url).href;
const { runOne } = (await nodeImport(RUN_TESTS_URL)) as RunTestsModule;

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

const FORCE_ASYNC_ENV = "ASTRODECK_CSS_STUB_FORCE_ASYNC";

// ------------------------------------------------------- fixture: a file that imports .css
//
// ".mjs", not ".ts": a plain ".ts" fixture in a directory with no
// package.json resolves through tsx's CommonJS-compatible require path,
// which fails a missing ".css" handler with a *different* error (a
// SyntaxError from trying to parse the stylesheet as JS) than the one the
// real bug and this stub both actually address. ".mjs" is unambiguous ESM
// to Node regardless of package.json, so it takes the same
// ERR_UNKNOWN_FILE_EXTENSION path the five real crashing files (and the
// stub) do. Confirmed by hand against both extensions - see task-7-report.md.
//
// mkdtempSync/writeFileSync live INSIDE this try, not before it: a
// setup-time throw (a transient disk/permission error) must not leak the
// temp directory past the finally below.
let dir: string | undefined;
try {
  dir = mkdtempSync(join(tmpdir(), "astrodeck-css-stub-"));
  const cssFile = join(dir, "fixture.css");
  const testFile = join(dir, "fixture.test.mjs");
  writeFileSync(cssFile, ".fixture { color: red; }\n", "utf8");
  writeFileSync(
    testFile,
    [
      'import "./fixture.css";',
      "export const result = { passed: 1, failed: 0, total: 1 };",
      "",
    ].join("\n"),
    "utf8",
  );

  // ---- path 1: whatever this Node does by default (registerHooks, here) ----
  // Only if the ambient environment has not already forced the other one. Set
  // outside, both halves of this file would drive the async path, both would
  // still pass, and the file would report two greens for one branch.
  assert(process.env[FORCE_ASYNC_ENV] === undefined,
    `${FORCE_ASYNC_ENV} is set in this environment, so path 1 below is the async path and the two halves grade the same branch`);
  const defaultOutcome = await runOne(testFile);
  test("default path: a file that imports .css is scored, not crashed", () => {
    assert(defaultOutcome.ok === true,
      `expected the stubbed .css import to let the file run cleanly, got: ${defaultOutcome.output}`);
  });
  test("default path: the recovered tally reflects the fixture's own export", () => {
    assert(
      defaultOutcome.counts !== null &&
        defaultOutcome.counts.passed === 1 &&
        defaultOutcome.counts.failed === 0,
      `expected {passed:1,failed:0}, got ${JSON.stringify(defaultOutcome.counts)}`,
    );
  });

  // ---- path 2: force the module.register branch CI's Node 20 will take ----
  const prevForceAsync = process.env[FORCE_ASYNC_ENV];
  process.env[FORCE_ASYNC_ENV] = "1";
  let asyncOutcome: RunOneResult;
  try {
    asyncOutcome = await runOne(testFile);
  } finally {
    if (prevForceAsync === undefined) delete process.env[FORCE_ASYNC_ENV];
    else process.env[FORCE_ASYNC_ENV] = prevForceAsync;
  }
  test("forced-async path (module.register): a file that imports .css is scored, not crashed", () => {
    assert(asyncOutcome.ok === true,
      `expected the stubbed .css import to let the file run cleanly, got: ${asyncOutcome.output}`);
  });
  test("forced-async path: the recovered tally reflects the fixture's own export", () => {
    assert(
      asyncOutcome.counts !== null &&
        asyncOutcome.counts.passed === 1 &&
        asyncOutcome.counts.failed === 0,
      `expected {passed:1,failed:0}, got ${JSON.stringify(asyncOutcome.counts)}`,
    );
  });
} finally {
  if (dir) rmSync(dir, { recursive: true, force: true });
}

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`runTestsCss.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));

export const result = { passed, failed, total };
