// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w18SkyHubPositionUnknown.test.tsx - WP-151 / #791: the SKY hub's pointing
// marker while the mount does not know where it points.
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/w18SkyHubPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// After a power cycle the AM5 reports its HOME position, the pole, wherever the
// tube is (`status.mount.position_known === false`, #144). The hub derived the
// dome card's `pointing` from `status.mount.alt/az` without reading the flag, so
// the card drew a scope marker at the pole.
//
// The marker itself is canvas-drawn, and jsdom has no canvas, so this grades the
// two things the `pointing` prop makes the dome PANEL do that a DOM can see:
//   * it asks the server how clear the sky is where the scope looks
//     (`GET /api/cloudmap/at`, which the panel sends only for a pointing above
//     the horizon - SkyDomePanel's own rule), and
//   * it prints the answer ("where you are pointing: ...").
// A known position does both - the control, without which "neither" proves
// nothing - and an unknown one does neither.
//
// Named mutant (hub reads the raw angles again): in SkyHub.tsx replace
// `const believed = believedPointing(status?.mount);` with
// `const believed = status?.mount ? { alt: status.mount.alt, az: status.mount.az } : null;`.

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
const asked: string[] = [];
const NOW = Date.UTC(2026, 8, 10, 5, 0, 0);

function ok(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}

const domePayload = {
  enabled: true, observed_at: "2026-09-10T05:00:00Z", stale: false,
  alt_start: 6, alt_step: 6, az_step: 10,
  rows: Array.from({ length: 14 }, () => Array.from({ length: 36 }, () => 0.05)),
};

const g = globalThis as any;
g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  asked.push(`${init?.method ?? "GET"} ${u}`);
  if (u.includes("/api/cloudmap/dome")) return ok(domePayload);
  if (u.includes("/api/cloudmap/at")) {
    return ok({ enabled: true, ahead_s: 0, probability: 0.12, basis: "mask_only", crossing_km: null, beam_m: 200 });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({ enabled: true, platform: "G18", observed_at: null, age_s: null, stale: false, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: false, picks: [] });
  if (u.includes("/api/catalog/region")) return ok({ rows: [], truncated: false, catalog_degraded: false, notes: [] });
  if (u.includes("/api/catalog?q=")) return ok({ rows: [] });
  if (u.includes("/api/site")) {
    return ok({
      site: { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20, horizon_points: [[0, 5], [180, 5], [359, 5]] },
      version: 3,
    });
  }
  return ok({});
};

for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLVideoElement", "Element", "SVGElement", "Node", "Event", "CustomEvent",
  "MouseEvent", "KeyboardEvent", "PointerEvent", "localStorage", "getComputedStyle",
  "matchMedia", "requestAnimationFrame", "cancelAnimationFrame", "WebSocket",
  "location", "history", "ResizeObserver", "HTMLCanvasElement", "HTMLImageElement", "Image",
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
const text = (): string => container.textContent as string;

const CAPS = [
  "view.status", "view.weather", "view.site_derived", "view.site_precise",
  "control.capture", "control.mount",
];

function seedState(mount: Record<string, unknown>): void {
  useStore.setState({
    principal: { role: "operator", caps: CAPS, name: "tester" },
    site: { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20 },
    equipConnected: true,
    wsPhase: "up",
    toasts: [],
    framing: null,
    status: {
      connected: { camera: { connected: true, name: "sim camera" } },
      looping: false,
      mount,
    },
    config: {
      optics: { focal_length_mm: 800, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176, auto_from_camera: false, telescope_name: "" },
      safety: { horizon: null },
      survey: { online_fetch: false },
    },
    weather: {
      enabled: true, fetched_ts: NOW / 1000, stale: false, ignore_tonight: false,
      threshold_pct: 60, sustain_minutes: 30, site_lat: 47.61, site_lon: -122.33,
      forecast: { times: [new Date(NOW).toISOString()], cloud: [8], cloud_low: [8], cloud_mid: [0], cloud_high: [0] },
      astrospheric: null, alert: null,
      now: { ts: new Date(NOW).toISOString(), temp_c: 14, dewpoint_c: 6, humidity_pct: 60, wind_kmh: 12, wind_dir_deg: 225, gust_kmh: 18, cloud_base_m: 2200 },
    },
  } as never);
}

const MOUNT = {
  ra_hours: 3.5, dec_deg: 40, ra_str: "03:30:00", dec_str: "+40:00:00",
  tracking: true, parked: false, slewing: false, alt: 45, az: 120,
};

/** Mount the hub fresh with this mount block, let it load, and report what the
 *  dome panel asked for and printed. A FRESH mount each time because the panel
 *  reads the pointing when it loads, which is once a minute. */
async function run(mount: Record<string, unknown>): Promise<{ at: string[]; shown: boolean }> {
  win.localStorage.setItem("astrodeck-next-sky-mode", "map");
  act(() => { seedState(mount); });
  win.location.hash = "#/sky";
  asked.length = 0;
  const root = createRoot(container);
  await act(async () => { root.render(createElement(SkyHub)); });
  await settle();
  const at = asked.filter((a) => a.includes("/api/cloudmap/at"));
  const shown = /where you are pointing/.test(text());
  const dome = container.querySelector('[data-testid="sky-dome-card"]');
  assert(dome != null, "the hub rendered no dome card - the fixture is wrong, not the component");
  // The panel's `load` asks for the status first, every time (the grid after it
  // is cached across mounts, so it is not a per-mount signal).
  assert(asked.some((a) => /\/api\/cloudmap$/.test(a)),
    "the dome panel never loaded, so it never reached the pointing check");
  await act(async () => { root.unmount(); });
  return { at, shown };
}

// --------------------------------------------------------------- the control
{
  const r = await run({ ...MOUNT, position_known: true });
  test("control: a mount that knows where it points makes the dome ask how clear the sky is there", () => {
    assert(r.at.length > 0, `no /api/cloudmap/at request for a known position at alt 45 - asked: ${JSON.stringify(asked)}`);
  });
  test("control: and the card prints the answer", () => {
    assert(r.shown, "the 'where you are pointing' line is missing for a known position");
  });
}
{
  const r = await run({ ...MOUNT });
  test("control: an ABSENT flag (an engine older than #144) reads as known", () => {
    assert(r.at.length > 0 && r.shown, "an absent flag withheld the pointing");
  });
}

// ------------------------------------------------------------------ the bug
{
  const r = await run({ ...MOUNT, position_known: false });
  test("position_known false: the dome does not ask about the mount's home reading", () => {
    assert(r.at.length === 0,
      `the dome asked the server about the home pointing: ${JSON.stringify(r.at)}`);
  });
  test("position_known false: the card does not print a 'where you are pointing' answer", () => {
    assert(!r.shown, "the card describes the sky along a pointing the mount cannot vouch for");
  });
}

Date.now = realNow;

const total = passed + failed;
console.log(`w18SkyHubPositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
