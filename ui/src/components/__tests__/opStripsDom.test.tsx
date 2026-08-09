// opStripsDom.test.tsx — the operation strips (2026-08-07), MOUNTED.
//
//   Run directly:  npx tsx src/components/__tests__/opStripsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Three long operations used to render as a stuck busy button: a goto's
// centering solves, the guide calibration walk, and a running sequence
// watched from a phone. Each strip is pinned here at the DOM: the narration
// it renders, the dials it PUTs, and the verdicts it must not swallow.

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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
const assert = {
  ok(cond: unknown, msg?: string) { if (!cond) throw new Error(msg ?? "not ok"); },
  equal(a: unknown, b: unknown, msg?: string) {
    if (a !== b) throw new Error(msg ?? `${String(a)} !== ${String(b)}`);
  },
  match(text: string, re: RegExp, msg?: string) {
    if (!re.test(text)) throw new Error(msg ?? `no match for ${re} in: ${text.slice(0, 250)}`);
  },
  deepEqual(a: unknown, b: unknown, msg?: string) {
    const ja = JSON.stringify(a), jb = JSON.stringify(b);
    if (ja !== jb) throw new Error(msg ?? `${ja} !== ${jb}`);
  },
};

const { api } = await import("../../api");
const { useStore } = await import("../../store");
const GotoStrip = (await import("../GotoStrip")).default;
const GuideQuickBar = (await import("../GuideQuickBar")).default;
const SequenceRunStrip = (await import("../sequence/SequenceRunStrip")).default;

const puts: Array<{ path: string; body: unknown }> = [];
(api as any).put = async (path: string, body?: unknown) => {
  puts.push({ path, body });
  return { exposure_s: 3, gain: 200, binning: 1 };
};
(api as any).get = async (_path: string) =>
  ({ exposure_s: 2, gain: 100, binning: 1 });

useStore.setState({
  principal: { role: "operator", email: null,
               caps: ["view.status", "control.mount", "control.guide"] },
} as any);

const root = createRoot(win.document.getElementById("root"));
const render = (el: React.ReactElement) => act(() => root.render(el));
const text = () => win.document.body.textContent ?? "";
const q = (sel: string): any => win.document.querySelector(sel);

// ------------------------------------------------------------------ GotoStrip

test("an idle mount renders no goto strip", () => {
  useStore.setState({ mountOp: null, status: { busy_lanes: [] } } as any);
  render(React.createElement(GotoStrip));
  assert.equal(win.document.getElementById("root").children.length, 0);
});

test("a centering solve narrates attempt and activity with the ring", () => {
  useStore.setState({
    status: { busy_lanes: ["goto"], mount: { slewing: false } },
    mountOp: { attempt: 2, activity: "solving" },
  } as any);
  render(React.createElement(GotoStrip));
  assert.match(text(), /solving…/);
  assert.match(text(), /attempt 2\/3/);
  assert.ok(q("[data-activity-ring='orbit']"), "no indeterminate ring for ASTAP");
});

test("the inert-mount verdict is loud and names the fix", () => {
  useStore.setState({
    status: { busy_lanes: [] },
    mountOp: { stuck: true, error_arcmin: 33.7 },
  } as any);
  render(React.createElement(GotoStrip));
  assert.match(text(), /not executing slews/);
  assert.match(text(), /33\.7′/);
  assert.match(text(), /unparked/);
});

// -------------------------------------------------------------- GuideQuickBar

await testAsync("the guide settings use the SHARED pickers and PUT the pick", async () => {
  /* 2026-08-07 21:27: one set of camera pickers across every screen that
     shoots a frame — the guide camera gets the same control the Align and
     Focus screens use, with its own exposure range. */
  useStore.setState({
    status: { busy_lanes: [], guider: { connected: true } },
    guide: { guiding: false, rms_ra: 0, rms_dec: 0, rms_total: 0, snr: 0,
             recent: [], phase: "idle" },
    guideCal: null,
  } as any);
  render(React.createElement(GuideQuickBar));
  await act(async () => {});               // flush any pending store work
  const exp = (): any =>
    Array.from(win.document.querySelectorAll("button[aria-haspopup='listbox']"))
      .find((b: any) => (b.getAttribute("aria-label") ?? "").startsWith("EXP"));
  assert.ok(exp(), "no shared exposure picker on the guide bar");
  assert.match(exp().getAttribute("aria-label"), /EXP — 2s$/);
  act(() => exp().click());
  const three = Array.from(win.document.querySelectorAll("[role='option']"))
    .find((o: any) => (o.textContent ?? "").trim() === "3s");
  assert.ok(three, "the guide exposure menu must list its presets");
  act(() => (three as any).click());
  assert.deepEqual(puts.at(-1), {
    // #176: one transport for every scope. `guide` is still its OWN scope —
    // a guide camera's exposure is legitimately not the imaging camera's —
    // it just no longer has its own private cache and its own route shape.
    path: "/api/camera/frame-settings?scope=guide", body: { exposure_s: 3 },
  });
  // The face follows OPTIMISTICALLY off the store, so it is already right
  // before the server answers (and rolls back if the server refuses).
  assert.match(exp().getAttribute("aria-label"), /EXP — 3s$/);
});

await testAsync("the guide camera's offset is reachable at all (#187)", async () => {
  /* It has been applied to every guide exposure since the loop was written —
     `guide/native.py` passes `self._offset` to each expose — while GuideConfig
     had no field for it and the route answered a literal 30. So the
     constructor default was the only value it could ever have: the same shape
     as the constructor-frozen 2.0 s exposure that shipped dead beside it. */
  useStore.setState({
    status: { busy_lanes: [], guider: { connected: true } },
    guide: { guiding: false, rms_ra: 0, rms_dec: 0, rms_total: 0, snr: 0,
             recent: [], phase: "idle" },
    guideCal: null,
  } as any);
  render(React.createElement(GuideQuickBar));
  const offs = (): any =>
    Array.from(win.document.querySelectorAll("button[aria-haspopup='listbox']"))
      .find((b: any) => (b.getAttribute("aria-label") ?? "").startsWith("OFFS"));
  assert.ok(offs(), "the guide bar offers no way to set the offset it applies");
  act(() => offs().click());
  const box: any = win.document.querySelector('input[aria-label="Offset in ADU"]');
  assert.ok(box, "no offset field");
  box.value = "64";
  const setBtn = Array.from(win.document.querySelectorAll("button"))
    .find((b: any) => (b.textContent ?? "").trim() === "Set");
  act(() => (setBtn as any).click());
  assert.deepEqual(puts.at(-1), {
    path: "/api/camera/frame-settings?scope=guide", body: { offset: 64 },
  });
});

await testAsync("the calibration walk gets a step chip, a pulse ring and the plot", async () => {
  useStore.setState({
    status: { busy_lanes: [], guider: { connected: true } },
    guide: { guiding: false, rms_ra: 0, rms_dec: 0, rms_total: 0, snr: 0,
             recent: [], phase: "calibrating" },
    guideCal: { leg: "go_west", dir: "west", ms: 1200, step: 7,
                walk: [[0, 0], [3.2, 0.4], [6.1, 0.9]] },
  } as any);
  render(React.createElement(GuideQuickBar));
  await act(async () => {});
  assert.match(text(), /calibrating · west step 7/);
  const ring = q("[data-activity-ring='fill']");
  assert.ok(ring, "no pulse ring");
  assert.equal(ring.querySelector(".solve-ring-fill").style.animationDuration,
    "1.2s", "the ring must fill on the pulse's own duration");
  assert.ok(q("[data-cal-walk]"), "no walk plot");
  assert.match(text(), /flexure/, "the plot must say how to read itself");
});

await testAsync("guiding shows the RMS in its honest unit", async () => {
  useStore.setState({
    status: { busy_lanes: [], guider: { connected: true } },
    guide: { guiding: true, rms_ra: 0.42, rms_dec: 0.31, rms_total: 0.52,
             snr: 40, recent: [], phase: "guiding", is_arcsec: false },
    guideCal: null,
  } as any);
  render(React.createElement(GuideQuickBar));
  await act(async () => {});
  assert.match(text(), /0\.52px/, "pixels must not be dressed up as arcsec");
  assert.match(text(), /RA 0\.42/);
});

// ----------------------------------------------------------- SequenceRunStrip

test("an idle sequence renders no strip", () => {
  useStore.setState({ sequence: { state: "idle" } } as any);
  render(React.createElement(SequenceRunStrip));
  assert.equal(win.document.getElementById("root").children.length, 0);
});

test("a running sequence shows target, frame ordinal, ETA and the exposure ring", () => {
  const now = Date.now();
  useStore.setState({
    sequence: {
      state: "running", target: "M 31", plan_name: "night1",
      progress: {
        frames_done: 2, frames_total: 24, percent: 9, elapsed_s: 300,
        rejected: 1, eta_s: 5400, eta_confident: true,
        current_exposure_s: 120, frame_started_at_ms: now - 40_000,
        server_now_ms: now,
      },
    },
  } as any);
  render(React.createElement(SequenceRunStrip));
  assert.match(text(), /M 31/);
  assert.match(text(), /frame 3\/24/);
  assert.match(text(), /1 rejected/);
  assert.match(text(), /1\.5h left/);
  assert.match(text(), /9%/);
  const ring = q("[data-activity-ring='fill']");
  assert.ok(ring, "no exposure ring");
  const fill = ring.querySelector(".solve-ring-fill");
  assert.equal(fill.style.animationDuration, "120s");
  assert.ok(fill.style.animationDelay.startsWith("-"),
    "a strip mounted mid-frame must start the fill mid-way, not at zero");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`opStripsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
