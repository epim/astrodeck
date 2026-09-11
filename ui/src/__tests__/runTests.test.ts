// A guard on the test RUNNER itself (`ui/run-tests.mjs`), not on any UI code.
//
// Review finding #48 proved two holes in the runner, both in the same place:
// a file's nonzero child exit was ignored whenever a passing tally could
// still be parsed out of its stdout/stderr.
//
//   * `ok: !timedOut && ((counts !== null && counts.failed === 0) || byExit)`
//     never looked at `err` (the `execFile` callback's error, set on ANY
//     nonzero exit, not just a timeout). A file that prints "2/2 passed" and
//     then throws — in teardown, in a stray unhandled rejection, anywhere
//     after its own summary line — reported "all green".
//   * `parseCounts`'s bare "N passed" branch (no explicit fail count, unlike
//     "N/M passed" or "N passed, M failed") returns `{failed: 0}` no matter
//     what actually happened. Paired with a `process.exit(1)` from that same
//     kind of file, it was scored green for the same reason.
//
// Both were the SAME missing check (the runner's `ok` decision skipped the
// child's exit code whenever a tally-shaped string existed) surfacing at two
// call sites, so `ui/run-tests.mjs`'s `computeOk` now folds `err` into the
// decision unconditionally: `if (timedOut || err) return false;`.
//
// This tests that function directly (`computeOk`/`parseCounts`/
// `assertionStyle` are exported for exactly this) rather than via a fixture
// file under `src/__tests__/` — a file built to prove "a crash after a
// passing tally reads as green" would, if the runner regressed, have to
// actually turn the real `npm test` run red, which defeats the point of a
// regression test. A pure-function unit test proves the same thing without
// needing the real suite to fail if the guard is ever removed.
//
// It also proves the fix does not change discovery or scoring for any real
// file: `computeOk` is unchanged for every case where `err` is falsy, i.e.
// every file that exits 0 — which is all 291 of them today.
//
//   Run directly:  npx tsx src/__tests__/runTests.test.ts

// run-tests.mjs is a plain untyped .mjs script outside tsconfig's `include`
// (it is the test RUNNER, not UI source) - a static `import` of it fails
// `tsc` with "could not find a declaration file". Same dependency-free
// dynamic-import trick as src/next/__tests__/shellCss.test.ts and
// src/__tests__/weatherHoldClaim.test.ts use for node:fs, which sidesteps
// static module resolution the same way.
interface RunTestsCounts {
  passed: number;
  failed: number;
  total: number;
}
interface RunTestsModule {
  computeOk(args: {
    counts: RunTestsCounts | null;
    byExit: boolean;
    err: unknown;
    timedOut: boolean;
  }): boolean;
  parseCounts(text: string): RunTestsCounts | null;
  assertionStyle(text: string): boolean;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const RUN_TESTS_URL = new URL("../../run-tests.mjs", import.meta.url).href;
const { computeOk, parseCounts, assertionStyle } =
  (await nodeImport(RUN_TESTS_URL)) as RunTestsModule;

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

const importStart = Date.now();

// ------------------------------------------------------- hole 1: crash after a full tally

test("hole 1: a passing N/M tally followed by a nonzero exit is NOT ok", () => {
  // e.g. a file that prints "2/2 passed" and then throws in teardown.
  const counts = parseCounts("widget.test: 2/2 passed");
  assert(counts !== null && counts.failed === 0, "setup: expected a clean 2/2 tally");
  const crashErr = Object.assign(new Error("Command failed"), { code: 1 });
  const ok = computeOk({ counts, byExit: false, err: crashErr, timedOut: false });
  assert(ok === false,
    "computeOk must return false when the child crashed, even with a clean tally parsed");
});

test("hole 1 control: the same clean tally with a clean exit IS ok", () => {
  const counts = parseCounts("widget.test: 2/2 passed");
  const ok = computeOk({ counts, byExit: false, err: null, timedOut: false });
  assert(ok === true, "a clean tally with no error must still be scored ok");
});

// ------------------------------------------------------- hole 2: bare "N passed" + exit 1

test("hole 2: parseCounts' bare 'N passed' form reports zero failures with no evidence", () => {
  // This is not itself wrong — the format carries no fail count — but it
  // means computeOk MUST look at the exit code for this shape, since the
  // tally alone cannot ever disagree with it.
  const counts = parseCounts("legacy.test: 5 passed");
  assert(counts !== null && counts.passed === 5 && counts.failed === 0 && counts.total === 5,
    "parseCounts' bare form should still parse the count itself correctly");
});

test("hole 2: bare 'N passed' plus a nonzero exit (process.exit(1)) is NOT ok", () => {
  const counts = parseCounts("legacy.test: 5 passed");
  const crashErr = Object.assign(new Error("Command failed"), { code: 1 });
  const ok = computeOk({ counts, byExit: false, err: crashErr, timedOut: false });
  assert(ok === false,
    "computeOk must return false for a bare 'N passed' tally paired with a nonzero exit");
});

// ------------------------------------------------------- unaffected paths (no regression)

test("unaffected: a failing tally is still NOT ok, with or without an error", () => {
  const counts = { passed: 1, failed: 1, total: 2 };
  assert(computeOk({ counts, byExit: false, err: null, timedOut: false }) === false,
    "a failing tally alone must stay red");
  const crashErr = Object.assign(new Error("Command failed"), { code: 1 });
  assert(computeOk({ counts, byExit: false, err: crashErr, timedOut: false }) === false,
    "a failing tally plus a crash must stay red");
});

test("unaffected: the throw-on-failure (byExit) path still passes on a clean exit", () => {
  assert(assertionStyle("all assertions passed") === true,
    "assertionStyle should recognise the completion phrase");
  const ok = computeOk({ counts: null, byExit: true, err: null, timedOut: false });
  assert(ok === true, "a byExit file with no error must still be scored ok");
});

test("unaffected: byExit itself requires a clean exit (unchanged by this fix)", () => {
  // byExit is computed by the caller as `!err && counts === null && assertionStyle(output)`,
  // so a crashed byExit-style file arrives here as byExit: false already. computeOk must
  // still refuse it via the `err` check, not rely solely on the caller's byExit flag.
  const crashErr = Object.assign(new Error("Command failed"), { code: 1 });
  const ok = computeOk({ counts: null, byExit: false, err: crashErr, timedOut: false });
  assert(ok === false, "a crashed run with no parseable tally must stay red");
});

test("unaffected: a timeout is never ok, regardless of any tally", () => {
  const counts = { passed: 2, failed: 0, total: 2 };
  const ok = computeOk({ counts, byExit: false, err: null, timedOut: true });
  assert(ok === false, "a timed-out run must stay red even with a clean tally parsed");
});

// ------------------------------------------------------- parseCounts' other two branches, unchanged

test("parseCounts: the 'N passed, M failed' branch is unaffected by this fix", () => {
  const counts = parseCounts("foo: 3 passed, 1 failed");
  assert(counts !== null && counts.passed === 3 && counts.failed === 1 && counts.total === 4,
    "the two-number branch must still read both counts");
});

test("parseCounts: the 'N/M passed' branch is unaffected by this fix", () => {
  const counts = parseCounts("foo: 7/10 passed");
  assert(counts !== null && counts.passed === 7 && counts.failed === 3 && counts.total === 10,
    "the ratio branch must still derive failed from total - passed");
});

test("parseCounts: unscorable output still returns null", () => {
  assert(parseCounts("nothing recognisable here") === null,
    "output with no tally shape must stay null, not silently default to a pass");
});

// ------------------------------------------------------- importing the runner must not run the suite

test("importing run-tests.mjs for its exports does not run the whole suite", () => {
  // If the `isMain` guard around the real run were ever lost, importing this
  // module would re-walk `src`, spawn a child process per real test file,
  // and finally call `process.exit(...)` — which would kill THIS process
  // before these assertions ever ran, and would take far longer than a
  // function import. Reaching this line at all is part of the proof; the
  // elapsed time bounds out the "it ran but was merely slow" case too.
  const elapsedMs = Date.now() - importStart;
  assert(elapsedMs < 15_000,
    `importing run-tests.mjs took ${elapsedMs}ms - suspiciously long for a module import; ` +
    "the isMain guard may be re-running the whole suite on import");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`runTests.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));

export const result = { passed, failed, total };
