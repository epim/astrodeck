// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w5PanelOverridesSheet.test.tsx - the Target modal's PANELS list sets and
// clears a panel's own exposure and count, MOUNTED in the sheet on the real
// store, and the saved flow carries it (#186, WP 5.4).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w5PanelOverridesSheet.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The sheet is shared by both UIs (the classic editor's and #/next's frame
// sheet mount the one `TargetFramingSheet`), so one mount grades both. The
// pure reading and writing of the text is w5PanelOverridesModel.test.tsx's.
//
// Each mutant below was run in a private copy of the file in the WP scratch
// directory, and the failure it produced is quoted.

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
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "Image", "URL", "fetch", "Blob", "WebSocket",
  "location", "history",
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
const { flowsApi } = await import("../../../../lib/flowsApi");
const { NODE_DEFS } = await import("../../nodeDefs");
const { framingApi, framingTiming } = await import("../framingApi");
const { api } = await import("../../../../api");
const { EXPOSURE_NOTE } = await import("../sections/PanelsSection");
const sheetModule = await import("../TargetFramingSheet");
const Sheet = sheetModule.default;
type FlowNodeRec = import("../../flowsTypes").FlowNodeRec;
type FlowEdgeRec = import("../../flowsTypes").FlowEdgeRec;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { unmount(); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
  }
}

const OPERATOR = ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"];

function target(over: Record<string, string | number> = {}, id = "n2"): FlowNodeRec {
  return {
    id, type: "target", x: 0, y: 0,
    params: {
      ...NODE_DEFS.target.params, name: "M31", ra: "00h 42m 44s", dec: "+41 00 00",
      rows: 2, cols: 2, overlap: 25, fovX: 2.0, fovY: 1.33, rotation: 0,
      angle: "Rotate to PA", counts: "Accepted subs", frameAnchor: "", ...over,
    },
  };
}
const CYCLE: FlowNodeRec = { id: "cy", type: "cycle", x: 260, y: 0, params: { ...NODE_DEFS.cycle.params } };
const E = (id: string, from: string, fromPort: string, to: string, toPort: string): FlowEdgeRec =>
  ({ id, from, fromPort, to, toPort });
const LANE = [E("a", "n2", "target", "cy", "run"), E("loop", "cy", "pass", "n2", "next")];

const COMPILED: any = {
  plan: {}, structural: [], issues: [], unmapped: [], readouts: {},
  rig: { fov_deg: [2.0, 1.33], fov_from: "", has_rotator: true, hop_s: null, hop_measured: false, hop_samples: 0 },
};

// ------------------------------------------------------------------ the network
(framingApi as any).mosaic = () => new Promise(() => {});
(framingApi as any).compileDraft = async () => COMPILED;
(flowsApi as any).compileDraft = async () => COMPILED;
framingTiming.settleMs = 0;
(api as any).get = async (path: string) => { throw new Error(`no network in this fixture: ${path}`); };

const real = useStore.getState();
let applyCalls: any[][] = [];
useStore.setState({
  flowsApplyFraming: (...a: any[]) => { applyCalls.push(a); return (real.flowsApplyFraming as any)(...a); },
  flowsFetchTonight: async () => {},
} as any);

function setup(nodes: FlowNodeRec[]): void {
  const graph: any = { nodes, edges: LANE };
  useStore.setState({
    principal: { role: "operator", email: null, caps: OPERATOR },
    // Offline throughout, so DONE opens on the mirror at once.
    wsConnected: false,
    status: { sky_angle: null } as any,
    config: null,
    site: null,
    flows: {
      ...FLOWS_INIT,
      record: { id: "f1", name: "M31 mosaic", folder: "", tagline: "", graph,
        created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: false },
      graph,
      compiled: COMPILED,
      progress: null,
      tonight: null,
      countsNote: null,
    },
  } as any);
  applyCalls = [];
}

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
const doneBtn = () => q("framing-done")?.closest("button") as any;
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
function typeInto(el: any, value: string): void {
  assert(el, "no field to type into");
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => { setter.call(el, value); el.dispatchEvent(new win.Event("input", { bubbles: true })); });
}
/** Leave a box: React's onBlur listens for `focusout`. */
function leave(el: any): void {
  act(() => { el.dispatchEvent(new win.FocusEvent("focusout", { bubbles: true })); });
}
async function done(): Promise<void> { click(doneBtn()); await flush(); }

const boxes = (label: string) => ({
  seconds: doc.querySelector(`[aria-label="Exposure in seconds for panel ${label}"]`) as any,
  count: doc.querySelector(`[aria-label="Frame count for panel ${label}"]`) as any,
});
/** A box's text, then leave it, as an operator does. */
function setBox(el: any, text: string): void { typeInto(el, text); leave(el); }
const saved = (): string => String(
  (useStore.getState().flows.graph as any).nodes.find((n: any) => n.id === "n2").params.panelExposure ?? "");
const why = (): string => String(q("framing-panel-exposure-why")?.textContent ?? "");

// ======================================================================

// MUTANT "boxes never drawn" (the sheet's `onExposure` prop to PanelsSection
// deleted). Observed:
//   x every panel that is shot has a pair of boxes, a skipped one has none: no boxes for panel 1-1
await test("every panel that is shot has a pair of boxes, a skipped one has none", async () => {
  setup([target({ skip: "2-2" }), CYCLE]);
  mount();
  for (const label of ["1-1", "1-2", "2-1"]) {
    assert(boxes(label).seconds && boxes(label).count, `no boxes for panel ${label}`);
    eq(boxes(label).seconds.getAttribute("placeholder"), "lane", `panel ${label}'s empty exposure box says the lane's`);
  }
  assert(!boxes("2-2").seconds, "a skipped panel has an exposure box");
  // A skipped panel taken back gets its boxes.
  click(doc.querySelector('[aria-label="Shoot panel 2-2"]'));
  assert(boxes("2-2").seconds, "a panel taken back has no boxes");
});

// MUTANT "note deleted" (PanelsSection: the `framing-exposure-note` row removed).
// Observed:
//   x the PANELS list says what a new exposure does to the panel's count: the note: expected
//     "Changing a panel's exposure starts that panel's count again at the new exposure. ...", got
//     undefined
await test("the PANELS list says what a new exposure does to the panel's count", async () => {
  setup([target(), CYCLE]);
  mount();
  eq(q("framing-exposure-note")?.textContent, "Changing a panel's exposure starts that panel's count again at "
    + "the new exposure. Frames already taken at the old exposure are kept, and count again if the override is "
    + "cleared.", "the note");
  eq(EXPOSURE_NOTE, q("framing-exposure-note")?.textContent, "the exported words are the drawn ones");
  eq(boxes("1-1").seconds.getAttribute("title"), EXPOSURE_NOTE, "the box carries it as its hint");
  unmount();
  // With every panel skipped there is no box to set, so no note.
  setup([target({ skip: "1-1, 1-2, 2-1, 2-2" }), CYCLE]);
  mount();
  eq(q("framing-exposure-note"), null, "a note over no boxes");
});

// MUTANT "exposure not written" (the sheet's onPanelExposure returns the draft
// unchanged). Observed, with three other tests of the file:
//   x setting a panel's exposure and count reaches the saved flow through DONE's apply: DONE's
//     patch: expected {"panelExposure":"1-1:30x40"}, got {}
await test("setting a panel's exposure and count reaches the saved flow through DONE's apply", async () => {
  setup([target(), CYCLE]);
  mount();
  setBox(boxes("1-1").seconds, "30");
  setBox(boxes("1-1").count, "40");
  eq(boxes("1-1").seconds.value, "30", "the exposure box after the count was committed");
  await done();
  eq(applyCalls.length, 1, "DONE applied once");
  eq(applyCalls[0][1], { panelExposure: "1-1:30x40" }, "DONE's patch");
  eq(saved(), "1-1:30x40", "the node's stored param");
});

await test("two panels each keep their own, in grid order", async () => {
  setup([target(), CYCLE]);
  mount();
  setBox(boxes("2-2").seconds, "90");
  setBox(boxes("1-2").seconds, "45.5");
  setBox(boxes("1-2").count, "8");
  await done();
  eq(applyCalls[0][1], { panelExposure: "1-2:45.5x8, 2-2:90" }, "DONE's patch");
});

// MUTANT "clear keeps the entry" (framingModel setPanelExposure: `others` is
// every panel when the exposure is blank). Observed:
//   x clearing a panel's boxes removes its override from the saved flow, and leaves the others:
//     DONE's patch: expected {"panelExposure":"2-1:60x5"}, got {}
await test("clearing a panel's boxes removes its override from the saved flow, and leaves the others", async () => {
  setup([target({ panelExposure: "1-1:30, 2-1:60x5" }), CYCLE]);
  mount();
  eq([boxes("1-1").seconds.value, boxes("1-1").count.value], ["30", ""], "panel 1-1's boxes open on its override");
  eq([boxes("2-1").seconds.value, boxes("2-1").count.value], ["60", "5"], "panel 2-1's boxes open on its override");
  eq(boxes("1-2").seconds.value, "", "a panel with none opens blank");
  setBox(boxes("1-1").seconds, "");
  await done();
  eq(applyCalls[0][1], { panelExposure: "2-1:60x5" }, "DONE's patch");
  eq(saved(), "2-1:60x5", "the stored param");
});

await test("the last override cleared leaves the param blank", async () => {
  setup([target({ panelExposure: "1-1:30x4" }), CYCLE]);
  mount();
  setBox(boxes("1-1").seconds, "");
  eq(boxes("1-1").count.value, "", "the count box follows its exposure when cleared");
  await done();
  eq(applyCalls[0][1], { panelExposure: "" }, "DONE's patch");
  eq(saved(), "", "the stored param");
});

// MUTANT "problem never said" (PanelsSection: `shown` made false). Observed:
//   x a pair that is not an override is said, once a box is left, and writes nothing: the words
//     after leaving the box with abc: ""
// (That a pair that is not an override writes nothing is the model's own
// guard, `setPanelExposure` returning null, and the model test holds it.)
await test("a pair that is not an override is said, once a box is left, and writes nothing", async () => {
  setup([target(), CYCLE]);
  mount();
  typeInto(boxes("1-1").seconds, "3.");
  eq(why(), "", "nothing is said while the number is still being typed");
  typeInto(boxes("1-1").seconds, "abc");
  leave(boxes("1-1").seconds);
  assert(/above zero/.test(why()), `the words after leaving the box with abc: ${JSON.stringify(why())}`);
  eq(boxes("1-1").seconds.getAttribute("aria-invalid"), "true", "the box is marked invalid");
  setBox(boxes("1-1").seconds, "");
  setBox(boxes("1-1").count, "5");
  assert(/needs an exposure/.test(why()), `the words for a count alone: ${JSON.stringify(why())}`);
  await done();
  eq(applyCalls.length === 0 || !("panelExposure" in applyCalls[0][1]), true, "DONE wrote no panelExposure");
  eq(saved(), "", "the stored param");
});

await test("a skip typed over a panel with an override keeps the override in the text", async () => {
  setup([target({ panelExposure: "1-1:30" }), CYCLE]);
  mount();
  click(doc.querySelector('[aria-label="Skip panel 1-1"]'));
  assert(!boxes("1-1").seconds, "a skipped panel keeps its boxes");
  click(doc.querySelector('[aria-label="Shoot panel 1-1"]'));
  eq(boxes("1-1").seconds.value, "30", "the panel taken back has its override");
  await done();
  eq(applyCalls.length === 0 || !("panelExposure" in applyCalls[0][1]), true, "a skip and an undo wrote no panelExposure");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`w5PanelOverridesSheet.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
