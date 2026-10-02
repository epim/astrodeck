// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w7ResumeTitle.test.ts - D-13's title decision (#487, owner-approved
// 2026-09-30), in isolation from the three cards that share it.
//
//   Run:  node --import tsx src/next/hubs/session/now/__tests__/w7ResumeTitle.test.ts   (from ui/)
//
// "RESUME STOPPED RUN" after an operator's own STOP (`end_reason === "aborted"`).
// "RESUME INTERRUPTED RUN" after anything else - a restart, a crash, a safety
// stop, a WP-44 shutdown, or no `end_reason` at all. The case worth a named
// mutant of its own is the shutdown one: WP-44 exists only so a polite server
// shutdown stops being confused with an operator's STOP (#565), and a helper
// that matched "any non-null end_reason" instead of the one abort word would
// silently reintroduce that confusion for every ending, not just shutdown.

import { END_REASON_ABORTED, resumeTitle, RESUME_INTERRUPTED_TITLE, RESUME_STOPPED_TITLE }
  from "../resumeTitle";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

test("an operator's own STOP reads RESUME STOPPED RUN", () => {
  eq(resumeTitle(END_REASON_ABORTED), RESUME_STOPPED_TITLE,
    "the one word the engine stamps only on the abort arm did not read as a stop");
});

// MUTANT "D-13 split removed" (`resumeTitle` body replaced with
// `return RESUME_INTERRUPTED_TITLE;` unconditionally - the pre-#487 behaviour
// this WP replaces). Observed, 1/6:
//   x an operator's own STOP reads RESUME STOPPED RUN: the one word the engine
//     stamps only on the abort arm did not read as a stop
//     expected "RESUME STOPPED RUN"
//     got      "RESUME INTERRUPTED RUN"

// A polite server shutdown (WP-44's `end_reason: "shutdown"`) is the case
// D-13's ruling exists to get right: it is a restart in D-13's sense, not a
// stop, because the operator never pressed anything.
test("a WP-44 shutdown reads RESUME INTERRUPTED RUN, not stopped", () => {
  eq(resumeTitle("shutdown"), RESUME_INTERRUPTED_TITLE,
    "a polite server shutdown was worded as though the operator had pressed STOP");
});

// MUTANT "any non-null end_reason reads as a stop" (the comparison changed
// from `endReason === END_REASON_ABORTED` to `endReason != null`). This is
// the #565 regression reborn through the title instead of the cause sentence:
// every ending a run can have except a missing end_reason would read STOPPED.
// Observed, 2/6:
//   x a WP-44 shutdown reads RESUME INTERRUPTED RUN, not stopped: a polite
//     server shutdown was worded as though the operator had pressed STOP
//     expected "RESUME INTERRUPTED RUN"
//     got      "RESUME STOPPED RUN"
//   x a restart reads RESUME INTERRUPTED RUN: ... (same failure)
test("a restart reads RESUME INTERRUPTED RUN", () => {
  eq(resumeTitle("restart"), RESUME_INTERRUPTED_TITLE, "a restart was worded as an operator STOP");
});

test("a crash or a safety stop reads RESUME INTERRUPTED RUN", () => {
  eq(resumeTitle("unsafe"), RESUME_INTERRUPTED_TITLE, "a safety ending was worded as an operator STOP");
  eq(resumeTitle("dawn_cutoff"), RESUME_INTERRUPTED_TITLE, "dawn was worded as an operator STOP");
  eq(resumeTitle("incomplete"), RESUME_INTERRUPTED_TITLE, "an incomplete ending was worded as an operator STOP");
  eq(resumeTitle("error"), RESUME_INTERRUPTED_TITLE, "an error ending was worded as an operator STOP");
});

test("no end_reason at all (an older server, or a card not yet wired) reads RESUME INTERRUPTED RUN", () => {
  eq(resumeTitle(null), RESUME_INTERRUPTED_TITLE, "null was worded as an operator STOP");
  eq(resumeTitle(undefined), RESUME_INTERRUPTED_TITLE, "undefined was worded as an operator STOP");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w7ResumeTitle: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as { process?: { exitCode?: number } }).process!.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
