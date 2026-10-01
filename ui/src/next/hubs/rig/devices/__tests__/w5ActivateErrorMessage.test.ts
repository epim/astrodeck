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
  + "(GET /api/sequence/resume-arm reports the step it is on); force stops "
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

test("the uncoded lane-busy 409 keeps its own sentence", () => {
  const msg = activateErrorMessage(new ApiError("'profile' is already running", 409));
  assert(msg === "Another profile is still connecting - wait for it to finish before switching again.",
    `unexpected message for the lane guard: "${msg}"`);
});

console.log(`w5ActivateErrorMessage (rigConnect): ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
