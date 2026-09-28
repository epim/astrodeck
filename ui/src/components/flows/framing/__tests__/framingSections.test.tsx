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
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// s4-umodal-mut), never in the shared tree (#254), and the failure it
// produced is quoted verbatim. After the limit reset every one was run
// again against the current tree in s4-umodal-r2-mut (2026-09-27), and
// each was red with the failure quoted.

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
const { ANY_ANGLE_ON_A_GRID, NO_OPTICS, NO_ROTATOR } = await import("../framingModel");
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
// From first principles, not through the code under test: the bin-1 pixel
// scale is 206.265 x pixel (um) / focal length (mm), config.py's one constant
// (`ARCSEC_PER_RAD`), and the field is that scale times the sensor, as
// config.py `fov_deg` computes it.
const SCALE = (206.265 * 3.76) / 530;
const FOV_X = (SCALE * 4144) / 3600;
const FOV_Y = (SCALE * 2822) / 3600;

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

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`framingSections.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
