// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w20SkyHubRecentrePositionUnknown.test.tsx - WP-173 / #928: the SKY hub's
// RECENTRE in a free-roam FRAME session, while the mount does not know where it
// points.
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/w20SkyHubRecentrePositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// A free-roam session has no object, so RECENTRE goes "back to where the mount
// points" (the button's own sub-label). A mount that does not know where it
// points (`position_known === false`, #144) reports its HOME position, the pole,
// wherever the tube is, so recentring there parked the view on the pole labelled
// as the scope. It now refuses out loud, with the position-unknown reason, and
// the view does not move. A known position still recentres (the control), and a
// frame with no mount block at all keeps its own sentence.
//
// Named mutant (the recentre reads the raw reading again): in SkyHub.tsx
// `recentre`, replace `const here = believedRaDec(m);` with `const here = m;`.

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = function () { return null; };

// ------------------------------------------------------------- fetch double
const NOW = Date.UTC(2026, 8, 10, 5, 0, 0);
function ok(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}
const visibilityNight = {
  date: "2026-09-10",
  transit_unix: NOW / 1000 + 7200, transit_alt: 62, transit_in_daylight: false,
  dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
  darkness_kind: "astronomical", samples: [],
  moon: { illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90, separation_deg: 80, rise_unix: null, set_unix: null },
  best_window: null, alt_limit_deg: 20, never_rises_above_limit: false,
};
const g = globalThis as any;
g.fetch = async (url: any) => {
  const u = String(url);
  if (u.includes("/api/survey/pack")) return ok({ present: false, slug: "p", survey: "s", order: null, bytes: null, tile_count: null, fetched_at: null, fetching: null });
  if (u.includes("/api/cloudmap/dome")) {
    return ok({ enabled: true, observed_at: null, stale: false, alt_start: 6, alt_step: 6, az_step: 10, rows: [] });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({ enabled: false, platform: "G18", observed_at: null, age_s: null, stale: false, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: false, picks: [] });
  if (u.includes("/api/catalog/region")) return ok({ rows: [], truncated: false, catalog_degraded: false, notes: [] });
  if (u.includes("/api/catalog?q=")) return ok({ results: [], rows: [], notes: [] });
  if (u.includes("/api/visibility")) return ok(visibilityNight);
  if (u.includes("/api/site")) {
    return ok({
      site: { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20 },
      version: 3,
    });
  }
  return ok({});
};

for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLVideoElement", "HTMLCanvasElement", "HTMLImageElement", "Image",
  "Element", "SVGElement", "Node", "Event", "CustomEvent",
  "MouseEvent", "KeyboardEvent", "PointerEvent", "localStorage", "getComputedStyle",
  "matchMedia", "requestAnimationFrame", "cancelAnimationFrame", "WebSocket",
  "location", "history", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const realNow = Date.now;
Date.now = () => NOW;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { resetRouterCacheForTests } = await import("../../../router");
const { SkyHub } = await import("../SkyHub");

const { POSITION_UNKNOWN_COPY_DETAIL } = await import("../../../../lib/slewController");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function near(a: number | undefined, b: number, msg: string): void {
  if (typeof a !== "number" || Math.abs(a - b) > 1e-6) {
    throw new Error(`${msg} (expected ${b}, got ${String(a)})`);
  }
}

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const byId = (id: string): any => container.querySelector(`[data-testid="${id}"]`);
const toasts = (): any[] => (useStore.getState() as any).toasts;

// ------------------------------------------------------------------ fixtures
const OPTICS = {
  have_optics: true, source: "config",
  focal_length_mm: 800, pixel_size_um: 3.76,
  sensor_width_px: 6248, sensor_height_px: 4176,
  image_scale_arcsec_px: 0.97, fov_w_deg: 1.68, fov_h_deg: 1.12, fov_diag_deg: 2.02,
};
// The mount's own reading, and a free-roam centre far from it, so a move is
// unmistakable.
const MOUNT = { ra_hours: 5.5, dec_deg: -5.4, ra_str: "05h 30m", dec_str: "-05d 24m" };
const FREE_ROAM = { ra_hours: 12.5, dec_deg: 70 };

function seedStore(mount: Record<string, unknown> | null): void {
  act(() => {
    useStore.setState({
      principal: {
        role: "operator", name: "tester",
        caps: ["view.status", "view.weather", "view.site_derived", "view.site_precise", "control.capture", "control.mount"],
      },
      site: { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20 },
      equipConnected: true,
      wsPhase: "up",
      authGate: "open",
      toasts: [],
      night: false,
      status: {
        connected: { camera: { connected: true, name: "sim camera" } },
        looping: false,
        optics: OPTICS,
        ...(mount === null ? {} : {
          mount: { tracking: true, parked: false, slewing: false, alt: 40, az: 180, ...mount },
        }),
      },
      config: {
        optics: { focal_length_mm: 800, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176, auto_from_camera: false, telescope_name: "" },
        optics_computed: OPTICS,
        safety: { horizon: null },
        survey: { online_fetch: false },
        rotator: { range_type: "full", range_start_deg: 0, tolerance_deg: 1 },
      },
      weather: null,
      // A free-roam session: no `target`, so RECENTRE goes to the mount.
      framing: {
        target: undefined,
        center: { ...FREE_ROAM },
        rotation_deg: 0,
        survey: "CDS/P/DSS2/color",
        stretch: "linear",
        fovZoomDeg: 2,
        mosaic: { rows: 1, cols: 1, overlap: 0.25 },
        panels: [],
        freeroamId: "Sky 12.50h +70.0\u00b0",
      },
    } as never);
  });
}

/** Mount the hub in FRAME on the free-roam session, press RECENTRE, and report
 *  where the view ended up and what was said. */
async function pressRecentre(mount: Record<string, unknown> | null): Promise<{
  pressed: boolean; ra: number | undefined; dec: number | undefined; toasts: any[];
}> {
  win.localStorage.clear();
  // ATLAS wins over FRAME while it is on, so FRAME is entered from MAP.
  win.localStorage.setItem("astrodeck-next-sky-mode", "map");
  seedStore(mount);
  win.location.hash = "#/sky?frame=1";
  resetRouterCacheForTests();
  const root = createRoot(container);
  await act(async () => { root.render(createElement(SkyHub)); });
  await settle();
  const btn = byId("sky-recentre");
  let pressed = false;
  if (btn) {
    pressed = true;
    await act(async () => { btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
    await settle();
  }
  const c = (useStore.getState() as any).framing?.center;
  const out = { pressed, ra: c?.ra_hours as number | undefined, dec: c?.dec_deg as number | undefined, toasts: toasts() };
  await act(async () => { root.unmount(); });
  return out;
}

// --------------------------------------------------------------- the control
{
  const r = await pressRecentre({ ...MOUNT, position_known: true });
  test("control: a free-roam FRAME session has a RECENTRE button", () => {
    assert(r.pressed, "no sky-recentre control - the fixture never reached FRAME");
  });
  test("control: a mount that knows where it points recentres the view on it, silently", () => {
    near(r.ra, MOUNT.ra_hours, "the recentred RA");
    near(r.dec, MOUNT.dec_deg, "the recentred Dec");
    assert(r.toasts.length === 0, `a toast was raised for a good recentre: ${JSON.stringify(r.toasts)}`);
  });
}
{
  const r = await pressRecentre({ ...MOUNT });
  test("control: an ABSENT flag (an engine older than #144) reads as known", () => {
    near(r.ra, MOUNT.ra_hours, "the recentred RA with no position_known key");
  });
}
{
  const r = await pressRecentre(null);
  test("control: no mount block keeps its own sentence and does not move the view", () => {
    assert(r.pressed, "no sky-recentre control");
    near(r.ra, FREE_ROAM.ra_hours, "the view's RA");
    assert(r.toasts.length === 1 && /has not reported a position/.test(r.toasts[0].title),
      `the no-mount toast: ${JSON.stringify(r.toasts)}`);
  });
}

// ------------------------------------------------------------------ the bug
{
  const r = await pressRecentre({ ...MOUNT, position_known: false });
  test("position_known false: RECENTRE is still on the page", () => {
    assert(r.pressed, "no sky-recentre control - the fixture never reached FRAME");
  });
  test("position_known false: RECENTRE does not move the view to the mount's home reading", () => {
    near(r.ra, FREE_ROAM.ra_hours, "the view's RA after the press");
    near(r.dec, FREE_ROAM.dec_deg, "the view's Dec after the press");
  });
  test("position_known false: RECENTRE says why, with the position-unknown reason", () => {
    assert(r.toasts.length === 1, `expected one toast, got ${JSON.stringify(r.toasts)}`);
    const t = r.toasts[0];
    assert(/Nothing to recentre on/.test(t.title), `the toast title: ${t.title}`);
    assert(/does not know where it points/.test(t.title), `the toast gives no reason: ${t.title}`);
    assert(t.detail === POSITION_UNKNOWN_COPY_DETAIL, `the toast detail: ${t.detail}`);
    assert(!/has not reported a position/.test(t.title),
      "the toast blames a missing report, when the mount reported its home position");
  });
}

// ------------------------------------------------- the ATLAS cold-start door
// ATLAS always has a session to draw: with none in the store the hub calls
// `store.openFraming()` itself, which seeds the centre from the mount (#928).
/** Open the hub in ATLAS (its default mode) with NO session in the store. */
async function coldAtlas(mount: Record<string, unknown>): Promise<{ ra: number | undefined; dec: number | undefined; id: string | undefined }> {
  win.localStorage.clear();
  seedStore(mount);
  act(() => { useStore.setState({ framing: null } as never); });
  win.location.hash = "#/sky";
  resetRouterCacheForTests();
  const root = createRoot(container);
  await act(async () => { root.render(createElement(SkyHub)); });
  await settle();
  const f = (useStore.getState() as any).framing;
  await act(async () => { root.unmount(); });
  return { ra: f?.center?.ra_hours, dec: f?.center?.dec_deg, id: f?.freeroamId };
}
{
  const r = await coldAtlas({ ...MOUNT, position_known: true });
  test("control: ATLAS opened with no session seeds its view on a known mount position", () => {
    near(r.ra, MOUNT.ra_hours, "the seeded RA");
    near(r.dec, MOUNT.dec_deg, "the seeded Dec");
  });
}
{
  const r = await coldAtlas({ ...MOUNT, position_known: false });
  test("position_known false: ATLAS opened with no session is not seeded on the home reading", () => {
    assert(r.ra !== undefined, "the hub opened no session at all - the fixture never reached ATLAS");
    assert(r.ra !== MOUNT.ra_hours && r.dec !== MOUNT.dec_deg,
      `the atlas opened a free-roam view on the mount's home reading (${r.ra}h ${r.dec})`);
    assert(!/5\.50h/.test(r.id ?? ""), `the session is named after the home reading: ${r.id}`);
  });
}

Date.now = realNow;

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w20SkyHubRecentrePositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
