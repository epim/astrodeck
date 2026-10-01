// w3SkyCanvasOverlayBoxes.test.tsx - WP-24b (b): the reserved overlay box uses
// the toggle's MEASURED footprint, and the "Object size" legend's own box is
// reserved from object labels in turn (#491, third instance; fix shape per
// docs/superpowers/plans/2026-09-30-open-issue-backlog.md WP-24b).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/atlas/__tests__/w3SkyCanvasOverlayBoxes.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT WAS WRONG (two instances of the same class: a reservation narrower than
// what it is meant to keep clear).
//   (1) `reservedBoxes` guessed a fixed 56 px column for `overlayControls`.
//       Measured on the real 390x844 page the modal's MOVE SKY / MOVE GRID
//       pair was 87.3 px wide, so the left ~31 px of both buttons sat OUTSIDE
//       the reservation and an object label could be placed partly under them.
//   (2) The "Object size" legend searches `reservedBoxes` to find its own
//       clear spot, but was never ADDED to them, so an object label's own
//       search had no reason to avoid the legend's chosen box.
//
// HOW THIS GRADES IT. Both cases place a marker whose DEFAULT anchor box (the
// box skyMarkers.ts's own `anchorRect` formula gives "e" or "n"/"s", in that
// tried order) lands exactly inside the box that is supposed to be reserved,
// so the first case proves nothing would have moved the label without the
// fix, and the fix's job is to move it. Case (1) stubs `getBoundingClientRect`
// on the overlay's own root node to the issue's measured 88x60 footprint
// (jsdom lays nothing out, so every unstubbed rect is 0x0x0x0 - see
// skyCanvasPanels.test.tsx's `withCamHeights` for the same technique against
// the "Your camera" label). Case (2) needs no stub: the legend's own position
// is plain arithmetic once `catalogTarget` and `fovZoomDeg` are fixed.
//
// Every coordinate below (the marker's screen position, and where a 2.5-deg
// panel's label system reports it) was confirmed empirically against this
// build before being written down (scratchpad probe, not published) rather
// than hand-derived and trusted; SEE the arithmetic in each test's comment
// for how a reader can check it independently.

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

// SkyCanvas measures overlayControls' root through this (mirrors camLabelH's
// own ref+ResizeObserver measurement). jsdom's own implementation is an
// all-zero stub, so only the one node the test cares about is overridden; the
// canvas box itself (and everything else) keeps the native all-zero rect,
// which is exactly what makes `t.left - b.left` below equal `t.left`.
const realGetBCR = win.HTMLElement.prototype.getBoundingClientRect;
win.HTMLElement.prototype.getBoundingClientRect = function (this: any) {
  if (this.getAttribute && this.getAttribute("data-testid") === "overlay-toggle") {
    // The issue's own measurement at 390x844: MOVE SKY / MOVE GRID together
    // 87.3 px wide, right-anchored 8 px from the canvas edge; rounded up here
    // to a plain 88 px so the arithmetic in the test stays in whole numbers.
    return { left: 264, top: 8, width: 88, height: 60, right: 352, bottom: 68, x: 264, y: 8, toJSON() { return {}; } };
  }
  return realGetBCR.call(this);
};

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
const { deproject } = await import("../../../lib/framing");
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

// SkyCanvas measures its own box with a ResizeObserver, which jsdom never
// fires, so it stays at its initial 360 CSS px: every coordinate below is
// deterministic (shared with skyCanvasLegend.test.tsx's own fixtures).
const BOX = 360;
const VIEW = 1000;
const ZOOM_DEG = 8;
const CENTER = { ra_hours: 5.0, dec_deg: 10.0 };
const PX_PER_DEG = VIEW / ZOOM_DEG;
const SCALE = BOX / VIEW; // viewBox units -> CSS px

/** Where a row must sit so its marker lands at CSS px (`cxCss`, `cyCss`),
 *  through the SAME gnomonic SkyCanvas itself draws with (lib/framing.ts) -
 *  not a second, "quick" approximation (the house rule this codebase keeps
 *  re-learning, see skyCanvasPanels.test.tsx and skyCanvasLegend.test.tsx's
 *  own use of `deproject` for exactly this). */
function rowAt(cxCss: number, cyCss: number): { ra_hours: number; dec_deg: number } {
  const half = VIEW / 2;
  const mx = cxCss / SCALE;
  const my = cyCss / SCALE;
  const xi = (half - mx) / PX_PER_DEG;
  const eta = (half - my) / PX_PER_DEG;
  return deproject(xi, eta, CENTER.ra_hours, CENTER.dec_deg);
}

/** `label` is kept to ONE character on purpose: the box-width arithmetic in
 *  each test's comment (w = 1*7.2 + 8 = 15.2, jsdom's fallback glyph advance)
 *  is only exact for a 1-char label, and `id` (the DOM selector below) need
 *  not match it. */
function mkRow(id: string, label: string, pos: { ra_hours: number; dec_deg: number }): SkyRow {
  return {
    id, label, kind: "dso", type: "Galaxy", ra_hours: pos.ra_hours, dec_deg: pos.dec_deg,
    mag: 9, size_arcmin: 0, constellation: "Orion", describe: "galaxy", alias: null,
  } as any;
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
function render(over: Record<string, unknown>): void {
  act(() => {
    root.render(createElement(SkyCanvas, {
      center: CENTER,
      rotationDeg: 0,
      survey: "schematic",
      mode: "schematic",
      stretch: "linear",
      fovZoomDeg: ZOOM_DEG,
      optics: null, // no "Your camera" label/reservation to account for
      mosaic: { rows: 1, cols: 1, overlap: 0.25 },
      night: false,
      onCenterChange: () => {},
      onRotate: () => {},
      onZoom: () => {},
      onPickObject: () => {},
      ...over,
    } as any));
  });
}

const label = (id: string) =>
  container.querySelector(`[data-object-id="${id}"]`) as any;
const legendEl = () =>
  (Array.from(container.querySelectorAll("span")) as any[])
    .find((el) => (el.textContent ?? "").trim() === "Object size");

// =================================================== (1) the measured footprint
// A point marker (MIN_R=5 floor, so gap = 5*0.36 + 5 = 6.8 CSS px) placed so
// its default "e" (east) label box is { x: 270, y: 12, w: 15.2, h: 16 } (a
// 1-char label "Q", measured here as jsdom's own 7.2 px/glyph fallback: w =
// 1*7.2 + 8 = 15.2). 270 sits OUTSIDE the old fixed 56 px column
// (boxPx-64=296 to boxPx-8=352) but INSIDE the issue's measured 88 px one
// (264 to 352) - exactly the ~31 px gap #491's third instance names.
//
// Mutant "overlay reservation stays a fixed 56 px column" (reservedBoxes
// pushes `{ x: boxPx - 64, y: 8, w: 56, h: 198 }` again instead of
// `overlayBox`): failed, 1/6:
//   x a label in the toggle's measured gap moves clear of it, not just the
//     old 56 px guess: the label is still at the old east spot (270, 12) -
//     the wider, MEASURED overlay footprint did nothing
test("a label in the toggle's measured gap moves clear of it, not just the old 56 px guess", () => {
  const row = mkRow("Q1", "Q", rowAt(263.2, 20));
  // Control: without overlayControls at all, nothing reserves that column,
  // and the label lands at its plain default spot - proving 270,12 really is
  // where this marker's label goes when nothing is in the way.
  render({ skyRows: [row] });
  const bare = label("Q1");
  assert(bare != null, "precondition: the label is not drawn at all without overlayControls");
  near(parseFloat(bare.style.left), 270, 0.5, "precondition: the bare east-anchor spot");
  near(parseFloat(bare.style.top), 12, 0.5, "precondition: the bare east-anchor spot");

  render({
    skyRows: [row],
    overlayControls: createElement("div", { "data-testid": "overlay-toggle" }),
  });
  const withToggle = label("Q1");
  assert(withToggle != null, "the label was dropped entirely once the toggle appeared");
  const left = parseFloat(withToggle.style.left);
  const top = parseFloat(withToggle.style.top);
  assert(!(left >= 264 && left < 352 && top < 68),
    `the label is still at the old east spot (${left}, ${top}) - the wider, MEASURED overlay footprint did nothing`);
  // It has somewhere to go (the "w" anchor skyMarkers.ts tries next): this is
  // a moved label, not a silently dropped one.
  near(left, 241.2, 0.5, "the label did not land at the expected west-anchor fallback");
  near(top, 12, 0.5, "the label did not land at the expected west-anchor fallback");
});

// ============================================================ (2) the legend
// catalogTarget 120' wide at the view centre (8 deg wide view, so
// cssPerDeg = 360/8 = 45, semiMajorDeg = 1, r = 45 CSS px): the legend's own
// (non-panel) formula puts it at left = ccx + r + 6 = 180+45+6 = 231,
// top = ccy = 180, anchor "left" - box { x:231, y:171, w:87.2, h:18 } (w is
// "Object size", 11 chars at the 7.2 px/glyph fallback, plus the 8 px plate).
//
// The second object sits at CSS (274.6, 195), picked so its "e", "w" and "n"
// anchor boxes (skyMarkers.ts's own `anchorRect`, tried in that order) all
// land inside the legend's box and only "s" clears it - proving the move is
// the legend's doing, not a coincidence of the first anchor tried.
//
// Mutant "object labels do not reserve the legend" (`placeSky`'s `reserved`
// stays `reservedBoxes`, dropping `reservedForObjects`): failed, 1/6:
//   x an object label keeps off the "Object size" legend, not just the
//     canvas's other furniture: the label (267, 201.8) still overlaps the
//     legend (231, 171, 87.2 x 18) - expected label.left=267,
//     label.top=201.8 (south anchor); the overlap check alone can still pass
//     a label the mutant left at the old east spot in a smaller grid, so this
//     case pins the exact landing spot too
test("an object label keeps off the \"Object size\" legend, not just the canvas's other furniture", () => {
  const M = {
    id: "M1", name: "M1", label: "M1", kind: "galaxy", type: "Galaxy",
    ra_hours: CENTER.ra_hours, dec_deg: CENTER.dec_deg, mag: 3, size_arcmin: 120,
  } as any;
  const row = mkRow("P1", "P", rowAt(274.6, 195));
  render({ catalogTarget: M, skyRows: [row] });

  const lg = legendEl();
  assert(lg != null, "precondition: the legend is not drawn at all");
  near(parseFloat(lg.style.left), 231, 0.5, "precondition: the legend's own spot");
  near(parseFloat(lg.style.top), 180, 0.5, "precondition: the legend's own spot");

  const el = label("P1");
  assert(el != null, "the label was dropped entirely rather than moved");
  const left = parseFloat(el.style.left);
  const top = parseFloat(el.style.top);
  const meetsLegend = left < 231 + 87.2 && 231 < left + 15.2 && top < 171 + 18 && 171 < top + 16;
  assert(!meetsLegend,
    `the label (${left}, ${top}) still overlaps the legend (231, 171, 87.2 x 18)`);
  // Pin the exact spot (the "s" anchor), so a mutant that merely breaks the
  // overlap check in a way that happens to clear THIS box cannot pass too.
  near(left, 267, 0.5, "the label did not land at the expected south-anchor fallback");
  near(top, 201.8, 0.5, "the label did not land at the expected south-anchor fallback");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
dom.window.close();
const total = passed + failed;
console.log(`w3SkyCanvasOverlayBoxes.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
