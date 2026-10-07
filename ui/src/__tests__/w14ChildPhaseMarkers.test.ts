// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w14ChildPhaseMarkers.test.ts - the REAL-CHILD half of the guard on
// run-tests.mjs's phase markers (#664 Part A, #614). The pure half -- what
// `timeoutPhase` and `splitChildOutput` make of captured text -- is
// w5RunTestsTimeoutPhase.test.ts; this proves the child process actually
// writes the markers, in the right order and at the right moments, and that
// the parent reads and strips them.
//
//   Run directly:  npx tsx src/__tests__/w14ChildPhaseMarkers.test.ts
//
// WHY A REAL CHILD. A pure test on captured text would pass against a runner
// whose child never wrote a marker at all (the see-the-code-under-test lesson:
// the doubles hide it). Each fixture below is a plain ".mjs" test file that
// freezes at ONE chosen point, and `runOne` is pointed at it with a short
// timeout, so the marker set the parent recovers can only have come from what
// the child really did before it was killed:
//
//   importHang   prints its tally, then spins at its own top level
//                -> no __IMPORTED__               -> phase import-pending
//   reportHang   its export's `failed` getter spins, so the child's own
//                `typeof r.failed` read never returns
//                -> __IMPORTED__, no __EXITING__  -> phase import-resolved
//   exitHang     an 'exit' listener spins, so process.exit() itself never
//                returns -- the shape #664's brief says to test first
//                -> __IMPORTED__ and __EXITING__  -> phase exit-hung
//
// Busy loops, not an unsettled await: Node 24 detects a bare unsettled
// top-level await once the loop would otherwise go idle and exits the child
// with a warning in well under a second, so it never reaches the parent's own
// kill (the same reason w13CleanTallyTimeoutRetry's fixtures spin).
//
// Timing: the three hang fixtures run CONCURRENTLY, each with KILL_MS, so
// this file costs about one KILL_MS of wall time, not three. KILL_MS is far
// above the ~90 ms Node+tsx bootstrap a lone child needs, because a child
// killed BEFORE it printed its tally reads as `no-tally` and would fail the
// case for a reason that has nothing to do with the markers (#669, #675: no
// assertion here leans on a short fixed delay).
//
// NAMED MUTANTS (backlog wave 14, WP-97; each applied to run-tests.mjs from a
// byte backup, restored byte-identically, sha256-verified, and checked gone).
// Observed failures, verbatim:
//
// "the child never writes __EXITING__ before the result-bearing process.exit"
// (the `mark(EXITING)` line above `process.exit(r.failed > 0 ? 1 : 0)`
// deleted). "w14ChildPhaseMarkers.test: 5/8 passed":
//   x process.exit() itself never returned -> exit-hung, with the tally still
//   recovered: __EXITING__ must reach the parent before the kill, got
//   timedOut=true imported=true exiting=false ok=false
//   output="exitHang.test: 1/1 passed"
//
// "the no-export exit path never writes __EXITING__" (the `mark(EXITING)`
// line above the bare `process.exit(0)` deleted).
// "w14ChildPhaseMarkers.test: 7/8 passed":
//   x a clean file with no result export reaches the bare process.exit(0) and
//   says so: the no-export exit path must write __EXITING__ too, got
//   timedOut=false imported=true exiting=false ok=true
//   output="cleanNoExport.test: 2/2 passed"
//
// "__IMPORTED__ is written before the import instead of after" (the
// `mark(IMPORTED)` line moved above `await import(...)`).
// "w14ChildPhaseMarkers.test: 6/8 passed":
//   x import never returned: killed after the tally with no __IMPORTED__ ->
//   import-pending: the child was still inside the import, got timedOut=true
//   imported=true exiting=false ok=false output="importHang.test: 1/1 passed"
//
// "runOne drops the markers from its result" (the `imported,` and `exiting,`
// lines deleted from runOne's resolved object).
// "w14ChildPhaseMarkers.test: 1/8 passed":
//   x a clean file with a result export: both markers, and neither survives
//   in the output: expected imported and exiting, got timedOut=false
//   imported=undefined exiting=undefined ok=true
//   output="cleanResult.test: 1/1 passed"
//
// "the markers are left in the output" (`.replace(MARKER_LINES, "")` deleted).
// "w14ChildPhaseMarkers.test: 4/8 passed":
//   x a clean file with a result export: both markers, and neither survives
//   in the output: the markers must be stripped from the output, got
//   "cleanResult.test: 1/1 passed\n__IMPORTED__\n__EXITING__"
//
// "timeoutPhase ignores the markers" (always the after-tally phase).
// "w14ChildPhaseMarkers.test: 6/8 passed":
//   x process.exit() itself never returned -> exit-hung, with the tally still
//   recovered: got import-pending: timedOut=true imported=true exiting=true
//   ok=false output="exitHang.test: 1/1 passed"
//
// "shouldRetry retries only the import-pending phase".
// "w14ChildPhaseMarkers.test: 7/8 passed":
//   x every post-tally freeze is retryable, from the result runOne itself
//   returned: import-resolved froze after a clean tally and must be retried

import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// run-tests.mjs is a plain untyped .mjs script outside tsconfig's `include`;
// the same dynamic-import trick runTests.test.ts uses for the same file.
interface RunOneResult {
  file: string;
  counts: { passed: number; failed: number; total: number } | null;
  byExit: boolean;
  ok: boolean;
  timedOut: boolean;
  output: string;
  imported: boolean;
  exiting: boolean;
}
interface Phase {
  id: string;
  prose: string;
}
interface RunTestsModule {
  runOne(file: string, timeoutMs?: number): Promise<RunOneResult>;
  timeoutPhase(r: { output: string; imported?: boolean; exiting?: boolean }): Phase;
  shouldRetry(r: {
    timedOut: boolean; output: string; imported?: boolean; exiting?: boolean;
  }): boolean;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const RUN_TESTS_URL = new URL("../../run-tests.mjs", import.meta.url).href;
const { runOne, timeoutPhase, shouldRetry } =
  (await nodeImport(RUN_TESTS_URL)) as RunTestsModule;

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const KILL_MS = 6000;

/** What the case saw, in one line, for a failure message. */
function describe(r: RunOneResult): string {
  return `timedOut=${r.timedOut} imported=${r.imported} exiting=${r.exiting} `
    + `ok=${r.ok} output=${JSON.stringify(r.output)}`;
}

let dir: string | undefined;
try {
  dir = mkdtempSync(join(tmpdir(), "astrodeck-w14-markers-"));
  const write = (name: string, lines: string[]): string => {
    const p = join(dir!, name);
    writeFileSync(p, lines.join("\n") + "\n", "utf8");
    return p;
  };

  // ------------------------------------------------- the files that exit
  const cleanResult = write("cleanResult.test.mjs", [
    'console.log("cleanResult.test: 1/1 passed");',
    "export const result = { passed: 1, failed: 0, total: 1 };",
  ]);
  const cleanNoExport = write("cleanNoExport.test.mjs", [
    // No `result` export: the parent reads the printed tally, and the child
    // leaves through the bare `process.exit(0)` at the end of its source.
    'console.log("cleanNoExport.test: 2/2 passed");',
  ]);
  const failing = write("failing.test.mjs", [
    'console.log("failing.test: 1/2 passed");',
    "export const result = { passed: 1, failed: 1, total: 2 };",
  ]);
  const selfExit = write("selfExit.test.mjs", [
    // A file that calls process.exit() itself (four real ones do): the CHILD
    // never reaches its own import return or its own exit call, so neither
    // marker may appear -- they record what the child did, not what the file
    // did.
    'console.log("selfExit.test: 1/1 passed");',
    "process.exit(0);",
  ]);

  const rClean = await runOne(cleanResult);
  test("a clean file with a result export: both markers, and neither survives in the output", () => {
    assert(rClean.ok === true && rClean.timedOut === false, describe(rClean));
    assert(rClean.imported === true && rClean.exiting === true,
      `expected imported and exiting, got ${describe(rClean)}`);
    assert(rClean.output === "cleanResult.test: 1/1 passed",
      `the markers must be stripped from the output, got ${JSON.stringify(rClean.output)}`);
    assert(rClean.counts !== null && rClean.counts.passed === 1 && rClean.counts.failed === 0,
      `the recovered tally must be unchanged, got ${JSON.stringify(rClean.counts)}`);
  });

  const rNoExport = await runOne(cleanNoExport);
  test("a clean file with no result export reaches the bare process.exit(0) and says so", () => {
    assert(rNoExport.ok === true && rNoExport.timedOut === false, describe(rNoExport));
    assert(rNoExport.imported === true && rNoExport.exiting === true,
      `the no-export exit path must write __EXITING__ too, got ${describe(rNoExport)}`);
    assert(rNoExport.output === "cleanNoExport.test: 2/2 passed",
      `got ${JSON.stringify(rNoExport.output)}`);
  });

  const rFailing = await runOne(failing);
  test("a failing file still writes __EXITING__ before its nonzero exit, and is still not green", () => {
    assert(rFailing.ok === false && rFailing.timedOut === false, describe(rFailing));
    assert(rFailing.imported === true && rFailing.exiting === true,
      `got ${describe(rFailing)}`);
    assert(rFailing.output === "failing.test: 1/2 passed",
      `got ${JSON.stringify(rFailing.output)}`);
  });

  const rSelfExit = await runOne(selfExit);
  test("a file that exits itself leaves no marker: the markers record the child, not the file", () => {
    assert(rSelfExit.ok === true && rSelfExit.timedOut === false, describe(rSelfExit));
    assert(rSelfExit.imported === false && rSelfExit.exiting === false,
      `no marker may be written for an exit the child did not make, got ${describe(rSelfExit)}`);
  });

  // ------------------------------------------------- the files that freeze
  const importHang = write("importHang.test.mjs", [
    'console.log("importHang.test: 1/1 passed");',
    "while (true) { /* busy -- the import never returns */ }",
  ]);
  const reportHang = write("reportHang.test.mjs", [
    'console.log("reportHang.test: 1/1 passed");',
    // The child reads `r.failed` right after the import returns; a getter
    // that never returns freezes it AFTER __IMPORTED__ and BEFORE the exit.
    "export const result = { get failed() { while (true) { /* busy */ } } };",
  ]);
  const exitHang = write("exitHang.test.mjs", [
    'console.log("exitHang.test: 1/1 passed");',
    // process.exit() runs its 'exit' listeners synchronously and does not
    // return until they do: a listener that spins is process.exit() hanging.
    'process.on("exit", () => { while (true) { /* busy -- exit never finishes */ } });',
    "export const result = { passed: 1, failed: 0, total: 1 };",
  ]);

  const [rImport, rReport, rExit] = await Promise.all([
    runOne(importHang, KILL_MS),
    runOne(reportHang, KILL_MS),
    runOne(exitHang, KILL_MS),
  ]);

  test("import never returned: killed after the tally with no __IMPORTED__ -> import-pending", () => {
    assert(rImport.timedOut === true && rImport.ok === false, describe(rImport));
    assert(rImport.imported === false && rImport.exiting === false,
      `the child was still inside the import, got ${describe(rImport)}`);
    const phase = timeoutPhase(rImport);
    assert(phase.id === "import-pending", `got ${phase.id}: ${describe(rImport)}`);
    assert(rImport.output === "importHang.test: 1/1 passed",
      `got ${JSON.stringify(rImport.output)}`);
  });

  test("import returned, then the child froze before process.exit -> import-resolved", () => {
    assert(rReport.timedOut === true && rReport.ok === false, describe(rReport));
    assert(rReport.imported === true && rReport.exiting === false,
      `expected imported only, got ${describe(rReport)}`);
    const phase = timeoutPhase(rReport);
    assert(phase.id === "import-resolved", `got ${phase.id}: ${describe(rReport)}`);
  });

  test("process.exit() itself never returned -> exit-hung, with the tally still recovered", () => {
    assert(rExit.timedOut === true && rExit.ok === false, describe(rExit));
    assert(rExit.imported === true && rExit.exiting === true,
      `__EXITING__ must reach the parent before the kill, got ${describe(rExit)}`);
    const phase = timeoutPhase(rExit);
    assert(phase.id === "exit-hung", `got ${phase.id}: ${describe(rExit)}`);
    assert(rExit.output === "exitHang.test: 1/1 passed",
      `the markers must not leak into the printed output, got ${JSON.stringify(rExit.output)}`);
    assert(rExit.counts !== null && rExit.counts.passed === 1 && rExit.counts.failed === 0,
      `the tag the child printed before exit must still be read, got ${JSON.stringify(rExit.counts)}`);
  });

  test("every post-tally freeze is retryable, from the result runOne itself returned", () => {
    for (const [name, r] of [["import-pending", rImport], ["import-resolved", rReport],
                             ["exit-hung", rExit]] as const) {
      assert(shouldRetry(r) === true, `${name} froze after a clean tally and must be retried`);
    }
  });
} finally {
  if (dir) rmSync(dir, { recursive: true, force: true });
}

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`w14ChildPhaseMarkers.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));

export const result = { passed, failed, total };
