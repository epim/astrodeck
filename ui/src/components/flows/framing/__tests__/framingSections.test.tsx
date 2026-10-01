// framingSections.test.tsx - the Target modal's sections, MOUNTED in the sheet
// on the real store (#189 S4 item 1; spec 2026-09-23 flows mosaic, 2.4, 1.4,
// 1.6, 6.9; Revision 2 rulings 1 and 2; S4 orchestrator rulings 4 and 8).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/framingSections.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE. Each section is presentational; the rules are
// framingModel's and framingModel.test.ts grades them. What only a mount can
// show is that the sheet WIRES them: that a lock reaches its control with its
// reason, that MATCH CAMERA reads the live profile-aware optics and not the
// compile's stale field, that USE MEASURED reads `status.sky_angle`, that the
// PANELS rows come in run order with the progress route's bars, and that RUN
// writes the loop through `flowsApplyFraming`, the flow setting through
// `flowsSetSetting`, shows the counts line for THIS block, prints the
// compile's numbers for the layout as framed, and gives the campaign line to
// a holder of the site view only.
//
// Since S5 it also holds that a grid is never given an angle nobody chose,
// that the measured angle is offered and applied only when pressed (#411, S5
// orchestrator ruling 1), that a single target's RUN and CENTRING say what a
// single target does (#413, on the route's recorded single-target answer),
// and that Grid order says where the run starts (#412 item 4).
//
// Since S7 it also holds PANELS' run lines in the run's words on the recorded
// sequence states: the panel the run is on while it is paused, holding for
// cloud or stopping is never said to be shot (#451), and a set-aside reason
// that already names its panel is not prefixed with the label again (#509).
// Those mutants were run in scratchpad S7-URUNHOLD-mut (2026-09-28).
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// s4-umodal-mut), never in the shared tree (#254), and the failure it
// produced is quoted verbatim. After the limit reset every one was run
// again against the current tree in s4-umodal-r2-mut (2026-09-27), and
// each was red with the failure quoted. S5's mutants were run the same way
// in s5-modal-mut (2026-09-28).

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
const { COUNTS_NOTE, COUNTS_DORMANT_ADDENDUM } = await import("../../countsNotice");
const { framingApi, framingTiming, campaignLine } = await import("../framingApi");
const { ANY_ANGLE_ON_A_GRID, NO_ANGLE_ON_A_GRID, NO_OPTICS, NO_ROTATOR, runPanelsOf } = await import("../framingModel");
const { ROTATE_LABEL, WHEN_WAITING_NO_MOSAIC } = await import("../sections/RunSection");
const {
  GRID_ORDER_NOTE, SETTING_FIRST_NOTE, PanelsSection, panelRows, runLine, runRowText, namesPanel,
  SHOOTING_NOW, SET_ASIDE_TONIGHT, CURRENT_PAUSED, CURRENT_HOLDING_FOR_CLOUD, CURRENT_HOLDING,
  CURRENT_STOPPING,
} = await import("../sections/PanelsSection");
// The classic overview's copy of the no-mosaic line, which RUN's is held
// equal to (the overview cannot import the sheet's: the classic flows chunk
// may not reach a framing module, classicFrameHost.test.tsx).
const { WHEN_WAITING_NO_MOSAIC: OVERVIEW_NO_MOSAIC } = await import("../../FlowInspector");
const { api } = await import("../../../../api");
const sheetModule = await import("../TargetFramingSheet");
const Sheet = sheetModule.default;
const { COMPILE_NEEDS_ACCESS } = sheetModule;
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
function near(got: number, want: number, tol: number, msg: string): void {
  assert(Math.abs(got - want) <= tol, `${msg}: expected ${want} (+-${tol}), got ${got}`);
}

// ------------------------------------------------------------------ fixtures
function readJson(rel: string): any {
  try {
    return JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8") as string);
  } catch (e) {
    throw new Error(`cannot read ${rel}: ${(e as Error).message}`);
  }
}
const READOUTS_FX = readJson("../../../../../../server/tests/fixtures/flow_readouts_m31.json");
/** The route's recorded answer for a single target (the `example-cycle`
 *  Example: one panel owning the default FILTER CYCLE), pinned against the
 *  route by test_flows_readouts.py `TestASingleTargetExamplesFixture`. Its
 *  block is n2, `mode` "single", every group number null, focus "once". */
const SINGLE_FX = readJson("../../../../../../server/tests/fixtures/flow_readouts_single.json");
/** The sequence states the engine published on the clocked simulator for a
 *  rotating 2x2 whose 2-2 is set aside, rebuilt byte for byte by
 *  test_s5_recorded_state.py: 1-1 being shot, and (#451) the run paused,
 *  holding for cloud and stopping from an Abort pressed in that hold, each
 *  with its group on 1-1. */
const RUN_FX = readJson("../../../../../../server/tests/fixtures/sequence_state_mosaic.json").states;

const OPERATOR = ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"];
const VIEWER = ["view.status", "view.preview"];

function target(over: Record<string, string | number> = {}, id = "n2"): FlowNodeRec {
  return {
    id, type: "target", x: 0, y: 0,
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
const LANE_ONLY = [E("a", "n2", "target", "cy", "run")];
const LOOP = E("loop", "cy", "pass", "n2", "next");

function compiledWith(rig: Record<string, unknown> = {}, readouts = READOUTS_FX.readouts): any {
  return { plan: {}, structural: [], issues: [], unmapped: [], readouts, rig: { ...READOUTS_FX.rig, ...rig } };
}

// ------------------------------------------------------------------ the network
let localCompile: any = compiledWith();
let localAsks = 0;
(framingApi as any).mosaic = () => new Promise(() => {});
(framingApi as any).compileDraft = async () => { localAsks++; return localCompile; };
let storeCompiles = 0;
(flowsApi as any).compileDraft = async () => { storeCompiles++; return compiledWith(); };
framingTiming.settleMs = 0;
/** The catalogue, for WHERE's search: M31 at its catalogued size, so SUGGEST
 *  GRID has something to size a grid by. Every other route still fails. */
const M31_ENTRY = {
  id: "M31", name: "Andromeda Galaxy", type: "galaxy", ra_hours: 0.7123, dec_deg: 41.269,
  mag: 3.4, size_arcmin: 178,
};
(api as any).get = async (path: string) => {
  if (path.startsWith("/api/catalog?")) return { results: [M31_ENTRY], notes: [] };
  throw new Error(`no network in this fixture: ${path}`);
};

const real = useStore.getState();
let applyCalls: any[][] = [];
let settingCalls: any[][] = [];
let tonightFetches = 0;
useStore.setState({
  flowsApplyFraming: (...a: any[]) => { applyCalls.push(a); return (real.flowsApplyFraming as any)(...a); },
  flowsSetSetting: (...a: any[]) => { settingCalls.push(a); return (real.flowsSetSetting as any)(...a); },
  flowsFetchTonight: async () => { tonightFetches++; },
} as any);

function setup(o: {
  caps?: string[]; nodes?: FlowNodeRec[]; edges?: FlowEdgeRec[]; compiled?: any; progress?: any;
  config?: any; skyAngle?: any; tonight?: any; countsNote?: string | null; settings?: any;
} = {}): void {
  const graph: any = { nodes: o.nodes ?? [target(), CYCLE], edges: o.edges ?? [...LANE_ONLY, LOOP] };
  if (o.settings) graph.settings = o.settings;
  useStore.setState({
    principal: { role: o.caps === VIEWER ? "viewer" : "operator", email: null, caps: o.caps ?? OPERATOR },
    // Offline throughout, so DONE opens on the mirror at once: this file
    // grades the sections, and targetFramingSheet.test.tsx grades the wait.
    wsConnected: false,
    status: { sky_angle: o.skyAngle ?? null } as any,
    config: o.config ?? null,
    site: null,
    flows: {
      ...FLOWS_INIT,
      record: { id: "f1", name: "M31 mosaic", folder: "", tagline: "", graph,
        created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: false },
      graph,
      compiled: o.compiled ?? compiledWith(),
      progress: o.progress ?? null,
      tonight: o.tonight ?? null,
      countsNote: o.countsNote ?? null,
    },
  } as any);
  applyCalls = []; settingCalls = []; storeCompiles = 0; localAsks = 0; localCompile = compiledWith();
  tonightFetches = 0;
}

const container = win.document.getElementById("root");
const root = createRoot(container);
/** A fresh sheet: whatever was mounted is unmounted first, so no draft
 *  survives from one mount to the next. */
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
const locked = (b: any) => b?.getAttribute("aria-disabled") === "true";
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
function buttonByText(text: string): any {
  return Array.from(doc.querySelectorAll("button") as any[]).find((b: any) => b.textContent.includes(text));
}
function typeInto(el: any, value: string): void {
  assert(el, "no field to type into");
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => { setter.call(el, value); el.dispatchEvent(new win.Event("input", { bubbles: true })); });
}
function choose(sel: any, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(win.HTMLSelectElement.prototype, "value")!.set!;
  act(() => { setter.call(sel, value); sel.dispatchEvent(new win.Event("change", { bubbles: true })); });
}
const chosenAngle = () => (doc.querySelector('[data-chosen="true"]') as any)?.getAttribute("data-angle");
async function done(): Promise<void> { click(doneBtn()); await flush(); }

// ======================================================================
// ANGLE: each lock with its reason

// MUTANT "any angle unlocked" (the sheet passes the ANGLE section
// `{ ...angleLocks(draft, rig), "Any angle": null }`). Observed:
//   x ANY ANGLE is locked on a grid and ROTATE TO without a rotator, each with its reason: ANY ANGLE
//     is live on a 3x2
// MUTANT "rotate unlocked" (the same with `"Rotate to PA": null`). Observed:
//   x ANY ANGLE is locked on a grid and ROTATE TO without a rotator, each with its reason: ROTATE TO
//     is live on a profile with no rotator
await test("ANY ANGLE is locked on a grid and ROTATE TO without a rotator, each with its reason", async () => {
  setup({ compiled: compiledWith({ has_rotator: false }) });
  mount();
  const any = buttonByText("ANY ANGLE");
  assert(locked(any), "ANY ANGLE is live on a 3x2");
  eq(any.getAttribute("title"), ANY_ANGLE_ON_A_GRID, "ANY ANGLE's reason");
  click(any);
  eq(chosenAngle(), "Rotate to PA", "the angle after pressing a locked ANY ANGLE");
  eq(q("framing-explain")?.textContent, ANY_ANGLE_ON_A_GRID, "pressing it says why");
  const rot = buttonByText("ROTATE TO");
  assert(locked(rot), "ROTATE TO is live on a profile with no rotator");
  eq(rot.getAttribute("title"), NO_ROTATOR, "ROTATE TO's reason");
  assert(!locked(buttonByText("CAMERA FIXED AT")), "CAMERA FIXED AT, which commands nothing, is locked");
  unmount();
  // Control: one panel on a profile with a rotator locks neither.
  setup({ nodes: [target({ rows: 1, cols: 1 }), CYCLE], compiled: compiledWith({ has_rotator: true }) });
  mount();
  assert(!locked(buttonByText("ANY ANGLE")), "ANY ANGLE is locked on one panel");
  assert(!locked(buttonByText("ROTATE TO")), "ROTATE TO is locked with a rotator");
  click(buttonByText("ANY ANGLE"));
  eq(chosenAngle(), "Any angle", "ANY ANGLE on one panel");
  unmount();
});

// ======================================================================
// GRID: its lock

// MUTANT "grid unlocked" (the sheet passes the GRID section `lock={null}`).
// Observed:
//   x GRID locks with the Settings sentence when there is no field to tile with, and so does MATCH
//     CAMERA: COLS is live with no field to tile with
await test("GRID locks with the Settings sentence when there is no field to tile with, and so does MATCH CAMERA", async () => {
  setup({ nodes: [target({ fovX: 0, fovY: 0 }), CYCLE], compiled: compiledWith({ fov_deg: null }) });
  mount();
  const more = buttonByText("more cols");
  assert(locked(more), "COLS is live with no field to tile with");
  eq(more.getAttribute("title"), NO_OPTICS, "COLS' reason");
  click(more);
  eq(q("framing-cols")?.querySelector(".tfs-value")?.textContent, "3", "COLS after pressing a locked +");
  eq(q("framing-explain")?.textContent, NO_OPTICS, "pressing it says why");
  eq(buttonByText("MATCH CAMERA").getAttribute("title"), NO_OPTICS, "MATCH CAMERA's reason");
  unmount();
  // Control: a block that snapshotted its field keeps its grid editable
  // while the rig is away.
  setup({ compiled: compiledWith({ fov_deg: null }) });
  mount();
  assert(!locked(buttonByText("more cols")), "a framed block's grid is locked with the rig away");
});

// ======================================================================
// GRID: MATCH CAMERA

/** A profile "Refractor" supplying the optics: 530 mm, 3.76 um pixels,
 *  4144 x 2822. The global block holds a DIFFERENT focal length, so a reader
 *  of the global block would tile for another camera. */
const OPTICS = { focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 4144, sensor_height_px: 2822 };
function profileConfig(): any {
  const entry = (k: string, v: number, global: number) => [`optics.${k}`, {
    value: v, layer: "profile", profile: v, config: global, default: 0,
    profile_id: "p1", profile_name: "Refractor",
  }];
  return {
    optics: { ...OPTICS, focal_length_mm: 400, auto_from_camera: false, telescope_name: "" },
    effective: Object.fromEntries([
      entry("focal_length_mm", 530, 400), entry("pixel_size_um", 3.76, 3.76),
      entry("sensor_width_px", 4144, 4144), entry("sensor_height_px", 2822, 2822),
    ]),
  };
}
// From first principles, not through the code under test: the linear
// small-angle formula, sensor size (mm) over focal length (mm) times
// 180/pi (#168, "ONE FOV FORMULA" -- `lib/framing.ts`'s `fovDegFromSensorMm`,
// which `fovFromOptics` now calls for `fov_x_deg`/`fov_y_deg`).
//
// RE-PINNED (W3 integration, WP-29 collateral, #168). This used to compute
// the field as the ROUNDED bin-1 pixel scale (206.265 x pixel-um /
// focal-mm, `ARCSEC_PER_RAD`) times the sensor, divided by 3600 -- which is
// what `fovFromOptics` computed before WP-29 and is still what its own
// `pixel_scale_arcsec` field is for. WP-29 switched `fov_x_deg`/
// `fov_y_deg` to the exact-trig `fovDegFromSensorMm` formula instead, so
// the Atlas overlay and the Settings preview compute FOV the same way
// (#168) -- but that formula does not derive from the rounded
// `ARCSEC_PER_RAD` pixel scale at all, so the two no longer agree to this
// test's original 1e-9 tolerance (measured gap: ~1.6e-6 deg, ~5.7 mas, at
// this fixture's 530mm/3.76um/4144px rig -- utterly negligible for framing
// a telescope, but enough to fail a bit-exact comparison).
//
// NOTED, NOT FIXED HERE: `config.py`'s `fov_deg()` (the server's own FOV
// anchor, which WP-29's docstring there calls "the ONE FOV formula's server
// anchor") still computes through the rounded `ARCSEC_PER_RAD` pixel scale,
// the OLD formula -- not through an exact-trig equivalent of
// `fovDegFromSensorMm`. So the claim that the UI and the server now
// compute FOV identically is not quite true: the two UI mirrors agree with
// EACH OTHER (that is what #168 asked for) but not bit-exactly with the
// server. Reconciling the server's formula too is a wider change than this
// integration pass's scope; flagged as a new defect for the backlog.
const FOCAL_MM = 530;
const PIXEL_UM = 3.76;
const SENSOR_W_PX = 4144;
const SENSOR_H_PX = 2822;
const FOV_X = ((PIXEL_UM * SENSOR_W_PX) / 1000 / FOCAL_MM) * (180 / Math.PI);
const FOV_Y = ((PIXEL_UM * SENSOR_H_PX) / 1000 / FOCAL_MM) * (180 / Math.PI);

// MUTANT "field from the stale compile" (the sheet builds MATCH CAMERA's rig
// with `liveRig(compiledRig(compiled), null, ...)`, so the compile's field,
// read when the flow was opened, stands). Observed:
//   x MATCH CAMERA snapshots the profile-aware effective optics at bin 1, not the compile's old
//     field: the camera line after MATCH CAMERA: "Tiled for 2.00 x 1.33 deg at bin 1 (profile Old,
//     matched 2026-09-01)"
await test("MATCH CAMERA snapshots the profile-aware effective optics at bin 1, not the compile's old field", async () => {
  setup({
    nodes: [target({ fovX: 0, fovY: 0, fovFrom: "" }), CYCLE],
    compiled: compiledWith({ fov_deg: [2.0, 1.33], fov_from: "profile Old, matched 2026-09-01" }),
    config: profileConfig(),
  });
  mount();
  click(buttonByText("MATCH CAMERA"));
  const line = q("framing-camera-line")?.textContent ?? "";
  assert(line.startsWith(`Tiled for ${FOV_X.toFixed(2)} x ${FOV_Y.toFixed(2)} deg at bin 1 (profile Refractor, matched `),
    `the camera line after MATCH CAMERA: ${JSON.stringify(line)}`);
  await done();
  eq(applyCalls.length, 1, "DONE applied");
  const patch = applyCalls[0][1];
  near(patch.fovX, FOV_X, 1e-9, "fovX");
  near(patch.fovY, FOV_Y, 1e-9, "fovY");
  assert(/^profile Refractor, matched \d{4}-\d{2}-\d{2}$/.test(patch.fovFrom), `fovFrom: ${JSON.stringify(patch.fovFrom)}`);
  unmount();
});

// ======================================================================
// ANGLE: USE MEASURED

// MUTANT "measured unwired" (the sheet reads `null` where it read
// `s.status?.sky_angle ?? null`). Observed:
//   x USE MEASURED reads status.sky_angle and lays the grid out at the camera's measured angle: no
//     USE MEASURED chip with a measurement in status
await test("USE MEASURED reads status.sky_angle and lays the grid out at the camera's measured angle", async () => {
  const now = Date.now() / 1000;
  setup({
    skyAngle: {
      pa_deg: 37.24, exposed_at: now - 14 * 60 - 20, solved_at: now - 14 * 60 - 5, source: "plate solve + sync",
      pier_side: "west", camera: "ZWO ASI2600MM", calibrated: false, reason: "no rotator is connected",
      mechanical_deg: null, rotator_before_deg: null, offset_deg: null,
    },
  });
  mount();
  const chip = q("framing-use-measured");
  assert(chip, "no USE MEASURED chip with a measurement in status");
  eq(chip.parentElement.textContent.includes("camera measured 37.2 deg, 14 min ago, by the centring solve, pier west"), true,
    `the chip's line: ${JSON.stringify(chip.parentElement.textContent)}`);
  click(chip);
  await done();
  eq(applyCalls[0][1].rotation, 37.2, "the angle DONE wrote");
  unmount();
  // Control: no measurement, no chip.
  setup();
  mount();
  assert(q("framing-use-measured") === null, "a USE MEASURED chip with nothing measured");
  unmount();
});

// ======================================================================
// PANELS: run order and the progress route's bars

/** The 3x2's progress: panel (0,0) complete, the rest part-way. */
function progress(): any {
  const counts: Record<string, number> = { "0,0": 80, "0,1": 40, "0,2": 0, "1,2": 10, "1,1": 0, "1,0": 20 };
  const panels = Object.entries(counts).map(([k, banked]) => {
    const [row, col] = k.split(",").map(Number);
    return { target_id: `t${k}`, name: `M31 ${row + 1}-${col + 1}`, row, col, banked, owed: 80 - banked, total: 80, steps: [] };
  });
  return {
    flow_id: "f1", session: { id: "s1", status: "dormant", nights: 2, count_mode: "accepted" },
    blocks: [{ node_id: "n2", name: "M31", kind: "target", banked: 150, owed: 330, total: 480,
      panels, grid: { rows: 2, cols: 3 }, skipped: [] }],
    orphaned: { frames: 0, steps: 0 },
  };
}
const rowsNow = () => Array.from(doc.querySelectorAll("[data-panel]") as any[]).map((r: any) => ({
  label: r.getAttribute("data-panel"),
  order: r.querySelector(".tfs-c-order").textContent,
  bar: r.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow") ?? null,
}));

// MUTANT "rows in grid order" (panelRows sorts the live panels by snake index
// alone, dropping the fraction banked). Observed:
//   x PANELS lists the panels in run order, least complete first, with the progress route's bars:
//     least complete first (ties in snake order): expected [{"label":"1-3","order":"1","bar":"0"},{"la
//     bel":"2-2","order":"2","bar":"0"},{"label":"2-3","order":"3","bar":"10"},{"label":"2-1","order":
//     "4","bar":"20"},{"label":"1-2","order":"5","bar":"40"},{"label":"1-1","order":"6","bar":"80"}],
//     got [{"label":"1-1","order":"1","bar":"80"},{"label":"1-2","order":"2","bar":"40"},{"label":"1-3
//     ","order":"3","bar":"0"},{"label":"2-3","order":"4","bar":"10"},{"label":"2-2","order":"5","bar"
//     :"0"},{"label":"2-1","order":"6","bar":"20"}]
await test("PANELS lists the panels in run order, least complete first, with the progress route's bars", async () => {
  setup({ progress: progress() });
  mount();
  eq(rowsNow(), [
    { label: "1-3", order: "1", bar: "0" }, { label: "2-2", order: "2", bar: "0" },
    { label: "2-3", order: "3", bar: "10" }, { label: "2-1", order: "4", bar: "20" },
    { label: "1-2", order: "5", bar: "40" }, { label: "1-1", order: "6", bar: "80" },
  ], "least complete first (ties in snake order)");
  choose(doc.querySelector("#tfs-order"), "Grid order");
  eq(rowsNow().map((r) => r.label), ["1-1", "1-2", "1-3", "2-3", "2-2", "2-1"], "grid order is snake order");
  // A skipped panel runs in no order and is listed after the rest.
  click(doc.querySelector('[aria-label="Skip panel 1-2"]'));
  eq(rowsNow().map((r) => `${r.label}:${r.order}`), ["1-1:1", "1-3:2", "2-3:3", "2-2:4", "2-1:5", "1-2:"],
    "a skipped panel's row");
  unmount();
});

// ======================================================================
// RUN: the loop, the flow setting, the counts line, the numbers

// MUTANT "loop not sent" (the sheet's `loopArg` is always `undefined`).
// Observed:
//   x 'Rotate panels every pass' reaches the loop wire through flowsApplyFraming: the loop DONE sent
//     for a toggle turned off: expected false, got undefined
await test("'Rotate panels every pass' reaches the loop wire through flowsApplyFraming", async () => {
  // A looped 3x2, toggled off: DONE sends false and the wire goes.
  setup();
  mount();
  eq(q("framing-loop")?.getAttribute("data-on"), "true", "a looped mosaic's toggle");
  click(q("framing-loop").closest("button"));
  await done();
  eq(applyCalls[0][2], false, "the loop DONE sent for a toggle turned off");
  eq(useStore.getState().flows.graph.edges.some((e: any) => e.fromPort === "pass"), false, "the loop wire after DONE");
  unmount();
  // Control: untouched, the loop is left alone.
  setup();
  mount();
  click(buttonByText("more cols"));
  await done();
  eq(applyCalls[0][2], undefined, "the loop DONE sent when RUN was not touched");
  eq(useStore.getState().flows.graph.edges.some((e: any) => e.id === "loop"), true, "the loop wire was moved");
  unmount();
  // A single target that DONE makes a mosaic gets the loop (spec 1.4).
  setup({ nodes: [target({ rows: 1, cols: 1 }), CYCLE], edges: LANE_ONLY });
  mount();
  click(buttonByText("more cols"));
  await done();
  eq(applyCalls[0][2], true, "the loop DONE sent for a block that became a mosaic");
  eq(useStore.getState().flows.graph.edges.filter((e: any) => e.fromPort === "pass" && e.to === "n2").length, 1,
    "the new loop wire");
  unmount();
  // A block that owns no stage has no RUN section at all.
  setup({ nodes: [target(), CYCLE], edges: [] });
  mount();
  assert(q("framing-run") === null, "RUN shown for a block with no stage");
  unmount();
});

// MUTANT "setting without a compile" (commit drops `if (settingChanged &&
// !wrote) await compileFlow();`). Observed:
//   x 'While a mosaic waits' goes through flowsSetSetting at DONE, and is compiled once: compiles
//     for a DONE that changed only the flow setting: expected 1, got 0
// MUTANT "setting compiles twice" (the extra compile runs whenever the
// setting changed, `if (settingChanged) await compileFlow();`, dropping
// `!wrote`). Observed (verifier, scratch copy s4-umodal-verify-mut):
//   x 'While a mosaic waits' goes through flowsSetSetting at DONE, and is compiled once: compiles
//     for a DONE that changed the setting and the framing: expected 1, got 2
await test("'While a mosaic waits' goes through flowsSetSetting at DONE, and is compiled once", async () => {
  setup();
  mount();
  choose(doc.querySelector("#tfs-when-waiting"), "Wait for the mosaic");
  eq(useStore.getState().flows.graph.settings, undefined, "the flow setting before DONE (CANCEL must discard it)");
  await done();
  eq(settingCalls, [["whenWaiting", "Wait for the mosaic"]], "flowsSetSetting calls");
  eq(applyCalls.length, 1, "flowsApplyFraming calls");
  eq(useStore.getState().flows.graph.settings?.whenWaiting, "Wait for the mosaic", "the flow setting after DONE");
  eq(storeCompiles, 1, "compiles for a DONE that changed only the flow setting");
  unmount();
  // With a framing change as well, the framing write's one compile covers
  // the setting: still one compile, not one each.
  setup();
  mount();
  choose(doc.querySelector("#tfs-when-waiting"), "Wait for the mosaic");
  click(buttonByText("more cols"));
  await done();
  eq(settingCalls.length, 1, "flowsSetSetting calls with a framing change");
  eq(applyCalls.length, 1, "flowsApplyFraming calls with a framing change");
  eq(useStore.getState().flows.graph.nodes.find((n: any) => n.id === "n2")?.params.cols, 4, "the framing after DONE");
  eq(storeCompiles, 1, "compiles for a DONE that changed the setting and the framing");
  unmount();
});

// MUTANT "counts notice for the whole flow" (the sheet builds the notice from
// `countsNotice(graph, countsNote)`, the whole flow, not this block).
// Observed:
//   x the counts notice shows for a block that counts attempts, with the dormant addendum, and only
//     for it: an accepted-subs block shows the counts notice
await test("the counts notice shows for a block that counts attempts, with the dormant addendum, and only for it", async () => {
  const other = target({ counts: "Every sub taken", name: "M33" }, "n9");
  setup({ nodes: [target({ counts: "Every sub taken" }), CYCLE, other] });
  mount();
  eq(q("framing-counts")?.textContent, COUNTS_NOTE, "an attempts-counting block's notice");
  unmount();
  setup({ nodes: [target({ counts: "Every sub taken" }), CYCLE, other],
    countsNote: `${COUNTS_NOTE} ${COUNTS_DORMANT_ADDENDUM}` });
  mount();
  eq(q("framing-counts")?.textContent, `${COUNTS_NOTE} ${COUNTS_DORMANT_ADDENDUM}`, "with a dormant session");
  unmount();
  // Control: this block counts accepted subs; another block in the flow does
  // not, and that is the flow's line, not this block's.
  setup({ nodes: [target(), CYCLE, other] });
  mount();
  assert(q("framing-counts") === null, "an accepted-subs block shows the counts notice");
  unmount();
});

// MUTANT "readouts of the old layout" (the RUN section always reads the
// store's compile, `runCompiled = compiled`). Observed:
//   x the RUN numbers are the compile's for the layout as framed, not the one the block had: the
//     framed layout's first line: expected "8 panels x 4 filters x 20 = 640 subs", got "6 panels x 4
//     filters x 20 = 480 subs"
//   (The viewer-compile test went red with it too.)
await test("the RUN numbers are the compile's for the layout as framed, not the one the block had", async () => {
  setup();
  mount();
  const lines = () => Array.from(doc.querySelectorAll('[data-testid="framing-readouts"] .tfs-readout') as any[])
    .map((l: any) => l.textContent);
  eq(lines()[0], "6 panels x 4 filters x 20 = 480 subs", "the stored layout's first line");
  const eight = { ...READOUTS_FX.readouts.n2, panels: 8, subs_total: 640, total_s: 76800 };
  localCompile = compiledWith({}, { n2: eight });
  click(buttonByText("more cols"));
  await flush();
  eq(lines()[0], "8 panels x 4 filters x 20 = 640 subs", "the framed layout's first line");
  unmount();
});

// MUTANT "viewer compiles" (the draft compile's effect runs on `needsLocal`
// instead of `mayCompile`, so a viewer's edit asks a route that refuses
// them). Observed:
//   x an edited layout is not compiled for a principal the compile route refuses, and RUN says why:
//     draft compiles asked for a viewer: expected 0, got 1
await test("an edited layout is not compiled for a principal the compile route refuses, and RUN says why", async () => {
  setup({ caps: VIEWER });
  mount();
  click(buttonByText("more cols"));
  await flush();
  eq(localAsks, 0, "draft compiles asked for a viewer");
  eq(q("framing-readouts")?.textContent, COMPILE_NEEDS_ACCESS, "a viewer's RUN note");
  unmount();
  // Control: an operator's edit is compiled once it settles.
  setup();
  mount();
  click(buttonByText("more cols"));
  await flush();
  eq(localAsks, 1, "draft compiles asked for an operator");
});

// ======================================================================
// RUN: the campaign line, for a holder of the site view only

const NIGHT = { ok: true, night: { dusk_unix: 1000, dawn_unix: 1000 + 7.5 * 3600 } };

// MUTANT "campaign for everyone" (campaignLine drops its
// `!canViewSiteDerived` test). Observed:
//   x the campaign line comes from the Tonight answer, for a holder of the site view only: a
//     viewer's campaign line: expected null, got "this is a campaign: about 2.1 nights of 7.5 h before
//     hops. The session stays armed and resumes at the next dusk."
await test("the campaign line comes from the Tonight answer, for a holder of the site view only", async () => {
  const r = READOUTS_FX.readouts.n2;
  eq(campaignLine(r, NIGHT, true),
    "this is a campaign: about 2.1 nights of 7.5 h before hops. The session stays armed and resumes at the next dusk.",
    "16 h of shutter over 7.5 h nights");
  eq(campaignLine(r, NIGHT, false), null, "a viewer's campaign line");
  eq(campaignLine({ ...r, total_s: 7 * 3600 }, NIGHT, true), null, "a block that fits one night");
  eq(campaignLine(r, { ok: false, night: null }, true), null, "an answer that laid out no night");
  setup({ tonight: NIGHT });
  mount();
  assert((q("framing-campaign")?.textContent ?? "").startsWith("this is a campaign: about 2.1 nights"), "an operator's campaign line");
  unmount();
  setup({ tonight: NIGHT, caps: VIEWER });
  mount();
  assert(q("framing-campaign") === null, "a viewer sees the campaign line");
  unmount();
  // The Tonight answer is site-derived itself (a night's length at a known
  // date gives the latitude): a viewer's sheet never asks for it, and an
  // operator's asks once when none is in hand.
  // MUTANT "viewer fetches Tonight" (the sheet's Tonight effect loses its
  // `!canSite` test). Observed (verifier, scratch copy
  // s4-umodal-verify-mut):
  //   x the campaign line comes from the Tonight answer, for a holder of the site view only: Tonight
  //     fetches for a viewer: expected 0, got 1
  setup({ caps: VIEWER });
  mount();
  await flush();
  eq(tonightFetches, 0, "Tonight fetches for a viewer");
  unmount();
  setup();
  mount();
  await flush();
  eq(tonightFetches, 1, "Tonight fetches for an operator with no answer in hand");
  unmount();
});

// ======================================================================
// ANGLE: a grid is laid out at one angle, and nobody's default is it (#411)
//
// S5 orchestrator ruling 1 (#411; spec 1.8). S4's `gridSafe` gave a draft at
// ANY ANGLE that became a grid ROTATE TO, whose "none" turns into 0, so a DONE
// after a column change commanded the rotator to PA 0: an angle nobody chose,
// set in the ANGLE section below GRID, off screen on a phone. Now the draft
// stays at ANY ANGLE, DONE is locked until an angle is chosen, the readout
// strip says the angle the grid is laid out at, and a measurement in
// `status.sky_angle` is OFFERED in the strip and in ANGLE, applied only when
// the operator presses it.

const ANY_1x1 = () => target({ rows: 1, cols: 1, angle: "Any angle", rotation: -1 });
const stripAngle = () => q("framing-strip-angle")?.textContent ?? null;
const degrees = () => (doc.querySelector("#tfs-angle-deg") as any)?.value ?? null;
/** WHERE's catalogue search, picked: M31 at its catalogued 178'. */
async function pickM31(): Promise<void> {
  typeInto(doc.querySelector('[aria-label="Search the target catalog"]'), "M31");
  await flush(300);   // CatalogSearch's 250 ms debounce, then its answer
  click(buttonByText("Andromeda Galaxy"));
}

// MUTANT "gridSafe falls back to 0" (the sheet's grid handler given S4's
// `gridSafe` back: a draft at ANY ANGLE that becomes a grid is set by
// `setAngleMode` to ROTATE TO, or CAMERA FIXED AT with no rotator, which turns
// "none" into 0). Observed (scratch copy s5-modal-mut):
//   x a grid made at ANY ANGLE, by the steppers or SUGGEST GRID, stays there, and DONE asks for an
//     angle: the angle after more cols made a 2x1: expected "Any angle", got "Rotate to PA"
//   (The offer's test below went red with it too.) The same fallback on
//   SUGGEST GRID's path alone, `onSuggest` writing through S4's rule while the
//   steppers do not. Observed (scratch copy s5-modal-mut):
//   x a grid made at ANY ANGLE, by the steppers or SUGGEST GRID, stays there, and DONE asks for an
//     angle: the angle after SUGGEST GRID made a 2x3: expected "Any angle", got "Rotate to PA"
// MUTANT "DONE open with no angle" (the sheet's DONE reason leaves out
// `gridAngleLock`). Observed (scratch copy s5-modal-mut):
//   x a grid made at ANY ANGLE, by the steppers or SUGGEST GRID, stays there, and DONE asks for an
//     angle: DONE is open on a grid with no angle
await test("a grid made at ANY ANGLE, by the steppers or SUGGEST GRID, stays there, and DONE asks for an angle", async () => {
  setup({ nodes: [ANY_1x1(), CYCLE], edges: LANE_ONLY });
  mount();
  eq(chosenAngle(), "Any angle", "premise: a 1x1 at any angle");
  click(buttonByText("more cols"));
  eq(chosenAngle(), "Any angle", "the angle after more cols made a 2x1");
  eq(degrees(), "", "the degree field after more cols made a 2x1");
  eq(stripAngle(), "no angle", "the strip's angle on the 2x1");
  assert(locked(doneBtn()), "DONE is open on a grid with no angle");
  eq(doneBtn().getAttribute("title"), NO_ANGLE_ON_A_GRID, "DONE's reason on a grid with no angle");
  click(doneBtn());
  eq(applyCalls.length, 0, "a DONE locked for its angle applied the framing");
  eq(q("framing-explain")?.textContent, NO_ANGLE_ON_A_GRID, "pressing DONE says why");
  assert(q("framing-strip-offer") === null && q("framing-angle-offer") === null, "an offer with nothing measured");
  // Control: the operator chooses the angle, and DONE writes what they chose.
  click(buttonByText("ROTATE TO"));
  typeInto(doc.querySelector("#tfs-angle-deg"), "12.5");
  eq(stripAngle(), "rotate to 12.5 deg", "the strip once the operator chose an angle");
  assert(!locked(doneBtn()), "DONE stays locked with an angle chosen");
  await done();
  eq(applyCalls.map((a) => a[1]), [{ cols: 2, angle: "Rotate to PA", rotation: 12.5 }], "what DONE wrote");
  unmount();
  // SUGGEST GRID is the other way a draft becomes a grid.
  setup({ nodes: [ANY_1x1(), CYCLE], edges: LANE_ONLY });
  mount();
  await pickM31();
  click(buttonByText("SUGGEST GRID 2x3"));
  eq([q("framing-cols")?.querySelector(".tfs-value")?.textContent, q("framing-rows")?.querySelector(".tfs-value")?.textContent],
    ["2", "3"], "premise: SUGGEST GRID laid out M31's 2x3");
  eq(chosenAngle(), "Any angle", "the angle after SUGGEST GRID made a 2x3");
  eq(stripAngle(), "no angle", "the strip's angle on the 2x3");
  assert(locked(doneBtn()), "DONE is open on the suggested grid with no angle");
});

const SKY = (() => {
  const now = Date.now() / 1000;
  return {
    pa_deg: 37.24, exposed_at: now - 14 * 60 - 20, solved_at: now - 14 * 60 - 5, source: "plate solve + sync",
    pier_side: "west", camera: "ZWO ASI2600MM", calibrated: false, reason: "no rotator is connected",
    mechanical_deg: null, rotator_before_deg: null, offset_deg: null,
  };
})();

// MUTANT "the offer applies itself" (the sheet's grid handler takes the
// measured offer as a draft becomes a grid, `takeOffer` on the new draft
// whenever `angleOffer` answers one: the prefill #411 suggested, with no
// press). Observed (scratch copy s5-modal-mut):
//   x with a measurement, the strip and ANGLE offer the measured angle, and only a press applies
//     it: a rig with a rotator: the angle once the grid was made, before any press: expected "Any
//     angle", got "Rotate to PA"
// Each surface is pressed on each rig. With one surface per rig, ANGLE's
// button was pressed only where there is no rotator, and there USE MEASURED
// writes the same CAMERA FIXED AT the offer does, so a button that said
// ROTATE TO and wrote CAMERA FIXED AT passed.
// MUTANT "ANGLE's offer runs USE MEASURED" (AngleSection: the offer button's
// onClick is `p.onUseMeasured`). Observed (scratch copy S5-MODAL-xverify-mut,
// the S5-MODAL verifier, 2026-09-28; 20/20 before the second surface):
//   x with a measurement, the strip and ANGLE offer the measured angle, and only a press applies
//     it: a rig with a rotator: the angle after pressing framing-angle-offer: expected ["Rotate to
//     PA","37.2"], got ["Camera fixed at PA","37.2"]
await test("with a measurement, the strip and ANGLE offer the measured angle, and only a press applies it", async () => {
  for (const [what, hasRotator, mode, label] of [
    ["a rig with a rotator", true, "Rotate to PA", "ROTATE TO 37.2 deg"],
    ["a rig with no rotator", false, "Camera fixed at PA", "CAMERA FIXED AT 37.2 deg"],
  ] as const) for (const press of ["framing-strip-offer", "framing-angle-offer"] as const) {
    setup({ nodes: [ANY_1x1(), CYCLE], edges: LANE_ONLY, skyAngle: SKY, compiled: compiledWith({ has_rotator: hasRotator }) });
    mount();
    click(buttonByText("more cols"));
    eq(chosenAngle(), "Any angle", `${what}: the angle once the grid was made, before any press`);
    eq(q("framing-strip-offer")?.textContent, label, `${what}: the strip's offer`);
    eq(q("framing-angle-offer")?.textContent, label, `${what}: ANGLE's offer`);
    assert((q("framing-angle")?.textContent ?? "").includes("camera measured 37.2 deg, 14 min ago, by the centring solve, pier west"),
      `${what}: ANGLE does not say where the offered angle came from`);
    assert(locked(doneBtn()), `${what}: DONE is open before the offer was taken`);
    click(q(press));
    eq([chosenAngle(), degrees()], [mode, "37.2"], `${what}: the angle after pressing ${press}`);
    eq(stripAngle(), `${mode === "Rotate to PA" ? "rotate to" : "camera fixed at"} 37.2 deg`, `${what}: the strip once taken`);
    assert(q("framing-strip-offer") === null && q("framing-angle-offer") === null, `${what}: the offer outlived its taking`);
    assert(!locked(doneBtn()), `${what}: DONE stays locked once the offer was taken`);
    await done();
    eq(applyCalls.map((a) => a[1]), [{ cols: 2, angle: mode, rotation: 37.2 }], `${what}: what DONE wrote after ${press}`);
    unmount();
  }
});

// The control the ruling names: a single panel at ANY ANGLE is a whole,
// runnable block (ruling 9 locks its first solve's angle at the run), so the
// sheet leaves it be: no offer, no lock, and a DONE that writes nothing.
// MUTANT "offer to any draft" (framingModel `angleOffer` loses its owed
// test, so a single panel at any angle is offered the measured angle).
// Observed (scratch copy s5-modal-mut):
//   x a single panel at ANY ANGLE is left alone: no offer, DONE open, nothing written: the strip
//     offers an angle to a single panel
await test("a single panel at ANY ANGLE is left alone: no offer, DONE open, nothing written", async () => {
  setup({ nodes: [ANY_1x1(), CYCLE], edges: LANE_ONLY, skyAngle: SKY, compiled: compiledWith({ has_rotator: true }) });
  mount();
  eq(chosenAngle(), "Any angle", "the 1x1's angle");
  eq(stripAngle(), "any angle", "the strip's angle on the 1x1");
  assert(q("framing-strip-offer") === null, "the strip offers an angle to a single panel");
  assert(q("framing-angle-offer") === null, "ANGLE offers an angle to a single panel");
  assert(q("framing-use-measured"), "the single panel lost its USE MEASURED chip");
  assert(!locked(doneBtn()), "DONE is locked on a single panel at any angle");
  await done();
  eq(applyCalls.map((a) => a[1]), [{}], "what DONE wrote for the untouched 1x1");
});

// The strip sits outside the fieldset that freezes the draft, so its offer is
// left out while the sheet is frozen rather than disabled by it: in view mode
// (a running session, an Example) a press there would edit a draft no DONE
// can write. ANGLE's offer is inside the fieldset and is disabled with it.
// DONE's wait freezes the sheet too, but a grid that owes an angle has DONE
// locked, so no offer is on screen while DONE writes; view mode is the case.
// MUTANT "the strip's offer outlives the freeze" (the sheet's strip draws the
// offer on `offer && (` with no `!frozen`). Observed (scratch copy
// S5-MODAL-xverify-mut, the S5-MODAL verifier, 2026-09-28):
//   x in view mode the strip offers no angle, and ANGLE's offer is frozen with the draft: the
//     strip offers an angle in view mode
await test("in view mode the strip offers no angle, and ANGLE's offer is frozen with the draft", async () => {
  setup({ nodes: [target({ angle: "Any angle", rotation: -1 }), CYCLE], skyAngle: SKY, compiled: compiledWith({ has_rotator: true }) });
  act(() => { root.render(null); });
  act(() => { root.render(createElement(Sheet, { nodeId: "n2", onClose: () => {}, viewOnly: true })); });
  assert(q("framing-view-why"), "premise: the sheet opened in view mode");
  eq(stripAngle(), "no angle", "the strip's angle on a stored 3x2 at any angle, in view mode");
  assert(q("framing-strip-offer") === null, "the strip offers an angle in view mode");
  const inAngle = q("framing-angle-offer");
  assert(inAngle, "premise: ANGLE draws the offer in view mode");
  eq(inAngle.closest("fieldset")?.disabled, true, "ANGLE's offer is in a disabled fieldset in view mode");
  unmount();
  // Control: the same block, editable, is offered the angle in the strip.
  mount();
  assert(q("framing-view-why") === null, "premise: the editable sheet says it is view-only");
  eq(q("framing-strip-offer")?.textContent, "ROTATE TO 37.2 deg", "the strip's offer on the editable sheet");
  unmount();
});

// The rotate handle turns the angle, and the angle it is turned to is the
// one the grid takes, from ANY ANGLE too: that is an angle the operator chose,
// so the ruling leaves it (#411). `]` is the handle's key (SkyCanvas), 5 deg
// from where the canvas draws the frame, north up for no angle.
// MUTANT "the handle leaves any angle" (onSkyRotate writes the rotation
// alone, keeping the mode). Observed (scratch copy s5-modal-mut):
//   x the rotate handle sets the angle it is turned to, from ANY ANGLE too: the angle the handle
//     turned to (rotator true): expected ["Rotate to PA","5"], got ["Any angle",""]
await test("the rotate handle sets the angle it is turned to, from ANY ANGLE too", async () => {
  for (const [hasRotator, mode] of [[true, "Rotate to PA"], [false, "Camera fixed at PA"]] as const) {
    setup({ nodes: [target({ angle: "Any angle", rotation: -1 }), CYCLE], compiled: compiledWith({ has_rotator: hasRotator }) });
    mount();
    eq(chosenAngle(), "Any angle", "premise: a 3x2 stored at any angle");
    const sky = doc.querySelector('[data-testid="framing-sky"] [role="application"]') as any;
    act(() => { sky.dispatchEvent(new win.KeyboardEvent("keydown", { key: "]", bubbles: true })); });
    eq([chosenAngle(), degrees()], [mode, "5"], `the angle the handle turned to (rotator ${hasRotator})`);
    eq(stripAngle(), `${mode === "Rotate to PA" ? "rotate to" : "camera fixed at"} 5.0 deg`, "the strip after the handle");
    unmount();
  }
});

// ======================================================================
// RUN and CENTRING on a single target (#413), on the route's recorded answer
//
// A one-panel TARGET compiles to no group (`compile_plan` emits `mosaic:
// null`), so nothing reads `passes` or `minVisit` and there is nothing to
// rotate between; the flow-level "while a mosaic waits" setting reads nothing
// in a flow with no mosaic; a single target has no first panel; and the
// CENTRING choice governs the angle check at every acquisition as well as the
// centring (5.6). Each is graded on server/tests/fixtures/
// flow_readouts_single.json, the compile route's own answer for one.

function singleCompiled(): any {
  return { plan: {}, structural: [], issues: [], unmapped: [], readouts: SINGLE_FX.readouts, rig: SINGLE_FX.rig };
}
const setupSingle = () => setup({ nodes: [target({ rows: 1, cols: 1 }), CYCLE], edges: LANE_ONLY, compiled: singleCompiled() });

// MUTANT "one panel offers visits" (RunSection renders the loop, PASSES PER
// VISIT and AT LEAST rows whatever `onePanel` says). Observed (scratch copy
// s5-modal-mut):
//   x on a one-panel block RUN leaves out the loop, passes and minimum visit, which nothing reads:
//     the loop, passes and minimum-visit rows on a one-panel block: expected [false,false,false],
//     got [true,true,true]
await test("on a one-panel block RUN leaves out the loop, passes and minimum visit, which nothing reads", async () => {
  setupSingle();
  mount();
  assert(q("framing-run"), "premise: RUN is shown for a single target that owns a stage");
  eq(SINGLE_FX.readouts.n2.passes, null, "premise: the route's single target has no visit settings");
  const run = () => q("framing-run");
  eq([q("framing-loop") !== null, q("framing-passes") !== null, q("framing-min-visit") !== null],
    [false, false, false], "the loop, passes and minimum-visit rows on a one-panel block");
  assert(!run().textContent.includes(ROTATE_LABEL), `RUN still says "${ROTATE_LABEL}" on one panel`);
  // Control: a column added in the sheet makes the draft a grid, and the rows
  // it now reads come back.
  click(buttonByText("more cols"));
  eq([q("framing-loop") !== null, q("framing-passes") !== null, q("framing-min-visit") !== null],
    [true, true, true], "the loop, passes and minimum-visit rows once the draft is a 2x1");
});

// MUTANT "no-mosaic line dropped" (RunSection draws the whenWaiting row with
// no line under it). Observed (scratch copy s5-modal-mut):
//   x the whenWaiting row says the flow has no mosaic while it has none, the draft included: the
//     line under whenWaiting in a flow of one single target: expected "This flow has no mosaic
//     yet, so this changes nothing until a TARGET has more than one panel.", got undefined
// MUTANT "no mosaic from the stored graph" (the sheet asks the stored graph,
// `!graph.nodes.some(isMultiPanel)`, and not the draft). Observed (scratch
// copy s5-modal-mut):
//   x the whenWaiting row says the flow has no mosaic while it has none, the draft included: the
//     no-mosaic line once this block's draft is a 2x1
await test("the whenWaiting row says the flow has no mosaic while it has none, the draft included", async () => {
  eq(WHEN_WAITING_NO_MOSAIC, OVERVIEW_NO_MOSAIC, "RUN's no-mosaic line against the classic overview's");
  setupSingle();
  mount();
  eq(q("framing-no-mosaic")?.textContent, WHEN_WAITING_NO_MOSAIC, "the line under whenWaiting in a flow of one single target");
  // The draft counts: a column added here makes this block the flow's mosaic.
  click(buttonByText("more cols"));
  assert(q("framing-no-mosaic") === null, "the no-mosaic line once this block's draft is a 2x1");
  unmount();
  // Control: another block in the flow is a mosaic, so the setting is read.
  setup({ nodes: [target({ rows: 1, cols: 1 }), CYCLE, target({ name: "M33" }, "n9")], edges: LANE_ONLY, compiled: singleCompiled() });
  mount();
  assert(q("framing-run"), "premise: RUN shown");
  assert(q("framing-no-mosaic") === null, "the no-mosaic line in a flow whose other block is a 3x2");
});

// MUTANT "single focus as a mosaic's" (runLines: the `mode === "single"`
// wording of the once line deleted). Observed (scratch copy s5-modal-mut):
//   x a single target's focus line says a sweep only at the start, on the route's recorded answer:
//     a single target's RUN lines: expected ["1 panel x 7 filters x 45 = 315 subs","9.75 h per
//     panel, 9.75 h in all","focus: a sweep only at the start; set a temperature delta to refocus
//     as the night cools"], got ["1 panel x 7 filters x 45 = 315 subs","9.75 h per panel, 9.75 h
//     in all","focus: a sweep only at the first panel; set a temperature delta to refocus as the
//     night cools"]
await test("a single target's focus line says a sweep only at the start, on the route's recorded answer", async () => {
  const lines = () => Array.from(doc.querySelectorAll('[data-testid="framing-readouts"] .tfs-readout') as any[])
    .map((l: any) => l.textContent);
  setupSingle();
  mount();
  eq(SINGLE_FX.readouts.n2.mode, "single", "premise: the recorded block is a single target");
  eq(lines(), [
    "1 panel x 7 filters x 45 = 315 subs",
    "9.75 h per panel, 9.75 h in all",
    "focus: a sweep only at the start; set a temperature delta to refocus as the night cools",
  ], "a single target's RUN lines");
  unmount();
  // Control: the eighth Example's 3x2 keeps its first panel.
  setup();
  mount();
  eq(lines()[lines().length - 1],
    "focus: a sweep only at the first panel; set a temperature delta to refocus as the night cools",
    "the 3x2's focus line");
});

// MUTANT "centring label drops the angle" (CentringSection's label back to S4's
// "IF A PANEL WILL NOT CENTRE"). Observed (scratch copy s5-modal-mut):
//   x CENTRING's choice is labelled for the angle as well as the centring, in the inspector's
//     words: CENTRING's select label: expected "IF A PANEL WILL NOT CENTRE OR REACH ITS ANGLE", got
//     "IF A PANEL WILL NOT CENTRE"
await test("CENTRING's choice is labelled for the angle as well as the centring, in the inspector's words", async () => {
  setupSingle();
  mount();
  const label = doc.querySelector('label[for="tfs-if-not"]') as any;
  eq(label?.textContent, "IF A PANEL WILL NOT CENTRE OR REACH ITS ANGLE", "CENTRING's select label");
  const field = NODE_DEFS.target.fields.find((f: any) => f.key === "ifNotCentred");
  eq(label?.textContent, String(field?.label).toUpperCase(), "the label against the inspector's field");
});

// ======================================================================
// PANELS: Grid order says where the run starts (#412 item 4)
//
// The engine's `grid` policy is the snake ROTATED to start after the
// last-visited panel (spec 5.2), and the modal numbers it from 1-1, so after
// the first visit its numbers, in the rows and on the sky, are not the order
// the run uses. "Setting first" says its order is decided at the run; Grid
// order now says so too.
// MUTANT "grid order note removed" (PanelsSection draws no note for Grid
// order). Observed (scratch copy s5-modal-mut):
//   x Grid order carries a note that the run starts after the last-visited panel, as Setting first
//     carries its own: the note under Grid order: expected "grid order is turned at the run so it
//     starts after the last-visited panel: listed here from 1-1", got null
await test("Grid order carries a note that the run starts after the last-visited panel, as Setting first carries its own", async () => {
  setup({ progress: progress() });
  mount();
  const note = () => q("framing-order-note")?.textContent ?? null;
  eq(note(), null, "a note under Least complete first, which the rows do follow");
  choose(doc.querySelector("#tfs-order"), "Grid order");
  eq(note(), GRID_ORDER_NOTE, "the note under Grid order");
  assert(/starts after the last-visited panel/.test(GRID_ORDER_NOTE), `the note does not say where the run starts: ${GRID_ORDER_NOTE}`);
  choose(doc.querySelector("#tfs-order"), "Setting first");
  eq(note(), SETTING_FIRST_NOTE, "the note under Setting first");
});

// ======================================================================
// PANELS in run mode: the run's line under a panel, in the run's words
// (S7: #451, #509; spec 2.6, 5.8).
//
// PANELS mounted on its own, for the recorded night's 2x2, each panel's run
// state asked of a recorded sequence state through the reader the sheet
// uses, framingModel `runPanelsOf`, with the sequence state passed as the
// sheet passes it since the S7 integration (it calls flowRunState
// `panelStateOf` for each panel): so the words are graded on what the engine
// published, and runMode.test.tsx grades the sheet's wiring. Asked through
// `runPanelsOf`, this case is red under framingModel.ts's mutant "state not
// passed" (`panelStateOf(label, group, run)` made `panelStateOf(label, group,
// { state: "running" })`), 22/23 in the integration's private copy
// (scratchpad S7-INTEG-r2-mut). It used to ask `panelStateOf` directly, so no
// mutant of the sheet's own reader could reach it:
//   x PANELS says what the run is doing to its panel, on the recorded states: shot, paused, holding
//     for cloud, stopping: PANELS under the recorded paused state: expected {"1-1":"1-1: current
//     panel, run paused",...}, got {"1-1":"1-1: shooting now",...} The engine's
// set-aside reason carries an em dash, which the console printed as a
// replacement character; it is elided below as "...".

/** PANELS for the recorded 2x2 in run mode, under the recorded `state`. */
function mountRunPanels(state: any): void {
  const run = runPanelsOf(state.group, 2, 2, { rows: 2, cols: 2 }, state);
  const rows = panelRows({
    rows: 2, cols: 2, skip: [], progress: null, answerPanels: null, order: "Least complete first", run,
  });
  act(() => { root.render(null); });
  act(() => {
    root.render(createElement(PanelsSection, {
      rows, order: "Least complete first", showAltitude: false, nightCard: null,
      onOrder: () => {}, onToggle: () => {},
    }));
  });
}
/** PANELS' run lines, by label. */
function panelRunLines(): Record<string, string> {
  const pairs: [string, string][] = [];
  for (const el of Array.from(doc.querySelectorAll('[data-testid="framing-panel-run"]')) as any[]) {
    pairs.push([el.getAttribute("data-panel-run"), el.textContent]);
  }
  return Object.fromEntries(pairs.sort(([a], [b]) => a.localeCompare(b)));
}
const RUN_REASON: string = RUN_FX.shooting.group.set_aside[0].reason;

// MUTANT "state check dropped" (flowRunState.ts `panelStateOf` answering
// `shooting` for the group's panel whatever the run's state). Observed, 22/23
// (flowRunState.test.ts is red on the recorded hold too):
//   x PANELS says what the run is doing to its panel, on the recorded states: shot, paused, holding
//     for cloud, stopping: PANELS under the recorded paused state: expected {"1-1":"1-1: current
//     panel, run paused","2-2":"set aside tonight: centring failed on 2-2 on 3 consecutive visits:
//     plate solve failed ... used raw GoTo"}, got {"1-1":"1-1: shooting now","2-2":"set aside
//     tonight: centring failed on 2-2 on 3 consecutive visits: plate solve failed ... used raw GoTo"}
// MUTANT "held words read as shooting" (PanelsSection.tsx `runLine` answering
// SHOOTING_NOW for the current kind). Observed, 22/23, the same line.
// MUTANT "hold worded as cloud whatever it is" (`runLine` answering
// CURRENT_HOLDING_FOR_CLOUD for every hold). Observed, 22/23:
//   x PANELS says what the run is doing to its panel, on the recorded states: shot, paused, holding
//     for cloud, stopping: a hold that names no reason: expected "current panel, run holding", got
//     "current panel, holding for cloud"
await test("PANELS says what the run is doing to its panel, on the recorded states: shot, paused, holding for cloud, stopping", async () => {
  const aside = `${SET_ASIDE_TONIGHT}: ${RUN_REASON}`;
  const cases: [string, string][] = [
    ["shooting", SHOOTING_NOW], ["paused", CURRENT_PAUSED],
    ["holding", CURRENT_HOLDING_FOR_CLOUD], ["aborting", CURRENT_STOPPING],
  ];
  for (const [kind, words] of cases) {
    mountRunPanels(RUN_FX[kind]);
    eq(panelRunLines(), { "1-1": `1-1: ${words}`, "2-2": aside }, `PANELS under the recorded ${kind} state`);
    if (kind !== "shooting") {
      const text = String(q("framing-panels")?.textContent ?? "");
      assert(!text.includes(SHOOTING_NOW), `PANELS says "${SHOOTING_NOW}" while the run is ${kind}: ${text}`);
    }
  }
  // An abort that still carries the hold's key (#513) reads as stopping,
  // never as holding: the words are the state's. Since H4 the engine takes
  // the hold off the aborting publish, so the recorded abort carries none
  // (premise), and the key a pre-H4 server left on it is put back here.
  // Re-pinned by the H4 integration: this read the key off the recorded
  // abort, which was then premise enough. RED under flowRunState.test.ts's
  // mutant "worded by the hold" (panelStateOf asking `run.hold` before the
  // state), run by the H4 integration (observed, 22/23):
  //   x PANELS says what the run is doing to its panel, on the recorded states: shot, paused, holding
  //     for cloud, stopping: PANELS under an abort that still carries the hold's key: expected
  //     {"1-1":"1-1: current panel, run stopping","2-2":"set aside tonight: centring failed on 2-2 on
  //     3 consecutive visits: plate solve failed — used raw GoTo"}, got {"1-1":"1-1: current panel,
  //     holding for cloud","2-2":"set aside tonight: centring failed on 2-2 on 3 consecutive visits:
  //     plate solve failed — used raw GoTo"}
  eq(RUN_FX.aborting.hold ?? null, null, "premise: the recorded abort carries no hold (#513, H4)");
  mountRunPanels({ ...RUN_FX.aborting, hold: "clouds" });
  eq(panelRunLines(), { "1-1": `1-1: ${CURRENT_STOPPING}`, "2-2": aside },
    "PANELS under an abort that still carries the hold's key");
  // A hold whose reason the engine does not name is a hold, not cloud.
  eq(runLine({ kind: "current", run: "holding", hold: null }), CURRENT_HOLDING, "a hold that names no reason");
  eq(runLine({ kind: "current", run: "holding", hold: "clouds" }), CURRENT_HOLDING_FOR_CLOUD, "a hold for cloud");
});

// MUTANT "label prefixed again" (PanelsSection.tsx `runRowText` prefixing the
// label whatever the reason says, as S5 printed every line). Observed, 21/23
// (the case above is red on the recorded shooting state's 2-2 line too, and
// runMode.test.tsx's three cases that pin it):
//   x the set-aside line names its panel once: a reason that names it stands alone, and one that
//     does not keeps the label: 2-2's line on the recorded state: expected "set aside tonight:
//     centring failed on 2-2 on 3 consecutive visits: plate solve failed ... used raw GoTo", got
//     "2-2: set aside tonight: centring failed on 2-2 on 3 consecutive visits: plate solve failed
//     ... used raw GoTo"
// MUTANT "label dropped always" (`runRowText` answering `runLine` alone, for
// every panel). Observed, 21/23 (the case above is red at 1-1's line, "1-1":
// "shooting now"):
//   x the set-aside line names its panel once: a reason that names it stands alone, and one that
//     does not keeps the label: a reason that does not name the panel: expected "2-2: set aside
//     tonight: guiding did not start on any panel of M31", got "set aside tonight: guiding did not
//     start on any panel of M31"
// MUTANT "names a longer label" (`namesPanel` answering `text.includes(label)`).
// Observed, 22/23:
//   x the set-aside line names its panel once: a reason that names it stands alone, and one that
//     does not keeps the label: 12-2 is not 2-2: expected false, got true
await test("the set-aside line names its panel once: a reason that names it stands alone, and one that does not keeps the label", async () => {
  assert(RUN_REASON.startsWith("centring failed on 2-2"), `premise: the engine's reason names its panel: ${RUN_REASON}`);
  mountRunPanels(RUN_FX.shooting);
  const line = panelRunLines()["2-2"];
  eq(line, `${SET_ASIDE_TONIGHT}: ${RUN_REASON}`, "2-2's line on the recorded state");
  eq(line.split("2-2").length - 1, 1, "times 2-2's line says 2-2");
  // CONTROL: the engine's words for a mosaic set aside when guiding never
  // started name no panel, so the line must.
  const guiding = "guiding did not start on any panel of M31";
  const other = {
    ...RUN_FX.shooting,
    group: { ...RUN_FX.shooting.group, set_aside: [{ panel: "2-2", reason: guiding }] },
  };
  mountRunPanels(other);
  eq(panelRunLines()["2-2"], `2-2: ${SET_ASIDE_TONIGHT}: ${guiding}`, "a reason that does not name the panel");
  // A whole label only: another panel's longer label is not this one.
  eq(namesPanel("centring failed on 12-2 on 3 consecutive visits", "2-2"), false, "12-2 is not 2-2");
  eq(namesPanel("centring failed on 2-21 on 3 consecutive visits", "2-2"), false, "2-21 is not 2-2");
  eq(namesPanel("M31 2-2: below the floor", "2-2"), true, "a reason that ends the name with 2-2");
  eq(namesPanel("2-2", "2-2"), true, "the label alone");
  eq(runRowText("2-2", { kind: "set_aside", reason: "centring failed on 12-2" }),
    `2-2: ${SET_ASIDE_TONIGHT}: centring failed on 12-2`, "a reason naming 12-2, on 2-2's line");
  eq(runRowText("1-1", null), null, "a panel the run is doing nothing to");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`framingSections.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
