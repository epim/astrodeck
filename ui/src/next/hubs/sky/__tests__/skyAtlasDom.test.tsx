// skyAtlasDom.test.tsx - ATLAS mode: the fourth button on the Sky toolbar and
// the classic Atlas's only door in this UI.
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/skyAtlasDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT THIS GUARDS, and why each one is a test rather than a comment:
//
//   1. THE BUTTON IS NEVER LOCKED. Every request behind the atlas is
//      `view.status` (`/api/survey/tile/...`, `/api/survey/cutout.jpg`,
//      `/api/survey/pack`) and it needs no site, no target and no connected
//      device - so a viewer on a rig with nothing plugged in must get the same
//      sky an admin does. The fixture below signs in as a VIEWER for exactly
//      this test, because "locked for the role that can least do anything" is
//      the state a mistake here would produce.
//   2. PRESSING IT MOUNTS THE CANVAS. FRAME mode was reachable and the whole
//      pannable sky was not; a button that flips a flag nothing renders is the
//      defect shape this whole hub was reviewed for.
//   3. THE PACK POLL STOPS WITH THE SCREEN. `AtlasView.tsx:236-245` polls
//      `GET /api/survey/pack` every 2 s while the survey is degraded with no
//      online source, because that is the one state whose copy moves. A poll
//      that outlives its screen is a request the rig answers forever.
//   4. THE DEGRADED ROW NAMES THE REAL CAUSE AND OFFERS THE FIX. Review #30:
//      one sentence ("download the offline sky pack") was shown for four
//      different failures, including a rig that already HAS the pack, so the
//      reason on the row is asserted per state. The row's VISIBLE text is the
//      route to Settings > SKY PACK rather than the sentence, because
//      `SkyCanvas` already paints that sentence twice on its own - over the
//      empty sky and in its own banner under the canvas - and a third copy on
//      one 390 px screen is the other thing this test guards against.
//   5. THE ROUND TRIP BACK TO THE FINDER EXISTS. The classic Atlas could
//      search the catalogue and then had nowhere to put what it found.
//   6. FRAME REFUSES GROUND. Below the horizon there is no target to frame,
//      which is the same rule the toolbar's own FRAME button applies to the
//      reticle - and it refuses honestly, with the sentence, not natively.
//
// SABOTAGE CHECKS (verified by hand, then reverted - a red file fails `npm test`):
//   * remove `"atlas"` from `finder/prefs.ts`'s `SkyMode` union -> `tsc`
//     fails first, and with the type widened by hand the "pressing ATLAS
//     mounts the pannable sky canvas" test goes red: `getMode` rejects the
//     stored value, `model.mode` never becomes `"atlas"`, and `atlas-canvas`
//     never appears.
//   * drop the `return () => { live = false; clearInterval(id); }` cleanup from
//     `AtlasHost`'s poll effect -> "the pack poll stops when the atlas
//     unmounts" goes red with the count that kept climbing after unmount.
//   * point `LEGACY_VIEW_ROUTE.atlas` back at `"/sky"` (or `"/sky?frame=1"`)
//     -> `__tests__/legacyBridge.test.ts`'s "the classic atlas view lands on
//     ATLAS mode, not the bare Sky hub" goes red by value.
//   * make `AtlasHost` pass `DEGRADED_NO_SOURCE` unconditionally (the review
//     #30 shape) -> "the degraded row names the cause per state" goes red on
//     the pack-is-installed case.
//   * paint `degradedText` as the row's visible label -> the same test goes red
//     on the sentence that is already on screen twice.
//   * delete `if (frameOnRef.current) return;` from `SkyHub`'s `?mode=atlas`
//     effect -> "a `?mode=atlas` that is the FRAME button's own echo is
//     ignored" goes red: pressing FRAME lands the user in ATLAS instead.

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

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div><div id="root2"></div></body></html>`,
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

// ------------------------------------------------------------- fetch double
const asked: string[] = [];
const NOW = Date.UTC(2026, 8, 10, 5, 0, 0);

function ok(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}

/** Swapped by the banner tests. `present: false` with online fetch off is the
 *  only state `DEGRADED_NO_SOURCE` is the true sentence for. */
let packPayload: any = {
  present: false, slug: "p", survey: "s", order: null, bytes: null,
  tile_count: null, fetched_at: null, fetching: null,
};

// M31 with a SERVER-SUPPLIED alt/az so the finder's opening aim is exact, and
// so the `?lock=m31` round trip below has a real row to land on.
const M31_ROW = {
  id: "m31", label: "M31", kind: "dso", type: "Galaxy",
  ra_hours: 0.7123, dec_deg: 41.269, mag: 3.4, size_arcmin: 190,
  constellation: "And", describe: "Andromeda Galaxy", alias: "NGC 224",
  alt: 58, az: 64,
};
const M31_PICK = {
  id: "m31", name: "M31", type: "Galaxy",
  ra_hours: 0.7123, dec_deg: 41.269, mag: 3.4, size_arcmin: 190,
  difficulty: "easy", surface_brightness: 21, difficulty_source: "heuristic",
  max_alt: 60, transit_unix: NOW / 1000 + 7200,
  best_window: null, moon_sep_deg: 80, never_rises_above_limit: false, score: 1,
};

/** A SECOND target, and the reason it exists: the finder auto-aims at the
 *  top-ranked object, which is M31 - so a LOCK IN FINDER test that framed M31
 *  and then asserted the finder was locked on M31 would have been true before
 *  anything was pressed. The finder is aimed HERE first, so the assertion is
 *  about a lock that moved. */
const NGC7000_ROW = {
  id: "ngc7000", label: "NGC 7000", kind: "dso", type: "Emission Nebula",
  ra_hours: 20.98, dec_deg: 44.5, mag: 4, size_arcmin: 120,
  constellation: "Cyg", describe: "North America Nebula", alias: null,
  alt: 52, az: 70,
};
const NGC7000_PICK = {
  id: "ngc7000", name: "NGC 7000", type: "Emission Nebula",
  ra_hours: 20.98, dec_deg: 44.5, mag: 4, size_arcmin: 120,
  difficulty: "easy", surface_brightness: 21, difficulty_source: "heuristic",
  max_alt: 59, transit_unix: NOW / 1000 + 7200,
  best_window: null, moon_sep_deg: 80, never_rises_above_limit: false, score: 0.9,
};

const visibilityNight = {
  date: "2026-09-10",
  transit_unix: NOW / 1000 + 7200, transit_alt: 62, transit_in_daylight: false,
  dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
  darkness_kind: "astronomical", samples: [],
  moon: { illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90, separation_deg: 80, rise_unix: null, set_unix: null },
  best_window: null, alt_limit_deg: 20, never_rises_above_limit: false,
};

const g = globalThis as any;
g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  asked.push(`${init?.method ?? "GET"} ${u}`);
  if (u.includes("/api/survey/pack")) return ok(packPayload);
  if (u.includes("/api/cloudmap/dome")) {
    return ok({ enabled: true, observed_at: null, stale: false, alt_start: 6, alt_step: 6, az_step: 10, rows: [] });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({ enabled: false, platform: "G18", observed_at: null, age_s: null, stale: false, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: false, picks: [M31_PICK, NGC7000_PICK] });
  if (u.includes("/api/catalog/region")) return ok({ rows: [M31_ROW, NGC7000_ROW], truncated: false, catalog_degraded: false, notes: [] });
  if (u.includes("/api/catalog?q=")) return ok({ results: [], rows: [], notes: [] });
  if (u.includes("/api/visibility")) return ok(visibilityNight);
  if (u.includes("/api/site")) {
    return ok({
      site: { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20 },
      version: 3,
    });
  }
  return ok({});
};

for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLVideoElement", "HTMLCanvasElement", "HTMLImageElement", "Image",
  "Element", "SVGElement", "Node", "Event", "CustomEvent",
  "MouseEvent", "KeyboardEvent", "PointerEvent", "localStorage", "getComputedStyle",
  "matchMedia", "requestAnimationFrame", "cancelAnimationFrame", "WebSocket",
  "location", "history", "ResizeObserver",
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
const { resetRouterCacheForTests } = await import("../../../router");
const { SkyHub, FRAME_NEEDS_AIM } = await import("../SkyHub");
const { AtlasHost, ATLAS_NO_OBJECT, centreLabel, surveyLabel } = await import("../atlas/AtlasHost");
const { DEGRADED_NO_SOURCE, DEGRADED_PACK_PRESENT, PACK_POLL_MS } = await import("../frame/degraded");

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

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};
/** Real time, inside `act`, for the interval the poll actually runs on. */
const wait = async (ms: number) => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};

const container = win.document.getElementById("root") as any;
const container2 = win.document.getElementById("root2") as any;
const inside = (root: any, sel: string): any => root.querySelector(sel);
const byId = (id: string): any => inside(container, `[data-testid="${id}"]`);
const byId2 = (id: string): any => inside(container2, `[data-testid="${id}"]`);
const click = (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const packCalls = (): number => asked.filter((a) => a.includes("/api/survey/pack")).length;

/** A VIEWER, deliberately: `view.status` is the only capability the atlas
 *  needs, so the role that can do least is the one that proves it. */
const CAPS_VIEWER = ["view.status", "view.weather", "view.site_derived"];

function seedStore(): void {
  useStore.setState({
    principal: { role: "viewer", caps: CAPS_VIEWER, name: "tester" },
    site: { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20 },
    equipConnected: false,
    wsPhase: "up",
    toasts: [],
    framing: null,
    status: {
      connected: {},
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
}

// ===================================================== the hub, on the finder
win.localStorage.clear();
act(() => { seedStore(); });
win.location.hash = "#/sky";
resetRouterCacheForTests();

const root = createRoot(container);
await act(async () => { root.render(createElement(SkyHub)); });
await settle();

// VACUITY GUARD. Every assertion below is about a screen SWAP, and both halves
// of a swap are trivially true of a hub that rendered nothing at all. So the
// finder has to be genuinely on screen first, with its own ranked count and its
// own reticle readout, before anything asks the atlas to replace it.
test("precondition: the finder rendered a real screen, and the atlas is not it", () => {
  assert(byId("hub-sky") != null, "no hub-sky marker - the fixture is wrong, not the component");
  const pill = byId("sky-suggested");
  assert(pill != null && /Show \d+ suggested targets/.test(pill.textContent),
    `the suggested-targets pill printed no count: "${pill?.textContent}"`);
  assert(/az \d{3}° · alt/.test(container.textContent as string),
    "the finder never printed a bearing - there is no reticle to replace");
  assert(byId("sky-atlas") == null, "the atlas is mounted before anything asked for it");
  assert(byId("atlas-canvas") == null, "the atlas canvas is mounted before anything asked for it");
});

// AIM SOMEWHERE THAT IS NOT THE ATLAS'S OBJECT. The finder opens locked on the
// top-ranked target, which is M31 - the same object the atlas frames below - so
// without this the LOCK IN FINDER assertion would have been true before the
// button existed. The reach strip is the design's own "tap to aim".
click(inside(container, '[data-reach-chip="ngc7000"]'));
await settle();

test("precondition: the finder is locked on something else entirely", () => {
  eq(byId("sky-lock-name")?.textContent, "NGC 7000",
    "the reach strip did not move the lock, so the hand-off test would prove nothing:");
});

test("the ATLAS button is on the toolbar and is never locked, even for a viewer", () => {
  const b = byId("sky-atlas-mode");
  assert(b != null, "no ATLAS button on the Sky toolbar");
  assert(/ATLAS/.test(b.textContent), `the button is not labelled ATLAS: "${b.textContent}"`);
  eq(b.getAttribute("aria-disabled"), null,
    "the survey is view.status and needs no site, target or device, so nothing may lock this:");
  eq(b.getAttribute("data-locked"), null, "and it carries no locked styling either:");
  eq(b.hasAttribute("disabled"), false, "house rule: never the native attribute");
  eq(b.getAttribute("aria-pressed"), "false", "it is not the active mode yet:");
  // The three it sits beside, so a row that lost one is not read as a pass.
  assert(byId("sky-mode") != null && byId("sky-frame") != null && byId("sky-gyro") != null,
    "the ATLAS button replaced a sibling instead of joining the row");
});

await testAsync("pressing ATLAS mounts the pannable sky canvas and puts the reticle cards away", async () => {
  click(byId("sky-atlas-mode"));
  await settle();
  assert(byId("sky-atlas") != null, "ATLAS did not mount its screen");
  assert(byId("atlas-canvas") != null, "ATLAS mounted without the canvas - the flag moved, the sky did not");
  assert(byId("atlas-search") != null, "no catalog search on the atlas");
  eq(byId("sky-atlas-mode").getAttribute("aria-pressed"), "true", "the button does not report the mode:");
  // The three cards that answer about a reticle that is no longer on screen.
  assert(byId("sky-cta") == null && byId("sky-patch") == null,
    "the lock/patch card is still describing a reticle nobody can see");
  assert(byId("sky-reach") == null, "the reach strip aims a finder that is not on screen");
  // The session the canvas draws is the store's, seeded free-roam.
  const f = useStore.getState().framing;
  assert(f != null, "ATLAS mounted with no framing session for the canvas to draw");
  eq(f?.target ?? null, null, "a free-roam atlas must not invent a target:");
});

test("LOCK IN FINDER refuses honestly while nothing is framed", () => {
  const b = byId("atlas-lock-in-finder");
  assert(b != null, "no LOCK IN FINDER button on the atlas");
  eq(b.getAttribute("aria-disabled"), "true", "with no object there is nothing to lock:");
  eq(b.getAttribute("title"), ATLAS_NO_OBJECT, "and the reason is on the control:");
  eq(b.hasAttribute("disabled"), false, "house rule: never the native attribute");
  const before = win.location.hash;
  click(b);
  eq(win.location.hash, before, "a locked press must navigate nowhere:");
});

await testAsync("FRAME from the atlas is locked with FRAME_NEEDS_AIM below the horizon", async () => {
  // Dec -80 never rises at 47.6N: measured altitude -43.2 at this fixture's
  // instant, which is ground in any direction.
  act(() => {
    useStore.setState({
      framing: { ...(useStore.getState().framing as any), center: { ra_hours: 0.7123, dec_deg: -80 } },
    } as never);
  });
  await settle();
  const b = byId("atlas-frame");
  assert(b != null, "no FRAME button on the atlas");
  eq(b.getAttribute("aria-disabled"), "true", "there is no sky at the centre to frame:");
  eq(b.getAttribute("title"), FRAME_NEEDS_AIM, "and it is the hub's own sentence, not a second one:");
  // The toolbar's FRAME button says the same thing about the same centre, so
  // the two cannot disagree about whether the atlas is pointed at ground.
  eq(byId("sky-frame").getAttribute("title"), FRAME_NEEDS_AIM,
    "the toolbar's FRAME must refuse the same centre for the same reason:");
});

await testAsync("with the centre back on sky, FRAME is live again", async () => {
  act(() => {
    useStore.setState({
      framing: {
        ...(useStore.getState().framing as any),
        target: { id: "m31", name: "M31", type: "Galaxy", ra_hours: 0.7123, dec_deg: 41.269, mag: 3.4, size_arcmin: 190 },
        center: { ra_hours: 0.7123, dec_deg: 41.269 },
      },
    } as never);
  });
  await settle();
  eq(byId("atlas-frame").getAttribute("aria-disabled"), null,
    "M31 is 42 degrees up at this instant - nothing may refuse it:");
  assert(/M31/.test(byId("sky-atlas").textContent),
    "the atlas does not name the object it is centred on");
});

await testAsync("LOCK IN FINDER aims the finder at the framed object and returns to MAP", async () => {
  const b = byId("atlas-lock-in-finder");
  eq(b.getAttribute("aria-disabled"), null, "with M31 framed the button must be live:");
  // The finder is still locked on NGC 7000 (asserted before ATLAS opened -
  // the lock card is not on screen in ATLAS, which is the point of the mode).
  click(b);
  // THE HASH IS NOT ASSERTED HERE, deliberately. The hand-off navigates to
  // `#/sky?lock=m31` - the same deep link the targets sheet and the catalog
  // search aim with - and `pushHash` emits synchronously, so the hub consumes
  // and clears the parameter inside this very `act`. What is left to assert is
  // what the parameter DID, which is the contract either way.
  await settle();
  assert(byId("sky-atlas") == null, "the atlas is still up - LOCK IN FINDER did not return to the finder");
  eq(byId("sky-mode").textContent?.includes("MAP"), true, "the finder must come back on the MAP:");
  eq(byId("sky-lock-name")?.textContent, "M31",
    "the lock did not move to the object the atlas handed over:");
  assert(!/lock=/.test(win.location.hash),
    `the aim parameter was left in the URL, so the next render re-aims: ${win.location.hash}`);
});

await testAsync("a `?mode=atlas` that is the FRAME button's own echo is ignored", async () => {
  // THE ECHO IS REAL, not hypothetical. `store.openFraming()` sets
  // `view: "atlas"`, `legacyBridge` turns a change of that field into
  // `nav.go("/sky?mode=atlas")`, and `enterFrame` calls `openFraming` - so
  // pressing FRAME on this screen sends the URL out and gets it back a tick
  // later. Without the guard the FRAME button lands the user in ATLAS.
  click(byId("sky-frame"));
  await settle();
  assert(byId("sky-frame-host") != null, "precondition: FRAME did not enter FRAME mode");

  act(() => { win.location.hash = "#/sky?mode=atlas"; });
  await settle();
  assert(byId("sky-frame-host") != null,
    "the FRAME button's own echo threw the user out of FRAME and into ATLAS");
  assert(byId("sky-atlas") == null, "the atlas opened over a live framing session");
  assert(!/mode=atlas/.test(win.location.hash),
    `the parameter was left in the URL, so it fires again on the next render: ${win.location.hash}`);
});

await act(async () => { root.unmount(); });

// ============================================ the atlas host, on its own root
//
// The two tests below drive `surveyDegraded` and the pack payload directly.
// Through the hub they would depend on SkyCanvas's own fetch failing on a
// timer, which is a test that passes or fails on how fast a double answers.

function hostProps(over: Record<string, unknown>): any {
  return {
    framing: {
      target: undefined,
      center: { ra_hours: 0.7123, dec_deg: 41.269 },
      rotation_deg: 0,
      survey: "CDS/P/DSS2/color",
      stretch: "linear",
      fovZoomDeg: 2,
      mosaic: { rows: 1, cols: 1, overlap: 0.15 },
      panels: [],
    },
    optics: null,
    night: false,
    mode: "survey",
    imageBrightness: 1,
    surveyDegraded: false,
    onlineFetch: false,
    mount: null,
    rotator: null,
    pointingWhere: null,
    skyRows: [],
    region: { degraded: false, truncated: false, error: null },
    selectedObjectId: null,
    onPick: () => {},
    onPickRow: () => {},
    onCenterChange: () => {},
    onRotate: () => {},
    onZoom: () => {},
    onSurveyError: () => {},
    onSurveyLoad: () => {},
    onSurveySource: () => {},
    onLockInFinder: () => {},
    onFrame: () => {},
    frameReason: null,
    onExplain: () => {},
    ...over,
  };
}

await testAsync("the pack poll runs while the atlas is up, and stops when it unmounts", async () => {
  packPayload = { present: false, slug: "p", survey: "s", order: null, bytes: null, tile_count: null, fetched_at: null, fetching: null };
  const before = packCalls();
  const host = createRoot(container2);
  await act(async () => {
    host.render(createElement(AtlasHost, hostProps({ surveyDegraded: true, onlineFetch: false })));
  });
  await settle();
  const first = packCalls();
  assert(first > before, "the atlas never asked for the pack status at all");

  await wait(PACK_POLL_MS + 300);
  const second = packCalls();
  assert(second > first,
    `the poll is not on its interval: ${first} call(s) before the tick, ${second} after`);

  await act(async () => { host.unmount(); });
  await wait(PACK_POLL_MS + 300);
  eq(packCalls(), second,
    "the poll outlived the screen - a rig answers this request forever once ATLAS has been opened:");
});

await testAsync("the degraded row names the cause per state, offers the fix, and is absent when nothing is wrong", async () => {
  let fixed = 0;
  // 1. No pack, no online fetch: the one state DEGRADED_NO_SOURCE is true for.
  packPayload = { present: false, slug: "p", survey: "s", order: null, bytes: null, tile_count: null, fetched_at: null, fetching: null };
  let host = createRoot(container2);
  await act(async () => {
    host.render(createElement(AtlasHost, hostProps({
      surveyDegraded: true, onlineFetch: false, onSurveySource: () => { fixed += 1; },
    })));
  });
  await settle();
  const row = byId2("atlas-degraded");
  assert(row != null, "the survey is unreachable and the atlas offers nothing");
  eq(row.getAttribute("title"), DEGRADED_NO_SOURCE, "the reason for the no-source state:");
  // THE THIRD COPY. SkyCanvas paints this sentence over the empty sky and again
  // in its own banner directly above this row, so a row that repeated it would
  // be the same paragraph three times on a 390 px screen.
  assert(!row.textContent.includes(DEGRADED_NO_SOURCE),
    `the row repeats a sentence already on screen twice: "${row.textContent}"`);
  assert(/SKY PACK/.test(row.textContent),
    `the row does not name where the fix is: "${row.textContent}"`);
  click(row);
  eq(fixed, 1, "pressing the row must open Settings > SKY PACK:");
  await act(async () => { host.unmount(); });

  // 2. The pack IS installed: the reason must not be "go and download it".
  packPayload = {
    present: true, slug: "dss2color", survey: "CDS/P/DSS2/color", order: 4,
    bytes: 1234, tile_count: 3072, fetched_at: "2026-09-01T00:00:00Z", fetching: null,
  };
  host = createRoot(container2);
  await act(async () => {
    host.render(createElement(AtlasHost, hostProps({ surveyDegraded: true, onlineFetch: false })));
  });
  await settle();
  const row2 = byId2("atlas-degraded");
  assert(row2 != null, "still degraded, still nothing offered");
  assert(row2.getAttribute("title") !== DEGRADED_NO_SOURCE,
    "a rig that HAS the pack was told to download it - review #30, exactly");
  eq(row2.getAttribute("title"), DEGRADED_PACK_PRESENT, "the pack-is-installed reason:");
  await act(async () => { host.unmount(); });

  // 3. A download in flight: its numbers are the only thing on this screen that
  //    moves while a pack fetches, and they are why the pack is polled at all.
  packPayload = {
    present: false, slug: "dss2color", survey: "CDS/P/DSS2/color", order: 4,
    bytes: null, tile_count: null, fetched_at: null, fetching: { done: 412, total: 1024 },
  };
  host = createRoot(container2);
  await act(async () => {
    host.render(createElement(AtlasHost, hostProps({ surveyDegraded: true, onlineFetch: false })));
  });
  await settle();
  assert(/412\/1024/.test(byId2("atlas-degraded").textContent),
    `the download progress never reached the row: "${byId2("atlas-degraded").textContent}"`);
  await act(async () => { host.unmount(); });

  // 4. Nothing wrong: no row at all, not an empty one.
  host = createRoot(container2);
  await act(async () => {
    host.render(createElement(AtlasHost, hostProps({ surveyDegraded: false, onlineFetch: false })));
  });
  await settle();
  eq(byId2("atlas-degraded"), null, "a row with nothing to report is still a row:");
  assert(byId2("atlas-canvas") != null, "and the canvas is still there to prove the host rendered");
  await act(async () => { host.unmount(); });
});

// ------------------------------------------------------------- pure helpers

test("the where-line carries what the canvas never prints", () => {
  // The canvas draws the field width and the pixel scale in its own corners and
  // never the centre; the survey picker lives in FRAME's popover, which is not
  // mounted here. Both are the whole reason that line exists.
  eq(centreLabel(0.7123, 41.269), "00h 42m +41° 16'");
  eq(centreLabel(12.5, -5.25), "12h 30m -05° 15'");
  eq(surveyLabel("CDS/P/DSS2/color"), "DSS2 color");
  eq(surveyLabel("CDS/P/DSS2/red"), "DSS2 red");
  eq(surveyLabel("schematic"), "schematic");
});

// ---------------------------------------------------------------- report
Date.now = realNow;
const total = passed + failed;
console.log(`skyAtlasDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
if (failed > 0) process.exitCode = 1;

export { passed, failed, total };
