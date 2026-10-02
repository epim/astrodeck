// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// doorsConverge.test.tsx - both doors are SEND TO FLOW WIZARD, MOUNTED and
// pressed, and each writes one TARGET block and nothing into the Plan (#196,
// #154's door half; spec 2026-09-23 flows mosaic, section 8 S6, Revision 2
// ruling 4, 2.4's one overlap).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/__tests__/doorsConverge.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE TWO DOORS. The classic Atlas's forward button and the #/next Sky FRAME's
// forward action used to send a framing to the classic Plan as targets sharing
// a `mosaic_group` (the Atlas's button, and the Sky quick sheet's side
// channel), shot panel-first. Both are SEND TO FLOW WIZARD now, for a mosaic
// and for a single target, and each opens the ONE shared wizard (D13)
// pre-filled from its framing.
//
// WHAT IS GRADED, on the requests that reach the network and on the store:
//
//   1. EACH DOOR SAYS SEND TO FLOW WIZARD, in the wizard's own title, and
//      nothing on its page says the Plan door's words.
//   2. EACH DOOR OPENS THE SHARED SHEET PRE-FILLED FROM ITS FRAMING: the name,
//      the framing's CENTRE (dragged off the catalogue position in the Atlas
//      case, so a door that sent the catalogue's would show), the PA, the grid,
//      the overlap and the camera field arrive and are not asked; the angle
//      MODE, which a framing does not hold, is asked. The PA is the COMMANDED
//      one: a dial never turned off 0 arrives as no PA, and the wizard asks.
//   3. DRIVEN TO GENERATE, with the network answering from the route's own
//      recording (server/tests/fixtures/wizard_mosaic_answer.json), each door
//      posts exactly ONE `POST /api/flows/wizard`, whose body is the recorded
//      request but for what the framing itself decides (its centre in the
//      TARGET node's format, its overlap, which the recording leaves to the
//      default, and its skip, which neither door has); the review shows ONE
//      TARGET block; and the store's Plan is byte-for-byte what it was (a Plan
//      that already holds a hand-built target, so "unchanged" is not "empty"),
//      with no plan route asked.
//   4. A SINGLE TARGET (a 1 x 1 framing) goes the same way, as the Deep-sky
//      kind with no grid, and writes no Plan target either. A FREE-ROAM
//      framing arrives with no name, which the wizard asks for.
//   5. ONE OVERLAP: the store's seed, the Sky's FRAME session, the modal's
//      missing-key reading and each door's prefill all read DEFAULT_OVERLAP.
//
// MUTATION RECORD, 2026-09-28, each run in a private scratch copy of ui/
// (scratchpad/S6-DOORS-mut in the session scratchpad, never the shared tree,
// #254). Output verbatim.
//
//   MUTANT "old handler left wired" (Atlas; AtlasView.tsx: the button's onClick
//   also calls the retired Plan write, `setPlan` appending a panel target as
//   `addTargetsToPlan` did, after opening the wizard). Observed
//   ("doorsConverge.test: 6/8 passed"):
//     x the Atlas's mosaic opens the wizard pre-filled, and GENERATE writes one TARGET block and no Plan target: the Atlas mosaic: the store's Plan changed - the door wrote Plan targets
//     expected true
//     got      false
//     x the Atlas's single target goes the same way, as one target with no grid: the Atlas single target: the store's Plan changed - the door wrote Plan targets
//
//   MUTANT "old handler only" (Atlas; the button writes the Plan target and opens
//   no wizard). Observed ("doorsConverge.test: 6/8 passed"):
//     x the Atlas's mosaic opens the wizard pre-filled, and GENERATE writes one TARGET block and no Plan target: timed out waiting for the wizard's sheet
//     x the Atlas's single target goes the same way, as one target with no grid: timed out waiting for the wizard's sheet
//
//   MUTANT "old handler left wired" (Sky; SkyHub.tsx: onSendToWizard opens the
//   wizard and also appends the framing's panel to the Plan). Observed
//   ("doorsConverge.test: 6/8 passed"):
//     x the Sky's mosaic opens the wizard over the sky, pre-filled, and GENERATE writes one TARGET block and no Plan target: the Sky mosaic: the store's Plan changed - the door wrote Plan targets
//     x the Sky's single target goes the same way, as one target with no grid: the Sky single target: the store's Plan changed - the door wrote Plan targets
//
//   MUTANT "SkyHub keeps 0.15" (SkyHub.tsx enterFrame: the reset's overlap back
//   to 0.15). Observed ("doorsConverge.test: 6/8 passed"):
//     x the Sky FRAME's forward action is SEND TO FLOW WIZARD, and FRAME starts at the one overlap: entering FRAME did not start the session at DEFAULT_OVERLAP:
//     expected 0.25
//     got      0.15
//     x the Sky's mosaic opens the wizard over the sky, pre-filled, and GENERATE writes one TARGET block and no Plan target: the framing in the route:
//     expected ["M31","00h 42m 44s","+41° 16′ 08″","2","3","30","25"]
//     got      ["M31","00h 42m 44s","+41° 16′ 08″","2","3","30","15"]
//
//   MUTANT "Atlas prefill takes the catalogue centre" (AtlasView.tsx
//   wizardPrefillNow: ra/dec from `target` instead of the framing's `center`).
//   Observed ("doorsConverge.test: 6/8 passed"):
//     x the Atlas's mosaic opens the wizard pre-filled, and GENERATE writes one TARGET block and no Plan target: the framing's centre did not arrive as the RA (00h 43m 15s): "RA00h 42m 44sFROM THE FRAMING"
//     x the Atlas's single target goes the same way, as one target with no grid: a single target's name and centre:
//     expected ["M31","00h 43m 15s","+41° 30′ 00″"]
//     got      ["M31","00h 42m 44s","+41° 16′ 08″"]
//
//   MUTANT "Sky prefill sends an angle mode" (mosaic.ts framingPrefill:
//   `angleMode: "Rotate to PA"`). Observed ("doorsConverge.test: 7/8 passed"):
//     x the Sky's mosaic opens the wizard over the sky, pre-filled, and GENERATE writes one TARGET block and no Plan target: an angle MODE was sent, but a framing holds none
//     expected undefined
//     got      "Rotate to PA"
//
//   ADDED BY THE TASK'S VERIFIER, 2026-09-28 (the six mutants below left the
//   first version of this file green; each case or check here was added for
//   one and run in a private scratch copy, scratchpad/S6-DOORS-verify-mut):
//
//   MUTANT "Atlas prefill sends the raw rotation" (AtlasView.tsx
//   wizardPrefillNow: `paDeg: rotation_deg` for `commandedPaDeg`). Observed
//   ("doorsConverge.test: 9/10 passed"):
//     x the Atlas's mosaic at a dial never turned off 0 arrives with NO PA, which the wizard asks for: an unturned dial arrived as a PA:
//     expected ""
//     got      "0"
//
//   MUTANT "framingPrefill sends the raw rotation" (mosaic.ts: `paDeg:
//   f.rotation_deg`). Observed ("doorsConverge.test: 9/10 passed";
//   quickMosaicDom stays 13/13, its framing is at PA 30):
//     x the Sky's mosaic at a dial never turned off 0 arrives with NO PA: an unturned dial arrived as a PA:
//     expected undefined
//     got      "0"
//
//   MUTANT "Atlas prefill drops the camera field" (AtlasView.tsx: `fov:
//   null`). Observed ("doorsConverge.test: 9/10 passed"):
//     x the Atlas's mosaic opens the wizard pre-filled, and GENERATE writes one TARGET block and no Plan target: the Atlas's camera field (1.35 x 0.90 deg) did not arrive: "no FRAMED WITH row"
//
//   MUTANT "Sky door hands no camera field" (SkyHub.tsx: framingPrefill given
//   a zero field). Observed ("doorsConverge.test: 9/10 passed"):
//     x the Sky's mosaic opens the wizard over the sky, pre-filled, and GENERATE writes one TARGET block and no Plan target: the Sky's camera field in the route:
//     expected ["1.3460","0.8996"]
//     got      ["NaN","NaN"]
//
//   MUTANT "Atlas free roam invents a name" (AtlasView.tsx wizardPrefillNow:
//   the old Plan door's "Sky" as the fallback name). Observed
//   ("doorsConverge.test: 10/11 passed"):
//     x a free-roam framing arrives with NO name, which the wizard asks for, from either door: the Atlas's free-roam framing arrived with a name:
//     expected 0
//     got      1
//
//   MUTANT "framingPrefill invents a free-roam name" (mosaic.ts: the same
//   "Sky" fallback). Observed ("doorsConverge.test: 10/11 passed"):
//     x a free-roam framing arrives with NO name, which the wizard asks for, from either door: the Sky's free-roam framing arrived with a name:
//     expected ""
//     got      "Sky"
//
//   Two mutants leave this file GREEN BY DESIGN and are red in
//   server/tests/test_overlap_constant_one.py instead: "store keeps its own 0.25"
//   and "the modal keeps its own missing-key 25" write the same value as
//   DEFAULT_OVERLAP, so no behaviour here can tell them apart; a second constant
//   with an equal value is a text fact, which that test reads.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// SkyHub reaches `atlas.css` and the wizard reaches `wizard.css`; Node has no
// idea what a `.css` file is, so a load hook answers with an empty module.
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

// @ts-ignore  node built-ins; tsx supplies them at runtime
const { readFileSync } = await import("node:fs");

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
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
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};

// ------------------------------------------------------------- the network
const NOW = Date.UTC(2026, 8, 10, 5, 0, 0);
function readJson(rel: string): any {
  return JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8") as string);
}
const FX = readJson("../../../server/tests/fixtures/wizard_mosaic_answer.json");
const ID = FX.answer.id as string;

interface Call { method: string; url: string; body: any }
let calls: Call[] = [];
function reply(body: unknown, status = 200): any {
  return {
    ok: status >= 200 && status < 300, status, statusText: String(status),
    headers: { get: () => "application/json" },
    json: async () => body, blob: async () => ({}),
  };
}
const M31_ROW = {
  id: "m31", label: "M31", kind: "dso", type: "Galaxy",
  ra_hours: 0.7123, dec_deg: 41.269, mag: 3.4, size_arcmin: 190,
  constellation: "And", describe: "Andromeda Galaxy", alias: "NGC 224", alt: 58, az: 64,
};
const M31_PICK = {
  id: "m31", name: "M31", type: "Galaxy",
  ra_hours: 0.7123, dec_deg: 41.269, mag: 3.4, size_arcmin: 190,
  difficulty: "easy", surface_brightness: 21, difficulty_source: "heuristic",
  max_alt: 60, transit_unix: NOW / 1000 + 7200,
  best_window: null, moon_sep_deg: 80, never_rises_above_limit: false, score: 1,
};
const NIGHT = {
  date: "2026-09-10", alt_limit_deg: 20, never_rises_above_limit: false,
  transit_unix: NOW / 1000 + 7200, transit_alt: 62, transit_in_daylight: false,
  dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
  darkness_kind: "astronomical", best_window: null, samples: [], hours_above_limit: 6,
  moon: { illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90, separation_deg: 80, rise_unix: null, set_unix: null },
};
win.fetch = async (url: any, init: any = {}) => {
  const u = String(url);
  const method = (init.method ?? "GET").toUpperCase();
  let body: any;
  try { body = init.body ? JSON.parse(init.body) : undefined; } catch { body = init.body; }
  calls.push({ method, url: u, body });
  if (method === "POST" && u.endsWith("/api/flows/wizard")) return reply(FX.answer);
  if (method === "POST" && u.endsWith(`/api/flows/${ID}/compile`)) return reply(FX.compile);
  if (u.includes("/api/framing/mosaic")) {
    return reply({ panels: [], total_fov_x_deg: 0, total_fov_y_deg: 0, frame_fov_x_deg: 1.346, frame_fov_y_deg: 0.9, pixel_scale_arcsec: 0.78 });
  }
  if (u.includes("/api/cloudmap/dome")) {
    return reply({ enabled: true, observed_at: null, stale: false, alt_start: 6, alt_step: 6, az_step: 10, rows: [] });
  }
  if (u.includes("/api/cloudmap")) {
    return reply({ enabled: false, platform: "G18", observed_at: null, age_s: null, stale: false, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  if (u.includes("/api/survey/pack")) {
    return reply({ present: false, slug: "p", survey: "s", order: null, bytes: null, tile_count: null, fetched_at: null, fetching: null });
  }
  if (u.includes("/api/catalog/tonight")) return reply({ date: "2026-09-10", site_is_default: false, picks: [M31_PICK] });
  if (u.includes("/api/catalog/region")) return reply({ rows: [M31_ROW], truncated: false, catalog_degraded: false, notes: [] });
  if (u.includes("/api/catalog?q=")) return reply({ results: [], rows: [], notes: [] });
  if (u.includes("/api/visibility")) return reply(NIGHT);
  if (u.includes("/api/site")) {
    return reply({ site: { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20 }, version: 3 });
  }
  return reply({});
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "HTMLVideoElement", "HTMLCanvasElement", "HTMLImageElement", "Image",
  "Element", "SVGElement", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "requestAnimationFrame", "cancelAnimationFrame", "WebSocket", "ResizeObserver", "URL", "fetch",
  "Blob", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;
Date.now = () => NOW;

// ------------------------------------------------------------------ imports
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../store");
const { FLOWS_INIT } = await import("../components/flows/flowsSlice");
const AtlasView = (await import("../views/AtlasView")).default;
const { SkyHub } = await import("../next/hubs/sky/SkyHub");
const { SheetHost } = await import("../next/shell/SheetHost");
const { resetRouterCacheForTests, useRoute, currentRoute } = await import("../next/router");
const { FLOW_WIZARD_SHEET } = await import("../next/hubs/session/flows/wizard/FlowWizardSheet");
const { WIZARD_TITLE } = await import("../components/flows/wizard/SendToWizardSheet");
const { MOSAIC_ANGLES, KIND_MOSAIC, KIND_DEEP_SKY, fieldWords } = await import("../components/flows/wizard/wizardModel");
const classicFmt = await import("../components/flows/QuickFlow");
const nextFmt = await import("../next/hubs/session/flows/create/quickPayload");
const { SEND_TO_WIZARD: SKY_LABEL } = await import("../next/hubs/sky/sheets/quickCopy");
const { DEFAULT_OVERLAP, fovFromOptics } = await import("../lib/framing");
const { draftFromParams, layoutOf } = await import("../components/flows/framing/framingModel");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
/** Keys sorted at every level: a request body is compared by its answers, not
 *  by the order the sheet happened to write them in (the sheet's own test
 *  grades that order against the recording). */
function canon(v: any): any {
  if (Array.isArray(v)) return v.map(canon);
  if (v && typeof v === "object") return Object.fromEntries(Object.keys(v).sort().map((k) => [k, canon(v[k])]));
  return v;
}

const doc = win.document;
const container = doc.getElementById("root") as any;
let root = createRoot(container);
const q = (id: string): any => doc.querySelector(`[data-testid="${id}"]`);
const qa = (id: string): any[] => Array.from(doc.querySelectorAll(`[data-testid="${id}"]`));
const btn = (id: string): any => (q(id)?.closest("button") ?? null);
const locked = (b: any) => b?.getAttribute("aria-disabled") === "true";
function click(el: any): void {
  assert(el, "no element to click - the fixture is wrong, not the component");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
function typeInto(el: any, value: string): void {
  assert(el, "no input to type into");
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => { setter.call(el, value); el.dispatchEvent(new win.Event("input", { bubbles: true })); });
}
async function settle(ms = 5, n = 6): Promise<void> {
  for (let i = 0; i < n; i++) await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}
async function until(what: string, cond: () => boolean): Promise<void> {
  for (let i = 0; i < 400; i++) {
    if (cond()) return;
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
  }
  throw new Error(`timed out waiting for ${what}`);
}
const stepNow = () => q("wizard-step")?.getAttribute("data-step");
function next(): void {
  const b = btn("wizard-next");
  assert(b && !locked(b), `NEXT is locked on ${stepNow()}: ${q("wizard-missing")?.textContent ?? b?.getAttribute("title")}`);
  click(b);
}
const wizardPosts = () => calls.filter((c) => c.method === "POST" && c.url.endsWith("/api/flows/wizard"));
/** Any request that could write the Plan: the plan library routes, and a
 *  sequence start (which would run the Plan the store holds). */
const planCalls = () => calls.filter((c) => /\/api\/plans?\b|\/api\/sequence\/start/.test(c.url) && c.method !== "GET");
const planNow = () => JSON.stringify(useStore.getState().plan);
/** The Plan door's words, which neither page may show. */
const RETIRED_WORDS = /panels to Plan|Add target to Plan|added to Plan/;

/** A Plan that already holds a hand-built target, so "unchanged" means the
 *  same target list and not merely an empty one. */
const HAND_BUILT_PLAN = {
  ...useStore.getState().plan,
  targets: [{
    id: "hand-1", name: "NGC 7000", ra_hours: 20.98, dec_deg: 44.3, center: true,
    autofocus_first: true, calibration: false,
    steps: [{ id: "s1", filter: null, exposure_s: 120, gain: 100, offset: 30, binning: 1, count: 12, frame_type: "light" }],
  }],
};

const OPERATOR = ["view.status", "view.preview", "view.weather", "view.site_derived", "view.site_precise",
  "control.capture", "control.mount"];
/** The recorded answer's camera: an IMX571 at 1000 mm, 1.346 x 0.900 deg. */
const OPTICS = {
  focal_length_mm: 1000, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176,
  guide_focal_length_mm: null, auto_from_camera: false, telescope_name: "",
};
const OPTICS_COMPUTED = {
  have_optics: true, source: "config", focal_length_mm: 1000, pixel_size_um: 3.76,
  sensor_width_px: 6248, sensor_height_px: 4176, image_scale_arcsec_px: 0.78,
  fov_w_deg: 1.346, fov_h_deg: 0.9, fov_diag_deg: 1.62,
};
/** The field each door frames with, from the same optics (the prefill's
 *  `fov`, shown on the FRAMING step as FRAMED WITH). */
const FRAMED_FOV = fovFromOptics(OPTICS);

function seed(framing: unknown): void {
  act(() => {
    useStore.setState({
      principal: { role: "operator", name: "tester", email: null, caps: OPERATOR },
      authGate: "open",
      site: { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20 },
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      night: false,
      status: {
        connected: { camera: { connected: true, name: "sim camera" } },
        looping: false, mode: "sim", busy_lanes: [],
        optics: OPTICS_COMPUTED,
        mount: { ra_hours: 5.5, dec_deg: -5.4, ra_str: "05h 30m", dec_str: "-05 24", alt: 40, az: 180, tracking: true, parked: false, slewing: false },
      },
      config: {
        version: 7, optics: OPTICS, optics_computed: OPTICS_COMPUTED,
        safety: { horizon: null }, survey: { online_fetch: false },
        rotator: { range_type: "full", range_start_deg: 0, tolerance_deg: 1 },
      },
      weather: null,
      flows: { ...FLOWS_INIT },
      framing,
    } as never);
    useStore.getState().setPlan(HAND_BUILT_PLAN as never, false);
  });
  calls = [];
}

/** The fixture's own claim, read before any door is graded against it: the
 *  recorded answer saves ONE TARGET block. */
const fxTargets = (FX.answer.graph.nodes as any[]).filter((n) => n.type === "target");

/** The walk a door's wizard takes to GENERATE: the TARGET step (everything
 *  arrived), the FRAMING step (the angle mode asked for a mosaic, the PA
 *  arrived), FILTERS (L, R, G, B), GUIDING (on), and GENERATE. */
async function walkToGenerate(mosaic: boolean, filters: string[] = ["L", "R", "G", "B"]): Promise<void> {
  await until("the wizard's sheet", () => q("send-to-wizard-sheet") !== null);
  eq(stepNow(), "target", "the wizard did not open on TARGET");
  next();
  eq(stepNow(), "framing", "NEXT did not reach FRAMING");
  if (mosaic) {
    eq(qa("wizard-arrived-angle").length, 0, "an angle MODE arrived, but no framing holds one");
    click(q("wizard-angle-mode-0"));
  }
  next();
  for (const f of filters) click(q(`wizard-filter-${f}`));
  next();
  eq(stepNow(), "guiding", "NEXT did not reach GUIDING");
  next();
  eq(stepNow(), "review", "NEXT did not reach REVIEW");
  eq(wizardPosts().length, 0, "something was generated before GENERATE");
  click(btn("wizard-generate"));
  await until("the saved flow's review", () => q("wizard-saved") !== null);
  await settle();
}

/** What every door's GENERATE must have done, and not done. */
function assertOneBlockNoPlan(plan0: string, door: string): void {
  eq(wizardPosts().length, 1, `${door}: GENERATE did not make exactly one POST /api/flows/wizard`);
  eq(fxTargets.length, 1, "precondition: the recorded answer saves one TARGET block");
  eq(qa("wizard-review-block").length, 1, `${door}: the review does not show exactly one TARGET block`);
  assert((q("wizard-saved")?.textContent ?? "").includes(`Saved as ${FX.answer.name}`),
    `${door}: the review does not say what was saved`);
  eq(planNow() === plan0, true, `${door}: the store's Plan changed - the door wrote Plan targets`);
  eq(planCalls().map((c) => `${c.method} ${c.url}`), [], `${door}: a plan route was asked`);
  // Read loosely: the integration deleted the slice with PlanEditor.tsx's
  // banner (#461), and this check outlives it: a door that raised the
  // banner again under that name would still be caught here.
  eq((useStore.getState() as any).atlasBannerPending ?? null, null, `${door}: the retired Plan banner was raised`);
}

// ================================================================ 0. fixture

await test("precondition: the recording and the two labels the doors must show", () => {
  eq(FX.request.kind, KIND_MOSAIC, "the recorded request is not the Mosaic kind");
  eq(WIZARD_TITLE, "SEND TO FLOW WIZARD", "the wizard's title");
  eq(SKY_LABEL, WIZARD_TITLE, "the Sky's door label is not the wizard's own title:");
  eq(MOSAIC_ANGLES[0], "Rotate to PA", "the first angle chip is not ROTATE TO");
});

// ================================================================ 1. the Atlas

/** The Atlas's framing: M31's catalogue position, and a CENTRE dragged off it
 *  to the recording's typed coordinates, framed 3 x 2 at PA 30. */
const ATLAS_CENTER = { ra_hours: 0.72083333, dec_deg: 41.5 };
function atlasFraming(rows: number, cols: number, rotation = 30) {
  return {
    target: { id: "M31", name: "M31", type: "Galaxy", ra_hours: 0.7123, dec_deg: 41.269, mag: 3.4, size_arcmin: 190 },
    center: ATLAS_CENTER,
    rotation_deg: rotation,
    survey: "CDS/P/DSS2/color",
    stretch: "linear",
    fovZoomDeg: 3,
    mosaic: { rows, cols, overlap: DEFAULT_OVERLAP },
    panels: [],
  };
}

async function mountAtlas(rows: number, cols: number, rotation = 30): Promise<void> {
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  seed(atlasFraming(rows, cols, rotation));
  await act(async () => { root.render(createElement(AtlasView)); });
  await settle();
}

await test("the Atlas's forward action is SEND TO FLOW WIZARD, and nothing on the page names the Plan door", async () => {
  await mountAtlas(2, 3);
  assert(/M31/.test(container.textContent ?? ""), "the Atlas never rendered the framed target");
  const b = q("atlas-send-to-wizard");
  assert(b, "the Atlas has no SEND TO FLOW WIZARD button");
  eq(b.textContent, WIZARD_TITLE, "the Atlas's door is not labelled with the wizard's title:");
  assert(!RETIRED_WORDS.test(doc.body.textContent ?? ""),
    `the Atlas still says the Plan door's words: "${(doc.body.textContent ?? "").match(RETIRED_WORDS)?.[0]}"`);
});

await test("the Atlas's mosaic opens the wizard pre-filled, and GENERATE writes one TARGET block and no Plan target", async () => {
  await mountAtlas(2, 3);
  const plan0 = planNow();
  click(q("atlas-send-to-wizard"));
  await until("the wizard's sheet", () => q("send-to-wizard-sheet") !== null);
  // What arrived is the FRAMING's: the name, the dragged centre in the TARGET
  // node's format (not the catalogue's 00h 42m 44s), and nothing asked of it.
  const ra = classicFmt.raHms(ATLAS_CENTER.ra_hours);
  const dec = classicFmt.decDms(ATLAS_CENTER.dec_deg);
  assert((q("wizard-arrived-name")?.textContent ?? "").includes("M31"), "the framing's name did not arrive");
  assert((q("wizard-arrived-ra")?.textContent ?? "").includes(ra),
    `the framing's centre did not arrive as the RA (${ra}): "${q("wizard-arrived-ra")?.textContent}"`);
  assert((q("wizard-arrived-dec")?.textContent ?? "").includes(dec), `the framing's Dec (${dec}) did not arrive`);
  next();
  assert((q("wizard-grid")?.textContent ?? "").includes("3 x 2 panels"), `grid line: ${q("wizard-grid")?.textContent}`);
  assert((q("wizard-overlap")?.textContent ?? "").includes(`${DEFAULT_OVERLAP * 100}%`),
    `the framing's overlap did not arrive: ${q("wizard-overlap")?.textContent}`);
  eq(q("wizard-ask-pa")?.value, "30", "the framing's PA did not arrive in the PA box:");
  // The camera field the Atlas framed with arrived (FRAMED WITH), so a
  // framing made with another camera can be told apart from the rig's field.
  const framedWith = fieldWords({ xDeg: FRAMED_FOV.fov_x_deg, yDeg: FRAMED_FOV.fov_y_deg });
  assert((q("wizard-fov")?.textContent ?? "").includes(framedWith),
    `the Atlas's camera field (${framedWith}) did not arrive: "${q("wizard-fov")?.textContent ?? "no FRAMED WITH row"}"`);
  click(btn("wizard-back"));
  await walkToGenerate(true);
  const body = wizardPosts()[0]?.body;
  // The recorded request, but for what the framing decides.
  const { skip: _skip, ...recorded } = FX.request;
  eq(canon(body), canon({ ...recorded, ra, dec, overlap_pct: DEFAULT_OVERLAP * 100 }),
    "the Atlas door's body is not the recorded request with the framing's centre and overlap:");
  assertOneBlockNoPlan(plan0, "the Atlas mosaic");
  click(btn("wizard-close"));
  await settle();
  eq(qa("send-to-wizard-sheet").length, 0, "CLOSE did not close the wizard back onto the Atlas");
  assert(q("atlas-send-to-wizard"), "the Atlas's framing is not under the closed wizard");
});

await test("the Atlas's single target goes the same way, as one target with no grid", async () => {
  await mountAtlas(1, 1);
  const plan0 = planNow();
  eq(q("atlas-send-to-wizard")?.textContent, WIZARD_TITLE, "a single target's door is not SEND TO FLOW WIZARD:");
  click(q("atlas-send-to-wizard"));
  await walkToGenerate(false, ["L"]);
  const body = wizardPosts()[0]?.body ?? {};
  eq(body.kind, KIND_DEEP_SKY, "a 1 x 1 framing did not go as one target:");
  eq(["rows", "cols", "angle_mode", "pa_deg", "overlap_pct", "skip"].filter((k) => k in body), [],
    "a single target sent grid answers:");
  eq([body.target, body.ra, body.dec],
    ["M31", classicFmt.raHms(ATLAS_CENTER.ra_hours), classicFmt.decDms(ATLAS_CENTER.dec_deg)],
    "a single target's name and centre:");
  assertOneBlockNoPlan(plan0, "the Atlas single target");
  click(btn("wizard-close"));
  await settle();
});

await test("the Atlas's mosaic at a dial never turned off 0 arrives with NO PA, which the wizard asks for", async () => {
  // `rotation_deg` starts at 0 for every framing session, so 0 is "nobody
  // chose an angle" (`commandedPa`), and the door hands the COMMANDED PA:
  // none. A door that handed the raw rotation would type 0 into the PA box
  // and lay the grid out, and command the rotator, at an angle nobody chose.
  await mountAtlas(2, 3, 0);
  click(q("atlas-send-to-wizard"));
  await until("the wizard's sheet", () => q("send-to-wizard-sheet") !== null);
  next();
  eq(stepNow(), "framing", "NEXT did not reach FRAMING");
  assert(q("wizard-ask-pa") !== null, "the FRAMING step did not ask for the PA - the fixture is wrong, not the door");
  eq(q("wizard-ask-pa")?.value, "", "an unturned dial arrived as a PA:");
  click(btn("wizard-close"));
  await settle();
});

// ================================================================ 2. the Sky

/** SkyHub and the real SheetHost on the real router, so the door's
 *  `openFlowWizard` is graded by the sheet it actually opens. */
function Next(): any {
  const r = useRoute();
  return createElement(Fragment, null,
    createElement(SkyHub),
    createElement(SheetHost as any, { route: r, phone: false }));
}

async function mountSky(): Promise<void> {
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  seed(null);
  // The schematic finder (MAP), where the reach strip and FRAME live.
  win.localStorage.setItem("astrodeck-next-sky-mode", "map");
  win.location.hash = "#/sky";
  resetRouterCacheForTests();
  await act(async () => { root.render(createElement(Next)); });
  await settle(100, 5);
  click(doc.querySelector('[data-reach-chip="m31"]'));
  await settle(50, 4);
  click(q("sky-frame"));
  await settle(50, 4);
  assert(q("sky-framing"), "FRAME mode did not come up - the fixture is wrong, not the component");
}

await test("the Sky FRAME's forward action is SEND TO FLOW WIZARD, and FRAME starts at the one overlap", async () => {
  await mountSky();
  const b = btn("sky-send-to-wizard");
  assert(b, "the Sky's framing card has no SEND TO FLOW WIZARD");
  eq(b.textContent, WIZARD_TITLE, "the Sky's door is not labelled with the wizard's title:");
  eq(useStore.getState().framing?.mosaic.overlap, DEFAULT_OVERLAP,
    "entering FRAME did not start the session at DEFAULT_OVERLAP:");
  assert(!RETIRED_WORDS.test(doc.body.textContent ?? ""), "the Sky still says the Plan door's words");
});

await test("the Sky's mosaic opens the wizard over the sky, pre-filled, and GENERATE writes one TARGET block and no Plan target", async () => {
  await mountSky();
  click(doc.querySelector('[data-mosaic="3x2"]'));
  click(doc.querySelector('[data-testid="sky-rot-dial"] [data-value="30"]'));
  await settle();
  const f = useStore.getState().framing!;
  eq([f.mosaic.cols, f.mosaic.rows, f.rotation_deg], [3, 2, 30], "precondition: the framing is a 3 x 2 at PA 30");
  const plan0 = planNow();
  click(btn("sky-send-to-wizard"));
  const r = currentRoute();
  eq([r.hub, r.sheets], ["sky", [FLOW_WIZARD_SHEET]], "the door did not open the wizard over the sky");
  const ra = nextFmt.raHms(f.center.ra_hours);
  const dec = nextFmt.decDms(f.center.dec_deg);
  eq([r.params.wz_name, r.params.wz_ra, r.params.wz_dec, r.params.wz_rows, r.params.wz_cols, r.params.wz_pa, r.params.wz_overlap],
    ["M31", ra, dec, "2", "3", "30", String(DEFAULT_OVERLAP * 100)], "the framing in the route:");
  eq(r.params.wz_angle, undefined, "an angle MODE was sent, but a framing holds none");
  // The camera field the reticle and the pitch were drawn from.
  eq([Number(r.params.wz_fovx).toFixed(4), Number(r.params.wz_fovy).toFixed(4)],
    [FRAMED_FOV.fov_x_deg.toFixed(4), FRAMED_FOV.fov_y_deg.toFixed(4)], "the Sky's camera field in the route:");
  await walkToGenerate(true);
  const body = wizardPosts()[0]?.body;
  const { skip: _skip, ...recorded } = FX.request;
  eq(canon(body), canon({ ...recorded, ra, dec, overlap_pct: DEFAULT_OVERLAP * 100 }),
    "the Sky door's body is not the recorded request with the framing's centre and overlap:");
  assertOneBlockNoPlan(plan0, "the Sky mosaic");
  // CLOSE pops back to FRAME exactly as it was.
  click(btn("wizard-close"));
  await settle(20, 4);
  eq(currentRoute().sheets, [], "CLOSE did not pop the wizard off the sky");
  assert(q("sky-framing"), "FRAME was not left up under the wizard");
  eq(useStore.getState().framing?.mosaic.cols, 3, "the framing changed under the wizard");
});

await test("the Sky's single target goes the same way, as one target with no grid", async () => {
  await mountSky();
  click(doc.querySelector('[data-mosaic="1x1"]'));
  await settle();
  const plan0 = planNow();
  click(btn("sky-send-to-wizard"));
  await walkToGenerate(false, ["L"]);
  const body = wizardPosts()[0]?.body ?? {};
  eq(body.kind, KIND_DEEP_SKY, "a 1 x 1 framing did not go as one target:");
  eq(["rows", "cols", "angle_mode", "pa_deg", "overlap_pct", "skip"].filter((k) => k in body), [],
    "a single target sent grid answers:");
  assertOneBlockNoPlan(plan0, "the Sky single target");
  click(btn("wizard-close"));
  await settle(20, 4);
});

await test("the Sky's mosaic at a dial never turned off 0 arrives with NO PA", async () => {
  // The Sky's half of the Atlas case above: FRAME starts every session at
  // rotation 0, and `framingPrefill` hands the commanded PA, none.
  await mountSky();
  click(doc.querySelector('[data-mosaic="3x2"]'));
  await settle();
  const f = useStore.getState().framing!;
  eq([f.mosaic.cols, f.mosaic.rows, f.rotation_deg], [3, 2, 0], "precondition: a 3 x 2 at the unturned dial");
  click(btn("sky-send-to-wizard"));
  const r = currentRoute();
  eq(r.sheets, [FLOW_WIZARD_SHEET], "the door did not open the wizard over the sky");
  eq(r.params.wz_cols, "3", "precondition: the framing reached the route");
  eq(r.params.wz_pa, undefined, "an unturned dial arrived as a PA:");
  await until("the wizard's sheet", () => q("send-to-wizard-sheet") !== null);
  click(btn("wizard-close"));
  await settle(20, 4);
});

await test("a free-roam framing arrives with NO name, which the wizard asks for, from either door", async () => {
  // A free-roam session has no catalogue name; its id is a coordinate label
  // nobody chose, and the frames are filed under the name. So neither door
  // invents one: the wizard asks, and its centre still arrives.
  await mountAtlas(2, 3);
  act(() => {
    useStore.getState().setFraming({ target: undefined, freeroamId: "Sky 0.72h +41.5°" } as never);
  });
  await settle();
  click(q("atlas-send-to-wizard"));
  await until("the wizard's sheet", () => q("send-to-wizard-sheet") !== null);
  eq(qa("wizard-arrived-name").length, 0, "the Atlas's free-roam framing arrived with a name:");
  eq(q("wizard-ask-name")?.value, "", "the Atlas's free-roam framing typed a name into the NAME box:");
  assert((q("wizard-arrived-ra")?.textContent ?? "").includes(classicFmt.raHms(ATLAS_CENTER.ra_hours)),
    "control: the free-roam centre did not arrive");
  click(btn("wizard-close"));
  await settle();
  // The Sky's door hands `framingPrefill` the session as it stands; a patch's
  // session carries no `target`.
  const { framingPrefill } = await import("../next/hubs/sky/frame/mosaic");
  const patch = framingPrefill(
    { center: ATLAS_CENTER, rotation_deg: 0, mosaic: { rows: 2, cols: 3, overlap: DEFAULT_OVERLAP } },
    FRAMED_FOV,
  );
  eq(patch.name, "", "the Sky's free-roam framing arrived with a name:");
  eq(patch.ra, nextFmt.raHms(ATLAS_CENTER.ra_hours), "control: the Sky's free-roam centre:");
});

// ============================================================ 3. one overlap

await test("the store's seed and the modal's missing-key reading are DEFAULT_OVERLAP", () => {
  act(() => { useStore.getState().openFraming(); });
  eq(useStore.getState().framing?.mosaic.overlap, DEFAULT_OVERLAP, "store.openFraming's overlap seed:");
  // A stored overlap that is no number (the "Infinity" #358 lets through) reads
  // as the missing-key overlap.
  const draft = { ...draftFromParams({}), overlap: "Infinity" };
  eq(layoutOf(draft as never).overlap, DEFAULT_OVERLAP, "the modal's missing-key overlap:");
  // Control: a real stored overlap still wins.
  eq(layoutOf({ ...draftFromParams({}), overlap: 10 } as never).overlap, 0.1, "a stored 10% overlap:");
});

// Unused-binding guard for a helper kept for mutants that type into the PA box.
void typeInto;

// ------------------------------------------------------------------- tally
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`doorsConverge.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
