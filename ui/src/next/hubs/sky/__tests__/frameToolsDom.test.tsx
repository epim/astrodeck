// frameToolsDom.test.tsx - the FRAME-mode tools the legacy Atlas had and the
// new hub had lost, MOUNTED and pressed (review #27, #28, #29, #30, #31, #32,
// #33, #35, #36).
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/frameToolsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// EVERY ONE OF THESE FAILED SILENTLY BEFORE. That is what makes them worth a
// mounted test rather than a unit one: nothing on the screen said the zoom was
// fixed, the survey picker absent, the deep link ignored or the catalogue half
// loaded. The app looked exactly the same as a working one.
//
//   1. `?frame=1` - Rig > Mount's FRAME button writes a framing session and
//      navigates here. `SkyHub` read `params.lock` and nothing else, so the
//      button landed on the schematic finder with FRAME off and no visible
//      change at all.
//   2. FREE-ROAM - `store.openFraming()` with no entry has always existed and
//      the new UI never called it, so survey imagery of an uncatalogued patch
//      was unreachable.
//   3. ZOOM - the only writer was SkyCanvas's wheel listener and the `+`/`-`
//      KEYS. On a phone the field of view was fixed for the whole session.
//   4. RECENTRE - after dragging off the object the only way back was leaving
//      FRAME, which also threw the mosaic and the angle away.
//   5. SURVEY / BRIGHTNESS / SCHEMATIC - three persisted preferences with no
//      control that wrote them, under a file header calling the schematic
//      fallback "the user's explicit choice".
//   6. `region.degraded` - a catalogue that half-loaded is indistinguishable
//      from empty sky without the sentence.
//   7. USE FINDER on the coordinates sheet - honest-locked on every phone
//      forever, because the one call site passed no az/alt.

/* eslint-disable @typescript-eslint/no-explicit-any */

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

// ------------------------------------------------------------- fetch double
const asked: { url: string; method: string; body: any }[] = [];
const NOW = Date.UTC(2026, 8, 10, 5, 0, 0);

function ok(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}

/** Toggled by the tests: an empty sky is what puts the PATCH card on screen
 *  (free-roam), and M31 is what puts a catalogued framing on screen. */
let regionRows: any[] = [];
let tonightPicks: any[] = [];
let catalogDegraded = false;

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

const visibilityNight = {
  date: "2026-09-10",
  transit_unix: NOW / 1000 + 7200, transit_alt: 62, transit_in_daylight: false,
  dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
  darkness_kind: "astronomical", samples: [],
  moon: { illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90, separation_deg: 80, rise_unix: null, set_unix: null },
  best_window: null, alt_limit_deg: 20, never_rises_above_limit: false,
};

/** Six panels: the bottom row peaks under the site's 20 degree limit and one
 *  panel has no answer at all, which is the pair `MosaicNightCard` exists for. */
const nightPanels = [
  { row: 0, col: 0, ra_hours: 0.6, dec_deg: 42.0, rotation_deg: 0, transit_alt: 62 },
  { row: 0, col: 1, ra_hours: 0.7, dec_deg: 42.0, rotation_deg: 0, transit_alt: 61 },
  { row: 0, col: 2, ra_hours: 0.8, dec_deg: 42.0, rotation_deg: 0, transit_alt: 60 },
  { row: 1, col: 0, ra_hours: 0.6, dec_deg: 40.5, rotation_deg: 0, transit_alt: 12 },
  { row: 1, col: 1, ra_hours: 0.7, dec_deg: 40.5, rotation_deg: 0, transit_alt: 11 },
  { row: 1, col: 2, ra_hours: 0.8, dec_deg: 40.5, rotation_deg: 0, transit_alt_error: "the ephemeris refused this point" },
];
const plainPanels = nightPanels.map(({ transit_alt: _a, transit_alt_error: _e, ...p }) => p);

const g = globalThis as any;
g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  let body: any = null;
  try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body ?? null; }
  asked.push({ url: u, method: init?.method ?? "GET", body });
  if (u.includes("/api/framing/mosaic")) {
    return ok({
      panels: body?.transit_alt === true ? nightPanels : plainPanels,
      total_fov_x_deg: 4.6, total_fov_y_deg: 2.1,
      frame_fov_x_deg: 1.68, frame_fov_y_deg: 1.12, pixel_scale_arcsec: 0.97,
    });
  }
  if (u.includes("/api/survey/pack")) return ok({ present: false, slug: "p", survey: "s", order: null, bytes: null, tile_count: null, fetched_at: null, fetching: null });
  if (u.includes("/api/cloudmap/dome")) {
    return ok({ enabled: true, observed_at: null, stale: false, alt_start: 6, alt_step: 6, az_step: 10, rows: [] });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({ enabled: false, platform: "G18", observed_at: null, age_s: null, stale: false, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: false, picks: tonightPicks });
  if (u.includes("/api/catalog/region")) {
    return ok({ rows: regionRows, truncated: false, catalog_degraded: catalogDegraded, notes: [] });
  }
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
const { SkyHub } = await import("../SkyHub");
const { CoordsSheet } = await import("../sheets/coords");
const { skyPrefs } = await import("../finder");
const { fovFromOptics } = await import("../../../../lib/framing");
const { frameFovDeg, matchCameraZoom } = await import("../frame/zoom");
const { SKY_PREF_KEYS } = skyPrefs;

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

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 500)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const q = (sel: string): any => container.querySelector(sel);
/** A popover is PORTALLED to `document.body` (`nx-popover-root`), so it is not
 *  under `#root` and a container-scoped query cannot see it. */
const anywhere = (sel: string): any => win.document.querySelector(sel);
const byId = (id: string): any => q(`[data-testid="${id}"]`);
const text = (): string => container.textContent as string;
const click = (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

const OPTICS = {
  have_optics: true, source: "config",
  focal_length_mm: 800, pixel_size_um: 3.76,
  sensor_width_px: 6248, sensor_height_px: 4176,
  image_scale_arcsec_px: 0.97, fov_w_deg: 1.68, fov_h_deg: 1.12, fov_diag_deg: 2.02,
};

function seedStore(over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      principal: {
        role: "operator", name: "tester",
        caps: ["view.status", "view.weather", "view.site_derived", "view.site_precise", "control.capture", "control.mount"],
      },
      site: { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20 },
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      framing: null,
      night: false,
      status: {
        connected: { camera: { connected: true, name: "sim camera" } },
        looping: false,
        optics: OPTICS,
        mount: { ra_hours: 5.5, dec_deg: -5.4, ra_str: "05h 30m", dec_str: "-05° 24′", alt: 40, az: 180 },
      },
      config: {
        optics: { focal_length_mm: 800, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176, auto_from_camera: false, telescope_name: "" },
        optics_computed: OPTICS,
        safety: { horizon: null },
        survey: { online_fetch: false },
        rotator: { range_type: "full", range_start_deg: 0, tolerance_deg: 1 },
      },
      weather: null,
      ...over,
    } as never);
  });
}

// =========================================== 1. free-roam over an empty patch
regionRows = [];
tonightPicks = [];
catalogDegraded = true;      // the catalogue half-loaded - it must SAY so
win.localStorage.clear();
seedStore();
win.location.hash = "#/sky";
resetRouterCacheForTests();

let root = createRoot(container);
await act(async () => { root.render(createElement(SkyHub)); });
await settle();

await testAsync("with nothing catalogued under the reticle, the patch card offers FRAME HERE", async () => {
  assert(byId("sky-patch") != null, "no patch card - the fixture is wrong, not the component");
  const btn = byId("sky-patch-frame");
  assert(btn != null, "the patch card has no free-roam door: survey imagery of an uncatalogued patch is unreachable");
  eq(btn.getAttribute("aria-disabled"), null,
    "FRAME HERE needs a POSITION, not a camera - it must not inherit the capture lock:");
});

await testAsync("FRAME HERE opens a framing session with NO target, centred on the reticle", async () => {
  click(byId("sky-patch-frame"));
  await settle();
  const f = useStore.getState().framing;
  assert(f != null, "FRAME HERE opened no framing session");
  eq(f?.target, undefined, "a free-roam session must carry no catalogue target:");
  assert(typeof f?.freeroamId === "string" && (f?.freeroamId?.length ?? 0) > 0,
    "free-roam needs a stable group id or its panels cannot be replaced on a re-frame");
  eq(f?.mosaic.overlap, 0.15, "the 25% seed must still be corrected to the printed 15%:");
  // Centred on the RETICLE, not on the mount: the mount may be parked, and the
  // reticle is what the user was looking at when they pressed the button.
  assert(Math.abs((f?.center.dec_deg ?? 0) - (-5.4)) > 1,
    "the free-roam centre was left on the mount rather than moved to the patch");
  assert(byId("sky-frame-host") != null, "FRAME HERE did not mount the survey canvas");
});

await testAsync("the catalogue's own absence is a sentence, not an empty sky", async () => {
  const note = byId("sky-region-note");
  assert(note != null, "a degraded catalogue drew nothing and said nothing");
  assert(/64 built-in objects/.test(note.textContent),
    `the note must say how much of the catalogue is left: "${note.textContent}"`);
});

await testAsync("the degraded banner names the cause the rig is actually in", async () => {
  // Online fetch is OFF and no pack is present in this fixture, so this is the
  // one case the shipped sentence was right about - and it must be reachable.
  const host = byId("sky-frame-host");
  assert(host != null, "no frame host");
  // The text is handed to SkyCanvas as a prop; asserting the prop's SOURCE is
  // what `frameModel.test.ts` does. Here the only claim is that the hub is
  // polling the pack for it, which is the half a unit test cannot see.
  assert(asked.some((a) => a.url.includes("/api/survey/pack")),
    "the pack status is never read, so the download-progress copy can never appear");
});

await testAsync("FIT OBJECT is honest-locked over a patch, MATCH CAMERA is not", async () => {
  const fit = byId("sky-fit-object");
  assert(fit != null, "no FIT OBJECT control at all");
  eq(fit.getAttribute("aria-disabled"), "true",
    "a free-roam patch has no catalogued size, so FIT must refuse and say why:");
  assert((fit.getAttribute("title") ?? "").length > 0, "a locked control with no reason");
  eq(byId("sky-match-camera")?.getAttribute("aria-disabled"), null,
    "the rig's optics are known, so MATCH CAMERA is live:");
});

await testAsync("RECENTRE names the centre it will actually use", async () => {
  const r = byId("sky-recentre");
  assert(r != null, "no RECENTRE control");
  assert(/where the mount points/.test(r.textContent),
    `a free-roam session has no object, and the button must not promise one: "${r.textContent}"`);
});

await testAsync("the zoom stepper is a real writer, in both directions", async () => {
  const before = useStore.getState().framing?.fovZoomDeg ?? 0;
  assert(before > 0, "precondition: the session seeded a field of view");
  click(q('[data-testid="sky-zoom"] [aria-label="Field of view tighter"]'));
  await settle();
  const tighter = useStore.getState().framing?.fovZoomDeg ?? 0;
  assert(tighter < before, `+ must show LESS sky: ${before} -> ${tighter}`);
  click(q('[data-testid="sky-zoom"] [aria-label="Field of view wider"]'));
  await settle();
  near(useStore.getState().framing?.fovZoomDeg ?? 0, before, 1e-9, "- returns to where it was:");
  assert(/°/.test(byId("sky-zoom-value")?.textContent ?? ""), "the readout carries its unit");
});

await testAsync("MATCH CAMERA zooms to what one frame will capture", async () => {
  click(byId("sky-match-camera"));
  await settle();
  // Derived from the SAME optics resolver the reticle and the mosaic request
  // use, not from the rounded `fov_w_deg` the fixture also carries: a test that
  // re-rounds the number would pass over a control reading a different layer.
  const f = fovFromOptics(OPTICS as never);
  near(useStore.getState().framing?.fovZoomDeg ?? 0,
    matchCameraZoom(frameFovDeg(f.fov_x_deg, f.fov_y_deg)), 1e-9,
    "the field after MATCH CAMERA (the long frame axis with the Atlas's 1.6x margin):");
});

await testAsync("RECENTRE brings a dragged free-roam session back to the mount", async () => {
  act(() => { useStore.getState().setFraming({ center: { ra_hours: 12.5, dec_deg: 70 } }); });
  await settle();
  click(byId("sky-recentre"));
  await settle();
  const c = useStore.getState().framing?.center;
  near(c?.ra_hours ?? 0, 5.5, 1e-6, "recentred RA:");
  near(c?.dec_deg ?? 0, -5.4, 1e-6, "recentred Dec:");
});

await testAsync("the survey popover writes the survey, the brightness and the mode", async () => {
  click(byId("sky-survey-btn"));
  await settle();
  assert(anywhere('[data-testid="sky-survey-pop"]') != null, "the SURVEY button opened nothing");

  // The online-only surveys are LOCKED, not hidden: a rig with the pack and no
  // internet still has to be told what DSS2 red would need.
  const red = anywhere('[data-testid="sky-survey-pick"] [data-value="CDS/P/DSS2/red"]');
  assert(red != null, "the online-only survey was hidden rather than explained");
  eq(red.getAttribute("aria-disabled"), "true", "with online fetch off, DSS2 red:");

  click(anywhere('[data-testid="sky-survey-pick"] [data-value="CDS/P/DSS2/color"]'));
  await settle();
  eq(useStore.getState().framing?.survey, "CDS/P/DSS2/color", "the picked survey reached the framing session:");

  click(anywhere('[data-testid="sky-frame-mode"]'));
  await settle();
  eq(skyPrefs.getFrameMode(), "schematic", "the schematic choice was persisted:");
  eq(win.localStorage.getItem(SKY_PREF_KEYS.frameMode), "schematic", "under its own key:");

  const slider = anywhere('[data-testid="sky-survey-bright"]');
  assert(slider != null, "no brightness control");
  // React tracks the last value it set on the node, so assigning `.value`
  // directly makes it treat the input event as a no-op. The prototype setter is
  // the house idiom for driving a controlled input in these tests.
  const setValue = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setValue.call(slider, "0.4");
    slider.dispatchEvent(new win.Event("input", { bubbles: true }));
    slider.dispatchEvent(new win.Event("change", { bubbles: true }));
  });
  await settle();
  near(skyPrefs.getSurveyBright(), 0.4, 1e-6, "the dimmed brightness was persisted:");
});

await testAsync("DONE on a free-roam framing leaves a trace, and ADJUST keeps it", async () => {
  click(q('[data-mosaic="2x1"]'));
  await settle();
  click(byId("sky-frame"));      // DONE
  await settle();

  const strip = byId("sky-patch-framed");
  assert(strip != null,
    "FRAME HERE > DONE changed nothing visible: the framing is kept, silently, and feeds the next quick session");
  assert(/2×1 mosaic/.test(strip.textContent), `the strip must name the shape: "${strip.textContent}"`);

  // The group id is the PATCH, not wherever the mount is parked. Two things
  // depend on it: `addTargetsToPlan` replaces-by-group (so re-framing the same
  // patch updates its panels), and the quick sheet recognises a patch's own
  // framing by the same string it is opened with.
  const f = useStore.getState().framing;
  const roam = f?.freeroamId ?? "";
  assert(/h/.test(roam) && /°/.test(roam),
    `the free-roam group id must name the patch, not the mount: "${roam}"`);
  assert(!roam.includes("5.50") && !roam.includes("-5.4"),
    `the group id was left on the mount position: "${roam}"`);

  // ADJUST must RESUME, not re-open: `openFraming` would throw the mosaic away.
  click(byId("sky-patch-frame-adjust"));
  await settle();
  eq(useStore.getState().framing?.mosaic.cols, 2,
    "ADJUST re-opened the session from scratch and lost the mosaic:");
  eq(useStore.getState().framing?.freeroamId, roam, "and lost its identity with it:");
  click(byId("sky-frame"));      // DONE again
  await settle();
});

await testAsync("RA / DEC carries the reticle's aim into the coordinates sheet", async () => {
  click(byId("sky-patch-coords"));
  await settle();
  const hash = String(win.location.hash);
  assert(/az=/.test(hash) && /alt=/.test(hash),
    `the one call site must hand the finder's aim over, or USE FINDER is locked forever: "${hash}"`);
});

act(() => { root.unmount(); });

// ============================================== 2. the coordinates sheet reads it
{
  const sheetRoot = createRoot(container);
  seedStore();
  act(() => {
    sheetRoot.render(createElement(CoordsSheet, { params: { az: "138.500", alt: "42.000" }, depth: 1 }));
  });
  await settle();

  await testAsync("USE FINDER is live with the aim in the params, and fills both fields", async () => {
    const btn = byId("use-finder");
    assert(btn != null, "no USE FINDER button");
    eq(btn.getAttribute("aria-disabled"), null,
      "with az and alt in the route, USE FINDER must not be locked:");
    click(btn);
    await settle();
    const ra = byId("coords-ra-input");
    const dec = byId("coords-dec-input");
    assert((ra?.value ?? "").trim().length > 0, "USE FINDER left RA empty");
    assert((dec?.value ?? "").trim().length > 0, "USE FINDER left Dec empty");
    assert(/h/.test(ra.value), `RA is not in the sexagesimal form the server parses: "${ra.value}"`);
  });

  act(() => { sheetRoot.unmount(); });
}

// ================================ 3. `#/sky?frame=1` resumes the store's session
{
  regionRows = [M31_ROW];
  tonightPicks = [M31_PICK];
  catalogDegraded = false;
  seedStore();
  // Exactly what `mount.tsx`'s FRAME button does before it navigates.
  act(() => {
    useStore.getState().openFraming({
      id: "m31", name: "M31", type: "Galaxy",
      ra_hours: 0.7123, dec_deg: 41.269, mag: 3.4, size_arcmin: 190,
    } as never);
  });
  win.location.hash = "#/sky?frame=1";
  resetRouterCacheForTests();

  root = createRoot(container);
  await act(async () => { root.render(createElement(SkyHub)); });
  await settle();

  await testAsync("`?frame=1` enters FRAME on the framing the store already holds", async () => {
    assert(byId("sky-frame-host") != null,
      "the deep link landed on the schematic finder: the framing session is unused and nothing on screen changed");
    assert(byId("sky-framing") != null, "no framing card under the survey");
    assert(/FRAMING · M31/.test(text()), `the card does not name what it resumed: "${text().slice(0, 200)}"`);
  });

  await testAsync("the param is CONSUMED, so a later render cannot re-enter FRAME", async () => {
    assert(!/frame=1/.test(String(win.location.hash)),
      `the frame param was left in the hash: "${win.location.hash}"`);
  });

  await testAsync("FIT OBJECT is live for a catalogued session and uses its extent", async () => {
    const fit = byId("sky-fit-object");
    eq(fit?.getAttribute("aria-disabled"), null, "M31 has a published size, so FIT is live:");
    click(fit);
    await settle();
    near(useStore.getState().framing?.fovZoomDeg ?? 0, 1.6 * (190 / 60), 1e-6,
      "the field after FIT OBJECT (1.6x M31's catalogued extent):");
  });

  await testAsync("RECENTRE goes back to the OBJECT when there is one", async () => {
    const r = byId("sky-recentre");
    assert(/back to the object/.test(r?.textContent ?? ""),
      `a catalogued session must promise the object: "${r?.textContent}"`);
    act(() => { useStore.getState().setFraming({ center: { ra_hours: 6, dec_deg: 12 } }); });
    await settle();
    click(r);
    await settle();
    near(useStore.getState().framing?.center.ra_hours ?? 0, 0.7123, 1e-6, "recentred RA:");
    near(useStore.getState().framing?.center.dec_deg ?? 0, 41.269, 1e-6, "recentred Dec:");
  });

  await testAsync("a mosaic says what tonight looks like across ALL of it, not just the centre", async () => {
    click(q('[data-mosaic="3x2"]'));
    await settle();
    await settle();
    const card = byId("sky-mosaic-night");
    assert(card != null, "a 3x2 mosaic said nothing about the panels that never clear the horizon");
    const below = byId("sky-mosaic-below");
    assert(below != null, "the bottom row peaks at 11-12 degrees under a 20 degree limit and nothing said so");
    assert(/never clear 20°/.test(below.textContent),
      `the sentence must name the site's own limit: "${below.textContent}"`);
    // A panel with NO altitude must produce words, never an omission.
    const missing = byId("sky-mosaic-missing");
    assert(missing != null, "a panel with no altitude went blank instead of saying why");
    assert(/the ephemeris refused this point/.test(missing.textContent),
      `the server's own cause must reach the screen: "${missing.textContent}"`);
    assert(!/—/.test(card.textContent),
      "an em-dash reached a new UI string (ARCHITECTURE non-negotiable 5)");
  });

  act(() => { root.unmount(); });
}

Date.now = realNow;

const total = passed + failed;
console.log(`frameToolsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
