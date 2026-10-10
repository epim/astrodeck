// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w18RigConnectLiveRun.test.ts - the two rig-teardown confirms in `rigConnect.ts`
// say "a sequence is running" for EVERY live run state, not only running and
// paused (#821, WP-158, wave 18).
//
//   Run directly:  npx tsx src/next/hubs/rig/devices/__tests__/w18RigConnectLiveRun.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `sequenceRunning()` (the one predicate this module feeds to both
// `activateProfileRow` and `disconnectRig`) answered `state === "running" ||
// state === "paused"`. The rig's `engine.running` is also true while the state
// is "holding" (a cloud hold, which on a cloudy night is the long-lived state)
// and "aborting" (up to ~210 s of wind-down), and `SequenceState` documents that
// every is-live predicate must include both. So during a hold the activate and
// disconnect dialogs dropped "A sequence is running, and that teardown aborts
// it" and an operator could confirm a rig teardown over a live run without being
// told. The activate dialog also stayed a tap-confirm instead of a hold.
//
// WHAT IS WORTH ASSERTING. The dialog each action opens, driven through the real
// functions with the store's `sequence` set to each state in the union, with a
// control row (a run that is NOT live says nothing about a run) so that a
// predicate which is simply always true fails too.
//
// MUTANT "running or paused only" (rigConnect.ts `sequenceRunning()` made
// `const s = useStore.getState().sequence?.state; return s === "running" || s
// === "paused";`, the unfixed text). Run from a byte backup, restored
// byte-identically (md5sum compared): see the report for the failing lines.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
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

import type { ProfileRow } from "../../../../../types";

const { activateProfileRow, disconnectRig } = await import("../rigConnect");
const { useStore } = await import("../../../../../store");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

// ------------------------------------------------------------------ fixtures
const posts: string[] = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  const u = String(url);
  const method = init?.method ?? "GET";
  const ok = (json: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });
  if (method === "POST") { posts.push(u); return ok({ started: "profile" }); }
  if (/\/api\/profiles\/[^/]+$/.test(u)) {
    return ok({ id: "p1", name: "Backyard", primary_backend: "sim", optics: null, devices: [] });
  }
  return ok({});
};

const ROW: ProfileRow = {
  id: "p1", name: "Backyard", mode: "alpaca", devices_count: 3,
  site_name: null, active: false,
};
const hooks = { setBusy() {}, onRows() {}, reload() {}, onResults() {}, onLanded() {} };
const SENTENCE_ACTIVATE = "A sequence is running, and that teardown aborts it.";
const SENTENCE_DISCONNECT = "A sequence is running. Disconnecting aborts it";

/** The whole `SequenceState["state"]` union, with whether the rig's
 *  `engine.running` is true in it (types.ts SequenceState documents "aborting"
 *  and "holding" as live runs; "nina_native" is NINA driving, no engine run). */
const STATES: Array<[string, boolean]> = [
  ["running", true], ["paused", true], ["holding", true], ["aborting", true],
  ["idle", false], ["complete", false], ["aborted", false], ["error", false],
  ["nina_native", false],
];

type Dialog = { title: string; body: string; mode: string };

/** Run `act`, wait for the dialog it opens (or for it to finish), answer it
 *  "cancel" so nothing proceeds, and report what it showed. */
async function drive(act: () => Promise<void>, state: string): Promise<Dialog | null> {
  posts.length = 0;
  useStore.setState({
    authGate: "open", wsPhase: "up", toasts: [], confirm: null,
    principal: { role: "admin", email: "a@rig", caps: ["view.status", "control.reconnect", "config.backend"] },
    sequence: { state },
  } as never);
  const done = act();
  let dialog: Dialog | null = null;
  let finished = false;
  void done.then(() => { finished = true; });
  const until = Date.now() + 3000;
  while (Date.now() < until && !finished) {
    const c = useStore.getState().confirm as any;
    if (c) {
      dialog = { title: String(c.title), body: String(c.body), mode: String(c.mode) };
      useStore.getState().resolveConfirm(false);
    }
    await new Promise((r) => setTimeout(r, 10));
  }
  await done;
  return dialog;
}

// ------------------------------------------------------------- the activate
for (const [state, live] of STATES) {
  await testAsync(`activate, run ${state}: the dialog ${live ? "says" : "does not say"} a sequence is running`, async () => {
    // Three live devices, so the dialog exists for every state (with none and a
    // simulator profile there is no dialog at all, which would grade nothing).
    const dialog = await drive(() => activateProfileRow(ROW, 3, hooks), state);
    assert(dialog !== null, "no dialog opened for an activate that drops three live devices");
    eq(dialog!.body.includes(SENTENCE_ACTIVATE), live,
      `the activate dialog with the run ${state}: ${dialog!.body}`);
    // The hold is part of the same claim: a teardown over a live run is a
    // hold-to-confirm, a tap is for a rig nothing is running on.
    eq(dialog!.mode, live ? "hold" : "confirm",
      `the activate dialog's mode with the run ${state}`);
    eq(posts.length, 0, "the cancelled dialog still sent an activate");
  });
}

// ------------------------------------------------------------ the disconnect
for (const [state, live] of STATES) {
  await testAsync(`disconnect, run ${state}: the dialog ${live ? "says" : "does not say"} a sequence is running`, async () => {
    const dialog = await drive(() => disconnectRig(3, hooks), state);
    assert(dialog !== null, "no dialog opened for a whole-rig disconnect");
    eq(dialog!.body.includes(SENTENCE_DISCONNECT), live,
      `the disconnect dialog with the run ${state}: ${dialog!.body}`);
    eq(posts.length, 0, "the cancelled dialog still sent a disconnect");
  });
}

console.log(`w18RigConnectLiveRun: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
