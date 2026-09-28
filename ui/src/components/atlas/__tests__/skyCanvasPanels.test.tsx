// skyCanvasPanels.test.tsx - the Atlas canvas MOUNTED with a mosaic's panels:
// labels at the server's coordinates, a grid that moves over a still sky, and
// a tap that names a panel (#189 S4 item 2; spec 2026-09-23 flows mosaic, 2.3).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/atlas/__tests__/skyCanvasPanels.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY THE LABELS ARE THE WHOLE POINT. Three layouts of one grid already exist
// in this tree and two of them are wrong about which panel is which:
// FovOverlay's screen-space grid is a point reflection of the server layout
// (its row 0 is drawn at the BOTTOM, its col 0 on the LEFT), and panelRects
// mirrors the columns. The server's panel (0,0) is the north-west corner, so on
// this north-up, east-left chart "1-1" sits TOP RIGHT. A label drawn from
// either screen grid names the wrong patch of sky, and the run then skips or
// reports a panel the operator never meant.
//
// So the positions asserted here come from the SERVER'S answer
// (server/tests/fixtures/mosaic_panels_3x2.json, read, never copied, and kept
// equal to POST /api/framing/mosaic by test_framing_panels_fixture.py), and the
// orientation is also asserted from first principles (north up, east left),
// not only through the projection under test.
//
// The control is everything this canvas did before: skyCanvasDrag,
// skyCanvasMarkers, atlasFov, atlasHonesty and the #/next sky suites run
// unchanged, and the "no panels" cases below pin that the new props are
// additive.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// s4-usky-mut), never in the shared tree (#254), and the failure it produced
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
const { skyToView, fovCornersSky } = await import("../../../lib/atlasFov");
type SkyPanel = import("../PanelLayer").SkyPanel;
type SkyRow = import("../../../lib/skyRegion").SkyRow;

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
interface Fixture {
  request: { ra_hours: number; dec_deg: number; rows: number; cols: number;
             overlap: number; rotation_deg: number; fov_x_deg: number; fov_y_deg: number };
  response: { panels: { row: number; col: number; ra_hours: number; dec_deg: number;
                        rotation_deg: number }[];
              frame_fov_x_deg: number; frame_fov_y_deg: number; total_fov_y_deg: number };
}
function readFixture(): Fixture {
  let text: string;
  try {
    text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE_REL}, the server answer these labels are graded against: ${(e as Error).message}`);
  }
  const fx = JSON.parse(text) as Fixture;
  if (!Array.isArray(fx.response?.panels) || fx.response.panels.length !== 6) {
    throw new Error(`${FIXTURE_REL} does not hold the 3x2's six panels`);
  }
  return fx;
}
const FX = readFixture();
const REQ = FX.request;

const VIEW = 1000;
// SkyCanvas measures its own box with a ResizeObserver, which jsdom never
// fires, so it stays at its initial 360 CSS px. That is what makes every
// coordinate below deterministic.
const BOX = 360;
const FOV_ZOOM = 8;          // the whole 5.0 x 2.33 deg grid inside the square
const PPD = VIEW / FOV_ZOOM;
const CENTER = { ra_hours: REQ.ra_hours, dec_deg: REQ.dec_deg };
const GEOM = { centerRaHours: CENTER.ra_hours, centerDecDeg: CENTER.dec_deg, pxPerDeg: PPD, view: VIEW };
const toCss = (v: number) => (v * BOX) / VIEW;

const OPTICS = {
  focal_length_mm: 530,
  pixel_size_um: 3.76,
  sensor_width_px: 4144,
  sensor_height_px: 2822,
};

const STATES = ["pending", "skipped", "shooting", "done", "set_aside", "pending"] as const;
const PANELS: SkyPanel[] = FX.response.panels.map((p, i) => ({
  row: p.row, col: p.col, ra_hours: p.ra_hours, dec_deg: p.dec_deg,
  rotation_deg: p.rotation_deg, order: i + 1, state: STATES[i],
}));
const PANEL_FOV = { fov_x_deg: FX.response.frame_fov_x_deg, fov_y_deg: FX.response.frame_fov_y_deg };

// ---------------------------------------------------------------- the render
const calls = { center: 0, frame: [] as [number, number][], taps: [] as [number, number][],
                picks: [] as (SkyRow | null)[] };

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
      fovZoomDeg: FOV_ZOOM,
      optics: OPTICS,
      mosaic: { rows: REQ.rows, cols: REQ.cols, overlap: REQ.overlap },
      night: true,
      onPickObject: (r: SkyRow | null) => { calls.picks.push(r); },
      onCenterChange: () => { calls.center++; },
      onRotate: () => {},
      onZoom: () => {},
      ...over,
    } as any));
  });
}
function reset(): void {
  calls.center = 0; calls.frame.length = 0; calls.taps.length = 0; calls.picks.length = 0;
}

const box = () => container.querySelector('[role="application"]') as any;
const geomSvg = () => box().querySelector("svg") as any;
const labels = () => Array.from(container.querySelectorAll('[data-role="panel-label"]')) as any[];
const labelAt = (row: number, col: number) =>
  container.querySelector(`[data-role="panel-label"][data-row="${row}"][data-col="${col}"]`) as any;
/** FovOverlay's frame group: the one `translate(..) rotate(..)` group in the
 *  GEOMETRY svg (the rotate handle's lives in its own svg). */
const fovGroup = () =>
  Array.from(geomSvg().querySelectorAll("g[transform]") as any[])
    .find((el: any) => /^translate\([^)]*\) rotate\(/.test(el.getAttribute("transform"))) as any;
const handleGroup = () =>
  (container.querySelector('[data-role="rotate-handle"]') as any)?.parentElement as any;
function translateOf(el: any): [number, number] {
  const m = /translate\(([-\d.e]+)[ ,]+([-\d.e]+)\)/.exec(el.getAttribute("transform"));
  assert(m !== null, `no translate in ${el.getAttribute("transform")}`);
  return [Number(m![1]), Number(m![2])];
}

function ptr(type: string, x: number, y: number, opts: any = {}): any {
  const ev = new win.Event(type, { bubbles: true, cancelable: true });
  ev.pointerId = opts.pointerId ?? 1;
  ev.pointerType = opts.pointerType ?? "mouse";
  ev.button = opts.button ?? 0;
  ev.buttons = opts.buttons ?? 1;
  ev.isPrimary = true;
  ev.clientX = x;
  ev.clientY = y;
  return ev;
}
const fire = (type: string, x: number, y: number, opts: any = {}) => {
  act(() => { box().dispatchEvent(ptr(type, x, y, opts)); });
};
function tapAt(x: number, y: number): void {
  fire("pointerdown", x, y);
  fire("pointerup", x, y, { buttons: 0 });
}

// ======================================================= controls: no panels
render({ onPanelTap: (r: number, c: number) => { calls.taps.push([r, c]); } });

test("control: with no panels the canvas draws FovOverlay at the centre and no panel layer", () => {
  assert(box() != null, "no canvas in the document - the fixture is wrong");
  assert(labels().length === 0, `${labels().length} panel labels with no panels given`);
  assert(container.querySelector('[data-role="panel"]') === null, "panel shapes with no panels given");
  const fg = fovGroup();
  assert(fg != null, "FovOverlay's frame group is gone without panels - the old picture changed");
  const [x, y] = translateOf(fg);
  assert(x === VIEW / 2 && y === VIEW / 2, `FovOverlay drawn at (${x}, ${y}), not the view centre`);
});

test("control: with no panels a tap on empty sky answers the object picker, never onPanelTap", () => {
  reset();
  tapAt(toCss(VIEW / 2), toCss(VIEW / 2));
  assert(calls.taps.length === 0, `onPanelTap fired ${calls.taps.length}x with no panels drawn`);
  assert(calls.picks.length === 1 && calls.picks[0] === null,
    `the object picker got ${JSON.stringify(calls.picks)}, expected one null`);
});

test("control: a frameCenter equal to the view centre renders exactly what no frameCenter does", () => {
  render({});
  const without = container.innerHTML;
  render({ frameCenter: CENTER });
  const withSame = container.innerHTML;
  assert(without === withSame,
    "frameCenter at the view centre changed the markup - the prop is not additive");
});

// =============================================== labels at the server's sky
render({ panels: PANELS, panelFov: PANEL_FOV,
         onPanelTap: (r: number, c: number) => { calls.taps.push([r, c]); } });

// Mutant "FovOverlay index layout" (each panel placed where FovOverlay draws
// index (row, col): x = cx + (col - (cols-1)/2)*stepX, y = cy + ((rows-1)/2 -
// row)*stepY, in screen px) failed. 1-1 lands BOTTOM LEFT, the point
// reflection spec 2.3 names:
//   x every label sits at skyToView of its own panel centre, within 0.5 px:
//     panel 1-1 label left: got 112.5, expected 247.5 (+-0.5)
//   x on this north-up, east-left chart 1-1 is top right and 2-3 bottom left:
//     1-1 is drawn at (112.5, 202.4), which is not the top-right (north-west)
//     corner of the grid
//   x with panels, frameCenter moves the grid's centre mark and leaves the
//     panels on their sky: label 1-1 left under a moved frameCenter: got
//     112.5, expected 247.5 (+-0.5)
// Mutant "panelRects layout" (the same, rows advancing DOWN from the top, as
// next/hubs/sky/frame/mosaic.ts panelRects lays them) failed the same three;
// 1-1 lands TOP LEFT, the column mirror:
//   x on this north-up, east-left chart 1-1 is top right and 2-3 bottom left:
//     1-1 is drawn at (112.5, 157.6), which is not the top-right (north-west)
//     corner of the grid
test("every label sits at skyToView of its own panel centre, within 0.5 px", () => {
  assert(labels().length === 6, `${labels().length} panel labels drawn, expected 6`);
  for (const p of PANELS) {
    const el = labelAt(p.row, p.col);
    const name = `${p.row + 1}-${p.col + 1}`;
    assert(el != null, `no label for panel ${name}`);
    assert((el.textContent ?? "").includes(name), `panel ${name}'s label reads ${JSON.stringify(el.textContent)}`);
    const want = skyToView(p.ra_hours, p.dec_deg, GEOM)!;
    near(parseFloat(el.style.left), toCss(want.x), 0.5, `panel ${name} label left`);
    near(parseFloat(el.style.top), toCss(want.y), 0.5, `panel ${name} label top`);
  }
});

test("on this north-up, east-left chart 1-1 is top right and 2-3 bottom left", () => {
  // From first principles, not through skyToView: row 1 is the north edge
  // (up), col 1 the west edge (right, because east is left).
  const pos = (r: number, c: number): [number, number] => {
    const el = labelAt(r, c);
    return [parseFloat(el.style.left), parseFloat(el.style.top)];
  };
  const [x11, y11] = pos(0, 0);
  assert(x11 > BOX / 2 && y11 < BOX / 2,
    `1-1 is drawn at (${x11.toFixed(1)}, ${y11.toFixed(1)}), which is not the top-right (north-west) corner of the grid`);
  const [x23, y23] = pos(1, 2);
  assert(x23 < BOX / 2 && y23 > BOX / 2,
    `2-3 is drawn at (${x23.toFixed(1)}, ${y23.toFixed(1)}), which is not the bottom-left (south-east) corner`);
  const [x12] = pos(0, 1);
  const [x13] = pos(0, 2);
  assert(x11 > x12 && x12 > x13, `row 1 does not run right to left (west to east): ${x11}, ${x12}, ${x13}`);
});

// Mutant "panels drawn over FovOverlay" (the `!panelsMode &&` gate on
// FovOverlay removed) failed:
//   x panels replace FovOverlay's screen grid, and each panel is drawn in its
//     state's shape: FovOverlay's point-reflected grid is still drawn under
//     the server's panels - two claims about one grid
test("panels replace FovOverlay's screen grid, and each panel is drawn in its state's shape", () => {
  assert(fovGroup() === undefined,
    "FovOverlay's point-reflected grid is still drawn under the server's panels - two claims about one grid");
  const drawn = Array.from(container.querySelectorAll('[data-role="panel"]')) as any[];
  assert(drawn.length === 6, `${drawn.length} panel shapes, expected 6`);
  // The states reach the canvas, as shapes (panelLayer.test.tsx holds the
  // five-way distinction itself).
  const skipped = container.querySelector('[data-role="panel"][data-state="skipped"] [data-mark="cross"]');
  assert(skipped != null, "the skipped panel has no X");
  const aside = container.querySelector('[data-role="panel-label"][data-state="set_aside"]');
  assert(aside != null && /!/.test(aside.textContent ?? ""), "the set-aside panel's label has no '!'");
  const shooting = container.querySelector('[data-role="panel"][data-state="shooting"] [data-mark="ticks"]');
  assert(shooting != null, "the shooting panel has no corner ticks");
});

// The live optics here (530 mm, 3.76 um, 4144 x 2822) give a 1.68 x 1.15 deg
// frame; the grid was tiled for 2.00 x 1.33. The panels must be drawn at the
// tiled size, because that is what the run will shoot at those centres.
//
// Mutant "outline sized from live optics" (SkyCanvas hands placePanels the
// optics field instead of panelFov) failed:
//   x panels are outlined at the size they were tiled for, not the live
//     optics: panel 1-2 corner 0 x: got 394.71, expected 374.98263344657954
//     (+-0.5)
test("panels are outlined at the size they were tiled for, not the live optics", () => {
  const p = PANELS[1];
  const el = container.querySelector(
    `[data-role="panel"][data-row="${p.row}"][data-col="${p.col}"] [data-mark="outline"]`) as any;
  assert(el != null, "panel 1-2 has no outline");
  const nums = (el.getAttribute("d") as string).match(/-?[\d.]+/g)!.map(Number);
  const want = fovCornersSky({ ra_hours: p.ra_hours, dec_deg: p.dec_deg },
    PANEL_FOV.fov_x_deg, PANEL_FOV.fov_y_deg, p.rotation_deg ?? 0)
    .map((c) => skyToView(c.ra_hours, c.dec_deg, GEOM)!);
  for (let i = 0; i < 4; i++) {
    near(nums[2 * i], want[i].x, 0.5, `panel 1-2 corner ${i} x`);
    near(nums[2 * i + 1], want[i].y, 0.5, `panel 1-2 corner ${i} y`);
  }
});

// ============================================================ tap -> panel
// Mutant "tap goes to objects only" (pickAt never consults the panels) failed:
//   x a tap on a panel calls onPanelTap(row, col) with the server's indices:
//     a tap on panel 1-1 produced [] - expected one call with (0, 0)
test("a tap on a panel calls onPanelTap(row, col) with the server's indices", () => {
  for (const p of PANELS) {
    reset();
    const c = skyToView(p.ra_hours, p.dec_deg, GEOM)!;
    tapAt(toCss(c.x), toCss(c.y));
    assert(calls.taps.length === 1 && calls.taps[0][0] === p.row && calls.taps[0][1] === p.col,
      `a tap on panel ${p.row + 1}-${p.col + 1} produced ${JSON.stringify(calls.taps)} - expected one call with (${p.row}, ${p.col})`);
    assert(calls.picks.length === 0, "a panel tap also went to the object picker");
  }
});

test("a tap outside every panel is not a panel tap, and a drag across one is not a tap", () => {
  reset();
  tapAt(4, BOX - 4);
  assert(calls.taps.length === 0, `a tap in the empty corner hit ${JSON.stringify(calls.taps)}`);
  assert(calls.picks.length === 1 && calls.picks[0] === null, "empty sky did not answer the picker with null");
  reset();
  const c = skyToView(PANELS[0].ra_hours, PANELS[0].dec_deg, GEOM)!;
  fire("pointerdown", toCss(c.x), toCss(c.y));
  fire("pointermove", toCss(c.x) + 40, toCss(c.y) + 30);
  fire("pointerup", toCss(c.x) + 40, toCss(c.y) + 30, { buttons: 0 });
  assert(calls.taps.length === 0, `a drag across panel 1-1 toggled ${JSON.stringify(calls.taps)}`);
});

test("a panel label is a control a keyboard can reach, and it names the same panel", () => {
  reset();
  const el = labelAt(1, 2);
  assert(el.tagName.toLowerCase() === "button", `the label is a <${el.tagName.toLowerCase()}>`);
  act(() => { el.click(); });
  assert(calls.taps.length === 1 && calls.taps[0][0] === 1 && calls.taps[0][1] === 2,
    `activating label 2-3 produced ${JSON.stringify(calls.taps)}`);
  assert(el.parentElement.className.includes("pointer-events-none"),
    "the panel-label layer takes pointer events - a drag starting on a label would not reach the sky");
});

// ======================================== frameCenter: the grid over a still sky
// A marked object stands in for "the sky": it is drawn from `center`, so if it
// holds still while the grid moves, the grid moved over the sky.
const STAR: SkyRow = {
  id: "S1", label: "S1", kind: "star", type: "Star",
  ra_hours: CENTER.ra_hours, dec_deg: CENTER.dec_deg - 1.5, mag: 3, size_arcmin: 0,
  constellation: "Andromeda", describe: "star", alias: null,
} as SkyRow;
const FRAME_AT = { ra_hours: CENTER.ra_hours + 0.05, dec_deg: CENTER.dec_deg + 0.6 };

function starCy(): number {
  const grp = container.querySelector('[data-role="annotation-markers"]') as any;
  assert(grp != null, "the reference star is not drawn - the still-sky check has nothing to watch");
  return Number(grp.querySelector("circle").getAttribute("cy"));
}

// Mutant "frameCenter ignored" (the grid centre stays at the view centre
// whatever frameCenter says) failed three tests:
//   x frameCenter moves FovOverlay's grid and the rotate handle through
//     skyToView, and the sky holds still: FovOverlay grid x: got 500,
//     expected 429.88869688215004 (+-0.5)
//   x with panels, frameCenter moves the grid's centre mark and leaves the
//     panels on their sky: grid centre mark x: got 500, expected
//     429.88869688215004 (+-0.5)
//   x MOVE GRID: a drag carries the grid centre with the pointer and never
//     pans the sky: grid centre moved right by the drag: got
//     61.240069122425986, expected 36 (+-0.5)
// Re-run after the verifier's cases at the bottom were added, it fails three
// of those too (13/19): the rotation pivot, the "Your camera" label and the
// horizon case, each with the failure quoted there.
test("frameCenter moves FovOverlay's grid and the rotate handle through skyToView, and the sky holds still", () => {
  reset();
  render({ skyRows: [STAR] });
  const skyBefore = starCy();
  render({ skyRows: [STAR], frameCenter: FRAME_AT });
  const want = skyToView(FRAME_AT.ra_hours, FRAME_AT.dec_deg, GEOM)!;
  const [x, y] = translateOf(fovGroup());
  near(x, want.x, 0.5, "FovOverlay grid x");
  near(y, want.y, 0.5, "FovOverlay grid y");
  const [hx, hy] = translateOf(handleGroup());
  near(hx, want.x, 0.5, "rotate handle x");
  near(hy, want.y, 0.5, "rotate handle y");
  near(starCy(), skyBefore, 1e-9, "the sky (a star drawn from the view centre)");
  assert(calls.center === 0, "moving the grid moved the view centre");
});

test("with panels, frameCenter moves the grid's centre mark and leaves the panels on their sky", () => {
  render({ panels: PANELS, panelFov: PANEL_FOV, frameCenter: FRAME_AT });
  const cross = container.querySelector('[data-role="panel-frame-center"]') as any;
  assert(cross != null, "no centre mark for the grid");
  const want = skyToView(FRAME_AT.ra_hours, FRAME_AT.dec_deg, GEOM)!;
  near(Number(cross.getAttribute("data-x")), want.x, 0.5, "grid centre mark x");
  near(Number(cross.getAttribute("data-y")), want.y, 0.5, "grid centre mark y");
  // The panels are sky coordinates: a new frameCenter does not move them
  // until the parent hands in the new server answer.
  const p = PANELS[0];
  const at = skyToView(p.ra_hours, p.dec_deg, GEOM)!;
  near(parseFloat(labelAt(0, 0).style.left), toCss(at.x), 0.5, "label 1-1 left under a moved frameCenter");
});

// MOVE GRID: a drag moves the frame centre with the pointer and never the view.
// Mutant "grid drag pans the sky" (with frameCenter and onFrameCenterChange the
// drag still takes the pan path) failed:
//   x MOVE GRID: a drag carries the grid centre with the pointer and never pans
//     the sky: the drag moved the view centre 1x and the grid 0x
test("MOVE GRID: a drag carries the grid centre with the pointer and never pans the sky", () => {
  reset();
  render({
    frameCenter: FRAME_AT,
    onFrameCenterChange: (ra: number, dec: number) => { calls.frame.push([ra, dec]); },
  });
  const start = skyToView(FRAME_AT.ra_hours, FRAME_AT.dec_deg, GEOM)!;
  fire("pointerdown", 100, 100);
  fire("pointermove", 136, 118);
  fire("pointerup", 136, 118, { buttons: 0 });
  assert(calls.center === 0 && calls.frame.length > 0,
    `the drag moved the view centre ${calls.center}x and the grid ${calls.frame.length}x`);
  const [ra, dec] = calls.frame[calls.frame.length - 1];
  const moved = skyToView(ra, dec, GEOM)!;
  // The grid point under the pointer follows it: +36, +18 CSS px.
  near(toCss(moved.x - start.x), 36, 0.5, "grid centre moved right by the drag");
  near(toCss(moved.y - start.y), 18, 0.5, "grid centre moved down by the drag");
});

test("control: MOVE SKY (no onFrameCenterChange) still pans the view as before", () => {
  reset();
  render({ frameCenter: FRAME_AT });
  fire("pointerdown", 100, 100);
  fire("pointermove", 136, 118);
  fire("pointerup", 136, 118, { buttons: 0 });
  assert(calls.center > 0 && calls.frame.length === 0,
    `a MOVE SKY drag moved the view ${calls.center}x and the grid ${calls.frame.length}x`);
});

// ============================================ what else hangs off the grid
// The cases below were added by the S4-USKY verifier: each is a behaviour the
// canvas gained with `panels`/`frameCenter` that no case above could fail on
// (each of these mutants passed the file 14/14 before they existed). Their
// mutants ran in a private scratch copy of ui/ (scratchpad
// s4-usky-verify-mut), never in the shared tree (#254).

// A tap on a star inside a panel is a question about the star. Answering it
// with onPanelTap would toggle that panel's skip, and the run would leave a
// hole the operator never asked for.
//
// Mutant "panel beats object marker" (pickAt's `if (hit || !onPanelTap)`
// narrowed to `if (!onPanelTap)`, so a marker hit falls through to the
// panels) failed:
//   x an object marker inside a panel wins the tap, and the panel beside it
//     still answers: a tap on the star inside panel 1-1 went to onPanelTap
//     [[0,0]] and to the picker [] - expected the star, and no skip toggled
const STAR_ON_1_1: SkyRow = {
  ...STAR, id: "S2", label: "S2",
  ra_hours: PANELS[0].ra_hours, dec_deg: PANELS[0].dec_deg,
} as SkyRow;

test("an object marker inside a panel wins the tap, and the panel beside it still answers", () => {
  reset();
  render({ panels: PANELS, panelFov: PANEL_FOV, skyRows: [STAR_ON_1_1],
           onPanelTap: (r: number, c: number) => { calls.taps.push([r, c]); } });
  const c = skyToView(PANELS[0].ra_hours, PANELS[0].dec_deg, GEOM)!;
  tapAt(toCss(c.x), toCss(c.y));
  assert(calls.taps.length === 0 && calls.picks.length === 1 && calls.picks[0]?.id === "S2",
    `a tap on the star inside panel 1-1 went to onPanelTap ${JSON.stringify(calls.taps)} and to the ` +
    `picker ${JSON.stringify(calls.picks.map((r) => r?.id ?? null))} - expected the star, and no skip toggled`);
  // Control: the marker is what won, not a dead panel layer.
  reset();
  const c2 = skyToView(PANELS[1].ra_hours, PANELS[1].dec_deg, GEOM)!;
  tapAt(toCss(c2.x), toCss(c2.y));
  assert(calls.taps.length === 1 && calls.taps[0][0] === 0 && calls.taps[0][1] === 1 && calls.picks.length === 0,
    `a tap on panel 1-2 beside the star produced taps ${JSON.stringify(calls.taps)}, picks ${calls.picks.length}`);
});

// jsdom lays nothing out, so every rect is 0 x 0 and a rotate about "the box
// centre" and about "the grid centre" would both be about (0, 0). A real rect
// for the box is what lets the pivot be told apart at all.
function withBoxRect(fn: () => void): void {
  const el = box();
  el.getBoundingClientRect = () => ({
    left: 0, top: 0, width: BOX, height: BOX, right: BOX, bottom: BOX, x: 0, y: 0, toJSON() { return {}; },
  });
  try { fn(); } finally { delete el.getBoundingClientRect; }
}

/** A quarter turn on the rotate handle, drawn about `pivot` (CSS px): press
 *  40 px straight above it, move to 40 px straight right of it. */
function quarterTurn(over: Record<string, unknown>, pivot: { x: number; y: number }): number[] {
  const rots: number[] = [];
  render({ ...over, onRotate: (d: number) => { rots.push(d); } });
  const h = container.querySelector('[data-role="rotate-handle"]') as any;
  assert(h != null, "no rotate handle - the rotate gesture has nothing to press");
  withBoxRect(() => {
    act(() => { h.dispatchEvent(ptr("pointerdown", pivot.x, pivot.y - 40)); });
    fire("pointermove", pivot.x + 40, pivot.y);
    fire("pointerup", pivot.x + 40, pivot.y, { buttons: 0 });
  });
  return rots;
}

// Mutant "rotation pivot at box centre" (pivotCss always answers the rect's
// centre, as before frameCenter) failed:
//   x with frameCenter, the rotate handle turns the grid about the GRID's
//     centre: a quarter turn about the moved grid's centre: got
//     49.17639817826166, expected 90 (+-0.000001)
test("with frameCenter, the rotate handle turns the grid about the GRID's centre", () => {
  // Control: no frameCenter, the old pivot (the box centre), the old answer.
  const still = quarterTurn({}, { x: BOX / 2, y: BOX / 2 });
  assert(still.length > 0, "a drag on the rotate handle rotated nothing - the gesture never ran");
  near(still[still.length - 1], 90, 1e-6, "control: a quarter turn about the view centre");
  const at = skyToView(FRAME_AT.ra_hours, FRAME_AT.dec_deg, GEOM)!;
  const moved = quarterTurn({ frameCenter: FRAME_AT }, { x: toCss(at.x), y: toCss(at.y) });
  assert(moved.length > 0, "a drag on the moved grid's rotate handle rotated nothing");
  near(moved[moved.length - 1], 90, 1e-6, "a quarter turn about the moved grid's centre");
});

// "Your camera" is pinned to the top of the frame footprint and "Object size"
// to the right of the object ellipse, and both are drawn at the grid centre.
// When the grid moves they must move with it; when it leaves the canvas they
// must leave too, because the label layer does not clip and a label past the
// edge widens the page (the "Object size" legend once did, at 390 px).
const FRAMED = {
  id: "T1", name: "T1", label: "T1", kind: "galaxy", type: "Galaxy",
  ra_hours: CENTER.ra_hours, dec_deg: CENTER.dec_deg, mag: 4, size_arcmin: 60,
} as any;
const FAR_NORTH = { ra_hours: CENTER.ra_hours, dec_deg: CENTER.dec_deg + 6 };  // 750 viewBox units up
const camLabel = () =>
  (Array.from(container.querySelectorAll("span")) as any[])
    .find((el) => el.style.top !== "" && /^Your camera/.test(el.textContent ?? ""));
const sizeLegend = () =>
  (Array.from(container.querySelectorAll("span")) as any[])
    .find((el) => (el.textContent ?? "").trim() === "Object size");

// Mutant "grid labels never hide" (`gridLabelsOnCanvas = true`) failed this
// and the horizon case below:
//   x the 'Your camera' label and the object-size legend go with the grid,
//     and leave the canvas with it: 'Your camera' is still drawn for a grid
//     moved 6 deg off the top of the canvas
// Mutant "camera label ignores frameCenter" (ccx, ccy back to the box centre)
// failed:
//   x the 'Your camera' label and the object-size legend go with the grid,
//     and leave the canvas with it: 'Your camera' moved across with the grid:
//     got 0, expected -25.24006912242598 (+-0.5)
test("the 'Your camera' label and the object-size legend go with the grid, and leave the canvas with it", () => {
  render({ catalogTarget: FRAMED });
  const cam0 = camLabel();
  const size0 = sizeLegend();
  assert(cam0 != null && size0 != null,
    "precondition: 'Your camera' or 'Object size' is not drawn at all, so nothing below can move it");
  const [cl, ct] = [parseFloat(cam0.style.left), parseFloat(cam0.style.top)];
  const [sl, st] = [parseFloat(size0.style.left), parseFloat(size0.style.top)];
  render({ catalogTarget: FRAMED, frameCenter: FRAME_AT });
  const at = skyToView(FRAME_AT.ra_hours, FRAME_AT.dec_deg, GEOM)!;
  const dx = toCss(at.x) - BOX / 2;
  const dy = toCss(at.y) - BOX / 2;
  near(parseFloat(camLabel().style.left) - cl, dx, 0.5, "'Your camera' moved across with the grid");
  near(parseFloat(camLabel().style.top) - ct, dy, 0.5, "'Your camera' moved up with the grid");
  near(parseFloat(sizeLegend().style.left) - sl, dx, 0.5, "'Object size' moved across with the grid");
  near(parseFloat(sizeLegend().style.top) - st, dy, 0.5, "'Object size' moved up with the grid");
  render({ catalogTarget: FRAMED, frameCenter: FAR_NORTH });
  assert(camLabel() === undefined,
    "'Your camera' is still drawn for a grid moved 6 deg off the top of the canvas");
  assert(sizeLegend() === undefined,
    "'Object size' is still drawn for a grid moved 6 deg off the top of the canvas");
});

// A frame centre on the far side of the sky has no gnomonic image: skyToView
// answers null, and a grid "somewhere" would be a mirrored claim.
const ANTIPODE = { ra_hours: (CENTER.ra_hours + 12) % 24, dec_deg: -CENTER.dec_deg };

// Mutant "horizon grid drawn at the view centre" (frameView falls back to
// (cx, cy) when skyToView answers null) failed:
//   x a frame centre past the projection horizon draws no grid, no handle, no
//     camera label; a drag pans: FovOverlay is drawn at translate(500 500)
//     rotate(0) for a frame centre on the far side of the sky
// Mutant "horizon drag takes the grid path" (the `&& frameView` dropped from
// onPointerDown's mode choice, so the drag falls through to the rotate
// branch) failed:
//   x a frame centre past the projection horizon draws no grid, no handle, no
//     camera label; a drag pans: a drag with the grid past the horizon moved
//     the view 0x and the grid 0x
test("a frame centre past the projection horizon draws no grid, no handle, no camera label; a drag pans", () => {
  reset();
  render({
    frameCenter: ANTIPODE,
    onFrameCenterChange: (ra: number, dec: number) => { calls.frame.push([ra, dec]); },
  });
  const fg = fovGroup();
  assert(fg === undefined,
    `FovOverlay is drawn at ${fg ? fg.getAttribute("transform") : ""} for a frame centre on the far side of the sky`);
  assert(container.querySelector('[data-role="rotate-handle"]') === null,
    "a rotate handle is drawn for a grid that has no place on this map");
  assert(camLabel() === undefined, "'Your camera' is drawn for a grid that has no place on this map");
  // MOVE GRID has no grid point under the pointer to carry, so the drag pans
  // the sky toward it (and never falls through to the rotate branch).
  fire("pointerdown", 100, 100);
  fire("pointermove", 136, 118);
  fire("pointerup", 136, 118, { buttons: 0 });
  assert(calls.center > 0 && calls.frame.length === 0,
    `a drag with the grid past the horizon moved the view ${calls.center}x and the grid ${calls.frame.length}x`);
});

// The rotate stalk stands on the top edge of the grid that is DRAWN. With
// panels that is the grid as tiled (panelFov), not the live optics (1.15 deg
// tall here, against 1.33 tiled). Graded against the SERVER's total height for
// this grid, not the client helper the handle calls.
//
// Mutant "handle sized from live optics" (`gridFovY = fov.fov_y_deg`) failed:
//   x with panels, the rotate handle stands on the top edge of the grid as
//     tiled: the stalk's base above the grid centre (viewBox units): got
//     125.46138893474841, expected 145.46875 (+-0.5)
test("with panels, the rotate handle stands on the top edge of the grid as tiled", () => {
  render({ panels: PANELS, panelFov: PANEL_FOV });
  const stalk = container.querySelector('[data-role="rotate-handle"] line') as any;
  assert(stalk != null, "no rotate stalk drawn with panels");
  near(-Number(stalk.getAttribute("y1")), (FX.response.total_fov_y_deg * PPD) / 2, 0.5,
    "the stalk's base above the grid centre (viewBox units)");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });

const total = passed + failed;
console.log(`skyCanvasPanels.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
