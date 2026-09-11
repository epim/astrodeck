// skyLockParam.test.tsx - `#/sky?lock=<id>`, the hash the rest of the app aims
// the finder with, MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/skyLockParam.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHY THIS IS A HASH AND NOT A PROP. A sheet is route state, not a child of the
// screen underneath it, so when the suggested-targets sheet's row press (and the
// catalog search's pick) aims the finder, the id can only travel through the
// URL - `sheets/targets.tsx` navigates to `#/sky?lock=<id>` and this hub picks
// it up. Nothing else connects the two, which is exactly why nothing noticed
// when the hub was not reading the param at all: every row press closed the
// sheet and left the finder pointing wherever it already was.
//
// Three properties, and all three have to hold or the deep link is a lie:
//   1. The finder AIMS at the id - the view moves and the target locks.
//   2. The param is then GONE from the hash. Left in place, the next render
//      (the 30 s clock, a lens toggle) re-aims and drags the view back off
//      whatever the user has since panned to.
//   3. An id the ranking does not carry SAYS SO, once, rather than silently
//      doing nothing - and clears itself the same way.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// `SkyHub` reaches `hubs/sky/atlas/AtlasHost.tsx`, which imports `atlas.css`
// (the r7Css rule: every area owns a stylesheet and a module of that area
// imports it). Node has no idea what a `.css` file is, so a synchronous load
// hook answers with an empty module - the same stub `shellDom.test.tsx` and
// every other area's DOM test already use.
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


import type { SkyTarget } from "../finder";

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky?lock=m33", pretendToBeVisual: true },
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

// ------------------------------------------------------------- fetch double
const NOW = Date.UTC(2026, 8, 10, 5, 0, 0);
const LAT = 47.61;
const LON = -122.33;

function ok(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}

// SERVER-SUPPLIED alt/az, so the aim can be checked against an exact readout
// rather than against arithmetic. M31 outranks M33 (higher, same sky), which is
// the point: the deep link has to beat the finder's own opening aim.
const regionRows = [
  {
    id: "m31", label: "M31", kind: "dso", type: "Galaxy",
    ra_hours: 0.7123, dec_deg: 41.269, mag: 3.4, size_arcmin: 190,
    constellation: "And", describe: "Andromeda Galaxy", alias: "NGC 224",
    alt: 58, az: 64,
  },
  {
    id: "m33", label: "M33", kind: "dso", type: "Galaxy",
    ra_hours: 1.5641, dec_deg: 30.66, mag: 5.7, size_arcmin: 178,
    constellation: "Tri", describe: "Triangulum Galaxy", alias: "NGC 598",
    alt: 40, az: 100,
  },
];

const tonightPicks = regionRows.map((r, i) => ({
  id: r.id, name: r.label, type: r.type,
  ra_hours: r.ra_hours, dec_deg: r.dec_deg, mag: r.mag, size_arcmin: r.size_arcmin,
  difficulty: "easy", surface_brightness: 21, difficulty_source: "heuristic",
  max_alt: 60 - i, transit_unix: NOW / 1000 + 7200,
  best_window: null, moon_sep_deg: 80, never_rises_above_limit: false, score: 1 - i * 0.1,
}));

const visibilityNight = {
  date: "2026-09-10",
  transit_unix: NOW / 1000 + 7200, transit_alt: 62, transit_in_daylight: false,
  dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
  darkness_kind: "astronomical", samples: [],
  moon: {
    illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90,
    separation_deg: 80, rise_unix: null, set_unix: null,
  },
  best_window: null, alt_limit_deg: 20, never_rises_above_limit: false,
};

const g = globalThis as any;
g.fetch = async (url: any) => {
  const u = String(url);
  if (u.includes("/api/cloudmap/dome")) {
    return ok({ enabled: false, observed_at: null, stale: true, alt_start: 6, alt_step: 6, az_step: 10, rows: [] });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({ enabled: false, platform: "G18", observed_at: null, age_s: null, stale: true, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: false, picks: tonightPicks });
  if (u.includes("/api/catalog/region")) return ok({ rows: regionRows, truncated: false, catalog_degraded: false, notes: [] });
  if (u.includes("/api/catalog?q=")) return ok({ results: [], notes: [] });
  if (u.includes("/api/visibility")) return ok(visibilityNight);
  if (u.includes("/api/site")) {
    return ok({
      site: {
        name: "Back lawn", latitude: LAT, longitude: LON, elevation_m: 52,
        is_default: false, horizon_min_deg: 20, horizon_points: [[0, 5], [180, 5], [359, 5]],
      },
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
  "location", "history",
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
const { nav, resetRouterCacheForTests } = await import("../../../router");
const { SkyHub, LOCK_WAIT_MS, lockNotListed, entryOf } = await import("../SkyHub");

// ------------------------------------------------------------------ harness
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
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
const q = (sel: string): any => container.querySelector(sel);
const byId = (id: string): any => q(`[data-testid="${id}"]`);

const wait = async (ms: number): Promise<void> => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};

/** Poll rather than sleep: the deep link's own refusal runs on a real 2.5 s
 *  timer and a cold `tsx` start spends most of a second compiling, so a fixed
 *  wait would either be flaky or so padded it measured nothing. */
async function until(what: string, pred: () => boolean, budgetMs = 9000): Promise<void> {
  const t0 = performance.now();
  while (performance.now() - t0 < budgetMs) {
    if (pred()) return;
    await wait(25);
  }
  throw new Error(`timed out after ${budgetMs}ms waiting for ${what}`);
}

function seed(): void {
  act(() => {
    useStore.setState({
      principal: {
        role: "operator",
        caps: ["view.status", "view.weather", "view.site_derived", "view.site_precise", "control.capture"],
        name: "tester",
      },
      site: {
        name: "Back lawn", latitude: LAT, longitude: LON, elevation_m: 52,
        is_default: false, horizon_min_deg: 20,
      },
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      framing: null,
      status: {
        connected: { camera: { connected: true, name: "sim camera" } },
        looping: false,
        optics: {
          have_optics: true, source: "config",
          focal_length_mm: 800, pixel_size_um: 3.76,
          sensor_width_px: 6248, sensor_height_px: 4176,
          image_scale_arcsec_px: 0.97, fov_w_deg: 1.68, fov_h_deg: 1.12, fov_diag_deg: 2.02,
        },
      },
      config: {
        optics: { focal_length_mm: 800, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176, auto_from_camera: false, telescope_name: "" },
        safety: { horizon: null },
        survey: { online_fetch: false },
      },
      weather: {
        enabled: true, fetched_ts: NOW / 1000, stale: false, ignore_tonight: false,
        threshold_pct: 60, sustain_minutes: 30, site_lat: LAT, site_lon: LON,
        forecast: { times: [new Date(NOW).toISOString()], cloud: [8], cloud_low: [8], cloud_mid: [0], cloud_high: [0] },
        astrospheric: null, alert: null,
        now: { ts: new Date(NOW).toISOString(), temp_c: 14, dewpoint_c: 6, humidity_pct: 60, wind_kmh: 8, wind_dir_deg: 225, gust_kmh: 12, cloud_base_m: 2200 },
      },
    } as never);
  });
}

// ======================================================== the deep link aims
seed();
win.location.hash = "#/sky?lock=m33";
resetRouterCacheForTests();
const root = createRoot(container);
await act(async () => { root.render(createElement(SkyHub)); });
await until("the lock card", () => byId("sky-lock-name") != null);
await until("the deep link to be consumed", () => !win.location.hash.includes("lock="));

await testAsync("#/sky?lock=m33 aims the finder at M33, not at the top-ranked M31", async () => {
  const readout = q("[data-sky-readout]")?.textContent ?? "";
  eq(readout, "az 100° · alt 40°", "the finder never moved to M33's position:");
  assert(
    /LOCKED/.test(q("[data-sky-locknote]")?.textContent ?? ""),
    `M33 is not under the reticle: "${q("[data-sky-locknote]")?.textContent}"`,
  );
  eq(byId("sky-lock-name")?.textContent, "M33", "the lock card names the wrong target:");
  // The precondition that makes this test mean something: M31 is the top of
  // the ranking, so it is what the finder aims at on its own. A hub that
  // ignored the param would pass every assertion above by accident if M33
  // happened to be the best target tonight.
  const chips = Array.from(container.querySelectorAll("[data-reach-chip]"))
    .map((el: any) => el.getAttribute("data-reach-chip"));
  eq(chips[0], "m31", "precondition: the top-ranked target the deep link had to beat:");
  assert(chips.includes("m33"), `M33 is not even in reach: [${chips.join(", ")}]`);
});

test("the param is consumed, so a later render cannot re-aim off the user's pan", () => {
  eq(win.location.hash, "#/sky", "the lock param is still in the hash:");
});

// ================================================= an id nobody can aim at
await testAsync("an id the ranking does not carry says so, once, and clears itself", async () => {
  const before = q("[data-sky-readout]")?.textContent ?? "";
  act(() => { useStore.setState({ toasts: [] } as never); });
  // Exactly what the targets sheet does with a row press.
  act(() => { nav.go("/sky?lock=m101"); });
  eq(win.location.hash, "#/sky?lock=m101", "precondition: the deep link is in the hash");

  await until(
    "the refusal",
    () => useStore.getState().toasts.some((t) => (t.title ?? "").includes("m101")),
    LOCK_WAIT_MS + 6000,
  );

  const said = useStore.getState().toasts.filter((t) => (t.title ?? "").includes("m101"));
  eq(said.length, 1, "the refusal was said more than once:");
  eq(said[0].title, lockNotListed("m101"), "the refusal does not name the id the link asked for:");
  eq(win.location.hash, "#/sky", "an unusable lock param was left in the hash:");
  eq(
    q("[data-sky-readout]")?.textContent ?? "",
    before,
    "a lock param nobody could aim at moved the finder anyway:",
  );
});

// ============================================ the extent reaches FRAME's call
test("entryOf hands FRAME the catalogued extent instead of a zero", () => {
  const m33 = {
    id: "m33", name: "M33", full: "Triangulum Galaxy", kind: "galaxy",
    ra_hours: 1.5641, dec_deg: 30.66, altNow: 40, azNow: 100,
    cloudPct: 8, obstructed: false, clouded: false,
    color: "var(--accent)", statusTxt: "CLEAR · 8%", palette: "LRGB",
    sizeArcmin: 178, transitLabel: "—", windowMinutes: 240, score: 0.8, moonSepDeg: 80,
  } as SkyTarget;
  eq(entryOf(m33).size_arcmin, 178, "the catalogue-extent ellipse would be drawn at zero size:");
  eq(
    entryOf({ ...m33, sizeArcmin: undefined }).size_arcmin,
    0,
    "a row that carried no extent must hand over 0, which draws nothing, not a guess:",
  );
});

act(() => { root.unmount(); });
Date.now = realNow;

const total = passed + failed;
console.log(`skyLockParam.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
