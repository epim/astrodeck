// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// defaultSitePlacement.test.tsx - a DEFAULT site places nothing on the finder's
// sky (#503), MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/__tests__/defaultSitePlacement.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. A fresh config's site is `is_default: true` with placeholder
// coordinates, 0 N 0 E - and those are NUMBERS, so `useSkyModel`'s
// `haveCoords` (a type test) passed and the finder placed, ranked and counted
// the whole sky for a point in the Gulf of Guinea: the reach count on the
// status row, the reach strip, every marker's altitude tag, the lock card's
// altitude and verdict, the auto-aim's first pick and the dome's arcs. Which
// objects were "up" then changed with the hour, deterministically, at a place
// that is not the operator's - the server refuses to rank or window that sky
// (409 `no_site`, #24) and the finder did it itself. The class is a consumer of
// the site that ignores `is_default` (#24, #121, #466).
//
// WHAT IS HELD, on a default site at TWO hours of the same day (22:00 UT, when
// M31 is 28 degrees up at the placeholder, and 12:00 UT, when it is 44 below):
//
//   * the finder's placement note shows, and it is the no-site sentence;
//   * no row is drawn: no marker, so no altitude tag; the in-view region is
//     never even asked about a placeholder sky;
//   * nothing is counted: the status row prints no number (it printed "Show 0
//     suggested targets" until #544), the patch card prints no "N of M
//     targets" line, and there is no reach chip, nor the empty strip's
//     "behind your horizon or under cloud";
//   * no verdict: the lock note is SWEEP, there is no lock card, and no
//     altitude or status word is printed anywhere on the hub outside the two
//     places named below, the patch card included (no "BELOW HORIZON"), and
//     IMAGE THIS PATCH gives the no-site reason.
//
// THE CONTROL: the SAME coordinates, SAVED (only `is_default` differs), with a
// computed list. There the scan must find markers, altitude tags, a count and
// a verdict at both hours - which is what keeps every "nothing is printed"
// above from passing on a harness that cannot see a placement at all.
//
// AND THE OTHER WAY TO HAVE NO COORDINATES: a role that is ranked but not
// given the site. The body the SERVER placed is drawn with its own altitude,
// the placement note is the hidden-coordinates sentence, and no window is
// walked for the body, where the rows used to be walked at the `?? 0`.
//
// TWO THINGS THE SCAN LEAVES OUT, each on purpose:
//   * `[data-sky-readout]`, "az 0° · alt 45°" - where the finder's own VIEW is
//     pointed, a screen direction the operator drags, not the altitude of any
//     object, and not derived from the site;
//   * the MAP grid's rulings, "20°" to "70°": the labels on the dashed lines of
//     the view's own scale (`SkyView`'s `altLines`), the same at any site.
//
// THE PATCH CARD AND THE STATUS ROW ARE IN THE SCAN since the H4 integration
// (#544). H4-USKY left the patch card out: with no coordinates its patch is
// null, and it printed the fallback "BELOW HORIZON", "0 of 0 targets clear and
// in reach" and "aim above the horizon" for any null patch, as if the view were
// aimed below the horizon, beside the status row's "Show 0 suggested targets".
// The card is now told why there is no patch (`placementNote`) and the hub
// passes no count when nothing was placed, and section 4 renders both cards
// alone as the control: a placed sky aimed below the horizon still gets its
// count, its "BELOW HORIZON" and its reason.
//
// MUTATION RECORD: see the block at the foot of this file.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
{
  const { registerHooks } = await import("node:module");
  registerHooks?.({
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
win.HTMLCanvasElement.prototype.getContext = function () { return null; };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

// ------------------------------------------------------------------- clocks
const NOW_UP = Date.UTC(2026, 8, 10, 22, 0, 0);
const NOW_DOWN = Date.UTC(2026, 8, 10, 12, 0, 0);
let NOW = NOW_UP;
const realNow = Date.now;
Date.now = () => NOW;

// ------------------------------------------------------------- fetch double
const NO_SITE_MSG =
  "no observing site is saved, so tonight's windows cannot be computed - save the site in Settings";
const PLACEHOLDER = { latitude: 0, longitude: 0 };
const DEFAULT_SITE = { name: "", ...PLACEHOLDER, elevation_m: 0, is_default: true, horizon_min_deg: 15 };
/** The control: the SAME coordinates, saved. Only `is_default` differs. */
const SAVED_SITE = { name: "Equator test", ...PLACEHOLDER, elevation_m: 0, is_default: false, horizon_min_deg: 15 };

let siteNow: any = DEFAULT_SITE;
const asks: string[] = [];

const M31_PICK = {
  id: "m31", name: "M31", type: "Galaxy", ra_hours: 0.7123, dec_deg: 41.269, mag: 3.4, size_arcmin: 190,
  difficulty: "easy", surface_brightness: 21, difficulty_source: "heuristic",
  max_alt: 50, transit_unix: null, best_window: null, moon_sep_deg: 80,
  never_rises_above_limit: false, score: 1,
};

/** A body the SERVER placed (it carries its own alt/az), for section 3. */
const JUPITER = {
  id: "Jupiter", name: "Jupiter, the largest planet.", type: "Planet", kind: "solar_system",
  ra_hours: 7.1, dec_deg: 22.5, mag: -2.2, size_arcmin: 0.7, alt: 40, az: 120,
};
let jupiterPlaced = false;

/** What `view.site_precise` leaves a role without it: the coordinates are
 *  STRIPPED, not nulled, and `is_default` stays (api/app.py). */
const STRIPPED_SITE = { is_default: false, horizon_min_deg: 15 };

function res(status: number, data: unknown) {
  return { ok: status < 400, status, statusText: status < 400 ? "OK" : "Conflict", json: async () => data };
}
const noSite = () => res(409, { detail: { detail: NO_SITE_MSG, code: "no_site" } });

const g = globalThis as any;
g.fetch = async (url: any) => {
  const u = String(url);
  asks.push(u);
  const saved = siteNow.is_default !== true;
  if (u.includes("/api/survey/pack")) {
    return res(200, { present: false, slug: "p", survey: "s", order: null, bytes: null, tile_count: null, fetched_at: null, fetching: null });
  }
  if (u.includes("/api/cloudmap/dome")) {
    return res(200, { enabled: false, observed_at: null, stale: true, alt_start: 6, alt_step: 6, az_step: 10, rows: [] });
  }
  if (u.includes("/api/cloudmap")) {
    return res(200, { enabled: false, platform: "G18", observed_at: null, age_s: null, stale: true, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  // The server's own answers for each site: a default site is refused a list
  // and a night (#24); a saved one is ranked and windowed.
  if (u.includes("/api/catalog/tonight")) {
    return saved ? res(200, { date: "2026-09-10", site_is_default: false, picks: [M31_PICK] }) : noSite();
  }
  if (u.includes("/api/visibility")) {
    if (!saved) return noSite();
    return res(200, {
      date: "2026-09-10", transit_unix: NOW / 1000 + 3600, transit_alt: 48, transit_in_daylight: false,
      dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
      darkness_kind: "astronomical", samples: [],
      moon: { illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90, separation_deg: 80, rise_unix: null, set_unix: null },
      best_window: null, alt_limit_deg: 15, never_rises_above_limit: false,
    });
  }
  if (u.includes("/api/catalog/region")) {
    // A catalogue row exactly where the finder asked, with no server alt/az:
    // anything that asks gets something to place at the centre of its view,
    // up or down, at whatever coordinates it places with.
    const q = new URL(u, "http://local").searchParams;
    return res(200, {
      rows: [{
        id: "decoy", label: "DECOY", kind: "dso", type: "Open Cluster",
        ra_hours: Number(q.get("ra_hours")), dec_deg: Number(q.get("dec_deg")),
        mag: 6, size_arcmin: 20, constellation: "", describe: "the row under the reticle", alias: null,
      }],
      truncated: false, catalog_degraded: false, notes: [],
    });
  }
  // The server withholds its alt/az from a default site (#24), so nothing is
  // placed for the finder by the rig there. Section 3's role, on a saved site,
  // is given the rig's own placement of Jupiter.
  if (u.includes("/api/catalog?q=planet")) {
    return res(200, { results: jupiterPlaced ? [JUPITER] : [], notes: [] });
  }
  if (u.includes("/api/catalog?q=")) return res(200, { results: [], notes: [] });
  if (u.includes("/api/site")) return res(200, { site: siteNow, version: 3 });
  return res(200, {});
};

for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLVideoElement", "HTMLCanvasElement", "HTMLImageElement", "Image",
  "Element", "SVGElement", "Node", "Event", "CustomEvent",
  "MouseEvent", "KeyboardEvent", "PointerEvent", "WheelEvent", "localStorage", "getComputedStyle",
  "matchMedia", "requestAnimationFrame", "cancelAnimationFrame", "WebSocket",
  "location", "history", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { resetRouterCacheForTests } = await import("../../../router");
const { altAzOf } = await import("../../../../lib/altaz");
const { AIM_SETTLE_MS, NO_COORDS_NOTE, NO_SITE_NOTE } = await import("../finder/model");
const { SkyHub } = await import("../SkyHub");
const { PatchCard, PATCH_BELOW_HORIZON, PATCH_HIDDEN_FOR_ROLE, PATCH_NEEDS_SITE } =
  await import("../cards/PatchCard");
const { StatusRow } = await import("../cards/StatusRow");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
}

const container = win.document.getElementById("root") as any;
const byId = (id: string): any => container.querySelector(`[data-testid="${id}"]`);
const wait = async (ms: number): Promise<void> => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};
async function until(what: string, pred: () => boolean, budgetMs = 9000): Promise<void> {
  const t0 = performance.now();
  while (performance.now() - t0 < budgetMs) {
    if (pred()) return;
    await wait(25);
  }
  throw new Error(`timed out after ${budgetMs}ms waiting for ${what}`);
}

/** Every altitude the hub prints: a number and a degree sign. */
const ALTITUDE = /-?\d+(?:\.\d+)?\s*°/;
/** Every status word a placement produces (`decorate`, the lock note, the
 *  reach strip's empty verdict). "clear -" on the status row is the WEATHER's
 *  absent reading, lower case, and is not one of them. */
const VERDICT = /CLEAR ·|CLOUD \d|BEHIND HORIZON|BELOW HORIZON|OBSTRUCT|UP · cloud|LOCKED|behind your horizon/;
/** Every count a placement produces: the patch card's "N of M targets" line
 *  and the status row's number (#544). */
const COUNT = /\d+\s*of\s*\d+\s*targets|Show\s*\d+\s*suggested/;

/** The hub's text, minus the two places named in the header. The grid's
 *  rulings carry no hook of their own, so they are found as what they are: the
 *  label span inside each dashed ruling of the MAP grid. */
function claims(): string {
  const clone = byId("hub-sky")?.cloneNode(true) as any;
  if (clone == null) return "";
  for (const el of clone.querySelectorAll(
    '[data-sky-readout], [data-sky-view] div[style*="dashed"] > span',
  )) el.remove();
  return clone.textContent as string;
}

const OPERATOR = {
  role: "operator",
  caps: ["view.status", "view.weather", "view.site_derived", "view.site_precise", "control.capture"],
  name: "tester",
};

function seed(site: any, principal: any = OPERATOR): void {
  siteNow = site;
  act(() => {
    useStore.setState({
      principal,
      site,
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
      weather: null,
    } as never);
  });
}

/** Mount the hub fresh in MAP (the finder's own box, where its markers are
 *  drawn), and let both catalogue sources and the settle timer run out, so
 *  anything that would be placed has had every chance to arrive. */
async function mount(at: number, site: any, principal: any = OPERATOR): Promise<() => void> {
  NOW = at;
  asks.length = 0;
  win.localStorage.clear();
  win.localStorage.setItem("astrodeck-next-sky-mode", "map");
  seed(site, principal);
  win.location.hash = "#/sky";
  resetRouterCacheForTests();
  const root = createRoot(container);
  await act(async () => { root.render(createElement(SkyHub)); });
  await until("the finder box", () => container.querySelector("[data-sky-view]") != null);
  await until("tonight's list to be asked", () => asks.some((u) => u.includes("/api/catalog/tonight")));
  await wait(AIM_SETTLE_MS + 600);
  return () => act(() => { root.unmount(); });
}

// ================================================================ premises
await testAsync("premise: M31 is up at the placeholder at one hour and down at the other", () => {
  const up = altAzOf(M31_PICK.ra_hours, M31_PICK.dec_deg, 0, 0, NOW_UP / 1000).altDeg;
  const down = altAzOf(M31_PICK.ra_hours, M31_PICK.dec_deg, 0, 0, NOW_DOWN / 1000).altDeg;
  assert(up > 15, `M31 at the UP hour is at ${up.toFixed(1)} deg, not above the 15 degree floor`);
  assert(down < 0, `M31 at the DOWN hour is at ${down.toFixed(1)} deg, not below the horizon`);
});

// ================================== 1. a DEFAULT site, at two hours of the day
for (const [at, hour] of [[NOW_UP, "UP"], [NOW_DOWN, "DOWN"]] as const) {
  const unmount = await mount(at, DEFAULT_SITE);

  await testAsync(`${hour} hour, default site: the finder says why its sky is empty`, () => {
    eq(byId("sky-placement-note")?.textContent ?? null, NO_SITE_NOTE,
      "the placement note is missing or is not the no-site sentence:");
  });

  await testAsync(`${hour} hour, default site: no row is placed, so no altitude tag is drawn`, () => {
    const markers = [...container.querySelectorAll("[data-sky-marker]")] as any[];
    eq(markers.length, 0, `rows were placed at the placeholder (${markers.map((m) => m.getAttribute("aria-label")).join(" | ")}):`);
    const region = asks.filter((u) => u.includes("/api/catalog/region"));
    eq(region.length, 0, "the finder asked the catalogue about a placeholder sky:");
  });

  await testAsync(`${hour} hour, default site: nothing is counted`, () => {
    // No number at all (#544): until the H4 integration this read "Show 0
    // suggested targets", a 0 for a sky nobody placed, and this case asked
    // only that the number was 0.
    const pill = byId("sky-suggested");
    assert(pill != null, "precondition: the status row's suggested-targets pill is there");
    const count = pill.textContent ?? "";
    assert(!/\d/.test(count), `the status row counts targets on an unplaced sky: "${count}"`);
    const patch = byId("sky-patch");
    assert(patch != null, "precondition: the patch card is up (nothing is locked), so its count can be read");
    const said = patch.textContent ?? "";
    assert(!COUNT.test(said), `the patch card counts targets on an unplaced sky: "${said}"`);
    const strip = byId("sky-reach");
    assert(strip == null,
      `a reach strip (chips, or the empty strip's verdict) is on an unplaceable sky: "${strip?.textContent}"`);
  });

  await testAsync(`${hour} hour, default site: no verdict and no altitude anywhere on the hub`, () => {
    eq(container.querySelector("[data-sky-locknote]")?.textContent ?? null, "SWEEP",
      "the reticle gave a verdict at the placeholder:");
    const card = byId("sky-lock");
    assert(card == null, `a lock card is up, so something was locked at the placeholder: "${card?.textContent}"`);
    const text = claims();
    assert(text.length > 200, `precondition: the hub rendered almost nothing to scan (${text.length} chars)`);
    const alt = text.match(ALTITUDE);
    assert(alt == null, `an altitude is printed at the placeholder: "${alt?.[0]}" in "${text}"`);
    const verdict = text.match(VERDICT);
    assert(verdict == null, `a verdict is printed at the placeholder: "${verdict?.[0]}" in "${text}"`);
    assert(byId("sky-patch") != null, "precondition: the patch card is in the scan");
    eq(byId("sky-patch-image")?.getAttribute("title") ?? null, PATCH_NEEDS_SITE,
      "IMAGE THIS PATCH's reason on a default site:");
  });

  unmount();
}

// ======================= 2. the control: the same coordinates, SAVED, ranked
for (const [at, hour] of [[NOW_UP, "UP"], [NOW_DOWN, "DOWN"]] as const) {
  const unmount = await mount(at, SAVED_SITE);

  await testAsync(`control, ${hour} hour: a saved site at the same coordinates IS placed, counted and judged`, async () => {
    await until("a marker", () => container.querySelector("[data-sky-marker]") != null);
    const note = byId("sky-placement-note");
    assert(note == null, `the placement note shows on a saved site with coordinates: "${note?.textContent}"`);
    const text = claims();
    assert(ALTITUDE.test(text), `the scan found no altitude on a placed sky, so it cannot see one: "${text}"`);
    assert(VERDICT.test(text), `the scan found no verdict on a placed sky, so it cannot see one: "${text}"`);
    const count = byId("sky-suggested")?.textContent ?? "";
    assert(/Show\s*[1-9]\d*\s*suggested/.test(count), `the status row counts nothing on a placed sky: "${count}"`);
  });

  unmount();
}

// ============ 3. a role without the site's coordinates: the rig's placements
// The other way to have no coordinates. Such a role may still be RANKED
// (`view.site_derived` without `view.site_precise`), and the server places the
// bodies it sends with their own alt/az, so those rows are drawn and counted -
// but no window is walked for them, because the walk needs the site's latitude
// and sidereal time, and walking at the placeholder's 0,0 printed a window for
// somewhere else (#508's class, in the rows).
{
  jupiterPlaced = true;
  const DERIVED_ONLY = { role: "custom", caps: ["view.status", "view.site_derived"], name: "ranked, not sited" };
  const unmount = await mount(NOW_UP, STRIPPED_SITE, DERIVED_ONLY);

  await testAsync("no coordinates for this role: the rig's own placement is drawn, and no window is walked for it", async () => {
    await until("Jupiter's marker", () => container.querySelector('[data-sky-marker="Jupiter"]') != null);
    assert(/^Precise location is hidden/.test(byId("sky-placement-note")?.textContent ?? ""),
      `the placement note is not the hidden-coordinates sentence: "${byId("sky-placement-note")?.textContent}"`);
    eq(container.querySelectorAll("[data-sky-marker]").length, 1,
      "a row the rig did not place was drawn (only Jupiter carries the server's alt/az):");
    const chip = byId("sky-reach")?.textContent ?? "";
    assert(/Jupiter/.test(chip) && /40°/.test(chip), `precondition: Jupiter is not in the reach strip at 40 degrees: "${chip}"`);
    assert(/40°\s*·\s*-/.test(chip), `the reach chip prints a window walked at 0,0 for a role with no coordinates: "${chip}"`);
    // The rig placed Jupiter, so it was judged and is counted: withholding a
    // count is for a sky with nothing placed at all (#544), not for a role
    // without the coordinates.
    const count = byId("sky-suggested")?.textContent ?? "";
    assert(/Show\s*1\s*suggested/.test(count), `the status row drops the rig's own count: "${count}"`);
  });

  unmount();
  jupiterPlaced = false;
}

// ====================== 4. the two cards alone: the placed-sky control (#544)
// The mounted control above never shows the patch card (the finder locks what
// it aims at), so the cards are rendered on their own here: a PLACED sky aimed
// below the horizon (no patch, no placement note) must still print its count,
// "BELOW HORIZON" and the aim-higher reason; a role without the coordinates for
// which the rig placed rows keeps that count and gets the role's reason; and
// the status row prints the number it is given, or none.
{
  const box = win.document.createElement("div");
  win.document.body.appendChild(box);
  const root = createRoot(box);
  const noop = () => {};
  const patchCard = (over: any) => createElement(PatchCard, {
    patch: null, reachCount: 3, targetCount: 5, placementNote: null,
    onImagePatch: noop, onFramePatch: noop, framed: null, onAdjustFrame: noop,
    onClearFrame: noop, onCoords: noop, imageReason: null, onExplain: noop, ...over,
  });
  const inBox = (id: string): any => box.querySelector(`[data-testid="${id}"]`);
  const read = () => ({
    count: inBox("sky-patch-count")?.textContent ?? null,
    status: inBox("sky-patch-status")?.textContent ?? null,
    reason: inBox("sky-patch-image")?.getAttribute("title") ?? null,
    sub: inBox("sky-patch-image")?.textContent ?? null,
  });

  await testAsync("control, the patch card alone: a placed sky aimed below the horizon keeps its count, its word and its reason", async () => {
    await act(async () => { root.render(patchCard({})); });
    eq(JSON.stringify(read()), JSON.stringify({
      count: "3 of 5 targets clear and in reach · tap a label to jump",
      status: "BELOW HORIZON",
      reason: PATCH_BELOW_HORIZON,
      sub: "IMAGE THIS PATCHaim above the horizon",
    }), "the placed sky below the horizon:");
  });

  await testAsync("the patch card alone: no site saved, no count, no word, the no-site reason", async () => {
    await act(async () => { root.render(patchCard({ placementNote: NO_SITE_NOTE, reachCount: null, targetCount: 0 })); });
    eq(JSON.stringify(read()), JSON.stringify({
      count: null, status: null, reason: PATCH_NEEDS_SITE, sub: "IMAGE THIS PATCHno position to send",
    }), "the unplaced sky:");
  });

  await testAsync("the patch card alone: a role without the coordinates keeps the rig's count and gets the role's reason", async () => {
    await act(async () => { root.render(patchCard({ placementNote: NO_COORDS_NOTE, reachCount: 1, targetCount: 1 })); });
    eq(JSON.stringify(read()), JSON.stringify({
      count: "1 of 1 targets clear and in reach · tap a label to jump",
      status: null, reason: PATCH_HIDDEN_FOR_ROLE, sub: "IMAGE THIS PATCHno position to send",
    }), "the role without coordinates:");
  });

  await testAsync("the status row alone: the number it is given, or none", async () => {
    const row = (reachCount: number | null) =>
      createElement(StatusRow, { reachCount, clearPct: null, siteName: "Set a site", onDome: noop });
    await act(async () => { root.render(row(4)); });
    eq(inBox("sky-suggested")?.textContent ?? null, "Show 4 suggested targets ›", "a count of 4:");
    await act(async () => { root.render(row(0)); });
    eq(inBox("sky-suggested")?.textContent ?? null, "Show 0 suggested targets ›", "a counted 0 is still a 0:");
    await act(async () => { root.render(row(null)); });
    eq(inBox("sky-suggested")?.textContent ?? null, "Suggested targets ›", "nothing placed to count:");
  });

  act(() => { root.unmount(); });
}

Date.now = realNow;

// MUTATION RECORD, 2026-09-29 (H4-USKY), each mutant run in a private scratch
// copy of ui/ (the session scratchpad's H4-USKY-mut, never the shared tree,
// #254), from a byte backup restored with its sha256 checked. Output verbatim;
// a long quoted text is cut at "[...]".
//
//   MUTANT "haveCoords ignores is_default" (finder/model.ts: the
//   `&& site.is_default !== true` term dropped, the code as it was). Observed
//   ("defaultSitePlacement.test: 4/12 passed"):
//     x UP hour, default site: the finder says why its sky is empty: the placement note is missing or is not the no-site sentence: expected "No site is saved, so the finder cannot place anything on this sky - set one under the site pill.", got null
//     x UP hour, default site: no row is placed, so no altitude tag is drawn: rows were placed at the placeholder (DECOY, altitude 45°, UP · cloud -): expected 0, got 1
//     x UP hour, default site: nothing is counted: the status row counts targets at the placeholder: "Show 1 suggested targets ›"
//     x UP hour, default site: no verdict and no altitude anywhere on the hub: the reticle gave a verdict at the placeholder: expected "SWEEP", got "LOCKED · CLEAR"
//     x DOWN hour, default site: [the same four, the same values]
//   (The controls and section 3 pass under it, as they must: a saved site
//   and a stripped one are unchanged by the default-site rule.)
//
//   MUTANT "placement note hidden in the finder" (SkyHub.tsx: the
//   `sky-placement-note` card's condition `false &&`). Observed ("9/12"):
//     x UP hour, default site: the finder says why its sky is empty: the placement note is missing or is not the no-site sentence: expected "No site is saved, so the finder cannot place anything on this sky - set one under the site pill.", got null
//     x DOWN hour, default site: [the same]
//     x no coordinates for this role: the rig's own placement is drawn, and no window is walked for it: the placement note is not the hidden-coordinates sentence: "undefined"
//
//   MUTANT "NO_COORDS_NOTE for a default site" (finder/model.ts
//   `placementNote: haveCoords ? null : NO_COORDS_NOTE`). Observed ("10/12"):
//     x UP hour, default site: the finder says why its sky is empty: the placement note is missing or is not the no-site sentence: expected "No site is saved, so the finder cannot place anything on this sky - set one under the site pill.", got "Precise location is hidden for this role, so the finder can only place what the rig placed for it."
//     x DOWN hour, default site: [the same]
//
//   MUTANT "empty reach strip on an unplaceable sky" (SkyHub.tsx: the
//   strip's `placementNote == null || reachList.length > 0` condition
//   dropped). Observed ("8/12"):
//     x UP hour, default site: nothing is counted: a reach strip (chips, or the empty strip's verdict) is on an unplaceable sky: "NOTHING IS CLEAR AND UP RIGHT NOWEverything the lens shows is either behind your horizon or under cloud. Tap the lens to show more kinds, or the site pill to change where you are."
//     x UP hour, default site: no verdict and no altitude anywhere on the hub: a verdict is printed at the placeholder: "behind your horizon" in "Show 0 suggested targets ›clear -SKYDOMESet a site ›330N030SWEEP[...]"
//     x DOWN hour, default site: [the same two]
//
//   MUTANT "rows walked at the placeholder with no coordinates"
//   (finder/model.ts: `const winMin = true ? minutesAboveFloor(...)`).
//   Observed ("11/12"):
//     x no coordinates for this role: the rig's own placement is drawn, and no window is walked for it: the reach chip prints a window walked at 0,0 for a role with no coordinates: "REACHABLE NOWtap to aimJupiter40° · 0m›"

// MUTATION RECORD, 2026-09-29 (the H4 integration, #544), each mutant run in a
// private scratch copy of ui/ (the session scratchpad's H4-INTEG-mut, never the
// shared tree, #254), from a byte backup restored with its sha256 checked.
// Output verbatim; a long quoted text is cut at "[...]".
//
//   MUTANT "the status row counts an unplaced sky" (SkyHub.tsx: StatusRow's
//   `reachCount={reachCount}` made `reachCount={model.reachCount}`, the code
//   before #544). Observed ("14/16"):
//     x UP hour, default site: nothing is counted: the status row counts targets on an unplaced sky: "Show 0 suggested targets ›"
//     x DOWN hour, default site: [the same]
//
//   MUTANT "the patch card counts an unplaced sky" (SkyHub.tsx: PatchCard's
//   `reachCount={reachCount}` made `reachCount={model.reachCount}`). Observed
//   ("14/16"):
//     x UP hour, default site: nothing is counted: the patch card counts targets on an unplaced sky: "NO CATALOGUE TARGET HERE0 of 0 targets clear and in reach · tap a label to jumpIMAGE THIS PATCHno position to sendFRAME HEREsurvey imagery, no object neededRA / DEC"
//     x DOWN hour, default site: [the same]
//
//   MUTANT "BELOW HORIZON for any missing patch" (PatchCard.tsx: `status`
//   made `patch?.statusTxt ?? "BELOW HORIZON"`, the code before #544).
//   Observed ("12/16"):
//     x UP hour, default site: no verdict and no altitude anywhere on the hub: a verdict is printed at the placeholder: "BELOW HORIZON" in "Suggested targets ›clear -SKYDOMESet a site ›330N030SWEEPATLASMAPFRAMEGYRONo site is saved, so the finder cannot place anything on this sky - set one under the site pill.NO CATALOGUE TARGET HEREBELOW HORIZONIMAGE THIS PATCHno position to send[...]"
//     x DOWN hour, default site: [the same]
//     x the patch card alone: no site saved, no count, no word, the no-site reason: the unplaced sky: expected "{\"count\":null,\"status\":null,[...]", got "{\"count\":null,\"status\":\"BELOW HORIZON\",[...]"
//     x the patch card alone: a role without the coordinates keeps the rig's count and gets the role's reason: [the same status]
//
//   MUTANT "the no-site reason for every missing patch" (PatchCard.tsx:
//   `reason` made `patch == null ? PATCH_NEEDS_SITE : imageReason`, the code
//   before #544). Observed ("14/16"):
//     x control, the patch card alone: a placed sky aimed below the horizon keeps its count, its word and its reason: the placed sky below the horizon: expected "{[...]\"reason\":\"Aim above the horizon - a patch below it cannot be imaged.\"[...]", got "{[...]"
//     x the patch card alone: a role without the coordinates keeps the rig's count and gets the role's reason: the role without coordinates: expected "{[...]\"reason\":\"The site's position is hidden for this role, so this patch has no coordinates to send.\"[...]", got "{[...]"
//
//   CONTROL MUTANT "counts never printed" (SkyHub.tsx: `const reachCount:
//   number | null = null;`). Observed ("13/16"):
//     x control, UP hour: a saved site at the same coordinates IS placed, counted and judged: the status row counts nothing on a placed sky: "Suggested targets ›"
//     x control, DOWN hour: [the same]
//     x no coordinates for this role: the rig's own placement is drawn, and no window is walked for it: the status row drops the rig's own count: "Suggested targets ›"
//
//   CONTROL MUTANT "the rig's count withheld too" (SkyHub.tsx: the
//   `&& model.targets.length === 0` term dropped). Observed ("15/16"):
//     x no coordinates for this role: the rig's own placement is drawn, and no window is walked for it: the status row drops the rig's own count: "Suggested targets ›"

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`defaultSitePlacement.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
