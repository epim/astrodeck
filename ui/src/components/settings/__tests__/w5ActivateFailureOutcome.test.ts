// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w5ActivateFailureOutcome.test.ts - what the profiles panel's force-activate
// flow does for a given error and retry state (#256): the force dialog is
// worded from the server's own coded-409 detail, and a forced retry that is
// STILL coded "running" (the ladder outliving its own stop bound) is its own
// case, not the lane guard's "another profile connect is still running".
//
//   Run directly:  npx tsx src/components/settings/__tests__/w5ActivateFailureOutcome.test.ts
//   Also run by `npm test` (run-tests.mjs).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Before ProfileList (and the store it imports) are loaded: the api client
// reads window.location at module scope (lib/base.ts) - same reason
// profileActivate.test.tsx bootstraps jsdom first.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body></body></html>`, { url: "http://local/" });
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { ApiError } = await import("../../../api");
const { activateFailureOutcome } = await import("../ProfileList");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// The server's exact `_TEARDOWN_WHILE_RECOVERING` text, folded into the busy
// case (app.py's `_teardown_busy_detail` joins the two with ", and " when the
// engine is ALSO busy) - copied verbatim rather than re-derived, because the
// dialog must carry the SERVER's words.
const RECOVERING_DETAIL =
  "a sequence, capture loop or polar alignment is running, and auto-resume is "
  + "re-centring the mount after a restart (GET /api/sequence/resume-arm "
  + "reports the step it is on); force stops the re-centring before its next "
  + "step and turns that session's auto-resume off, as Abort does, then goes ahead";

// The server's exact `_wait_for_the_ladder` 409 text for a forced retry whose
// ladder did not stop within LADDER_STOP_WAIT_S - nothing was torn down.
const LADDER_STUCK_DETAIL =
  "auto-resume's re-centring was asked to stop and has not stopped within 35 "
  + "s, so nothing was torn down; its session's auto-resume is off. GET "
  + "/api/sequence/resume-arm reports it until it has stopped; try again then.";

test("an unforced coded 409 offers to force, worded from the server's detail", () => {
  const e = new ApiError(RECOVERING_DETAIL, 409, false, "running");
  const outcome = activateFailureOutcome(e, false);
  assert(outcome.kind === "force", `expected a force confirm, got ${JSON.stringify(outcome)}`);
  if (outcome.kind === "force") {
    assert(outcome.confirm.body.includes("turns that session's auto-resume off"),
      `dialog body does not mention disarming auto-resume: "${outcome.confirm.body}"`);
  }
});

test("a forced retry that is STILL coded 409 shows the server's own detail, not the lane guard's", () => {
  const e = new ApiError(LADDER_STUCK_DETAIL, 409, false, "running");
  const outcome = activateFailureOutcome(e, true);
  assert(outcome.kind === "toast" && outcome.level === "warning",
    `expected a warning toast, got ${JSON.stringify(outcome)}`);
  if (outcome.kind === "toast") {
    assert(outcome.message.startsWith("Auto-resume's re-centring was asked to stop"),
      `message does not lead with the ladder's own detail: "${outcome.message}"`);
    assert(!outcome.message.includes("Another profile connect"),
      "the forced retry's genuine ladder-stuck detail was replaced by the lane "
      + `guard's false claim: "${outcome.message}"`);
    assert(outcome.verbatim === true,
      "the ladder-stuck detail is long enough to be silently truncated by the "
      + "toast's log-line shortener without verbatim:true");
  }
});

test("the uncoded lane-busy 409 keeps its own sentence, forced or not", () => {
  const e = new ApiError("'profile' is already running", 409);
  const outcome = activateFailureOutcome(e, false);
  assert(outcome.kind === "toast" && outcome.message.includes("Another profile connect is still running"),
    `expected the lane-guard sentence, got ${JSON.stringify(outcome)}`);
});

console.log(`w5ActivateFailureOutcome: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
