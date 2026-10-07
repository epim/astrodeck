// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16ActivateRelayCopy.test.ts - the CLASSIC profile-activate failure copy stops
// pointing at force-activate over the relay (#762; WP-145, wave 16).
//
//   Run directly:  npx tsx src/views/__tests__/w16ActivateRelayCopy.test.ts
//   Also run by `npm test` (run-tests.mjs).
//
// Wave 15 (#685) opened an UNFORCED profile activate to a relayed operator and
// left `force` as the one option the rig answers 403 `local_only`. Only the
// #/next copies were fixed (`rigConnect.ts`, `ProfilesEditor.tsx`); the classic
// root is the production root, and its two copies went on saying the thing that
// cannot be done:
//
//   EquipmentView.activateErrorMessage  "... Stop it first, or force-activate
//                                        from Settings -> Profiles."
//   ProfileList.activateFailureOutcome   a hold-confirm "Force-activate this
//                                        profile anyway?" whose yes sends
//                                        force=true and is refused 403
//
// WHAT IS WORTH ASSERTING:
//
//   ON THE RELAY a coded 409 says what is true (stop the run; forcing needs the
//   LAN) and names nothing to press: the toast, never the dialog. Both copies say
//   the SAME sentence as the #/next ones (`forceNeedsLan`), so the three surfaces
//   cannot tell an operator three things.
//
//   THE SERVER'S OWN REASON SURVIVES. The sentence is worded from the rig's
//   detail (#256), so auto-resume's "turns that session's auto-resume off" is
//   still on the page and is not replaced by a fixed one.
//
//   ON THE LAN NOTHING CHANGES. The pointer to force, and the dialog that sends
//   it, stay: the relay wording must not leak onto the origin that can force.
//
//   THE DEFAULT IS THE WIRING. Production calls pass the error alone, so the
//   relay wording reaches an operator only through the `viaRelay` default
//   (`onRelay()`); the cases that pass the flag by hand cannot see it broken, so
//   one case per surface sets the origin the way the app does and passes nothing.
//
// MUTANTS RUN (each from a byte backup, restored byte-identically with sha256
// compared, the mutant text grepped out). The failing assertion each produced,
// verbatim (long JSON cut with ...):
//
//   W1 "EquipmentView ignores the relay" (`? (viaRelay` made `? (false`), 7/11:
//      "x EquipmentView on the relay: the coded 409 says forcing needs the LAN
//      and points nowhere: unexpected relay message: "A sequence, capture loop or
//      polar alignment is running. Stop it first, or force-activate from Settings
//      -> Profiles.""
//   W2 "ProfileList still offers force on the relay" (`if (viaRelay) {` made
//      `if (false) {`), 8/11:
//      "x ProfileList on the relay: an unforced coded 409 is a toast, never the
//      force dialog: the relay was offered a force dialog whose yes the rig
//      refuses 403: {"kind":"force","confirm":{"title":"Rig is busy",...}}"
//   W3 "EquipmentView's default ignores the origin" (`viaRelay: boolean =
//      onRelay()` made `= false`), 10/11:
//      "x EquipmentView: the default reads the origin (relay says it needs the
//      LAN, LAN points at force): on the relay, with no second argument, the toast
//      still points at force: "A sequence, capture loop or polar alignment is
//      running. Stop it first, or force-activate from Settings -> Profiles.""
//   W4 "ProfileList's default ignores the origin" (the same, in ProfileList),
//      10/11:
//      "x ProfileList: the default reads the origin (relay is a toast, LAN is the
//      dialog): on the relay, with no third argument, the force dialog is still
//      offered: {"kind":"force","confirm":{"title":"Rig is busy",...}}"
//
// Before the change the file read 4/11: the four cases that assert the LAN is
// unchanged passed, and every relay case failed with the same messages.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Both modules pull in the store and the api client, whose lib/base.ts reads
// window.location at module scope.
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
g.fetch = async () => ({ ok: true, status: 200, statusText: "OK", json: async () => ({}) }) as any;

const { ApiError } = await import("../../api");
const { activateErrorMessage } = await import("../EquipmentView");
const { activateFailureOutcome } = await import("../../components/settings/ProfileList");
const { forceNeedsLan } = await import("../../next/hubs/rig/profiles/profilesModel");
const { noteRemoteStatus, resetRelayForTests } = await import("../../next/lib/relay");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** Run `fn` with the origin set the way the app sets it (the rig's own `via`),
 *  and put it back. */
function onOrigin(via: "relay" | "direct", fn: () => void): void {
  noteRemoteStatus({ via });
  try { fn(); } finally { resetRelayForTests(); }
}

// The server's exact `_TEARDOWN_WHILE_RECOVERING` text (app.py), copied
// verbatim: the sentence must carry the SERVER's words (#256).
const RECOVERING_DETAIL =
  "auto-resume is re-centring the mount after a restart "
  + "(the Monitor shows the step it is on); force stops "
  + "the re-centring before its next step and turns that session's "
  + "auto-resume off, as Abort does, then goes ahead";
const BUSY_DETAIL = "a sequence, capture loop or polar alignment is running";
const RELAY_SENTENCE =
  "A sequence, capture loop or polar alignment is running. Stop it first: "
  + "forcing the switch needs the LAN, and you are connected through the relay.";

const coded = (detail: string) => new ApiError(detail, 409, false, "running");

// ------------------------------------------------------ EquipmentView (classic)
test("EquipmentView on the relay: the coded 409 says forcing needs the LAN and points nowhere", () => {
  const msg = activateErrorMessage(coded(BUSY_DETAIL), true);
  assert(msg === RELAY_SENTENCE, `unexpected relay message: "${msg}"`);
  assert(!/force-activate/i.test(msg) && !/Settings/.test(msg),
    `the relay was pointed at a force it cannot use: "${msg}"`);
});

test("EquipmentView on the relay: it is the same sentence the #/next surfaces say", () => {
  assert(activateErrorMessage(coded(BUSY_DETAIL), true) === forceNeedsLan(BUSY_DETAIL),
    "the classic toast and forceNeedsLan() disagree, so two surfaces say two things");
});

test("EquipmentView on the relay: the server's own reason survives", () => {
  const msg = activateErrorMessage(coded(RECOVERING_DETAIL), true);
  assert(msg.includes("turns that session's auto-resume off"),
    `the relay message dropped the server's own reason: "${msg}"`);
  assert(/forcing the switch needs the LAN/.test(msg), `no statement of what forcing needs: "${msg}"`);
});

test("EquipmentView on the LAN: the pointer to force-activate is unchanged", () => {
  const msg = activateErrorMessage(coded(BUSY_DETAIL), false);
  assert(msg === "A sequence, capture loop or polar alignment is running. Stop it first, "
    + "or force-activate from Settings \u2192 Profiles.", `the LAN message changed: "${msg}"`);
  assert(!/needs the LAN/.test(msg), `the LAN was told forcing needs the LAN: "${msg}"`);
});

test("EquipmentView: the relay wording changes only the coded 409", () => {
  const lane = activateErrorMessage(new ApiError("'profile' is already running", 409), true);
  assert(lane === "Another profile is still connecting \u2014 wait for it to finish before switching again.",
    `the lane guard changed on the relay: "${lane}"`);
  assert(activateErrorMessage(new Error("boom"), true) === "boom", "a plain error changed on the relay");
});

test("EquipmentView: the default reads the origin (relay says it needs the LAN, LAN points at force)", () => {
  onOrigin("relay", () => {
    const msg = activateErrorMessage(coded(BUSY_DETAIL));
    assert(/forcing the switch needs the LAN/.test(msg) && !/force-activate/.test(msg),
      `on the relay, with no second argument, the toast still points at force: "${msg}"`);
  });
  onOrigin("direct", () => {
    const msg = activateErrorMessage(coded(BUSY_DETAIL));
    assert(/force-activate from Settings/.test(msg) && !/needs the LAN/.test(msg),
      `on the LAN, with no second argument, the toast changed: "${msg}"`);
  });
});

// ----------------------------------------------------------- ProfileList (classic)
test("ProfileList on the relay: an unforced coded 409 is a toast, never the force dialog", () => {
  const outcome = activateFailureOutcome(coded(BUSY_DETAIL), false, true);
  assert(outcome.kind === "toast",
    `the relay was offered a force dialog whose yes the rig refuses 403: ${JSON.stringify(outcome)}`);
  if (outcome.kind === "toast") {
    assert(outcome.message === RELAY_SENTENCE, `unexpected relay toast: "${outcome.message}"`);
    assert(outcome.level === "warning", `the toast is ${outcome.level}, not the warning every running 409 is`);
    assert(outcome.verbatim === true,
      "the sentence is long enough to be cut by the toast's log-line shortener without verbatim:true");
    assert(outcome.message === forceNeedsLan(BUSY_DETAIL),
      "the classic toast and forceNeedsLan() disagree, so two surfaces say two things");
  }
});

test("ProfileList on the relay: the server's own reason survives", () => {
  const outcome = activateFailureOutcome(coded(RECOVERING_DETAIL), false, true);
  assert(outcome.kind === "toast" && outcome.message.includes("turns that session's auto-resume off"),
    `the relay toast dropped the server's own reason: ${JSON.stringify(outcome)}`);
});

test("ProfileList on the LAN: the force dialog is still offered, worded from the server's detail", () => {
  const outcome = activateFailureOutcome(coded(RECOVERING_DETAIL), false, false);
  assert(outcome.kind === "force", `the LAN lost its force dialog: ${JSON.stringify(outcome)}`);
  if (outcome.kind === "force") {
    assert(outcome.confirm.body.includes("turns that session's auto-resume off"),
      `dialog body does not carry the server's reason: "${outcome.confirm.body}"`);
  }
});

test("ProfileList: a forced retry and the lane guard read the same on the relay as on the LAN", () => {
  for (const relay of [true, false]) {
    const stuck = activateFailureOutcome(coded(BUSY_DETAIL), true, relay);
    assert(stuck.kind === "toast" && stuck.message === "A sequence, capture loop or polar alignment is running.",
      `a forced retry changed with relay=${relay}: ${JSON.stringify(stuck)}`);
    const lane = activateFailureOutcome(new ApiError("'profile' is already running", 409), false, relay);
    assert(lane.kind === "toast" && lane.message.includes("Another profile connect is still running"),
      `the lane guard changed with relay=${relay}: ${JSON.stringify(lane)}`);
  }
});

test("ProfileList: the default reads the origin (relay is a toast, LAN is the dialog)", () => {
  onOrigin("relay", () => {
    const outcome = activateFailureOutcome(coded(BUSY_DETAIL), false);
    assert(outcome.kind === "toast",
      `on the relay, with no third argument, the force dialog is still offered: ${JSON.stringify(outcome)}`);
  });
  onOrigin("direct", () => {
    const outcome = activateFailureOutcome(coded(BUSY_DETAIL), false);
    assert(outcome.kind === "force", `on the LAN, with no third argument, the dialog is gone: ${JSON.stringify(outcome)}`);
  });
});

console.log(`w16ActivateRelayCopy: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
