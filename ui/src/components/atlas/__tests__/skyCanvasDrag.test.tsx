// skyCanvasDrag.test.tsx — the Atlas sky canvas MOUNTED, dragged, and then
// interrupted the ways a real gesture gets interrupted.
//
//   Run directly:  npx tsx src/components/atlas/__tests__/skyCanvasDrag.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS IS ABOUT (2026-08-05 press-and-release sweep, class C).
// The canvas is a HELD control: press, drag, release. It kept its drag mode in
// a ref that only `pointerup` / `pointercancel` cleared, so:
//
//   1. Any exit that delivers neither — alt-tab, an OS app switch, a screen
//      timeout, a right-click whose release the context menu eats, a capture
//      that was revoked — left the mode set. Back on the page, moving the mouse
//      across the canvas WITH NO BUTTON HELD went on sliding the sky, and on
//      the rotate stalk went on spinning the camera angle. Rotation is the
//      expensive half: `rotation_deg` is what AtlasView posts to the rotator on
//      "Go to this target", so a drag nobody is holding silently rewrites the
//      PA the next slew commands.
//   2. The cursor is RENDERED from that mode, but clearing a ref schedules no
//      render — so the released canvas kept the closed-fist `grabbing` cursor
//      until some unrelated render happened along, and a pan never showed a
//      held cursor at all (the ternary only tested "rotate").
//
// WHY THE HARNESS HOLDS STATE. In the app, `onCenterChange` / `onRotate` write
// the framing session, so the parent re-renders DURING a drag — which is the
// only reason the stale cursor was ever visible. A harness with frozen props
// would never render mid-drag and would report "grab" throughout, passing over
// the broken build. Center and rotation are held in state here for that reason.

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
// jsdom implements neither, and the component calls both inside try/catch — but
// an unstubbed throw would still hide a genuine failure behind a swallowed one.
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
// SkyCanvas measures the box and the "Your camera" label with these; a throw
// inside those effects would fail the commit and take the canvas down with it.
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

const OPTICS = {
  focal_length_mm: 530,
  pixel_size_um: 3.76,
  sensor_width_px: 4144,
  sensor_height_px: 2822,
};

let pans = 0;
let rotates = 0;
let lastRotation = 0;
let zooms = 0;

function Harness(): any {
  const [center, setCenter] = useState({ ra_hours: 0.712, dec_deg: 41.27 });
  const [rotationDeg, setRotationDeg] = useState(0);
  return createElement(SkyCanvas, {
    overlayControls: createElement('div', { 'data-atlas-controls': true }, createElement('button', null, 'Layers')),
    center,
    rotationDeg,
    // "schematic" keeps the survey loader and the WebGL tile engine out of the
    // fixture entirely; neither has anything to do with the gesture.
    survey: "schematic",
    mode: "schematic",
    stretch: "linear",
    fovZoomDeg: 2,
    optics: OPTICS,
    mosaic: { rows: 1, cols: 1, overlap: 0.25 },
    night: false,
    onCenterChange: (ra_hours: number, dec_deg: number) => {
      pans++;
      setCenter({ ra_hours, dec_deg });
    },
    onRotate: (deg: number) => {
      rotates++;
      lastRotation = deg;
      setRotationDeg(deg);
    },
    onZoom: () => { zooms++; },
  } as any);
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(Harness)); });

const box = () => container.querySelector('[role="application"]') as any;
const handle = () => container.querySelector('[data-role="rotate-handle"]') as any;
const cursor = () => box().style.cursor;

/** A pointer event jsdom does not implement. Built as a plain Event with the
 *  fields React copies onto its synthetic one — the same trick holdButtonDom
 *  uses, extended with the coordinates and `buttons` this component reads. */
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
function down(x: number, y: number, opts: any = {}): void {
  act(() => { (opts.on ?? box()).dispatchEvent(ptr("pointerdown", x, y, opts)); });
}
function move(x: number, y: number, opts: any = {}): void {
  act(() => { box().dispatchEvent(ptr("pointermove", x, y, opts)); });
}
function up(x: number, y: number): void {
  act(() => { box().dispatchEvent(ptr("pointerup", x, y, { buttons: 0 })); });
}

// --------------------------------------------------------------- the fixture
test("the canvas mounted, a press-drag pans the sky, and the cursor says held", () => {
  assert(box() != null, "no canvas in the document — the fixture is wrong, not the component");
  assert(handle() != null,
    "no rotate handle — the optics fixture is wrong, so every rotate assertion " +
    "below would be dispatching at nothing");
  assert(cursor() === "grab", `resting cursor is "${cursor()}", expected grab`);

  pans = 0;
  down(100, 100);
  move(140, 130);
  assert(pans > 0,
    "a press and a drag panned nothing — the gesture never worked in this " +
    "fixture, so every 'it stopped panning' assertion below would pass vacuously");
  assert(cursor() === "grabbing",
    `the canvas shows "${cursor()}" while a pan is being held — the held cursor ` +
    "is the only sign the surface has taken your finger");
  up(140, 130);
  assert(cursor() === "grab",
    `the canvas still shows "${cursor()}" after the release — the cursor is read ` +
    "from a ref that clearing schedules no render for");
});

// -------------------------------------------- the exits that deliver no event
test("alt-tabbing away mid-pan ends the drag instead of latching it", () => {
  pans = 0;
  down(100, 100);
  move(140, 130);
  // PRECONDITION: the drag is genuinely live. Without this, "no further pans"
  // is what a press that never started also reports.
  const held = pans;
  assert(held > 0, "the pan never started, so the release below proves nothing");

  act(() => { win.dispatchEvent(new win.Event("blur")); });
  assert(cursor() === "grab",
    `the canvas still advertises "${cursor()}" after the window went away`);

  // The load-bearing half. Deliberately driven with buttons=1 — the user came
  // back to the tab still holding the mouse down — so this proves the blur exit
  // alone, with no help from the no-button self-heal tested further down.
  move(300, 300, { buttons: 1 });
  move(320, 340, { buttons: 1 });
  assert(pans === held,
    `${pans - held} more pans landed after the window lost focus — the sky is ` +
    "sliding on a gesture the browser stopped reporting");
});

test("the tab going hidden mid-pan ends the drag too", () => {
  pans = 0;
  down(100, 100);
  move(140, 130);
  const held = pans;
  assert(held > 0, "precondition: the pan never started");

  act(() => {
    Object.defineProperty(win.document, "visibilityState", {
      value: "hidden", configurable: true,
    });
    win.document.dispatchEvent(new win.Event("visibilitychange", { bubbles: true }));
  });
  Object.defineProperty(win.document, "visibilityState", {
    value: "visible", configurable: true,
  });
  assert(cursor() === "grab", `cursor stayed "${cursor()}" after the tab was hidden`);
  move(300, 300, { buttons: 1 });
  assert(pans === held,
    "the sky kept panning after the tab was hidden — on a tablet that is a " +
    "screen timeout, and the pan resumes on the next stray touch");
});

test("losing pointer capture ends the drag", () => {
  pans = 0;
  down(100, 100);
  move(140, 130);
  const held = pans;
  assert(held > 0, "precondition: the pan never started");

  act(() => {
    box().dispatchEvent(new win.Event("lostpointercapture", { bubbles: true }));
  });
  move(300, 300, { buttons: 1 });
  assert(pans === held, "the drag survived losing the pointer capture it was riding");
  assert(cursor() === "grab", `cursor stayed "${cursor()}" after capture was lost`);
});

test("a hover with no button held ends a drag that lost its release", () => {
  // The self-heal. Every listener above is a guess about HOW the release went
  // missing; this one needs no theory — a mouse move reporting buttons=0 cannot
  // belong to a held gesture, whatever swallowed the pointerup.
  pans = 0;
  down(100, 100);
  move(140, 130);
  const held = pans;
  assert(held > 0, "precondition: the pan never started");

  move(200, 200, { buttons: 0 });   // the hover that should end it
  assert(pans === held,
    "the hover ITSELF panned the sky — it was treated as part of the drag");
  move(260, 260, { buttons: 1 });   // and it must stay ended
  assert(pans === held, "the drag survived a hover with nothing pressed");
  assert(cursor() === "grab", `cursor stayed "${cursor()}" after the hover`);
});

// ------------------------------------------------------- the expensive half
test("a rotate drag releases, and the released canvas stops looking held", () => {
  rotates = 0;
  down(100, 100, { on: handle() });
  move(100, 200);
  assert(rotates > 0,
    "pressing the rotate stalk and dragging rotated nothing — the handle " +
    "hit-test missed, so the rotate assertions below would prove nothing");
  assert(cursor() === "grabbing", `no held cursor during a rotate, got "${cursor()}"`);
  up(100, 200);
  assert(cursor() === "grab",
    `the canvas still shows "${cursor()}" after the rotate handle was released — ` +
    "it advertises itself as held with nothing on it");
});

test("alt-tabbing mid-rotate does not leave the camera angle following the mouse", () => {
  // The one that reaches the hardware: rotation_deg is what AtlasView posts to
  // the rotator on the next "Go to this target".
  rotates = 0;
  down(100, 100, { on: handle() });
  move(100, 200);
  const spun = rotates;
  const angle = lastRotation;
  assert(spun > 0, "precondition: the rotate never started");

  act(() => { win.dispatchEvent(new win.Event("blur")); });
  move(300, 100, { buttons: 1 });
  move(300, 400, { buttons: 1 });
  assert(rotates === spun,
    `${rotates - spun} more rotations landed after the window lost focus — the ` +
    "PA the next slew commands is being written by an unheld mouse");
  assert(lastRotation === angle,
    `the camera angle moved from ${angle}° to ${lastRotation}° with nothing held`);
});

test("a tablet screen timeout mid-rotate ends the finger's rotate", () => {
  // The device this app is driven from. A finger reports buttons=1 for as long
  // as it exists and sends no hover moves, so the no-button self-heal cannot
  // reach this case — the visibilitychange exit is the only thing between a
  // sleeping tablet and a camera angle that follows the next touch.
  const touch = { pointerType: "touch", buttons: 1 };
  rotates = 0;
  down(100, 100, { ...touch, on: handle() });
  move(100, 200, touch);
  const spun = rotates;
  assert(spun > 0,
    "a finger on the rotate stalk rotated nothing — touch is gated out of this " +
    "path, so the assertion below would prove nothing");

  act(() => {
    Object.defineProperty(win.document, "visibilityState", {
      value: "hidden", configurable: true,
    });
    win.document.dispatchEvent(new win.Event("visibilitychange", { bubbles: true }));
  });
  Object.defineProperty(win.document, "visibilityState", {
    value: "visible", configurable: true,
  });
  move(300, 300, touch);
  assert(rotates === spun,
    `${rotates - spun} more rotations landed after the screen went away — the ` +
    "next stray touch is writing the PA the following slew will command");
});

test("a right-button press starts no drag at all", () => {
  // The native context menu eats the release, so this press had no exit.
  up(0, 0);   // independence: nothing an earlier test left may be read as state
  pans = 0;
  down(100, 100, { button: 2, buttons: 2 });
  assert(cursor() === "grab",
    `a right-click put the canvas in "${cursor()}" — it took the press as a drag`);
  move(200, 220, { buttons: 2 });
  assert(pans === 0, "a right-button drag panned the sky");
  move(300, 300, { buttons: 0 });
  assert(pans === 0, "and it went on panning on a bare hover afterwards");
});

// ------------------------------------------------------------------- report
test("scrolling an overlay leaves sky zoom unchanged", () => {
  zooms = 0;
  const control = container.querySelector('[data-atlas-controls] button');
  act(() => control.dispatchEvent(new win.WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 80 })));
  assert(zooms === 0, 'Scrolling layers zoomed the sky underneath');
  act(() => box().dispatchEvent(new win.WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 80 })));
  assert(zooms === 1, 'Wheel zoom on the sky itself stopped working');
});
act(() => { root.unmount(); });

const total = passed + failed;
console.log(`skyCanvasDrag.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
