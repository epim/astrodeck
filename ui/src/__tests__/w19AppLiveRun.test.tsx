// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w19AppLiveRun.test.tsx - the classic header strip, MOUNTED: "SEQ done/total"
// is shown for EVERY live run state, not only running and paused (#922, WP-161,
// wave 19).
//
//   Run directly:  npx tsx src/__tests__/w19AppLiveRun.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `App.tsx` derived `seqRunning` as `state === "running" || state ===
// "paused"` and gated both the header's "SEQ n/m" progress and the Sequence
// tab's busy LED on it. A cloud hold ("holding") and an abort's wind-down
// ("aborting") are live runs on the rig, so on a cloudy night the header read as
// though nothing was running while the run sat in its hold.
//
// WHAT IS WORTH ASSERTING. This mounts the real `App` on a live store and flips
// the sequence state. Per state in the union, with a progress block on every one
// (so a run that is not live must still print nothing: the control row, which a
// predicate that is always true fails): the header's progress count is on screen
// exactly for the live states.
//
// MUTANT "running or paused only" (App.tsx's `const seqRunning = runIsLive(sequence);`
// made `const seqRunning = sequence.state === "running" || sequence.state ===
// "paused";`, the unfixed text). Run from a byte backup, restored
// byte-identically (md5sum compared): see the report for the failing lines.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {}
  send() {}
  addEventListener() {}
  removeEventListener() {}
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

g.fetch = async (_url: any, _init?: any) => ({
  ok: false, status: 404, statusText: "Not Found",
  headers: { get: () => "application/json" },
  json: async () => ({}),
  text: async () => "",
});

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../store");
const App = (await import("../App")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;

const PROGRESS = { frames_done: 7, frames_total: 31, percent: 22, rejected: 0, elapsed_s: 600 };
function seed(sequence: Record<string, unknown>): void {
  act(() => {
    useStore.setState({
      authMethods: { methods: [], first_run: false } as any,
      principal: { role: "admin", email: null, caps: ["view.status", "view.preview"] } as any,
      wsPhase: "up",
      telemetryStale: false,
      equipConnected: false,
      view: "connect",
      runBanner: null,
      resumeArm: null,
      weather: null,
      sequence: { ...sequence, progress: PROGRESS } as any,
      status: { connected: {}, looping: false, mode: "sim" } as any,
    } as never);
  });
}
const headerText = (): string => String(container.querySelector(".app-header")?.textContent ?? "");

win.location.hash = "#/classic";
seed({ state: "idle" });
const root = createRoot(container);
act(() => { root.render(createElement(App)); });
await settle();

test("control: the header rendered, and an idle rig prints no SEQ count", () => {
  assert(container.querySelector(".app-header") != null, "no .app-header - the render never reached the header");
  assert(!/SEQ \d/.test(headerText()), `an idle rig prints a SEQ count: "${headerText()}"`);
});

/** The whole `SequenceState["state"]` union, with whether the rig's
 *  `engine.running` is true in it. */
const STATES: Array<[string, boolean]> = [
  ["running", true], ["paused", true], ["holding", true], ["aborting", true],
  ["idle", false], ["complete", false], ["aborted", false], ["error", false],
  ["nina_native", false],
];

for (const state of STATES.map(([s]) => s)) {
  const live = STATES.find(([s]) => s === state)![1];
  seed({ state });
  await settle();
  test(`run ${state}: the header ${live ? "shows" : "does not show"} the SEQ count`, () => {
    const has = /SEQ 7\/31/.test(headerText());
    assert(has === live,
      live
        ? `the header reads as though no run is going while it is ${state}: "${headerText()}"`
        : `the header prints a SEQ count for a run that is ${state}: "${headerText()}"`);
  });
}

act(() => { root.unmount(); });

console.log(`w19AppLiveRun: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
