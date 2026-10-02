// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3FlowCardLoadingCopy.test.tsx - WP-61 (#592): the Sky flow card's loading
// copy must say something the empty card does not already show.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx <this file>
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT (#592). While the card waits for the open it asked for, the
// waiting card was titled OPENING THIS FLOW and then hinted
// "This flow's stages show once it has loaded." - the hint told the reader
// nothing the title had not already. RUN's locked reason, shown as the
// button's `title` and in its explain-toast, was "Loading this flow...",
// which does the same thing: it repeats the title's word and never says what
// is missing, unlike the card's other two waiting reasons
// (FLOW_CARD_NO_ID, FLOW_CARD_RUN_NOT_OPENED), which each name a cause.
//
// This mounts the card on a link whose flow never answers (the fetch below
// never resolves), which is the "still opening" branch of `waiting` in
// flow.tsx, and pins both strings to wording that names a cause, in the
// shape of the other two.
//
// MUTATION RECORD, 2026-10-01, run in a byte copy of this worktree's ui/,
// restored to the pristine bytes and checked by sha256 afterwards.
//
//   MUTANT "loading hint reverted" (flow.tsx: FLOW_CARD_LOADING's string put
//   back to "This flow's stages show once it has loaded."):
//     x hint: the loading hint names the open as the reason nothing shows yet: hint text:
//       expected "The open this card asked for has not answered yet, so there is nothing here to show."
//       got      "This flow's stages show once it has loaded."
//
//   MUTANT "RUN's loading reason reverted" (flow.tsx: FLOW_CARD_RUN_LOADING's
//   string put back to "Loading this flow..."):
//     x run reason: RUN's locked reason says the flow has not loaded, in the shape of the other two reasons: RUN's reason:
//       expected "This flow has not loaded yet, so there is nothing here to run."
//       got      "Loading this flow..."

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky/quick/flow?id=flow-never-answers", pretendToBeVisual: true },
);
const win = dom.window as any;

// The phone layout: every media query answers false (desktop width).
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {} send() {} addEventListener() {} removeEventListener() {}
};
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "SVGElement", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "PointerEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// Every request hangs forever: the card under test is the one still waiting
// for its open to answer, and a resolved fetch would carry it past that
// state before the assertions below run.
g.fetch = () => new Promise(() => {});

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { FlowCardSheet, FLOW_CARD_LOADING } = await import("../flow");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
const settle = async (n = 4) => {
  for (let i = 0; i < n; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

// control.mount + a connected camera, so `runBlockedReason` answers null and
// the button's locked reason is the waiting state's, not an access refusal.
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount", "control.guide"],
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const q = (sel: string) => container.querySelector(sel) as any;
const byId = (id: string) => q(`[data-testid="${id}"]`);

useStore.setState({
  principal: OPERATOR,
  authGate: "open",
  equipConnected: true,
  toasts: [],
  confirm: null,
} as any);

await test("hint: the loading hint names the open as the reason nothing shows yet", async () => {
  await act(async () => {
    root.render(createElement(FlowCardSheet, { params: { id: "flow-never-answers" }, depth: 0 }));
  });
  await settle();
  const waiting = byId("flow-card-waiting");
  assert(waiting != null, "no waiting card for a flow whose open never answers");
  eq(FLOW_CARD_LOADING,
    "The open this card asked for has not answered yet, so there is nothing here to show.",
    "hint text:");
  assert(String(waiting.textContent).includes(FLOW_CARD_LOADING),
    `the waiting card does not render FLOW_CARD_LOADING as its hint: "${waiting.textContent}"`);
});

await test("run reason: RUN's locked reason says the flow has not loaded, in the shape of the other two reasons", async () => {
  const runBtn = byId("flow-run");
  assert(runBtn != null, "no RUN button: the fixture is wrong, not the component");
  eq(runBtn.getAttribute("aria-disabled"), "true", "RUN is not locked while the open is still out:");
  eq(runBtn.getAttribute("title"),
    "This flow has not loaded yet, so there is nothing here to run.",
    "RUN's reason:");
});

await act(async () => { root.unmount(); });

const total = passed + failed;
console.log(`w3FlowCardLoadingCopy.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
