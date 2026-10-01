// w3SkyCanvasEmptyStateOffGrid.test.tsx - #491 remainder (WP-24b new defect,
// backlog wave 3 integration).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/atlas/__tests__/w3SkyCanvasEmptyStateOffGrid.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The `surveyDegraded` empty-state sentence ("No sky survey
// available..." / "The sky survey is not arriving...", `data-role=
// "survey-empty"`) was always drawn `absolute inset-0 grid place-items-center`
// - dead centre of the WHOLE canvas box. In panel mode (a mosaic's `panels`
// given) that box is also where the grid and each panel's label
// (`panelLabelBoxes`, #385) are drawn, so the sentence painted straight
// through the grid and over panel labels.
//
// THE FIX. In panel mode the sentence now renders on the line under the
// canvas instead (same `data-role`, same text, outside the `role=
// "application"` box the grid and its labels live inside) - this file checks
// that the element is a sibling of the canvas box, not inside it. Plain
// survey mode (no panels) keeps the original centred-in-canvas placement,
// which this file also pins so it cannot regress the other way.
//
// This mounts SkyCanvas directly (as w3SkyCanvasZoomFit.test.tsx does, not
// through FramingSky's network-backed TileEngine double), since
// `surveyDegraded` is a plain controlled prop: no fetch simulation is needed
// to reach the empty state, only an unknown survey slug (absent from
// SURVEY_SLUGS, so the <img> pipeline runs, and jsdom draws no real <img>, so
// `shownUrl` never becomes true).

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

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { SkyCanvas } = await import("../SkyCanvas");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const OPTICS = {
  focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 4144, sensor_height_px: 2822,
};
const CENTER = { ra_hours: 5.0, dec_deg: 10.0 };
const PANEL_FOV = { fov_x_deg: 2, fov_y_deg: 2 };
const ONE_PANEL = [{ row: 0, col: 0, ra_hours: CENTER.ra_hours, dec_deg: CENTER.dec_deg, state: "pending" as const }];
// Absent from SURVEY_SLUGS: no tile engine, so the <img> pipeline runs and
// `shownUrl` stays false in jsdom (no real image ever loads).
const UNKNOWN_SURVEY = "CDS/P/UNKNOWN";

const container = win.document.getElementById("root") as any;
const box = () => container.querySelector('[role="application"]') as any;
const emptyEl = () => container.querySelector('[data-role="survey-empty"]') as any;

function mount(panelsMode: boolean): { unmount: () => void } {
  const root = createRoot(container);
  act(() => {
    root.render(createElement(SkyCanvas, {
      center: CENTER,
      rotationDeg: 0,
      survey: UNKNOWN_SURVEY,
      mode: "survey",
      stretch: "linear",
      fovZoomDeg: 8,
      optics: OPTICS,
      mosaic: { rows: 1, cols: 1, overlap: 0 },
      night: false,
      surveyDegraded: true,
      onlineFetch: false,
      ...(panelsMode ? { panels: ONE_PANEL, panelFov: PANEL_FOV } : {}),
      onCenterChange: () => {},
      onRotate: () => {},
      onZoom: () => {},
    } as any));
  });
  return { unmount: () => act(() => { root.unmount(); }) };
}

// Mutant "placement never gated on panelsMode" (SkyCanvas.tsx: the `!panelsMode`
// condition on the in-canvas block dropped, and the below-canvas block's
// `panelsMode` condition dropped too, restoring the single always-centred
// block), run from a byte backup restored byte-identical afterwards.
// Observed ("w3SkyCanvasEmptyStateOffGrid.test: 1/2 passed"):
//   x panel mode: the empty-state sentence is off the grid, on the line under
//   the canvas: the empty-state sentence is drawn INSIDE the canvas box,
//   through the grid and over panel labels

test("survey mode (no panels): the empty-state sentence stays centred inside the canvas", () => {
  const m = mount(false);
  try {
    const b = box();
    assert(b != null, "no canvas box: the fixture is wrong, not the component");
    const el = emptyEl();
    assert(el != null, "no survey-empty element: the fixture is wrong, not the component");
    assert(b.contains(el), "plain survey mode must keep the sentence centred inside the canvas box");
  } finally {
    m.unmount();
  }
});

test("panel mode: the empty-state sentence is off the grid, on the line under the canvas", () => {
  const m = mount(true);
  try {
    const b = box();
    assert(b != null, "no canvas box: the fixture is wrong, not the component");
    const el = emptyEl();
    assert(el != null, "no survey-empty element: the fixture is wrong, not the component");
    assert(!b.contains(el),
      "the empty-state sentence is drawn INSIDE the canvas box, through the grid and over panel labels");
  } finally {
    m.unmount();
  }
});

// ------------------------------------------------------------------- report
dom.window.close();
const total = passed + failed;
console.log(`w3SkyCanvasEmptyStateOffGrid.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
