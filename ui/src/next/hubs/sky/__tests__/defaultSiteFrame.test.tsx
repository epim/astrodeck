// defaultSiteFrame.test.tsx - the Sky hub with no site saved (#466), MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/__tests__/defaultSiteFrame.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. A fresh config's site is the default: `is_default: true` with
// placeholder coordinates (0 N, 0 E). Two things in the hub read it as a site:
//
//   1. The atlas FRAME was judged at the placeholder's horizon. `trackLat` took
//      the coordinates whenever they were numbers, so FRAME on M31 was refused
//      ("Aim above the horizon first") at the hours M31 is below the horizon
//      of the Gulf of Guinea, and allowed at the others.
//   2. LOCK IN FINDER aims through `#/sky?lock=<id>`, which finds the id in the
//      merged ranking, and tonight's list (its main source) is a 409 with no
//      site. So M31 was "not in tonight's list", and the finder's FRAME then
//      framed the reach list's first object under a card titled with ITS name.
//
// WHAT IS HELD, at TWO hours of the same day with the same default site - one
// with M31 up at the placeholder (22:00 UT, alt 28) and one with it down
// (12:00 UT, alt -44, where the finder's aim is clamped at -12):
//
//   * FRAME from the atlas is live at both hours; with a SAVED site at the
//     same coordinates it still refuses the hour M31 is down (the control that
//     keeps the rule from passing by never refusing anything).
//   * LOCK IN FINDER locks M31 at both hours: the lock card names it and says
//     it was not ranked, the toast names it and carries the model's sentence
//     for why the list is empty, and no refusal is said.
//   * The hold WINS over whatever catalogue row sits under the reticle. The
//     region double answers every request with a DECOY at the centre it was
//     asked about, so the finder always has a real lock of its own under the
//     reticle to be displaced by; that is asserted before the lock card is.
//   * FRAME in the finder frames M31, and the hold survives FRAME.
//   * The hold ends when the operator aims elsewhere: a drag (the view moves)
//     and a tap on the decoy at the reticle (the tracked id changes while the
//     view barely does), each on its own.
//   * With no list, a deep link to an id nobody framed is still refused, and
//     says it cannot be found without the list, with the same reason.
//   * With a list that simply does not carry M31 (a saved site), LOCK IN
//     FINDER refuses it as before: the hold is for a missing list, not for a
//     missing row.
//   * The held card's obstruction verdict: none from a default site's
//     placeholder horizon (the primary stays IMAGE with M31 at -44 there),
//     and the saved site's own horizon when a SAVED site's list failed (a
//     500): BEHIND OBSTRUCTION at the DOWN hour, IMAGE at the UP hour.
//   * A tap on empty sky in the atlas still aims the finder on a default site.
//
// MUTATION RECORD: see the block at the foot of this file.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// For a direct `npx tsx` run; run-tests.mjs loads test-css-stub.mjs itself.
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
/** M31 is UP at the placeholder (alt 28.4, az 41.4) and DOWN (alt -44.5). The
 *  premise is re-computed below with the app's own `altAzOf`, not trusted. */
const NOW_UP = Date.UTC(2026, 8, 10, 22, 0, 0);
const NOW_DOWN = Date.UTC(2026, 8, 10, 12, 0, 0);
let NOW = NOW_UP;
const realNow = Date.now;
Date.now = () => NOW;

// ------------------------------------------------------------- fetch double
/** The server's own 409 detail for a night asked of no site (visibility.py). */
const NO_SITE_MSG =
  "no observing site is saved, so tonight's windows cannot be computed - save the site in Settings";

const PLACEHOLDER = { latitude: 0, longitude: 0 };
const DEFAULT_SITE = { name: "", ...PLACEHOLDER, elevation_m: 0, is_default: true, horizon_min_deg: 15 };
/** The control: the SAME coordinates, saved. Only `is_default` differs. */
const SAVED_SITE = { name: "Equator test", ...PLACEHOLDER, elevation_m: 0, is_default: false, horizon_min_deg: 15 };

let siteNow: any = DEFAULT_SITE;
let listComputed = false;
/** Tonight's list failing for a reason that is NOT the no-site 409 (a 500),
 *  on a SAVED site: the hold still happens, and the site's horizon judges it. */
let tonightFails = false;

const M31 = {
  id: "m31", name: "M31", type: "Galaxy",
  ra_hours: 0.7123, dec_deg: 41.269, mag: 3.4, size_arcmin: 190,
};

function res(status: number, data: unknown) {
  return { ok: status < 400, status, statusText: status < 400 ? "OK" : "Conflict", json: async () => data };
}
const noSite = () => res(409, { detail: { detail: NO_SITE_MSG, code: "no_site" } });

const g = globalThis as any;
g.fetch = async (url: any) => {
  const u = String(url);
  if (u.includes("/api/survey/pack")) {
    return res(200, { present: false, slug: "p", survey: "s", order: null, bytes: null, tile_count: null, fetched_at: null, fetching: null });
  }
  if (u.includes("/api/cloudmap/dome")) {
    return res(200, { enabled: false, observed_at: null, stale: true, alt_start: 6, alt_step: 6, az_step: 10, rows: [] });
  }
  if (u.includes("/api/cloudmap")) {
    return res(200, { enabled: false, platform: "G18", observed_at: null, age_s: null, stale: true, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  if (u.includes("/api/catalog/tonight")) {
    if (tonightFails) return res(500, { detail: "Internal Server Error" });
    return listComputed ? res(200, { date: "2026-09-10", site_is_default: false, picks: [] }) : noSite();
  }
  if (u.includes("/api/visibility")) {
    if (!listComputed && !tonightFails) return noSite();
    return res(200, {
      date: "2026-09-10", transit_unix: NOW / 1000 + 3600, transit_alt: 48, transit_in_daylight: false,
      dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
      darkness_kind: "astronomical", samples: [],
      moon: { illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90, separation_deg: 80, rise_unix: null, set_unix: null },
      best_window: null, alt_limit_deg: 15, never_rises_above_limit: false,
    });
  }
  if (u.includes("/api/catalog/region")) {
    // THE DECOY: one catalogue row exactly where it was asked about, with no
    // server alt/az, so the finder places it with its own arithmetic at the
    // very centre of whatever view asked. A real lock under the reticle, for
    // the hold to be displaced by.
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
const { nav, resetRouterCacheForTests } = await import("../../../router");
const { altAzOf } = await import("../../../../lib/altaz");
const { DEFAULT_OVERLAP } = await import("../../../../lib/framing");
const {
  SkyHub, FRAME_NEEDS_AIM, HELD_STATUS, LOCK_WAIT_MS, lockHeld, lockNoList, lockNotListed,
} = await import("../SkyHub");

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
const settle = async (): Promise<void> => { await wait(0); await wait(400); await wait(0); };
async function until(what: string, pred: () => boolean, budgetMs = 9000): Promise<void> {
  const t0 = performance.now();
  while (performance.now() - t0 < budgetMs) {
    if (pred()) return;
    await wait(25);
  }
  throw new Error(`timed out after ${budgetMs}ms waiting for ${what}`);
}
const click = async (el: any, what: string): Promise<void> => {
  assert(el != null, `no ${what} to press - the fixture is wrong, not the component`);
  await act(async () => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
  await settle();
};
const toasts = (): Array<{ title?: string; detail?: string; level?: string }> =>
  useStore.getState().toasts as any;
const lockName = (): string | null => byId("sky-lock-name")?.textContent ?? null;
const locked = (id: string): boolean => byId(id)?.getAttribute("aria-disabled") === "true";
/** Which of `lockCta`'s cases the lock card's primary is drawing. */
const ctaKind = (): string | null => byId("sky-cta")?.getAttribute("data-cta") ?? null;

/** A tap on the atlas canvas, `skyAtlasDom`'s own helper: the canvas is given
 *  a 400 px box so the tap's client coordinates mean a point on the sky. */
function tapCanvas(x: number, y: number, size = 400): void {
  const box = container.querySelector('[role="application"]') as any;
  assert(box != null, "tapCanvas: no sky canvas on screen - the fixture is wrong");
  box.getBoundingClientRect = () => ({
    left: 0, top: 0, right: size, bottom: size, width: size, height: size, x: 0, y: 0,
    toJSON() { /* the shape DOMRect has */ },
  });
  const at = { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 };
  act(() => {
    box.dispatchEvent(new win.MouseEvent("pointerdown", at));
    box.dispatchEvent(new win.MouseEvent("pointerup", at));
  });
}

function m31Framing() {
  return {
    target: { ...M31 },
    center: { ra_hours: M31.ra_hours, dec_deg: M31.dec_deg },
    rotation_deg: 0,
    survey: "CDS/P/DSS2/color",
    stretch: "linear",
    fovZoomDeg: 60,
    mosaic: { rows: 1, cols: 1, overlap: DEFAULT_OVERLAP },
    panels: [],
  };
}

function seed(site: any): void {
  siteNow = site;
  act(() => {
    useStore.setState({
      principal: {
        role: "operator",
        caps: ["view.status", "view.weather", "view.site_derived", "view.site_precise", "control.capture"],
        name: "tester",
      },
      site,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      framing: m31Framing(),
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

/** Mount the hub fresh, in ATLAS (a phone that has never chosen), and wait for
 *  the ranking to have answered: with no list, the atlas prints the model's
 *  sentence for why; with one, the decoy arrives as a pill. */
async function mount(at: number, site: any, computed: boolean): Promise<{ unmount: () => void; why: string | null }> {
  NOW = at;
  listComputed = computed;
  win.localStorage.clear();
  seed(site);
  win.location.hash = "#/sky";
  resetRouterCacheForTests();
  const root = createRoot(container);
  await act(async () => { root.render(createElement(SkyHub)); });
  await settle();
  assert(byId("sky-atlas") != null, "precondition: the hub did not open in ATLAS - the fixture is wrong");
  let why: string | null = null;
  if (!computed) {
    await until("the atlas to say why tonight's list is empty", () =>
      [...container.querySelectorAll(".nx-atlas-note")].some((n: any) => /rank tonight/.test(n.textContent)));
    why = ([...container.querySelectorAll(".nx-atlas-note")] as any[])
      .map((n) => n.textContent as string).find((t) => /rank tonight/.test(t)) ?? null;
  }
  return { unmount: () => act(() => { root.unmount(); }), why };
}

/** LOCK IN FINDER, then give the lock card time to arrive. A card that never
 *  does is left for the test's own assertion to report, rather than thrown
 *  here outside any test, where it would end the file instead of failing a
 *  case. */
async function lockInFinder(): Promise<void> {
  await click(byId("atlas-lock-in-finder"), "LOCK IN FINDER");
  await until("the lock card", () => lockName() === "M31", 4000).catch(() => { /* graded by the case */ });
}

/** The finder's own lock (the model's, not the hub's) is the decoy under the
 *  reticle: the premise every "the hold wins" assertion stands on. */
async function decoyUnderReticle(): Promise<void> {
  await until("the decoy under the reticle", () =>
    container.querySelector('[data-sky-marker="decoy"]') != null
    && (container.querySelector("[data-sky-locknote]")?.textContent ?? "SWEEP") !== "SWEEP");
}

async function holdsM31(why: string | null, when: string): Promise<void> {
  eq(lockName(), "M31", `${when}: LOCK IN FINDER did not lock the object the atlas handed over:`);
  assert((byId("sky-lock")?.textContent ?? "").includes(HELD_STATUS),
    `${when}: the held lock's card does not say it was not ranked: "${byId("sky-lock")?.textContent}"`);
  const refusals = toasts().filter((t) => t.title === lockNotListed("m31") || t.title === lockNoList("m31"));
  eq(refusals.length, 0, `${when}: the object was refused as well as locked:`);
  const said = toasts().filter((t) => t.title === lockHeld("M31"));
  eq(said.length, 1, `${when}: LOCK IN FINDER did not say it locked M31 from the catalogue, once:`);
  assert(why != null && /no observing site is saved/.test(why), `${when}: precondition: the model's reason "${why}"`);
  eq(said[0].detail, why, `${when}: the toast does not carry the model's own sentence for why the list is empty:`);
  assert(!/lock=/.test(win.location.hash), `${when}: the lock param was left in the URL: ${win.location.hash}`);
  assert((byId("sky-mode")?.textContent ?? "").includes("MAP"), `${when}: the finder did not come back on the MAP`);
}

// ================================================================ premises
await testAsync("premise: M31 is up at the placeholder at one hour and below the finder's clamp at the other", () => {
  const up = altAzOf(M31.ra_hours, M31.dec_deg, 0, 0, NOW_UP / 1000).altDeg;
  const down = altAzOf(M31.ra_hours, M31.dec_deg, 0, 0, NOW_DOWN / 1000).altDeg;
  assert(up > 15, `M31 at the UP hour is at ${up.toFixed(1)} deg, not up`);
  assert(down < -12, `M31 at the DOWN hour is at ${down.toFixed(1)} deg, not below the finder's -12 clamp`);
});

// ====================================== 1. the UP hour, default site, no list
{
  const m = await mount(NOW_UP, DEFAULT_SITE, false);

  await testAsync("UP hour, default site: FRAME from the atlas is live", () => {
    eq(locked("atlas-frame"), false, "the atlas FRAME is locked on a default site:");
    eq(locked("sky-frame"), false, "the toolbar's FRAME in ATLAS is locked on a default site:");
  });

  await lockInFinder();
  await testAsync("UP hour: LOCK IN FINDER locks M31 with no list, and says why the list is empty", async () => {
    await holdsM31(m.why, "UP hour");
  });

  await testAsync("UP hour: the decoy under the reticle does not take the lock", async () => {
    await decoyUnderReticle();
    eq(lockName(), "M31", "a catalogue row under the reticle displaced the held object:");
  });

  await testAsync("UP hour: the finder's FRAME frames M31, and the hold survives FRAME", async () => {
    await click(byId("sky-frame"), "the toolbar's FRAME");
    assert(byId("sky-frame-host") != null, "FRAME did not open");
    eq(useStore.getState().framing?.target?.id ?? null, "m31", "FRAME framed another object:");
    await settle();
    eq(lockName(), "M31", "FRAME dropped the hold, so the card below it names another object:");
  });

  m.unmount();
}

// ================ 1b. the UP hour, default site: a tap on empty sky still aims
// The finder PLACES the sky at the placeholder whatever `is_default` says, so
// `aimAtSky` converts with that pair (`placeLat`), not the claim pair
// (`trackLat`, null here): a default site still has a finder to aim. The
// atlas's reticle readout is `model.patch`, which is derived from the finder's
// view, so it carries the tapped position only if the aim reached the finder.
{
  const m = await mount(NOW_UP, DEFAULT_SITE, false);
  const PATCH = { ra_hours: 23.2583, dec_deg: 10 };  // 23h 15m 30s +10

  await testAsync("UP hour, default site: a tap on empty sky aims the finder there", async () => {
    const alt = altAzOf(PATCH.ra_hours, PATCH.dec_deg, 0, 0, NOW_UP / 1000).altDeg;
    assert(alt > 15, `precondition: the patch is at ${alt.toFixed(1)} deg at the placeholder, not up`);
    act(() => {
      useStore.setState({
        framing: { ...(useStore.getState().framing as any), center: { ...PATCH }, fovZoomDeg: 10 },
      } as never);
    });
    await settle();
    const before = byId("atlas-aim")?.textContent ?? "";
    assert(!/reticle\s+23h\s*15m/.test(before), `precondition: the reticle is already on the patch: "${before}"`);
    tapCanvas(200, 200);           // dead centre: the tangent point itself
    await settle();
    const readout = byId("atlas-aim")?.textContent ?? "";
    assert(/reticle\s+23h\s*15m/.test(readout),
      `a tap on bare sky did not move the finder's aim on a default site: "${readout}"`);
  });

  m.unmount();
}

// ==================================== 2. the DOWN hour, default site, no list
{
  const m = await mount(NOW_DOWN, DEFAULT_SITE, false);

  await testAsync("DOWN hour, default site: FRAME from the atlas is live (the placeholder's horizon is not the operator's)", () => {
    eq(byId("atlas-frame")?.getAttribute("title") ?? null, null, "the atlas FRAME carries a refusal on a default site:");
    eq(locked("atlas-frame"), false, "the atlas FRAME is locked on a default site:");
    eq(locked("sky-frame"), false, "the toolbar's FRAME in ATLAS is locked on a default site:");
  });

  await lockInFinder();
  await testAsync("DOWN hour: LOCK IN FINDER locks M31 with no list, and says why the list is empty", async () => {
    await holdsM31(m.why, "DOWN hour");
  });

  // M31 is 44 degrees under the PLACEHOLDER's horizon here. "Behind the
  // horizon" is a claim about the operator's sky, and a default site is no
  // site, so the held card makes none - the same rule that keeps FRAME live.
  await testAsync("DOWN hour: a default site's placeholder horizon gives the held object no obstruction verdict", () => {
    eq(lockName(), "M31", "precondition: M31 held");
    eq(ctaKind(), "image", "the held card's primary judged M31 against the placeholder's horizon:");
  });

  await testAsync("DOWN hour: the decoy under the clamped reticle does not take the lock", async () => {
    await decoyUnderReticle();
    eq(lockName(), "M31", "a catalogue row under the reticle displaced the held object:");
  });

  await testAsync("a drag of the finder ends the hold", async () => {
    const box = container.querySelector("[data-sky-view]");
    assert(box != null, "no finder box to drag - the fixture is wrong");
    await act(async () => {
      box.dispatchEvent(new win.WheelEvent("wheel", { deltaX: 90, deltaY: 0, bubbles: true, cancelable: true }));
    });
    await settle();
    assert(lockName() !== "M31", "the finder was dragged off the held object and the card still names it");
  });

  // Held again, from the atlas, for the tracked-id half of the rule.
  await click(byId("sky-atlas-mode"), "ATLAS");
  act(() => { useStore.setState({ toasts: [] } as never); });
  await lockInFinder();
  await testAsync("a tap on the decoy at the reticle ends the hold, though the view barely moves", async () => {
    eq(lockName(), "M31", "precondition: M31 held again");
    await decoyUnderReticle();
    await click(container.querySelector('[data-sky-marker="decoy"]'), "the decoy's marker");
    eq(lockName(), "DECOY", "a tap on another object left the hold standing:");
  });

  await testAsync("with no list, a deep link to an id nobody framed is refused, and says why", async () => {
    act(() => { useStore.setState({ toasts: [] } as never); });
    act(() => { nav.go("/sky?lock=m101"); });
    await until("the refusal", () => toasts().some((t) => (t.title ?? "").includes("m101")), LOCK_WAIT_MS + 6000);
    const said = toasts().filter((t) => (t.title ?? "").includes("m101"));
    eq(said.length, 1, "the refusal was said more than once:");
    eq(said[0].title, lockNoList("m101"), "with no list, the refusal blames tonight's list for leaving it out:");
    eq(said[0].detail, m.why, "the refusal does not say why there is no list:");
    eq(lockName(), "DECOY", "a refused id moved the lock:");
  });

  m.unmount();
}

// ============================ 3. controls: a SAVED site, and a list computed
{
  const m = await mount(NOW_DOWN, SAVED_SITE, true);

  await testAsync("control: a saved site at the same coordinates still refuses FRAME with M31 below its horizon", async () => {
    await until("the atlas FRAME to refuse", () => locked("atlas-frame"));
    eq(byId("atlas-frame").getAttribute("title"), FRAME_NEEDS_AIM, "the refusal is not the hub's sentence:");
  });

  await testAsync("control: with a list that does not carry M31, LOCK IN FINDER refuses it and holds nothing", async () => {
    await click(byId("atlas-lock-in-finder"), "LOCK IN FINDER");
    await until("the refusal", () => toasts().some((t) => t.title === lockNotListed("m31")), LOCK_WAIT_MS + 6000);
    eq(toasts().filter((t) => t.title === lockHeld("M31")).length, 0, "M31 was held although a list was computed:");
    assert(lockName() !== "M31", "M31 is on the lock card although the list that was computed does not carry it");
  });

  m.unmount();
}

// ============== 4. a SAVED site whose list failed (a 500, not the no-site 409)
// The hold is for any list that could not be computed, so it happens here too
// - and here there IS an operator's horizon. The finder judges every row it
// places against it, and the held card must too: its primary is the one that
// sends the object to a quick session. At the DOWN hour M31 is at -44.5 under
// a 15 degree floor; at the UP hour, 28.4 above it (the control that keeps the
// rule from passing by calling everything obstructed).
tonightFails = true;
for (const [at, hour, want] of [[NOW_DOWN, "DOWN", "obstructed"], [NOW_UP, "UP", "image"]] as const) {
  const m = await mount(at, SAVED_SITE, false);
  await lockInFinder();
  await testAsync(`${hour} hour, saved site, list failed: the held card is judged against the site's horizon`, () => {
    assert(m.why != null && /Couldn't rank tonight/.test(m.why), `precondition: the model's reason "${m.why}"`);
    eq(lockName(), "M31", "LOCK IN FINDER did not hold M31 when a saved site's list failed:");
    eq(toasts().filter((t) => t.title === lockHeld("M31")).length, 1, "the hold was not said, once:");
    eq(ctaKind(), want, `the held card's primary at the ${hour} hour:`);
    eq(locked("sky-cta"), want === "obstructed", `the held card's primary lock state at the ${hour} hour:`);
  });
  m.unmount();
}
tonightFails = false;

Date.now = realNow;

// MUTATION RECORD, 2026-09-28 (S7-USKYHUB), each mutant run in a private
// scratch copy of ui/ (scratchpad/S7-USKYHUB-mut in the session scratchpad,
// never the shared tree, #254) from a byte backup restored with its sha256
// checked. Output verbatim; a quoted sentence is cut at "[...]".
//
//   MUTANT "default site read as a site" (SkyHub.tsx: `trackLat = placeLat`,
//   `trackLon = placeLon`, the is_default check gone). Observed
//   ("defaultSiteFrame.test: 12/13 passed"):
//     x DOWN hour, default site: FRAME from the atlas is live (the placeholder's horizon is not the operator's): the atlas FRAME carries a refusal on a default site: expected null, got "Aim above the horizon first - FRAME needs a target or a patch of sky to look at."
//
//   MUTANT "refuse objects not in tonight's list" (the hold's branch in the
//   `?lock=` effect guarded `false &&`). Observed ("6/13 passed"):
//     x UP hour: LOCK IN FINDER locks M31 with no list, and says why the list is empty: UP hour: LOCK IN FINDER did not lock the object the atlas handed over: expected "M31", got "DECOY"
//     x UP hour: the decoy under the reticle does not take the lock: [...] expected "M31", got "DECOY"
//     x UP hour: the finder's FRAME frames M31, and the hold survives FRAME: FRAME framed another object: expected "m31", got "decoy"
//     x DOWN hour: LOCK IN FINDER locks M31 with no list, and says why the list is empty: DOWN hour: LOCK IN FINDER did not lock the object the atlas handed over: expected "M31", got "DECOY"
//     x DOWN hour: the decoy under the clamped reticle does not take the lock: [...] expected "M31", got "DECOY"
//     x a tap on the decoy at the reticle ends the hold, though the view barely moves: precondition: M31 held again expected "M31", got null
//     x with no list, a deep link to an id nobody framed is refused, and says why: a refused id moved the lock: expected "DECOY", got null
//
//   MUTANT "a catalogue row under the reticle displaces the hold" (`lock =
//   model.lock ?? heldLock`). Observed ("7/13 passed"): the same five UP/DOWN
//   lock and decoy cases, each `expected "M31", got "DECOY"` (FRAME's
//   `expected "m31", got "decoy"`), and
//     x a tap on the decoy at the reticle ends the hold, though the view barely moves: precondition: M31 held again expected "M31", got "DECOY"
//
//   MUTANT "FRAME drops the hold" (enterFrame's `keepHold = false`).
//   Observed ("12/13 passed"):
//     x UP hour: the finder's FRAME frames M31, and the hold survives FRAME: FRAME dropped the hold, so the card below it names another object: expected "M31", got "DECOY"
//
//   MUTANT "a drag keeps the hold" (heldActive without the view comparison).
//   Observed ("12/13 passed"):
//     x a drag of the finder ends the hold: the finder was dragged off the held object and the card still names it
//   and MUTANT "the aim is never captured" (the effect that stores `view`
//   removed), the same case, the same line.
//
//   MUTANT "the hold ignores what the finder tracks" (heldActive without the
//   `model.trackId === held.entry.id` term). Observed ("11/13 passed"):
//     x a tap on the decoy at the reticle ends the hold, though the view barely moves: a tap on another object left the hold standing: expected "DECOY", got "M31"
//     x with no list, a deep link to an id nobody framed is refused, and says why: a refused id moved the lock: expected "DECOY", got "M31"
//   (This one first SURVIVED, 13/13, while HELD_VIEW_EPS_DEG was 1e-6: the
//   region request rounds its centre to six decimals, so the decoy tap moved
//   the view by ~1e-5 degrees and the view term ended the hold on its own.
//   The tolerance is now 0.01 degrees, computed against the one-pixel gesture,
//   and the tracked-id term is graded by itself.)
//
//   MUTANT "the no-list refusal blames tonight's list" (the timer's `why`
//   forced null). Observed ("12/13 passed"):
//     x with no list, a deep link to an id nobody framed is refused, and says why: with no list, the refusal blames tonight's list for leaving it out: expected "m101 cannot be found without tonight's list", got "m101 is not in tonight's list"
//
//   MUTANT (control) "hold even with a list" (the hold's `rankingError !=
//   null` term dropped). Observed ("12/13 passed"):
//     x control: with a list that does not carry M31, LOCK IN FINDER refuses it and holds nothing: timed out after 8500ms waiting for the refusal
//
//   (The counts above are of the 13-case file. The four cases below - the
//   default-site verdict pin, section 1b and section 4's two - were added by
//   the independent verifier with the fix they guard.)
//
// VERIFIER'S MUTATION RECORD, 2026-09-28 (S7-USKYHUB verify), in its own
// private scratch copy of ui/ (scratchpad/S7-USKYHUB-verify-mut), each from a
// byte backup restored with its sha256 checked. THE DEFECT: `heldTarget`
// hard-coded `obstructed: false`, and the hold fires for ANY list failure,
// so a saved site whose list answered 500 held M31 at alt -45 under a 15
// degree floor with a live IMAGE M31 primary (probed first: `data-cta`
// "image", not locked). Output verbatim:
//
//   MUTANT "a held lock is never obstructed" (the verdict `false && ...`,
//   which is the code as it was). Observed ("defaultSiteFrame.test: 16/17
//   passed"):
//     x DOWN hour, saved site, list failed: the held card is judged against the site's horizon: the held card's primary at the DOWN hour: expected "obstructed", got "image"
//
//   MUTANT "a default site's placeholder horizon judges the hold" (the
//   `siteSaved &&` term dropped). Observed ("16/17 passed"):
//     x DOWN hour: a default site's placeholder horizon gives the held object no obstruction verdict: the held card's primary judged M31 against the placeholder's horizon: expected "image", got "obstructed"
//
//   MUTANT (control) "a saved site calls every hold obstructed" (`siteSaved
//   || ...`). Observed ("15/17 passed"): the default-site pin as above, and
//     x UP hour, saved site, list failed: the held card is judged against the site's horizon: the held card's primary at the UP hour: expected "image", got "obstructed"
//
//   MUTANT "aimAtSky uses the claim pair" (`trackLat`/`trackLon` in place of
//   `placeLat`/`placeLon`, the one-pair version of this change). It survived
//   every sky test before section 1b existed (defaultSiteFrame 13/13,
//   skyAtlasDom 22/22, skyHubDom 11/11, skyLockCtaDom 8/8, skyLockParam 4/4).
//   Observed ("16/17 passed"):
//     x UP hour, default site: a tap on empty sky aims the finder there: a tap on bare sky did not move the finder's aim on a default site: "reticle 21h 19m 47s +45° 00′ 00″ · UP · cloud -"

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`defaultSiteFrame.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
