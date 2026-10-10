// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w19SheetsLiveRun.test.tsx - RIG - the camera, focuser, mount, power and filter
// wheel sheets, MOUNTED: each treats EVERY live run state as the run owning its
// device, not only running and paused (#922, WP-161, wave 19).
//
//   Run directly:  npx tsx src/next/hubs/rig/sheets/__tests__/w19SheetsLiveRun.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. Each sheet carried its own `state === "running" || state ===
// "paused"` (`flowOwns`, `seqOwnsCamera`, `runOwns`) to decide whether the run
// owns the camera, the mount, the filter wheel or the session-critical power
// ports. A cloud hold ("holding") and an abort's wind-down ("aborting") are
// `engine.running` on the rig, so during either the sheet armed controls the run
// owns: a cooler switch, a SINGLE shutter, the mount pad, a mount power port and
// LEARN OFFSETS, each of which the rig then refused or, worse, accepted.
//
// WHAT IS WORTH ASSERTING. Per sheet, per state in the union: one control that
// the run owns is locked with the run's own sentence exactly when the run is
// live, and is not locked by a run that is not (the control row, which a
// predicate that is always true fails).
//
// MUTANT "running or paused only" (each sheet's `runIsLive(sequence)` made
// `sequence?.state === "running" || sequence?.state === "paused"`, the unfixed
// text). Run from a byte backup, restored byte-identically (md5sum compared):
// see the report for the failing lines.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.hasPointerCapture = function () { return true; };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "Image", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// A simulator-shaped power box, so the power sheet has ports to lock.
const PORTS = [
  { id: 0, name: "Mount 12V", can_write: true, is_boolean: true, value: 1, min: 0, max: 1, unit: "" },
  { id: 2, name: "Bench light", can_write: true, is_boolean: true, value: 0, min: 0, max: 1, unit: "" },
];
const ok = (json: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });
g.fetch = async (url: string) => {
  const u = String(url);
  if (u.includes("/api/switch/ports")) return ok(PORTS);
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { CameraSheet, FLOW_OWNS_CAMERA, FLOW_OWNS_CAMERA_PAUSED } = await import("../camera");
const { FocuserSheet, FLOW_OWNS_CAMERA: FOCUSER_OWNS, FLOW_OWNS_CAMERA_PAUSED: FOCUSER_OWNS_PAUSED } =
  await import("../focuser");
const { MountSheet, FLOW_OWNS_MOUNT } = await import("../mount");
const { PowerSheet } = await import("../power");
const { legacyLockReason } = await import("../../lib/portSettings");
const { WheelSheet, FLOW_OWNS_WHEEL } = await import("../wheel");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
const ADMIN = { ...OPERATOR, role: "admin", caps: [...OPERATOR.caps, "control.power", "config.safety"] };

const FRAMES = {
  capture: { exposure_s: 120, gain: 100, offset: 50, binning: 1, filter: null },
  focus: { exposure_s: 3, gain: 200, offset: 30, binning: 1, filter: null },
  solve: { exposure_s: 3, gain: 100, offset: 50, binning: 1, filter: null },
  guide: { exposure_s: 2, gain: 100, offset: 10, binning: 1, filter: null },
};
const NAMES = ["L", "R", "G", "B", "Ha", "OIII", "SII"];

/** One status carrying every device the five sheets read. */
const STATUS = {
  connected: {
    camera: { name: "ZWO ASI2600MM Pro", kind: "camera", connected: true },
    focuser: { name: "ZWO EAF", kind: "focuser", connected: true },
    filterwheel: { name: "Wanderer Snowflake", kind: "filterwheel", connected: true },
    telescope: { connected: true, name: "ZWO AM5N" },
    switch: { name: "UPB", kind: "switch", connected: true },
  },
  looping: false,
  busy_lanes: [],
  busy: null,
  backend_links: [
    { role: "camera", connected: true, error: null },
    { role: "focuser", connected: true, error: null },
    { role: "filterwheel", connected: true, error: null },
    { role: "telescope", connected: true, error: null },
    { role: "switch", connected: true, error: null },
  ],
  camera: {
    temperature: -9.4, can_cool: true, has_dew_heater: true, width: 6248, height: 4176,
    max_gain: 300, max_bin: 4, egain: 0, egain_learned: {},
    cooler: { on: true, power: 62, target_c: -10, at_target: false, can_report_power: true },
  },
  focuser: { position: 19950, max: 20000, temperature: 11.2, moving: false, can_set_position: true },
  filterwheel: {
    position: 0, names: NAMES, current: "L", offsets: [0, 12, -8, 4, 60, 55, 58],
    opaque: [false, false, false, false, false, false, false],
    narrowband: [false, false, false, false, true, true, true],
    exposures: [60, 120, 120, 120, 300, 300, 300],
    gains: [100, 100, 100, 100, 200, 200, 200], moving: false,
  },
  mount: {
    ra_hours: 20.5, dec_deg: 60.1, ra_str: "20h 30m", dec_str: "+60° 06'",
    tracking: true, parked: false, slewing: false, tracking_rate: "sidereal",
    can_set_tracking_rate: true, can_find_home: true, max_rate_deg_s: 1.44,
    position_known: true,
  },
  meridian: { status: "counting", hours_to_flip: 1.63, flip_enabled: true, pier_side: "east" },
};

function seed(sequence: Record<string, unknown>): void {
  act(() => {
    useStore.setState({
      status: STATUS,
      equipConnected: true,
      wsPhase: "up",
      authGate: "open",
      principal: ADMIN,
      frameSettings: FRAMES,
      config: {
        cooling: { warm_ramp: true, warm_rate_c_per_min: 2, warm_ambient_c: null },
        standards: {
          apply_filter_offsets: true, refocus_on_temp_delta_c: 1.5, min_stars: 0,
          max_guide_rms: 0, max_eccentricity: 0, max_consecutive_rejects: 0,
          max_consecutive_rejects_night: 0,
        },
        escalation: { hfr_reject_factor: 0, hfr_reject_action: "warn" },
        safety: { solar_avoidance: true, solar_exclusion_deg: 30 },
      },
      sequence,
      polar: {
        state: "idle", az_error: 0, alt_error: 0, total_error: 0,
        progress: 0, message: "", source: null,
      },
      egainLearn: null,
      filterOffsetsLearn: null,
      focus: null,
      lastAutofocusResult: null,
      plan: { autofocus_every: 30, steps: [] },
      previews: [],
      livePreviewId: null,
      selectedPreviewId: null,
      mountOp: null,
      logs: [],
      toasts: [],
      confirm: null,
      locked: false,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
async function mount(Sheet: unknown): Promise<void> {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(Sheet as any, { params: {}, depth: 0 })); });
  await settle();
}
const q = (sel: string) => container.querySelector(sel) as any;
const text = () => (container.textContent || "") as string;
const locked = (node: any): boolean => node?.getAttribute("aria-disabled") === "true";
const titleOf = (node: any): string => (node?.getAttribute("title") ?? "") as string;

/** The whole `SequenceState["state"]` union, with whether the rig's
 *  `engine.running` is true in it. */
const STATES: Array<[string, boolean]> = [
  ["running", true], ["paused", true], ["holding", true], ["aborting", true],
  ["idle", false], ["complete", false], ["aborted", false], ["error", false],
  ["nina_native", false],
];

// ------------------------------------------------------------------- camera
for (const [state, live] of STATES) {
  await testAsync(`camera sheet, run ${state}: the cooler switch is ${live ? "locked with the run's camera sentence" : "not locked by a run"}`, async () => {
    seed({ state, target: "NGC 6946" });
    await mount(CameraSheet);
    const sw = q('[data-testid="cooler-switch"]');
    assert(sw != null, "no cooler switch (nothing below grades a blank page)");
    if (live) {
      assert(locked(sw), `the cooler switch is ARMED while the run is ${state}: the run owns the camera`);
      eq(titleOf(sw), state === "paused" ? FLOW_OWNS_CAMERA_PAUSED : FLOW_OWNS_CAMERA,
        `the cooler switch's reason while the run is ${state}`);
    } else {
      assert(!locked(sw), `the cooler switch is locked by a run that is ${state}: ${titleOf(sw)}`);
    }
  });
}

// ------------------------------------------------------------------ focuser
for (const [state, live] of STATES) {
  await testAsync(`focuser sheet, run ${state}: SINGLE is ${live ? "locked, and a banner says the run has the camera" : "not locked by a run"}`, async () => {
    seed({ state, target: "NGC 6946" });
    await mount(FocuserSheet);
    const single = q('[data-testid="focus-single"]');
    assert(single != null, "no SINGLE shutter (nothing below grades a blank page)");
    if (live) {
      assert(locked(single), `SINGLE is ARMED while the run is ${state}: the run owns the camera`);
      eq(titleOf(single), "A sequence owns the camera - stop it first",
        `SINGLE's reason while the run is ${state}`);
      assert(text().includes(state === "paused" ? FOCUSER_OWNS_PAUSED : FOCUSER_OWNS),
        `the sheet carries no banner saying the run has the camera while it is ${state}`);
    } else {
      assert(!locked(single), `SINGLE is locked by a run that is ${state}: ${titleOf(single)}`);
    }
  });
}

// -------------------------------------------------------------------- mount
for (const [state, live] of STATES) {
  await testAsync(`mount sheet, run ${state}: the RA step is ${live ? "locked, and the header says the flow owns the mount" : "not locked by a run"}`, async () => {
    seed({ state, target: "NGC 6946" });
    await mount(MountSheet);
    const track = q('[data-testid="mount-ra-step"] .nx-dial-track');
    assert(track != null, "no RA step dial (nothing below grades a blank page)");
    const header = String(q(".nx-sheet-live")?.textContent ?? "");
    if (live) {
      eq(titleOf(track), FLOW_OWNS_MOUNT, `the RA step's reason while the run is ${state}`);
      assert(/owned by the flow/.test(header),
        `the header does not say the flow owns the mount while the run is ${state}: "${header}"`);
    } else {
      assert(titleOf(track) !== FLOW_OWNS_MOUNT, `the RA step is locked by a run that is ${state}`);
      assert(!/owned by the flow/.test(header), `the header claims a flow while the run is ${state}: "${header}"`);
    }
  });
}

// -------------------------------------------------------------------- power
for (const [state, live] of STATES) {
  await testAsync(`power sheet, run ${state}: the mount port is ${live ? "locked: session-critical" : "not locked by a run"}`, async () => {
    seed({ state, target: "NGC 6946" });
    await mount(PowerSheet);
    await settle();
    const port = q('[data-testid="port-0"]');
    assert(port != null, `no Mount 12V port (nothing below grades a blank page): ${text().slice(0, 200)}`);
    if (live) {
      assert(locked(port), `the Mount 12V port is ARMED while the run is ${state}: the run owns the mount`);
      eq(titleOf(port), legacyLockReason("Mount 12V"), `the port's reason while the run is ${state}`);
    } else {
      assert(!locked(port), `the Mount 12V port is locked by a run that is ${state}: ${titleOf(port)}`);
    }
  });
}

// -------------------------------------------------------------------- wheel
for (const [state, live] of STATES) {
  await testAsync(`wheel sheet, run ${state}: the sheet ${live ? "says the run has the camera" : "names no run"}`, async () => {
    seed({ state, target: "NGC 6946" });
    await mount(WheelSheet);
    assert(q('[data-testid="rig-wheel"]') != null, "no wheel sheet (nothing below grades a blank page)");
    if (live) {
      assert(text().includes(FLOW_OWNS_WHEEL),
        `the wheel sheet does not say the run owns the wheel while it is ${state}`);
    } else {
      assert(!text().includes(FLOW_OWNS_WHEEL), `the wheel sheet claims a run while it is ${state}`);
    }
  });
}

if (rootRef) act(() => { rootRef!.unmount(); });

console.log(`w19SheetsLiveRun: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
