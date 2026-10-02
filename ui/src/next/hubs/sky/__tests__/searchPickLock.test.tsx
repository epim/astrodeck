// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// searchPickLock.test.tsx - a targets-sheet search pick locks a catalogue
// object with no tonight's list, and so does a role without coordinates
// (#504), MOUNTED: the sheet and the hub, side by side, joined only by the
// hash, as they are in the app.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/__tests__/searchPickLock.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `#/sky?lock=<id>` aims the finder at an id in the merged
// ranking, and with no tonight's list the ranking cannot carry a searched
// object. #466 let the hub HOLD a catalogue row instead - but only the row it
// could read off the framing session, which is LOCK IN FINDER's. The targets
// sheet's search sent `aim(id)`, the id alone, so the hub had nothing to hold
// and refused the object after `LOCK_WAIT_MS` ("M31 cannot be found without
// tonight's list"): from the Sky hub with no site, an object found through the
// search could not be locked, framed or imaged. And the hold needed the
// finder's placement coordinates, so a role that sees none fell through to the
// same refusal from LOCK IN FINDER too.
//
// WHAT IS HELD:
//   1. Default site, `GET /api/catalog/tonight` a 409: a search pick of M31
//      locks it - the lock card names it, says it was not ranked and prints
//      no altitude (nothing is placed at the placeholder, #503) - the hold is
//      said once and nothing is refused, and every key the pick put in the
//      hash is consumed with it.
//   2. The control: a SAVED site whose list is computed and does not carry
//      M31 refuses the same pick and holds nothing. The hold is for a missing
//      list, not for a row that travelled.
//   3. A role that sees no coordinates (a viewer: no `view.site_precise`, no
//      `view.site_derived`, so never ranked): both doors - LOCK IN FINDER's
//      framed row and a search pick's carried one - give a lock card with no
//      placement instead of the refusal, and its primary says the position is
//      hidden for this role rather than asking the viewer to set a site.
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
  `<!doctype html><html><body><div id="hub"></div><div id="sheet"></div></body></html>`,
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

const NOW = Date.UTC(2026, 8, 10, 22, 0, 0);
const realNow = Date.now;
Date.now = () => NOW;

// ------------------------------------------------------------- fetch double
const NO_SITE_MSG =
  "no observing site is saved, so tonight's windows cannot be computed - save the site in Settings";
const DEFAULT_SITE = { name: "", latitude: 0, longitude: 0, elevation_m: 0, is_default: true, horizon_min_deg: 15 };
const SAVED_SITE = { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20 };
/** What `view.site_precise` leaves a viewer: the keys are STRIPPED, not nulled,
 *  and `is_default` stays (api/app.py). */
const STRIPPED_SITE = { is_default: false, horizon_min_deg: 20 };

/** The search's row for M31 as `/api/catalog` builds it (`_dso_row`): the id
 *  is the designation and the name is the common name. */
const M31_ROW = {
  id: "M31", name: "Andromeda Galaxy", type: "Galaxy", kind: "dso",
  ra_hours: 0.712305, dec_deg: 41.26917, mag: 3.4, size_arcmin: 190, difficulty: "easy",
};
/** A computed list that does not carry M31, for the control. */
const OTHER_PICK = {
  id: "NGC7000", name: "North America Nebula", type: "Emission Nebula",
  ra_hours: 20.98, dec_deg: 44.5, mag: 4, size_arcmin: 120,
  difficulty: "easy", surface_brightness: 21, difficulty_source: "heuristic",
  max_alt: 60, transit_unix: null, best_window: null, moon_sep_deg: 80,
  never_rises_above_limit: false, score: 1,
};

let siteNow: any = DEFAULT_SITE;

function res(status: number, data: unknown) {
  return { ok: status < 400, status, statusText: status < 400 ? "OK" : "Conflict", json: async () => data };
}

const g = globalThis as any;
g.fetch = async (url: any) => {
  const u = String(url);
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
  if (u.includes("/api/catalog/tonight")) {
    return saved
      ? res(200, { date: "2026-09-10", site_is_default: false, picks: [OTHER_PICK] })
      : res(409, { detail: { detail: NO_SITE_MSG, code: "no_site" } });
  }
  if (u.includes("/api/visibility")) {
    if (!saved) return res(409, { detail: { detail: NO_SITE_MSG, code: "no_site" } });
    return res(200, {
      date: "2026-09-10", transit_unix: NOW / 1000 + 3600, transit_alt: 48, transit_in_daylight: false,
      dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
      darkness_kind: "astronomical", samples: [],
      moon: { illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90, separation_deg: 80, rise_unix: null, set_unix: null },
      best_window: null, alt_limit_deg: 20, never_rises_above_limit: false,
    });
  }
  // No catalogue row in view, so the ranking never carries M31 by accident.
  if (u.includes("/api/catalog/region")) return res(200, { rows: [], truncated: false, catalog_degraded: false, notes: [] });
  if (u.includes("/api/catalog?q=M%2031")) return res(200, { results: [M31_ROW], notes: [] });
  if (u.includes("/api/catalog?q=")) return res(200, { results: [], notes: [] });
  if (u.includes("/api/site")) return res(200, { site: siteNow, version: 3 });
  return res(200, {});
};

for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLVideoElement", "HTMLCanvasElement", "HTMLImageElement", "Image",
  "Element", "SVGElement", "Node", "Event", "CustomEvent",
  "MouseEvent", "KeyboardEvent", "PointerEvent", "WheelEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "requestAnimationFrame", "cancelAnimationFrame", "WebSocket",
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
const { LOCK_ROW_KEYS } = await import("../finder/targets");
const { TargetsSheet } = await import("../sheets/targets");
const {
  SkyHub, HELD_STATUS, LOCK_WAIT_MS, lockHeld, lockNoList, lockNotListed, unplacedReason,
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

const hubBox = win.document.getElementById("hub") as any;
const sheetBox = win.document.getElementById("sheet") as any;
const inHub = (id: string): any => hubBox.querySelector(`[data-testid="${id}"]`);
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
const toasts = (): Array<{ title?: string; detail?: string; level?: string }> =>
  useStore.getState().toasts as any;
const lockName = (): string | null => inHub("sky-lock-name")?.textContent ?? null;
const ctaKind = (): string | null => inHub("sky-cta")?.getAttribute("data-cta") ?? null;

const OPERATOR = {
  role: "operator",
  caps: ["view.status", "view.weather", "view.site_derived", "view.site_precise", "control.capture"],
  name: "tester",
};
const VIEWER = { role: "viewer", caps: ["view.status"], name: "guest" };

function seed(site: any, principal: any, framing: any = null): void {
  siteNow = site;
  act(() => {
    useStore.setState({
      principal,
      site,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      framing,
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

/** The hub in MAP and the targets sheet beside it, both fresh. The hub has had
 *  time for tonight's list to answer (or not be asked) before anything is
 *  picked, so the hold is decided on the list's real state. */
async function mount(site: any, principal: any, framing: any = null): Promise<() => void> {
  win.localStorage.clear();
  win.localStorage.setItem("astrodeck-next-sky-mode", "map");
  seed(site, principal, framing);
  win.location.hash = "#/sky";
  resetRouterCacheForTests();
  const hub = createRoot(hubBox);
  const sheet = createRoot(sheetBox);
  await act(async () => {
    hub.render(createElement(SkyHub));
    sheet.render(createElement(TargetsSheet, { params: {}, depth: 0 as const }));
  });
  await settle();
  await wait(1600);
  return () => act(() => { hub.unmount(); sheet.unmount(); });
}

/** Type "M 31" into the sheet's search, and press the hit. */
async function pickM31FromSearch(): Promise<void> {
  const input = sheetBox.querySelector('input[aria-label="Search the target catalog"]');
  assert(input != null, "no search field on the targets sheet - the fixture is wrong, not the component");
  await act(async () => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!.call(input, "M 31");
    input.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  await until("the search hit", () =>
    ([...sheetBox.querySelectorAll("button")] as any[]).some((b) => /Andromeda Galaxy/.test(b.textContent)));
  const hit = ([...sheetBox.querySelectorAll("button")] as any[]).find((b) => /Andromeda Galaxy/.test(b.textContent));
  await act(async () => {
    hit.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
}

/** No key of the lock link is left in the URL. */
function linkConsumed(): string | null {
  const q = win.location.hash.split("?")[1] ?? "";
  const keys = [...new URLSearchParams(q).keys()];
  const left = keys.filter((k) => k === "lock" || (LOCK_ROW_KEYS as readonly string[]).includes(k));
  return left.length === 0 ? null : `${left.join(", ")} left in ${win.location.hash}`;
}

const refusals = () => toasts().filter((t) =>
  t.title === lockNoList("M31") || t.title === lockNotListed("M31")
  || t.title === lockNoList("m31") || t.title === lockNotListed("m31"));

// ============================= 1. default site, tonight's list a 409: search
{
  const unmount = await mount(DEFAULT_SITE, OPERATOR);
  await pickM31FromSearch();
  await until("the lock card", () => lockName() === "Andromeda Galaxy", LOCK_WAIT_MS + 3000).catch(() => { /* graded below */ });

  await testAsync("default site, no list: a search pick of M31 locks it on the hub", () => {
    eq(lockName(), "Andromeda Galaxy", "the search pick did not lock the object it handed over:");
    assert((inHub("sky-lock")?.textContent ?? "").includes(HELD_STATUS),
      `the held card does not say it was not ranked: "${inHub("sky-lock")?.textContent}"`);
    eq(inHub("sky-lock-alt")?.textContent ?? null, "-", "the held card prints an altitude on a default site:");
    eq(refusals().length, 0, `the pick was refused as well: ${JSON.stringify(refusals())}`);
    eq(toasts().filter((t) => t.title === lockHeld("Andromeda Galaxy")).length, 1,
      "the hold was not said, once:");
  });

  await testAsync("default site: every key the search pick put in the hash is consumed with the lock", () => {
    eq(linkConsumed(), null, "the lock link outlived the lock:");
  });

  await testAsync("the held card is M31's: its info button opens M31's brief", async () => {
    await act(async () => {
      inHub("sky-lock-info").dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    assert(/\/brief\?id=M31$/.test(win.location.hash), `the card's brief is not M31's: ${win.location.hash}`);
  });

  unmount();
}

// ======= 2. control: a saved site whose computed list does not carry M31
{
  const unmount = await mount(SAVED_SITE, OPERATOR);
  await pickM31FromSearch();
  await testAsync("control: with a computed list that does not carry M31, the search pick is refused and nothing is held", async () => {
    await until("the refusal", () => toasts().some((t) => t.title === lockNotListed("M31")), LOCK_WAIT_MS + 6000);
    eq(toasts().filter((t) => t.title === lockHeld("Andromeda Galaxy")).length, 0,
      "M31 was held although a list was computed:");
    assert(lockName() !== "Andromeda Galaxy", "M31 is on the lock card although the computed list does not carry it");
    eq(linkConsumed(), null, "the refused link outlived the refusal:");
  });
  unmount();
}

// ============= 3. a role that sees no coordinates, through both doors
{
  // LOCK IN FINDER's door: the framed row, and the same `?lock=` it navigates.
  const framing = {
    target: { ...M31_ROW },
    center: { ra_hours: M31_ROW.ra_hours, dec_deg: M31_ROW.dec_deg },
    rotation_deg: 0, survey: "CDS/P/DSS2/color", stretch: "linear", fovZoomDeg: 60,
    mosaic: { rows: 1, cols: 1, overlap: 0.2 }, panels: [],
  };
  const unmount = await mount(STRIPPED_SITE, VIEWER, framing);
  act(() => { nav.go(`/sky?lock=${encodeURIComponent(M31_ROW.id)}`); });
  await until("the lock card", () => lockName() === "Andromeda Galaxy", LOCK_WAIT_MS + 3000).catch(() => { /* graded below */ });

  await testAsync("no coordinates for this role: LOCK IN FINDER locks the framed object, with no placement, instead of refusing it", () => {
    eq(lockName(), "Andromeda Galaxy", "the framed object was not locked for a role without coordinates:");
    eq(refusals().length, 0, `the object was refused: ${JSON.stringify(refusals())}`);
    eq(inHub("sky-lock-alt")?.textContent ?? null, "-", "the card prints an altitude this role has no coordinates for:");
    eq(inHub("sky-lock-window")?.textContent ?? null, "-", "the card prints a window this role has no coordinates for:");
    eq(ctaKind(), "unplaced", "the primary is not the hidden-position case for a role that cannot see the site:");
    eq(inHub("sky-cta")?.getAttribute("aria-disabled"), "true", "the hidden-position primary can be pressed:");
  });
  unmount();
}
{
  // The search door, for the same role.
  const unmount = await mount(STRIPPED_SITE, VIEWER);
  await pickM31FromSearch();
  await until("the lock card", () => lockName() === "Andromeda Galaxy", LOCK_WAIT_MS + 3000).catch(() => { /* graded below */ });
  await testAsync("no coordinates for this role: a search pick locks M31 with no placement, instead of refusing it", () => {
    eq(lockName(), "Andromeda Galaxy", "the search pick was not locked for a role without coordinates:");
    eq(refusals().length, 0, `the pick was refused: ${JSON.stringify(refusals())}`);
    eq(inHub("sky-lock-alt")?.textContent ?? null, "-", "the card prints an altitude this role has no coordinates for:");
    eq(linkConsumed(), null, "the lock link outlived the lock:");
  });
  unmount();
}

// ===== 3b. a role that MAY capture but cannot see the site: the reason itself
// Added by the H4-USKY verifier. The viewer cases above cannot tell WHY the
// hidden-position primary is locked: a viewer holds no `control.capture`, so
// `capture.lockedReason` locks the button before `unplacedReason` is ever
// read, and the "can be pressed" assertion passed with that branch deleted
// (see the record below). A role that may capture but is not given the site's
// coordinates reaches the branch on its own: there, nothing but the unknown
// placement stands between the reader and a primary whose press does nothing.
{
  const framing = {
    target: { ...M31_ROW },
    center: { ra_hours: M31_ROW.ra_hours, dec_deg: M31_ROW.dec_deg },
    rotation_deg: 0, survey: "CDS/P/DSS2/color", stretch: "linear", fovZoomDeg: 60,
    mosaic: { rows: 1, cols: 1, overlap: 0.2 }, panels: [],
  };
  const CAPTURE_UNSITED = { role: "custom", caps: ["view.status", "control.capture"], name: "captures, not sited" };
  const unmount = await mount(STRIPPED_SITE, CAPTURE_UNSITED, framing);
  act(() => { nav.go(`/sky?lock=${encodeURIComponent(M31_ROW.id)}`); });
  await until("the lock card", () => lockName() === "Andromeda Galaxy", LOCK_WAIT_MS + 3000).catch(() => { /* graded below */ });
  await testAsync("a role that may capture but cannot see the site: the hidden-position primary is locked with its own reason, and a press says it", async () => {
    eq(lockName(), "Andromeda Galaxy", "precondition: the framed object is held for this role");
    eq(ctaKind(), "unplaced", "the primary is not the hidden-position case:");
    eq(inHub("sky-cta")?.getAttribute("aria-disabled"), "true",
      "the hidden-position primary is live for a role whose capture is not locked, so a press does nothing:");
    act(() => { useStore.setState({ toasts: [] } as never); });
    await act(async () => {
      inHub("sky-cta").dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    const said = toasts().map((t) => t.title);
    assert(said.includes(unplacedReason("Andromeda Galaxy")),
      `a press on the hidden-position primary did not say why it is locked: ${JSON.stringify(said)}`);
  });
  unmount();
}

Date.now = realNow;

// MUTATION RECORD, 2026-09-29 (H4-USKY), each mutant run in a private scratch
// copy of ui/ (the session scratchpad's H4-USKY-mut, never the shared tree,
// #254), from a byte backup restored with its sha256 checked. Output verbatim.
//
//   MUTANT "id only" (sheets/targets.tsx `onPick`: `aim(e.id)`, the code as
//   it was). Observed ("searchPickLock.test: 3/6 passed"):
//     x default site, no list: a search pick of M31 locks it on the hub: the search pick did not lock the object it handed over: expected "Andromeda Galaxy", got null
//     x the held card is M31's: its info button opens M31's brief: Cannot read properties of null (reading 'dispatchEvent')
//     x no coordinates for this role: a search pick locks M31 with no placement, instead of refusing it: the search pick was not locked for a role without coordinates: expected "Andromeda Galaxy", got null
//
//   MUTANT "hold needs a placement" (SkyHub.tsx: the hold's branch also
//   requiring `model.place` to answer, the old `placeLat !== null` guard).
//   Observed ("2/6"):
//     x default site, no list: a search pick of M31 locks it on the hub: the search pick did not lock the object it handed over: expected "Andromeda Galaxy", got null
//     x the held card is M31's: its info button opens M31's brief: Cannot read properties of null (reading 'dispatchEvent')
//     x no coordinates for this role: LOCK IN FINDER locks the framed object, with no placement, instead of refusing it: the framed object was not locked for a role without coordinates: expected "Andromeda Galaxy", got null
//     x no coordinates for this role: a search pick locks M31 with no placement, instead of refusing it: the search pick was not locked for a role without coordinates: expected "Andromeda Galaxy", got null
//
//   MUTANT (control) "hold whenever a row travels" (the hold's `rankingError
//   != null` term dropped). Observed ("5/6"):
//     x control: with a computed list that does not carry M31, the search pick is refused and nothing is held: timed out after 8500ms waiting for the refusal
//
//   MUTANT "row keys left in the URL" (SkyHub.tsx `clearLockParam`: the loop
//   deleting `LOCK_ROW_KEYS` removed). Observed ("3/6"):
//     x default site: every key the search pick put in the hash is consumed with the lock: the lock link outlived the lock: expected null, got "lockDec, lockKind, lockName, lockRa, lockSize, lockType left in #/sky?lockDec=41.26917&lockKind=dso&lockName=Andromeda+Galaxy&lockRa=0.712305&lockSize=190&lockType=Galaxy"
//     x control: with a computed list that does not carry M31, the search pick is refused and nothing is held: the refused link outlived the refusal: [the same keys]
//     x no coordinates for this role: a search pick locks M31 with no placement, instead of refusing it: the lock link outlived the lock: [the same keys]
//
//   MUTANT "haveCoords ignores is_default" (finder/model.ts). Observed ("5/6"):
//     x default site, no list: a search pick of M31 locks it on the hub: the held card prints an altitude on a default site: expected "-", got "28°"
//
//   MUTANT "obstructed: false placeholder" (SkyHub.tsx `heldTarget`, the
//   unplaced lock's obstruction written `false`). Observed ("5/6"):
//     x no coordinates for this role: LOCK IN FINDER locks the framed object, with no placement, instead of refusing it: the primary is not the hidden-position case for a role that cannot see the site: expected "unplaced", got "image"
//
//   MUTANT "heldTarget passes 0" on the unplaced lock, and MUTANT "windowLabel
//   folds null into 0m". Each observed ("5/6"):
//     x no coordinates for this role: LOCK IN FINDER locks the framed object, with no placement, instead of refusing it: the card prints a window this role has no coordinates for: expected "-", got "0m"
//
//   MUTANT "unplaced reads SET A SITE" (cards/LockCard.tsx `lockCardCta`:
//   `siteSaved` read as false). Observed ("5/6"):
//     x no coordinates for this role: LOCK IN FINDER locks the framed object, with no placement, instead of refusing it: the primary is not the hidden-position case for a role that cannot see the site: expected "unplaced", got "site"
//
// VERIFIER'S MUTATION RECORD, 2026-09-29 (H4-USKY verify), in its own private
// scratch copy of ui/ (the session scratchpad's H4-USKY-verifyB-mut), each
// from a byte backup restored with its sha256 checked. Output verbatim.
//
//   MUTANT "unplaced primary not locked" (SkyHub.tsx `primaryReason`: the
//   `lockCtaKind === "unplaced" ? unplacedReason(lock.name)` arm written
//   `null`). It SURVIVED the six cases above ("searchPickLock.test: 6/6
//   passed"): the viewer's "the hidden-position primary can be pressed" line
//   is held true by `capture.lockedReason`, since a viewer may not capture, so
//   it never reached this branch. Section 3b was added for it. Observed
//   ("6/7 passed"):
//     x a role that may capture but cannot see the site: the hidden-position primary is locked with its own reason, and a press says it: the hidden-position primary is live for a role whose capture is not locked, so a press does nothing: expected "true", got null
//
//   MUTANT "unplaced primary locked with the obstruction sentence" (the same
//   arm written `obstructedReason(lock.name, model.siteName)`). Observed
//   ("6/7 passed"):
//     x a role that may capture but cannot see the site: the hidden-position primary is locked with its own reason, and a press says it: a press on the hidden-position primary did not say why it is locked: ["Andromeda Galaxy is below the horizon you drew for Set a site. Pick another target, or edit the horizon under the site pill."]

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`searchPickLock.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
