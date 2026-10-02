// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w7OpenMeteoSourceLink.test.tsx - #634: Open-Meteo's CC BY 4.0 terms ask for a
// LINK next to any place its data is displayed, not just the name.
//
//   Run directly:  npx tsx src/next/hubs/weather/__tests__/w7OpenMeteoSourceLink.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// ONE TEST PER SURFACE this work package (WP-78) was given write access to,
// both against the rendered DOM rather than the source, because the licence
// condition is about what a reader SEES, not a string existing in a file:
//
//   1. The Sky hub's SKYDOME card (`sky/cards/DomeCard.tsx`). Before this WP it
//      carried a `wind` prop all the way to the ghost-advection maths and wrote
//      NO wind reading anywhere on screen - so there was nowhere on this card
//      for a source link to stand next to. The fix mounts the Weather hub's own
//      `DomeLegend` (one component, one place the link lives - `domeOverlay.tsx`)
//      rather than growing a second wind readout.
//   2. The Weather hub's DomeScreen, which already mounted `DomeLegend` before
//      this WP. The test pins the link REACHING the screen through it, not the
//      legend's own pre-existing existence.
//
// A third surface the issue names - the forecast cloud bands in a `quick.tsx` -
// is NOT fixed here. #634 and the backlog plan's "Owned files" column both say
// `ui/src/next/hubs/session/flows/create/quick.tsx`, but that file renders no
// cloud/forecast/weather data at all (grep confirms it top to bottom - it is
// the target/subs/filters/guide flow-creation sheet). The actual forecast cloud
// bands the issue is describing are `ArcHold[]` drawn by `NightArc` in
// `ui/src/next/hubs/sky/sheets/quick.tsx` ("IMAGE THIS", a different file with
// the same basename one directory over) - outside this WP's edit list, so that
// part is reported in the return rather than fixed under the wrong path.
//
// NAMED MUTANT (one line, recorded here and in the return): delete the `<a>`
// anchor from `DomeLegend`'s wind span in `domeOverlay.tsx`, leaving the
// `<Mono>` wind text with no link beside it. Both tests below fail on
// `legend.querySelector('a[href="https://open-meteo.com/"]')` being null.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body>`
  + `<div id="dome-card-root"></div><div id="dome-screen-root"></div>`
  + `</body></html>`,
  { url: "http://local/#/sky", pretendToBeVisual: true },
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
  "HTMLCanvasElement", "Element", "SVGElement", "Node", "Event", "CustomEvent",
  "MouseEvent", "KeyboardEvent", "PointerEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------ the clock
const NOW = Date.UTC(2026, 8, 20, 4, 0, 0);
const realNow = Date.now;
Date.now = () => NOW;

// ----------------------------------------------------------------- the site
// A made-up site and a made-up name - never the rig's real coordinates.
const LAT = 51.0;
const LON = 4.0;
const SITE_NAME = "Test field";
const HORIZON: [number, number][] = [[0, 10], [180, 10], [359, 10]];
const TARGET = { name: "M57", ra_hours: 18.8853, dec_deg: 33.03 };

function ok(json: unknown) {
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => json,
    text: async () => JSON.stringify(json),
  };
}

g.fetch = async (url: any) => {
  const u = String(url);
  if (u.includes("/api/cloudmap/dome")) {
    return ok({
      enabled: true, observed_at: new Date(NOW).toISOString(), stale: false,
      alt_start: 6, alt_step: 6, az_step: 10, cell_km: [2.0, 2.0],
      rows: Array.from({ length: 14 }, () => Array.from({ length: 36 }, () => 0.05)),
    });
  }
  if (u.includes("/api/cloudmap/at")) {
    return ok({ probability: 0.05, basis: "granule", beam_m: 90 });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({
      enabled: true, platform: "G18", observed_at: new Date(NOW).toISOString(),
      age_s: 60, stale: false, last_error: null, motion: null,
      credit: { source: "NOAA GOES", url: "" },
    });
  }
  if (u.includes("/api/visibility")) {
    return ok({
      date: "2026-09-20",
      transit_unix: NOW / 1000 + 3600, transit_alt: 60, transit_in_daylight: false,
      dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 6 * 3600,
      darkness_kind: "astronomical", samples: [],
      moon: {
        illumination: 0.3, phase_name: "Waxing Crescent", alt: -10, az: 90,
        separation_deg: 60, rise_unix: null, set_unix: null,
      },
      best_window: null, alt_limit_deg: 20, never_rises_above_limit: false,
    });
  }
  if (u.includes("/api/site")) {
    return ok({
      site: {
        name: SITE_NAME, latitude: LAT, longitude: LON, elevation_m: 10,
        is_default: false, horizon_min_deg: 10, horizon_points: HORIZON,
      },
      version: 3,
    });
  }
  return ok({});
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { DomeCard } = await import("../../sky/cards/DomeCard");
const { DomeScreen } = await import("../dome/DomeScreen");
const { windSummary } = await import("../dome/domeOverlay");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const WEATHER_NOW = {
  enabled: true, fetched_ts: NOW / 1000, stale: false, ignore_tonight: false,
  threshold_pct: 60, sustain_minutes: 30, site_lat: LAT, site_lon: LON,
  forecast: {
    times: [new Date(NOW).toISOString()],
    cloud: [20], cloud_low: [20], cloud_mid: [0], cloud_high: [0],
  },
  astrospheric: null, alert: null,
  now: {
    ts: new Date(NOW).toISOString(), temp_c: 10, dewpoint_c: 4, humidity_pct: 65,
    wind_kmh: 14, wind_dir_deg: 250, gust_kmh: 20, cloud_base_m: 2400,
  },
};

useStore.setState({
  principal: {
    role: "operator", name: "tester",
    caps: [
      "view.status", "view.weather", "view.site_derived", "view.site_precise",
      "control.capture", "config.site_optics",
    ],
  },
  equipConnected: true, wsPhase: "up", wsConnected: true, toasts: [],
  framing: null,
  site: {
    name: SITE_NAME, latitude: LAT, longitude: LON, elevation_m: 10,
    is_default: false, horizon_min_deg: 10,
  },
  config: { cloudmap: { enabled: true, platform: "auto", poll_minutes: 10, half_px: 120 } },
  weather: WEATHER_NOW,
  sequence: { target: TARGET.name },
  plan: { targets: [TARGET] },
  status: {
    connected: {
      camera: { connected: true, name: "sim camera" },
      telescope: { connected: true, name: "sim mount" },
    },
    looping: false,
    mount: {
      ra_hours: 9.9, dec_deg: 69, ra_str: "09:54:00", dec_str: "+69:00:00",
      alt: 55, az: 40, tracking: true, parked: false, slewing: false,
    },
  },
} as never);

// ============================================= surface 1: the Sky hub's card
// MUTANT (domeOverlay.tsx, DomeLegend's wind span): delete the `<a>` anchor.
// Observed failure with the mutant applied: "no anchor to open-meteo.com
// beside the Sky hub's wind reading - #634 asks for a link, not just the
// name, next to every place Open-Meteo data is shown".
await testAsync(
  "the Sky hub's SKYDOME card carries the Open-Meteo link beside the wind reading",
  async () => {
    const host = win.document.getElementById("dome-card-root") as any;
    const root = createRoot(host);
    const wind = windSummary(WEATHER_NOW.now as never);
    assert(wind != null, "precondition: the fixture's wind_dir_deg did not resolve a WindSummary");
    await act(async () => {
      root.render(createElement(DomeCard, {
        canViewWeather: true,
        pointing: { alt: 55, az: 40 },
        target: null,
        horizon: HORIZON.map(([az, alt]) => ({ az, alt })),
        wind,
        tracks: [],
        height: 220,
        lockId: null,
        onExplain: () => {},
      } as never));
    });
    await settle();

    const legend = host.querySelector('[data-testid="wx-dome-legend"]');
    assert(legend != null,
      "the card mounted no legend at all - the wind reading has nowhere to carry the link");
    const link = legend.querySelector('a[href="https://open-meteo.com/"]');
    assert(link != null,
      "no anchor to open-meteo.com beside the Sky hub's wind reading - #634 asks for a "
      + "link, not just the name, next to every place Open-Meteo data is shown");
    assert(/open-meteo/i.test(link.textContent ?? ""),
      `the link's own text does not name the source: "${link.textContent}"`);

    await act(async () => { root.unmount(); });
  },
);

// ========================================== surface 2: the Weather hub's screen
// Same mutant, same failing assertion, now through the screen that already
// mounted `DomeLegend` before this WP.
await testAsync(
  "the Weather hub's DomeScreen carries the Open-Meteo link beside the wind reading",
  async () => {
    win.location.hash = "#/weather/sky";
    const host = win.document.getElementById("dome-screen-root") as any;
    const root = createRoot(host);
    await act(async () => { root.render(createElement(DomeScreen)); });
    await settle();

    const legend = host.querySelector('[data-testid="wx-dome-legend"]');
    assert(legend != null, "DomeScreen mounted no legend - precondition for the rest of this test");
    const link = legend.querySelector('a[href="https://open-meteo.com/"]');
    assert(link != null,
      "no anchor to open-meteo.com beside the Weather hub's wind reading - #634 asks for "
      + "a link, not just the name, next to every place Open-Meteo data is shown");

    await act(async () => { root.unmount(); });
  },
);

Date.now = realNow;

const total = passed + failed;
console.log(`w7OpenMeteoSourceLink.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
