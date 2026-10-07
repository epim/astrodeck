// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w13CleanTallyTimeoutRetry.test.ts - a guard on the test RUNNER itself
// (`ui/run-tests.mjs`), not on any UI code. Companion to runTests.test.ts
// (computeOk/parseCounts/assertionStyle) and w5RunTestsTimeoutPhase.test.ts
// (timeoutPhase); this covers `shouldRetry`/`runFileWithRetry`, added for
// issue #664.
//
//   Run directly:  npx tsx src/__tests__/w13CleanTallyTimeoutRetry.test.ts
//
// WP-H3's own directive (issue #664, filed 2026-10-02 -- after the backlog
// plan's 2026-09-30 approval, so no D-01..D-20 owner ruling in that plan
// covers it) was to confirm or rule out jsdom's native requestAnimationFrame
// as the carrier of #664 (a file prints its own clean tally, then its child
// is killed at TIMEOUT_MS - the same symptom #614/#652 named; "the default
// export never resolved" was only ever a guess at where, and the child's
// phase markers, w5RunTestsTimeoutPhase.test.ts and
// w14ChildPhaseMarkers.test.ts, now measure it). RULED OUT, not confirmed:
//
//   * A stress harness ran capturePreviewMobileOverflow.test.tsx (the file
//     #664 actually names) 160 times at CONCURRENCY 8 (40, then 120 more).
//     Zero hangs.
//   * Two full 548-file suite runs on THIS box reproduced the exact symptom
//     anyway - but on src/next/__tests__/hubBoundary.test.tsx and then
//     src/__tests__/appHeaderDom.test.tsx, two files that are not the one
//     #664 names and that do not share a render tree. hubBoundary's own tree
//     (HubBoundary/SheetHost plus two synthetic throwing bodies) calls
//     requestAnimationFrame nowhere at all, directly confirmed by reading it.
//   * Instrumented from a byte backup of run-tests.mjs (restored
//     byte-identically afterward, sha256-verified): every child was given an
//     unref'd setInterval, armed BEFORE its own `await import()`, writing
//     process._getActiveHandles()/_getActiveRequests() to a file every 2s (a
//     file, not a console.log through a pipe that a SIGTERM might never
//     flush). BOTH caught hangs wrote ZERO lines in their full 60s window -
//     no handle, no request, the whole time. A dangling jsdom rAF timer (or
//     any other lingering async resource) would show up in that list, and
//     would not stop an unrelated interval on the SAME event loop from firing
//     alongside it - timers on one event loop interleave; they do not block
//     each other. Zero dumps for 60 straight seconds on two different,
//     rAF-free files is evidence against a dangling handle of any kind, not
//     evidence for one.
//
// WP-H3's own instrumentation (an unref'd setInterval dumping
// process._getActiveHandles()/_getActiveRequests() to a file every 2s, armed
// before the child's own `await import()`) found ZERO heartbeat lines for the
// full 60s window on BOTH caught hangs. An unref'd interval fires whenever the
// event loop turns at all, so CPU/scheduling contention on the shared box -
// WP-H3's first conclusion - cannot explain that: contention slows how often
// the loop turns, it does not stop it turning for a full minute. Zero dumps
// for the whole window means the child's MAIN THREAD stopped running
// entirely after the tally printed (a synchronous stall or a blocked write),
// not that it kept running, slowly. An independent reviewer read the same
// evidence the same way. THE ROOT CAUSE IS UNKNOWN - this rules out jsdom's
// rAF and rules out CPU contention, it does not yet say what blocks the
// thread; see #664. The fix is a runner-level mitigation for exactly the
// shape measured, never for anything broader: `shouldRetry` named below is
// true ONLY when a file's own tally already proved it correct and the child
// then failed to finish (one of the post-tally phases timeoutPhase names:
// import-pending, import-resolved, exit-hung, compared by id, not by prose);
// `runFileWithRetry` then gives it
// exactly one more try in a fresh child. A timeout with no tally at all -
// something still inside the file's own cases when it was killed - is a
// different, more serious shape and is never retried, so a real hang still
// fails the suite on the first attempt. Every file this happens to is also
// named in the final summary block (`retriedSummaryLine`), not just in a
// per-attempt console.log a concurrent run can bury between other files'
// output, so CI logs show how often this is actually happening.
//
// Real `execFile` calls, not a pure-function stub for the end-to-end cases:
// `shouldRetry` alone being right would not prove `runFileWithRetry` is wired
// into anything (see this repo's own "test doubles hide the code under test"
// lesson) - and the earlier two guards on this file (runTests.test.ts,
// w5RunTestsTimeoutPhase.test.ts) already show what a PURE unit test of a
// decision function buys on its own. A custom `timeoutMs` (3s, not the real
// 60s TIMEOUT_MS) keeps both real-timeout fixtures well under a second each
// of wall time beyond the measured ~90ms Node+tsx bootstrap, without this
// guard becoming the next "costs a minute of CI time by design" problem.
//
// NAMED MUTANT "the retry safety net removed" (shouldRetry's body replaced
// with `return false;`, run from a byte backup, restored byte-identically,
// sha256-verified, grepped gone): the positive end-to-end case goes red
// because runFileWithRetry never gives the hang-once fixture its second
// chance, so the OVERALL result comes back exactly as it did before #664 was
// looked at - timed out, not ok - even though the file's own single assertion
// had already passed. It also takes down both pure-boundary cases, since
// neither shape is "retried" once the function always says no. Observed
// failure (verbatim, "w13CleanTallyTimeoutRetry.test: 4/7 passed"):
//
//   x a timeout after a clean N/N tally is retried: the #664 shape itself -
//   a clean tally, then the kill - must be retried
//   x a timeout after a throw-on-failure completion phrase is retried: a
//   throw-on-failure file's completion phrase counts as a finished tally too
//   - timeoutPhase already says so
//   x a file that times out AFTER printing a clean tally gets exactly one
//   retry, and the retry's clean pass is what is reported: expected ok=true
//   timedOut=false after retry, got ok=false timedOut=true
//
// NAMED MUTANT "the summary line removed" (backlog wave 13 integration,
// `retriedSummaryLine`'s body replaced with `return null;`, run from a byte
// backup, restored byte-identically, sha256-verified): the "retriedSummaryLine
// names a file that froze after a clean tally and passed on retry" case goes
// red because the line that should name the frozen file is unconditionally
// absent. Observed failure (verbatim, "w13CleanTallyTimeoutRetry.test:
// 10/11 passed"):
//
//   x retriedSummaryLine names a file that froze after a clean tally and
//   passed on retry: expected a non-null summary line for a
//   retried-and-passed result
//
// Backlog wave 14 (WP-97, #664 Part A) added the child's phase markers, so
// `shouldRetry` now compares a phase ID for each post-tally shape and the
// retry carries the first attempt's phase into the summary. Named mutants
// (each applied to run-tests.mjs from a byte backup, restored
// byte-identically, sha256-verified, and checked gone). Observed failures,
// verbatim:
//
// "shouldRetry retries only the import-pending phase" (the
// `RETRYABLE_PHASES.has(...)` test replaced with `timeoutPhase(result).id ===
// PHASES.importPending.id`). "w13CleanTallyTimeoutRetry.test: 15/17 passed":
//   x a timeout after the tally with __IMPORTED__ but no __EXITING__ is
//   retried: the import-resolved shape is a post-tally freeze like the others
//   x a timeout with __EXITING__ seen (process.exit never returned) is
//   retried: the exit-hung shape is the one #664's brief says to test first,
//   and it stays retried until its cause is found
//
// "shouldRetry drops the tally requirement" (the `&& hasTally(result.output)`
// line deleted). "w13CleanTallyTimeoutRetry.test: 16/17 passed":
//   x a timeout whose import resolved but printed nothing scorable is never
//   retried: no tally: never retried, whatever the markers say
//
// "runFileWithRetry does not carry the first phase" (`firstPhase: phase.id`
// dropped from the returned result). "w13CleanTallyTimeoutRetry.test: 15/17
// passed":
//   x a retried result carries the phase its first attempt froze in: expected
//   the first attempt's phase import-pending, got undefined
//
// "retriedSummaryLine omits the phase" (the ` [${r.firstPhase}]` suffix
// deleted). "w13CleanTallyTimeoutRetry.test: 16/17 passed":
//   x retriedSummaryLine names the phase beside the file: expected the phase
//   id in the summary line, got: 1 file(s) froze after a clean tally and
//   passed on a retry (#664): <the fixture's path>
//
// Backlog wave 15 (WP-115, #664 Part B) added the grace kill: a child that has
// said __EXITING__ and still has not finished a few seconds later is killed by
// the runner, passed with a note when its tally was clean, and, being no
// timeout, never retried. Named mutants (applied to run-tests.mjs from a byte
// backup, restored byte-identically, sha256-verified, and checked gone).
// Observed failures, verbatim (the rest of the mutant list is in
// w15ChildExitReport.test.ts):
//
// "a grace-killed child is retried" (shouldRetry's `return result.timedOut` ->
// `return (result.timedOut || result.graceKilled)`). "w13CleanTallyTimeoutRetry
// .test: 17/22 passed":
//   x a non-timeout result is never retried, even with a clean tally: retrying
//   is only ever about a timeout; a file that exited on its own has nothing to
//   retry
//   x a child the grace kill ended is not a timeout, so it is never retried: a
//   grace-killed child must be reported as it is, not retried
//   x a file whose process.exit() never returns is grace-killed and passed on
//   the first attempt, never retried: a grace kill is not a freeze to retry,
//   but the result says retried=true
//
// "the grace kill is never armed" (`if (exitGraceMs > 0) {` -> `if (false) {`).
// "19/22 passed":
//   x a file whose process.exit() never returns is grace-killed and passed on
//   the first attempt, never retried: expected ok=true timedOut=false
//   graceKilled=true, got ok=false timedOut=true graceKilled=false
//   x runFileWithRetry with no grace argument still grace-kills a hung exit
//   (the call main makes): the default grace, not the 30000ms timeout, must end
//   a hung exit: ok=false timedOut=true graceKilled=false
//
// "gracedSummaryLine finds nothing" (`const killed = ...` -> `[]`). "21/22
// passed":
//   x the grace-killed pass reaches the summary note and not the retried line:
//   a passed-with-a-note file must be named in the summary, got: null
//
// Three mutants the first version of this file let through, found by the WP-115
// verifier, which is why the last two cases below exist. Every other case hands
// runFileWithRetry its grace explicitly, so the production DEFAULT and what
// `main` does with it were pinned by nothing:
//
// "the default grace is off" (runFileWithRetry's `exitGraceMs = EXIT_GRACE_MS`
// -> `exitGraceMs = 0`). "21/22 passed", in about 75 s (the 30 s timeout, then
// the retry's):
//   x runFileWithRetry with no grace argument still grace-kills a hung exit
//   (the call main makes): the default grace must be EXIT_GRACE_MS (5000ms),
//   got 0ms
//
// "the grace is as long as the timeout" (`EXIT_GRACE_MS = 5_000` ->
// `5_000_000`). "21/22 passed", same cost:
//   x runFileWithRetry with no grace argument still grace-kills a hung exit
//   (the call main makes): the default grace, not the 30000ms timeout, must end
//   a hung exit: ok=false timedOut=true graceKilled=false
//
// "main passes its own grace" (main's `runFileWithRetry(files[next++])` ->
// `runFileWithRetry(files[next++], 60_000, 0)`). "21/22 passed":
//   x main gives runFileWithRetry only the file and prints the grace note: main
//   passes runFileWithRetry a timeout or a grace of its own, so the default
//   grace this file pins is not the one a real run gets
//
// "main never prints the note" (`if (gracedLine) console.log(gracedLine);` ->
// `if (false) console.log(gracedLine);`). "21/22 passed":
//   x main gives runFileWithRetry only the file and prints the grace note: main
//   must print `gracedLine` whenever it is not null: a grace-killed pass is
//   otherwise silent

/* eslint-disable @typescript-eslint/no-explicit-any */

import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// run-tests.mjs is a plain untyped .mjs script outside tsconfig's `include`
// (it is the test RUNNER, not UI source) - a static `import` of it fails
// `tsc` with "could not find a declaration file". Same dynamic-import trick
// runTests.test.ts and runTestsCss.test.ts already use for the same file.
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
  imported?: boolean;
  exiting?: boolean;
  retried?: boolean;
  firstPhase?: string;
  graceKilled?: boolean;
  exitGraceMs?: number;
}
interface RunTestsModule {
  runOne(file: string, timeoutMs?: number): Promise<RunOneResult>;
  runFileWithRetry(file: string, timeoutMs?: number, exitGraceMs?: number): Promise<RunOneResult>;
  shouldRetry(result: {
    timedOut: boolean; output: string; imported?: boolean; exiting?: boolean;
    graceKilled?: boolean;
  }): boolean;
  retriedSummaryLine(results: RunOneResult[]): string | null;
  gracedSummaryLine(results: RunOneResult[]): string | null;
  EXIT_GRACE_MS: number;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const RUN_TESTS_URL = new URL("../../run-tests.mjs", import.meta.url).href;
const {
  runOne, runFileWithRetry, shouldRetry, retriedSummaryLine, gracedSummaryLine, EXIT_GRACE_MS,
} = (await nodeImport(RUN_TESTS_URL)) as RunTestsModule;

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function atest(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// Well under the real TIMEOUT_MS (60s) but far above the measured ~90ms
// Node+tsx bootstrap for a trivial fixture, so a loaded CI runner cannot
// flake this the way #664 itself flaked the real suite. It was 3000 until
// 2026-10-07, when a box saturated by parallel agents proved that is not far
// enough: in 3 of 25 loaded runs the RETRY's child needed longer than 3 s to
// start, timed out too, and "a file that times out AFTER printing a clean
// tally gets exactly one retry" failed with ok=false timedOut=true. The whole
// budget applies to the retry's bootstrap, so it has to be generous; the cost
// is paid only by the two fixtures that are killed on purpose.
const SMALL_TIMEOUT_MS = 6000;
// For the case that runs with the REAL default grace (EXIT_GRACE_MS, 5 s): far
// above it plus a loaded box's bootstrap, so a timeout can only win if the grace
// kill did not happen.
const DEFAULT_PATH_TIMEOUT_MS = 30_000;

// --------------------------------------------------- shouldRetry: pure cases
// Fast, exact-boundary coverage before the slower real-process cases below -
// the same pairing runTestsCss.test.ts (real cases) and runTests.test.ts
// (pure computeOk cases) already use for this file's other two guards.

test("a timeout after a clean N/N tally is retried", () => {
  assert(shouldRetry({ timedOut: true, output: "widget.test: 4/4 passed" }) === true,
    "the #664 shape itself - a clean tally, then the kill - must be retried");
});

test("a timeout after a throw-on-failure completion phrase is retried", () => {
  assert(shouldRetry({ timedOut: true, output: "setting up...\nall assertions passed" }) === true,
    "a throw-on-failure file's completion phrase counts as a finished tally too - timeoutPhase already says so");
});

test("a timeout with no tally at all is NEVER retried", () => {
  assert(shouldRetry({ timedOut: true, output: "rendering...\n" }) === false,
    "a hang INSIDE the file's own cases is a different, more serious shape and must fail on the first attempt");
});

test("a non-timeout result is never retried, even with a clean tally", () => {
  assert(shouldRetry({ timedOut: false, output: "widget.test: 4/4 passed" }) === false,
    "retrying is only ever about a timeout; a file that exited on its own has nothing to retry");
});

// The child's own phase markers (#664 Part A) split "after the tally" into
// three shapes; shouldRetry compares the phase ID for every one of them, so
// a freeze that the markers name precisely is still retried, and none of the
// three is mistaken for the no-tally shape.

test("a timeout after the tally with __IMPORTED__ but no __EXITING__ is retried", () => {
  assert(shouldRetry({ timedOut: true, output: "widget.test: 4/4 passed", imported: true }) === true,
    "the import-resolved shape is a post-tally freeze like the others");
});

test("a timeout with __EXITING__ seen (process.exit never returned) is retried", () => {
  assert(shouldRetry({
    timedOut: true, output: "widget.test: 4/4 passed", imported: true, exiting: true,
  }) === true,
    "the exit-hung shape is the one #664's brief says to test first, and it stays retried until its cause is found");
});

test("a timeout whose import resolved but printed nothing scorable is never retried", () => {
  // __IMPORTED__ proves the cases finished, but the runner's rule is that a
  // timeout with no tally at all is never retried (existing rule, #664). Such
  // a file is unscorable anyway; retrying it would only cost a second minute.
  assert(shouldRetry({ timedOut: true, output: "", imported: true, exiting: true }) === false,
    "no tally: never retried, whatever the markers say");
});

test("a child the grace kill ended is not a timeout, so it is never retried", () => {
  // #664 Part B: a child that said __EXITING__ and never finished is killed by
  // the runner and passed with a note. It looks like exit-hung to the phase
  // logic (tally, both markers), so only the timedOut=false it carries keeps
  // the retry from running a second child for a result already in.
  assert(shouldRetry({
    timedOut: false, output: "widget.test: 4/4 passed", imported: true, exiting: true,
    graceKilled: true,
  }) === false, "a grace-killed child must be reported as it is, not retried");
});

// --------------------------------------------------------- end-to-end cases
//
// Each fixture is a plain ".mjs" (unambiguous ESM regardless of package.json,
// same reasoning runTestsCss.test.ts's own fixture gives for the extension),
// so it runs the identical code path `findTests`/`runOne` give a real file.

let dir: string | undefined;
try {
  dir = mkdtempSync(join(tmpdir(), "astrodeck-w13-retry-"));

  // ---- positive: the #664 shape itself -- hangs once, then is clean -------
  const marker = join(dir, "ran-once.marker");
  const hangOnceFile = join(dir, "hangOnce.test.mjs");
  writeFileSync(
    hangOnceFile,
    [
      'import { existsSync, writeFileSync } from "node:fs";',
      `const MARKER = ${JSON.stringify(marker)};`,
      // The tally prints EVERY attempt, exactly like a real file's -- #664's
      // own report shape is "tally printed, THEN the child is killed at
      // TIMEOUT_MS", never a tally that fails to print at all.
      'console.log("hangOnce.test: 1/1 passed");',
      "if (!existsSync(MARKER)) {",
      '  writeFileSync(MARKER, "ran");',
      // A busy `while` loop, not `await new Promise(() => {})`: Node 24 (this
      // box's only Node) detects a bare unsettled top-level await once the
      // event loop would otherwise go idle and exits the process itself with
      // a warning, in well under a second -- it never reaches our own
      // SIGTERM. A loop that never yields matches what WP-H3 actually
      // measured for #664 (zero live handles for the whole window the real
      // hangs were killed in) and forces the genuine, timed-out-by-us kill
      // this case needs to exercise.
      "  while (true) { /* busy -- never yields, no handle for Node to see */ }",
      "}",
      "export const result = { passed: 1, failed: 0, total: 1 };",
      "",
    ].join("\n"),
    "utf8",
  );

  let hangOnceFinal: RunOneResult | undefined;
  await atest(
    "a file that times out AFTER printing a clean tally gets exactly one retry, and the retry's clean pass is what is reported",
    async () => {
      const start = Date.now();
      const final = await runFileWithRetry(hangOnceFile, SMALL_TIMEOUT_MS);
      hangOnceFinal = final;
      const elapsedMs = Date.now() - start;
      assert(final.ok === true && final.timedOut === false,
        `expected ok=true timedOut=false after retry, got ok=${final.ok} timedOut=${final.timedOut}`);
      // The first attempt must have genuinely been killed at SMALL_TIMEOUT_MS
      // (not short-circuited some other way) -- if the retry wiring silently
      // skipped straight to a second attempt without the first ever actually
      // timing out, this bound would not hold.
      assert(elapsedMs >= SMALL_TIMEOUT_MS,
        `expected the first attempt to consume the full ${SMALL_TIMEOUT_MS}ms timeout before the retry ran, `
        + `but the whole call took only ${elapsedMs}ms`);
    },
  );

  // ---- #664 item 2: the retry is named in the FINAL summary block, not
  // only in the per-attempt console.log a concurrent run can bury between
  // other files' own output.
  test("a file that froze once after its tally is marked retried, for the final summary to name", () => {
    assert(hangOnceFinal !== undefined, "the previous case must have run first");
    assert(hangOnceFinal!.retried === true,
      `expected runFileWithRetry to mark a genuinely-retried result, got retried=${hangOnceFinal!.retried}`);
  });

  test("retriedSummaryLine names a file that froze after a clean tally and passed on retry", () => {
    assert(hangOnceFinal !== undefined, "the previous case must have run first");
    const line = retriedSummaryLine([hangOnceFinal!]);
    assert(line !== null, "expected a non-null summary line for a retried-and-passed result");
    assert(line!.includes("1 file(s)"),
      `expected the count in the summary line, got: ${line}`);
    assert(line!.includes("#664"), `expected the issue number in the summary line, got: ${line}`);
    assert(line!.includes("hangOnce.test.mjs"),
      `expected the frozen file's own name in the summary line, got: ${line}`);
  });

  // The phase the FIRST attempt stopped in is what makes the summary line
  // evidence instead of a bare count: a freeze that passes on its retry leaves
  // no other trace, so without the phase a full-suite loop could not say where
  // the freezes land (#664). hangOnce spins at its own top level, so its
  // import never returned.
  test("a retried result carries the phase its first attempt froze in", () => {
    assert(hangOnceFinal !== undefined, "the previous case must have run first");
    assert(hangOnceFinal!.firstPhase === "import-pending",
      `expected the first attempt's phase import-pending, got ${hangOnceFinal!.firstPhase}`);
  });

  test("retriedSummaryLine names the phase beside the file", () => {
    assert(hangOnceFinal !== undefined, "the previous case must have run first");
    const line = retriedSummaryLine([hangOnceFinal!]);
    assert(line !== null && line.includes("import-pending"),
      `expected the phase id in the summary line, got: ${line}`);
  });

  test("retriedSummaryLine still names a retried file whose phase was not recorded", () => {
    const noPhase: RunOneResult = {
      file: hangOnceFile, counts: { passed: 1, failed: 0, total: 1 },
      byExit: false, ok: true, timedOut: false, output: "", retried: true,
    };
    const line = retriedSummaryLine([noPhase]);
    assert(line !== null && line.includes("hangOnce.test.mjs") && !line.includes("undefined"),
      `a missing phase must not print as the word undefined, got: ${line}`);
  });

  test("retriedSummaryLine is null when nothing was retried", () => {
    const cleanResult: RunOneResult = {
      file: hangOnceFile, counts: { passed: 1, failed: 0, total: 1 },
      byExit: false, ok: true, timedOut: false, output: "",
    };
    assert(retriedSummaryLine([cleanResult]) === null,
      "a run with no retried file must print nothing, not an empty-named line");
  });

  test("retriedSummaryLine excludes a file that was retried but still failed", () => {
    const stillBroken: RunOneResult = {
      file: hangOnceFile, counts: null, byExit: false, ok: false,
      timedOut: true, output: "", retried: true,
    };
    assert(retriedSummaryLine([stillBroken]) === null,
      "a retry that did NOT pass belongs in the broken list, not the retried-and-passed summary line");
  });

  // ---- #664 Part B: a hung exit is killed by the grace, passed, and not retried
  // The file prints a clean tally and then its process.exit() never returns
  // (an 'exit' listener spins). It appends to a log on every run, so a retry
  // (which must NOT happen: the grace kill is not a timeout) would show as a
  // second line.
  const exitAttempts = join(dir, "exit-attempts.log");
  const hangInExitFile = join(dir, "hangInExit.test.mjs");
  writeFileSync(
    hangInExitFile,
    [
      'import { appendFileSync } from "node:fs";',
      `appendFileSync(${JSON.stringify(exitAttempts)}, "x");`,
      'console.log("hangInExit.test: 1/1 passed");',
      'process.on("exit", () => { while (true) { /* busy -- exit never finishes */ } });',
      "export const result = { passed: 1, failed: 0, total: 1 };",
      "",
    ].join(String.fromCharCode(10)),
    "utf8",
  );

  let hangInExitFinal: RunOneResult | undefined;
  await atest(
    "a file whose process.exit() never returns is grace-killed and passed on the first attempt, never retried",
    async () => {
      // A timeout far above the grace, so only the grace kill can end it
      // before the assertion's deadline (#669, #675).
      const final = await runFileWithRetry(hangInExitFile, 40_000, 1500);
      hangInExitFinal = final;
      assert(final.ok === true && final.timedOut === false && final.graceKilled === true,
        `expected ok=true timedOut=false graceKilled=true, got ok=${final.ok} timedOut=${final.timedOut} graceKilled=${final.graceKilled}`);
      assert(final.retried === undefined,
        `a grace kill is not a freeze to retry, but the result says retried=${final.retried}`);
      // One "x" appended per child that ran.
      const attempts = readFileSync(exitAttempts, "utf8").length;
      assert(attempts === 1,
        `expected exactly one child process (no retry), got ${attempts} attempt(s)`);
    },
  );

  test("the grace-killed pass reaches the summary note and not the retried line", () => {
    assert(hangInExitFinal !== undefined, "the previous case must have run first");
    assert(retriedSummaryLine([hangInExitFinal!]) === null,
      "nothing was retried, so the retried line must stay empty");
    const line = gracedSummaryLine([hangInExitFinal!]);
    assert(line !== null && line.includes("hangInExit.test.mjs") && line.includes("#664"),
      `a passed-with-a-note file must be named in the summary, got: ${line}`);
  });

  // ---- #664 Part B: the grace kill is on for the call `main` actually makes
  // Every case above hands runFileWithRetry its grace explicitly, so a default
  // of 0 (or a grace as long as the timeout) would leave all of them green
  // while the real run never killed anything: `main` calls
  // runFileWithRetry(file) and takes whatever the defaults are. This runs the
  // same shape with NO grace argument, under a timeout far above the real
  // EXIT_GRACE_MS plus a loaded box's child bootstrap, so only the default
  // grace can have ended it (#669, #675).
  const hangInExitDefaultFile = join(dir, "hangInExitDefault.test.mjs");
  writeFileSync(
    hangInExitDefaultFile,
    [
      'console.log("hangInExitDefault.test: 1/1 passed");',
      'process.on("exit", () => { while (true) { /* busy -- exit never finishes */ } });',
      "export const result = { passed: 1, failed: 0, total: 1 };",
      "",
    ].join(String.fromCharCode(10)),
    "utf8",
  );
  await atest(
    "runFileWithRetry with no grace argument still grace-kills a hung exit (the call main makes)",
    async () => {
      const final = await runFileWithRetry(hangInExitDefaultFile, DEFAULT_PATH_TIMEOUT_MS);
      assert(final.exitGraceMs === EXIT_GRACE_MS,
        `the default grace must be EXIT_GRACE_MS (${EXIT_GRACE_MS}ms), got ${final.exitGraceMs}ms`);
      assert(final.graceKilled === true && final.timedOut === false && final.ok === true,
        `the default grace, not the ${DEFAULT_PATH_TIMEOUT_MS}ms timeout, must end a hung exit: `
        + `ok=${final.ok} timedOut=${final.timedOut} graceKilled=${final.graceKilled}`);
      assert(final.retried === undefined,
        `a grace kill is not a freeze to retry, but the result says retried=${final.retried}`);
    },
  );

  // `main` cannot be run from here (importing the module must not run the
  // suite, and running it would), so what it does with these two is read from
  // its source: it must call runFileWithRetry with the file and NOTHING else,
  // which is what makes the default grace above the one it gets, and it must
  // print gracedSummaryLine, which is the only place a passed-with-a-note file
  // is visible once the run is over.
  test("main gives runFileWithRetry only the file and prints the grace note", () => {
    const src = readFileSync(new URL("../../run-tests.mjs", import.meta.url), "utf8");
    const at = src.indexOf("async function main()");
    assert(at >= 0, "run-tests.mjs no longer has `async function main()`: update this case");
    const body = src.slice(at);
    const call = body.indexOf("runFileWithRetry(");
    assert(call >= 0, "main must run every file through runFileWithRetry");
    let depth = 0;
    let topLevelCommas = 0;
    for (let i = call + "runFileWithRetry".length; i < body.length; i++) {
      const c = body[i];
      if (c === "(" || c === "[") depth++;
      else if (c === ")" || c === "]") { depth--; if (depth === 0) break; }
      else if (c === "," && depth === 1) topLevelCommas++;
    }
    assert(topLevelCommas === 0,
      "main passes runFileWithRetry a timeout or a grace of its own, so the default grace "
      + "this file pins is not the one a real run gets");
    // The note is a variable main then prints: reading that it is ASKED for is
    // not enough, a `if (false)` in front of the print would leave it silent.
    const noted = /const\s+(\w+)\s*=\s*gracedSummaryLine\(results\)/.exec(body);
    assert(noted !== null,
      "main must ask gracedSummaryLine(results) for the note: without it a grace-killed pass is silent");
    const v = noted![1];
    assert(new RegExp("if\\s*\\(\\s*" + v + "\\s*\\)\\s*console\\.log\\(\\s*" + v + "\\s*\\)").test(body),
      `main must print \`${v}\` whenever it is not null: a grace-killed pass is otherwise silent`);
  });

  // ---- negative: a hang with NO tally is never retried --------------------
  const attemptsLog = join(dir, "attempts.log");
  const hangAlwaysFile = join(dir, "hangAlways.test.mjs");
  writeFileSync(
    hangAlwaysFile,
    [
      'import { appendFileSync } from "node:fs";',
      // Appended, not overwritten: a second invocation (which must never
      // happen) would add a second line, which the assertion below catches.
      `appendFileSync(${JSON.stringify(attemptsLog)}, "attempt\\n");`,
      // No console.log at all -- this file never gets as far as its own
      // tally, the "during its test cases" shape shouldRetry must refuse.
      // A busy loop, not a dangling await -- see hangOnce.test.mjs above for
      // why.
      "while (true) { /* busy -- never yields, no handle for Node to see */ }",
      "",
    ].join("\n"),
    "utf8",
  );

  await atest(
    "a file that times out with NO tally is reported broken on the first attempt, never retried",
    async () => {
      const final = await runFileWithRetry(hangAlwaysFile, SMALL_TIMEOUT_MS);
      assert(final.ok === false && final.timedOut === true,
        `expected the no-tally hang to stay broken, got ok=${final.ok} timedOut=${final.timedOut}`);
      const attempts = readFileSync(attemptsLog, "utf8").trim().split("\n").filter(Boolean);
      assert(attempts.length === 1,
        `expected exactly one child process to have run (no retry), got ${attempts.length} attempt(s)`);
    },
  );

  // Sanity control: runOne on its own (no retry wrapper) must still behave
  // exactly as it did before this change for a file that just exits cleanly
  // -- the optional `timeoutMs` default must not have altered that path.
  const cleanFile = join(dir, "clean.test.mjs");
  writeFileSync(cleanFile, 'console.log("clean.test: 1/1 passed");\nexport const result = { passed: 1, failed: 0, total: 1 };\n', "utf8");
  await atest("runOne with an explicit timeoutMs still scores a clean file correctly", async () => {
    const r = await runOne(cleanFile, SMALL_TIMEOUT_MS);
    assert(r.ok === true && r.timedOut === false && r.counts?.failed === 0,
      `expected a clean pass, got ${JSON.stringify(r)}`);
  });
} finally {
  if (dir) rmSync(dir, { recursive: true, force: true });
}

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`w13CleanTallyTimeoutRetry.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));

export const result = { passed, failed, total };
