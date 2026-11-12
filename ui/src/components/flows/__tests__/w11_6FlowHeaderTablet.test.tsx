// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w11_6FlowHeaderTablet.test.tsx - the classic flow header's tablet row fits
// beside UNSAVED EDITS (#770, WP 11.6).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w11_6FlowHeaderTablet.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// MEASURED, NOT ESTIMATED. In a real Chromium at 700, 768 and 820 px with the
// pill reading UNSAVED EDITS, the unfixed row overflowed at 700 (scrollWidth
// 627 over clientWidth 580, RUN and the i button past the edge) and at all
// three widths the validation chip was squeezed to a 0 to 35 px box while its
// text painted over the pill, because the Tooltip's root span is a shrinkable
// flex item whatever its trigger says. jsdom has no layout, so this file pins
// the three structural facts the fix rests on; the pixel measurements are in
// the work package report.
//
//   * the chip's own flex item cannot shrink;
//   * the backend pill (the app header carries the same badge, above this
//     row) is not drawn on the tablet row;
//   * TONIGHT is its glyph alone on the tablet row, with its name for a
//     screen reader, as it already is on the phone.

/* eslint-disable @typescript-eslint/no-explicit-any */

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const realSetTimeout = globalThis.setTimeout;
const tickReal = () => new Promise<void>((r) => realSetTimeout(r, 0));

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { flowsApi } = await import("../../../lib/flowsApi");
const { default: FlowHeader } = await import("../FlowHeader");
const { NODE_DEFS } = await import("../nodeDefs");

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { await drain(); }
}
/** A PUT still held when a case ends (it failed before it answered it) is
 *  answered here: the slice keeps its save in a closure the whole file shares,
 *  and every later case would wait on it for ever. */
async function drain(): Promise<void> {
  await act(async () => {
    for (const p of puts) p.resolve({ ...p.flow, updated_ts: 9 });
    puts = [];
  });
  await tickReal();
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

// ------------------------------------------------------------------ stubs
interface Put { flow: any; resolve: (v: unknown) => void; reject: (e: unknown) => void }
let puts: Put[] = [];
(flowsApi as any).save = (_id: string, flow: any) =>
  new Promise((resolve, reject) => { puts.push({ flow, resolve, reject }); });
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };

// ------------------------------------------------------------------ fixture
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount", "view.site_derived"],
};
const GRAPH = {
  nodes: [{ id: "t1", type: "target", x: 0, y: 0, params: { ...NODE_DEFS.target.params } }],
  edges: [],
};
function record(readonly: boolean) {
  return {
    id: "f1", name: "NGC7331 Preferential Filtering", folder: "My flows", tagline: "",
    graph: GRAPH, created_ts: 0, updated_ts: 0,
    last_run: null, last_result: "" as const, readonly,
  };
}

function seed(o: {
  dirty?: boolean; saving?: boolean; readonly?: boolean; liveSession?: boolean;
}): void {
  const flows = useStore.getState().flows;
  puts = [];
  act(() => { useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    confirm: null,
    toasts: [],
    status: { connected: { camera: { connected: true, name: "sim" } } },
    sequence: o.liveSession
      ? { state: "running", session: { id: "s1" } }
      : { state: "idle" },
    flows: {
      ...flows,
      record: record(o.readonly === true),
      graph: JSON.parse(JSON.stringify(GRAPH)),
      dirty: o.dirty === true,
      saving: o.saving === true,
      sessionIds: o.liveSession ? ["s1"] : [],
      logs: [],
      libraryError: null,
      compiled: null,
      progress: null,
      run: { ...flows.run, phase: "idle", startedAt: null },
      ui: { ...flows.ui, screen: "editor" },
    },
  } as never); });
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 3; i++) await act(async () => { await tickReal(); });
};
async function mount(tier: "tablet" | "phone" | "desktop" = "tablet"): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(FlowHeader as any, { tier })); });
  await settle();
}


const chipItem = (): any => {
  let el: any = [...container.querySelectorAll("span")]
    .find((s: any) => /OPEN CHECK|GRAPH VALID|NOT CHECKED/.test(s.textContent ?? "") && s.children.length === 0);
  while (el && el.parentElement && el.parentElement.tagName !== "HEADER") el = el.parentElement;
  return el;
};
const providerBadge = (): any => container.querySelector('[aria-label^="Rig backend"]');
const tonight = (): any => [...container.querySelectorAll("button")]
  .find((b: any) => /TONIGHT/i.test(`${b.textContent} ${b.querySelector("[aria-label]")?.getAttribute("aria-label") ?? ""}`));

function seedSim(): void {
  seed({ dirty: true });
  act(() => { useStore.setState({
    status: { mode: "sim", connected: { camera: { connected: true, name: "sim" } } },
  } as never); });
}

// MUTANT "chip may shrink" (FlowHeader.tsx: the `shrink-0` on the span that
// wraps the validation chip's Tooltip removed).
await testAsync("the validation chip's flex item cannot shrink", async () => {
  seedSim();
  await mount("tablet");
  const item = chipItem();
  eq(item !== undefined && item !== null, true, "no validation chip on the tablet row");
  eq(String(item.className).split(/\s+/).includes("shrink-0"), true,
    `the chip's flex item (${item.className}) shrinks, so its text paints over the pill`);
});

// MUTANT "tablet draws the backend pill" (`showProv = !phone && !tablet`
// made `!phone`).
await testAsync("the tablet row does not draw the backend pill, the desktop row does", async () => {
  seedSim();
  await mount("tablet");
  eq(providerBadge() === null ? "no pill" : "a pill", "no pill",
    "the tablet row spends 100 px on a badge the app header above already shows");
  await mount("desktop");
  eq(providerBadge() === null ? "no pill" : "a pill", "a pill",
    "the desktop row lost its backend pill");
});

// MUTANT "tablet spells TONIGHT out" (the label's `!phone && " TONIGHT"`).
await testAsync("TONIGHT is its glyph on the tablet row, and spelt out on the desktop row", async () => {
  seedSim();
  await mount("tablet");
  const t = tonight();
  eq(String(t.textContent).trim(), "◷", "the tablet TONIGHT button spells its name out");
  eq(t.querySelector("[aria-label]")?.getAttribute("aria-label"), "TONIGHT",
    "the glyph-only button has no accessible name");
  await mount("desktop");
  eq(/TONIGHT/.test(String(tonight().textContent)), true, "the desktop TONIGHT lost its word");
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`w11_6FlowHeaderTablet.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
