// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w7QuickArcOpenMeteoSource.test.tsx - #634's third surface (W7 follow-on to
// WP-78): the IMAGE THIS sheet's NIGHT ARC draws its amber hold bands from
// weather.forecast.cloud and threshold_pct - Open-Meteo's own forecast - and
// until this fix carried no link beside that reading anywhere on the sheet.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/w7QuickArcOpenMeteoSource.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// w7OpenMeteoSourceLink.test.tsx (the Weather hub) named this exact gap and
// deliberately left it unfixed, out of WP-78's edit list
// (`ui/src/next/hubs/sky/sheets/quick.tsx`, NOT
// `ui/src/next/hubs/session/flows/create/quick.tsx`, which carries no
// weather data at all - confirmed again here by this file living next to
// the one that does).
//
// NAMED MUTANT, run from a byte backup of quick.tsx and restored
// byte-identical afterwards (sha256 checked). The observed failure is
// quoted at the test it turns red.
//   M1 "the link removed" (the `<a href="https://open-meteo.com/">` anchor
//      deleted from the NIGHT ARC's forecast-source row, leaving the
//      "Cloud forecast:" text with nothing beside it)

/* eslint-disable @typescript-eslint/no-explicit-any */

// quick.tsx pulls in session/flows/create modules that import create.css -
// Node has no idea what a `.css` file is, so a load hook answers with an
// empty module, the same stub w7PlanResumeTitle.test.tsx and
// sessionSheets.test.tsx use.
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
  { url: "http://local/#/sky/quick?target=m31", pretendToBeVisual: true },
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

win.localStorage.setItem("astrodeck-next-sky-quick", JSON.stringify({ hours: 6 }));

// ------------------------------------------------------------- the clock
const NOW = Date.UTC(2026, 8, 20, 4, 0, 0);
const realNow = Date.now;
Date.now = () => NOW;

// -------------------------------------------------------------- fetch stub
const M31 = {
  id: "m31", name: "Andromeda Galaxy", type: "Galaxy", kind: "dso",
  ra_hours: 0.712305, dec_deg: 41.26917, mag: 3.4, size_arcmin: 190,
  difficulty: "easy",
};

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
  if (u.includes("/api/catalog?q=")) return ok({ results: [M31], notes: [] });
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
  return ok({});
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { QuickSessionSheet } = await import("../quick");

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

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.mount", "control.guide"],
};

/** A forecast carrying real cloud cover, so the NIGHT ARC draws at least one
 *  amber hold band from it - not the real site, a made-up one. */
const WEATHER_WITH_FORECAST = {
  enabled: true, fetched_ts: NOW / 1000, stale: false, ignore_tonight: false,
  threshold_pct: 50, sustain_minutes: 30, site_lat: 47.6, site_lon: -122.3,
  forecast: {
    times: Array.from({ length: 8 }, (_, i) => new Date(NOW + i * 3600_000).toISOString()),
    cloud: [10, 20, 80, 90, 85, 30, 10, 5],
    cloud_low: [0, 0, 0, 0, 0, 0, 0, 0],
    cloud_mid: [0, 0, 0, 0, 0, 0, 0, 0],
    cloud_high: [0, 0, 0, 0, 0, 0, 0, 0],
  },
  astrospheric: null, alert: null,
  now: {
    ts: new Date(NOW).toISOString(), temp_c: 10, dewpoint_c: 4, humidity_pct: 65,
    wind_kmh: 14, wind_dir_deg: 250, gust_kmh: 20, cloud_base_m: 2400,
  },
};

function seed(weather: unknown): void {
  act(() => {
    useStore.setState({
      principal: OPERATOR,
      equipConnected: true,
      wsPhase: "up",
      site: {
        name: "Back lawn", latitude: 47.6, longitude: -122.3, elevation_m: 50,
        is_default: false, horizon_min_deg: 30,
      },
      status: {
        connected: { camera: { connected: true, name: "sim" }, telescope: { connected: true, name: "sim" } },
        filterwheel: {
          position: 0, names: ["L", "R", "G", "B"],
          opaque: [false, false, false, false], narrowband: [false, false, false, false],
          exposures: [60, 60, 60, 60],
        },
      },
      config: {
        optics: { focal_length_mm: 1000, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176 },
      },
      weather,
      framing: null,
      frameSettings: {
        capture: { exposure_s: 60, gain: 100, offset: 30, binning: 1, filter: null },
        focus: { exposure_s: 2, gain: 200, offset: 30, binning: 1, filter: null },
        solve: { exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null },
        guide: { exposure_s: 2, gain: 100, offset: 30, binning: 1, filter: null },
      },
    } as never);
  });
}

const container = win.document.getElementById("root") as any;

await testAsync(
  "the NIGHT ARC carries the Open-Meteo link beside its forecast-cloud hold bands",
  async () => {
    seed(WEATHER_WITH_FORECAST);
    const root = createRoot(container);
    await act(async () => {
      root.render(createElement(QuickSessionSheet, { params: { target: "m31" }, depth: 0 as const }));
    });
    await settle();

    const arc = container.querySelector('[data-testid="quick-arc"]');
    assert(arc != null, "the NIGHT ARC never rendered - the fixture is wrong, not the link");
    const link = container.querySelector('[data-testid="quick-arc-weather-source"]');
    assert(link != null,
      "no Open-Meteo link beside the NIGHT ARC's forecast-cloud reading - #634 asks for a "
      + "link, not just the name, next to every place Open-Meteo data is shown");
    assert(link.getAttribute("href") === "https://open-meteo.com/",
      `the link does not point at Open-Meteo: "${link.getAttribute("href")}"`);
    assert(/open-meteo/i.test(link.textContent ?? ""),
      `the link's own text does not name the source: "${link.textContent}"`);

    await act(async () => { root.unmount(); });
  },
);

await testAsync(
  "no weather session at all draws no forecast-source link (nothing to credit)",
  async () => {
    seed(null);
    const root = createRoot(container);
    await act(async () => {
      root.render(createElement(QuickSessionSheet, { params: { target: "m31" }, depth: 0 as const }));
    });
    await settle();

    const link = container.querySelector('[data-testid="quick-arc-weather-source"]');
    assert(link == null,
      `a forecast-source link appeared with no weather session at all: "${link?.outerHTML}"`);

    await act(async () => { root.unmount(); });
  },
);

Date.now = realNow;

const total = passed + failed;
console.log(`w7QuickArcOpenMeteoSource: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
