// targetFramingSheet.test.tsx - the Target modal MOUNTED on the real store
// (#189 S4 items 1 and 4; spec 2026-09-23 flows mosaic, 2.1-2.7, 6.9).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/targetFramingSheet.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE. framingModel.test.ts holds the pure rules
// (the lock, the question, the patch). This file holds that the SHEET obeys
// them with a real store, a real flowsSlice and a real SkyCanvas under it:
//
//   1. DONE stays locked until the server has answered for the spec the draft
//      makes NOW, and an answer that lands after the draft moved on never
//      unlocks it; offline, or for a viewer, no request is made and DONE opens
//      on the mirror with the chip.
//   2. One DONE is one `flowsApplyFraming`, so one graph write and one
//      compile, however many sections changed.
//   3. The re-frame question appears only when the progress route reports
//      banked subs, a skip never asks, and the question goes once the draft
//      moves on (its RE-FRAME commits the draft as it is when pressed).
//      CANCEL writes nothing at all; while DONE's write waits for its
//      compile, CANCEL and Escape refuse and say why, the draft takes no edit
//      it could not keep, and the host's onClose runs once at most (#382),
//      a compile that fails included.
//   4. Opening block A never changes `store.framing`, the Atlas singleton
//      whose sharing put one target's mosaic on another target's flow.
//   5. A viewer never sees the altitude column or the night card: absent, not
//      empty.
//   6. A read-only Example opens in view mode and says why.
//   7. RUN's loop toggle opens on what the run does (#429): `targetLoops`,
//      the card's reader, so OFF over a tail loop beside a stale pass wire
//      from mid-lane (M12), graded on the panel-lane fixture's own case, and
//      switching it on repairs the lane to the graph the fixture records.
//
// Run mode (spec 2.6: the sheet read-only while the flow's session runs,
// drawing the live group) is runMode.test.tsx's, graded on the rig's and the
// progress route's recorded answers.
//
// THE PANELS ARE THE SERVER'S ANSWER, READ, NEVER COPIED:
// server/tests/fixtures/mosaic_panels_3x2.json, kept equal to the route by
// test_framing_panels_fixture.py. The compile answer's readouts are
// server/tests/fixtures/flow_readouts_m31.json, the route's recorded answer.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// s4-umodal-mut), never in the shared tree (#254), and the failure it
// produced is quoted verbatim. After the limit reset every one was run again
// against the current tree in s4-umodal-r2-mut (2026-09-27), with the three
// #382 mutants, and each was red with the failure quoted. S5 ran the three
// #382 mutants again on the committed tree (HEAD 029914bb) in s5-modal-mut
// (2026-09-28), each red with its quoted line, and then on its own tree.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";

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
// Nothing in this file may reach a network: every route is replaced below,
// and a request that slips past them fails loudly here.
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
const { createElement, act, StrictMode, useEffect } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { FLOWS_INIT } = await import("../../flowsSlice");
const { flowsApi } = await import("../../../../lib/flowsApi");
const { NODE_DEFS } = await import("../../nodeDefs");
const { framingApi, framingTiming } = await import("../framingApi");
const { OFFLINE_MIRROR, WAITING_FOR_PANELS } = await import("../framingModel");
const sheetModule = await import("../TargetFramingSheet");
const Sheet = sheetModule.default;
const { EXAMPLE_VIEW_ONLY, CHECKING } = sheetModule;
const { loadTargetFramingSheet, TargetFramingSheetLazy } = await import("../index");
type FlowNodeRec = import("../../flowsTypes").FlowNodeRec;
type FlowEdgeRec = import("../../flowsTypes").FlowEdgeRec;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
/** Each test UNMOUNTS in `finally`: a failed assertion must not leave its
 *  sheet mounted, where the next mount would reuse its state (same component,
 *  same key) and its sky would keep the process alive. */
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

// ------------------------------------------------------------------ fixtures
function readJson(rel: string): any {
  try {
    return JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8") as string);
  } catch (e) {
    throw new Error(`cannot read ${rel}: ${(e as Error).message}`);
  }
}
const PANELS_FX = readJson("../../../../../../server/tests/fixtures/mosaic_panels_3x2.json");
const READOUTS_FX = readJson("../../../../../../server/tests/fixtures/flow_readouts_m31.json");
/** The panel-lane cases test_flows_panel_lane.py holds to compile.py. */
const LANE_FX = readJson("../../../../../../server/tests/fixtures/panel_lane_cases.json");

const OPERATOR = ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"];
const VIEWER = ["view.status", "view.preview"];

/** M31, 3 columns by 2 rows of the fixture's 2.00 x 1.33 deg camera at 25%,
 *  camera fixed at north up, counting accepted subs, then a FILTER CYCLE
 *  whose "pass done" loops back to "next panel". Node id n2, the id the
 *  recorded readouts are keyed by. */
function target(over: Record<string, string | number> = {}): FlowNodeRec {
  return {
    id: "n2", type: "target", x: 0, y: 0,
    params: {
      ...NODE_DEFS.target.params, name: "M31", ra: "00h 42m 44s", dec: "+41 00 00",
      rows: 2, cols: 3, overlap: 25, fovX: 2.0, fovY: 1.33, rotation: 0,
      angle: "Rotate to PA", counts: "Accepted subs", frameAnchor: "", ...over,
    },
  };
}
const CYCLE: FlowNodeRec = { id: "cy", type: "cycle", x: 260, y: 0, params: { ...NODE_DEFS.cycle.params } };
const E = (id: string, from: string, fromPort: string, to: string, toPort: string): FlowEdgeRec =>
  ({ id, from, fromPort, to, toPort });
const LANE = [E("a", "n2", "target", "cy", "run"), E("loop", "cy", "pass", "n2", "next")];

const COMPILED = {
  plan: {}, structural: [], issues: [], unmapped: [],
  readouts: READOUTS_FX.readouts, rig: READOUTS_FX.rig,
};

/** The progress route's block for n2: `banked` subs on the 3x2. */
function progressWith(banked: number): any {
  const panels = PANELS_FX.response.panels.map((p: any, i: number) => ({
    target_id: `t${i}`, name: `M31 ${p.row + 1}-${p.col + 1}`, row: p.row, col: p.col,
    banked: i === 0 ? banked : 0, owed: 80, total: 80, steps: [],
  }));
  return {
    flow_id: "f1",
    session: banked > 0 ? { id: "s1", status: "dormant", nights: 1, count_mode: "accepted" } : null,
    blocks: [{ node_id: "n2", name: "M31", kind: "target", banked, owed: 480 - banked, total: 480,
      panels, grid: { rows: 2, cols: 3 }, skipped: [] }],
    orphaned: { frames: 0, steps: 0 },
  };
}

// ------------------------------------------------------------------ the network
interface Pending { req: any; transit: boolean; resolve: (v: any) => void; reject: (e: any) => void }
let mosaicCalls: Pending[] = [];
(framingApi as any).mosaic = (req: any, transit: boolean) =>
  new Promise((resolve, reject) => { mosaicCalls.push({ req, transit, resolve, reject }); });
(framingApi as any).compileDraft = async () => COMPILED;
let storeCompiles = 0;
/** While set, the STORE's compile waits on it, so DONE's write stays in
 *  flight for as long as a test holds it (#382). Null in every other test. */
let compileGate: Promise<void> | null = null;
(flowsApi as any).compileDraft = async () => {
  storeCompiles++;
  if (compileGate) await compileGate;
  return COMPILED;
};
framingTiming.settleMs = 0;

// ------------------------------------------------------------------ the store
// The real slice actions, counted: DONE is graded on what reaches them.
const real = useStore.getState();
let applyCalls: any[][] = [];
let settingCalls: any[][] = [];
let framingWrites = 0;
useStore.setState({
  flowsApplyFraming: (...a: any[]) => { applyCalls.push(a); return (real.flowsApplyFraming as any)(...a); },
  flowsSetSetting: (...a: any[]) => { settingCalls.push(a); return (real.flowsSetSetting as any)(...a); },
  flowsFetchTonight: async () => {},
  setFraming: (patch: any) => { framingWrites++; (real.setFraming as any)(patch); },
} as any);

function setup(o: {
  caps?: string[]; ws?: boolean; progress?: any; readonly?: boolean;
  params?: Record<string, string | number>; framing?: any; tonight?: any;
  graph?: any;
} = {}): void {
  const graph = o.graph ?? { nodes: [target(o.params), CYCLE], edges: LANE };
  useStore.setState({
    principal: { role: o.caps === VIEWER ? "viewer" : "operator", email: null, caps: o.caps ?? OPERATOR },
    wsConnected: o.ws ?? true,
    status: { sky_angle: null } as any,
    config: null,
    site: null,
    framing: o.framing ?? null,
    flows: {
      ...FLOWS_INIT,
      record: { id: "f1", name: "M31 mosaic", folder: "", tagline: "", graph,
        created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: o.readonly ?? false },
      graph,
      compiled: COMPILED,
      progress: o.progress ?? null,
      tonight: o.tonight ?? null,
    },
  } as any);
  mosaicCalls = []; applyCalls = []; settingCalls = []; storeCompiles = 0; framingWrites = 0;
}

const container = win.document.getElementById("root");
const root = createRoot(container);
let closes = 0;
/** A fresh sheet: whatever was mounted is unmounted first, so no draft
 *  survives from one mount to the next. */
function mount(nodeId = "n2"): void {
  act(() => { root.render(null); });
  act(() => { root.render(createElement(Sheet, { nodeId, onClose: () => { closes++; } })); });
}
function unmount(): void {
  act(() => { root.render(null); });
  closes = 0;
}
async function flush(ms = 5): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}
const doc = win.document;
const q = (id: string) => doc.querySelector(`[data-testid="${id}"]`) as any;
const doneBtn = () => q("framing-done")?.closest("button") as any;
const locked = (b: any) => b?.getAttribute("aria-disabled") === "true";
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
function buttonByText(text: string): any {
  return Array.from(doc.querySelectorAll("button") as any[]).find((b: any) => b.textContent.includes(text));
}
function typeInto(el: any, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => { setter.call(el, value); el.dispatchEvent(new win.Event("input", { bubbles: true })); });
}
function answer(p: Pending, extra: Record<string, unknown> = {}): Promise<void> {
  return act(async () => { p.resolve({ ...PANELS_FX.response, ...extra }); await Promise.resolve(); });
}
function fail(p: Pending): Promise<void> {
  return act(async () => { p.reject(new Error("503 from the route")); await Promise.resolve(); });
}
function choose(sel: any, value: string): void {
  assert(sel, "no select to choose from");
  const setter = Object.getOwnPropertyDescriptor(win.HTMLSelectElement.prototype, "value")!.set!;
  act(() => { setter.call(sel, value); sel.dispatchEvent(new win.Event("change", { bubbles: true })); });
}

// ======================================================================
// the door

await test("the sheet is a lazy default export whose mount carries the view marker", async () => {
  const mod = await loadTargetFramingSheet();
  assert(mod.default === Sheet, "index.ts's loader does not resolve to the sheet's default export");
  assert(typeof TargetFramingSheetLazy === "object" && (TargetFramingSheetLazy as any).$$typeof,
    "TargetFramingSheetLazy is not a React.lazy component");
  setup({ ws: false });
  mount();
  assert(q("target-framing-sheet"), "no data-testid=\"target-framing-sheet\" view marker");
  unmount();
});

// ======================================================================
// 1. DONE and the server's answer

// MUTANT "any answer unlocks" (the sheet's mosaic answer handler loses its
// `latestKey.current !== sent` guard and tags the answer with
// `latestKey.current`, the key of whatever the draft asks NOW). Observed:
//   x DONE stays locked until the answer for the CURRENT spec, and a late answer never unlocks it: a
//     late answer for the 3x2 unlocked DONE on the 4x2
await test("DONE stays locked until the answer for the CURRENT spec, and a late answer never unlocks it", async () => {
  setup();
  mount();
  assert(locked(doneBtn()), "DONE is live before the server has answered");
  eq(doneBtn().getAttribute("title"), WAITING_FOR_PANELS, "DONE's reason");
  click(doneBtn());
  eq(applyCalls.length, 0, "a locked DONE applied the framing");
  eq(q("framing-explain")?.textContent, WAITING_FOR_PANELS, "pressing a locked DONE says why");
  await flush();
  eq(mosaicCalls.length, 1, "requests after the first settle");
  const first = mosaicCalls[0];
  eq(first.req.anchor, undefined, "a block with no stored anchor sends none");
  eq(first.transit, true, "an operator asks for the panels' peak altitudes");
  // The operator adds a column while the first answer is still out.
  click(buttonByText("more cols"));
  await flush();
  eq(mosaicCalls.length, 2, "requests after the second settle");
  eq(mosaicCalls[1].req.cols, 4, "the second request's cols");
  await answer(first);
  assert(locked(doneBtn()), "a late answer for the 3x2 unlocked DONE on the 4x2");
  await answer(mosaicCalls[1], { panels: [] });
  assert(!locked(doneBtn()), "the answer for the current spec did not unlock DONE");
  unmount();
});

// A FAILURE is an answer too: doneState opens DONE on the mirror when the
// current spec's request fails, so a failure recorded against the wrong spec
// would unlock DONE just as a late success would.
// MUTANT "a late failure unlocks" (the failure handler loses its
// `latestKey.current !== sent` guard and tags the failure with
// `latestKey.current`). Observed (verifier, scratch copy
// s4-umodal-verify-mut):
//   x a FAILED answer for an old spec never unlocks DONE; a failure for the current one opens it on
//     the mirror: a late failure for the 3x2 unlocked DONE on the 4x2
await test("a FAILED answer for an old spec never unlocks DONE; a failure for the current one opens it on the mirror", async () => {
  setup();
  mount();
  await flush();
  const first = mosaicCalls[0];
  click(buttonByText("more cols"));
  await flush();
  eq(mosaicCalls.length, 2, "requests after the second settle");
  await fail(first);
  await flush();
  assert(locked(doneBtn()), "a late failure for the 3x2 unlocked DONE on the 4x2");
  assert(q("framing-chip") === null, "a late failure for the 3x2 put the mirror chip on the 4x2");
  // Control: the current spec's own failure is all the server will say
  // about it, so DONE opens on the mirror and says so.
  await fail(mosaicCalls[1]);
  await flush();
  assert(!locked(doneBtn()), "a failure for the current spec left DONE locked");
  eq(q("framing-chip")?.textContent, OFFLINE_MIRROR, "the chip after the current spec failed");
});

// MUTANT "offline asks the server" (the ServerView the sheet builds says
// `offline: false` and `canViewSiteDerived: true` whatever the store says).
// Observed:
//   x offline, or for a viewer, no request is made and DONE opens on the mirror with the chip:
//     offline: DONE is locked with nothing to wait for
//   (Six more tests went red with it, each pressing a DONE the mutant keeps
//   locked, e.g. "one DONE is one flowsApplyFraming carrying every section's
//   change, and one compile: flowsApplyFraming calls for one DONE: expected 1,
//   got 0". Four when first run; the two #382 tests below press DONE too.)
await test("offline, or for a viewer, no request is made and DONE opens on the mirror with the chip", async () => {
  for (const [what, o] of [["offline", { ws: false }], ["a viewer", { caps: VIEWER }]] as const) {
    setup(o as any);
    mount();
    await flush();
    eq(mosaicCalls.length, 0, `${what}: requests to a route that cannot answer`);
    assert(!locked(doneBtn()), `${what}: DONE is locked with nothing to wait for`);
    eq(q("framing-chip")?.textContent, OFFLINE_MIRROR, `${what}: the chip`);
    unmount();
  }
  // Control: online, as an operator, there is no chip.
  setup();
  mount();
  assert(q("framing-chip") === null, "an operator online sees the offline chip");
  unmount();
});

// MUTANT "mirror only" (the sky's panels are always the client mirror's,
// `(null ?? mirror)`, whatever the server answered). Observed:
//   x the sky previews the grid with the mirror, then redraws its labels from the server's answer,
//     and a tap skips: panel 1-1's label did not move to the server's position: top 157.55625 ->
//     157.55625
await test("the sky previews the grid with the mirror, then redraws its labels from the server's answer, and a tap skips", async () => {
  setup();
  mount();
  const label = (r: number, c: number) =>
    doc.querySelector(`[data-role="panel-label"][data-row="${r}"][data-col="${c}"]`) as any;
  assert(label(0, 0), "no label for panel 1-1 before the answer (the mirror draws none)");
  const before = parseFloat(label(0, 0).style.top);
  await flush();
  // The server's answer, every panel 0.3 deg further north than the mirror
  // would put it: a label drawn from the answer moves up the chart.
  const north = PANELS_FX.response.panels.map((p: any) => ({ ...p, dec_deg: p.dec_deg + 0.3 }));
  await answer(mosaicCalls[0], { panels: north });
  const after = parseFloat(label(0, 0).style.top);
  assert(after < before - 5, `panel 1-1's label did not move to the server's position: top ${before} -> ${after}`);
  eq(label(0, 0).querySelector('[data-role="panel-order"]')?.textContent, "1", "panel 1-1's run-order number");
  // A tap on a panel (its label is the keyboard channel of the same tap)
  // toggles its skip, in the draft and on the sky.
  click(label(0, 0));
  eq(label(0, 0).getAttribute("data-state"), "skipped", "panel 1-1 after a tap");
  eq(doc.querySelector('[data-panel="1-1"] button')?.getAttribute("aria-pressed"), "false", "PANELS' toggle for 1-1 after the tap");
  unmount();
});

function ptr(type: string, x: number, y: number): any {
  const ev = new win.Event(type, { bubbles: true, cancelable: true });
  Object.assign(ev, { pointerId: 1, pointerType: "mouse", button: 0, buttons: 1, isPrimary: true, clientX: x, clientY: y });
  return ev;
}
/** A mouse drag across the sky. SkyCanvas measures its box at 360 CSS px in
 *  jsdom (its ResizeObserver never fires), so (180, 180) is its centre. */
function drag(el: any, x0: number, y0: number, x1: number, y1: number): void {
  act(() => { el.dispatchEvent(ptr("pointerdown", x0, y0)); });
  act(() => { el.dispatchEvent(ptr("pointermove", (x0 + x1) / 2, (y0 + y1) / 2)); });
  act(() => { el.dispatchEvent(ptr("pointermove", x1, y1)); });
  act(() => { el.dispatchEvent(ptr("pointerup", x1, y1)); });
}

// MUTANT "move grid moves the sky" (FramingSky passes no
// `onFrameCenterChange`, so SkyCanvas treats a MOVE GRID drag as a pan).
// Observed:
//   x MOVE SKY drags the sky under the pinned grid, MOVE GRID drags the grid over a still sky, and
//     both move the centre: MOVE GRID did not move the mosaic centre: RA stayed 00h 46m 58s
await test("MOVE SKY drags the sky under the pinned grid, MOVE GRID drags the grid over a still sky, and both move the centre", async () => {
  setup({ ws: false });
  mount();
  const app = () => doc.querySelector('[data-testid="framing-sky"] [role="application"]') as any;
  const label = () => doc.querySelector('[data-role="panel-label"][data-row="0"][data-col="0"]') as any;
  const ra = () => (doc.querySelector("#tfs-ra") as any).value as string;
  const x0 = parseFloat(label().style.left);
  const ra0 = ra();
  drag(app(), 180, 200, 216, 200);
  assert(Math.abs(parseFloat(label().style.left) - x0) < 0.5,
    `MOVE SKY moved the grid on the chart: left ${x0} -> ${label().style.left}`);
  assert(ra() !== ra0, `MOVE SKY did not move the mosaic centre: RA stayed ${ra0}`);
  click(buttonByText("MOVE GRID"));
  const x1 = parseFloat(label().style.left);
  const ra1 = ra();
  drag(app(), 180, 200, 216, 200);
  const x2 = parseFloat(label().style.left);
  assert(Math.abs(x2 - (x1 + 36)) < 1.5, `MOVE GRID: the grid did not follow the pointer 36 px: left ${x1} -> ${x2}`);
  assert(ra() !== ra1, `MOVE GRID did not move the mosaic centre: RA stayed ${ra1}`);
});

// MUTANT "anchor not sent" (the sheet builds its request with
// `mosaicRequest(draft, undefined)`). Observed:
//   x the stored anchor goes with the request, and the server's reframe decides the line and the
//     question: a move past the threshold: the request's anchor: expected "{\"ra_hours\":0.712222,\"de
//     c_deg\":41.0,\"rows\":2,\"cols\":3,\"overlap\":0.25,\"rotation_deg\":0.0,\"fov_x_deg\":2.0,\"fov
//     _y_deg\":1.33}", got undefined
await test("the stored anchor goes with the request, and the server's reframe decides the line and the question", async () => {
  const ANCHOR = '{"ra_hours":0.712222,"dec_deg":41.0,"rows":2,"cols":3,"overlap":0.25,"rotation_deg":0.0,"fov_x_deg":2.0,"fov_y_deg":1.33}';
  for (const [what, reframe, line, asks] of [
    ["a move past the threshold", { carry: false, max_move_deg: 14.8 / 60, threshold_deg: 10 / 60, reason: "move" },
      "moved 14.8', more than the 10.0' this grid allows: counts restart", true],
    ["a move inside it", { carry: true, max_move_deg: 4.2 / 60, threshold_deg: 10 / 60, reason: "move" },
      "moved 4.2' of the 10.0' this grid allows: counts carry over", false],
  ] as const) {
    setup({ params: { frameAnchor: ANCHOR }, progress: progressWith(212) });
    mount();
    typeInto(doc.querySelector("#tfs-ra"), "00h 43m 40s");
    await flush();
    const last = mosaicCalls[mosaicCalls.length - 1];
    eq(last.req.anchor, ANCHOR, `${what}: the request's anchor`);
    await answer(last, { reframe });
    eq(q("framing-move")?.textContent, line, `${what}: the strip's move line`);
    click(doneBtn());
    await flush();
    if (asks) {
      eq(q("framing-question")?.textContent.includes(
        "Re-framing moves the panels 14.8', more than the 10.0' this grid allows, so all 6 panels start from zero: 212 banked subs"),
      true, `${what}: the question, got ${JSON.stringify(q("framing-question")?.textContent)}`);
      eq(applyCalls.length, 0, `${what}: applied before the question was answered`);
    } else {
      assert(q("framing-question") === null, `${what}: asked`);
      eq(applyCalls.length, 1, `${what}: applied`);
    }
    unmount();
  }
});

// ======================================================================
// 2. one DONE, one write, one compile

// MUTANT "apply per section" (commit calls `flowsApplyFraming` once per
// changed key, `Promise.all(Object.entries(patch).map(([k, v]) =>
// applyFraming(nodeId, { [k]: v }, loopArg)))`). Observed:
//   x one DONE is one flowsApplyFraming carrying every section's change, and one compile:
//     flowsApplyFraming calls for one DONE: expected 1, got 5
//   (The store.framing test went red with it: "opening block A, dragging its
//   sky, zooming and pressing DONE never changes store.framing: the sheet's
//   DONE did not reach flowsApplyFraming", its guard against anything but one
//   call.)
await test("one DONE is one flowsApplyFraming carrying every section's change, and one compile", async () => {
  setup({ ws: false });
  mount();
  typeInto(doc.querySelector("#tfs-name"), "M31 wide");            // WHERE
  click(buttonByText("more cols"));                                  // GRID
  click(buttonByText("+15"));                                        // ANGLE
  click(buttonByText("more passes per visit"));                      // RUN
  click(buttonByText("more tries"));                                 // CENTRING
  click(doneBtn());
  await flush();
  eq(applyCalls.length, 1, "flowsApplyFraming calls for one DONE");
  const [id, patch] = applyCalls[0];
  eq(id, "n2", "the node DONE framed");
  eq(Object.keys(patch).sort(), ["centerTries", "cols", "name", "passes", "rotation"], "the one patch's keys");
  eq(storeCompiles, 1, "compiles for one DONE");
  const n = useStore.getState().flows.graph.nodes.find((x: any) => x.id === "n2")!;
  eq([n.params.name, n.params.cols, n.params.rotation, n.params.passes, n.params.centerTries],
    ["M31 wide", 4, 15, 2, 4], "the node after DONE");
  eq(closes, 1, "DONE closes the sheet once the compile has answered");
  unmount();
  // Control: DONE on an untouched draft writes nothing and compiles nothing.
  setup({ ws: false });
  mount();
  click(doneBtn());
  await flush();
  eq(applyCalls.length, 1, "an untouched DONE still goes through flowsApplyFraming once");
  eq(applyCalls[0][1], {}, "an untouched DONE's patch");
  eq(storeCompiles, 0, "compiles for an untouched DONE");
  eq(useStore.getState().flows.dirty, false, "an untouched DONE marked the flow dirty");
  unmount();
});

// ======================================================================
// 3. the re-frame question

// MUTANT "skip asks" (onDone asks whenever the patch is non-empty and the
// block has banked subs, `Object.keys(patch).length > 0 &&
// bankedSubs(progressBlock) > 0 || decision.ask`). Observed:
//   x the re-frame question appears only with banked subs, and a skip never asks: a skip asked the
//     re-frame question
//   (The anchor test went red with it too: a carried move asked.)
await test("the re-frame question appears only with banked subs, and a skip never asks", async () => {
  // A skip, with 212 subs banked: straight through.
  setup({ ws: false, progress: progressWith(212) });
  mount();
  click(doc.querySelector('[aria-label="Skip panel 1-1"]'));
  click(doneBtn());
  await flush();
  assert(q("framing-question") === null, "a skip asked the re-frame question");
  eq(applyCalls.length, 1, "a skip's DONE applied");
  eq(applyCalls[0][1], { skip: "1-1" }, "a skip's patch");
  unmount();
  // A grid change, with 212 banked: it asks, applies nothing until answered.
  setup({ ws: false, progress: progressWith(212) });
  mount();
  click(buttonByText("more cols"));
  click(doneBtn());
  await flush();
  eq(q("framing-question")?.textContent.includes(
    "Changing the grid from 3x2 to 4x2 means all 8 panels start from zero: 212 banked subs belong to the old layout and stay on disk."),
  true, `the grid question, got ${JSON.stringify(q("framing-question")?.textContent)}`);
  eq(applyCalls.length, 0, "DONE applied before the question was answered");
  click(buttonByText("RE-FRAME"));
  await flush();
  eq(applyCalls.length, 1, "RE-FRAME applied once");
  unmount();
  // Control: the same grid change with nothing banked does not ask.
  setup({ ws: false, progress: progressWith(0) });
  mount();
  click(buttonByText("more cols"));
  click(doneBtn());
  await flush();
  assert(q("framing-question") === null, "a grid change with nothing banked asked");
  eq(applyCalls.length, 1, "the unbanked grid change applied");
  unmount();
});

// RE-FRAME commits the draft as it is WHEN PRESSED. A question left standing
// while the operator keeps editing would commit a layout it never described,
// and would do it while DONE itself is locked waiting for that layout's
// answer.
// MUTANT "question outlives the draft" (the effect that drops a stale
// explanation leaves the question alone: `setQuestion(null)` removed, which
// is the sheet as first submitted, #377). Observed (verifier, scratch copy
// s4-umodal-verify-mut):
//   x the re-frame question goes when the draft moves on, so RE-FRAME never commits a layout it did
//     not ask about: the question about the 4x2 still stands on the 4x3
await test("the re-frame question goes when the draft moves on, so RE-FRAME never commits a layout it did not ask about", async () => {
  setup({ progress: progressWith(212) });
  mount();
  await flush();
  await answer(mosaicCalls[0]);
  click(buttonByText("more cols"));
  await flush();
  await answer(mosaicCalls[1]);
  assert(!locked(doneBtn()), "DONE is locked with the 4x2's answer in hand");
  click(doneBtn());
  await flush();
  assert(q("framing-question"), "a grid change with 212 banked did not ask");
  // Control: with nothing changed, the question stands.
  await flush();
  assert(q("framing-question"), "the question went with nothing changed");
  // The operator keeps editing instead of answering: a third row.
  click(buttonByText("more rows"));
  await flush();
  eq(mosaicCalls.length, 3, "requests after the third settle");
  assert(locked(doneBtn()), "DONE is live before the 4x3's answer");
  assert(q("framing-question") === null, "the question about the 4x2 still stands on the 4x3");
  assert(buttonByText("RE-FRAME") === undefined, "RE-FRAME is still offered with the 4x3's answer out");
  eq(applyCalls.length, 0, "applied before any question about the 4x3");
});

// MUTANT "cancel commits" (CANCEL's handler runs DONE's `commit` instead of
// `onClose`). Observed (verifier, scratch copy s4-umodal-verify-mut):
//   x CANCEL writes nothing: no framing, no flow setting, no compile: flowsApplyFraming calls for
//     CANCEL: expected 0, got 1
//   (Re-run in s4-umodal-r2-mut, the #382 test's control went red with it too:
//   "... and the sheet closes once: CANCEL with nothing in flight: closes:
//   expected 1, got 0": the mutant's CANCEL is DONE's commit, which closes
//   only after an await, and that control reads the count straight after the
//   press.)
await test("CANCEL writes nothing: no framing, no flow setting, no compile", async () => {
  setup({ ws: false });
  const before = useStore.getState().flows.graph;
  mount();
  typeInto(doc.querySelector("#tfs-name"), "M31 wide");               // WHERE
  click(buttonByText("more cols"));                                     // GRID
  click(buttonByText("+15"));                                           // ANGLE
  choose(doc.querySelector("#tfs-when-waiting"), "Wait for the mosaic"); // RUN, the flow setting
  const cancel = q("framing-header").querySelector("button") as any;
  eq(cancel?.textContent, "CANCEL", "the header's first button");
  click(cancel);
  await flush();
  eq(closes, 1, "CANCEL closes the sheet");
  eq(applyCalls.length, 0, "flowsApplyFraming calls for CANCEL");
  eq(settingCalls.length, 0, "flowsSetSetting calls for CANCEL");
  eq(storeCompiles, 0, "compiles for CANCEL");
  assert(useStore.getState().flows.graph === before, "CANCEL changed the graph");
  eq(useStore.getState().flows.dirty, false, "CANCEL marked the flow dirty");
});

// DONE writes the framing, then waits for the compile that write starts, and
// closes the sheet when it answers. By then the write has happened, so CANCEL
// can no longer mean "write nothing"; and a close taken in that window was
// followed by DONE's own when the compile answered, so the host's onClose ran
// twice, which in #/next popped the sheet under the modal or left Flows
// through the browser's Back (#382). While the write is in flight CANCEL and
// Escape refuse, and say why. The wait is bounded: the compile times out
// with the api's 15 s, and flowsCompile settles either way.
// Observed on the sheet as it stood before this test (the shared tree, before
// the fix):
//   x while DONE's write waits for its compile, CANCEL and Escape refuse and say why, and the sheet
//     closes once: CANCEL's reason while DONE writes: expected "writing the framing and waiting for
//     the compiler", got null
// MUTANT "cancel live while DONE writes" (CANCEL's HonestButton given
// `reason={null}` whatever `busy` says). Observed (scratch copy
// s4-umodal-r2-mut):
//   x while DONE's write waits for its compile, CANCEL and Escape refuse and say why, and the sheet
//     closes once: CANCEL's reason while DONE writes: expected "writing the framing and waiting for
//     the compiler", got null
// MUTANT "escape live while DONE writes" (the Overlay handed the host's
// `onClose` again instead of `requestClose`). Observed (scratch copy
// s4-umodal-r2-mut):
//   x while DONE's write waits for its compile, CANCEL and Escape refuse and say why, and the sheet
//     closes once: closes after Escape, with DONE's compile still out: expected 0, got 1
// MUTANT "escape refuses silently" (`requestClose` returns on `cancelReason`
// without `setExplained`). Observed (verifier, scratch copy
// s4-umodal-verify-r2-mut):
//   x while DONE's write waits for its compile, CANCEL and Escape refuse and say why, and the sheet
//     closes once: what Escape said while DONE writes: expected "writing the framing and waiting for
//     the compiler", got undefined
//   (Before the verifier split the two ways out onto sheets of their own, this
//   mutant was green: Escape was pressed after CANCEL had already put the same
//   sentence on the explain line, so nothing Escape did could change it.)
// MUTANT "cancel refuses silently" (CANCEL's HonestButton handed
// `onExplain={() => {}}`). Observed (verifier, scratch copy
// s4-umodal-verify-r2-mut):
//   x while DONE's write waits for its compile, CANCEL and Escape refuse and say why, and the sheet
//     closes once: what CANCEL said while DONE writes: expected "writing the framing and waiting for
//     the compiler", got undefined
await test("while DONE's write waits for its compile, CANCEL and Escape refuse and say why, and the sheet closes once", async () => {
  // Each way out on a sheet of its own, pressed while nothing has explained
  // itself yet, so the explain line after the press can only be that press's.
  for (const how of ["CANCEL", "Escape"] as const) {
    setup({ ws: false });
    mount();
    typeInto(doc.querySelector("#tfs-name"), "M31 wide");
    let release: () => void = () => {};
    compileGate = new Promise<void>((r) => { release = r; });
    try {
      click(doneBtn());
      await flush();
      eq(applyCalls.length, 1, `${how}: DONE's writes`);
      eq(storeCompiles, 1, `${how}: compiles DONE started`);
      eq(closes, 0, `${how}: closes before DONE's compile answered`);
      const cancel = q("framing-header").querySelector("button") as any;
      eq(cancel?.textContent, "CANCEL", "the header's first button");
      eq(cancel.getAttribute("title"), CHECKING, "CANCEL's reason while DONE writes");
      assert(q("framing-explain") === null, `${how}: an explanation on screen before any press`);
      if (how === "CANCEL") click(cancel);
      else act(() => { doc.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
      eq(closes, 0, `closes after ${how}, with DONE's compile still out`);
      eq(q("framing-explain")?.textContent, CHECKING, `what ${how} said while DONE writes`);
    } finally {
      // Released and flushed HERE, so a DONE left waiting by a failed
      // assertion finishes inside this test and cannot close the next one's
      // sheet.
      release();
      compileGate = null;
      await flush();
    }
    eq(closes, 1, `${how}: closes once DONE's compile answered`);
    eq(useStore.getState().flows.graph.nodes.find((n: any) => n.id === "n2")!.params.name, "M31 wide",
      `${how}: the name DONE wrote`);
    unmount();
  }
  // Control: with no write in flight, CANCEL and Escape each close, once.
  for (const how of ["CANCEL", "Escape"] as const) {
    setup({ ws: false });
    mount();
    const cancel = q("framing-header").querySelector("button") as any;
    eq(cancel.getAttribute("title"), null, `${how}: CANCEL's reason with nothing in flight`);
    if (how === "CANCEL") click(cancel);
    else act(() => { doc.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
    eq(closes, 1, `${how} with nothing in flight: closes`);
    unmount();
  }
});

// The host can still take the sheet down while DONE's write is in flight (a
// browser Back in #/next unmounts it): that close has happened, and DONE's own
// close when the compile answers would be a second one, popping whatever sits
// under the modal (#382).
// Observed on the sheet as it stood before this test (the shared tree, before
// the fix):
//   x a DONE whose sheet the host has already taken down closes nothing when its compile answers:
//     onClose calls after the host had already taken the sheet down: expected 0, got 1
// MUTANT "a closed sheet closes again" (commit calls `onClose()` without
// asking whether the sheet is still mounted). Observed (scratch copy
// s4-umodal-r2-mut):
//   x a DONE whose sheet the host has already taken down closes nothing when its compile answers:
//     onClose calls after the host had already taken the sheet down: expected 0, got 1
await test("a DONE whose sheet the host has already taken down closes nothing when its compile answers", async () => {
  setup({ ws: false });
  mount();
  typeInto(doc.querySelector("#tfs-name"), "M31 wide");
  let release: () => void = () => {};
  compileGate = new Promise<void>((r) => { release = r; });
  try {
    click(doneBtn());
    await flush();
    eq(applyCalls.length, 1, "DONE's writes");
    act(() => { root.render(null); });
  } finally {
    release();
    compileGate = null;
    await flush();
  }
  eq(closes, 0, "onClose calls after the host had already taken the sheet down");
  // Control: the write DONE made before the host closed it stands.
  eq(useStore.getState().flows.graph.nodes.find((n: any) => n.id === "n2")!.params.name, "M31 wide",
    "the name DONE wrote");
});

// A compile that FAILS is the other way DONE's wait ends (a relay drop, a
// 5xx, the api's 15 s timeout). flowsCompile catches it and keeps the last
// good answer, so the write DONE made stands and the sheet closes, once: the
// lock traps no one, and nothing about a failure closes twice (#382, checked
// again by S5 on the committed tree).
// MUTANT "a failed compile rejects" (flowsSlice's flowsCompile rethrows from
// its catch, after logging, so flowsApplyFraming rejects and DONE's commit
// throws before its close). Observed (scratch copy s5-modal-mut):
//   x a DONE whose compile fails closes the sheet once, and CANCEL and Escape in the wait close
//     nothing: closes once DONE's compile had failed: expected 1, got 0
// MUTANT "cancel live while DONE writes" (CANCEL's HonestButton given
// `reason={null}`), run against this case too. Observed (scratch copy
// s5-modal-mut):
//   x a DONE whose compile fails closes the sheet once, and CANCEL and Escape in the wait close
//     nothing: closes after CANCEL, with DONE's compile still out: expected 0, got 1
// MUTANT "escape live while DONE writes" (the Overlay handed the host's
// `onClose`), likewise. Observed (scratch copy s5-modal-mut):
//   x a DONE whose compile fails closes the sheet once, and CANCEL and Escape in the wait close
//     nothing: closes after Escape, with DONE's compile still out: expected 0, got 1
await test("a DONE whose compile fails closes the sheet once, and CANCEL and Escape in the wait close nothing", async () => {
  // A failed DONE must be seen here, not end the run: under the first mutant
  // its rejection would otherwise be unhandled and exit the process.
  const proc = (globalThis as any).process;
  const rejections: unknown[] = [];
  const onRejection = (e: unknown) => { rejections.push(e); };
  proc.on("unhandledRejection", onRejection);
  setup({ ws: false });
  mount();
  typeInto(doc.querySelector("#tfs-name"), "M31 wide");
  let fail: (e: Error) => void = () => {};
  compileGate = new Promise<void>((_ok, bad) => { fail = bad; });
  try {
    click(doneBtn());
    await flush();
    eq(applyCalls.length, 1, "DONE's writes");
    eq(storeCompiles, 1, "compiles DONE started");
    click(q("framing-header").querySelector("button"));
    eq(closes, 0, "closes after CANCEL, with DONE's compile still out");
    act(() => { doc.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
    eq(closes, 0, "closes after Escape, with DONE's compile still out");
  } finally {
    fail(new Error("503 from the compile route"));
    compileGate = null;
    await flush();
    await flush(20);
    proc.off("unhandledRejection", onRejection);
  }
  eq(closes, 1, "closes once DONE's compile had failed");
  eq(rejections.length, 0, "DONE's commit rejected");
  const log = useStore.getState().flows.logs.map((l: any) => l.msg);
  assert(log.some((m: string) => m.startsWith("could not check this flow")),
    `the failed compile was not logged: ${JSON.stringify(log)}`);
  eq(useStore.getState().flows.graph.nodes.find((n: any) => n.id === "n2")!.params.name, "M31 wide",
    "the name DONE wrote before its compile failed");
});

// DONE's write takes the draft as it stands when DONE is pressed, and the
// sheet closes when that write's compile answers. An edit made in between
// was drawn and accepted, then dropped with the sheet. Found by the verifier
// with the compile held open: a name typed and a column added in the wait
// were on screen ("M31 wider", COLS 4), and the flow kept "M31 wide" on 3
// columns. So over the wait every control is disabled (the fieldset, which a
// browser enforces) and the sky's editing gestures are unwired, as in view
// mode.
// MUTANT "fields live while DONE writes" (the fieldset's `disabled={frozen}`
// back to `disabled={readOnly}`). Observed (verifier, scratch copy
// s4-umodal-verify-r2-mut):
//   x while DONE's write waits for its compile, the draft takes no edit: every control disabled, a
//     panel tap skips nothing: the controls while DONE writes: expected true, got false
// MUTANT "sky live while DONE writes" (FramingSky's `readOnly={frozen}` back
// to `readOnly={readOnly}`). Observed (verifier, scratch copy
// s4-umodal-verify-r2-mut):
//   x while DONE's write waits for its compile, the draft takes no edit: every control disabled, a
//     panel tap skips nothing: panel 1-1 after a tap while DONE writes: expected "pending", got
//     "skipped"
await test("while DONE's write waits for its compile, the draft takes no edit: every control disabled, a panel tap skips nothing", async () => {
  setup({ ws: false });
  mount();
  const fs = () => doc.querySelector(".tfs-fieldset") as any;
  const label = () => doc.querySelector('[data-role="panel-label"][data-row="0"][data-col="0"]') as any;
  // Control: before DONE the same sheet edits, by field and by tap.
  eq(fs()?.disabled, false, "the controls before DONE");
  typeInto(doc.querySelector("#tfs-name"), "M31 wide");
  click(label());
  eq(label().getAttribute("data-state"), "skipped", "panel 1-1 after a tap before DONE");
  click(label());
  eq(label().getAttribute("data-state"), "pending", "panel 1-1 after a second tap before DONE");
  let release: () => void = () => {};
  compileGate = new Promise<void>((r) => { release = r; });
  try {
    click(doneBtn());
    await flush();
    eq(applyCalls.length, 1, "DONE's writes");
    eq(closes, 0, "closes before DONE's compile answered");
    eq(fs()?.disabled, true, "the controls while DONE writes");
    click(label());
    eq(label().getAttribute("data-state"), "pending", "panel 1-1 after a tap while DONE writes");
  } finally {
    release();
    compileGate = null;
    await flush();
  }
  eq(closes, 1, "closes once DONE's compile answered");
  eq(useStore.getState().flows.graph.nodes.find((n: any) => n.id === "n2")!.params.name, "M31 wide",
    "the name DONE wrote");
});

// The `alive` flag that stops a closed sheet closing again must not stop a
// LIVE one. The app renders under StrictMode (main.tsx), which in development
// mounts every component, runs its effects, cleans them up and runs them
// again. A flag cleared in the cleanup and never set again reads "unmounted"
// on a sheet that is on screen, and DONE would write the framing and then
// never close the sheet.
// MUTANT "alive never re-armed" (the effect's `alive.current = true;`
// removed, leaving only the cleanup). Observed (verifier, scratch copy
// s4-umodal-verify-r2-mut):
//   x under StrictMode, whose remount runs the effects twice, DONE still closes the sheet once its
//     compile answers: closes under StrictMode once DONE's compile answered: expected 1, got 0
//   (Green before the verifier added this test: no other mount in this file is
//   under StrictMode.)
await test("under StrictMode, whose remount runs the effects twice, DONE still closes the sheet once its compile answers", async () => {
  // The harness must really be StrictMode's development double run, or this
  // test grades nothing: a production React runs each effect once.
  let probeRuns = 0;
  function Probe(): null { useEffect(() => { probeRuns++; }, []); return null; }
  setup({ ws: false });
  act(() => { root.render(null); });
  act(() => {
    root.render(createElement(StrictMode, null,
      createElement(Probe),
      createElement(Sheet, { nodeId: "n2", onClose: () => { closes++; } })));
  });
  eq(probeRuns, 2, "effect runs of a mount-once effect under StrictMode (the harness's double run)");
  typeInto(doc.querySelector("#tfs-name"), "M31 wide");
  click(doneBtn());
  await flush();
  eq(applyCalls.length, 1, "DONE's writes under StrictMode");
  eq(closes, 1, "closes under StrictMode once DONE's compile answered");
});

// ======================================================================
// 4. a local session

// MUTANT "global session" (the sheet's sky writes mirror into the Atlas's
// session: `setCentre` and `onZoom` also call
// `useStore.getState().setFraming(...)` with the centre and the zoom).
// Observed:
//   x opening block A, dragging its sky, zooming and pressing DONE never changes store.framing:
//     setFraming calls from block A's sheet: expected 0, got 2
await test("opening block A, dragging its sky, zooming and pressing DONE never changes store.framing", async () => {
  // Block B's framing, as the Atlas left it: another object entirely.
  const B = {
    center: { ra_hours: 22.617, dec_deg: 34.416 }, rotation_deg: 12, survey: "CDS/P/DSS2/color",
    stretch: "linear" as const, fovZoomDeg: 3, mosaic: { rows: 1, cols: 1, overlap: 0.25 }, panels: [],
  };
  setup({ ws: false, framing: B });
  const before = useStore.getState().framing;
  mount();
  const sky = doc.querySelector('[data-testid="framing-sky"] [role="application"]') as any;
  assert(sky, "the sheet mounted no SkyCanvas");
  act(() => { sky.dispatchEvent(new win.KeyboardEvent("keydown", { key: "+", bubbles: true })); });
  act(() => { sky.dispatchEvent(new win.KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true })); });
  click(buttonByText("MOVE GRID"));
  typeInto(doc.querySelector("#tfs-ra"), "00h 50m 00s");
  click(buttonByText("+15"));
  click(doneBtn());
  await flush();
  assert(applyCalls.length === 1, "the sheet's DONE did not reach flowsApplyFraming");
  eq(framingWrites, 0, "setFraming calls from block A's sheet");
  assert(useStore.getState().framing === before, "store.framing is no longer the object block B left");
  eq(useStore.getState().framing, B, "store.framing after block A's sheet");
  unmount();
});

// ======================================================================
// 5. site-derived: absent for a viewer

// MUTANT "rendered empty for a viewer" (the sheet passes `showAltitude` true
// for everyone and builds the night card without asking `canSite`, so a
// viewer gets the column with empty cells and the card). Observed:
//   x a viewer never sees the altitude column or the night card; an operator sees both: a viewer's
//     altitude header: expected 0, got 1
await test("a viewer never sees the altitude column or the night card; an operator sees both", async () => {
  setup({ caps: VIEWER });
  mount();
  await flush();
  eq(doc.querySelectorAll('[data-testid="framing-alt-head"]').length, 0, "a viewer's altitude header");
  eq(doc.querySelectorAll('[data-testid="framing-alt-cell"]').length, 0, "a viewer's altitude cells");
  eq(doc.querySelectorAll('[data-testid="sky-mosaic-night"]').length, 0, "a viewer's night card");
  eq(doc.querySelectorAll('[data-panel]').length, 6, "a viewer's panel rows (the rest of PANELS stays)");
  unmount();
  // Control: an operator gets the column, filled from the route's answer,
  // and the card.
  setup();
  mount();
  await flush();
  const peaks = PANELS_FX.response.panels.map((p: any, i: number) => ({ ...p, transit_alt: 60 + i }));
  await answer(mosaicCalls[0], { panels: peaks });
  eq(doc.querySelectorAll('[data-testid="framing-alt-head"]').length, 1, "an operator's altitude header");
  const cells = Array.from(doc.querySelectorAll('[data-testid="framing-alt-cell"]') as any[]).map((c: any) => c.textContent);
  eq(cells.length, 6, "an operator's altitude cells");
  assert(cells.every((c: string) => /^6\d\u00b0$/.test(c)), `an operator's cells hold peaks: ${JSON.stringify(cells)}`);
  eq(doc.querySelectorAll('[data-testid="sky-mosaic-night"]').length, 1, "an operator's night card");
  unmount();
});

// ======================================================================
// 6. read-only Examples

// MUTANT "examples edit" (the shell ignores `record.readonly`, so an Example
// opens editable). Observed:
//   x a read-only Example opens in view mode and says why: the reason: expected "Example flow: its
//     framing opens to view, not to edit. Duplicate the flow to frame your own.", got undefined
// MUTANT "view-mode sky taps" (FramingSky wires `onPanelTap` whatever
// `readOnly` says). Observed (verifier, scratch copy s4-umodal-verify-mut):
//   x a read-only Example opens in view mode and says why: a tap on an Example's sky skipped panel
//     1-1
await test("a read-only Example opens in view mode and says why", async () => {
  setup({ ws: false, readonly: true });
  mount();
  eq(q("framing-view-why")?.textContent, EXAMPLE_VIEW_ONLY, "the reason");
  assert(q("framing-done") === null, "an Example offers DONE");
  assert(buttonByText("CLOSE"), "an Example's sheet has no CLOSE");
  const fs = doc.querySelector(".tfs-fieldset") as any;
  eq(fs?.disabled, true, "an Example's controls are not read-only");
  // The sky is view-only too: the fieldset does not reach it, so a tap on a
  // panel must skip nothing on its own account.
  const label = () => doc.querySelector('[data-role="panel-label"][data-row="0"][data-col="0"]') as any;
  assert(label(), "an Example's sky draws no panel 1-1");
  click(label());
  assert(label().getAttribute("data-state") !== "skipped", "a tap on an Example's sky skipped panel 1-1");
  unmount();
  // Control: a flow of the operator's own opens editable.
  setup({ ws: false });
  mount();
  assert(q("framing-view-why") === null, "an editable flow says it is view-only");
  assert(q("framing-done"), "an editable flow has no DONE");
  unmount();
});

// ======================================================================
// 7. RUN's loop toggle opens on what the run does (#429)

/** A `loop_cases` entry of the panel-lane fixture, by id, failing (never
 *  skipping) when it is gone. */
function loopCase(id: string): any {
  const c = (LANE_FX.loop_cases ?? []).find((x: any) => x.id === id);
  if (!c) throw new Error(`panel_lane_cases.json holds no loop case ${id}`);
  return c;
}
/** The case's graph with its TARGET framed (this file's M31: a camera field,
 *  coordinates, an angle), so the sheet can lay the grid out and DONE is not
 *  held by a missing field or angle. The rows, the columns and every wire
 *  stay the fixture's. */
function framedCase(g: any, block: string): any {
  return {
    ...g,
    nodes: g.nodes.map((n: any) => (n.id === block
      ? { ...n, params: { ...target().params, ...n.params } } : n)),
  };
}
const loopToggle = () => q("framing-loop") as any;
const passWiresInto = (block: string) => useStore.getState().flows.graph.edges
  .filter((e: any) => e.fromPort === "pass" && e.to === block && e.toPort === "next")
  .map((e: any) => `${e.id}:${e.from}`);

// MUTANT "modal reads loopWires" (TargetFramingSheet.tsx: `opened.loop` back to
// `loopWires(graph, nodeId).length > 0`, the sheet as S5 left it). Run in
// scratchpad S7-USLICE-mut. Observed ("targetFramingSheet.test: 20/21
// passed"):
//   x RUN's loop opens OFF over a tail loop beside a stale mid-lane wire, and switching it on repairs the lane: RUN's loop over a lane the card says runs one panel at a time: expected "false", got "true"
await test("RUN's loop opens OFF over a tail loop beside a stale mid-lane wire, and switching it on repairs the lane", async () => {
  const c = loopCase("loop-on-stranded-beside-the-tail-loop");
  // Premises, from the fixture: the graph does not rotate (M12) and the
  // repaired one does; the case is a press of `true`.
  eq(c.rotates, { graph: false, result: true }, "premise: the fixture's rotation, before and after");
  eq(c.loop, true, "premise: the case is the loop turned on");
  setup({ ws: false, graph: framedCase(c.graph, c.block) });
  mount(c.block);
  assert(loopToggle(), "precondition: the sheet shows no RUN loop toggle for the fixture's lane");
  eq(loopToggle().getAttribute("data-on"), "false", "RUN's loop over a lane the card says runs one panel at a time");
  click(loopToggle().closest("button"));
  eq(loopToggle().getAttribute("data-on"), "true", "RUN's loop after one press");
  click(doneBtn());
  await flush();
  eq(applyCalls.length, 1, "precondition: DONE did not reach flowsApplyFraming");
  eq(applyCalls[0][2], true, "the loop DONE sent");
  const want = c.result.edges.filter((e: any) => e.fromPort === "pass" && e.to === c.block && e.toPort === "next")
    .map((e: any) => `${e.id}:${e.from}`);
  eq(passWiresInto(c.block), want, "the pass wires into next after DONE, as the fixture records them");
  eq(JSON.stringify(useStore.getState().flows.graph.edges), JSON.stringify(c.result.edges),
    "every wire after DONE, as the fixture records them");
  unmount();
  // CONTROL: the repaired lane, which rotates, opens ON, and a DONE that
  // leaves RUN alone sends no loop and moves no wire.
  setup({ ws: false, graph: framedCase(c.result, c.block) });
  mount(c.block);
  eq(loopToggle()?.getAttribute("data-on"), "true", "RUN's loop over the repaired lane");
  typeInto(doc.querySelector("#tfs-name"), "M31 wide");
  click(doneBtn());
  await flush();
  eq(applyCalls[0]?.[2], undefined, "the loop DONE sent with RUN untouched on a lane that rotates");
  eq(JSON.stringify(useStore.getState().flows.graph.edges), JSON.stringify(c.result.edges),
    "the repaired lane's wires after a DONE that left RUN alone");
});

// The other fact DONE needs is still the tail's wire, not the rotation: a
// mosaic made one panel lifts every pass wire into its `next` when its tail
// carries a loop wire, rotating or not, or the stale ones would stand as pass
// wires into a 1x1 block.
//
// MUTANT "the one-panel lift reads the rotation" (TargetFramingSheet.tsx:
// `loopArg`'s one-panel arm asks `opened.loop` in place of
// `opened.tailWire`). Run in scratchpad S7-USLICE-mut. Observed
// ("targetFramingSheet.test: 20/21 passed"):
//   x a stranded lane made one panel has its tail loop and the stale wire lifted: the loop DONE sent for a looped mosaic made one panel: expected false, got undefined
await test("a stranded lane made one panel has its tail loop and the stale wire lifted", async () => {
  const c = loopCase("loop-on-stranded-beside-the-tail-loop");
  setup({ ws: false, graph: framedCase(c.graph, c.block) });
  mount(c.block);
  eq(passWiresInto(c.block).length, 2, "precondition: the fixture's two pass wires into next");
  click(buttonByText("fewer rows"));
  click(buttonByText("fewer cols"));
  click(buttonByText("fewer cols"));
  click(doneBtn());
  await flush();
  eq(applyCalls.length, 1, "precondition: DONE did not reach flowsApplyFraming");
  const n = useStore.getState().flows.graph.nodes.find((x: any) => x.id === c.block)!;
  eq([n.params.rows, n.params.cols], [1, 1], "precondition: DONE made the block one panel");
  eq(applyCalls[0][2], false, "the loop DONE sent for a looped mosaic made one panel");
  eq(passWiresInto(c.block), [], "pass wires into a one-panel block after DONE");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`targetFramingSheet.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
