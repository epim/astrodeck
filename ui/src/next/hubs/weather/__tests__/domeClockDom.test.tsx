// domeClockDom.test.tsx - WEATHER > SKY keeps ticking, and so do the two marks
// drawn on top of the dome.
//
//   Run directly:  npx tsx src/next/hubs/weather/__tests__/domeClockDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT WENT WRONG, and why a mounted test is the only thing that catches it.
// `DomeScreen`'s target position and its path to dawn were both `useMemo`s that
// read `Date.now()` in their bodies and listed `[target, lat, lon]` as their
// dependencies. None of those three moves during a session, so both memos ran
// exactly once - at mount - and never again, while the dome underneath them
// re-tiled on the cloud model's own cadence. An hour into a session the ring
// marked where the object had BEEN and the arc started from a position it had
// already left, and nothing on the screen said so. Every unit assertion about
// either mark passes in that state, because both are correct at t = 0.
//
// So this file mounts the screen, moves the clock, fires the screen's own
// interval, and asserts BOTH marks moved:
//
//   1. THE PATH. Its polyline is walked from the target's hour angle NOW, so
//      every sample shifts once the clock does. Read off the rendered `points`
//      attribute, before and after.
//   2. THE TARGET'S POSITION. It is drawn on a canvas jsdom cannot paint, so it
//      is read through the one place it reaches the DOM: the clause that says
//      whether the target clears the site's horizon profile at its CURRENT
//      azimuth. The offset used is SEARCHED FOR in the fixture rather than
//      guessed - the test walks the same `altAzOf` + `isObstructed` pair the
//      screen does until it finds an hour at which the verdict flips - so the
//      assertion cannot quietly become vacuous if the fixture's geometry is
//      edited.
//
// The interval is captured rather than waited for: `useSlowClock` runs on a
// minute, and a test that slept for one would be a test nobody runs.

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

// ---- the screen's own minute interval, captured instead of waited for -------
const minuteTicks: (() => void)[] = [];
const realSetInterval = win.setInterval.bind(win);
win.setInterval = (fn: () => void, ms?: number): number => {
  // 60_000 is `useSlowClock`'s period. Anything else (the cloud model's poll,
  // an animation) is left on the real timer so this hook does not change the
  // behaviour of the components it is not about.
  if (ms === 60_000) { minuteTicks.push(fn); return -1 as unknown as number; }
  return realSetInterval(fn, ms);
};

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
const T0 = new Date(2026, 8, 10, 21, 0, 0).getTime();
let clockMs = T0;
const realNow = Date.now;
Date.now = () => clockMs;

// ----------------------------------------------------------------- the site
const LAT = 47.61;
const LON = -122.33;
/** A low, uneven profile: a target crossing it changes verdict within a few
 *  hours, which is what the search below relies on. */
const HORIZON: [number, number][] = [[0, 8], [90, 42], [180, 8], [270, 42], [359, 8]];

/**
 * M27, at its real coordinates, transiting shortly after T0.
 *
 * A SOUTHERN target on purpose. The dome is drawn as a hemisphere and its far
 * side is culled (`pathRuns` drops every sample whose projection is not
 * `facing`), so a circumpolar northern object can have a perfect nine-hour walk
 * and draw no polyline at all - which would make the path assertions below
 * grade an empty group. This one is above 20 degrees and on the near side for
 * the whole window, and it descends through the site's profile inside it.
 */
const TARGET = { name: "M27", ra_hours: 19.9934, dec_deg: 22.7211 };

/** Two more objects in the same loaded plan, so the dome has THREE arcs on it
 *  and not one. They are what the re-tick assertion needs: the running target's
 *  path re-walking while the rest of the plan sat frozen would be exactly the
 *  defect this file exists for, one arc further along. Both are on the near
 *  half of the dome for the whole window from this site (checked against
 *  `placeTrack`, not assumed - an arc round the back draws nothing and would
 *  make the count vacuous). */
const PLAN_ALSO = [
  { name: "M57", ra_hours: 18.8853, dec_deg: 33.03 },
  { name: "M11", ra_hours: 18.8517, dec_deg: -6.27 },
];

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
  if (u.includes("/api/visibility")) {
    return ok({
      date: "2026-09-10",
      transit_unix: T0 / 1000 + 3600, transit_alt: 70, transit_in_daylight: false,
      dark_start_unix: T0 / 1000 - 3600, dark_end_unix: T0 / 1000 + 9 * 3600,
      darkness_kind: "astronomical",
      samples: [{ t_unix: T0 / 1000, alt: 60, moon_alt: -10, sun_alt: -20 }],
      moon: {
        illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90,
        separation_deg: 70, rise_unix: null, set_unix: null,
      },
      best_window: null, alt_limit_deg: 20, never_rises_above_limit: false,
    });
  }
  if (u.includes("/api/site")) {
    return ok({
      site: {
        name: "Back lawn", latitude: LAT, longitude: LON, elevation_m: 52,
        is_default: false, horizon_min_deg: 20, horizon_points: HORIZON,
      },
      version: 3,
    });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({
      enabled: true, platform: "G18", observed_at: null, age_s: 90, stale: false,
      last_error: null, motion: null, credit: { source: "NOAA GOES", url: "x" },
      suggested_platform: "G18",
      rows: [new Array(36).fill(0.4)], alt_start: 15, alt_step: 6, az_step: 10,
    });
  }
  return ok({});
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { DomeScreen } = await import("../dome/DomeScreen");
// The SAME pair the screen uses, so the offset searched for below is one the
// screen must agree about rather than one this file derived its own way.
const { altAzOf } = await import("../../../../lib/altaz");
const { isObstructed } = await import("../../../lib/horizonModel");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const q = (sel: string) => container.querySelector(sel) as any;
const byId = (id: string) => q(`[data-testid="${id}"]`);
const skyText = (): string => String(byId("wx-sky")?.textContent ?? "");

const hzPoints = HORIZON.map(([az, alt]) => ({ az, alt }));
const obstructedAt = (ms: number): boolean => {
  const { altDeg, azDeg } = altAzOf(TARGET.ra_hours, TARGET.dec_deg, LAT, LON, ms / 1000);
  return isObstructed(hzPoints, altDeg, azDeg);
};

/** The first quarter-hour step within tonight at which the horizon verdict
 *  flips. Searched, not guessed: an offset hardcoded against a fixture that is
 *  later edited becomes an assertion that cannot fail. */
function flipOffsetMs(): number | null {
  const start = obstructedAt(T0);
  for (let m = 15; m <= 9 * 60; m += 15) {
    if (obstructedAt(T0 + m * 60_000) !== start) return m * 60_000;
  }
  return null;
}

/** Move the clock and run the screen's own minute interval, which is the ONLY
 *  thing that is supposed to make either mark move. */
async function tickTo(ms: number): Promise<void> {
  clockMs = ms;
  await act(async () => { for (const fn of minuteTicks) fn(); });
  await settle();
}

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
  // The dome is about the RUNNING session's target, resolved against the loaded
  // plan for its coordinates (`contextTarget`).
  sequence: { target: TARGET.name },
  plan: {
    targets: [
      { name: TARGET.name, ra_hours: TARGET.ra_hours, dec_deg: TARGET.dec_deg },
      ...PLAN_ALSO,
    ],
  },
  status: {
    connected: {}, looping: false,
    mount: {
      ra_hours: 9.9, dec_deg: 69, ra_str: "09:54:00", dec_str: "+69:00:00",
      alt: 60, az: 30, tracking: true, parked: false, slewing: false,
    },
  },
} as never);

const root = createRoot(container);
await act(async () => { root.render(createElement(DomeScreen)); });
await settle();

/** Every arc's geometry, keyed by the subject it belongs to. One string per
 *  arc and not one for the whole group: a group-wide comparison passes the
 *  moment ANY arc moves, which is exactly the state this file was written for -
 *  the running target re-walking while the rest of the plan sat frozen. */
const arcPoints = (): Record<string, string> => {
  const out: Record<string, string> = {};
  for (const g of Array.from(byId("wx-dome-path")?.querySelectorAll("[data-track]") ?? [])) {
    const el = g as any;
    out[String(el.getAttribute("data-track"))] =
      Array.from(el.querySelectorAll("polyline"))
        .map((p: any) => p.getAttribute("points")).join("|");
  }
  return out;
};

await testAsync("precondition: the dome drew a path for the target AND the rest of the plan", async () => {
  assert(byId("wx-sky") != null, "the sky screen did not render at all");
  assert(minuteTicks.length > 0,
    "the screen registered NO minute interval, so nothing below could ever re-tick");
  const path = byId("wx-dome-path");
  assert(path != null,
    "no path group on the dome - every assertion below would pass over a missing mark");
  assert(path.querySelectorAll("polyline").length > 0,
    "the path group is empty, so its 'points' cannot say anything about the clock");
  assert(/M27/.test(skyText()), "the screen does not name the target it is drawing");

  const arcs = Array.from(path.querySelectorAll("[data-track]")) as any[];
  assert(arcs.length >= 3,
    `only ${arcs.length} arc(s) on a dome whose plan has three targets - the rest of `
    + "the night is not being drawn at all");
  const bright = arcs.filter((a) => a.getAttribute("data-bright") === "1");
  eq(bright.length, 1, "exactly one arc - the running target's - may be bright:");
  assert(String(bright[0].getAttribute("data-track")).includes(TARGET.name),
    `the bright arc is ${bright[0].getAttribute("data-track")}, not the running target`);
  for (const [id, pts] of Object.entries(arcPoints())) {
    assert(pts.length > 0, `arc ${id} has no geometry, so the tick test cannot grade it`);
  }
});

await testAsync("EVERY path re-walks from the CURRENT hour angle, not from mount time", async () => {
  const before = arcPoints();
  assert(Object.keys(before).length >= 3, "precondition: fewer than three arcs to compare");

  await tickTo(T0 + 45 * 60_000);

  const after = arcPoints();
  eq(Object.keys(after).length, Object.keys(before).length,
    "an arc vanished rather than moving:");
  for (const id of Object.keys(before)) {
    assert(after[id] !== undefined, `arc ${id} disappeared on the tick`);
    assert(after[id] !== before[id],
      `45 minutes passed and ${id}'s arc is identical - that walk is frozen at the `
      + "instant the screen mounted, so it now starts from a position the object has "
      + "already left");
  }
});

await testAsync("the target's position is re-derived, so the horizon verdict follows it", async () => {
  const flip = flipOffsetMs();
  assert(flip != null,
    "the fixture never crosses its own horizon profile tonight - the assertion below "
    + "would be vacuous, so fix the fixture rather than the component");
  const want = obstructedAt(T0 + (flip as number));

  clockMs = T0;
  await tickTo(T0);
  const startsObstructed = /is behind your horizon profile/.test(skyText());
  assert(startsObstructed === obstructedAt(T0),
    `precondition: the screen and the geometry disagree at t0 (screen ${startsObstructed})`);

  await tickTo(T0 + (flip as number));
  const nowObstructed = /is behind your horizon profile/.test(skyText());
  assert(nowObstructed === want,
    "the target moved across the site's horizon profile and the clause did not follow it - "
    + "the position is still the one computed when the screen mounted");
});

await act(async () => { root.unmount(); });
Date.now = realNow;

const total = passed + failed;
console.log(`domeClockDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
