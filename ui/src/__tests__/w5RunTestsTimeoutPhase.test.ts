// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// A guard on the test RUNNER itself (`ui/run-tests.mjs`), not on any UI code.
// Companion to runTests.test.ts, which already covers `computeOk`,
// `parseCounts` and `assertionStyle`; this covers the pure functions that
// decide what a timed-out file's captured output says about WHERE the child
// stopped: `splitChildOutput` (reads the child's own phase markers) and
// `timeoutPhase` (turns them, and the printed tally, into a phase).
//
// Issue #614: flowInspectorNotes.test.tsx printed "7/7 passed" and then the
// child never exited, on a loaded CI runner. Issue #664: the same shape on
// other files, and its cause is still unknown. run-tests.mjs used to say only
// "after its own tally -- the default export never resolved", which was a
// GUESS: nothing in the child recorded whether the import had resolved. The
// child now writes two markers to fd 2 with fs.writeSync (a pipe write that
// is on the wire before the next statement runs, so a stall or an exit cannot
// swallow it):
//
//   __IMPORTED__   immediately after `await import(file)` returned
//   __EXITING__    immediately before each `process.exit(...)`
//
// so a kill after the tally lands in one of four places, and the phase id
// names which one:
//
//   no-tally         no tally, and the import never resolved: the cases
//   import-pending   a tally, but no __IMPORTED__: the file's own tail or the
//                    module loader never returned
//   import-resolved  __IMPORTED__ but no __EXITING__: the child's own few
//                    report lines never finished
//   exit-hung        __EXITING__: process.exit() was called and never returned
//
// This is a pure-function unit test, same reasoning as runTests.test.ts: a
// fixture file that actually has to survive TIMEOUT_MS (60s) to prove the
// message text is right would make this guard cost a minute of CI time by
// design. The real-child half (the markers actually reach the parent, and are
// stripped from its output) is w14ChildPhaseMarkers.test.ts.
//
// NAMED MUTANTS (backlog wave 14, WP-97; each applied to run-tests.mjs from a
// byte backup, restored byte-identically, sha256-verified, and checked gone).
// Observed failures, verbatim:
//
// "timeoutPhase ignores the markers" (the four-line tail of timeoutPhase
// replaced with `return hasTally(output) ? PHASES.importPending :
// PHASES.noTally;`, i.e. always the after-tally phase): the process.exit case
// goes red because a child that wrote __EXITING__ reads as "import never
// resolved". "w5RunTestsTimeoutPhase.test: 11/16 passed":
//   x __EXITING__ seen and the child never exited: process.exit() hung: a
//   child that wrote __EXITING__ must read as exit-hung, got import-pending:
//   after its own tally, before its import resolved -- the file's own tail or
//   the module loader never returned
//
// "timeoutPhase ignores the import marker" (`if (!imported) return
// PHASES.importPending;` replaced with `if (true) return
// PHASES.importPending;`): the 'import resolved, process.exit never returned'
// case reads as 'import never resolved' (#614's own question answered the
// wrong way round). "w5RunTestsTimeoutPhase.test: 11/16 passed":
//   x the import marker is what separates 'import never resolved' from
//   'import resolved': expected import-pending then import-resolved, got
//   import-pending then import-pending
//
// "markers are read from stdout as well as stderr" (`imported:
// err.includes(IMPORTED_TAG)` -> `(stdout + err).includes(IMPORTED_TAG)`).
// "w5RunTestsTimeoutPhase.test: 15/16 passed":
//   x a marker word in the file's own STDOUT is not a marker: stdout text must
//   never count as a marker, got imported=true exiting=false
//
// "the markers are left in the output" (`.replace(MARKER_LINES, "")`
// deleted). "w5RunTestsTimeoutPhase.test: 14/16 passed":
//   x the markers are stripped from the output the summary prints: expected
//   only the file's own line to survive, got
//   "x.test: 1/1 passed\n__IMPORTED__\n__EXITING__"
//
// "the marker strip is line-anchored" (MARKER_LINES gains `^` and the `m`
// flag). "w5RunTestsTimeoutPhase.test: 15/16 passed":
//   x a marker glued to the end of a file's own unterminated stderr line is
//   still found and stripped: got "partial__IMPORTED__"
//
// "brokenReason prints the phase object, not its prose"
// (`${timeoutPhase(r).prose}` -> `${timeoutPhase(r)}`).
// "w5RunTestsTimeoutPhase.test: 15/16 passed":
//   x the summary reason for a timeout names where the child stopped, in
//   words: expected the timeout and process.exit named, got: timed out after
//   60s ([object Object])
//
//   Run directly:  npx tsx src/__tests__/w5RunTestsTimeoutPhase.test.ts

interface Phase {
  id: "no-tally" | "import-pending" | "import-resolved" | "exit-hung";
  prose: string;
}
interface ChildSplit {
  output: string;
  tagged: { passed: number; failed: number; total: number } | null;
  imported: boolean;
  exiting: boolean;
}
interface BrokenInput {
  timedOut: boolean;
  counts: { passed: number; failed: number; total: number } | null;
  output: string;
  imported?: boolean;
  exiting?: boolean;
}
interface RunTestsModule {
  splitChildOutput(stdout: string, stderr: string): ChildSplit;
  timeoutPhase(r: { output: string; imported?: boolean; exiting?: boolean }): Phase;
  brokenReason(r: BrokenInput): string;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const RUN_TESTS_URL = new URL("../../run-tests.mjs", import.meta.url).href;
const { splitChildOutput, timeoutPhase, brokenReason } =
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

// What the parent does with a killed child's streams, from CAPTURED text: the
// same splitChildOutput runOne calls, so the markers are read by the code
// that reads them in production, not by a copy of it in this file.
function phaseOfCapture(stdout: string, stderr: string): Phase {
  return timeoutPhase(splitChildOutput(stdout, stderr));
}

// ---------------------------------------------- the two pre-marker shapes
// Output with no markers at all: what a child killed before it imported
// anything looks like, and what every result looked like before #664.

test("a kill after the file's own N/M tally, import not yet resolved, is import-pending", () => {
  const phase = timeoutPhase({ output: "flowInspectorNotes.test: 7/7 passed" });
  assert(phase.id === "import-pending",
    `expected import-pending for a tally with no __IMPORTED__, got ${phase.id}`);
  assert(/after its own tally/.test(phase.prose),
    `the prose must still say the kill came after the tally, got: ${phase.prose}`);
});

test("a kill after the bare 'N passed, M failed' tally reads the same way", () => {
  const phase = timeoutPhase({ output: "legacy.test: 25 passed, 0 failed" });
  assert(phase.id === "import-pending",
    "the two-number tally shape must be recognised too, not just N/M");
});

test("a kill after a throw-on-failure completion phrase reads as after the tally", () => {
  // These files have no numeric tally at all -- "OK" or "all assertions
  // passed" is their only signal -- so timeoutPhase must fall back to
  // assertionStyle rather than reporting every one of them as "during the
  // cases" for lack of a number to parse.
  const phase = timeoutPhase({ output: "some setup log line\nall assertions passed" });
  assert(phase.id === "import-pending",
    "a throw-on-failure file's completion phrase must count as having finished its cases");
});

test("a kill with no tally and no completion phrase reads as inside the cases", () => {
  const phase = timeoutPhase({ output: "setting up fixture...\nrendering...\n" });
  assert(phase.id === "no-tally",
    `expected no-tally for output with no tally, got ${phase.id}`);
  assert(/during its test cases/.test(phase.prose),
    `the prose must say the kill came inside the cases, got: ${phase.prose}`);
});

test("empty output (killed before it ever wrote anything) reads as inside the cases", () => {
  assert(timeoutPhase({ output: "" }).id === "no-tally",
    "a file that produced no output at all must not be misread as having finished");
});

// ------------------------------------------------- the markers (#664 / #614)

test("a tally and __IMPORTED__ but no __EXITING__: the import resolved, the child never reached process.exit", () => {
  const phase = phaseOfCapture("x.test: 3/3 passed\n", "__IMPORTED__\n");
  assert(phase.id === "import-resolved",
    `expected import-resolved, got ${phase.id}: ${phase.prose}`);
  assert(/import resolved/.test(phase.prose),
    `the prose must say the import resolved, got: ${phase.prose}`);
});

test("__EXITING__ seen and the child never exited: process.exit() hung", () => {
  // The hypothesis #664's own brief says to test first: after the tally the
  // child does nothing but resolve the import and call process.exit, and
  // `--import tsx` runs a loader that exit has to tear down.
  const phase = phaseOfCapture(
    "x.test: 3/3 passed\n",
    '__IMPORTED__\n__COUNTS__{"passed":3,"failed":0,"total":3}\n__EXITING__\n',
  );
  assert(phase.id === "exit-hung",
    `a child that wrote __EXITING__ must read as exit-hung, got ${phase.id}: ${phase.prose}`);
  assert(/process\.exit/.test(phase.prose),
    `the prose must name process.exit, got: ${phase.prose}`);
});

test("the import marker is what separates 'import never resolved' from 'import resolved'", () => {
  // Same tally, same output text; only __IMPORTED__ differs.
  const without = phaseOfCapture("x.test: 3/3 passed\n", "");
  const withIt = phaseOfCapture("x.test: 3/3 passed\n", "__IMPORTED__\n");
  assert(without.id === "import-pending" && withIt.id === "import-resolved",
    `expected import-pending then import-resolved, got ${without.id} then ${withIt.id}`);
});

test("a file whose import resolved but printed no scorable tally is NOT 'during its test cases'", () => {
  // __IMPORTED__ is proof the file's own top level (its cases) finished,
  // whatever it did or did not print. Reading "no tally" as "inside the
  // cases" there would send the next debugger to the wrong place.
  const phase = phaseOfCapture("", "__IMPORTED__\n");
  assert(phase.id === "import-resolved",
    `expected import-resolved (the import returned), got ${phase.id}: ${phase.prose}`);
});

test("no tally and no markers is no-tally even when the output is long", () => {
  const phase = phaseOfCapture("rendering...\n".repeat(50), "warn: something\n");
  assert(phase.id === "no-tally", `expected no-tally, got ${phase.id}`);
});

// ------------------------------------------------------- splitChildOutput

test("the markers are stripped from the output the summary prints", () => {
  const split = splitChildOutput(
    "x.test: 1/1 passed\n",
    '__IMPORTED__\n__COUNTS__{"passed":1,"failed":0,"total":1}\n__EXITING__\n',
  );
  assert(split.output === "x.test: 1/1 passed",
    `expected only the file's own line to survive, got ${JSON.stringify(split.output)}`);
  assert(split.imported === true && split.exiting === true,
    `expected both markers read, got imported=${split.imported} exiting=${split.exiting}`);
  assert(split.tagged !== null && split.tagged.passed === 1 && split.tagged.failed === 0,
    `the __COUNTS__ tag must still be parsed, got ${JSON.stringify(split.tagged)}`);
});

test("a marker glued to the end of a file's own unterminated stderr line is still found and stripped", () => {
  // `process.stderr.write("partial")` with no newline, then the child's own
  // marker: the two share a line, so a line-anchored read would lose it.
  const split = splitChildOutput("", "partial__IMPORTED__\n");
  assert(split.imported === true, "an unanchored marker must still be read");
  assert(split.output === "partial", `got ${JSON.stringify(split.output)}`);
});

test("a marker word in the file's own STDOUT is not a marker", () => {
  // Only fd 2 carries the child's markers. A test file that happens to
  // print the word (this one's own failure messages can) must not make a
  // frozen child read as having reached process.exit.
  const split = splitChildOutput("__EXITING__\n__IMPORTED__\n", "");
  assert(split.imported === false && split.exiting === false,
    `stdout text must never count as a marker, got imported=${split.imported} exiting=${split.exiting}`);
  assert(split.output.includes("__EXITING__"),
    "stdout is the file's own text and is kept verbatim");
});

test("a child with no markers reads as neither imported nor exiting", () => {
  const split = splitChildOutput("x.test: 1/1 passed\n", "");
  assert(split.imported === false && split.exiting === false && split.tagged === null,
    `expected a bare read, got ${JSON.stringify(split)}`);
});

// ------------------------------------------------------------ brokenReason
// The sentence the final summary prints for a file that is not green. It is
// the only place the phase reaches a human when a retry did not rescue the
// file, and `main` only prints it on a real failure, so without a case here a
// slip (the phase object interpolated instead of its prose) would first show
// up in the middle of the next broken CI run.

test("the summary reason for a timeout names where the child stopped, in words", () => {
  const clean = { passed: 1, failed: 0, total: 1 };
  const exitHung = brokenReason({
    timedOut: true, counts: clean, output: "x.test: 1/1 passed", imported: true, exiting: true,
  });
  assert(/^timed out after 60s \(/.test(exitHung) && /process\.exit/.test(exitHung),
    `expected the timeout and process.exit named, got: ${exitHung}`);
  assert(!/\[object/.test(exitHung), `the phase must print as prose, got: ${exitHung}`);
  const pending = brokenReason({ timedOut: true, counts: clean, output: "x.test: 1/1 passed" });
  assert(/before its import resolved/.test(pending), `got: ${pending}`);
  const inCases = brokenReason({ timedOut: true, counts: null, output: "rendering..." });
  assert(/during its test cases/.test(inCases), `got: ${inCases}`);
});

test("a result that did not time out keeps its own reasons", () => {
  assert(brokenReason({ timedOut: false, counts: { passed: 1, failed: 2, total: 3 }, output: "" })
    === "2 failed", "a tally with failures reads as N failed");
  assert(/no pass\/fail tally/.test(brokenReason({ timedOut: false, counts: null, output: "" })),
    "an unscorable file keeps its own reason");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`w5RunTestsTimeoutPhase.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));

export const result = { passed, failed, total };
