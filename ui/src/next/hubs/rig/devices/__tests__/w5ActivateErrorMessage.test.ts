// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w5ActivateErrorMessage.test.ts - the Rig hub's activate-failure toast shows
// the server's own coded-409 reason (#256), not a fixed sentence that reads
// "a sequence is running" over an idle engine while auto-resume re-centres
// the mount after a restart (#238).
//
//   Run directly:  npx tsx src/next/hubs/rig/devices/__tests__/w5ActivateErrorMessage.test.ts
//   Also run by `npm test` (run-tests.mjs).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// rigConnect.ts pulls in the store (confirmDialog, useStore) and the api
// client, whose lib/base.ts reads window.location at module scope.
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

const { ApiError } = await import("../../../../../api");
const { activateErrorMessage } = await import("../rigConnect");
const { noteRemoteStatus, resetRelayForTests } = await import("../../../../lib/relay");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// The server's exact `_TEARDOWN_WHILE_RECOVERING` text (app.py) - copied
// verbatim rather than re-derived, because the toast must carry the SERVER's
// words, which is the entire point of #256.
const RECOVERING_DETAIL =
  "auto-resume is re-centring the mount after a restart "
  + "(the Monitor shows the step it is on); force stops "
  + "the re-centring before its next step and turns that session's "
  + "auto-resume off, as Abort does, then goes ahead";

test("the coded 409 shows the server's reason, not a fixed sentence", () => {
  const msg = activateErrorMessage(new ApiError(RECOVERING_DETAIL, 409, false, "running"));
  assert(msg.includes("turns that session's auto-resume off"),
    `message does not carry the server's own reason: "${msg}"`);
  assert(msg.includes("force-activate from the profiles sheet"),
    `message dropped the pointer to where force-activate lives: "${msg}"`);
});

test("the plain busy cause still reads as a capitalized sentence", () => {
  const msg = activateErrorMessage(
    new ApiError("a sequence, capture loop or polar alignment is running", 409, false, "running"),
  );
  assert(
    msg === "A sequence, capture loop or polar alignment is running. Stop it first, "
      + "or force-activate from the profiles sheet.",
    `unexpected message for the plain cause: "${msg}"`,
  );
});

// MUTANT "the relay is pointed at the profiles sheet's force" (rigConnect.ts:
// `viaRelay ? ... : ...` made always the LAN wording). Observed, "4/5 passed":
// "x on the relay the coded 409 says forcing needs the LAN, and points nowhere:
// unexpected relay message: "A sequence is running. Stop it first, or
// force-activate from the profiles sheet."".
test("on the relay the coded 409 says forcing needs the LAN, and points nowhere", () => {
  const msg = activateErrorMessage(
    new ApiError("a sequence is running", 409, false, "running"), true);
  assert(msg === "A sequence is running. Stop it first: forcing the switch needs the LAN, "
    + "and you are connected through the relay.", `unexpected relay message: "${msg}"`);
  assert(!/force-activate/.test(msg),
    `the relay was pointed at a force-activate it cannot use: "${msg}"`);
  const recovering = activateErrorMessage(
    new ApiError(RECOVERING_DETAIL, 409, false, "running"), true);
  assert(recovering.includes("turns that session's auto-resume off"),
    `the relay message dropped the server's own reason: "${recovering}"`);
});

test("the relay wording changes only the coded 409", () => {
  const lane = activateErrorMessage(new ApiError("'profile' is already running", 409), true);
  assert(lane === "Another profile is still connecting - wait for it to finish before switching again.",
    `the lane guard changed on the relay: "${lane}"`);
  assert(activateErrorMessage(new Error("boom"), true) === "boom", "a plain error changed on the relay");
});

// THE DEFAULT IS THE WIRING (wave 15 verifier). Every production caller passes
// the error alone (`activateErrorMessage(e)`), so the relay wording reaches an
// operator only through `viaRelay`'s default, `onRelay()`. The cases above pass
// `viaRelay` by hand, so a default that read `false` (the relay told to point at
// a force it cannot use) left all of them green.
//
// MUTANT "the default ignores the relay" (rigConnect.ts: `viaRelay: boolean =
// onRelay()` made `viaRelay: boolean = false`). Run from a byte backup and
// restored byte-identically (sha256 compared): the cases above stay 5/5, and
// this one is red:
// "x the default reads the origin: on the relay, with no second argument, the
// coded 409 says forcing needs the LAN: the relay was pointed at a
// force-activate it cannot use: "A sequence is running. Stop it first, or
// force-activate from the profiles sheet."".
test("the default reads the origin: on the relay, with no second argument, the coded 409 says forcing needs the LAN", () => {
  noteRemoteStatus({ via: "relay" });
  try {
    const msg = activateErrorMessage(
      new ApiError("a sequence is running", 409, false, "running"));
    assert(/forcing the switch needs the LAN/.test(msg) && !/force-activate/.test(msg),
      `the relay was pointed at a force-activate it cannot use: "${msg}"`);
  } finally {
    resetRelayForTests();
  }
  noteRemoteStatus({ via: "direct" });
  try {
    const msg = activateErrorMessage(
      new ApiError("a sequence is running", 409, false, "running"));
    assert(/force-activate from the profiles sheet/.test(msg) && !/needs the LAN/.test(msg),
      `the LAN was told forcing needs the LAN: "${msg}"`);
  } finally {
    resetRelayForTests();
  }
});

test("the uncoded lane-busy 409 keeps its own sentence", () => {
  const msg = activateErrorMessage(new ApiError("'profile' is already running", 409));
  assert(msg === "Another profile is still connecting - wait for it to finish before switching again.",
    `unexpected message for the lane guard: "${msg}"`);
});

console.log(`w5ActivateErrorMessage (rigConnect): ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
