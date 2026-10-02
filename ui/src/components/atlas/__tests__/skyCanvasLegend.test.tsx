// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// skyCanvasLegend.test.tsx - the "Object size" legend in panel mode keeps off
// the panel labels (#425, mosaic slice S7; spec 2026-09-23 flows mosaic, 2.3).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/atlas/__tests__/skyCanvasLegend.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT WAS WRONG. SkyCanvas pinned the legend at the grid centre's height, 6 px
// right of the object's ellipse. On a grid with an odd number of rows the
// middle row's panel labels are centred on that same height, and for an object
// about the grid's size (M31, 190') the ellipse's right edge falls on the
// west-most one (right, on this east-left chart). A throwaway probe in a
// scratch copy put it over 2-1 on a 3x3 and over 1-1 on a 3x1 at 5, 6, 8 and
// 10 deg wide, every time. A panel label is the control that skips its panel,
// so the legend was copy drawn over a control.
//
// HOW THIS GRADES IT. The boxes, not the markup: each panel label's box is
// modelled from what PanelLabels draws (the same model skyCanvasPanels.test
// uses, its chrome read off the classes so a restyle fails by name), and the
// legend's from its own style (left, top, which corner the transform anchors)
// and its text. jsdom measures nothing, so a glyph of the 12 px mono these
// labels use is 7.2 px, SkyCanvas's own fallback advance, which is what its
// placement measures here too. The grids are the server's arithmetic: the 3x2
// is the server's own answer (mosaic_panels_3x2.json), and the 3x3 and 3x1 come
// from the client mirror `mosaicGrid`, held here to reproduce that answer.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// S7-USKY-mut), never in the shared tree (#254), and the failure it produced
// is quoted verbatim.

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
const { mosaicGrid, deproject } = await import("../../../lib/framing");
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
const FIXTURE_REL = "../../../../../server/tests/fixtures/mosaic_panels_3x2.json";
function readFixture(): any {
  let text: string;
  try {
    text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE_REL}, the server answer these grids are graded against: ${(e as Error).message}`);
  }
  return JSON.parse(text);
}
const FX = readFixture();
const REQ = FX.request;

// SkyCanvas measures its own box with a ResizeObserver, which jsdom never
// fires, so it stays at its initial 360 CSS px: every coordinate below is
// deterministic.
const BOX = 360;
const CENTER = { ra_hours: REQ.ra_hours, dec_deg: REQ.dec_deg };
const PANEL_FOV = { fov_x_deg: FX.response.frame_fov_x_deg, fov_y_deg: FX.response.frame_fov_y_deg };
const OPTICS = { focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 4144, sensor_height_px: 2822 };

/** M31 at the grid centre, as the modal frames it when the operator picks it
 *  in WHERE: 190', so the ellipse is about the grid's size. */
const M31 = {
  id: "M31", name: "M31", label: "M31", kind: "galaxy", type: "Galaxy",
  ra_hours: CENTER.ra_hours, dec_deg: CENTER.dec_deg, mag: 3.4, size_arcmin: 190,
} as any;

/** A grid laid out by the client mirror, numbered in run order the way the
 *  modal numbers it, so each label carries its order chip (the widest label
 *  there is). */
function grid(rows: number, cols: number): SkyPanel[] {
  return mosaicGrid({
    ra_hours: REQ.ra_hours, dec_deg: REQ.dec_deg, rows, cols, overlap: REQ.overlap,
    rotation_deg: REQ.rotation_deg, fov_x_deg: REQ.fov_x_deg, fov_y_deg: REQ.fov_y_deg,
  }).map((p, i) => ({ ...p, order: i + 1, state: "pending" as const }));
}
const FIXTURE_3X2: SkyPanel[] = FX.response.panels.map((p: any, i: number) => ({
  row: p.row, col: p.col, ra_hours: p.ra_hours, dec_deg: p.dec_deg,
  rotation_deg: p.rotation_deg, order: i + 1, state: "pending" as const,
}));

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
      fovZoomDeg: 8,
      optics: OPTICS,
      mosaic: { rows: 1, cols: 1, overlap: REQ.overlap },
      catalogTarget: M31,
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

// ------------------------------------------------------------- the boxes
const GLYPH = 7.2;
const LEGEND = "Object size";
interface Box { x: number; y: number; w: number; h: number }
const meets = (a: Box, b: Box) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
const boxStr = (b: Box) => `(${b.x.toFixed(1)}, ${b.y.toFixed(1)}, ${b.w.toFixed(1)} x ${b.h.toFixed(1)})`;
const hasClass = (el: any, c: string) => String(el.className).split(/\s+/).includes(c);

const legendEl = () =>
  (Array.from(container.querySelectorAll("span")) as any[])
    .find((el) => (el.textContent ?? "").trim() === LEGEND);

/** The legend's box in CSS px: `left`/`top` are the anchor, and the transform
 *  says which corner of the box it is (translateY(-50%): the middle of the
 *  left edge; translate(-100%, -50%): the middle of the right edge). Its width
 *  is its text plus px-1 each side, its height its 18 px leading; the classes
 *  are read, so a restyle fails here by name rather than leaving the model,
 *  and SkyCanvas's own box for it, quietly wrong. */
function legendBox(el: any): Box {
  for (const c of ["px-1", "whitespace-nowrap", "leading-[18px]", "text-[12px]"]) {
    assert(hasClass(el, c),
      `the legend's chrome changed (no "${c}" in ${JSON.stringify(String(el.className))}): the box model here and SkyCanvas's both assume it`);
  }
  const w = LEGEND.length * GLYPH + 2 * 4;
  const h = 18;
  const left = parseFloat(el.style.left);
  const top = parseFloat(el.style.top);
  const t = String(el.style.transform).replace(/\s+/g, "");
  assert(t === "translateY(-50%)" || t === "translate(-100%,-50%)",
    `the legend is anchored by a transform this model does not know: ${JSON.stringify(el.style.transform)}`);
  return { x: t === "translateY(-50%)" ? left : left - w, y: top - h / 2, w, h };
}

/** A panel label's box, from what PanelLabels draws: centred on its panel, an
 *  inline-flex plate of px-1 and a 1 px border, its parts gap-1 apart, the
 *  order chip with px-0.5 and a border of its own, 14 px leading plus the
 *  border. As in skyCanvasPanels.test, the chrome is read off the classes. */
function panelLabelBox(el: any): Box {
  for (const c of ["px-1", "gap-1", "border", "leading-[14px]", "inline-flex"]) {
    assert(hasClass(el, c), `a panel label's chrome changed (no "${c}" in ${JSON.stringify(String(el.className))})`);
  }
  const parts = ["panel-flag", "panel-order", "panel-rc"]
    .map((r) => el.querySelector(`[data-role="${r}"]`) as any)
    .filter((p) => p != null);
  let w = 2 * 4 + 2 * 1;
  parts.forEach((p, i) => {
    w += String(p.textContent).length * GLYPH + (i > 0 ? 4 : 0);
    if (p.getAttribute("data-role") === "panel-order") {
      assert(hasClass(p, "px-0.5") && hasClass(p, "border"), `the order chip's chrome changed (${JSON.stringify(p.className)})`);
      w += 2 * 2 + 2 * 1;
    }
  });
  const h = 14 + 2;
  return { x: parseFloat(el.style.left) - w / 2, y: parseFloat(el.style.top) - h / 2, w, h };
}
const panelLabels = (): { name: string; box: Box }[] =>
  (Array.from(container.querySelectorAll('[data-role="panel-label"]')) as any[]).map((el) => ({
    name: `${Number(el.getAttribute("data-row")) + 1}-${Number(el.getAttribute("data-col")) + 1}`,
    box: panelLabelBox(el),
  }));

/** Where the legend went before this change, for a view `zoom` deg wide: 6 px
 *  right of the ellipse at the grid centre's height, parked on the right edge
 *  (and stepped down past the W) only when that anchor was off the canvas. */
function oldSpot(zoom: number, sizeArcmin = 190): Box {
  const r = (sizeArcmin / 60 / 2) * (BOX / zoom);
  const want = BOX / 2 + r + 6;
  const w = LEGEND.length * GLYPH + 2 * 4;
  const clamped = want > BOX - 6;
  return clamped
    ? { x: BOX - 6 - w, y: BOX / 2 + 22 - 9, w, h: 18 }
    : { x: want, y: BOX / 2 - 9, w, h: 18 };
}

// ================================================================= controls
test("control: the client mirror reproduces the server's 3x2, so the 3x3 and 3x1 are the server's arithmetic", () => {
  const mine = grid(REQ.rows, REQ.cols);
  assert(mine.length === FIXTURE_3X2.length, `${mine.length} panels from mosaicGrid, the server answered ${FIXTURE_3X2.length}`);
  for (const s of FIXTURE_3X2) {
    const m = mine.find((p) => p.row === s.row && p.col === s.col);
    assert(m != null, `mosaicGrid has no panel ${s.row},${s.col}`);
    near(m!.ra_hours, s.ra_hours, 1e-6, `panel ${s.row},${s.col} ra`);
    near(m!.dec_deg, s.dec_deg, 1e-6, `panel ${s.row},${s.col} dec`);
  }
});

// Without panels the legend is not this change's business: it goes where it
// always went, anchor and all.
test("control: without panels the legend sits where it always did, 6 px right of the ellipse at the grid centre's height", () => {
  for (const zoom of [5, 8]) {
    render({ fovZoomDeg: zoom });
    const el = legendEl();
    assert(el != null, `at ${zoom} deg the legend is not drawn without panels - the finder cannot see it at all`);
    const want = oldSpot(zoom);
    const got = legendBox(el);
    near(got.x, want.x, 0.01, `at ${zoom} deg, the legend's left edge without panels`);
    near(got.y, want.y, 0.01, `at ${zoom} deg, the legend's top edge without panels`);
  }
});

// In panel mode the first place tried is the old one, so a legend that was in
// nobody's way does not move: a small object on the 3x2 at 8 deg.
//
// Mutant "always dropped in panel mode" (the legend is not drawn whenever
// panels are): failed, 2/5:
//   x control: in panel mode a legend in nobody's way stays where it always
//     was: the legend is not drawn for a 30' object on the 3x2 at 8 deg,
//     where nothing is in its way
//   x in panel mode the legend meets no panel label on a 3x3 and a 3x1 at 5,
//     6, 8 and 10 deg, and stays beside the object: the legend was dropped on
//     the 3x3 at 5 deg, the 3x3 at 6 deg, the 3x3 at 8 deg, the 3x3 at 10
//     deg, the 3x1 at 5 deg, the 3x1 at 6 deg, the 3x1 at 8 deg, the 3x1 at
//     10 deg, where there is room for it beside the ellipse
//   x near the canvas's bottom edge the legend goes over the ellipse, not
//     under it and off the canvas: the legend was dropped beside the moved
//     3x3, where over the ellipse is clear
test("control: in panel mode a legend in nobody's way stays where it always was", () => {
  const small = { ...M31, size_arcmin: 30 };
  render({ panels: FIXTURE_3X2, panelFov: PANEL_FOV, catalogTarget: small, fovZoomDeg: 8 });
  const el = legendEl();
  assert(el != null, "the legend is not drawn for a 30' object on the 3x2 at 8 deg, where nothing is in its way");
  const want = oldSpot(8, 30);
  const got = legendBox(el);
  near(got.x, want.x, 0.01, "the legend's left edge");
  near(got.y, want.y, 0.01, "the legend's top edge");
  for (const pl of panelLabels()) {
    assert(!meets(got, pl.box), `control: the old spot ${boxStr(got)} meets panel ${pl.name}'s label ${boxStr(pl.box)}, so this is not a case with nothing in the way`);
  }
});

// ============================================================ the collision
// Mutant "pinned at the grid centre's height" (in panel mode the legend is
// placed exactly as without panels, at the grid centre's height, as before
// this change): failed, 3/5:
//   x in panel mode the legend meets no panel label on a 3x3 and a 3x1 at 5,
//     6, 8 and 10 deg, and stays beside the object: the 3x3 at 5 deg: the
//     legend (300.0, 171.0, 87.2 x 18.0) is drawn over panel 2-1's label
//     (263.6, 172.0, 48.8 x 16.0)
//   x near the canvas's bottom edge the legend goes over the ellipse, not
//     under it and off the canvas: the legend (243.0, 291.0, 87.2 x 18.0) is
//     drawn over panel 2-1's label (209.7, 292.0, 48.8 x 16.0)
// Mutant "legend ignores the panel labels" (its search checks the canvas's
// other furniture but not the panel labels): failed, 3/5:
//   x in panel mode the legend meets no panel label on a 3x3 and a 3x1 at 5,
//     6, 8 and 10 deg, and stays beside the object: the 3x3 at 5 deg: the
//     legend (6.0, 171.0, 87.2 x 18.0) is drawn over panel 2-3's label (47.6,
//     172.0, 48.8 x 16.0)
//   x near the canvas's bottom edge the legend goes over the ellipse, not
//     under it and off the canvas: the legend (243.0, 291.0, 87.2 x 18.0) is
//     drawn over panel 2-1's label (209.7, 292.0, 48.8 x 16.0)
test("in panel mode the legend meets no panel label on a 3x3 and a 3x1 at 5, 6, 8 and 10 deg, and stays beside the object", () => {
  let drawn = 0;
  const dropped: string[] = [];
  for (const [rows, cols] of [[3, 3], [1, 3]] as const) {
    const panels = grid(rows, cols);
    for (const zoom of [5, 6, 8, 10]) {
      const where = `the ${cols}x${rows} at ${zoom} deg`;
      render({ panels, panelFov: PANEL_FOV, fovZoomDeg: zoom, mosaic: { rows, cols, overlap: REQ.overlap } });
      const labels = panelLabels();
      assert(labels.length === rows * cols, `${where}: ${labels.length} panel labels drawn, expected ${rows * cols}`);
      // The case is a real collision: where the legend used to go, a panel
      // label is in the way (the issue's table). A case where it was not
      // would grade nothing.
      const old = oldSpot(zoom);
      assert(labels.some((pl) => meets(old, pl.box)),
        `precondition: on ${where} the old spot ${boxStr(old)} meets no panel label, so this case cannot tell a moved legend from a pinned one`);
      const el = legendEl();
      if (!el) { dropped.push(where); continue; }
      drawn++;
      const b = legendBox(el);
      for (const pl of labels) {
        assert(!meets(b, pl.box), `${where}: the legend ${boxStr(b)} is drawn over panel ${pl.name}'s label ${boxStr(pl.box)}`);
      }
      // On the canvas: the label layer does not clip, and a legend past the
      // edge is cut by the modal's sky or widens the page (it once did, at
      // 390 px).
      assert(b.x >= 0 && b.y >= 0 && b.x + b.w <= BOX && b.y + b.h <= BOX,
        `${where}: the legend ${boxStr(b)} runs off the ${BOX} px canvas`);
      // Still the ellipse's legend: touching it or within the 6 px gap it
      // always kept from the rim, not parked in a corner that names nothing.
      // The ellipse is a circle here (SkyCanvas draws it with both semi-axes
      // the catalogue's size), centred on the grid.
      const r = (M31.size_arcmin / 60 / 2) * (BOX / zoom);
      const nx = Math.min(Math.max(BOX / 2, b.x), b.x + b.w) - BOX / 2;
      const ny = Math.min(Math.max(BOX / 2, b.y), b.y + b.h) - BOX / 2;
      assert(Math.hypot(nx, ny) <= r + 6 + 0.5,
        `${where}: the legend ${boxStr(b)} is ${(Math.hypot(nx, ny) - r).toFixed(1)} px off the ellipse's rim (radius ${r.toFixed(1)} px), so it names nothing`);
    }
  }
  // Each of these grids has somewhere clear beside M31, so the legend moves
  // rather than going: a legend dropped wherever it once collided would pass
  // every check above by drawing nothing.
  assert(drawn === 8, `the legend was dropped on ${dropped.join(", ")}, where there is room for it beside the ellipse`);
});

// Near an edge of the canvas the place the search would take next can be past
// it. Here a 3x3 at 10 deg is moved 120 px down: every place beside the rim is
// blocked by a panel label, and under the ellipse is past the bottom edge, in
// the gap between the two readouts where no other box would stop it. The
// legend goes over the ellipse instead.
//
// Mutant "legend may leave the canvas" (the search's on-canvas test dropped,
// so only the furniture decides): failed, 4/5:
//   x near the canvas's bottom edge the legend goes over the ellipse, not
//     under it and off the canvas: the legend (136.4, 363.0, 87.2 x 18.0) runs
//     off the 360 px canvas
// A first version of this case moved a 3x1 to 20 px above the bottom edge at
// 8 deg; the mutant passed it 5/5, because the bottom readouts' reserved
// boxes already ruled out every place past the edge, so the on-canvas test
// never decided anything there.
test("near the canvas's bottom edge the legend goes over the ellipse, not under it and off the canvas", () => {
  const zoom = 10;
  const ppd = BOX / zoom;
  // 120 px straight down from the view centre (south, on this north-up chart).
  const frame = deproject(0, -120 / ppd, CENTER.ra_hours, CENTER.dec_deg);
  const panels = mosaicGrid({
    ra_hours: frame.ra_hours, dec_deg: frame.dec_deg, rows: 3, cols: 3, overlap: REQ.overlap,
    rotation_deg: 0, fov_x_deg: REQ.fov_x_deg, fov_y_deg: REQ.fov_y_deg,
  }).map((p, i) => ({ ...p, order: i + 1, state: "pending" as const }));
  render({ panels, panelFov: PANEL_FOV, fovZoomDeg: zoom, frameCenter: frame,
           catalogTarget: { ...M31, ra_hours: frame.ra_hours, dec_deg: frame.dec_deg },
           mosaic: { rows: 3, cols: 3, overlap: REQ.overlap } });
  const labels = panelLabels();
  assert(labels.length === 9, `${labels.length} panel labels drawn for the moved 3x3, expected 9`);
  const ccy = BOX / 2 + 120;
  const mid = labels.find((pl) => pl.name === "2-2")!;
  near(mid.box.y + mid.box.h / 2, ccy, 0.5, "the moved grid's centre row (the grid did not land where this case needs it)");
  const r = (M31.size_arcmin / 60 / 2) * ppd;
  const w = LEGEND.length * GLYPH + 8;
  // Under the ellipse, centred on it, is past the bottom edge.
  assert(ccy + r + 6 + 18 > BOX,
    `precondition: under the ellipse (${(ccy + r + 6).toFixed(1)} to ${(ccy + r + 24).toFixed(1)}) is on the canvas, so the edge is never tested`);
  const under = { x: BOX / 2 - w / 2, y: ccy + r + 6, w, h: 18 };
  assert(!labels.some((pl) => meets(under, pl.box)),
    `precondition: a panel label already rules out the place under the ellipse ${boxStr(under)}, so the edge is not what decides`);
  const el = legendEl();
  assert(el != null, "the legend was dropped beside the moved 3x3, where over the ellipse is clear");
  const b = legendBox(el);
  assert(b.x >= 0 && b.y >= 0 && b.x + b.w <= BOX && b.y + b.h <= BOX, `the legend ${boxStr(b)} runs off the ${BOX} px canvas`);
  for (const pl of labels) {
    assert(!meets(b, pl.box), `the legend ${boxStr(b)} is drawn over panel ${pl.name}'s label ${boxStr(pl.box)}`);
  }
});

// Where nothing beside the object is clear, the legend goes (#425: it "moves
// clear, or is dropped when nowhere is clear"). A 7x7 at 14 deg packs the
// labels tighter than the legend fits: a column pitch of 1.5 deg is 38.6 px,
// under a label's 48.8, and a row pitch of 0.9975 deg is 25.6 px, leaving
// 9.6 px between rows for an 18 px legend. So every place near the ellipse's
// rim meets a label, which the case proves by trying every place on the
// canvas rather than trusting the search's own order, and the old spot is
// among them.
//
// Mutant "never dropped" (nowhere clear, the legend falls back to the old spot
// instead of `return null`), added by the S7-USKY verifier in a private
// scratch copy (S7-USKY-verify-mut): passed 5/5 before this case existed,
// so the drop branch went ungraded; with it, failed, 5/6:
//   x where nothing beside the object is clear of the panel labels, the
//     legend is not drawn: nowhere beside the object is clear and the legend
//     is drawn anyway, at (226.7, 171.0, 87.2 x 18.0), over panel 4-3's label
//     (190.6, 172.0, 56.0 x 16.0)
// The totals quoted for the mutants above predate this case: re-run, "pinned
// at the grid centre's height" fails 3/6 (this case with it) and "legend
// over the ellipse skipped" (the verifier's) fails 5/6 on the edge case alone.
test("where nothing beside the object is clear of the panel labels, the legend is not drawn", () => {
  const zoom = 14;
  const [rows, cols] = [7, 7];
  render({ panels: grid(rows, cols), panelFov: PANEL_FOV, fovZoomDeg: zoom,
           mosaic: { rows, cols, overlap: REQ.overlap } });
  const labels = panelLabels();
  assert(labels.length === rows * cols, `${labels.length} panel labels drawn for the 7x7, expected ${rows * cols}`);
  const old = oldSpot(zoom);
  assert(labels.some((pl) => meets(old, pl.box)),
    `precondition: the old spot ${boxStr(old)} meets no panel label on the 7x7, so a legend left there would pass`);
  // Every legend-sized box on the canvas within the 6 px gap of the rim, at
  // 1 px steps, meets a panel label (no padding: stricter than the 3 px the
  // canvas keeps).
  const r = (M31.size_arcmin / 60 / 2) * (BOX / zoom);
  const w = LEGEND.length * GLYPH + 2 * 4;
  const h = 18;
  let open: Box | null = null;
  for (let y = 0; y + h <= BOX && !open; y++) {
    for (let x = 0; x + w <= BOX; x++) {
      const b = { x, y, w, h };
      const nx = Math.min(Math.max(BOX / 2, x), x + w) - BOX / 2;
      const ny = Math.min(Math.max(BOX / 2, y), y + h) - BOX / 2;
      if (Math.hypot(nx, ny) > r + 6 + 0.5) continue;
      if (!labels.some((pl) => meets(b, pl.box))) { open = b; break; }
    }
  }
  assert(open === null,
    `precondition: ${open ? boxStr(open) : ""} beside the object is clear of every panel label, so this is not a case where nowhere is`);
  const el = legendEl();
  if (el) {
    const b = legendBox(el);
    const hit = labels.find((pl) => meets(b, pl.box));
    throw new Error(`nowhere beside the object is clear and the legend is drawn anyway, at ${boxStr(b)}` +
      (hit ? `, over panel ${hit.name}'s label ${boxStr(hit.box)}` : ""));
  }
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
dom.window.close();
const total = passed + failed;
console.log(`skyCanvasLegend.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
