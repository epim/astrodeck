// skyCanvasMarkers.test.tsx — the Atlas canvas MOUNTED, with objects in the
// sky, asserted as DOM at a computed coordinate and then tapped.
//
//   Run directly:  npx tsx src/components/atlas/__tests__/skyCanvasMarkers.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY THIS IS A MOUNTED TEST AND NOT ANOTHER PURE ONE. lib/__tests__/
// skyMarkers.test.ts already proves the placement arithmetic. What it cannot
// prove is that any of it reaches the screen — and that is precisely how task
// #111 was closed on work that rendered nothing: stars, planets and the Moon
// were merged into the catalog search, the search box listed them, and no
// pixel of the canvas ever changed. views/__tests__/atlasHonesty.test.tsx says
// in its own header that it asserts nothing about the picture. This file is
// the one that does.
//
// So the assertions here are: a marker ELEMENT exists, at a coordinate this
// file computes from the gnomonic identity rather than from the projection
// under test; its name is on screen as real text; and a press-and-release on
// its pixel calls back with that object while a drag across it does not.

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
// No 2D context, which is also what the component's label measurement finds
// here: it falls back to a monospace advance width. That fallback only ever
// runs where nothing is rendered to a human, and the label POSITIONS are not
// what this file asserts — the marker positions are.
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
  assert(Math.abs(a - b) <= tol, `${what}: got ${a}, expected ${b} (±${tol})`);
}

const RAD = 180 / Math.PI;
const VIEW = 1000;
// SkyCanvas measures its own box with a ResizeObserver, which jsdom never
// fires, so it stays at its initial 360 CSS px. That is what makes every
// coordinate below deterministic.
const BOX = 360;
const FOV = 2;
const CENTER = { ra_hours: 5.0, dec_deg: 10.0 };

const OPTICS = {
  focal_length_mm: 530,
  pixel_size_um: 3.76,
  sensor_width_px: 4144,
  sensor_height_px: 2822,
};

function mkRow(over: Partial<SkyRow> & { id: string }): SkyRow {
  return {
    label: over.id,
    kind: "dso",
    type: "Galaxy",
    ra_hours: CENTER.ra_hours,
    dec_deg: CENTER.dec_deg,
    mag: 9,
    size_arcmin: 0,
    constellation: "Orion",
    describe: "galaxy in Orion · mag 9.0",
    alias: null,
    ...over,
  } as SkyRow;
}

/** Where an object `dDeg` north of the view centre must appear, in viewBox
 *  units. Derived here from the gnomonic identity eta = tan(d) on the tangent
 *  point's own meridian — NOT by calling the projection under test. */
function expectedY(dDeg: number): number {
  return VIEW / 2 - Math.tan(dDeg / RAD) * RAD * (VIEW / FOV);
}
const toCss = (v: number) => (v * BOX) / VIEW;

// ---------------------------------------------------------------- the render
const picks: (SkyRow | null)[] = [];
let rows: SkyRow[] = [];

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

function render(): void {
  act(() => {
    root.render(createElement(SkyCanvas, {
      center: CENTER,
      rotationDeg: 0,
      survey: "schematic",
      mode: "schematic",
      stretch: "linear",
      fovZoomDeg: FOV,
      optics: OPTICS,
      mosaic: { rows: 1, cols: 1, overlap: 0.25 },
      night: false,
      skyRows: rows,
      selectedObjectId: null,
      onPickObject: (r: SkyRow | null) => { picks.push(r); },
      onCenterChange: () => {},
      onRotate: () => {},
      onZoom: () => {},
    } as any));
  });
}

const box = () => container.querySelector('[role="application"]') as any;
const markerGroup = () =>
  container.querySelector('[data-role="annotation-markers"]') as any;
const labels = () =>
  Array.from(container.querySelectorAll('[data-role="object-label"]')) as any[];

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

// ================================================== an empty sky draws nothing
render();
test("with no rows the canvas mounts and draws no marker layer at all", () => {
  assert(box() != null, "no canvas in the document — the fixture is wrong");
  assert(markerGroup() === null,
    "an empty region produced marker chrome. An empty patch of sky is what an " +
    "empty patch of sky looks like: nothing, not a placeholder");
  assert(labels().length === 0, "labels with no objects");
});

// ================================================== a marker is actually drawn
rows = [
  mkRow({ id: "M31", label: "Andromeda Galaxy", size_arcmin: 30 }),
  mkRow({ id: "Vega", label: "Vega", kind: "star", type: "Star", mag: 0.03,
          dec_deg: CENTER.dec_deg + 0.5, describe: "α Lyr · Lyra" }),
];
render();

test("an object at the view centre is drawn, as an element, at the centre", () => {
  const grp = markerGroup();
  assert(grp != null,
    "NOTHING WAS DRAWN. Two catalogued objects are in view and the canvas has " +
    "no marker layer — this is the exact state task #111 shipped in");
  const el = grp.querySelector("ellipse");           // galaxy -> ellipse
  assert(el != null, "the galaxy has no ellipse in the DOM");
  near(Number(el.getAttribute("cx")), VIEW / 2, 1e-6, "galaxy cx");
  near(Number(el.getAttribute("cy")), VIEW / 2, 1e-6, "galaxy cy");
  near(Number(el.getAttribute("rx")), (30 / 60 / 2) * (VIEW / FOV), 1e-6,
       "galaxy semi-major (its real angular size)");
});

test("a star half a degree north is drawn half a degree up, and as a DIFFERENT shape", () => {
  const grp = markerGroup();
  const dot = grp.querySelector("circle");
  assert(dot != null,
    "the named star has no marker — stars, planets and the Moon reaching the " +
    "search box and not the map is the whole of #111's failure");
  near(Number(dot.getAttribute("cy")), expectedY(0.5), 1e-6, "star cy");
  near(Number(dot.getAttribute("cx")), VIEW / 2, 1e-6, "star cx");
  assert(grp.querySelector("ellipse") !== null && dot.tagName.toLowerCase() === "circle",
    "the galaxy and the star are the same shape — this app is used in the " +
    "dark under a red filter, so type cannot be carried by colour");
});

test("both objects have their names on screen as real, readable text", () => {
  const texts = labels().map((b) => b.textContent);
  assert(texts.includes("Andromeda Galaxy"),
    `the galaxy's name is not on the canvas: ${JSON.stringify(texts)}`);
  assert(texts.includes("Vega"), `the star's name is not on the canvas: ${JSON.stringify(texts)}`);
  // Real px, not viewBox units: text inside the scaled viewBox becomes 4px on
  // a phone, which is the rule this canvas's header states.
  const el = labels()[0];
  assert(el.className.includes("text-[12px]"),
    "an object label is not at the canvas's 12px floor");
  assert(el.getAttribute("aria-label")?.includes("·") ||
         el.getAttribute("aria-label")?.includes("galaxy"),
    "the label's accessible name carries only the designation — a screen " +
    "reader gets no more than the sighted user's two words");
});

// ============================================================== tap -> object
test("a tap on the marker's own pixel opens that object", () => {
  picks.length = 0;
  tapAt(toCss(VIEW / 2), toCss(VIEW / 2));
  assert(picks.length === 1, `a tap produced ${picks.length} picks, expected 1`);
  assert(picks[0]?.id === "M31",
    `a tap on the galaxy's own pixel resolved to ${picks[0]?.id ?? "nothing"}`);
});

test("a tap on the star resolves to the star, not to the nearer-listed galaxy", () => {
  picks.length = 0;
  tapAt(toCss(VIEW / 2), toCss(expectedY(0.5)));
  assert(picks[0]?.id === "Vega",
    `tapping the star resolved to ${picks[0]?.id ?? "nothing"} — the hit test `
    + `is finding the first row in range rather than the nearest marker`);
});

test("a tap on empty sky answers 'nothing', so the card returns to its own words", () => {
  picks.length = 0;
  // Bottom-left corner: no object anywhere near it.
  tapAt(10, BOX - 10);
  assert(picks.length === 1 && picks[0] === null,
    `a tap on empty sky produced ${JSON.stringify(picks.map((p) => p?.id ?? null))}. ` +
    "It must be an explicit 'nothing here' — a tap that silently does nothing " +
    "reads as a broken control");
});

test("dragging the sky across a marker selects nothing", () => {
  picks.length = 0;
  fire("pointerdown", toCss(VIEW / 2), toCss(VIEW / 2));
  fire("pointermove", toCss(VIEW / 2) + 40, toCss(VIEW / 2) + 25);
  fire("pointerup", toCss(VIEW / 2) + 40, toCss(VIEW / 2) + 25, { buttons: 0 });
  assert(picks.length === 0,
    "a pan that started on an object opened its card. Every drag on this " +
    "canvas begins somewhere, and half the sky has something in it");
});

test("the browser taking the gesture away is not a tap", () => {
  picks.length = 0;
  fire("pointerdown", toCss(VIEW / 2), toCss(VIEW / 2));
  fire("pointercancel", toCss(VIEW / 2), toCss(VIEW / 2), { buttons: 0 });
  assert(picks.length === 0,
    "a cancelled gesture was spent as a tap — on a phone that is every " +
    "scroll that happens to start on the map");
});

test("a finger can tap an object without first arming finger-drag mode", () => {
  // On touch the canvas defaults to `touch-action: pan-y` and starts NO drag,
  // so the page can always be scrolled. A tap must still work in that mode:
  // otherwise objects are untappable on a phone until the user finds a toggle.
  picks.length = 0;
  const touch = { pointerType: "touch" };
  fire("pointerdown", toCss(VIEW / 2), toCss(VIEW / 2), touch);
  fire("pointerup", toCss(VIEW / 2), toCss(VIEW / 2), { ...touch, buttons: 0 });
  assert(picks[0]?.id === "M31",
    `a finger tap in page-scroll mode resolved to ${picks[0]?.id ?? "nothing"} — ` +
    "the tap candidate has to be recorded BEFORE the touch gate that skips " +
    "the drag");
});

test("a name on the canvas is a real control a keyboard can reach", () => {
  picks.length = 0;
  const vega = labels().find((b) => b.textContent === "Vega");
  assert(vega != null, "no label to activate");
  assert(vega.tagName.toLowerCase() === "button",
    `the label is a <${vega.tagName.toLowerCase()}> — a name with no control ` +
    "behind it is unreachable without a pointer");
  act(() => { vega.click(); });
  assert(picks[0]?.id === "Vega",
    "activating the label opened nothing");
});

test("the label layer never eats a gesture aimed at the sky behind it", () => {
  // The labels sit over a pannable canvas. If they took pointer events, the
  // first 44px of any drag that started on a name would be swallowed.
  const layer = labels()[0].parentElement;
  assert(layer.className.includes("pointer-events-none"),
    "the object-label layer takes pointer events — a drag beginning on a name " +
    "would not pan the sky");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });

const total = passed + failed;
console.log(`skyCanvasMarkers.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
