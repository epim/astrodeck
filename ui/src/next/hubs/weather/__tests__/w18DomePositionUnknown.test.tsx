// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w18DomePositionUnknown.test.tsx - WP-151 / #791: WEATHER > SKY's pointing
// marker while the mount does not know where it points.
//
//   Run directly:  npx tsx src/next/hubs/weather/__tests__/w18DomePositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// After a power cycle the AM5 reports its HOME position, the pole, wherever the
// tube is (`status.mount.position_known === false`, #144). `DomeScreen` derived
// the dome's `pointing` from `status.mount.alt/az` without reading the flag, so
// the screen drew a scope marker at the pole.
//
// The marker is canvas-drawn and jsdom has no canvas, so this grades what the
// `pointing` prop makes the dome PANEL do that a DOM can see: it asks the server
// how clear the sky is where the scope looks (`GET /api/cloudmap/at`, sent only
// for a pointing above the horizon - SkyDomePanel's own rule) and prints the
// answer ("where you are pointing: ..."). A known position does both, which is
// the control; an unknown one does neither.
//
// Named mutant (screen reads the raw angles again): in DomeScreen.tsx replace
// `const believed = believedPointing(mount);` with
// `const believed = mount ? { alt: mount.alt, az: mount.az } : null;`.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/weather/sky", pretendToBeVisual: true },
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
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "SVGElement", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "PointerEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------ the clock
const T0 = Date.UTC(2026, 8, 11, 4, 0, 0);
const realNow = Date.now;
Date.now = () => T0;

const LAT = 47.61;
const LON = -122.33;

const asked: string[] = [];
function ok(json: any) {
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => json,
    text: async () => JSON.stringify(json),
  };
}

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  asked.push(`${init?.method ?? "GET"} ${u}`);
  if (u.includes("/api/cloudmap/at")) {
    return ok({ enabled: true, ahead_s: 0, probability: 0.12, basis: "mask_only", crossing_km: null, beam_m: 200 });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({
      enabled: true, platform: "G18", observed_at: null, age_s: 90, stale: false,
      last_error: null, motion: null, credit: { source: "NOAA GOES", url: "x" },
      suggested_platform: "G18",
      rows: [new Array(36).fill(0.4)], alt_start: 15, alt_step: 6, az_step: 10,
    });
  }
  if (u.includes("/api/site")) {
    return ok({
      site: { name: "Back lawn", latitude: LAT, longitude: LON, elevation_m: 52, is_default: false, horizon_min_deg: 20, horizon_points: [[0, 8], [359, 8]] },
      version: 3,
    });
  }
  return ok({});
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { DomeScreen } = await import("../dome/DomeScreen");

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
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;

const MOUNT = {
  ra_hours: 9.9, dec_deg: 69, ra_str: "09:54:00", dec_str: "+69:00:00",
  alt: 60, az: 30, tracking: true, parked: false, slewing: false,
};

function seedState(mount: Record<string, unknown>): void {
  useStore.setState({
    principal: {
      role: "admin", name: "tester",
      caps: ["view.status", "view.weather", "view.site_derived", "view.site_precise",
        "control.capture", "config.site_optics"],
    },
    equipConnected: true,
    wsPhase: "up",
    wsConnected: true,
    toasts: [],
    site: { name: "Back lawn", latitude: LAT, longitude: LON, elevation_m: 52, is_default: false, horizon_min_deg: 20 },
    config: { cloudmap: { enabled: true, platform: "auto", poll_minutes: 10, half_px: 120 } },
    weather: {
      enabled: true, fetched_ts: T0 / 1000, stale: false, ignore_tonight: false,
      threshold_pct: 60, sustain_minutes: 30, site_lat: LAT, site_lon: LON,
      forecast: { times: [new Date(T0).toISOString()], cloud: [10], cloud_low: [10], cloud_mid: [0], cloud_high: [0] },
      astrospheric: null, alert: null,
      now: { ts: new Date(T0).toISOString(), temp_c: 11, dewpoint_c: 6, humidity_pct: 70, wind_kmh: 12, wind_dir_deg: 225, gust_kmh: 18, cloud_base_m: 2200 },
    },
    sequence: { target: null },
    plan: { targets: [] },
    status: { connected: {}, looping: false, mount },
  } as never);
}

/** Mount the screen fresh with this mount block, let the panel load, and report
 *  what it asked for and printed. FRESH each time because the panel reads the
 *  pointing when it loads, which is once a minute. */
async function run(mount: Record<string, unknown>): Promise<{ at: string[]; shown: boolean }> {
  act(() => { seedState(mount); });
  asked.length = 0;
  const root = createRoot(container);
  await act(async () => { root.render(createElement(DomeScreen)); });
  await settle();
  assert(byId("wx-sky") != null, "the sky screen did not render - the fixture is wrong, not the component");
  assert(asked.some((a) => /\/api\/cloudmap$/.test(a)),
    "the dome panel never loaded, so it never reached the pointing check");
  const at = asked.filter((a) => a.includes("/api/cloudmap/at"));
  const shown = /where you are pointing/.test(container.textContent as string);
  await act(async () => { root.unmount(); });
  return { at, shown };
}

// --------------------------------------------------------------- the control
{
  const r = await run({ ...MOUNT, position_known: true });
  test("control: a mount that knows where it points makes the dome ask how clear the sky is there", () => {
    assert(r.at.length > 0, "no /api/cloudmap/at request for a known position at alt 60");
  });
  test("control: and the screen prints the answer", () => {
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
  test("position_known false: the screen does not print a 'where you are pointing' answer", () => {
    assert(!r.shown, "the screen describes the sky along a pointing the mount cannot vouch for");
  });
}

Date.now = realNow;

const total = passed + failed;
console.log(`w18DomePositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
