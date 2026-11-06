// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_9SuggestEllipseOnTheSheet.test.tsx - SUGGEST GRID on the REAL Target modal
// sizes the grid from the picked object's ellipse (#181, consolidation WP 3.9).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w3_9SuggestEllipseOnTheSheet.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// w3_9SuggestEllipse.test.ts grades the model; this file mounts the sheet, picks
// a catalogue row that carries `minor_arcmin` and `pa_deg` through WHERE's
// search, and reads what the SUGGEST GRID button offers and what pressing it
// does to the draft. The sheet is the only caller of `suggestGridFor`, so the
// wiring itself is what is graded here (the "built, tested, unreachable" gap
// of w16PanelCsvOnTheSheet.test.tsx).
//
// NAMED MUTANT "sheet sizes by the circle" (TargetFramingSheet's `suggestion`
// made `suggestGrid(sky.target.size_arcmin ?? 0, draft)`, from a byte backup,
// restored and sha256 compared). Observed:
//   x SUGGEST GRID on the sheet is sized for the picked ellipse: the button
//     offers "SUGGEST GRID 2x3" where the ellipse at rotation 0 wants 1x3

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
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = () => null;
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};
win.fetch = async (url: string) => { throw new Error(`no network in this fixture: ${url}`); };
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "PointerEvent",
  "KeyboardEvent", "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "Image", "URL", "fetch", "Blob",
  "WebSocket", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { FLOWS_INIT } = await import("../../flowsSlice");
const { NODE_DEFS } = await import("../../nodeDefs");
const { api } = await import("../../../../api");
const { framingApi, framingTiming } = await import("../framingApi");
const Sheet = (await import("../TargetFramingSheet")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { mount(); await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { unmount(); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

// ------------------------------------------------------------------ the mount
(framingApi as any).mosaic = () => new Promise(() => {});
(framingApi as any).compileDraft = () => new Promise(() => {});
framingTiming.settleMs = 0;
/** M31 as the search serves it: 178 x 69.3 arcminutes, 35 degrees east of
 *  north. The numbers are the test model's, not the catalogue's. */
const ELLIPSE_ENTRY = {
  id: "M31", name: "Andromeda Galaxy", type: "galaxy", ra_hours: 0.7123, dec_deg: 41.269,
  mag: 3.4, size_arcmin: 178, minor_arcmin: 69.3, pa_deg: 35,
};
const CIRCLE_ENTRY = { ...ELLIPSE_ENTRY, minor_arcmin: null, pa_deg: null };
let served: any = ELLIPSE_ENTRY;
(api as any).get = async (path: string) => {
  if (path.startsWith("/api/catalog?")) return { results: [served], notes: [] };
  throw new Error(`no network in this fixture: ${path}`);
};
const graph = {
  nodes: [
    { id: "n2", type: "target", x: 0, y: 0, params: {
      ...NODE_DEFS.target.params, name: "NGC 891", ra: "02h 22m 33s", dec: "+42 20 57",
      rows: 1, cols: 1, overlap: 25, fovX: 2.0, fovY: 1.33, rotation: 0, angle: "Rotate to PA",
      counts: "Accepted subs" } },
    { id: "cy", type: "cycle", x: 260, y: 0, params: { ...NODE_DEFS.cycle.params } },
  ],
  edges: [{ id: "a", from: "n2", fromPort: "target", to: "cy", toPort: "run" }],
};
useStore.setState({
  principal: { role: "operator", email: null, caps: ["view.status", "view.site_derived"] },
  wsConnected: false, status: null, config: null, site: null,
  flows: { ...FLOWS_INIT, graph,
    record: { id: "f1", name: "Andromeda mosaic", folder: "", tagline: "", graph, created_ts: 0,
      updated_ts: 0, last_run: null, last_result: "", readonly: false } },
} as any);

const container = win.document.getElementById("root");
const root = createRoot(container);
function mount(): void {
  act(() => { root.render(null); });
  act(() => { root.render(createElement(Sheet, { nodeId: "n2", onClose: () => {} })); });
}
function unmount(): void { act(() => { root.render(null); }); }
async function flush(ms = 5): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}
const doc = win.document;
const q = (id: string) => doc.querySelector(`[data-testid="${id}"]`) as any;
const searchField = () => doc.querySelector('[aria-label="Search the target catalog"]') as any;
function typeInto(el: any, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => { setter.call(el, value); el.dispatchEvent(new win.Event("input", { bubbles: true })); });
}
const buttons = () => Array.from(doc.querySelectorAll("button") as any[]);
const suggestButton = () => buttons().find((b: any) => /^SUGGEST GRID/.test(b.textContent.trim())) as any;
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
/** Search for M31 and pick the row the fixture serves. */
async function pickM31(): Promise<void> {
  typeInto(searchField(), "M 31");
  await flush(320);
  const row = buttons().find((b: any) => /M31/.test(b.textContent) && /Andromeda Galaxy/.test(b.textContent));
  assert(row, "the search listed no M31 result");
  click(row);
  await flush();
}

// ======================================================================= cases

await test("SUGGEST GRID on the sheet is sized for the picked ellipse", async () => {
  served = ELLIPSE_ENTRY;
  await pickM31();
  const label = suggestButton()?.textContent.trim();
  assert(label === "SUGGEST GRID 1x3",
    `the button offers "${label}" where the ellipse at rotation 0 wants 1x3 (the circle rule's is 2x3)`);
  assert(/ellipse/.test(String(q("framing-where")?.textContent)),
    "the hint does not say the grid was sized for the ellipse");
  click(suggestButton());
  await flush();
  assert(/1/.test(q("framing-cols").textContent) && !/2/.test(q("framing-cols").textContent),
    `COLS reads "${q("framing-cols").textContent}" after SUGGEST GRID 1x3`);
  assert(/3/.test(q("framing-rows").textContent), `ROWS reads "${q("framing-rows").textContent}"`);
});

await test("a row with no shape is sized for its long axis, and the hint says so", async () => {
  served = CIRCLE_ENTRY;
  await pickM31();
  const label = suggestButton()?.textContent.trim();
  assert(label === "SUGGEST GRID 2x3", `the button offers "${label}" for a circle (2x3)`);
  assert(/long axis/.test(String(q("framing-where")?.textContent)),
    "the hint does not say the grid was sized for the long axis");
  assert(!/ellipse/.test(String(q("framing-where")?.textContent)),
    "the hint claims an ellipse for a row with none");
});

// ------------------------------------------------------------------- tally
unmount();
const total = passed + failed;
console.log(`w3_9SuggestEllipseOnTheSheet.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
