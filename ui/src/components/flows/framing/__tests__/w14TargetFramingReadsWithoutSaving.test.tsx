// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w14TargetFramingReadsWithoutSaving.test.tsx - opening the Target modal over
// an edited flow reads, and a READ does not save (#688, WP-86 ruling).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w14TargetFramingReadsWithoutSaving.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE RULING. WP-86 made `flowsFetchTonight` save the canvas before it reads
// (Tonight's STORY and PLAN must describe the graph on screen). The Target
// modal calls the same action once, on open, only to draw its campaign line
// from the flow as STORED, so with the flush it PUT an edited flow because a
// sheet had opened over it, and could raise the re-anchor toast and the
// counts-switch line outside any Tonight or RUN action. The orchestrator's ruling (backlog wave 14 integration): a
// READ must not save. `flowsFetchTonight` takes `{ flush?: boolean }`
// (default true) and the modal passes `flush: false`.
//
// THE REAL STORE, THE REAL SLICE, THE REAL SHEET. Only the network is
// replaced (`flowsApi.save` counts PUTs, `flowsApi.tonight` counts reads), so
// a sheet that asked for the default read, or a slice that flushed anyway,
// shows as a PUT. Unlike targetFramingSheet.test.tsx, `flowsFetchTonight` is
// NOT stubbed out here.
//
// Every case names the mutant it kills and quotes what that mutant produced
// when it was run from a byte-for-byte backup of the file named (restored and
// sha256-compared after each run, the mutant's marker grepped absent).

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
// Nothing here may reach a network: every route is replaced below, and a
// request that slips past them fails loudly.
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
const Sheet = (await import("../TargetFramingSheet")).default;
type FlowNodeRec = import("../../flowsTypes").FlowNodeRec;
type FlowEdgeRec = import("../../flowsTypes").FlowEdgeRec;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
/** Each test UNMOUNTS in `finally`: a failed assertion must not leave its
 *  sheet mounted, where its sky would keep the process alive. */
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { unmount(); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

// ------------------------------------------------------------------ fixtures
const OPERATOR = ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"];

/** M31 on a 3x2 grid with a FILTER CYCLE in its lane, so the block owns a
 *  stage and the sheet asks for Tonight (the campaign line's source). */
const TARGET: FlowNodeRec = {
  id: "n2", type: "target", x: 0, y: 0,
  params: {
    ...NODE_DEFS.target.params, name: "M31", ra: "00h 42m 44s", dec: "+41 00 00",
    rows: 2, cols: 3, overlap: 25, fovX: 2.0, fovY: 1.33, rotation: 0,
    angle: "Rotate to PA", counts: "Accepted subs", frameAnchor: "",
  },
};
const CYCLE: FlowNodeRec = { id: "cy", type: "cycle", x: 260, y: 0, params: { ...NODE_DEFS.cycle.params } };
const E = (id: string, from: string, fromPort: string, to: string, toPort: string): FlowEdgeRec =>
  ({ id, from, fromPort, to, toPort });
const LANE = [E("a", "n2", "target", "cy", "run"), E("loop", "cy", "pass", "n2", "next")];
const COMPILED = { plan: {}, structural: [], issues: [], unmapped: [], readouts: {}, rig: {} };
const STORED_ANSWER = { ok: true, night: null, marker: "the flow as stored" };

// ------------------------------------------------------------------ the network
let puts = 0;
let tonightReads = 0;
let storeCompiles = 0;
/** While set, the tonight request waits on it, so a test can look at the
 *  store WHILE the read is out. */
let tonightGate: Promise<void> | null = null;
(flowsApi as any).save = async (id: string, flow: any) => { puts++; return { ...flow, id, updated_ts: 2 }; };
(flowsApi as any).tonight = async () => {
  tonightReads++;
  if (tonightGate) await tonightGate;
  return STORED_ANSWER;
};
(flowsApi as any).compileDraft = async () => { storeCompiles++; return COMPILED; };
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };
(flowsApi as any).list = async () => [];
(flowsApi as any).folders = async () => [];
// The sheet's own requests: its panels never answer, its draft compile does.
(framingApi as any).mosaic = () => new Promise(() => { /* never answers */ });
(framingApi as any).compileDraft = async () => COMPILED;
framingTiming.settleMs = 0;

/** An edited flow open in the store: `dirty`, an answer in hand when asked. */
function setup(o: { tonight?: any } = {}): void {
  const graph = { nodes: [TARGET, CYCLE], edges: LANE };
  useStore.setState({
    principal: { role: "operator", email: null, caps: OPERATOR },
    wsConnected: true,
    status: { sky_angle: null } as any,
    config: null,
    site: null,
    framing: null,
    flows: {
      ...FLOWS_INIT,
      record: { id: "f1", name: "M31 mosaic", folder: "", tagline: "", graph,
        created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: false },
      graph,
      compiled: COMPILED,
      dirty: true,
      tonight: o.tonight ?? null,
    },
  } as any);
  puts = 0; tonightReads = 0; storeCompiles = 0; tonightGate = null;
}

const container = win.document.getElementById("root");
const root = createRoot(container);
function mount(nodeId = "n2"): void {
  act(() => { root.render(null); });
  act(() => { root.render(createElement(Sheet, { nodeId, onClose: () => {} })); });
}
function unmount(): void {
  act(() => { root.render(null); });
}
async function flush(ms = 5): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}

// ======================================================================

// MUTANT "the modal passes flush:true" (TargetFramingSheet.tsx: the
// `void fetchTonight({ flush: false });` made `void fetchTonight({ flush:
// true });`). Observed (4/5 passed):
//   x opening the Target modal on an edited flow reads Tonight and sends no
//     PUT: opening the Target modal over an edited flow saved it, so the
//     sheet read with a flush
//     expected "0 PUTs, 1 reads"
//     got      "1 PUTs, 1 reads"
await test("opening the Target modal on an edited flow reads Tonight and sends no PUT", async () => {
  setup();
  eq(useStore.getState().flows.dirty, true, "precondition: the flow is edited and unsaved");
  mount();
  await flush(20);
  eq(`${puts} PUTs, ${tonightReads} reads`, "0 PUTs, 1 reads",
    "opening the Target modal over an edited flow saved it, so the sheet read with a flush");
  eq(useStore.getState().flows.dirty, true, "the modal's read marked the edit saved");
  eq((useStore.getState().flows.tonight as any)?.marker, STORED_ANSWER.marker,
    "the modal's read never landed in the store");
});

// MUTANT "the default flushes no more" (flowsSlice.ts: `const flush =
// options?.flush !== false;` made `const flush = options?.flush === true;`).
// Observed (3/5 passed; the sheet's own case above stays green, it passes
// flush:false itself, and the event case below goes red with this one):
//   x CONTROL: the default read still saves an edited flow, which is what
//     Tonight's doors want: a Tonight read of an edited flow did not save it
//     first (#688)
//     expected "1 PUTs"
//     got      "0 PUTs"
await test("CONTROL: the default read still saves an edited flow, which is what Tonight's doors want", async () => {
  setup();
  await useStore.getState().flowsFetchTonight();
  eq(`${puts} PUTs`, "1 PUTs", "a Tonight read of an edited flow did not save it first (#688)");
  eq(useStore.getState().flows.dirty, false, "the flush left the saved edit marked unsaved");
});

// MUTANT "an event counts as no flush" (flowsSlice.ts: `const flush =
// options?.flush !== false;` made `const flush = !options || options.flush
// !== false ? !options : false;`, so any options object, an event included,
// reads as a pure read). Observed (4/5 passed):
//   x CONTROL: a caller that hands the action to an event handler gets the
//     default flush: an event passed as options turned the Tonight read into
//     a pure read, and the edit was not saved
//     expected "1 PUTs"
//     got      "0 PUTs"
await test("CONTROL: a caller that hands the action to an event handler gets the default flush", async () => {
  setup();
  const event = { type: "click", target: {} } as any;
  await (useStore.getState().flowsFetchTonight as any)(event);
  eq(`${puts} PUTs`, "1 PUTs",
    "an event passed as options turned the Tonight read into a pure read, and the edit was not saved");
});

// MUTANT "a pure read still clears the answer in hand" (flowsSlice.ts: the
// `flush && ` taken out of `...(flush && start.dirty && !start.record?.readonly
// ? { tonight: null } : {})`). Observed (4/5 passed):
//   x a pure read keeps the answer in hand while it is out, because it
//     replaces nothing: a read that saves nothing blanked the answer the
//     modal was drawing from
//     expected "the answer in hand"
//     got      "cleared"
await test("a pure read keeps the answer in hand while it is out, because it replaces nothing", async () => {
  const before = { ok: true, night: null, marker: "the answer in hand" };
  setup({ tonight: before });
  let release: () => void = () => {};
  tonightGate = new Promise<void>((resolve) => { release = resolve; });
  const reading = useStore.getState().flowsFetchTonight({ flush: false });
  await flush();
  eq((useStore.getState().flows.tonight as any)?.marker ?? "cleared", "the answer in hand",
    "a read that saves nothing blanked the answer the modal was drawing from");
  release();
  await reading;
  eq((useStore.getState().flows.tonight as any)?.marker, STORED_ANSWER.marker,
    "the read's answer did not replace the one in hand");
  eq(`${puts} PUTs`, "0 PUTs", "a pure read saved the flow");
});

// MUTANT "a pure read still waits for a compile" (flowsSlice.ts: the
// `if (!compiledIsCurrent(get().flows)) await get().flowsCompile();` moved
// out of the `if (flush)` block). Observed (4/5 passed):
//   x a pure read asks for no compile: it describes the flow as stored, and
//     PLAN is not its business: a read that saves nothing still held Tonight
//     for a compile it did not need
//     expected "0 compiles"
//     got      "1 compiles"
await test("a pure read asks for no compile: it describes the flow as stored, and PLAN is not its business", async () => {
  setup();
  eq(
    useStore.getState().flows.compiled !== null
      && (useStore.getState().flows.compiled as any).from === undefined,
    true, "precondition: the compile in hand was not made from the graph on screen");
  await useStore.getState().flowsFetchTonight({ flush: false });
  eq(`${storeCompiles} compiles`, "0 compiles",
    "a read that saves nothing still held Tonight for a compile it did not need");
  eq(`${puts} PUTs`, "0 PUTs", "a pure read saved the flow");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`w14TargetFramingReadsWithoutSaving.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
