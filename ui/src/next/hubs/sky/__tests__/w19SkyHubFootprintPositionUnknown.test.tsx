// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w19SkyHubFootprintPositionUnknown.test.tsx - WP-160 / #913: the SKY hub's
// live-pointing footprint and its caption, in ATLAS and in FRAME, while the
// mount does not know where it points.
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/w19SkyHubFootprintPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Both canvases draw "Pointing now": a footprint at the mount's RA/Dec, a label
// naming it, and, when it is off the map, a sentence under the canvas carrying
// the mount's own RA/Dec text (`pointingWhere`). #791 gated the hub's alt/az
// marker (the dome card) and left these, which took `status.mount` and
// `${ra_str} ${dec_str}` straight off the frame. After a power cycle the AM5
// reports its HOME position, the pole, wherever the tube is
// (`status.mount.position_known === false`, #144), so each claimed the tube was
// at the pole. A known position does all of it (the control); an unknown one
// does none.
//
// WHAT THIS FIXTURE IS NOT. The canvases are mounted but blind (jsdom has no
// WebGL or canvas 2D); the footprint is graded through what it leaves in the
// DOM, the "Pointing now" label and the caption.
//
// Named mutant (the hub passes the raw mount and text again): in SkyHub.tsx
// replace `const footprintMount = raDec ? status?.mount ?? null : null;` with
// `const footprintMount = status?.mount ?? null;` and
// `const footprintWhere = raDec ? ...` with
// ``const footprintWhere = status?.mount ? `${status.mount.ra_str} ${status.mount.dec_str}` : null;``.

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

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const byId = (id: string): any => container.querySelector(`[data-testid="${id}"]`);
const text = (): string => container.textContent as string;

// ------------------------------------------------------------------ fixtures
const OPTICS = {
  have_optics: true, source: "config",
  focal_length_mm: 800, pixel_size_um: 3.76,
  sensor_width_px: 6248, sensor_height_px: 4176,
  image_scale_arcsec_px: 0.97, fov_w_deg: 1.68, fov_h_deg: 1.12, fov_diag_deg: 2.02,
};
// M31, the framing the canvas is centred on. The mount either reports the same
// place (the footprint lands in the middle of the map) or a far one (the
// footprint is off the map and the caption states where it is).
const NEAR = { ra_hours: 0.7123, dec_deg: 41.269, ra_str: "00h 42m", dec_str: "+41d 16m" };
const FAR = { ra_hours: 5.5, dec_deg: -5.4, ra_str: "05h 30m", dec_str: "-05d 24m" };

function seedStore(mount: Record<string, unknown>): void {
  act(() => {
    useStore.setState({
      principal: {
        role: "operator", name: "tester",
        caps: ["view.status", "view.weather", "view.site_derived", "view.site_precise", "control.capture", "control.mount"],
      },
      site: { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20 },
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      framing: null,
      night: false,
      status: {
        connected: { camera: { connected: true, name: "sim camera" } },
        looping: false,
        optics: OPTICS,
        mount: { tracking: true, parked: false, slewing: false, alt: 40, az: 180, ...mount },
      },
      config: {
        optics: { focal_length_mm: 800, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176, auto_from_camera: false, telescope_name: "" },
        optics_computed: OPTICS,
        safety: { horizon: null },
        survey: { online_fetch: false },
        rotator: { range_type: "full", range_start_deg: 0, tolerance_deg: 1 },
      },
      weather: null,
    } as never);
  });
}

/** Mount the hub fresh in `mode`, centred on M31, with this mount block. */
async function run(mode: "atlas" | "frame", mount: Record<string, unknown>): Promise<{ text: string; hostId: string; present: boolean }> {
  win.localStorage.clear();
  // ATLAS wins over FRAME while it is on, so FRAME is entered from MAP, as a
  // phone that chose MAP does.
  if (mode === "frame") win.localStorage.setItem("astrodeck-next-sky-mode", "map");
  seedStore(mount);
  act(() => {
    useStore.getState().openFraming({
      id: "m31", name: "M31", type: "Galaxy",
      ra_hours: NEAR.ra_hours, dec_deg: NEAR.dec_deg, mag: 3.4, size_arcmin: 190,
    } as never);
  });
  // ATLAS is the hub's default mode; FRAME is entered by the deep link the Rig
  // hub's FRAME button uses, on the framing the store already holds.
  win.location.hash = mode === "frame" ? "#/sky?frame=1" : "#/sky";
  resetRouterCacheForTests();
  const root = createRoot(container);
  await act(async () => { root.render(createElement(SkyHub)); });
  await settle();
  const hostId = mode === "frame" ? "sky-frame-host" : "atlas-canvas";
  const out = { text: text(), hostId, present: byId(hostId) != null };
  await act(async () => { root.unmount(); });
  return out;
}

// --------------------------------------------------------------- the control
for (const mode of ["atlas", "frame"] as const) {
  {
    const r = await run(mode, { ...NEAR, position_known: true });
    test(`control (${mode}): the canvas is on screen`, () => {
      assert(r.present, `no ${r.hostId} - the fixture never reached ${mode} mode`);
    });
    test(`control (${mode}): a mount that knows where it points draws the 'Pointing now' label`, () => {
      assert(/Pointing now/.test(r.text), "no live-pointing label for a known position");
    });
  }
  {
    const r = await run(mode, { ...FAR, position_known: true });
    test(`control (${mode}): a known position off the map says where the scope is, in the mount's own words`, () => {
      assert(r.present, `no ${r.hostId}`);
      assert(r.text.includes(`${FAR.ra_str} ${FAR.dec_str}`),
        `the off-map sentence does not carry the mount's position: ${r.text.slice(0, 300)}`);
      assert(/Scope is pointing/.test(r.text), "no off-map sentence for a known position");
    });
  }
  {
    const r = await run(mode, { ...NEAR });
    test(`control (${mode}): an ABSENT flag (an engine older than #144) reads as known`, () => {
      assert(/Pointing now/.test(r.text), "an absent flag withheld the footprint");
    });
  }

  // ---------------------------------------------------------------- the bug
  {
    const r = await run(mode, { ...NEAR, position_known: false });
    test(`position_known false (${mode}): the footprint's 'Pointing now' label is not drawn`, () => {
      assert(r.present, `no ${r.hostId}`);
      assert(!/Pointing now/.test(r.text), "the canvas drew the scope's footprint at the mount's home reading");
    });
  }
  {
    const r = await run(mode, { ...FAR, position_known: false });
    test(`position_known false (${mode}): no sentence carries the mount's home position`, () => {
      assert(r.present, `no ${r.hostId}`);
      assert(!r.text.includes(FAR.ra_str) && !r.text.includes(FAR.dec_str),
        `the mount's home reading is printed on the page as the tube's: ${r.text.slice(0, 300)}`);
      assert(!/Scope is pointing/.test(r.text),
        "the canvas describes how far the scope is from this view, from a position the mount cannot vouch for");
    });
  }
}

Date.now = realNow;

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w19SkyHubFootprintPositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
