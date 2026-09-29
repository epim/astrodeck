// framingSkyDegraded.test.tsx - the Target modal's sky on a rig with no survey
// source says so, instead of LOADING for good (#404, UX-07; spec 2026-09-23
// flows mosaic, 2.3).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/framingSkyDegraded.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT WAS WRONG. SkyCanvas gates its tile-engine LOADING skeleton on
// `!surveyDegraded` and shows UX-07's empty state instead ("No sky survey
// available - download the offline pack or enable online fetch in
// Settings."), but `surveyDegraded` is a PROP: each host keeps the state from
// `onSurveyError` / `onSurveyLoad` and hands it back. AtlasView, SkyHub and
// CompassSurvey do. FramingSky wired none of the three, so in the modal
// `surveyDegraded` was always false, and on the S4 probe's fresh config (no
// sky pack, online fetch off: every tile 404) the sky pulsed "LOADING color..."
// in the middle of the grid for the whole walk, 3 to 7 s past the last 404.
//
// HOW THIS DRIVES IT. Nothing here calls onSurveyError by hand: a test that
// fired the callback itself would pass whether or not SkyCanvas ever fires it
// on the path the modal really takes. jsdom has no WebGL, so a context whose
// every method is a no-op is handed to the canvas; SkyCanvas's probe passes,
// the TileEngine mounts exactly as in a browser, asks /api/survey/tile for its
// tiles, gets the probe's 404 for each, and after its eighth consecutive
// failure on a blank view reports `onAllFailing`, which SkyCanvas turns into
// `onSurveyError`. Then the tiles are made to answer, and the engine's first
// drawn frame is what must clear the state (`onSurveyLoad`).
//
// The second half is the other render path: a survey with no tile slug (or a
// browser with no WebGL) takes SkyCanvas's <img> cutout pipeline, whose own
// first-load skeleton said LOADING for good in the same way, degraded or not.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// S5-SKY-mut), never in the shared tree (#254), and the failure it produced is
// quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";

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
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};

// A WebGL context that accepts every call and answers every query with a
// truthy object: enough for initTileGL to compile, link and draw (it checks
// only that each create/compile/link answer is truthy). The 2D context, which
// SkyCanvas's label measurement asks for, stays absent, as in every other
// canvas test.
const GL_STUB: any = new Proxy({}, { get: () => () => ({}) });
win.HTMLCanvasElement.prototype.getContext = function (kind: string) {
  return kind === "webgl" || kind === "experimental-webgl" ? GL_STUB : null;
};

// The network. `tiles` decides what /api/survey/tile answers; the cutout
// route (the <img> path) always fails. Every request is counted, so a case
// can say how many 404s it took.
const net = { tiles: "404" as "404" | "ok", tileCalls: 0, cutoutCalls: 0 };
win.fetch = async (url: string) => {
  const s = String(url);
  if (s.includes("/api/survey/tile/")) {
    net.tileCalls++;
    if (net.tiles === "ok") return { ok: true, status: 200, blob: async () => ({}) };
    return { ok: false, status: 404, blob: async () => ({}) };
  }
  if (s.includes("/api/survey/cutout")) {
    net.cutoutCalls++;
    return { ok: false, status: 404, headers: { get: () => null }, blob: async () => ({}) };
  }
  throw new Error(`no network in this fixture: ${s}`);
};
(globalThis as any).createImageBitmap = async () => ({ close() {} });

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame",
  "Image", "URL", "fetch", "Blob",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { FramingSky } = await import("../FramingSky");
type SkyPanel = import("../../../atlas/PanelLayer").SkyPanel;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

/** Let real time pass inside act(), so the rAF draw loop and the fetches run
 *  and every state update they cause is flushed before the next look. */
async function waitFor(what: () => boolean, ms: number): Promise<boolean> {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (what()) return true;
    await act(async () => { await new Promise((r) => setTimeout(r, 25)); });
  }
  return what();
}

// ------------------------------------------------------------ the fixture
// The 3x2 the probe walked: the server's own answer, read, never copied.
const FIXTURE_REL = "../../../../../../server/tests/fixtures/mosaic_panels_3x2.json";
function readPanels(): { centre: { ra_hours: number; dec_deg: number }; panels: SkyPanel[];
                         fov: { fov_x_deg: number; fov_y_deg: number } } {
  let text: string;
  try {
    text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE_REL}: ${(e as Error).message}`);
  }
  const fx = JSON.parse(text);
  return {
    centre: { ra_hours: fx.request.ra_hours, dec_deg: fx.request.dec_deg },
    panels: fx.response.panels.map((p: any) => ({
      row: p.row, col: p.col, ra_hours: p.ra_hours, dec_deg: p.dec_deg,
      rotation_deg: p.rotation_deg, state: "pending",
    })),
    fov: { fov_x_deg: fx.response.frame_fov_x_deg, fov_y_deg: fx.response.frame_fov_y_deg },
  };
}
const FX = readPanels();

const OPTICS = {
  focal_length_mm: 530,
  pixel_size_um: 3.76,
  sensor_width_px: 4144,
  sensor_height_px: 2822,
};

const noop = () => {};
function props(over: Record<string, unknown> = {}): any {
  return {
    frameCentre: FX.centre,
    viewCentre: null,
    moveGrid: false,
    panels: FX.panels,
    panelFov: FX.fov,
    rotationDeg: 0,
    zoomDeg: 8,
    survey: "CDS/P/DSS2/color",
    optics: OPTICS,
    mosaic: { rows: 2, cols: 3, overlap: 0.25 },
    night: false,
    onlineFetch: false,
    readOnly: false,
    onMoveGrid: noop, onFrameCentre: noop, onViewCentre: noop,
    onRotate: noop, onZoom: noop, onPanelTap: noop,
    ...over,
  };
}

/** UX-07's empty state, as SkyCanvas words it when the host gives no text of
 *  its own (FramingSky has no pack status to choose a better one from). */
const EMPTY_STATE = /No sky survey available/;
const LOADING = /LOADING/;
const UNREACHABLE = /Survey unreachable/;

const container = win.document.getElementById("root") as any;
const text = () => String(container.textContent ?? "");
const skyBox = () => container.querySelector('[role="application"]') as any;

// ============================================ the tile engine (the probe's path)
const root = createRoot(container);
act(() => { root.render(createElement(FramingSky, props())); });

await test("control: before any tile has answered, the tile engine is mounted and says LOADING, not the empty state", () => {
  assert(skyBox() != null, "no sky canvas in the document - the fixture is wrong");
  assert(skyBox().querySelector("canvas") != null,
    "the tile engine did not mount (no <canvas> in the sky), so this file would grade the <img> path, not the probe's");
  assert(LOADING.test(text()), `the first-load skeleton is not drawn: ${JSON.stringify(text().slice(0, 200))}`);
  assert(!EMPTY_STATE.test(text()), "the empty state is drawn before any tile has failed");
});

// Mutant "degraded not wired" (FramingSky passes no surveyDegraded, and wires
// neither onSurveyError nor onSurveyLoad, as before this change):
//   failed, 2/5, the two controls green:
//   x a canvas whose tiles all 404 shows UX-07's empty state and no LOADING:
//     after 11 tile 404s the modal's sky shows no empty state; it reads "MOVE
//     SKYMOVE GRIDLOADING color…NW1.46″/px8.00° wide1-1, pending1-2,
//     pending1-3, pending2-3, pending2-2, pending2-1, pending"
//   x when the tiles come back, the engine's first drawn frame clears the
//     degraded state: precondition: the sky is not degraded, so there is
//     nothing for the tiles to clear: "MOVE SKYMOVE GRIDLOADING
//     color…NW1.46″/px8.00° wide1-1, pending1-2, pending1-3, pending2-3,
//     pending2-2, pending2-1, pending"
//   x on the <img> path a failed cutout shows the empty state and no LOADING:
//     after the cutout failed the <img> path shows no empty state; it reads
//     "MOVE SKYMOVE GRIDLOADING color…NW1.46″/px8.00° wide1-1, pending1-2,
//     pending1-3, pending2-3, pending2-2, pending2-1, pending"
await test("a canvas whose tiles all 404 shows UX-07's empty state and no LOADING", async () => {
  const shown = await waitFor(() => EMPTY_STATE.test(text()), 3000);
  assert(net.tileCalls >= 8,
    `only ${net.tileCalls} tile requests were made, under the engine's 8-failure threshold - the fixture never degraded`);
  assert(shown,
    `after ${net.tileCalls} tile 404s the modal's sky shows no empty state; it reads ${JSON.stringify(text().slice(0, 160))}`);
  assert(!LOADING.test(text()),
    `LOADING is still drawn beside the empty state after ${net.tileCalls} tile 404s`);
});

// Mutant "load never clears" (FramingSky wires onSurveyError but not
// onSurveyLoad, so nothing ever takes the state down):
//   failed, 4/5 (the canvas's status line opens with a warning-sign glyph,
//     written [warning sign] here):
//   x when the tiles come back, the engine's first drawn frame clears the
//     degraded state: the engine drew its tiles and the sky still says the
//     survey is unreachable: "MOVE SKYMOVE GRIDSurvey unreachable, retrying,
//     showing schematicNW1.46″/px3.00° wide1-1, pending1-2, pending2-2,
//     pending2-1, pending[warning sign] Survey unreachable — schematic framing;
//     retrying automatically."
await test("when the tiles come back, the engine's first drawn frame clears the degraded state", async () => {
  assert(EMPTY_STATE.test(text()) && UNREACHABLE.test(text()),
    `precondition: the sky is not degraded, so there is nothing for the tiles to clear: ${JSON.stringify(text().slice(0, 200))}`);
  net.tiles = "ok";
  // The failed tiles sit in the engine's 45 s negative cache, so the sky is
  // moved to fresh tiles, which is what a pan or zoom back to fetchable sky
  // does in the modal.
  act(() => { root.render(createElement(FramingSky, props({ zoomDeg: 3 }))); });
  const calls = net.tileCalls;
  // The in-canvas empty state is drawn only until the engine's first drawn
  // frame (its own first-draw flag), whatever the host does, so its going is
  // what says the tiles really arrived. The degraded state itself is the
  // host's, and only onSurveyLoad clears it: the status line under the canvas
  // and the screen reader's line both say "Survey unreachable" until then.
  const drew = await waitFor(() => !EMPTY_STATE.test(text()) && net.tileCalls > calls, 3000);
  assert(drew,
    `the tiles were made to answer and the engine never drew one (${net.tileCalls - calls} requests since): ` +
    JSON.stringify(text().slice(0, 200)));
  const cleared = await waitFor(() => !UNREACHABLE.test(text()), 1000);
  assert(cleared,
    `the engine drew its tiles and the sky still says the survey is unreachable: ${JSON.stringify(text().slice(0, 200))}`);
  assert(!LOADING.test(text()), "LOADING came back after the tiles answered");
});

act(() => { root.unmount(); });

// ================================================ the <img> cutout pipeline
// A survey the tile engine has no slug for takes the <img> path even with
// WebGL; so does every survey on a browser without it.
const root2 = createRoot(container);
act(() => {
  root2.render(createElement(FramingSky, props({ survey: "CDS/P/Mellinger/color" })));
});

await test("control: a survey with no tile slug takes the <img> path and says LOADING until its first answer", () => {
  assert(skyBox().querySelector("canvas") == null,
    "the tile engine mounted for a survey it has no slug for - this half would grade the wrong path");
  assert(LOADING.test(text()), `the <img> path's first-load skeleton is not drawn: ${JSON.stringify(text().slice(0, 200))}`);
  assert(!EMPTY_STATE.test(text()), "the <img> path shows the empty state before its cutout has failed");
});

// Mutant "img skeleton ungated" (SkyCanvas's <img>-path skeleton loses its
// `!surveyDegraded` and the <img>-path empty state is not drawn, as before
// this change):
//   failed, 4/5:
//   x on the <img> path a failed cutout shows the empty state and no LOADING:
//     after the cutout failed the <img> path shows no empty state; it reads
//     "MOVE SKYMOVE GRIDSurvey unreachable, retrying, showing schematicLOADING
//     color…NW1.46″/px8.00° wide1-1, pending1-2, pending1-3, pending2-3,
//     pending2-2, pending2-"
await test("on the <img> path a failed cutout shows the empty state and no LOADING", async () => {
  const shown = await waitFor(() => EMPTY_STATE.test(text()), 3000);
  assert(net.cutoutCalls >= 1, "the <img> path never asked for its cutout - the fixture never degraded");
  assert(shown,
    `after the cutout failed the <img> path shows no empty state; it reads ${JSON.stringify(text().slice(0, 160))}`);
  assert(!LOADING.test(text()), "LOADING is still drawn beside the empty state on the <img> path");
});

act(() => { root2.unmount(); });

// ------------------------------------------------------------------- report
dom.window.close();
const total = passed + failed;
console.log(`framingSkyDegraded.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
