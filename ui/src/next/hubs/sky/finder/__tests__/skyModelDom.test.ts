// skyModelDom.test.ts - `useSkyModel` MOUNTED, with the routes it actually
// calls answered by a fetch double.
//
//   Run directly:  npx tsx src/next/hubs/sky/finder/__tests__/skyModelDom.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// `skyModel.test.ts` covers the math, which is pure and needs no DOM. What it
// cannot cover is the three things this file exists for, all of which are
// properties of the HOOK - how it reads a response, when it aims, and what it
// carries through:
//
//   1. THE SOLAR-SYSTEM RESPONSE SHAPE. `/api/catalog?q=…&explain=1` answers
//      `{"results": […]}`. Reading `rows` parsed every answer as an empty list,
//      so no planet and no Moon ever reached the finder - and nothing looked
//      broken, because a sky with no planets in it is an ordinary sight. Only a
//      mounted hook can catch that: the parse is inside the effect.
//   2. WHEN THE OPENING AIM FIRES. Two sources feed the merged ranking and they
//      answer at different times. Aiming at the first non-empty one aimed at
//      this file's own arithmetic and then sat still while the SERVER's alt/az
//      arrived and moved the marker - the finder opening a few degrees off its
//      own top target, in SWEEP. The fixture below answers the region call
//      EMPTY the first time and with hinted rows the second, which is the real
//      sequence, on a real timer.
//   3. THE CATALOGUED EXTENT. `size_arcmin` has to survive three sources, a
//      merge and a ranking to reach FRAME's ellipse.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body></body></html>`,
  { url: "http://local/#/sky", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
Object.defineProperty(win, "isSecureContext", { value: false, configurable: true });
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const NOW = Date.UTC(2026, 8, 10, 5, 0, 0);
const LAT = 47.61;
const LON = -122.33;

// ------------------------------------------------------------- the fixtures
//
// Every one of these is a plain server row shape, verbatim from the route that
// sends it: picks from `/api/catalog/tonight`, region rows from
// `/api/catalog/region`, bodies from `/api/catalog?q=…&explain=1`.

interface Row { [k: string]: unknown }

let picks: Row[] = [];
let regionRows: Row[] = [];
let planetResults: Row[] = [];
let moonResults: Row[] = [];
/** How many times the region route has been asked, so a scenario can answer the
 *  FIRST call empty and later ones with rows - the "picks first, region later"
 *  sequence, without a wall-clock race in the test. */
let regionCalls = 0;
let regionFirstEmpty = false;

const pick = (id: string, name: string, type: string, ra: number, dec: number, size: number): Row => ({
  id, name, type, ra_hours: ra, dec_deg: dec, mag: 5, size_arcmin: size,
  difficulty: "easy", surface_brightness: 21, difficulty_source: "heuristic",
  max_alt: 60, transit_unix: NOW / 1000 + 7200, best_window: null,
  moon_sep_deg: 80, never_rises_above_limit: false, score: 1,
});

const regionRow = (
  id: string, label: string, type: string, ra: number, dec: number, size: number,
  alt?: number, az?: number,
): Row => ({
  id, label, kind: "dso", type, ra_hours: ra, dec_deg: dec, mag: 5,
  size_arcmin: size, constellation: "And", describe: `${label}, a ${type}.`,
  alias: null, ...(alt == null ? {} : { alt, az }),
});

const body = (id: string, type: string, ra: number, dec: number, alt: number, az: number): Row => ({
  id,
  // A body's `id` is the LABEL and its `name` is a whole sentence - the
  // opposite way round from a DSO (solar_system.py row()).
  name: `${id}, ${type === "Moon" ? "a waning crescent" : "44 arcseconds across"}, ${Math.round(alt)} degrees up.`,
  type, kind: "solar_system", ra_hours: ra, dec_deg: dec, alt, az,
  mag: -2, size_arcmin: type === "Moon" ? 31 : 0.7,
});

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

function ok(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}

const asked: string[] = [];
const g = globalThis as any;
g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  asked.push(`${init?.method ?? "GET"} ${u}`);
  if (u.includes("/api/cloudmap/dome")) {
    // No dome: the cloud reading then comes from the hourly forecast, which is
    // what a rig without a satellite pair actually shows.
    return ok({ enabled: false, observed_at: null, stale: true, alt_start: 6, alt_step: 6, az_step: 10, rows: [] });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({ enabled: false, platform: "G18", observed_at: null, age_s: null, stale: true, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  if (u.includes("/api/catalog/tonight")) {
    return ok({ date: "2026-09-10", site_is_default: false, picks });
  }
  if (u.includes("/api/catalog/region")) {
    regionCalls += 1;
    const rows = regionFirstEmpty && regionCalls <= 1 ? [] : regionRows;
    return ok({ rows, truncated: false, catalog_degraded: false, notes: [] });
  }
  if (u.includes("/api/catalog?q=planet")) return ok({ results: planetResults, notes: [] });
  if (u.includes("/api/catalog?q=moon")) return ok({ results: moonResults, notes: [] });
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
  "window", "document", "navigator", "HTMLElement", "Element", "SVGElement",
  "Node", "Event", "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle",
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
const { useStore } = await import("../../../../../store");
const { altAzOf } = await import("../../../../../lib/altaz");
const { AIM_FOLLOW_DEG, useSkyModel } = await import("../model");
import type { SkyModel } from "../model";

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
function near(got: number, want: number, tol: number, msg: string): void {
  if (!(Math.abs(got - want) <= tol)) {
    throw new Error(`${msg} (expected ${want} +/- ${tol}, got ${got})`);
  }
}

/** The same folded, cosine-narrowed gap the model follows its top target by. */
function gapDeg(a: { az: number; alt: number }, b: { az: number; alt: number }): number {
  const dAlt = b.alt - a.alt;
  const dAz = (((b.az - a.az) % 360) + 540) % 360 - 180;
  return Math.hypot(dAlt, dAz * Math.cos((((a.alt + b.alt) / 2) * Math.PI) / 180));
}

const wait = async (ms: number): Promise<void> => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};

/**
 * Wait for a condition rather than for a duration.
 *
 * The model's own settle timer is 1.5 s of REAL time and a cold `tsx` start
 * spends most of a second compiling, so every fixed wait in here would either
 * be flaky or be padded to the point of measuring nothing. Polling asserts the
 * SEQUENCE (this happened, then that did) instead of the clock.
 */
async function until(what: string, pred: () => boolean, budgetMs = 8000): Promise<void> {
  // `Date.now` is frozen to the fixture's instant, so the wall clock here is
  // `performance.now()` - the one clock in the file that still moves.
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
      status: { connected: {}, looping: false },
      config: { optics: null, safety: { horizon: null }, survey: { online_fetch: false } },
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

interface Mounted {
  model(): SkyModel;
  unmount(): void;
}

async function mountModel(): Promise<Mounted> {
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  let latest: SkyModel | null = null;
  const Probe = (): null => {
    latest = useSkyModel(370);
    return null;
  };
  const root = createRoot(host);
  await act(async () => { root.render(createElement(Probe)); });
  return {
    model: () => {
      if (latest == null) throw new Error("the probe never rendered - the fixture is wrong");
      return latest;
    },
    unmount: () => { act(() => { root.unmount(); }); host.remove(); },
  };
}

// ============================================ 1. the solar-system row shape
// plus the extent, both on one mount: the fixture is the same, and the two
// answers travel the same merge.

await testAsync(
  "the Moon and the planets arrive under `results`, and the extent survives to the target",
  async () => {
    picks = [
      pick("m31", "M31", "Galaxy", 0.7123, 41.269, 190),
      pick("m33", "M33", "Galaxy", 1.5641, 30.66, 178),
    ];
    regionRows = [
      regionRow("m31", "M31", "Galaxy", 0.7123, 41.269, 190, 58, 64),
      regionRow("m33", "M33", "Galaxy", 1.5641, 30.66, 178, 40, 100),
    ];
    // Both bodies well down the ranking, so they prove they ARRIVED without
    // also taking over the opening aim.
    planetResults = [body("Jupiter", "Planet", 4.2, 21.1, 20, 200)];
    moonResults = [body("Moon", "Moon", 6.4, 18.0, 15, 250)];
    regionFirstEmpty = false;
    regionCalls = 0;
    seed();

    const m = await mountModel();
    // Both sources answer at once here, so the aim comes from the ranking
    // having SETTLED, not from the settle timer running out.
    await until("the opening aim", () => m.model().trackId != null);
    const model = m.model();

    const ids = model.targets.map((t) => t.id);
    assert(
      ids.includes("Jupiter"),
      `no planet reached the finder - the route answers {results}, not {rows}: [${ids.join(", ")}]`,
    );
    assert(ids.includes("Moon"), `the Moon never reached the finder: [${ids.join(", ")}]`);
    const jupiter = model.targets.find((t) => t.id === "Jupiter");
    eq(jupiter?.kind, "planet", "a planet must land under the PLANETS lens:");
    eq(jupiter?.name, "Jupiter", "a body's id is its label - the pill would print a paragraph:");

    const m33 = model.targets.find((t) => t.id === "m33");
    eq(m33?.sizeArcmin, 178, "the catalogued extent did not survive to the target:");
    const moon = model.targets.find((t) => t.id === "Moon");
    eq(moon?.sizeArcmin, 31, "the Moon's own extent was dropped:");

    // Both sources answered inside 500 ms, so the aim did not wait out the
    // settle timer - and it landed on the SERVER's alt/az for the top target.
    near(model.az, 64, 1e-9, "the opening aim's azimuth");
    near(model.alt, 58, 1e-9, "the opening aim's altitude");
    eq(model.trackId, "m31", "the top-ranked target should be the tracked one:");
    eq(model.lockNote, "LOCKED · CLEAR", "the aim did not put the top target under the reticle:");

    m.unmount();
  },
);

// ================================== 2. the aim waits for the merged ranking

await testAsync(
  "the opening aim waits for both sources, then follows the server's own alt/az",
  async () => {
    // Picks first (no alt/az of their own, so the position is this file's
    // arithmetic), region rows AFTER, carrying the server's hints.
    picks = [pick("m31", "M31", "Galaxy", 0.7123, 41.269, 190)];
    regionRows = [regionRow("m31", "M31", "Galaxy", 0.7123, 41.269, 190, 30, 200)];
    planetResults = [];
    moonResults = [];
    regionFirstEmpty = true;
    regionCalls = 0;
    seed();

    const computed = altAzOf(0.7123, 41.269, LAT, LON, NOW / 1000);
    const hinted = { az: 200, alt: 30 };
    assert(
      gapDeg({ az: computed.azDeg, alt: computed.altDeg }, hinted) > AIM_FOLLOW_DEG,
      "the fixture's two positions agree, so it could not tell the two aims apart",
    );

    const m = await mountModel();

    // The picks have landed and the region has answered EMPTY, so the merged
    // ranking is not settled. THIS is the moment the old model aimed - at its
    // own arithmetic, with the server's answer still to come.
    await until("the ranked picks", () => m.model().targets.length > 0);
    near(m.model().az, 0, 1e-9, "the finder aimed before the merged ranking settled");
    near(m.model().alt, 45, 1e-9, "the finder aimed before the merged ranking settled");

    // Past the settle timer the aim goes in on what has answered - and the
    // region's next answer moves the top target, so the view FOLLOWS it rather
    // than sitting a few degrees off it in SWEEP.
    await until(
      "the view to follow the server's own alt/az",
      () => m.model().az === hinted.az && m.model().alt === hinted.alt,
    );
    const model = m.model();
    assert(regionCalls >= 2, `the region was only asked ${regionCalls} time(s) - the fixture never delivered the hints`);
    near(model.az, hinted.az, 1e-9, "the view did not follow the server's azimuth");
    near(model.alt, hinted.alt, 1e-9, "the view did not follow the server's altitude");
    eq(model.trackId, "m31", "the followed target is not the tracked one:");
    eq(model.lockNote, "LOCKED · CLEAR", "the followed target is not under the reticle:");

    m.unmount();
  },
);

// ============================ 3. a deliberate aim ends the auto-aim for good

await testAsync("a pan stops the auto-aim - the view is never taken back", async () => {
  picks = [pick("m31", "M31", "Galaxy", 0.7123, 41.269, 190)];
  regionRows = [regionRow("m31", "M31", "Galaxy", 0.7123, 41.269, 190, 58, 64)];
  planetResults = [];
  moonResults = [];
  regionFirstEmpty = false;
  regionCalls = 0;
  seed();

  const m = await mountModel();
  // The user drags the sky before anything has been ranked, which is exactly
  // when the answers are still landing.
  act(() => { m.model().setView({ az: 200, alt: 20 }); });
  // The ranking then arrives WITH the server's alt/az, putting the top target
  // 140 degrees away from where the user is looking - which is the auto-aim's
  // one chance to take the view back.
  await until(
    "the ranked target at its hinted position",
    () => m.model().targets.some((t) => t.id === "m31" && t.azNow === 64),
  );
  await wait(2600);   // and well past the settle timer, which is its other one

  const model = m.model();
  assert(model.targets.length > 0, "precondition: there was something to steal the view for");
  near(model.az, 200, 1e-9, "the auto-aim took the view back after a pan");
  near(model.alt, 20, 1e-9, "the auto-aim took the view back after a pan");

  m.unmount();
});

Date.now = realNow;

const total = passed + failed;
console.log(`skyModelDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
