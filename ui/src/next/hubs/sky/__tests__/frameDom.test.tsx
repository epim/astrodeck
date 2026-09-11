// frameDom.test.tsx - FRAME mode, MOUNTED: the mosaic picker, the rotation dial,
// the rotator honesty note, and what DONE actually sends.
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/frameDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// FOUR THINGS THAT CAN ONLY BE CHECKED WITH THE SCREEN UP:
//
//   1. The overlap in the REQUEST is 0.15. `store.openFraming` seeds 0.25 (the
//      Atlas's own default) and the card prints "Panels overlap 15%", so the two
//      can disagree silently: the copy is right, the sky is wrong, and nothing on
//      screen says which. This is the sabotage target.
//   2. The rotation dial writes `store.framing`, which is the SAME slice the
//      Atlas and the sequence engine read. A dial that only moved a local
//      useState would look identical and command nothing.
//   3. The rotator note describes the rig that is actually connected. With a
//      rotator the angle is SENT; without one it is a manual instruction to the
//      human. Printing the wrong one is how a run ends up framed 30 degrees off
//      with a panel promising otherwise (AtlasView carried that exact defect
//      until 2026-07-31).
//   4. DONE keeps the framing where the user can see it: a strip on the lock card
//      and the panels still drawn on the finder. Losing it on exit would make the
//      whole mode a no-op that looked like it worked.

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
// SkyCanvas asks for a WebGL context to decide whether the tile engine can run.
// jsdom answers null for every context, which is the "no WebGL" branch - the
// `<img>` survey pipeline - and that is a perfectly real browser too.
win.HTMLCanvasElement.prototype.getContext = function () { return null; };
// jsdom has no ResizeObserver and SkyCanvas measures itself with one. A stub
// that observes nothing is the honest fixture: the canvas then renders at its
// initial (zero) size, which is what a never-laid-out element is.
win.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// ------------------------------------------------------------- fetch double
const asked: { url: string; method: string; body: any }[] = [];
const NOW = Date.UTC(2026, 8, 10, 5, 0, 0);

function ok(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}

const regionRows = [
  {
    id: "m31", label: "M31", kind: "dso", type: "Galaxy",
    ra_hours: 0.7123, dec_deg: 41.269, mag: 3.4, size_arcmin: 190,
    constellation: "And", describe: "Andromeda Galaxy", alias: "NGC 224",
    alt: 58, az: 64,
  },
];

const tonightPicks = [{
  id: "m31", name: "M31", type: "Galaxy",
  ra_hours: 0.7123, dec_deg: 41.269, mag: 3.4, size_arcmin: 190,
  difficulty: "easy", surface_brightness: 21, difficulty_source: "heuristic",
  max_alt: 60, transit_unix: NOW / 1000 + 7200,
  best_window: null, moon_sep_deg: 80, never_rises_above_limit: false, score: 1,
}];

const visibilityNight = {
  date: "2026-09-10",
  transit_unix: NOW / 1000 + 7200, transit_alt: 62, transit_in_daylight: false,
  dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
  darkness_kind: "astronomical", samples: [],
  moon: { illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90, separation_deg: 80, rise_unix: null, set_unix: null },
  best_window: null, alt_limit_deg: 20, never_rises_above_limit: false,
};

const mosaicResult = {
  panels: [
    { row: 0, col: 0, ra_hours: 0.66, dec_deg: 41.269, rotation_deg: 30 },
    { row: 0, col: 1, ra_hours: 0.76, dec_deg: 41.269, rotation_deg: 30 },
  ],
  total_fov_x_deg: 3.1, total_fov_y_deg: 1.1,
  frame_fov_x_deg: 1.68, frame_fov_y_deg: 1.12, pixel_scale_arcsec: 0.97,
};

const g = globalThis as any;
g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  let body: any = null;
  try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body ?? null; }
  asked.push({ url: u, method: init?.method ?? "GET", body });
  if (u.includes("/api/framing/mosaic")) return ok(mosaicResult);
  if (u.includes("/api/cloudmap/dome")) {
    return ok({ enabled: true, observed_at: null, stale: false, alt_start: 6, alt_step: 6, az_step: 10, rows: [] });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({ enabled: false, platform: "G18", observed_at: null, age_s: null, stale: false, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: false, picks: tonightPicks });
  if (u.includes("/api/catalog/region")) return ok({ rows: regionRows, truncated: false, catalog_degraded: false, notes: [] });
  if (u.includes("/api/catalog?q=")) return ok({ rows: [] });
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
const { SkyHub } = await import("../SkyHub");

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
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const q = (sel: string): any => container.querySelector(sel);
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

/** Patch ONLY `status`, so the framing session under test survives. */
function setRotator(present: boolean): void {
  act(() => {
    useStore.setState((s: any) => ({
      status: {
        ...s.status,
        rotator: present
          ? { name: "CAA", sky_deg: 0, mech_deg: 0, moving: false, synced: true, can_reverse: false, reverse: false }
          : undefined,
      },
    }) as never);
  });
}

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
    status: { connected: { camera: { connected: true, name: "sim camera" } }, looping: false, optics: OPTICS },
    config: {
      optics: { focal_length_mm: 800, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176, auto_from_camera: false, telescope_name: "" },
      optics_computed: OPTICS,
      safety: { horizon: null },
      survey: { online_fetch: false },
      rotator: { range_type: "full", range_start_deg: 0, tolerance_deg: 1 },
    },
    weather: null,
  } as never);
});

win.location.hash = "#/sky";
const root = createRoot(container);
await act(async () => { root.render(createElement(SkyHub)); });
await settle();

// Aim through the reach strip, so the lock is M31 and FRAME has something to
// frame. Then FRAME.
click(q('[data-reach-chip="m31"]'));
await settle();
setRotator(true);
await settle();
click(byId("sky-frame"));
await settle();

await testAsync("precondition: FRAME mode is up and names what it is framing", async () => {
  assert(byId("sky-framing") != null, "no framing card - the fixture is wrong, not the component");
  assert(/FRAMING · M31/.test(text()), `the framing card does not name the target: "${text().slice(0, 300)}"`);
  assert(byId("sky-frame-host") != null, "FRAME mode did not mount the survey canvas");
  const f = useStore.getState().framing;
  assert(f != null, "entering FRAME opened no framing session");
  eq(f?.mosaic.overlap, 0.15, "openFraming's 25% seed was not corrected to the 15% the card prints:");
  eq(byId("sky-frame")?.querySelector(".nx-iconbtn-label")?.textContent, "DONE", "the toolbar button label in FRAME mode:");
});

await testAsync("the rotation dial writes store.framing, not a local number", async () => {
  eq(useStore.getState().framing?.rotation_deg, 0, "precondition: a fresh framing starts at 0");
  click(q('[data-testid="sky-rot-dial"] [data-value="30"]'));
  await settle();
  eq(useStore.getState().framing?.rotation_deg, 30, "the dial did not reach the framing session:");
  assert(/CAMERA ROTATION · 30°/.test(text()), "the dial label did not follow the value");
});

await testAsync("with a rotator, the note says the angle is SENT and names the device", async () => {
  const note = byId("sky-rot-note")?.textContent ?? "";
  assert(/sends PA 30° to CAA/.test(note), `the rotator note is wrong: "${note}"`);
  assert(!/manual/.test(note), "a rig WITH a rotator was told to set the camera by hand");
});

await testAsync("with no rotator, the same angle becomes a manual instruction", async () => {
  setRotator(false);
  await settle();
  const note = byId("sky-rot-note")?.textContent ?? "";
  assert(/Camera angle is manual/.test(note), `the manual note is missing: "${note}"`);
  assert(/PA 30°/.test(note), "the manual note must still say WHICH angle");
  assert(!/CAA/.test(note), "a rig with no rotator named one anyway");
  setRotator(true);
  await settle();
});

await testAsync("the dead-band holds: at 0 degrees nothing is commanded and nothing is promised", async () => {
  click(q('[data-testid="sky-rot-dial"] [data-value="0"]'));
  await settle();
  eq(useStore.getState().framing?.rotation_deg, 0, "back to zero:");
  assert(byId("sky-rot-note") == null, "0 degrees is not a commanded angle and must promise no rotation");
  click(q('[data-testid="sky-rot-dial"] [data-value="30"]'));
  await settle();
});

await testAsync("DONE asks the ENGINE for the panels, at the overlap the card promised", async () => {
  click(q('[data-mosaic="2x1"]'));
  await settle();
  assert(/2 panels/.test(byId("sky-framing-meta")?.textContent ?? ""), "the meta line did not follow the picker");

  // The MOSAIC NIGHT card asks the SAME route while the picker is open, with
  // `transit_alt: true`, so "how many panel requests did DONE make" has to
  // exclude it - otherwise this counts a readout as a commitment.
  const panelPosts = () => asked.filter(
    (a) => a.url.includes("/api/framing/mosaic") && a.body?.transit_alt !== true,
  );
  const before = panelPosts().length;
  eq(before, 0, "precondition: nothing has been asked of the mosaic engine yet");

  click(byId("sky-frame"));   // DONE
  await settle();

  const posts = panelPosts();
  eq(posts.length, 1, "exactly one mosaic request per DONE:");
  eq(posts[0].method, "POST", "the mosaic engine is a POST:");
  eq(posts[0].body.rows, 1, "rows:");
  eq(posts[0].body.cols, 2, "cols:");
  eq(posts[0].body.overlap, 0.15, "the overlap the card printed and the one it asked for must agree:");
  eq(posts[0].body.rotation_deg, 30, "the commanded angle must travel with the panels:");
  assert(posts[0].body.fov_x_deg > 1.6 && posts[0].body.fov_x_deg < 1.8, `the frame size is not the rig's: ${posts[0].body.fov_x_deg}`);
});

await testAsync("DONE keeps the framing where the user can see it", async () => {
  const strip = byId("sky-lock-framed");
  assert(strip != null, "the kept framing left no trace on the lock card");
  assert(/Framed · 2×1 mosaic/.test(strip.textContent), `the framed strip reads "${strip.textContent}"`);
  assert(/rot 30°/.test(strip.textContent), "the strip must carry the angle too");
  assert(byId("sky-framed-overlay") != null, "the panels are no longer drawn on the finder");
  eq(byId("sky-frame")?.querySelector(".nx-iconbtn-label")?.textContent, "ADJUST", "after DONE the toolbar offers:");

  // The engine's own panel centres are kept on the framing session, so whatever
  // queues the night sends what was drawn here rather than recomputing it.
  eq(useStore.getState().framing?.panels.length, 2, "the engine's panels were not kept:");
});

await testAsync("the x clears the framing, and says what that means for the run", async () => {
  click(byId("sky-frame-clear"));
  await settle();
  assert(byId("sky-lock-framed") == null, "the framed strip survived the clear");
  const f = useStore.getState().framing;
  eq(f?.mosaic.cols, 1, "clearing must return to a single frame:");
  eq(f?.rotation_deg, 0, "clearing must return to no commanded angle:");
  const toasts = useStore.getState().toasts;
  assert(
    toasts.some((t) => /catalogue position/.test(t.title ?? "")),
    "clearing the framing said nothing about what the run will do instead",
  );
});

act(() => { root.unmount(); });
Date.now = realNow;

const total = passed + failed;
console.log(`frameDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
