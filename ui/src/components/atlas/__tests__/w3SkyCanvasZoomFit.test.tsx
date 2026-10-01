// w3SkyCanvasZoomFit.test.tsx - WP-24b (a): ZOOM_MAX fitted to the configured
// mosaic's extent, with a notice when the survey is too coarse at that zoom
// (#182, backlog ruling D-nn n/a; fix shape per
// docs/superpowers/plans/2026-09-30-open-issue-backlog.md WP-24b).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/atlas/__tests__/w3SkyCanvasZoomFit.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT WAS WRONG. `ZOOM_MAX` was a fixed 10 deg, so the framing modal's own
// zoom-out gesture (wheel, +/-) could never reach a scale wide enough to show
// a mosaic larger than that, and gave no reason why it stopped short. A 10x10
// grid of 2 deg panels needs 20 deg; a 20x20 grid needs 40.
//
// HOW THIS GRADES IT. A controlled Harness holds `fovZoomDeg` in state and
// feeds it back through `onZoom`, so repeated "-" (zoom out) keypresses walk
// the SAME controlled-prop loop a real framing modal runs; after enough
// presses the value converges on whatever ceiling the canvas enforces. An
// ordinary (1x1) view is run as a control: the fix must not raise the
// ceiling when there is no configured mosaic to fit. The coarse-survey cases
// use a `panelFov` of exactly 2 deg (not derived from optics, so the numbers
// here are hand round, not borrowed from SkyCanvas's own pixel-scale math)
// and a survey slug absent from SURVEY_SLUGS, which keeps the tile engine out
// regardless of the test machine's WebGL support (`useTileEngine` gates on
// `surveySlug !== null`) so the fixed-width `<img>` cutout path is exercised.
//
// Every number below (the 20 deg and 40 deg ceilings, the 38 px estimate) was
// confirmed empirically against this build before being written down here
// (scratchpad probe, not published) rather than hand-derived and trusted.

/* eslint-disable @typescript-eslint/no-explicit-any */

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
win.HTMLCanvasElement.prototype.getContext = () => null;
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};
win.fetch = async () => { throw new Error("no network in this fixture"); };

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

const { createElement, act, useState } = await import("react");
const { createRoot } = await import("react-dom/client");
const { SkyCanvas } = await import("../SkyCanvas");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function near(a: number, b: number, tol: number, what: string): void {
  assert(Math.abs(a - b) <= tol, `${what}: got ${a}, expected ${b} (+-${tol})`);
}

const OPTICS = {
  focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 4144, sensor_height_px: 2822,
};
/** Hand round, not derived from OPTICS: the grid-extent arithmetic below
 *  (cols * fov, no overlap) is then checkable by a reader without a calculator. */
const PANEL_FOV = { fov_x_deg: 2, fov_y_deg: 2 };
const CENTER = { ra_hours: 5.0, dec_deg: 10.0 };
/** One row/col of a mosaic, just enough to set `panelsMode` so `panelFov` (not
 *  the live optics) decides the per-panel field; its own position is not what
 *  either test below reads. */
const ONE_PANEL = [{ row: 0, col: 0, ra_hours: CENTER.ra_hours, dec_deg: CENTER.dec_deg, state: "pending" as const }];

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

let currentFov = 0;
function Harness(p: { mosaic: { rows: number; cols: number; overlap: number }; initial: number;
                       mode?: "survey" | "schematic"; survey?: string }): any {
  const [fov, setFov] = useState(p.initial);
  currentFov = fov;
  return createElement(SkyCanvas, {
    center: CENTER,
    rotationDeg: 0,
    survey: p.survey ?? "schematic",
    mode: p.mode ?? "schematic",
    stretch: "linear",
    fovZoomDeg: fov,
    optics: OPTICS,
    panels: ONE_PANEL,
    panelFov: PANEL_FOV,
    mosaic: p.mosaic,
    night: false,
    onCenterChange: () => {},
    onRotate: () => {},
    onZoom: (v: number) => setFov(v),
  } as any);
}

const box = () => container.querySelector('[role="application"]') as any;
/** A real "-" keydown, bubbled to the React root's delegated listener (React
 *  17+ attaches there, not to `document`), exactly as a browser's own keydown
 *  would reach SkyCanvas's `onKeyDown`. */
function zoomOutKey(): void {
  const ev = new win.KeyboardEvent("keydown", { key: "-", bubbles: true, cancelable: true });
  act(() => { box().dispatchEvent(ev); });
}

// ========================================================= the zoom ceiling
// Mutant "zoom ceiling never fitted" (`const zoomMax = ZOOM_MAX;`, dropping
// the grid-extent calc): failed, 1/5:
//   x a 10x10 grid of 2 deg panels can be zoomed all the way out to see it
//     whole: 20 keydowns from 8 deg converged on 10, not 20 - the framing
//     modal can never show the whole grid
test("a 10x10 grid of 2 deg panels can be zoomed all the way out to see it whole", () => {
  act(() => {
    root.render(createElement(Harness, { mosaic: { rows: 10, cols: 10, overlap: 0 }, initial: 8 }));
  });
  for (let i = 0; i < 20; i++) zoomOutKey();
  // 10 panels of 2 deg, no overlap: 20 deg across. Repeated "-" (*1.12 each,
  // clamped) converges on the ceiling well inside 20 presses from 8 deg
  // (8 * 1.12^20 ~= 77 deg, far past 20, so convergence is on the clamp).
  near(currentFov, 20, 1e-6,
    "a 10x10 grid of 2 deg panels can be zoomed all the way out to see it whole");
});

// Control: an ordinary (1x1) view's ceiling is untouched by the fix - a
// configured mosaic is what raises it, not merely having optics.
test("control: a plain 1x1 view's zoom ceiling is still 10 deg", () => {
  act(() => {
    root.render(createElement(Harness, { mosaic: { rows: 1, cols: 1, overlap: 0 }, initial: 8 }));
  });
  for (let i = 0; i < 20; i++) zoomOutKey();
  near(currentFov, 10, 1e-6, "control: a plain 1x1 view's zoom ceiling is still 10 deg");
});

// ======================================================= the coarseness notice
// Mutant "coarse notice never computed" (`const surveyCoarse = false;`):
// failed, 1/6:
//   x a mosaic zoomed out to fit reads as coarse, and says so: no
//     [data-role="survey-coarse"] notice at 40 deg for a 20x20 grid of 2 deg
//     panels
test("a mosaic zoomed out to fit reads as coarse, and says so", () => {
  // 20 panels of 2 deg, no overlap: 40 deg across; the `<img>` pipeline's
  // fixed SURVEY_CUTOUT_PX=768 then maps each 2 deg panel onto (2/40)*768
  // = 38.4 source px, under PANEL_COARSE_PX=48.
  act(() => {
    root.render(createElement(Harness, {
      mosaic: { rows: 20, cols: 20, overlap: 0 }, initial: 8,
      mode: "survey", survey: "CDS/P/UNKNOWN", // absent from SURVEY_SLUGS: no tile engine
    }));
  });
  for (let i = 0; i < 20; i++) zoomOutKey();
  near(currentFov, 40, 1e-6, "precondition: the grid did not zoom out to its full 40 deg extent");
  const notice = container.querySelector('[data-role="survey-coarse"]');
  assert(notice != null,
    `no [data-role="survey-coarse"] notice at ${currentFov} deg for a 20x20 grid of 2 deg panels`);
  assert(/38 px/.test(notice.textContent ?? ""),
    `the coarse notice's own px estimate is wrong: ${JSON.stringify(notice.textContent)}`);
});

// Control: a plain single-frame survey view, well inside its own field, never
// claims to be coarse - the notice is a mosaic-only concern (#182).
test("control: a plain 1x1 survey view never shows the coarse notice", () => {
  act(() => {
    root.render(createElement(Harness, {
      mosaic: { rows: 1, cols: 1, overlap: 0 }, initial: 2,
      mode: "survey", survey: "CDS/P/UNKNOWN",
    }));
  });
  assert(container.querySelector('[data-role="survey-coarse"]') === null,
    "control: a plain 1x1 survey view shows the coarse notice - it should be a mosaic-only concern");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
dom.window.close();
const total = passed + failed;
console.log(`w3SkyCanvasZoomFit.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
