// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Run every UI test file and fail the process if any assertion failed.
//
//   npm test
//
// Why this exists: the test files are plain tsx scripts. Each runs its own
// assertions, prints "N/M passed", and exports { passed, failed, total }. That
// design is deliberate (no framework, no jsdom) but it has a hole — a file that
// FAILS still exits zero, because printing a failure is not reporting one. So
// CI ran `npm run build` and nothing else: 83 test files gating nothing.
//
// Why a process per file, which is slower: these files were written to be run
// one at a time (`npx tsx <file>`), and they act like it.
//   * Several install fake globals — window, WebSocket, localStorage, fetch —
//     and never restore them. Imported into one shared process they stomp each
//     other: ws.test.ts passes alone and fails after another file has replaced
//     `window`. A runner that reports that is manufacturing failures.
//   * Four of them call process.exit() on failure, which would kill the whole
//     run partway and leave every later file silently unreported.
// Isolation is not a nicety here; without it the runner cannot be believed.
//
// Each child imports its file and exits on the file's own exported result, so
// the pass/fail signal is the file's, not a guess parsed out of its stdout.
//
// The scoring functions below (`parseCounts`, `assertionStyle`, `computeOk`)
// are exported so a unit test can exercise them directly, without spawning
// real child processes or planting a fixture file that a normal `npm test`
// walk would have to run (and, for the crash/false-tally shapes these guard
// against, would have to run withOUT failing the real suite). Importing this
// module for those exports must not itself run the whole suite — see the
// `isMain` guard at the bottom of the file.

import { readdirSync, statSync } from "node:fs";
import { execFile } from "node:child_process";
import { join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = fileURLToPath(new URL(".", import.meta.url));
//: enough to keep the box busy without oversubscribing a CI runner
const CONCURRENCY = 8;
const TIMEOUT_MS = 60_000;
// Loaded into every child BEFORE tsx (see runOne's execFile args below), so
// any test file whose render tree imports a `.css` specifier (wave R7's
// every-area-owns-its-stylesheet rule) does not crash the whole file with
// ERR_UNKNOWN_FILE_EXTENSION before a single test runs. See issue #50 and
// ./test-css-stub.mjs.
const CSS_STUB_URL = pathToFileURL(join(ROOT, "test-css-stub.mjs")).href;

function findTests(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== "node_modules") findTests(p, out);
    } else if (name.endsWith(".test.ts") || name.endsWith(".test.tsx")) {
      out.push(p);
    }
  }
  return out;
}

/** Recover the pass/fail counts from a file's printed summary.
 *
 *  Most files export `result`, which is exact. About forty do not — they only
 *  print their tally — and they are no less real for it, so the printed line is
 *  parsed rather than treated as a missing result. Two formats are in use:
 *
 *      name: 7/7 passed
 *      name: 25 passed, 0 failed
 *
 *  Returns null when NEITHER a result nor a recognisable tally is present. That
 *  is a genuine "cannot tell", and it fails — a file whose output we cannot
 *  read must never be scored as a pass.
 */
export function parseCounts(text) {
  const both = /(\d+)\s+passed,\s*(\d+)\s+failed/i.exec(text);
  if (both) {
    return { passed: +both[1], failed: +both[2], total: +both[1] + +both[2] };
  }
  const ratio = /(\d+)\s*\/\s*(\d+)\s+passed/i.exec(text);
  if (ratio) {
    const [passed, total] = [+ratio[1], +ratio[2]];
    return { passed, failed: total - passed, total };
  }
  // Bare "N passed", with no fail count of its own — carries no evidence
  // about whether anything failed. Reading zero failures out of it is only
  // sound alongside a clean exit; a nonzero exit paired with this shape must
  // still fail the file (see `computeOk`), not be waved through because the
  // text alone looked green.
  const only = /(\d+)\s+passed\b/i.exec(text);
  if (only) return { passed: +only[1], failed: 0, total: +only[1] };
  return null;
}

/** A handful of files use a throw-on-failure convention instead of a tally:
 *  they assert, and print "OK" or "all assertions passed" only if they reach
 *  the end. For those the CHILD'S EXIT CODE is the signal — a failed assertion
 *  throws, the import rejects, and the child dies nonzero. Recognised by the
 *  phrase so that a file which prints nothing still counts as unscorable. */
export function assertionStyle(text) {
  return /\bOK\b|all assertions passed/i.test(text);
}

/** Whether one file's run counts as green. `err` is the `execFile` callback's
 *  error — truthy on ANY nonzero exit, not only a timeout — and disqualifies
 *  the run outright, even when a passing tally was recovered from its output.
 *  Without this a file that prints "2/2 passed" and then dies (a throw in
 *  teardown, a stray unhandled rejection, or a hand-written file using the
 *  bare "N passed" form — which `parseCounts` reads as zero failures because
 *  it carries no fail count of its own — behind a `process.exit(1)`) reports
 *  "all green" purely because the printed text looked clean. `byExit` already
 *  requires `!err` to be true (a throw-on-failure file that reached its end
 *  cleanly), so folding `err` in here does not change that path — it only
 *  closes the tally branch, which used to ignore the exit code entirely. */
export function computeOk({ counts, byExit, err, timedOut }) {
  if (timedOut || err) return false;
  return (counts !== null && counts.failed === 0) || byExit;
}

/** On a timeout, whether the file's own cases had already finished.
 *
 *  Issue #614: flowInspectorNotes.test.tsx printed its "7/7 passed" tally and
 *  then the child never exited, on a loaded CI runner. A kill that lands AFTER
 *  a file's tally (or throw-on-failure completion phrase) means whatever hung
 *  is in code that runs after the file's own cases — this runner's `runOne`
 *  has nothing left to do but `await import()` itself resolving and call
 *  `process.exit()`, so that import is what never returned. A kill with no
 *  tally at all hung somewhere inside the cases, before the file ever got to
 *  summarise them — a different bug shape, and worth telling apart on sight
 *  rather than re-deriving from the raw output every time. Exported so this
 *  is a pure-function unit test on captured output, not a fixture file that
 *  has to actually survive TIMEOUT_MS to prove the message is right. */
export function timeoutPhase(output) {
  return (parseCounts(output) !== null || assertionStyle(output))
    ? "after its own tally -- the default export never resolved"
    : "during its test cases -- no tally was ever printed";
}

// `timeoutMs` defaults to the real suite's TIMEOUT_MS but can be overridden
// per call — issue #664's own regression test (w13CleanTallyTimeoutRetry)
// needs a real timeout-and-kill to prove runFileWithRetry's wiring, and
// waiting out the full 60s for that would make the guard itself the next
// "cost a minute of CI time by design" problem runTests.test.ts's own header
// warns against.
export function runOne(file, timeoutMs = TIMEOUT_MS) {
  // The child imports the file, which runs its assertions, then reports the
  // file's own exported `result` when it has one.
  const url = pathToFileURL(file).href;
  const code = `
    const m = await import(${JSON.stringify(url)});
    const r = m.result;
    if (r && typeof r.failed === "number") {
      console.error("__COUNTS__" + JSON.stringify(r));
      process.exit(r.failed > 0 ? 1 : 0);
    }
    process.exit(0);   // no export: the parent reads the printed tally
  `;
  return new Promise((resolve) => {
    execFile(
      process.execPath,
      ["--import", CSS_STUB_URL, "--import", "tsx", "--input-type=module", "--eval", code],
      { cwd: ROOT, timeout: timeoutMs, maxBuffer: 8 << 20 },
      (err, stdout, stderr) => {
        const output = [stdout, (stderr || "").replace(/__COUNTS__.*\n?/, "")]
          .join("").trim();
        const tagged = /__COUNTS__(\{.*\})/.exec(stderr || "");
        const counts = tagged ? JSON.parse(tagged[1]) : parseCounts(output);
        const timedOut = !!err && err.killed;
        // Exit 0 + a completion phrase = a throw-on-failure file that reached
        // its end. Counted as green but contributing no assertion count, since
        // it never told us one — better an undercount than an invented number.
        const byExit = !err && counts === null && assertionStyle(output);
        resolve({
          file,
          counts,
          byExit,
          // A crash (nonzero exit, with or without counts) is a failure even
          // when a passing tally was printed before it died — see `computeOk`.
          ok: computeOk({ counts, byExit, err, timedOut }),
          timedOut,
          output,
        });
      },
    );
  });
}

/** Issue #664. A full-suite run (548 files, CONCURRENCY 8) on this shared box
 *  twice produced the #614 shape — a file printed its own clean tally and
 *  then the child was killed at TIMEOUT_MS anyway — on two DIFFERENT files
 *  (hubBoundary.test.tsx, appHeaderDom.test.tsx) neither of which schedules
 *  requestAnimationFrame anywhere in its own render path. jsdom's native rAF
 *  is RULED OUT as the carrier: hubBoundary's own render tree calls
 *  requestAnimationFrame nowhere at all (grepped and read directly).
 *
 *  Instrumented from a byte backup (process._getActiveHandles()/
 *  _getActiveRequests(), polled every 2s by an UNREF'D setInterval armed
 *  before the child's own `await import()`, writing to a file rather than a
 *  console.log through a pipe that a SIGTERM might never flush): BOTH caught
 *  hangs wrote ZERO heartbeat lines for the entire 60s window, start to
 *  finish. An unref'd `setInterval` fires whenever the event loop turns AT
 *  ALL — it needs no handle of its own to be tracked, only for libuv to reach
 *  its timer phase — so CPU/scheduling contention (other processes on the
 *  shared box taking turns with this one) cannot explain zero fires for a
 *  full minute: contention slows how OFTEN the loop turns, it does not stop
 *  it turning, and sixty seconds is enormously more than one turn needs. Zero
 *  dumps for the whole window means the child's MAIN THREAD stopped running
 *  entirely after the tally printed — a synchronous stall or a blocked
 *  (non-async) write are the two shapes that would do that — not that it kept
 *  running, slowly. An independent reviewer reached the same reading from the
 *  same evidence. THE ROOT CAUSE IS UNKNOWN: this rules out one specific
 *  mechanism (a dangling rAF/timer handle) and the CPU-contention
 *  explanation an earlier pass of this comment drew from the same evidence,
 *  but does not yet say what blocks the thread. See #664.
 *
 *  160 isolated runs of the originally-reported file
 *  (capturePreviewMobileOverflow.test.tsx, 40 at CONCURRENCY 8 and then 120
 *  more) produced zero hangs on its own — whatever this is, it needs the
 *  full-suite run to show up, and has so far appeared on a different file
 *  each time it has been caught.
 *
 *  shouldRetry names the ONE shape worth a second try: the file's own
 *  assertions already finished (it printed a clean tally or a throw-on-
 *  failure completion phrase) and only the process's own exit missed the
 *  deadline. A timeout with NO tally at all is a different, more serious
 *  shape — something inside the file's own cases never returned — and must
 *  never be retried into a false green. */
export function shouldRetry(result) {
  return result.timedOut
    && timeoutPhase(result.output) === "after its own tally -- the default export never resolved";
}

/** Runs `file` once, and if it times out in the shape `shouldRetry` names,
 *  tries it exactly once more in a fresh child before reporting it broken.
 *  Exactly one retry, never a loop: a genuine hang (an app bug that cancels
 *  its own animation loop incorrectly, say) is deterministic and will time
 *  out the same way again, so a second timeout is reported as the real
 *  failure it is rather than retried forever waiting for the box to go
 *  quiet. `timeoutMs` threads through to both attempts — see `runOne`.
 *
 *  The returned result carries `retried: true` whenever a second attempt
 *  ran (whether or not it then passed), so `main`'s final summary can name
 *  every file this happened to instead of relying on the per-attempt log
 *  line below, which a concurrent run can bury between other files'
 *  output. */
export async function runFileWithRetry(file, timeoutMs = TIMEOUT_MS) {
  const first = await runOne(file, timeoutMs);
  if (!shouldRetry(first)) return first;
  const retry = await runOne(file, timeoutMs);
  if (retry.ok) {
    console.log(`  (${relative(ROOT, file)} froze after printing its own clean `
      + "tally -- cause unknown, see #664 -- then passed on a retry)");
  }
  return { ...retry, retried: true };
}

/** The line the final summary block prints naming every file that froze
 *  after a clean tally and then passed on its one retry (#664), or `null`
 *  when none did. A pure function of `results` (each as `runFileWithRetry`
 *  returns it) so the "appears in the final summary" behaviour this guards
 *  can be tested directly, without spawning `main`'s own typecheck + process
 *  exit. Named so CI logs show the FREQUENCY of this shape across a whole
 *  run (today buried in a per-attempt console.log a concurrent run can print
 *  between two other files' own output) rather than only the single most
 *  recent occurrence. */
export function retriedSummaryLine(results) {
  const names = results
    .filter((r) => r.retried && r.ok)
    .map((r) => relative(ROOT, r.file));
  if (names.length === 0) return null;
  return `${names.length} file(s) froze after a clean tally and passed on a `
    + `retry (#664): ${names.join(", ")}`;
}

/** Typecheck the whole project before running anything, and fail the run if
 *  it does not pass.
 *
 *  ISSUE #126. tsx strips types; it does not check them. So a test file could
 *  contain a type error, print "31/31 passed", and be committed green — and
 *  two were, on 2026-09-21: `photosphereStability.test.ts` asserted on
 *  `VisualStability.canWitness`, which is `private`, and TS2341 was raised by
 *  nothing until a release build ran `tsc -b` in a clean worktree two days and
 *  76 commits later. A gate that only a release runs is a gate with an
 *  unbounded interval between the break and the discovery, and the worst
 *  possible place to find one is between a version bump and a rig waiting for
 *  the build.
 *
 *  It goes INSIDE the runner rather than beside it in the npm script so that
 *  invoking the runner directly — which is the local habit — cannot skip it.
 *  Measured at 10.7 s cold on this box, and `tsc -b` is incremental, so a
 *  repeat run costs a fraction of that. It runs before the file workers
 *  because a type error usually explains whatever the tests are about to do.
 */
function typecheck() {
  return new Promise((resolve) => {
    const started = Date.now();
    execFile(process.execPath,
             [join(ROOT, "node_modules", "typescript", "bin", "tsc"), "-b"],
             { cwd: ROOT, maxBuffer: 8 << 20 },
             (err, stdout, stderr) => {
      const secs = ((Date.now() - started) / 1000).toFixed(1);
      if (!err) {
        console.log(`tsc -b: clean (${secs}s)`);
        resolve(true);
        return;
      }
      console.error(`\ntsc -b FAILED after ${secs}s — a type error is a broken`
                    + ` test suite, whatever the tallies below would say:\n`);
      console.error(`${stdout || ""}${stderr || ""}`.trim());
      resolve(false);
    });
  });
}

async function main() {
  if (!await typecheck()) process.exit(1);
  const files = findTests(join(ROOT, "src")).sort();
  if (files.length === 0) {
    console.error("no test files found — the discovery walk is broken, which " +
                  "would otherwise read as a clean run");
    process.exit(1);
  }

  const results = [];
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(CONCURRENCY, files.length) }, async () => {
      while (next < files.length) results.push(await runFileWithRetry(files[next++]));
    }),
  );

  let passed = 0, failed = 0;
  const broken = [];
  for (const r of results) {
    passed += r.counts?.passed ?? 0;
    failed += r.counts?.failed ?? 0;
    if (!r.ok) {
      const why = r.timedOut
        ? `timed out after ${TIMEOUT_MS / 1000}s (${timeoutPhase(r.output)})`
        : r.counts ? `${r.counts.failed} failed`
          : "no pass/fail tally in its output — cannot be scored";
      broken.push({ name: relative(ROOT, r.file), why, output: r.output });
    }
  }

  console.log(`\n${"=".repeat(64)}`);
  console.log(`${files.length} files · ${passed} passed · ${failed} failed`);
  const retriedLine = retriedSummaryLine(results);
  if (retriedLine) console.log(retriedLine);
  if (broken.length) {
    for (const b of broken) {
      console.error(`\n--- ${b.name}: ${b.why}`);
      if (b.output) console.error(b.output);
    }
    console.error(`\n${broken.length} file(s) not green`);
    process.exit(1);
  }
  console.log("all green");
}

// Only run the whole suite when this file is the process entry point (`npm
// test` -> `node --import tsx run-tests.mjs`), never when it is imported for
// its exported scoring functions (`parseCounts`, `assertionStyle`,
// `computeOk`) — e.g. from src/__tests__/runTests.test.ts. Importing this
// module must be side-effect-free; only executing it drives the suite.
const isMain = import.meta.url === pathToFileURL(process.argv[1] ?? "").href;
if (isMain) await main();
