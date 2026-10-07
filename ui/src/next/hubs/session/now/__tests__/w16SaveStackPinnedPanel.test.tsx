// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16SaveStackPinnedPanel.test.tsx - SAVE STACK downloads the picture of the
// panel pinned on screen, not the latest panel's (#172 part A, WP-121 wave 16
// integration). MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/now/__tests__/w16SaveStackPinnedPanel.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `stackView.useSessionStackStatus()` returns the PINNED panel's
// status once a chip is pressed, so `status.seq` and `status.target` are that
// panel's. RunControls built its SAVE STACK link as
// `sessionStackImageUrl(stack.seq, 2400)` with no panel, so with Alpha pinned
// the operator saw Alpha's picture, tapped SAVE STACK and downloaded the
// LATEST panel's composite, saved as "Alpha-stack.jpg" with Alpha's seq as the
// cache key. The fix reads `panel` from `useStackView()` and passes it on.
//
// WHAT IS GUARDED
//
//   1. The vacuity guard: the link mounted, with a href.
//   2. Following the latest: the href is byte-identical to the one this app
//      has always built (no `panel=`), seq the latest panel's.
//   3. Pinning Alpha: the href carries `panel=t-a`, Alpha's own seq, and the
//      download name is Alpha's.
//   4. Releasing the pin goes back to the plain href.
//
// NAMED MUTANT (from a byte backup in the worktree, restored and
// sha256-compared): "SAVE STACK ignores the pin": RunControls.tsx
// `sessionStackImageUrl(stack.seq, 2400, undefined, panel ?? undefined)` made
// `sessionStackImageUrl(stack.seq, 2400)`, the line as it was. RED, 3/4
// passed, quoted at the case below.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/session/now", pretendToBeVisual: true },
);
const win = dom.window as any;
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
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------- the fake stack server
interface FakePanel { key: string; target: string; frames: number; integrated_s: number; seq: number }
const PANELS: FakePanel[] = [
  { key: "t-a", target: "Alpha", frames: 4, integrated_s: 240, seq: 12 },
  { key: "t-b", target: "Beta", frames: 2, integrated_s: 120, seq: 15 },
];
const BACKFILL = {
  running: false, total: 0, done: 0, added: 0, skipped: 0, failed: 0,
  channel: "", error: "", started_ts: null, finished_ts: null, available: 0,
};
/** The status the real route returns: with no `?panel=` the LATEST panel's
 *  numbers, with one that panel's; the whole list either way. */
function statusFor(key: string | null): any {
  const latest = PANELS.reduce((b, p) => (p.seq > b.seq ? p : b));
  const hit = (key ? PANELS.find((p) => p.key === key) : null) ?? latest;
  return {
    enabled: true, target: hit.target, seq: hit.seq,
    channels: [{ channel: "Ha", frames: hit.frames, integrated_s: hit.integrated_s, rejected: 0 }],
    frames: hit.frames, integrated_s: hit.integrated_s, rejected: 0,
    mode: "narrowband", downsample: 2, has_image: true, render_age_s: 3,
    backfill: BACKFILL, panels: PANELS.map((p) => ({ ...p })), evicted: [],
  };
}
g.fetch = async (url: any) => {
  const u = String(url);
  let body: any = { ok: true };
  if (u.includes("/api/sequence/stack")) {
    const m = /[?&]panel=([^&]*)/.exec(u);
    body = statusFor(m ? decodeURIComponent(m[1]) : null);
  } else if (u.includes("/api/sessions")) {
    body = { sessions: [] };
  }
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => "",
  };
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { RunControls } = await import("../RunControls");
const {
  resetStackViewForTests, resetSessionStackStateForTests, useStackView,
} = await import("../stackView");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const link = (): any => container.querySelector('[data-testid="now-save-stack"]');
const href = (): string => link()?.getAttribute("href") ?? "";
const download = (): string => link()?.getAttribute("download") ?? "";

useStore.setState({
  principal: {
    role: "operator", email: "op@rig",
    caps: ["view.status", "view.preview", "view.site_derived", "control.capture"],
  },
  equipConnected: true,
  wsPhase: "up",
  wsLastEvent: Date.now(),
  status: { connected: { camera: true }, looping: false },
  sequence: {
    state: "running", target: "Beta", plan_name: "Mosaic", target_index: 0,
    progress: { frames_done: 6, frames_total: 40, percent: 15, elapsed_s: 600, rejected: 0 },
  },
} as never);

// The same hook the chips use, so the test pins a panel the way a chip does.
let view: any = null;
function Probe(): null {
  view = useStackView();
  return null;
}

const root = createRoot(container);
await act(async () => { root.render(null); });
resetStackViewForTests();
resetSessionStackStateForTests();
await act(async () => {
  root.render(createElement(Fragment, null, createElement(Probe), createElement(RunControls)));
});
await settle();

// --------------------------------------------------- 1. the vacuity guard first
await testAsync("the SAVE STACK link mounted with a picture to save", () => {
  assert(link() != null, "no SAVE STACK link: the mount or the store seed is broken");
  assert(href() !== "", "the link has no href: has_image/the stack status never arrived");
});

// ----------------------------------------------- 2. following the latest is today's
await testAsync("following the latest panel: the plain URL this app has always built", () => {
  eq(view.panel, null, "premise: nothing is pinned:");
  eq(href(), "/api/sequence/stack/preview.jpg?size=2400&seq=15",
    "the href for a screen that follows the latest panel:");
  eq(download(), "Beta-stack.jpg", "the download name:");
});

// ------------------------------------------------------------ 3. a pin is honoured
// MUTANT "SAVE STACK ignores the pin" (RunControls.tsx: the stack URL built
// without the panel). Observed, 3/4 passed:
//   x a pinned panel's SAVE STACK link carries ?panel= and the panel's own
//     seq: the href does not ask for Alpha's stack, so the download would be
//     the latest panel's composite named for Alpha:
//     "/api/sequence/stack/preview.jpg?size=2400&seq=12"
await testAsync("a pinned panel's SAVE STACK link carries ?panel= and the panel's own seq", async () => {
  await act(async () => { view.setPanel("t-a"); });
  await settle();
  eq(view.panel, "t-a", "premise: Alpha is pinned:");
  const h = href();
  assert(h.includes("panel=t-a"),
    `the href does not ask for Alpha's stack, so the download would be the latest panel's composite named for Alpha: "${h}"`);
  assert(h.includes("seq=12"), `the cache key is not Alpha's own seq: "${h}"`);
  eq(download(), "Alpha-stack.jpg", "the download name is the pinned panel's:");
});

await testAsync("releasing the pin goes back to the plain URL", async () => {
  await act(async () => { view.setPanel(null); });
  await settle();
  eq(view.panel, null, "premise: the pin is released:");
  eq(href(), "/api/sequence/stack/preview.jpg?size=2400&seq=15",
    "the href after the pin was released:");
});

// ------------------------------------------------------------------- tally
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`w16SaveStackPinnedPanel.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
