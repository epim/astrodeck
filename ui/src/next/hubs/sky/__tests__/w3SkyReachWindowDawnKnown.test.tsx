// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3SkyReachWindowDawnKnown.test.tsx - the reach strip's window with no dawn
// known yet, MOUNTED (#551, backlog ruling WP-24a (b), owner-approved
// 2026-09-30).
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/w3SkyReachWindowDawnKnown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `finder/model.ts`'s `ranked` memo walked every placed row's
// window off `trackCtx.hoursToDawn` whether or not a night was actually known:
// with `/api/visibility` not yet answered or failed, `hoursToDawn` reads 0,
// `walkTrack` returns no samples, and `minutesAboveFloor` folds that into a
// real-looking "0m" - "never clears the floor tonight" - for a row that is
// rising and has not been walked at all. `SkyModel.place` (the held lock card)
// already told the two apart with `dawnKnown` (H4-USKY); this is the same
// silence arriving at the reach strip instead.
//
// THE FIX. The `ranked` memo's `winMin` is now `haveCoords && dawnKnown ? ... :
// null`, so `windowLabel` prints "-" for a row nobody has walked yet, and the
// real walked figure once a night is known - the same contract `place` and
// `windowLabel` (#508) already keep.
//
// MUTATION RECORD: see the block at the foot of this file.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
Object.defineProperty(win, "isSecureContext", { value: false, configurable: true });
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.scrollBy = function () { /* jsdom has none */ };
win.HTMLCanvasElement.prototype.getContext = function () { return null; };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

// ------------------------------------------------------------------- clocks
// M31 is up (well clear of a 15 degree floor) at this fixture's made-up site
// and instant - the same pairing `defaultSiteFrame.test.tsx` proves with
// `altAzOf` before relying on it, re-proved below rather than trusted.
const NOW = Date.UTC(2026, 8, 10, 22, 0, 0);
const realNow = Date.now;
Date.now = () => NOW;

// ------------------------------------------------------------- fetch double
function ok(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}
function fail(status: number) {
  return { ok: false, status, statusText: "Internal Server Error", json: async () => ({ detail: "Internal Server Error" }) };
}

/** A made-up fixture, never the real observing site: 0 N 0 E, saved (not
 *  default), so `haveCoords` is true and the only variable left is whether a
 *  night is known. */
const SAVED_SITE = { name: "Fixture site", latitude: 0, longitude: 0, elevation_m: 0, is_default: false, horizon_min_deg: 15 };

const M31 = { ra_hours: 0.7123, dec_deg: 41.269 };
const tonightPicks = [{
  id: "m31", name: "M31", type: "Galaxy",
  ra_hours: M31.ra_hours, dec_deg: M31.dec_deg, mag: 3.4, size_arcmin: 190,
  difficulty: "easy", surface_brightness: 21, difficulty_source: "heuristic",
  max_alt: 60, transit_unix: NOW / 1000 + 7200,
  best_window: null, moon_sep_deg: 80, never_rises_above_limit: false, score: 1,
}];

/** Flipped per section to choose whether `/api/visibility` ever answers. */
let nightAnswers = false;

const g = globalThis as any;
g.fetch = async (url: any) => {
  const u = String(url);
  if (u.includes("/api/survey/pack")) {
    return ok({ present: false, slug: "p", survey: "s", order: null, bytes: null, tile_count: null, fetched_at: null, fetching: null });
  }
  if (u.includes("/api/cloudmap/dome")) {
    return ok({ enabled: false, observed_at: null, stale: true, alt_start: 6, alt_step: 6, az_step: 10, rows: [] });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({ enabled: false, platform: "G18", observed_at: null, age_s: null, stale: true, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: false, picks: tonightPicks });
  if (u.includes("/api/visibility")) {
    if (!nightAnswers) return fail(500);
    return ok({
      date: "2026-09-10", transit_unix: NOW / 1000 + 3600, transit_alt: 48, transit_in_daylight: false,
      dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
      darkness_kind: "astronomical", samples: [],
      moon: { illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90, separation_deg: 80, rise_unix: null, set_unix: null },
      best_window: null, alt_limit_deg: 15, never_rises_above_limit: false,
    });
  }
  if (u.includes("/api/catalog/region")) return ok({ rows: [], truncated: false, catalog_degraded: false, notes: [] });
  if (u.includes("/api/catalog?q=")) return ok({ results: [], notes: [] });
  if (u.includes("/api/site")) return ok({ site: SAVED_SITE, version: 3 });
  return ok({});
};

for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLVideoElement", "HTMLCanvasElement", "HTMLImageElement", "Image",
  "Element", "SVGElement", "Node", "Event", "CustomEvent",
  "MouseEvent", "KeyboardEvent", "PointerEvent", "WheelEvent", "localStorage", "getComputedStyle",
  "matchMedia", "requestAnimationFrame", "cancelAnimationFrame", "WebSocket",
  "location", "history", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { SkyHub } = await import("../SkyHub");
const { altAzOf, lstHours } = await import("../../../../lib/altaz");
const { D2R, FLOOR_DEG, minutesAboveFloor, walkTrack, windowLabel } = await import("../finder");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
const settle = async (): Promise<void> => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};
const chipText = (id: string): string | null =>
  container.querySelector(`[data-reach-chip="${id}"]`)?.textContent ?? null;

const OPTICS = {
  have_optics: true, source: "config",
  focal_length_mm: 800, pixel_size_um: 3.76,
  sensor_width_px: 6248, sensor_height_px: 4176,
  image_scale_arcsec_px: 0.97, fov_w_deg: 1.68, fov_h_deg: 1.12, fov_diag_deg: 2.02,
};

/** The walk the MODEL ought to produce once a dawn is known: 0 N 0 E, dawn
 *  five hours off (the double's own `dark_end_unix`), the 15 degree floor,
 *  no horizon line, no forecast - the app's own `walkTrack`, not a restated
 *  number, so this pins the real contract rather than a guess at it. */
function modelWindow(): number {
  const lst = lstHours(0, NOW / 1000);
  return minutesAboveFloor(walkTrack(M31.dec_deg * D2R, (lst - M31.ra_hours) * 15 * D2R, {
    latDeg: 0, hoursToDawn: 5, horizon: [], horizonMinDeg: 15, maskOn: true, holdAt: () => false,
  }));
}

async function mount(): Promise<{ unmount: () => void }> {
  act(() => {
    useStore.setState({
      principal: {
        role: "operator", name: "tester",
        caps: ["view.status", "view.weather", "view.site_derived", "view.site_precise", "control.capture"],
      },
      site: SAVED_SITE,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      framing: null,
      status: { connected: { camera: { connected: true, name: "sim camera" } }, looping: false, optics: OPTICS },
      config: {
        optics: { focal_length_mm: 800, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176, auto_from_camera: false, telescope_name: "" },
        optics_computed: OPTICS,
        safety: { horizon: null },
        survey: { online_fetch: false },
      },
      weather: null,
    } as never);
  });
  win.localStorage.setItem("astrodeck-next-sky-mode", "map");
  win.location.hash = "#/sky";
  const root = createRoot(container);
  await act(async () => { root.render(createElement(SkyHub)); });
  await settle();
  await settle();
  return { unmount: () => act(() => { root.unmount(); }) };
}

await testAsync("premise: M31 clears the 15 degree floor at the fixture site, now and for the next five hours", () => {
  const now = altAzOf(M31.ra_hours, M31.dec_deg, 0, 0, NOW / 1000).altDeg;
  assert(now > FLOOR_DEG, `M31 is at ${now.toFixed(1)} deg now, not above the ${FLOOR_DEG} degree floor`);
  let min = 90;
  for (let m = 0; m <= 300; m += 10) {
    min = Math.min(min, altAzOf(M31.ra_hours, M31.dec_deg, 0, 0, NOW / 1000 + m * 60).altDeg);
  }
  assert(min > FLOOR_DEG, `M31 dips to ${min.toFixed(1)} deg within five hours, not clear of the floor the whole time`);
});

// ================================================== 1. no dawn known yet
{
  nightAnswers = false;
  const m = await mount();

  await testAsync("no dawn known: the reach chip does not read a 0m walked to a dawn nobody knows", () => {
    const text = chipText("m31");
    assert(text != null, "M31 never reached the reach strip - the fixture is wrong, not the component");
    assert(!/0m/.test(text as string), `the reach chip reads a computed-looking zero window with no night known: "${text}"`);
    assert((text as string).includes(windowLabel(null)), `the reach chip does not print the absent window: "${text}"`);
  });

  m.unmount();
}

// ======================================= 2. control: the night does answer
{
  nightAnswers = true;
  const m = await mount();

  await testAsync("control: once a dawn is known, the reach chip carries the walked window", () => {
    const want = modelWindow();
    assert(want > 0, `premise: the model's own walk gives M31 ${want} minutes, not a window`);
    const text = chipText("m31");
    assert(text != null, "M31 never reached the reach strip - the fixture is wrong, not the component");
    assert((text as string).includes(windowLabel(want)), `the reach chip does not carry the walked window: "${text}", want "${windowLabel(want)}"`);
  });

  m.unmount();
  nightAnswers = false;
}

Date.now = realNow;

// MUTATION RECORD, 2026-09-30 (WP-24a), in a private scratch copy of ui/
// (never the shared tree, #254), from a byte backup restored with its sha256
// checked. Output verbatim.
//
//   MUTANT "ranked walks with no dawn" (finder/model.ts: the `ranked` memo's
//   `winMin` condition `haveCoords && dawnKnown` written back to `haveCoords`,
//   the code as it was before this fix). Observed
//   ("w3SkyReachWindowDawnKnown.test: 2/3 passed"):
//     x no dawn known: the reach chip does not read a 0m walked to a dawn
//     nobody knows: the reach chip reads a computed-looking zero window with
//     no night known: "M3128° · 0m"
//
//   MUTANT (control) "ranked never walks" (`winMin` forced to `null`
//   unconditionally, which would pass the case above by printing "-" for
//   every row, even once a night is known). Observed ("2/3 passed"):
//     x control: once a dawn is known, the reach chip carries the walked
//     window: the reach chip does not carry the walked window: "M3128° · -",
//     want "5h 15m"

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w3SkyReachWindowDawnKnown.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
