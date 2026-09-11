// skyEphemerisDom.test.tsx - satellites and comets on the Sky targets sheet,
// MOUNTED (wave U7b, T-U7b-1; decision D-SKY-1).
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/skyEphemerisDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHERE THIS MOUNTS, AND WHY IT IS NOT `SkyHub`. The wave plan's sketch put
// these assertions on the Sky hub with a satellite LOCKED. A satellite cannot
// be locked: `finder/targets.ts SATELLITE_MARKERS` is false, so it is not in
// the marker set and `pickLock` can never return it. The passes card therefore
// lives in the targets sheet, which is this task's file; `SkyHub.tsx` belongs
// to T-U7b-11 and receives the lock-card mount as a delivered block against the
// day a per-second position source makes a satellite marker honest.
//
// FIVE THINGS, and every one of them is a way the feature can look right and
// be wrong:
//
//  1. A VACUITY GUARD. The sheet's own marker AND the ranked rows, before any
//     other assertion - a sheet that rendered its frame and nothing else would
//     satisfy "no passes were fetched" for entirely the wrong reason.
//  2. THE PASSES CALL IS ONCE PER SATELLITE, NEVER A POLL.
//     `GET /api/satellites/passes` is thousands of SGP4 evaluations on a worker
//     thread; a card that re-fetched on every render would be invisible on
//     screen and expensive on the rig. The assertion counts the recorded
//     requests across two settles.
//  3. A VIEWER GETS THE SERVER'S SENTENCE AND FIRES NOTHING. The whole passes
//     route is `view.site_derived`; the honest answer is the satellite
//     module's own withheld note, verbatim, not a cap phrase we wrote.
//  4. THE STALE NOTE IS THE SERVER'S, WORD FOR WORD. It carries the drift
//     magnitude and it names where to fix it - a paraphrase drops both.
//  5. A SUNLIT FRACTION OF ZERO IS A SENTENCE, NOT A SMALL NUMBER. A pass in
//     the Earth's shadow is invisible, and rendered as "0%" it would look like
//     every other row on the card.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky/targets", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame",
  // `next/router.ts` moves the hash through the bare globals.
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const NOW = Date.UTC(2026, 8, 10, 16, 7, 0);
Date.now = () => NOW;

// ---------------------------------------------------------------- the wire
//
// Every payload below is shaped from the server's own row builders. The notes
// are the server's real sentences, trimmed to their opening clause where the
// full text is long - what matters is that the UI prints whatever it is GIVEN,
// so the fixture and the assertion share one constant.
const WITHHELD_NOTE =
  "Satellites are not offered here: where one appears in the sky depends "
  + "entirely on where you are standing. Sign in with a role that can see "
  + "site-derived data to search for them.";

const STALE_NOTE =
  "These satellite elements are 9 days old; a pass time this old can be a "
  + "minute out. Refresh them from Sky settings when the rig is online.";

const SATELLITES = [
  {
    id: "ISS (ZARYA)",
    name: "ISS (ZARYA) is 62 degrees up in the south, 431 km away and sunlit.",
    type: "Satellite", kind: "satellite",
    ra_hours: 5.5, dec_deg: 12, alt: 62, az: 180,
    mag: null, size_arcmin: 0,
    norad_id: 25544, range_km: 431, sunlit: true, shadow: "sunlit",
    elements_age_days: 9.1, ephemeris_unix: NOW / 1000,
  },
  {
    id: "HST",
    name: "HST is 21 degrees up in the west, 620 km away and in shadow.",
    type: "Satellite", kind: "satellite",
    ra_hours: 12.0, dec_deg: -3, alt: 21, az: 270,
    mag: null, size_arcmin: 0,
    norad_id: 20580, range_km: 620, sunlit: false, shadow: "umbra",
    elements_age_days: 9.1, ephemeris_unix: NOW / 1000,
  },
];

const COMETS = [
  {
    id: "12P/Pons-Brooks",
    name: "12P/Pons-Brooks is 24 degrees up in the west at magnitude 4.8.",
    type: "Comet", kind: "comet",
    ra_hours: 1.25, dec_deg: 33, alt: 24, az: 270,
    mag: 4.8, size_arcmin: 0,
    r_au: 1.2, delta_au: 0.94, elements_age_days: 3, ephemeris_unix: NOW / 1000,
    topocentric: true, geocentric_reason: null,
  },
  {
    id: "C/2026 K1",
    name: "C/2026 K1 is 2.1 au away at magnitude 11.2.",
    type: "Comet", kind: "comet",
    ra_hours: 18.2, dec_deg: 12,
    mag: 11.2, size_arcmin: 0,
    r_au: 2.9, delta_au: 2.1, elements_age_days: 3, ephemeris_unix: NOW / 1000,
    topocentric: false,
    geocentric_reason: "Computed from the centre of the Earth: this rig has no site set.",
  },
];

const PASSES = {
  passes: [
    {
      norad_id: 25544, name: "ISS (ZARYA)",
      start_unix: NOW / 1000 + 3600, peak_unix: NOW / 1000 + 3900, end_unix: NOW / 1000 + 4200,
      start_az: 270, peak_az: 180, end_az: 90,
      max_alt_deg: 62, duration_s: 600, sunlit_fraction: 1,
      enters_shadow_unix: null, leaves_shadow_unix: null, elements_age_days: 9.1,
    },
    {
      norad_id: 25544, name: "ISS (ZARYA)",
      start_unix: NOW / 1000 + 9600, peak_unix: NOW / 1000 + 9840, end_unix: NOW / 1000 + 10_080,
      start_az: 250, peak_az: 200, end_az: 120,
      max_alt_deg: 31, duration_s: 480, sunlit_fraction: 0,
      enters_shadow_unix: null, leaves_shadow_unix: null, elements_age_days: 9.1,
    },
    {
      norad_id: 25544, name: "ISS (ZARYA)",
      start_unix: NOW / 1000 + 15_600, peak_unix: NOW / 1000 + 15_900, end_unix: NOW / 1000 + 16_200,
      start_az: 300, peak_az: 210, end_az: 100,
      max_alt_deg: 45, duration_s: 600, sunlit_fraction: 0.4,
      enters_shadow_unix: NOW / 1000 + 15_840, leaves_shadow_unix: NOW / 1000 + 16_100,
      elements_age_days: 9.1,
    },
    // ROSE IN SHADOW, came out of it halfway across. Both crossings are
    // refined and `leaves` comes FIRST, which is the case the card used to
    // describe backwards: it assumed every pass began sunlit, so this one read
    // "sunlit for the first 3 minutes" at exactly the minutes it was invisible.
    {
      norad_id: 25544, name: "ISS (ZARYA)",
      start_unix: NOW / 1000 + 20_000, peak_unix: NOW / 1000 + 20_300, end_unix: NOW / 1000 + 20_600,
      start_az: 240, peak_az: 190, end_az: 110,
      max_alt_deg: 38, duration_s: 600, sunlit_fraction: 0.5,
      leaves_shadow_unix: NOW / 1000 + 20_180, enters_shadow_unix: NOW / 1000 + 20_480,
      elements_age_days: 9.1,
    },
    // FIX-S1's visible window, which is NOT the horizon window: up for ten
    // minutes, worth going outside for six. The fields are optional on the
    // wire, so this row is the only one that carries them.
    {
      norad_id: 25544, name: "ISS (ZARYA)",
      start_unix: NOW / 1000 + 26_000, peak_unix: NOW / 1000 + 26_300, end_unix: NOW / 1000 + 26_600,
      start_az: 280, peak_az: 200, end_az: 100,
      max_alt_deg: 52, duration_s: 600, sunlit_fraction: 1,
      enters_shadow_unix: null, leaves_shadow_unix: null,
      visible_start_unix: NOW / 1000 + 26_120, visible_end_unix: NOW / 1000 + 26_480,
      elements_age_days: 9.1,
    },
  ],
  horizon_source: "the horizon polyline saved for this site",
  elements: {
    which: "satellites", present: true, source: "celestrak",
    fetched_unix: NOW / 1000 - 9 * 86_400, age_days: 9.1, stale: true,
    count: 203, note: STALE_NOTE,
  },
  notes: [],
};

const PICKS = [
  {
    id: "m31", name: "Andromeda Galaxy", type: "Galaxy",
    ra_hours: 0.712305, dec_deg: 41.26917, mag: 3.4, size_arcmin: 190,
    difficulty: "easy", max_alt: 58, transit_unix: NOW / 1000 + 3600,
    moon_sep_deg: 84, never_rises_above_limit: false, score: 8,
  },
  {
    id: "n869", name: "Double Cluster", type: "Open Cluster",
    ra_hours: 2.32, dec_deg: 57.13, mag: 3.7, size_arcmin: 30,
    difficulty: "easy", max_alt: 71, transit_unix: NOW / 1000 + 5400,
    moon_sep_deg: 84, never_rises_above_limit: false, score: 9,
  },
];

const asks: string[] = [];
/** When set, `GET /api/satellites/passes` answers 409 with this code and
 *  sentence instead of the pass list. */
let passesFailure: { code: string; detail: string } | null = null;
/** The server's own site-unset sentence, which the card must show verbatim. */
const SITE_UNSET_DETAIL =
  "This rig has no site set, so there is nothing to compute passes from. "
  + "Set one under the site pill.";
/** Flipped by the viewer scenario: the SERVER is what withholds satellites, so
 *  the fixture withholds them the way the server does - empty rows plus its own
 *  sentence - rather than the UI deciding not to ask. */
let satellitesWithheld = false;

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  asks.push(`${(init?.method ?? "GET").toUpperCase()} ${u}`);
  const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
  if (u.includes("/api/satellites/passes")) {
    // The route's two 409s, in the server's own nested shape
    // (`HTTPException(409, detail={"detail": ..., "code": ...})`), so the code
    // reaches the card through `parseApiError` rather than being planted.
    if (passesFailure) {
      const f = passesFailure;
      return {
        ok: false, status: 409, statusText: "Conflict",
        json: async () => ({ detail: { detail: f.detail, code: f.code } }),
      };
    }
    return ok(PASSES);
  }
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: false, picks: PICKS });
  if (u.includes("/api/catalog?q=satellites")) {
    return satellitesWithheld
      ? ok({ results: [], notes: [WITHHELD_NOTE] })
      : ok({ results: SATELLITES, notes: [STALE_NOTE] });
  }
  if (u.includes("/api/catalog?q=comets")) return ok({ results: COMETS, notes: [] });
  // The free-text search for a satellite. `/api/catalog` discriminates every
  // row it serves, so the hit carries `kind: "satellite"` and its NORAD id -
  // which is the whole reason the sheet can answer with a pass list rather
  // than aiming a finder that never draws one.
  if (u.includes("/api/catalog?q=iss")) return ok({ results: [SATELLITES[0]], notes: [] });
  if (u.includes("/api/catalog?q=")) return ok({ results: [], notes: [] });
  if (u.includes("/api/cloudmap/dome")) {
    return ok({ enabled: true, observed_at: null, stale: false, rows: [], alt_start: 5, alt_step: 6, az_step: 10, reason: null });
  }
  if (u.includes("/api/site")) return ok({ site: { name: "Back lawn", horizon_min_deg: 20, horizon_points: null } });
  if (u.includes("/api/visibility")) return ok({});
  return ok({});
};

const passAsks = () => asks.filter((a) => a.includes("/api/satellites/passes"));
/** Where the `?sat=` remount starts in `asks`, so its request can be told from
 *  the ones the first mount made. */
let satParamFrom = 0;

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { TargetsSheet } = await import("../sheets/targets");

// ------------------------------------------------------------------- harness
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
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];
const byId = (id: string) => q(`[data-testid="${id}"]`);
const text = () => String(container.textContent ?? "");
const click = (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function forecast() {
  const times: string[] = [];
  const cloud: number[] = [];
  const t0 = NOW - 2 * 15 * 60_000;
  for (let i = 0; i < 8; i++) {
    times.push(new Date(t0 + i * 15 * 60_000).toISOString());
    cloud.push(8);
  }
  return { times, cloud, cloud_low: cloud, cloud_mid: cloud, cloud_high: cloud };
}

function seed(principal: unknown = OPERATOR): void {
  useStore.setState({
    principal,
    equipConnected: true,
    wsPhase: "up",
    site: { name: "Back lawn", latitude: 47.6, longitude: -122.3, elevation_m: 50, is_default: false, horizon_min_deg: 20 },
    status: {
      connected: { camera: { connected: true, name: "sim" } },
      filterwheel: { position: 0, names: ["L", "R", "G", "B"] },
    },
    weather: {
      enabled: true, fetched_ts: NOW / 1000, stale: false,
      ignore_tonight: false, threshold_pct: 60, sustain_minutes: 30,
      site_lat: 47.6, site_lon: -122.3, forecast: forecast(),
      astrospheric: null, alert: null,
    },
    toasts: [],
    config: null,
  } as never);
}

// Nothing hidden by the lens, so an absent row is the wire's answer and never a
// stored filter.
win.localStorage.setItem("astrodeck-next-sky-lens", JSON.stringify({
  galaxy: true, nebula: true, cluster: true, planet: true, moon: true,
  satellite: true, comet: true,
}));
win.localStorage.setItem("astrodeck-next-sky-floor", "0");

// ================================================== the operator's screen
act(() => { seed(OPERATOR); });
let root = createRoot(container);
await act(async () => {
  root.render(createElement(TargetsSheet, { params: {}, depth: 0 as const }));
});
await settle();

// ------------------------------------------------------------ 0. vacuity
test("the sheet rendered, with its ranked rows - the fixture is doing work", () => {
  assert(byId("sky-targets") != null,
    "no sky-targets marker: the fixture is wrong, not the component");
  const rows = qa("[data-target-row]");
  assert(rows.length >= 2, `expected the ranked picks to render, got ${rows.length} rows`);
});

// ------------------------------------------------- 1. the ephemeris section
test("satellites and comets have a section of their own, off the reach ranking", () => {
  assert(byId("targets-ephemeris") != null, "no ephemeris section on the sheet");
  const sats = qa('[data-testid="target-row-satellite"]');
  eq(sats.length, 2, "satellite rows:");
  const comets = qa('[data-testid="target-row-comet"]');
  eq(comets.length, 2, "comet rows:");
  // The row-shape trap: `id` is the label and `name` is a sentence.
  const iss = sats[0].querySelector(".nx-display");
  eq(iss?.textContent, "ISS (ZARYA)", "the satellite row title must be the short label");
  assert(sats[0].textContent.includes("431 km away"),
    "the server's sentence should still be the second line");
});

test("neither kind is sorted into the reach ranking - they are not scored on reach", () => {
  const ids = qa("[data-target-row]").map((r) => r.getAttribute("data-target-row"));
  assert(!ids.includes("ISS (ZARYA)"),
    `a satellite reached the ranked list, so it is a marker: ${ids.join(", ")}`);
  assert(!ids.includes("12P/Pons-Brooks"),
    `a comet was sorted by a reach score it cannot be scored on: ${ids.join(", ")}`);
  // They are both on the sheet, in their own section, which is the whole point
  // of the split - absent would be the old behaviour, and wrong.
  assert(text().includes("ISS (ZARYA)"), "the satellite is nowhere on the sheet");
  assert(text().includes("12P/Pons-Brooks"), "the comet is nowhere on the sheet");
});

await testAsync("a comet the server placed aims the finder, like any deep-sky row", async () => {
  const rows = qa('[data-testid="target-row-comet"]');
  const placed = rows.find((r) => r.textContent.includes("12P/Pons-Brooks"));
  assert(placed != null, "the topocentric comet is missing");
  const btn = placed.querySelector('[data-testid="comet-pick"]');
  assert(btn?.getAttribute("aria-disabled") == null,
    "a comet with a horizon position must be aimable");
  await act(async () => {
    btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  assert(/lock=12P/.test(String(win.location.hash)),
    `the comet row did not aim the finder: ${win.location.hash}`);
  win.location.hash = "#/sky/targets";
  await settle();
});

test("a geocentric comet is listed with the server's reason, and is not aimable", () => {
  const rows = qa('[data-testid="target-row-comet"]');
  const geo = rows.find((r) => r.textContent.includes("C/2026 K1"));
  assert(geo != null, "the geocentric comet is missing from the list");
  const note = geo.querySelector('[data-testid="comet-geocentric"]');
  assert(note != null, "no reason rendered for a comet with no horizon position");
  eq(note.textContent, COMETS[1].geocentric_reason,
    "the reason must be the server's own sentence:");
  const btn = geo.querySelector('[data-testid="comet-pick"]');
  eq(btn?.getAttribute("aria-disabled"), "true",
    "a comet with no alt/az must not offer to aim the finder:");
});

// ------------------------------------------------------------- 2. the passes
await testAsync("tapping a satellite fetches its passes ONCE and renders them", async () => {
  const before = passAsks().length;
  eq(before, 0, "the passes route was called before anybody asked for a satellite:");
  click(qa('[data-testid="target-row-satellite"]')[0]);
  await settle();

  assert(byId("sky-passes") != null, "no passes card after picking a satellite");
  eq(qa('[data-testid="sky-pass-row"]').length, 5, "pass rows:");
  eq(passAsks().length, 1, "passes requests after one pick:");
  // The URL carries the one NORAD id, repeated-key style, and the window.
  assert(/ids=25544/.test(passAsks()[0]), `the request did not scope to the satellite: ${passAsks()[0]}`);
  assert(/hours=24/.test(passAsks()[0]), `the request did not carry the window: ${passAsks()[0]}`);

  // A second settle is a second render pass. A card that re-fetched per render
  // would put a worker thread's worth of SGP4 on the rig every beat, and
  // nothing on screen would change.
  await settle();
  eq(passAsks().length, 1, "passes requests after two settles:");
});

test("the stale-elements sentence is the server's, word for word", () => {
  const note = byId("sky-passes-stale");
  assert(note != null, "no stale note on a card built from 9-day-old elements");
  eq(note.textContent, STALE_NOTE, "the stale note must be verbatim:");
});

test("a pass in the Earth's shadow says so - it is not a small percentage", () => {
  const clauses = qa('[data-testid="sky-pass-sunlit"]').map((n) => n.textContent);
  assert(clauses.includes("sunlit throughout"), `no fully-sunlit clause: ${clauses.join(" | ")}`);
  assert(clauses.includes("in shadow the whole pass - not visible"),
    `an invisible pass did not say so: ${clauses.join(" | ")}`);
  assert(clauses.some((c) => /sunlit for the first \d+ of \d+ minutes/.test(c)),
    `no split clause: ${clauses.join(" | ")}`);
  // The defect this guards: a fraction rendered as a bare percentage of a
  // quantity the reader was never told.
  assert(!clauses.some((c) => /%/.test(c ?? "")),
    "a sunlit fraction reached the screen as a percentage of nothing");
});

test("a pass row names the time AND the direction to face", () => {
  const row = qa('[data-testid="sky-pass-row"]')[0];
  const t = String(row.textContent);
  assert(/RISE/.test(t) && /PEAK/.test(t) && /SET/.test(t), `the three phases are not on the row: ${t}`);
  // Per SPAN, not over the concatenated row text: the clocks are local and
  // adjacent spans run together in `textContent`, so a word-boundary test on
  // the whole string is a test of this machine's timezone.
  const spans = Array.from(row.querySelectorAll("span")).map((s: any) => String(s.textContent));
  for (const point of ["W", "S", "E"]) {
    assert(spans.some((s) => s.endsWith(` ${point}`) || s.endsWith(` deg ${point}`)),
      `no span faces ${point} - the row cannot be acted on: ${spans.join(" | ")}`);
  }
  assert(/62 deg/.test(t), `the peak altitude is missing: ${t}`);
  assert(/10 min/.test(t), `the duration is missing: ${t}`);
});

test("a pass that ROSE in shadow is described in that order", () => {
  const clauses = qa('[data-testid="sky-pass-sunlit"]').map((n) => String(n.textContent));
  // `leaves` at +180 s and `enters` at +480 s of a 600 s pass: dark for the
  // first three minutes, lit for the next five.
  assert(clauses.some((c) => /^in shadow for the first 3 of 10 minutes, then sunlit until minute 8$/.test(c)),
    `the shadow-first pass is not described in the order it happened: ${clauses.join(" | ")}`);
  // And the opposite order still reads the other way round, so the branch is a
  // branch rather than a reworded constant.
  assert(clauses.some((c) => /^sunlit for the first 4 of 10 minutes, then in shadow until minute 8$/.test(c)),
    `the sunlit-first pass lost its own order: ${clauses.join(" | ")}`);
});

test("a pass carrying a VISIBLE window shows that window, not the horizon one", () => {
  const rows = qa('[data-testid="sky-pass-row"]');
  const row = rows[rows.length - 1];
  const t = String(row.textContent);
  assert(/VISIBLE/.test(t) && /UNTIL/.test(t),
    `the row still leads with the horizon crossings: ${t}`);
  assert(!/RISE/.test(t), `the horizon crossing is still the headline: ${t}`);
  // Six minutes of visibility inside a ten-minute pass. The duration shown has
  // to be the window shown, or the row contradicts itself.
  assert(/6 min/.test(t), `the duration is the horizon window, not the visible one: ${t}`);
  assert(!/10 min/.test(t), `the ten-minute horizon window is still being claimed: ${t}`);
  // The compass points exist only on the horizon crossings, so they may not sit
  // beside a visible-window time - they move to their own clause instead.
  const horizon = row.querySelector('[data-testid="sky-pass-horizon"]');
  assert(horizon != null,
    "the horizon crossings were dropped entirely - they are the bearings you face");
  assert(/ W to /.test(String(horizon.textContent)) && / E$/.test(String(horizon.textContent).trim()),
    `the horizon clause names no bearings: ${String(horizon.textContent)}`);

  // Every OTHER row has no such fields and must be unchanged.
  const first = String(rows[0].textContent);
  assert(/RISE/.test(first) && !/VISIBLE/.test(first),
    `a row with no visible window invented one: ${first}`);
});

// ------------------------------------------------ 2b. how a satellite is REACHED
await testAsync("a satellite search hit opens its passes instead of aiming the finder", async () => {
  // Start from nothing selected, so the card below can only be this pick's.
  click(qa('[data-testid="target-row-satellite"]')[0]);
  await settle();
  assert(byId("sky-passes") == null, "precondition: the passes card should be closed again");

  const hashBefore = String(win.location.hash);
  const input = q("input");
  assert(input != null, "no catalogue search field on the sheet");
  act(() => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!
      .call(input, "iss");
    input.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  await settle();

  const hit = qa("button").find((b: any) => /ISS \(ZARYA\)/.test(String(b.textContent))
    && !String(b.getAttribute("data-testid") ?? "").startsWith("target-row"));
  assert(hit != null, "the search dropdown never offered the satellite the server returned");
  click(hit);
  await settle();

  assert(byId("sky-passes") != null,
    "a satellite search hit did not open its pass list - the only answer a rig has about one");
  eq(String(win.location.hash), hashBefore,
    "the hit aimed the finder at a body that is never drawn on it, which the hub then "
    + "blames on tonight's list:");
});

// -------------------------------------------------- 3. the settings link
test("a note that asks for a refresh carries the link to the sheet it names", () => {
  const link = byId("ephemeris-settings-link");
  assert(link != null, "the stale note asks for a refresh and offers no way to do it");
  click(link);
  assert(/skyPack/.test(String(win.location.hash)),
    `the link did not open the sky-data sheet: ${win.location.hash}`);
});

await act(async () => { root.unmount(); });

// ================================== 3b. `?sat=`, the hash the lock card hands over
//
// `SkyHub`'s NEXT PASS CTA (`lockCta`'s `passes` case) cannot be exercised
// today - `SATELLITE_MARKERS` is false, so a satellite is never the lock - but
// the CONTRACT it depends on is this sheet's, and it is reachable: the hub
// navigates to `#/sky/targets?sat=<id>` and this sheet has to open that
// satellite's pass list. If this end is not wired, the day the constant flips
// the CTA opens an empty sheet.
satParamFrom = asks.length;
root = createRoot(container);
await act(async () => {
  root.render(createElement(TargetsSheet, { params: { sat: "ISS (ZARYA)" }, depth: 0 as const }));
});
await settle();

test("?sat= opens that satellite's pass list without a tap", () => {
  assert(byId("sky-targets") != null, "the sheet did not render at all");
  assert(byId("sky-passes") != null,
    "the hash named a satellite and the sheet opened no pass list for it");
  assert(/PASSES - ISS \(ZARYA\)/.test(String(byId("sky-passes").textContent)),
    `the card is not about the satellite the hash named: ${String(byId("sky-passes").textContent).slice(0, 80)}`);
  const fired = asks.slice(satParamFrom).filter((a) => a.includes("/api/satellites/passes"));
  assert(fired.length >= 1 && /ids=25544/.test(fired[0]),
    `the passes request did not scope to the named satellite: ${fired.join(" | ")}`);
});

await act(async () => { root.unmount(); });

// ============================== 3c. the two 409s the route answers, and their faces

passesFailure = { code: "satellites_unavailable", detail: SITE_UNSET_DETAIL };
root = createRoot(container);
await act(async () => {
  root.render(createElement(TargetsSheet, { params: { sat: "ISS (ZARYA)" }, depth: 0 as const }));
});
await settle();

test("no site set is a STATE, in the server's words, with nothing to retry", () => {
  const card = byId("sky-passes");
  assert(card != null, "precondition: no passes card at all, so its face cannot be graded");
  const withheld = byId("sky-passes-withheld");
  assert(withheld != null,
    "a refusal nobody can retry rendered as a failure - the fix is to set a site, not to press again");
  eq(String(withheld.textContent), SITE_UNSET_DETAIL,
    "the sentence must be the server's, verbatim:");
  assert(byId("sky-passes-retry") == null,
    "TRY AGAIN is offered for a refusal that will answer exactly the same way until a site is set");
  assert(card.querySelector('[role="alert"]') == null,
    "the withheld state is not an alert - nothing has gone wrong");
});

await act(async () => { root.unmount(); });

passesFailure = { code: "passes_busy", detail: "A pass search is already running." };
root = createRoot(container);
await act(async () => {
  root.render(createElement(TargetsSheet, { params: { sat: "ISS (ZARYA)" }, depth: 0 as const }));
});
await settle();

test("a busy pass search says what to wait for and KEEPS the retry", () => {
  const card = byId("sky-passes");
  assert(card != null, "precondition: no passes card at all");
  assert(byId("sky-passes-withheld") == null,
    "a refusal that waiting fixes was rendered as a permanent state");
  const alert = card.querySelector('[role="alert"]');
  assert(alert != null, "no alert for a refusal");
  assert(/one at a time/.test(String(alert.textContent)),
    `the sentence does not say why waiting is the answer: ${String(alert.textContent)}`);
  assert(byId("sky-passes-retry") != null,
    "the one refusal a retry actually fixes lost its retry button");
});

await act(async () => { root.unmount(); });
passesFailure = null;

// ============================================================ the viewer
satellitesWithheld = true;
win.location.hash = "#/sky/targets";
const viewerFrom = asks.length;
act(() => { seed(VIEWER); });
root = createRoot(container);
await act(async () => {
  root.render(createElement(TargetsSheet, { params: {}, depth: 0 as const }));
});
await settle();

test("a viewer sees the server's withheld sentence, verbatim", () => {
  assert(byId("sky-targets") != null, "the sheet did not render for a viewer");
  const notes = qa('[data-testid="ephemeris-note"]').map((n) => String(n.textContent));
  assert(notes.some((n) => n.startsWith(WITHHELD_NOTE)),
    `the withheld sentence is not on the sheet: ${notes.join(" | ")}`);
});

test("a viewer fires no /api/satellites/passes at all", () => {
  const after = asks.slice(viewerFrom).filter((a) => a.includes("/api/satellites/passes"));
  eq(after.length, 0,
    "a viewer's mount called the site-derived passes route, which answers 403:");
  assert(qa('[data-testid="target-row-satellite"]').length === 0,
    "a viewer got satellite rows the server withheld");
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`skyEphemerisDom: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed > 0) process.exitCode = 1;

export default { passed, failed, total };
export { passed, failed, total };
