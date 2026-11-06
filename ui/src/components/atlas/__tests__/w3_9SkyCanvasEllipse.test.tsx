// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_9SkyCanvasEllipse.test.tsx - the Atlas draws a catalogue object as its
// ellipse, at its position angle, when the row has one (#181, consolidation
// WP 3.9); a circle across the long axis otherwise.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/atlas/__tests__/w3_9SkyCanvasEllipse.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The canvas passed the semi-major axis for both radii. Now a row with a minor
// axis AND an angle is drawn with two different radii, the major axis laid PA
// degrees east of north. The Atlas puts east on the LEFT, so the major axis
// (vertical before the turn) is turned counter-clockwise by PA, an SVG rotate
// of -PA about the centre. A row with a minor axis and no angle orients
// nothing, so it is a circle across the long axis; so is a row with neither.
// Both draw paths are graded, the framing modal's panel layer (`PanelShapes`)
// and the single-frame overlay (`FovOverlay`).
//
// NAMED MUTANTS: see the report of WP 3.9; each is run from a byte backup of
// SkyCanvas.tsx and restored.

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
const { mosaicGrid } = await import("../../../lib/framing");
type SkyPanel = import("../PanelLayer").SkyPanel;

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

// ------------------------------------------------------------ the fixture
// SkyCanvas measures its own box with a ResizeObserver, which jsdom never
// fires, so it stays at its initial 360 CSS px: every coordinate is
// deterministic. The view box is 1000 and `fovZoomDeg` 8 gives 125 px a degree.
const FOV_ZOOM_DEG = 8;
const PX_PER_DEG = 1000 / FOV_ZOOM_DEG;
const CENTER = { ra_hours: 0.7123, dec_deg: 41.269 };
const PANEL_FOV = { fov_x_deg: 2.0, fov_y_deg: 1.33 };
const OPTICS = { focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 4144, sensor_height_px: 2822 };
const OVERLAP = 0.25;

/** A galaxy 190 x 70 arcminutes with its major axis 35 degrees east of north
 *  (the test's numbers, not the catalogue's). */
const GALAXY = {
  id: "TEST 1", name: "Test galaxy", type: "Galaxy",
  ra_hours: CENTER.ra_hours, dec_deg: CENTER.dec_deg, mag: 3.4,
  size_arcmin: 190, minor_arcmin: 70, pa_deg: 35,
} as any;
const SEMI_MAJOR_PX = (190 / 120) * PX_PER_DEG;
const SEMI_MINOR_PX = (70 / 120) * PX_PER_DEG;

const PANELS: SkyPanel[] = mosaicGrid({
  ra_hours: CENTER.ra_hours, dec_deg: CENTER.dec_deg, rows: 2, cols: 2, overlap: OVERLAP,
  rotation_deg: 0, fov_x_deg: PANEL_FOV.fov_x_deg, fov_y_deg: PANEL_FOV.fov_y_deg,
}).map((p, k) => ({ ...p, order: k + 1, state: "pending" as const }));

// ---------------------------------------------------------------- the render
const container = win.document.getElementById("root") as any;
const root = createRoot(container);
function render(over: Record<string, unknown> = {}): void {
  act(() => {
    root.render(createElement(SkyCanvas, {
      center: CENTER,
      rotationDeg: 0,
      survey: "schematic",
      mode: "schematic",
      stretch: "linear",
      fovZoomDeg: FOV_ZOOM_DEG,
      optics: OPTICS,
      mosaic: { rows: 1, cols: 1, overlap: OVERLAP },
      catalogTarget: GALAXY,
      night: true,
      // The modal's MOVE SKY / MOVE GRID pair rides here; SkyCanvas keeps its
      // column clear, and so must the legend.
      overlayControls: createElement("div", { "data-testid": "overlay" }),
      onCenterChange: () => {},
      onRotate: () => {},
      onZoom: () => {},
      ...over,
    } as any));
  });
}


/** The drawn object ellipse: its radii, centre and transform. */
function ellipse(): { rx: number; ry: number; transform: string | null; cx: number; cy: number } {
  const els = Array.from(container.querySelectorAll('[data-testid="object-ellipse"]')) as any[];
  assert(els.length === 1, `expected one object ellipse, found ${els.length}`);
  const el = els[0];
  return {
    rx: parseFloat(el.getAttribute("rx")), ry: parseFloat(el.getAttribute("ry")),
    cx: parseFloat(el.getAttribute("cx")), cy: parseFloat(el.getAttribute("cy")),
    transform: el.getAttribute("transform"),
  };
}
const turnOf = (e: { transform: string | null }): { deg: number; about: [number, number] } | null => {
  const m = /^rotate\((-?[\d.]+) (-?[\d.]+) (-?[\d.]+)\)$/.exec(e.transform ?? "");
  return m ? { deg: parseFloat(m[1]), about: [parseFloat(m[2]), parseFloat(m[3])] } : null;
};

const MODES: Array<[string, Record<string, unknown>]> = [
  ["in the panel layer", { panels: PANELS, panelFov: PANEL_FOV, mosaic: { rows: 2, cols: 2, overlap: OVERLAP } }],
  ["in the single-frame overlay", {}],
];

for (const [where, over] of MODES) {
  test(`a shaped row draws two different radii at the stated angle ${where}`, () => {
    render(over);
    const e = ellipse();
    near(e.ry, SEMI_MAJOR_PX, 0.01, "the major radius is vertical before the turn");
    near(e.rx, SEMI_MINOR_PX, 0.01, "the minor radius");
    assert(e.rx !== e.ry, `radii (${e.rx.toFixed(1)}, ${e.ry.toFixed(1)}) are equal where the ellipse is (${SEMI_MINOR_PX.toFixed(1)}, ${SEMI_MAJOR_PX.toFixed(1)})`);
    const turn = turnOf(e);
    assert(turn !== null, `no turn on the ellipse where PA 35 says rotate(-35 ...): ${JSON.stringify(e.transform)}`);
    near(turn!.deg, -35, 1e-9, "the turn is minus the position angle (east is on the left)");
    near(turn!.about[0], e.cx, 1e-9, "turned about the ellipse's centre, x");
    near(turn!.about[1], e.cy, 1e-9, "turned about the ellipse's centre, y");
  });

  test(`a due-north angle of 0 is an angle, not a missing one ${where}`, () => {
    render({ ...over, catalogTarget: { ...GALAXY, pa_deg: 0 } });
    const e = ellipse();
    assert(e.rx !== e.ry, "an angle of 0 drew a circle");
    near(turnOf(e)?.deg ?? NaN, 0, 1e-9, "a turn of 0");
  });

  test(`a row with a minor axis and no angle is a circle across the long axis ${where}`, () => {
    render({ ...over, catalogTarget: { ...GALAXY, pa_deg: null } });
    const e = ellipse();
    near(e.rx, SEMI_MAJOR_PX, 0.01, "rx");
    near(e.ry, SEMI_MAJOR_PX, 0.01, "ry");
    assert(e.transform === null, `a circle was turned: ${e.transform}`);
  });

  test(`a row with no shape is a circle across the long axis ${where}`, () => {
    render({ ...over, catalogTarget: { ...GALAXY, minor_arcmin: null, pa_deg: null } });
    const e = ellipse();
    near(e.rx, SEMI_MAJOR_PX, 0.01, "rx");
    near(e.ry, SEMI_MAJOR_PX, 0.01, "ry");
    assert(e.transform === null, `a circle was turned: ${e.transform}`);
  });
}

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
dom.window.close();
const total = passed + failed;
console.log(`w3_9SkyCanvasEllipse.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
