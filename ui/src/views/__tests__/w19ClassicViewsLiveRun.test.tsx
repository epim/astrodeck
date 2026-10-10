// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w19ClassicViewsLiveRun.test.tsx - the CLASSIC Capture, Focus, Sequence and
// Tonight views, MOUNTED: each treats EVERY live run state as a live run, not
// only running and paused (#922, WP-161, wave 19).
//
//   Run directly:  npx tsx src/views/__tests__/w19ClassicViewsLiveRun.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The classic root is the production root, and four of its views
// each spelled the live-run test out by hand and left a state out.
//   CaptureView / FocusView (`seqOwnsCamera`): `running || paused`. A cloud hold
//     or an abort's wind-down left SINGLE and LOOP armed, no notice that the
//     camera was reserved, and the focus page without its "a run has the
//     camera" card.
//   SequenceView (`running`): `running || paused || aborting`, no "holding". On
//     a cloudy night the view dropped the run panel and the Abort control and
//     unlocked the plan editor for as long as the hold lasted.
//   TonightView (`running`): `running || paused`. The "Imaging now" line, which
//     exists so opening Tonight mid-run never reads as "nothing is happening",
//     vanished through a hold.
//
// WHAT IS WORTH ASSERTING. Per view, per state in the union, one thing the
// operator reads or can press that exists exactly when the run is live (the
// control row, which a predicate that is always true fails).
//
// MUTANT "no holding / running or paused only" (each view's `runIsLive(sequence)`
// made the unfixed comparison). Run from a byte backup, restored
// byte-identically (md5sum compared): see the report for the failing lines.

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
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};
win.Element.prototype.hasPointerCapture = function () { return false; };
win.Element.prototype.scrollIntoView = function () {};

const NOW_S = Math.floor(Date.now() / 1000);
const NIGHT = {
  date: "2026-08-05", transit_unix: NOW_S + 3600, transit_alt: 70,
  transit_in_daylight: false,
  dark_start_unix: NOW_S, dark_end_unix: NOW_S + 7200,
  darkness_kind: "astronomical",
  samples: [
    { unix: NOW_S, alt: 40, az: 90, moon_sep: 90 },
    { unix: NOW_S + 3600, alt: 70, az: 150, moon_sep: 90 },
    { unix: NOW_S + 7200, alt: 45, az: 220, moon_sep: 90 },
  ],
  moon: { illumination: 0.2, alt: -20, az: 10, separation_deg: 90 },
  best_window: { start_unix: NOW_S, end_unix: NOW_S + 7200, mean_alt: 55 },
  alt_limit_deg: 30, never_rises_above_limit: false,
};
const posts: string[] = [];
const respond = (body: unknown) => ({
  ok: true, status: 200, statusText: "OK", headers: { get: () => "application/json" },
  json: async () => body, text: async () => JSON.stringify(body),
});
const fetchStub = async (url: any, init: any = {}) => {
  const path = String(url);
  if ((init.method ?? "GET").toUpperCase() === "POST") posts.push(path);
  if (path.includes("/api/sequence/recoverable")) return respond({ recoverable: false });
  if (path.includes("/api/visibility")) return respond(NIGHT);
  if (path.includes("/api/catalog/tonight")) return respond({ picks: [], notes: [] });
  if (path.includes("/api/catalog")) return respond({ results: [], notes: [] });
  if (path.includes("/api/calibration/masters") || path.includes("/api/plans")) return respond([]);
  return respond({});
};
win.fetch = fetchStub;

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "Element",
  "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle", "matchMedia",
  "WebSocket", "fetch", "ResizeObserver", "IntersectionObserver", "Image", "URL", "Blob",
]) {
  const v = k === "window" ? win
    : k === "ResizeObserver" || k === "IntersectionObserver"
      ? class { observe() {} unobserve() {} disconnect() {} }
      : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const CaptureView = (await import("../CaptureView")).default;
const FocusView = (await import("../FocusView")).default;
const SequenceView = (await import("../SequenceView")).default;
const TonightView = (await import("../TonightView")).default;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const settle = async (ms = 0) => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
  await act(async () => { await Promise.resolve(); });
};

/** The whole `SequenceState["state"]` union, with whether the rig's
 *  `engine.running` is true in it. */
const STATES: Array<[string, boolean]> = [
  ["running", true], ["paused", true], ["holding", true], ["aborting", true],
  ["idle", false], ["complete", false], ["aborted", false], ["error", false],
  ["nina_native", false],
];

const PROGRESS = {
  frames_done: 3, frames_total: 20, percent: 15, elapsed_s: 900, rejected: 0,
  server_now_ms: Date.now(), current_exposure_s: 300, frame_started_at_ms: null,
};
function seed(sequence: Record<string, unknown>): void {
  act(() => {
    useStore.setState({
      status: {
        connected: {
          camera: { connected: true, name: "sim cam" },
          focuser: { connected: true, name: "sim foc" },
        },
        looping: false, mode: "sim", busy_lanes: [],
        camera: {
          temperature: -4.2, can_cool: true, width: 1000, height: 800, max_gain: 300, max_bin: 2,
        },
        focuser: { position: 12000, max: 40000, moving: false, temperature: 5.5 },
        filterwheel: { names: ["L", "Ha"], opaque: [false, false], position: 0 },
      },
      principal: { role: "operator", email: null, caps: ["view.status", "control.capture", "control.mount"] },
      plan: {
        ...(useStore.getState() as any).plan,
        name: "NGC7000 SHO",
        targets: [{
          id: "t1", name: "NGC7000", ra_hours: 20.98, dec_deg: 44.5,
          center: true, autofocus_first: false, calibration: false,
          steps: [{ id: "s1", filter: "Ha", exposure_s: 300, gain: 100, offset: 10, binning: 1, count: 20 }],
        }],
      },
      sequence: { ...sequence, plan_name: "NGC7000 SHO", target: "NGC7000", progress: PROGRESS },
      polar: {
        state: "idle", az_error: 0, alt_error: 0, total_error: 0, progress: 0,
        message: "", source: null,
      },
      toasts: [],
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
async function mount(View: unknown): Promise<void> {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  await act(async () => { rootRef!.render(createElement(View as any)); });
  await settle(30);
}
const text = (): string => (container.textContent || "").replace(/\s+/g, " ");
const buttons = (): any[] => [...container.querySelectorAll("button")];
const byText = (re: RegExp): any => buttons().find((b: any) => re.test((b.textContent || "").trim()));

/** The Capture view's notice word per state. */
const CAPTURE_NOTICE: Record<string, string> = {
  running: "Sequence running", paused: "Sequence paused",
  holding: "Sequence holding", aborting: "Sequence stopping",
};

// ------------------------------------------------------------ Capture view
for (const [state, live] of STATES) {
  await testAsync(`Capture view, run ${state}: ${live ? "the camera is reserved and the notice says so" : "no reservation"}`, async () => {
    seed({ state });
    await mount(CaptureView);
    assert(/Exposure/.test(text()), `the Capture view did not render: ${text().slice(0, 200)}`);
    if (live) {
      assert(text().includes(`${CAPTURE_NOTICE[state]} — camera reserved.`),
        `no "${CAPTURE_NOTICE[state]}" notice while the run is ${state}: ${text().slice(0, 300)}`);
    } else {
      assert(!/camera reserved/.test(text()), `a reservation notice while the run is ${state}`);
    }
  });
}

// -------------------------------------------------------------- Focus view
for (const [state, live] of STATES) {
  await testAsync(`Focus view, run ${state}: ${live ? "the page says a run has the camera" : "names no run"}`, async () => {
    seed({ state });
    await mount(FocusView);
    assert(/Focus/i.test(text()), `the Focus view did not render: ${text().slice(0, 200)}`);
    if (live) {
      assert(/A run has the camera/.test(text()),
        `the Focus view does not say a run has the camera while the run is ${state}`);
    } else {
      assert(!/A run has the camera/.test(text()), `the Focus view claims a run while it is ${state}`);
    }
  });
}

// ----------------------------------------------------------- Sequence view
const abortButton = () => byText(/Abort/);
for (const [state, live] of STATES) {
  await testAsync(`Sequence view, run ${state}: the Abort control is ${live ? "on screen" : "absent"}`, async () => {
    seed({ state });
    await mount(SequenceView);
    assert(/NGC7000/.test(text()), `the Sequence view did not render the plan: ${text().slice(0, 200)}`);
    if (live) {
      assert(abortButton() != null,
        `no Abort control while the run is ${state}: the operator cannot stop a run that is still live`);
    } else {
      assert(abortButton() == null, `an Abort control while the run is ${state}`);
    }
  });
}
await testAsync("Sequence view: a cloud hold is labelled HOLDING and keeps the run strip and the progress", async () => {
  seed({ state: "holding" });
  await mount(SequenceView);
  const badge = [...container.querySelectorAll("span")].find((s: any) =>
    /^(RUNNING|PAUSING|PAUSED|ABORTING|HOLDING|COMPLETE|ERROR|ABORTED)$/.test((s.textContent || "").trim()));
  assert(badge != null && (badge.textContent || "").trim() === "HOLDING",
    `the state badge reads "${badge?.textContent ?? ""}" during a hold`);
  assert(container.querySelector("[data-sequence-strip]") != null, "the run strip is gone during a hold");
  assert(/frame 4\/20/.test(text()), `the strip lost its frame ordinal during a hold: ${text().slice(0, 300)}`);
});

// ------------------------------------------------------------ Tonight view
const TONIGHT_WORD: Record<string, string> = {
  running: "Imaging now", paused: "Paused on", holding: "Holding on", aborting: "Stopping on",
};
for (const [state, live] of STATES) {
  await testAsync(`Tonight view, run ${state}: ${live ? `the active-session line reads "${TONIGHT_WORD[state]}"` : "no active-session line"}`, async () => {
    seed({ state });
    await mount(TonightView);
    if (live) {
      assert(text().includes(`${TONIGHT_WORD[state]} · NGC7000`),
        `no "${TONIGHT_WORD[state]} · NGC7000" line while the run is ${state}: ${text().slice(0, 300)}`);
    } else {
      assert(!/(Imaging now|Paused on|Holding on|Stopping on)/.test(text()),
        `an active-session line while the run is ${state}: ${text().slice(0, 300)}`);
    }
  });
}

if (rootRef) act(() => { rootRef!.unmount(); });

console.log(`w19ClassicViewsLiveRun: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
