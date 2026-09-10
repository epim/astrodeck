// targetsDom.test.tsx - the suggested-targets sheet, MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/targetsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// FOUR THINGS, and each of them is a way the list can look right and be wrong:
//
//  1. A PRECONDITION MARKER: the header counts something and a row carries a
//     cloud figure. A sheet that rendered its frame and no rows would satisfy
//     "the comet is absent" for the wrong reason.
//  2. SATELLITES AND COMETS ARE ABSENT. The engine carries no ephemeris for
//     either. The seeded solar-system payload contains one of each, plus a
//     PLANETARY NEBULA - because `q=planet` matches that too, and a filter on
//     the query rather than on the row's own `kind` would put a DSO in the
//     planets.
//  3. A ROW AND A SEARCH HIT DO THE SAME THING. Both aim the finder, through
//     the hash, because the finder lives in a different component from this
//     sheet and a sheet is route state rather than a child of the screen.
//  4. A VIEWER GETS THE SEARCH FIELD AND A SENTENCE. `/api/catalog/tonight` is
//     `view.site_derived`-gated: without it there is no ranking, and an empty
//     list would read as "nothing is up tonight".

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
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// Nothing hidden by the lens: the sheet must show every kind it CAN show, so
// an absent comet is the engine's answer and not a stored filter.
win.localStorage.setItem("astrodeck-next-sky-lens", JSON.stringify({
  galaxy: true, nebula: true, cluster: true, planet: true, moon: true,
}));
win.localStorage.setItem("astrodeck-next-sky-floor", "0");

// M31's real ra/dec puts it near the 20-degree flat-horizon cutoff for this
// fixture's site (lat 47.6): its altitude right now crosses 20 degrees twice
// a day, which flips a row between the cloud reading and BEHIND HORIZON
// depending on the wall clock the suite happens to run under. Pin the clock
// to 09:07 PDT, where M31 sits at 24 degrees - clear of the cutoff - so the
// hole-in-the-cloud-map case below tests what its comment says regardless of
// when this file runs.
const NOW = Date.UTC(2026, 8, 10, 16, 7, 0);
const realNow = Date.now;
Date.now = () => NOW;

// ------------------------------------------------------------- fetch recorder
const asks: string[] = [];

/** `/api/catalog/tonight` picks: built inline by the server and carrying NO
 *  `kind` field, because every pick is deep-sky. */
const PICKS = [
  {
    id: "n869", name: "Double Cluster", type: "Open Cluster",
    ra_hours: 2.32, dec_deg: 57.13, mag: 3.7, size_arcmin: 30,
    difficulty: "easy", max_alt: 71, transit_unix: Math.round(Date.now() / 1000) + 5400,
    moon_sep_deg: 84, never_rises_above_limit: false, score: 9,
  },
  {
    id: "m31", name: "Andromeda Galaxy", type: "Galaxy",
    ra_hours: 0.712305, dec_deg: 41.26917, mag: 3.4, size_arcmin: 190,
    difficulty: "easy", max_alt: 58, transit_unix: Math.round(Date.now() / 1000) + 3600,
    moon_sep_deg: 12, never_rises_above_limit: false, score: 8,
  },
  {
    id: "n7000", name: "North America Nebula", type: "Emission Nebula",
    ra_hours: 20.98, dec_deg: 44.5, mag: 4, size_arcmin: 120,
    difficulty: "hard", max_alt: 46, transit_unix: Math.round(Date.now() / 1000) - 600,
    moon_sep_deg: 40, never_rises_above_limit: false, score: 6,
  },
];

/** What `q=planet` really returns: two bodies, a comet the engine has no
 *  ephemeris for, and a PLANETARY NEBULA - `_TYPE_NAMES["PN"]` matches the
 *  query, and it is a DSO. */
const PLANET_HITS = [
  { id: "Jupiter", name: "Jupiter, 44″ across, rising in Taurus.", type: "Planet", kind: "solar_system", ra_hours: 5.1, dec_deg: 22.0, mag: -2.4, size_arcmin: 0.73, alt: 34, az: 116 },
  { id: "C/2026 K1", name: "Comet C/2026 K1, mag 7.8.", type: "Comet", kind: "solar_system", ra_hours: 18.2, dec_deg: 12.0, mag: 7.8, size_arcmin: 12, alt: 41, az: 302 },
  { id: "m57", name: "Ring Nebula", type: "Planetary Nebula", kind: "dso", ra_hours: 18.89, dec_deg: 33.03, mag: 8.8, size_arcmin: 1.4 },
];
const MOON_HITS = [
  { id: "Moon", name: "The Moon, 71% waxing gibbous.", type: "Moon", kind: "solar_system", ra_hours: 22.4, dec_deg: -8.0, mag: -12.1, size_arcmin: 31, alt: 27, az: 168 },
  { id: "ISS", name: "ISS pass 22:41-22:47.", type: "Satellite", kind: "solar_system", ra_hours: 3.0, dec_deg: 10.0, mag: -3.4, size_arcmin: 0.02, alt: 58, az: 238 },
];

let searchHits: any[] = [];

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  asks.push(`${(init?.method ?? "GET").toUpperCase()} ${u}`);
  const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: false, picks: PICKS });
  if (u.includes("/api/catalog?q=planet")) return ok({ results: PLANET_HITS, notes: [] });
  if (u.includes("/api/catalog?q=moon")) return ok({ results: MOON_HITS, notes: [] });
  if (u.includes("/api/catalog?q=")) return ok({ results: searchHits, notes: [] });
  if (u.includes("/api/cloudmap/dome")) {
    // No granule pair yet: a HOLE, which the row must not print as 0% cloud.
    return ok({ enabled: true, observed_at: null, stale: false, rows: [], alt_start: 5, alt_step: 6, az_step: 10, reason: null });
  }
  if (u.includes("/api/site")) return ok({ site: { name: "Back lawn", horizon_min_deg: 20, horizon_points: null } });
  if (u.includes("/api/visibility")) return ok({});
  return ok({});
};
const tonightAsks = () => asks.filter((a) => a.includes("/api/catalog/tonight"));

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { TargetsSheet } = await import("../targets");
const { RANK_NEEDS_SITE } = await import("../targetsModel");

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

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

/** A 15-minute forecast grid centred on now, so the hourly cloud figure exists
 *  and a row can print one. */
function forecast() {
  const times: string[] = [];
  const cloud: number[] = [];
  const t0 = Date.now() - 2 * 15 * 60_000;
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
      filterwheel: { position: 0, names: ["L", "R", "G", "B", "Ha", "OIII", "SII"] },
    },
    weather: {
      enabled: true, fetched_ts: Math.round(Date.now() / 1000), stale: false,
      ignore_tonight: false, threshold_pct: 60, sustain_minutes: 30,
      site_lat: 47.6, site_lon: -122.3, forecast: forecast(),
      astrospheric: null, alert: null,
    },
    config: null,
  } as never);
}

seed();
let root = createRoot(container);
await act(async () => {
  root.render(createElement(TargetsSheet, { params: {}, depth: 0 as const }));
});
await settle();

// ------------------------------------------------------------ 1. the marker
test("the sheet rendered a counted header and rows carrying a sky figure", () => {
  assert(q('[data-testid="sky-targets"]') != null,
    "no sky-targets marker: the fixture is wrong, not the component");
  const title = q(".nx-sheet-title");
  assert(/\d+ SUGGESTED TARGETS/.test(title?.textContent ?? ""),
    `the header does not count anything: ${title?.textContent}`);
  const rows = qa("[data-target-row]");
  assert(rows.length >= 3, `expected the ranked picks to render, got ${rows.length} rows`);
  assert(rows.some((r) => /%/.test(r.textContent)),
    "no row carries a cloud figure - the sky column never rendered");
});

test("the ranked picks and the two bodies are all on the list", () => {
  const ids = qa("[data-target-row]").map((r) => r.getAttribute("data-target-row"));
  for (const want of ["m31", "n869", "n7000", "Jupiter", "Moon"]) {
    assert(ids.includes(want), `${want} is missing from the list (${ids.join(", ")})`);
  }
});

test("satellites and comets are absent - no ephemeris, no row", () => {
  const ids = qa("[data-target-row]").map((r) => r.getAttribute("data-target-row"));
  assert(!ids.includes("C/2026 K1"),
    "a comet reached the list; the engine carries no cometary ephemeris");
  assert(!ids.includes("ISS"),
    "a satellite reached the list; the engine carries no TLE propagator");
  // ...and they are not greyed out either. A kind with no data source is not a
  // filter anybody can usefully turn on.
  assert(!/C\/2026/.test(container.textContent), "the comet is named somewhere on the sheet");
  assert(!/\bISS\b/.test(container.textContent), "the satellite is named somewhere on the sheet");
});

test("a PLANETARY NEBULA that matched `q=planet` is a nebula, not a planet", () => {
  // `q=planet` also matches `_TYPE_NAMES["PN"]`. The filter is on the row's own
  // `kind`, so this DSO never enters through the solar-system door - but it is
  // not on the list at all here, because the ranked picks are the DSO source
  // and m57 is not in them.
  const ids = qa("[data-target-row]").map((r) => r.getAttribute("data-target-row"));
  assert(!ids.includes("m57"), "a Planetary Nebula entered through the solar-system query");
});

test("a body's SHORT LABEL is the title, not its describe sentence", () => {
  const row = q('[data-target-row="Jupiter"]');
  assert(row != null, "no Jupiter row");
  // For a solar-system row `id` is the label and `name` is a whole sentence.
  // Rendering `name` as the title prints a paragraph into the list.
  const label = row.querySelector(".nx-display");
  eq(label?.textContent, "Jupiter", "the row title must be the label");
  assert(row.textContent.includes("rising in Taurus"),
    "the server's sentence should still be the second line");
});

test("a hole in the cloud map is not 0% cloud", () => {
  // The dome answered with no rows at all, so the per-target reading is absent
  // and the hourly total (8%) is what the row shows. What it must NOT show is a
  // confident CLEAR from a granule pair that does not exist.
  const row = q('[data-target-row="m31"]');
  assert(/CLEAR · 8%/.test(row.textContent),
    `the row should fall back to the hourly figure, got ${row.textContent}`);
});

test("the moon-separation glyph warns on a close target and not on a far one", () => {
  const near = q('[data-target-row="m31"] [data-moon-glyph]');
  const far = q('[data-target-row="n869"] [data-moon-glyph]');
  eq(near?.getAttribute("data-moon-glyph"), "✕", "12 degrees from the moon is the worst tier");
  eq(far?.getAttribute("data-moon-glyph"), "☾", "84 degrees is comfortable");
  assert(/heavy gradient/.test(near?.getAttribute("title") ?? ""),
    "the close row does not explain itself");
});

test("the difficulty glyph carries the server's tier", () => {
  eq(q('[data-target-row="m31"] [data-difficulty]')?.getAttribute("data-difficulty"), "easy", "");
  eq(q('[data-target-row="n7000"] [data-difficulty]')?.getAttribute("data-difficulty"), "hard", "");
});

test("the palette is derived from the wheel, not stored", () => {
  // Seven slots with Ha/OIII/SII in them: an emission nebula gets SHO, a galaxy
  // gets LRGB, and a body gets video.
  assert(/SHO/.test(q('[data-target-row="n7000"]').textContent), "an emission nebula wants narrowband");
  assert(/LRGB/.test(q('[data-target-row="m31"]').textContent), "a galaxy is broadband");
  assert(/RGB video/.test(q('[data-target-row="Jupiter"]').textContent), "a planet is video");
});

// ------------------------------------------------------- 2. pressing a row
await testAsync("pressing a row leaves the sheet and aims the finder at it", async () => {
  const row = q('[data-target-row="m31"] [data-testid="target-pick"]');
  await act(async () => {
    row.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
  eq(win.location.hash, "#/sky?lock=m31",
    "the row must close the sheet and hand the id to the finder");
});

await testAsync("the info button opens the brief for THAT row", async () => {
  win.location.hash = "#/sky/targets";
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  seed();
  await act(async () => {
    root.render(createElement(TargetsSheet, { params: {}, depth: 0 as const }));
  });
  await settle();
  await act(async () => {
    q('[data-target-row="n7000"] [data-testid="target-info"]').dispatchEvent(
      new win.MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  });
  await settle();
  eq(win.location.hash, "#/sky/targets/brief?id=n7000", "the brief opens on top, carrying the id");
});

// --------------------------------------------------------- 3. the search
await testAsync("the search field asks the catalogue, and a hit aims the finder", async () => {
  win.location.hash = "#/sky/targets";
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  seed();
  searchHits = [{ id: "m31", name: "Andromeda Galaxy", type: "Galaxy", kind: "dso", ra_hours: 0.712305, dec_deg: 41.26917, mag: 3.4, size_arcmin: 190, alt: 58, az: 64 }];
  await act(async () => {
    root.render(createElement(TargetsSheet, { params: {}, depth: 0 as const }));
  });
  await settle();

  const input = q('input[aria-label="Search the target catalog"]');
  assert(input != null, "the search field is missing - GAP-ANALYSIS 6 asks for it by name");
  // The placeholder is load-bearing: whatever the example shows, a beginner
  // types, and the server was changed so that the spaced designation "M 31"
  // finds M31 (squash_designation drops separators - the dash below is not
  // on the wire).
  eq(input.getAttribute("placeholder"), "Search catalog - e.g. M 31", "the documented example");
  assert(
    !(input.getAttribute("placeholder") ?? "").includes("\u2014"),
    "the placeholder must never carry an em-dash",
  );

  const before = asks.length;
  await act(async () => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!
      .call(input, "M 31");
    input.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  await settle();
  const asked = asks.slice(before).find((a) => a.includes("q=M%2031"));
  assert(asked != null, `the query never reached the server: ${asks.slice(before).join(" | ")}`);

  const hit = qa("button").find((b) => /Andromeda Galaxy/.test(b.textContent));
  assert(hit != null, "the result row did not render");
  await act(async () => {
    hit.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
  eq(win.location.hash, "#/sky?lock=m31",
    "a search hit goes through the same handler a row press does");
});

// --------------------------------------------------------- 4. the viewer
await testAsync("a viewer gets the search field and a sentence, not an empty list", async () => {
  win.location.hash = "#/sky/targets";
  await act(async () => { root.unmount(); });
  asks.length = 0;
  root = createRoot(container);
  seed(VIEWER);
  await act(async () => {
    root.render(createElement(TargetsSheet, { params: {}, depth: 0 as const }));
  });
  await settle();

  eq(tonightAsks().length, 0,
    "the ranking route is view.site_derived-gated; asking would be a guaranteed 403");
  eq(qa("[data-target-row]").length, 0, "there is no ranked list for this role");
  const line = q('[data-testid="targets-needs-site"]');
  assert(line != null, "an empty list with no sentence reads as 'nothing is up tonight'");
  eq(line.textContent, RANK_NEEDS_SITE, "the sentence says what is missing and what still works");
  assert(q('input[aria-label="Search the target catalog"]') != null,
    "search still works for a viewer, and the sentence promises it does");
});

await act(async () => { root.unmount(); });

Date.now = realNow;

const total = passed + failed;
console.log(`targetsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
