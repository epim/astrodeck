// A guard on the test RUNNER itself (`ui/run-tests.mjs`), not on any UI code.
// Companion to runTests.test.ts, which already covers `computeOk`,
// `parseCounts` and `assertionStyle`; this covers the function WP-69 (a)
// added for issue #614: `timeoutPhase`, which decides what a timed-out
// file's printed output says about WHERE it hung.
//
// Issue #614: flowInspectorNotes.test.tsx printed "7/7 passed" and then the
// child never exited, on a loaded CI runner. run-tests.mjs's broken-file
// message used to say only "timed out after 60s" for every timeout, whether
// the file never got as far as a tally (a hang inside the cases) or printed
// one and then hung in whatever runs after them (a hang in the file's own
// import() resolving, which is the #614 shape). Those are different bugs and
// want different debugging, so the message now says which.
//
// This is a pure-function unit test, same reasoning as runTests.test.ts: a
// fixture file that actually has to survive TIMEOUT_MS (60s) to prove the
// message text is right would make this guard cost a minute of CI time by
// design, for a message that is otherwise just string formatting.
//
//   Run directly:  npx tsx src/__tests__/w5RunTestsTimeoutPhase.test.ts

interface RunTestsModule {
  timeoutPhase(output: string): string;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const RUN_TESTS_URL = new URL("../../run-tests.mjs", import.meta.url).href;
const { timeoutPhase } = (await nodeImport(RUN_TESTS_URL)) as RunTestsModule;

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

test("a kill after the file's own N/M tally reads as the export never resolving", () => {
  const output = "flowInspectorNotes.test: 7/7 passed";
  assert(/after its own tally/.test(timeoutPhase(output)),
    `expected the after-tally phase for output ${JSON.stringify(output)}, got ${timeoutPhase(output)}`);
});

test("a kill after the bare 'N passed, M failed' tally reads the same way", () => {
  const output = "legacy.test: 25 passed, 0 failed";
  assert(/after its own tally/.test(timeoutPhase(output)),
    "the two-number tally shape must be recognised too, not just N/M");
});

test("a kill after a throw-on-failure completion phrase reads as after the tally", () => {
  // These files have no numeric tally at all -- "OK" or "all assertions
  // passed" is their only signal -- so timeoutPhase must fall back to
  // assertionStyle rather than reporting every one of them as "during the
  // cases" for lack of a number to parse.
  const output = "some setup log line\nall assertions passed";
  assert(/after its own tally/.test(timeoutPhase(output)),
    "a throw-on-failure file's completion phrase must count as having finished its cases");
});

test("a kill with no tally and no completion phrase reads as inside the cases", () => {
  const output = "setting up fixture...\nrendering...\n";
  assert(/during its test cases/.test(timeoutPhase(output)),
    `expected the during-cases phase for output with no tally, got ${timeoutPhase(output)}`);
});

test("empty output (killed before it ever wrote anything) reads as inside the cases", () => {
  assert(/during its test cases/.test(timeoutPhase("")),
    "a file that produced no output at all must not be misread as having finished");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`w5RunTestsTimeoutPhase.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));

export const result = { passed, failed, total };
