// w3SkyFrameNoSiteReason.test.tsx - the finder's FRAME refusal on a default
// site, MOUNTED (#568, backlog ruling WP-24a (a), owner-approved 2026-09-30).
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/w3SkyFrameNoSiteReason.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. Since H4-USKY (#503) a default site has no coordinates, so the
// finder's `model.patch` (the reticle's own RA/Dec) is null for TWO different
// reasons: the reticle aimed at ground (`view.alt < 0`), or no coordinates to
// place it with at all (`!haveCoords`). The toolbar's FRAME button used the
// same sentence, FRAME_NEEDS_AIM ("Aim above the horizon first"), for both -
// so on a fresh config, with the reticle at its default alt 45, FRAME was
// locked and told the operator to aim up when there was nothing wrong with
// the aim at all. The probe's S6 Sky walks (routes_s5_s6.json) hit exactly
// this: `[data-testid="sky-frame"]` stays locked, with `az 000 alt 45` on the
// card under it.
//
// THE FIX. `frameReason` (SkyHub.tsx) now reaches for `model.placementNote`
// first - finder/model.ts's own sentence for "no coordinates," which already
// names the site pill (NO_SITE_NOTE) or the role (NO_COORDS_NOTE) - and falls
// back to FRAME_NEEDS_AIM only once there ARE coordinates to judge the aim
// against. The control section below pins that FRAME_NEEDS_AIM still fires
// for a genuinely grounded reticle on a site that has coordinates.
//
// MUTATION RECORD: see the block at the foot of this file.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
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
const NOW = Date.UTC(2026, 8, 10, 22, 0, 0);
const realNow = Date.now;
Date.now = () => NOW;

// ------------------------------------------------------------- fetch double
function ok(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}

/** A made-up site the fixture never points a reticle or a lock card at the
 *  real sky with - only used so a SAVED site exists to contrast against the
 *  default one. */
const SAVED_SITE = {
  name: "Fixture backyard", latitude: 47.61, longitude: -122.33,
  elevation_m: 52, is_default: false, horizon_min_deg: 15,
};
/** A fresh config's site: numeric placeholder coordinates, `is_default: true`
 *  (#503/#568's premise). */
const DEFAULT_SITE = {
  name: "", latitude: 0, longitude: 0, elevation_m: 0,
  is_default: true, horizon_min_deg: 15,
};

let siteNow: any = DEFAULT_SITE;

const g = globalThis as any;
g.fetch = async (url: any) => {
  const u = String(url);
  if (u.includes("/api/survey/pack")) {
    return ok({ present: false, slug: "p", survey: "s", order: null, bytes: null, tile_count: null, fetched_at: null, fetching: null });
  }
  if (u.includes("/api/cloudmap/dome")) {
    return ok({ enabled: false, observed_at: null, stale: true, alt_start: 6, alt_step: 6, az_step: 10, rows: [] });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({ enabled: false, platform: "G18", observed_at: null, age_s: null, stale: true, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: siteNow.is_default === true, picks: [] });
  if (u.includes("/api/visibility")) {
    return ok({
      date: "2026-09-10", transit_unix: NOW / 1000 + 3600, transit_alt: 48, transit_in_daylight: false,
      dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
      darkness_kind: "astronomical", samples: [],
      moon: { illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90, separation_deg: 80, rise_unix: null, set_unix: null },
      best_window: null, alt_limit_deg: 15, never_rises_above_limit: false,
    });
  }
  if (u.includes("/api/catalog/region")) return ok({ rows: [], truncated: false, catalog_degraded: false, notes: [] });
  if (u.includes("/api/catalog?q=")) return ok({ results: [], notes: [] });
  if (u.includes("/api/site")) return ok({ site: siteNow, version: 3 });
  return ok({});
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
const { SkyHub, FRAME_NEEDS_AIM } = await import("../SkyHub");
const { NO_SITE_NOTE } = await import("../finder/model");

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
const settle = async (): Promise<void> => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const OPTICS = {
  have_optics: true, source: "config",
  focal_length_mm: 800, pixel_size_um: 3.76,
  sensor_width_px: 6248, sensor_height_px: 4176,
  image_scale_arcsec_px: 0.97, fov_w_deg: 1.68, fov_h_deg: 1.12, fov_diag_deg: 2.02,
};

function seed(site: any): void {
  siteNow = site;
  act(() => {
    useStore.setState({
      principal: {
        role: "operator", name: "tester",
        caps: ["view.status", "view.weather", "view.site_derived", "view.site_precise", "control.capture"],
      },
      site,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      framing: null,
      status: { connected: { camera: { connected: true, name: "sim camera" } }, looping: false, optics: OPTICS },
      config: {
        optics: { focal_length_mm: 800, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176, auto_from_camera: false, telescope_name: "" },
        optics_computed: OPTICS,
        safety: { horizon: null },
        survey: { online_fetch: false },
      },
      weather: null,
    } as never);
  });
}

/** Mount the hub fresh, already on MAP (the finder) - ATLAS is the default
 *  mode and every assertion here is about the reticle's own FRAME button. */
async function mount(site: any): Promise<{ unmount: () => void }> {
  seed(site);
  win.localStorage.setItem("astrodeck-next-sky-mode", "map");
  win.location.hash = "#/sky";
  const root = createRoot(container);
  await act(async () => { root.render(createElement(SkyHub)); });
  await settle();
  assert(byId("sky-frame") != null, "precondition: no FRAME button - the fixture is wrong, not the component");
  return { unmount: () => act(() => { root.unmount(); }) };
}

// =============================================== 1. no site saved, alt UP
{
  const m = await mount(DEFAULT_SITE);

  await testAsync("no site saved: the reticle itself is above the horizon (the probe's own premise)", () => {
    const view = container.querySelector("[data-sky-view]");
    assert(view != null, "no finder box - the fixture is wrong, not the component");
    assert(/alt\s*45/i.test(container.textContent ?? ""), `the default reticle is not at alt 45: "${container.textContent?.slice(0, 200)}"`);
  });

  await testAsync("no site saved: FRAME in the finder is locked with the model's no-site sentence, not 'aim above the horizon'", () => {
    const b = byId("sky-frame");
    eq(b.getAttribute("aria-disabled"), "true", "FRAME is not locked with nothing to frame:");
    eq(b.getAttribute("data-locked"), "true", "FRAME's locked class attribute:");
    eq(b.getAttribute("title"), NO_SITE_NOTE, "FRAME's refusal is not the model's own no-site sentence:");
    assert(b.getAttribute("title") !== FRAME_NEEDS_AIM,
      "FRAME still blames the aim when the reticle is above the horizon and the real problem is no site:");
  });

  m.unmount();
}

// ======================================= 2. control: a saved site, grounded
// The reticle's default is alt 45 (above the horizon), so to exercise
// FRAME_NEEDS_AIM honestly this drags it to ground first - `gestures.ts`'s
// ALT_MIN (-12) is the floor, reached with any sufficiently large deltaY.
{
  const m = await mount(SAVED_SITE);
  const view = container.querySelector("[data-sky-view]");
  assert(view != null, "no finder box to drag - the fixture is wrong, not the component");
  await act(async () => {
    view.dispatchEvent(new win.WheelEvent("wheel", { deltaX: 0, deltaY: 100000, bubbles: true, cancelable: true }));
  });
  await settle();

  await testAsync("control: the drag actually grounded the reticle", () => {
    assert(/alt\s*-1[12]/i.test(container.textContent ?? ""), `the reticle did not reach ground: "${container.textContent?.slice(0, 200)}"`);
  });

  await testAsync("control: with coordinates, a grounded reticle still reads FRAME_NEEDS_AIM", () => {
    const b = byId("sky-frame");
    eq(b.getAttribute("aria-disabled"), "true", "FRAME is not locked with the reticle on the ground:");
    eq(b.getAttribute("title"), FRAME_NEEDS_AIM, "a saved site's grounded reticle must still say to aim up:");
    assert(b.getAttribute("title") !== NO_SITE_NOTE,
      "a saved site's own refusal was replaced by the no-site sentence:");
  });

  m.unmount();
}

Date.now = realNow;

// MUTATION RECORD, 2026-09-30 (WP-24a), in a private scratch copy of ui/
// (never the shared tree, #254), from a byte backup restored with its sha256
// checked. Output verbatim.
//
//   MUTANT "frameReason never consults placementNote" (SkyHub.tsx: the finder
//   branch's `(model.placementNote ?? FRAME_NEEDS_AIM)` written back to
//   `FRAME_NEEDS_AIM`, the code as it was before this fix). Observed
//   ("w3SkyFrameNoSiteReason.test: 3/4 passed"):
//     x no site saved: FRAME in the finder is locked with the model's no-site
//     sentence, not 'aim above the horizon': FRAME's refusal is not the
//     model's own no-site sentence: expected "No site is saved, so the finder
//     cannot place anything on this sky - set one under the site pill.", got
//     "Aim above the horizon first - FRAME needs a target or a patch of sky
//     to look at."
//
//   MUTANT (control) "frameReason never falls back" (the finder branch's
//   fallback written `model.placementNote ?? null`, dropping FRAME_NEEDS_AIM
//   entirely). Observed ("3/4 passed"):
//     x control: with coordinates, a grounded reticle still reads
//     FRAME_NEEDS_AIM: FRAME is not locked with the reticle on the ground:
//     expected "true", got null

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w3SkyFrameNoSiteReason.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
