// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15ChildExitReport.test.ts - the REAL-CHILD guard on two things run-tests.mjs
// now does with the way a child ENDS (#706, #664 Part B). Companion to
// w14ChildPhaseMarkers.test.ts (where the child's markers come from) and to
// w5RunTestsTimeoutPhase.test.ts / w13CleanTallyTimeoutRetry.test.ts (the pure
// wording and the retry wiring).
//
//   Run directly:  npx tsx src/__tests__/w15ChildExitReport.test.ts
//
// #706. A child that dies silently (empty output, no timeout) used to be
// reported as "no pass/fail tally in its output", and `runOne` threw away the
// only evidence of how it died: err.code, err.signal and the exit event. They
// are kept on the result now (`exitCode`, `signal`, `exitEvent`) and the
// summary's sentence names them.
//
// #664 Part B. Wave 14's markers showed a freeze in the `exit-hung` phase:
// `__EXITING__` reached the parent and the child still did not finish for
// 60 s. execFile's callback waits for the stdio pipes as well as the exit, so
// two different things look identical there:
//
//   * process.exit() was called and the process NEVER EXITED (an 'exit'
//     listener or a tail of the exit path that does not return);
//   * the process EXITED and something else still holds its stdio pipes open,
//     so the 'close' event never comes.
//
// `runOne` now listens for the child's own 'exit' event and records whether it
// had fired when the runner pulled the trigger (`exitedBeforeKill`), which is
// the discriminator, and, when asked (`exitGraceMs`), kills a child that has
// said `__EXITING__` and has still not finished a few seconds later
// (`graceKilled`). A child killed that way after a CLEAN tally counts as
// passed, with a note in the summary (`gracedSummaryLine`): the file's own
// result was already in, and the freeze is the runner's problem, not the
// file's.
//
// Real children, not captured text, for the same reason w14 gives: a pure
// test of the wording passes against a runner that never kills anything. Each
// fixture freezes at ONE chosen point and the runs go CONCURRENTLY, so this
// file costs about one grace period of wall time. The grace and the timeout
// are far apart (GRACE_MS against LONG_MS) so a loaded box that delays the
// child's bootstrap cannot make the timeout win the race with the grace kill
// (#669, #675: no assertion leans on a short fixed delay), and the one case
// that must NOT be killed (a slow but clean exit) has a grace many times the
// time it needs.
//
// NAMED MUTANTS (backlog wave 15, WP-115; each applied to run-tests.mjs from a
// byte backup, restored byte-identically, sha256-verified, and checked gone).
// Observed failures, verbatim, are recorded below the cases that catch them.
//
// "runOne drops the exit code" (`exitCode: err ? (err.code ?? null) : 0,` ->
// `exitCode: undefined,`). "w15ChildExitReport.test: 12/19 passed":
//   x a clean child reports exit code 0, no signal, and the exit event it
//   made: expected exit code 0 and no signal, got timedOut=false
//   graceKilled=false ok=true exitCode=undefined signal=null ...
//
// "runOne drops the signal" (`signal: err?.signal ?? exitEvent?.signal ??
// null,` -> `signal: null,`). "17/19 passed":
//   x process.exit() called and the process never exited: grace-killed, exit
//   event NOT seen: err.signal must be kept: the grace kill is a SIGKILL, got
//   ... exitCode=null signal=null exitEvent={"code":null,"signal":"SIGKILL"}
//
// "the exit event is never recorded" (the 'exit' listener's body emptied).
// "14/19 passed":
//   x a clean child reports exit code 0, ...: the exit event must be kept, got
//   ... exitCode=0 signal=null exitEvent=null ...
//
// "the discriminator is hard-wired" (`exitedBeforeKill = exitEvent !== null;`
// -> `exitedBeforeKill = false;`). "16/19 passed":
//   x the process exited and its stdio pipes were held open: grace-killed,
//   exit event seen: the process HAD exited (only its pipes were held), so the
//   discriminator must say so: ... exitedBeforeKill=false ...
//
// "the grace kill is never armed" (`if (exitGraceMs > 0) {` -> `if (false) {`).
// "12/19 passed" (w13CleanTallyTimeoutRetry.test: 18/20):
//   x process.exit() called and the process never exited: grace-killed, exit
//   event NOT seen: the grace kill, not the timeout, must have ended it:
//   timedOut=true graceKilled=false ok=false exitCode=null signal=SIGTERM ...
//
// "a grace kill is recorded as a timeout" (`const timedOut = endedBy ===
// "timeout";` -> `endedBy !== null`). "12/19 passed":
//   x process.exit() called and the process never exited: ...: the grace kill,
//   not the timeout, must have ended it: timedOut=true graceKilled=true
//   ok=false ... signal=SIGKILL ...
//
// "the old timeout rule" (the same line -> `!!err && err.killed`, which is
// what `runOne` used before this change). "10/19 passed": the cases above, and
//   x a child stopped by the runner's own buffer limit is not reported as a
//   timeout: err.killed is set by the buffer kill too, and that is not a
//   timeout: timedOut=undefined graceKilled=false ok=false
//   exitCode=ERR_CHILD_PROCESS_STDIO_MAXBUFFER ...
//   x a child that exited but whose pipes were held is a timeout, not a silent
//   pass: waiting out the whole timeout for a pipe and then passing is the
//   silence #664 is about: timedOut=false graceKilled=false ok=true exitCode=0
//
// "the grace period is zero" (the grace timer's `exitGraceMs` delay -> `0`).
// "14/19 passed":
//   x a child that merely takes a moment to exit is not grace-killed: a 400ms
//   exit inside a 8000ms grace is healthy: timedOut=false graceKilled=true
//   ok=true exitCode=null signal=SIGKILL ...
//
// "computeOk ignores graceKilled" (`if (err && !graceKilled) return false;` ->
// `if (err) return false;`). "15/19 passed":
//   x a child killed in the grace window after a clean tally counts as passed,
//   tally intact: never-exits: a clean tally then a hung exit is
//   passed-with-a-note, not red: timedOut=false graceKilled=true ok=false ...
//
// "a grace kill turns a failing tally green" (computeOk's last line gains
// `graceKilled ||`). "15/19 passed":
//   x a failing tally stays failing when the exit then hangs: 1 of 2 failed:
//   the grace kill must never turn it green, timedOut=false graceKilled=true
//   ok=true ...
//
// "a grace-killed throw-on-failure file is not passed" (`byExit = (!err ||
// graceKilled) && ...` -> `byExit = !err && ...`). "18/19 passed":
//   x a throw-on-failure file with a hung exit is passed too, with no
//   assertion count invented: it reached process.exit() through a clean
//   import: timedOut=false graceKilled=true ok=false ...
//
// "the watcher does not need __IMPORTED__" (exitingWatcher's `let sawImported
// = false;` -> `true`). "18/19 passed" (w5RunTestsTimeoutPhase.test: 28/30):
//   x a file that only prints the exit marker while it is still running is not
//   killed: the clock starts at the CHILD's marker pair, never at a word in
//   the file's own output: timedOut=false graceKilled=true ok=false ...
//   imported=false exiting=true output=""
//
// "the kill does not close the pipes" (the two `.destroy()` lines in `end`
// deleted). "15/19 passed", and in 30 s, not the 120 s the grandchild lives:
//   x the process exited and its stdio pipes were held open: ...: the grace
//   kill, not the timeout, must have ended it: ... output="(pipesHeld: runOne
//   did not return within 30000ms)"
//
// "the grace kill is a SIGTERM" (`child.kill(why === "grace" ? "SIGKILL" :
// "SIGTERM")` -> `child.kill("SIGTERM")`). "18/19 passed":
//   x process.exit() called and the process never exited: ...: err.signal must
//   be kept: the grace kill is a SIGKILL, got ... signal=SIGTERM ...
//
// "the timeout timer is not cleared" and "the grace timer is not cleared" (the
// `clearTimeout(timer);` / `clearTimeout(graceTimer);` line in the callback
// deleted, one each). Both "18/19 passed":
//   x a run that ends on its own leaves neither its timeout nor its grace
//   timer armed: 1 timer(s) outlived the run: both are minutes long and would
//   hold the runner open
//
// "gracedSummaryLine finds nothing" (`const killed = ...` -> `[]`). "18/19
// passed":
//   x a grace-killed pass is named in the summary, with which kind of hang it
//   was: a passed-with-a-note result must reach the summary, never silently
//
// "the summary note omits the kind" and "the kinds are swapped" (`graceKind`).
// Both "18/19 passed":
//   x a grace-killed pass is named in the summary, ...: each file must carry
//   ITS kind in the note, got: 2 file(s) printed a clean tally, called
//   process.exit() and were killed 1.5s later because the child never
//   finished -- counted as passed (#664): <neverExits.test.mjs>, ...

import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
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
  exitCode: number | string | null;
  signal: string | null;
  exitEvent: { code: number | null; signal: string | null } | null;
  exitedBeforeKill: boolean | null;
  graceKilled: boolean;
}
interface RunTestsModule {
  runOne(file: string, timeoutMs?: number, opts?: { exitGraceMs?: number }): Promise<RunOneResult>;
  brokenReason(r: RunOneResult): string;
  gracedSummaryLine(results: RunOneResult[]): string | null;
  computeOk(a: {
    counts: { passed: number; failed: number; total: number } | null;
    byExit: boolean;
    err: unknown;
    timedOut: boolean;
    graceKilled?: boolean;
  }): boolean;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const RUN_TESTS_URL = new URL("../../run-tests.mjs", import.meta.url).href;
const { runOne, brokenReason, gracedSummaryLine, computeOk } =
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

/** What the case saw, in one line, for a failure message. */
function describe(r: RunOneResult): string {
  return `timedOut=${r.timedOut} graceKilled=${r.graceKilled} ok=${r.ok} `
    + `exitCode=${r.exitCode} signal=${r.signal} exitEvent=${JSON.stringify(r.exitEvent)} `
    + `exitedBeforeKill=${r.exitedBeforeKill} imported=${r.imported} exiting=${r.exiting} `
    + `output=${JSON.stringify(r.output)}`;
}

/** Races a run against a deadline, so a runner that never lets go of a child
 *  fails the case with a message instead of hanging the whole file. The
 *  stand-in carries the text, and every flag says "nothing good happened". The
 *  timer is cleared either way: a pending one would outlive the file. */
function within(run: Promise<RunOneResult>, ms: number, label: string): Promise<RunOneResult> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<RunOneResult>((resolve) => {
    timer = setTimeout(() => resolve({
      file: label, counts: null, byExit: false, ok: false, timedOut: false,
      output: `(${label}: runOne did not return within ${ms}ms)`, imported: false,
      exiting: false, exitCode: null, signal: null, exitEvent: null,
      exitedBeforeKill: null, graceKilled: false,
    }), ms);
  });
  return Promise.race([run, late]).finally(() => clearTimeout(timer));
}

// The grace the frozen fixtures are given, and the timeout that must NOT be
// what ends them. LONG_MS is far above GRACE_MS plus a loaded box's child
// bootstrap, so a result with timedOut=true means the grace kill did not
// happen, not that the box was slow.
const GRACE_MS = 1500;
const LONG_MS = 40_000;
// The most a run that SHOULD end by the grace kill is allowed before the case
// calls the runner hung: above LONG_MS would be the timeout winning, so below.
const DEADLINE_MS = 30_000;
// What the slow-but-clean exit needs, against the grace it is given: the case
// is only meaningful while the grace is many times the need.
const SLOW_EXIT_BUSY_MS = 400;
const SLOW_EXIT_GRACE_MS = 8000;
// The timeout for the two cases that WANT the timeout to be what ends the child
// (no grace asked for). The child must have got as far as `__EXITING__` before
// it, or the phase reads earlier and the case fails for a reason that is about
// the box, not the runner: w14 uses 6000 for the same reason, and on a box
// saturated by parallel agents (2026-10-07) a lone child's bootstrap alone
// took more than 3 s, so this is wider.
const KILL_MS = 10_000;
// How long the marker-word-only file keeps running after it says the word: well
// past GRACE_MS, so a runner that wrongly started the clock would kill it
// while it is still working.
const SAYS_MARKER_RUNS_MS = 4000;

let dir: string | undefined;
const heldPidFiles: string[] = [];
try {
  dir = mkdtempSync(join(tmpdir(), "astrodeck-w15-exit-"));
  const write = (name: string, lines: string[]): string => {
    const p = join(dir!, name);
    writeFileSync(p, lines.join("\n") + "\n", "utf8");
    return p;
  };
  const BUSY = "while (true) { /* busy -- never returns */ }";

  // -------------------------------------------- the children that end alone
  const cleanFile = write("clean.test.mjs", [
    'console.log("clean.test: 1/1 passed");',
    "export const result = { passed: 1, failed: 0, total: 1 };",
  ]);
  // A file that calls process.exit itself with a code and prints nothing: the
  // shape #706 saw (empty output, no timeout, no tally) with a code to name.
  const exit3File = write("exit3.test.mjs", ["process.exit(3);"]);
  const exit0File = write("exit0.test.mjs", ["process.exit(0);"]);

  // ------------------------------------------ the children that freeze late
  // process.exit() runs its 'exit' listeners synchronously and does not return
  // until they do: a listener that spins is "process.exit() was called and the
  // process never exited".
  const neverExits = write("neverExits.test.mjs", [
    'console.log("neverExits.test: 1/1 passed");',
    `process.on("exit", () => { ${BUSY} });`,
    "export const result = { passed: 1, failed: 0, total: 1 };",
  ]);
  // The other half: the child DOES exit, and a detached grandchild that
  // inherited its stdout/stderr keeps the pipes open, so 'close' never comes.
  // The grandchild's pid goes to a file so the test can end it (it is given a
  // long life on purpose: the runner must not be waiting for it to finish).
  const heldFixture = (name: string): string => {
    const pidFile = join(dir!, `${name}.pid`);
    heldPidFiles.push(pidFile);
    return write(`${name}.test.mjs`, [
      'import { spawn } from "node:child_process";',
      'import { writeFileSync } from "node:fs";',
      `console.log(${JSON.stringify(`${name}.test: 1/1 passed`)});`,
      "const gc = spawn(process.execPath, [\"-e\", \"setTimeout(() => {}, 120000)\"],",
      '  { stdio: ["ignore", "inherit", "inherit"], detached: true, windowsHide: true });',
      `writeFileSync(${JSON.stringify(pidFile)}, String(gc.pid));`,
      "gc.unref();",
      "export const result = { passed: 1, failed: 0, total: 1 };",
    ]);
  };
  const pipesHeld = heldFixture("pipesHeld");
  // The same child with NO grace asked for: the timeout ends it, and execFile
  // reports no error for a process that exited 0, so the result must not
  // read as the pass it used to be after the whole wait.
  const pipesHeldNoGrace = heldFixture("pipesHeldNoGrace");
  // More output than the runner buffers (its maxBuffer is 8 MiB): execFile
  // kills the child itself and sets err.killed, which is NOT a timeout.
  const flood = write("flood.test.mjs", [
    'const chunk = "x".repeat(1 << 20);',
    "for (let i = 0; i < 10; i++) process.stdout.write(chunk);",
  ]);
  // A clean file whose exit simply takes a little while (an 'exit' listener
  // that does some work). It must NOT be mistaken for a hung exit.
  const slowExit = write("slowExit.test.mjs", [
    'console.log("slowExit.test: 1/1 passed");',
    `process.on("exit", () => { const end = Date.now() + ${SLOW_EXIT_BUSY_MS}; while (Date.now() < end) { /* work */ } });`,
    "export const result = { passed: 1, failed: 0, total: 1 };",
  ]);
  // A throw-on-failure file (no tally, no export, only the completion phrase)
  // with a hung exit: it reached process.exit(0) through a clean import, so it
  // is as passed as one that exits.
  const phraseNeverExits = write("phraseNeverExits.test.mjs", [
    'console.log("all assertions passed");',
    `process.on("exit", () => { ${BUSY} });`,
  ]);
  // A FAILING tally in a hung exit: the kill must not turn it green.
  const failingNeverExits = write("failingNeverExits.test.mjs", [
    'console.log("failingNeverExits.test: 1/2 passed");',
    `process.on("exit", () => { ${BUSY} });`,
    "export const result = { passed: 1, failed: 1, total: 2 };",
  ]);
  // No export and nothing printed, then a hung exit: reaching process.exit()
  // proves nothing about the file's cases, so this is not a pass.
  const silentNeverExits = write("silentNeverExits.test.mjs", [
    `process.on("exit", () => { ${BUSY} });`,
  ]);

  // A file that only SAYS the exit marker on its own stderr, while its cases
  // are still running. It is not exiting, so it must not be killed.
  const saysMarker = write("saysMarker.test.mjs", [
    'console.error("__EXITING__");',
    `await new Promise((resolve) => setTimeout(resolve, ${SAYS_MARKER_RUNS_MS}));`,
    'console.log("saysMarker.test: 1/1 passed");',
    "export const result = { passed: 1, failed: 0, total: 1 };",
  ]);

  const started = Date.now();
  const [rClean, rExit3, rExit0, rNever, rHeld, rSlow, rFailing, rSilent, rNoGrace,
    rHeldNoGrace, rFlood, rSays, rPhrase] =
    await Promise.all([
      runOne(cleanFile),
      runOne(exit3File),
      runOne(exit0File),
      runOne(neverExits, LONG_MS, { exitGraceMs: GRACE_MS }),
      // The two runs that can hang the runner itself if a kill does not
      // release the pipes, so each has a deadline of its own.
      within(runOne(pipesHeld, LONG_MS, { exitGraceMs: GRACE_MS }), DEADLINE_MS, "pipesHeld"),
      runOne(slowExit, LONG_MS, { exitGraceMs: SLOW_EXIT_GRACE_MS }),
      runOne(failingNeverExits, LONG_MS, { exitGraceMs: GRACE_MS }),
      runOne(silentNeverExits, LONG_MS, { exitGraceMs: GRACE_MS }),
      // No grace asked for: the raw freeze, ended by the timeout, with the
      // same discriminator recorded at THAT kill.
      runOne(neverExits, KILL_MS),
      // Same: with the pipes held and nothing to release them, only the
      // timeout kill closing them lets this run return.
      within(runOne(pipesHeldNoGrace, KILL_MS), DEADLINE_MS, "pipesHeldNoGrace"),
      runOne(flood),
      runOne(saysMarker, LONG_MS, { exitGraceMs: GRACE_MS }),
      runOne(phraseNeverExits, LONG_MS, { exitGraceMs: GRACE_MS }),
    ]);
  const elapsedMs = Date.now() - started;

  // ------------------------------------------------------------ #706 cases
  test("a clean child reports exit code 0, no signal, and the exit event it made", () => {
    assert(rClean.ok === true, describe(rClean));
    assert(rClean.exitCode === 0 && rClean.signal === null,
      `expected exit code 0 and no signal, got ${describe(rClean)}`);
    assert(rClean.exitEvent !== null && rClean.exitEvent.code === 0 && rClean.exitEvent.signal === null,
      `the exit event must be kept, got ${describe(rClean)}`);
    assert(rClean.graceKilled === false && rClean.exitedBeforeKill === null,
      `a child nobody killed has no kill to describe, got ${describe(rClean)}`);
  });

  test("a child that exits 3 with no output keeps exit code 3 on its result", () => {
    assert(rExit3.ok === false && rExit3.timedOut === false && rExit3.counts === null,
      `an unscorable silent exit is not green: ${describe(rExit3)}`);
    assert(rExit3.exitCode === 3,
      `err.code must be kept: expected exit code 3, got ${describe(rExit3)}`);
    assert(rExit3.signal === null, `no signal was involved: ${describe(rExit3)}`);
    assert(rExit3.exitEvent !== null && rExit3.exitEvent.code === 3,
      `the exit event must be kept too: ${describe(rExit3)}`);
  });

  test("the summary names the exit code of a silently dead child", () => {
    const why = brokenReason(rExit3);
    assert(/no pass\/fail tally/.test(why),
      `the reason is still the no-tally one, got: ${why}`);
    assert(/exited with code 3/.test(why),
      `the reason must say which code the child died with, got: ${why}`);
    assert(!/undefined|null/.test(why), `no field may print as a word, got: ${why}`);
  });

  test("a silent clean exit is named as one, not left as an unexplained missing tally", () => {
    const why = brokenReason(rExit0);
    assert(/exited with code 0/.test(why) && /no pass\/fail tally/.test(why),
      `exit 0 with no tally must say so, got: ${why}`);
  });

  // ------------------------------------------- #664 B: the discriminator
  test("process.exit() called and the process never exited: grace-killed, exit event NOT seen", () => {
    assert(rNever.graceKilled === true && rNever.timedOut === false,
      `the grace kill, not the timeout, must have ended it: ${describe(rNever)}`);
    assert(rNever.exiting === true && rNever.imported === true,
      `the child said it was exiting before it was killed: ${describe(rNever)}`);
    assert(rNever.exitedBeforeKill === false,
      `the process had NOT exited when it was killed, so the discriminator must say so: ${describe(rNever)}`);
    assert(rNever.signal === "SIGKILL",
      `err.signal must be kept: the grace kill is a SIGKILL, got ${describe(rNever)}`);
    assert(elapsedMs < LONG_MS,
      `the runs took ${elapsedMs}ms: the grace kill never fired and the timeout ended them`);
  });

  test("the process exited and its stdio pipes were held open: grace-killed, exit event seen", () => {
    assert(rHeld.graceKilled === true && rHeld.timedOut === false,
      `the grace kill, not the timeout, must have ended it: ${describe(rHeld)}`);
    assert(rHeld.exitedBeforeKill === true,
      `the process HAD exited (only its pipes were held), so the discriminator must say so: ${describe(rHeld)}`);
    assert(rHeld.exitEvent !== null && rHeld.exitEvent.code === 0,
      `the exit event the child made must be kept: ${describe(rHeld)}`);
  });

  test("a child killed in the grace window after a clean tally counts as passed, tally intact", () => {
    for (const [name, r] of [["never-exits", rNever], ["pipes-held", rHeld]] as const) {
      assert(r.ok === true,
        `${name}: a clean tally then a hung exit is passed-with-a-note, not red: ${describe(r)}`);
      assert(r.counts !== null && r.counts.passed === 1 && r.counts.failed === 0,
        `${name}: the tally the file printed must survive the kill: ${JSON.stringify(r.counts)}`);
      assert(!/__EXITING__|__IMPORTED__/.test(r.output),
        `${name}: the markers must not leak into the output: ${JSON.stringify(r.output)}`);
    }
  });

  test("a child that merely takes a moment to exit is not grace-killed", () => {
    assert(rSlow.graceKilled === false && rSlow.timedOut === false && rSlow.ok === true,
      `a ${SLOW_EXIT_BUSY_MS}ms exit inside a ${SLOW_EXIT_GRACE_MS}ms grace is healthy: ${describe(rSlow)}`);
    assert(rSlow.exitCode === 0 && rSlow.exitedBeforeKill === null,
      `it exited on its own: ${describe(rSlow)}`);
  });

  test("a file that only prints the exit marker while it is still running is not killed", () => {
    assert(rSays.graceKilled === false && rSays.timedOut === false && rSays.ok === true,
      `the clock starts at the CHILD's marker pair, never at a word in the file's own output: ${describe(rSays)}`);
    assert(rSays.exitCode === 0, `it ran to the end and exited on its own: ${describe(rSays)}`);
  });

  test("a failing tally stays failing when the exit then hangs", () => {
    assert(rFailing.graceKilled === true && rFailing.timedOut === false,
      `the hung exit is still killed: ${describe(rFailing)}`);
    assert(rFailing.ok === false,
      `1 of 2 failed: the grace kill must never turn it green, ${describe(rFailing)}`);
    assert(brokenReason(rFailing) === "1 failed", `got: ${brokenReason(rFailing)}`);
  });

  test("a throw-on-failure file with a hung exit is passed too, with no assertion count invented", () => {
    assert(rPhrase.graceKilled === true && rPhrase.ok === true,
      `it reached process.exit() through a clean import: ${describe(rPhrase)}`);
    assert(rPhrase.counts === null && rPhrase.byExit === true,
      `it told us no count, so none may be made up: ${describe(rPhrase)}`);
  });

  test("a hung exit with no tally at all is not a pass, and the reason says why it was killed", () => {
    assert(rSilent.graceKilled === true && rSilent.ok === false,
      `reaching process.exit() proves nothing about a file that printed nothing: ${describe(rSilent)}`);
    const why = brokenReason(rSilent);
    assert(/no pass\/fail tally/.test(why) && /grace/.test(why),
      `the reason must name the grace kill, got: ${why}`);
  });

  test("with no grace asked for, the raw freeze is ended by the timeout and still says which kind", () => {
    assert(rNoGrace.timedOut === true && rNoGrace.graceKilled === false && rNoGrace.ok === false,
      `without exitGraceMs the child is left to the timeout: ${describe(rNoGrace)}`);
    assert(rNoGrace.exiting === true && rNoGrace.exitedBeforeKill === false,
      `the timeout kill must record the discriminator too: ${describe(rNoGrace)}`);
    assert(rNoGrace.signal === "SIGTERM",
      `err.signal must be kept: the timeout kill is a SIGTERM, got ${describe(rNoGrace)}`);
    const why = brokenReason(rNoGrace);
    assert(/never exited/.test(why),
      `the reason must say the process never exited, got: ${why}`);
  });

  test("a child that exited but whose pipes were held is a timeout, not a silent pass", () => {
    assert(rHeldNoGrace.timedOut === true && rHeldNoGrace.ok === false,
      `waiting out the whole timeout for a pipe and then passing is the silence #664 is about: ${describe(rHeldNoGrace)}`);
    assert(rHeldNoGrace.exitedBeforeKill === true,
      `the process HAD exited, only its pipes were held: ${describe(rHeldNoGrace)}`);
    assert(/stdio pipes never closed/.test(brokenReason(rHeldNoGrace)),
      `the reason must name the held pipes, got: ${brokenReason(rHeldNoGrace)}`);
  });

  test("a child stopped by the runner's own buffer limit is not reported as a timeout", () => {
    assert(rFlood.timedOut === false && rFlood.graceKilled === false && rFlood.ok === false,
      `err.killed is set by the buffer kill too, and that is not a timeout: ${describe(rFlood)}`);
    assert(rFlood.exitCode === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER",
      `err.code names what stopped it: ${describe(rFlood)}`);
    const why = brokenReason(rFlood);
    assert(/MAXBUFFER/.test(why) && !/timed out/.test(why), `got: ${why.slice(0, 200)}`);
  });

  // ------------------------------------------------ the summary's own note
  test("a grace-killed pass is named in the summary, with which kind of hang it was", () => {
    const line = gracedSummaryLine([rClean, rNever, rHeld]);
    assert(line !== null, "a passed-with-a-note result must reach the summary, never silently");
    assert(line!.includes("2 file(s)") && line!.includes("#664"), `got: ${line}`);
    assert(line!.includes("neverExits.test.mjs") && line!.includes("pipesHeld.test.mjs"),
      `both killed files must be named, got: ${line}`);
    assert(!line!.includes("clean.test.mjs"), `a file nobody killed is not in the note: ${line}`);
    assert(/neverExits\.test\.mjs \[process-never-exited\]/.test(line!)
        && /pipesHeld\.test\.mjs \[stdio-never-closed\]/.test(line!),
      `each file must carry ITS kind in the note, got: ${line}`);
  });

  test("the summary note is absent when nothing was grace-killed, and omits a kill that failed", () => {
    assert(gracedSummaryLine([rClean, rSlow]) === null,
      "a run with no grace kill must print nothing");
    assert(gracedSummaryLine([rFailing, rSilent]) === null,
      "a grace-killed file that is NOT green belongs in the broken list, not the passed-with-a-note line");
  });

  // ------------------------------------------------- no timer left behind
  // runOne owns its two timers now (execFile's own timeout used to). One left
  // armed would hold the runner's process open for a whole TIMEOUT_MS after
  // the last file, on every run. Measured after the concurrent runs above are
  // all finished, so nothing else is pending.
  {
    const timers = (): number =>
      process.getActiveResourcesInfo().filter((r) => r === "Timeout").length;
    const before = timers();
    const rQuick = await runOne(cleanFile, 600_000, { exitGraceMs: 600_000 });
    const after = timers();
    test("a run that ends on its own leaves neither its timeout nor its grace timer armed", () => {
      assert(rQuick.ok === true, describe(rQuick));
      // `<=`, not `===`: a timer that went away is no leak, and the count
      // includes timers Node itself owns.
      assert(after <= before,
        `${after - before} timer(s) outlived the run: both are minutes long and would hold the runner open`);
    });
  }

  // ------------------------------------------------------ computeOk, pure
  test("computeOk: the runner's own grace kill does not disqualify a clean tally", () => {
    const clean = { passed: 1, failed: 0, total: 1 };
    const killed = Object.assign(new Error("killed"), { killed: true, signal: "SIGKILL" });
    assert(computeOk({ counts: clean, byExit: false, err: killed, timedOut: false, graceKilled: true }) === true,
      "a grace-killed child with a clean tally is green");
    assert(computeOk({ counts: clean, byExit: false, err: killed, timedOut: false }) === false,
      "the SAME error without the grace flag is a crash and stays red (runTests.test.ts's rule)");
    assert(computeOk({ counts: { passed: 1, failed: 1, total: 2 }, byExit: false, err: killed,
      timedOut: false, graceKilled: true }) === false,
      "a failing tally is red whatever killed the child");
    assert(computeOk({ counts: clean, byExit: false, err: killed, timedOut: true, graceKilled: true }) === false,
      "a timeout is red even if the grace flag was somehow set");
  });
} finally {
  // The pipes-held fixtures left a detached grandchild each, with a long life.
  for (const f of heldPidFiles) {
    try { process.kill(Number(readFileSync(f, "utf8"))); }
    catch { /* already gone, or never written */ }
  }
  if (dir) rmSync(dir, { recursive: true, force: true });
}

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`w15ChildExitReport.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));

export const result = { passed, failed, total };
