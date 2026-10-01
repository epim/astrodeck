// framingCardDial.test.tsx - the framing card's rotation controls show the angle
// the frame is really at, and touching them does not move it away (#173).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/frame/__tests__/framingCardDial.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The finder's rotate drag writes continuous degrees, and the dial
// was handed `ROTS.includes(v) ? v : 0`: after a drag to 37 it sat on the 0 stop
// under a label reading 37, and its first arrow key or scrub moved the frame
// from 0. Nothing on screen was wrong except the one control that should have
// said 37, and the cost was the angle the user had framed.
//
// Every mutation named below was run in a private scratch copy of ui/ (never the
// shared tree), from a byte-for-byte backup of FramingCard.tsx, and each quoted
// failure is the one observed there, with the file's sha256 matched against the
// backup afterwards. RED BEFORE THE FIX, against FramingCard.tsx as it was at
// 812fcf9e: 0/14 passed, the defect itself reading
//
//   x an angle between the stops renders as itself, never as 0: the dial's
//     selected stop at 37 (expected 37, got 0)
//   x the arrow keys move from the angle the frame is at, to its neighbours:
//     ArrowRight from 37 (expected 45, got 15)
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally
// plus the `{ passed, failed, total }` export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// `FramingCard` reaches `mosaic.ts`, which imports `api.ts`, and that reads
// `window.location` AT MODULE SCOPE, so the globals go in before any import of
// the component (the same order frameModel.test.ts keeps, for the same reason).
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
// The Dial calls both behind a guard; jsdom implements neither.
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "localStorage", "getComputedStyle", "matchMedia",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act, useState } = await import("react");
const { createRoot } = await import("react-dom/client");
const { FramingCard, wrapDeg } = await import("../FramingCard");
const { ROTS } = await import("../mosaic");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

const container = win.document.getElementById("root") as any;

const q = (sel: string): any => container.querySelector(sel);
const qa = (sel: string): any[] => Array.from(container.querySelectorAll(sel));
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const keydown = (el: any, key: string) => {
  act(() => { el.dispatchEvent(new win.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })); });
};
/** A pointer step of a drag. React listens by TYPE, so a MouseEvent named
 *  "pointermove" reaches `onPointerMove` with a real `clientX`, which is all
 *  the Dial reads (primitivesDom.test.tsx drives it the same way). */
const pointer = (el: any, type: string, clientX: number) => {
  act(() => {
    el.dispatchEvent(new win.MouseEvent(type, { bubbles: true, cancelable: true, clientX }));
  });
};
/** Type into the PA field the way React sees it: the native value setter,
 *  then an `input` event, then Enter (NumberField commits on Enter and blur). */
const typeInto = (el: any, text: string) => {
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(el, text);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  keydown(el, "Enter");
};

const dial = () => q('[data-testid="sky-rot-dial"]');
const track = () => q('[data-testid="sky-rot-dial"] [role="slider"]');
const selected = () => q('[data-testid="sky-rot-dial"] [data-selected="true"]');
const stopValues = () => qa('[data-testid="sky-rot-dial"] [data-value]').map((s: any) => s.getAttribute("data-value"));
const field = () => q('[data-testid="sky-rot-deg"]');
const nudge = (d: number) => q(`[data-nudge="${d}"]`);

/** What the card sent, in order. */
let sent: number[] = [];
/** The finder's rotate drag, which writes the SAME framing slice from outside
 *  the card: set by the harness below on every render. */
let fromCanvas: (deg: number) => void = () => {};

/** The card with the store's role played by a `useState`: `onRotate` writes
 *  it and the card re-renders on the new angle, as SkyHub's
 *  `setFraming({ rotation_deg })` makes it do. */
function Harness({ start }: { start: number }) {
  const [deg, setDeg] = useState(start);
  fromCanvas = setDeg;
  return createElement(FramingCard, {
    targetName: "M31",
    cols: 1,
    rows: 1,
    rotationDeg: deg,
    fovXDeg: 1.2,
    fovYDeg: 0.8,
    overlap: 0.15,
    rotator: null,
    rotatorRange: { range_type: "full", range_start_deg: 0 },
    onMosaic: () => {},
    onRotate: (d: number) => { sent.push(d); setDeg(d); },
  });
}

/** A fresh card at `start`, with nothing sent yet. A fresh root per case, so a
 *  held stop from one case can never carry into the next. */
let current: ReturnType<typeof createRoot> | null = null;
function mount(start: number): void {
  if (current) act(() => { current!.unmount(); });
  current = createRoot(container);
  sent = [];
  act(() => { current!.render(createElement(Harness, { start })); });
}

// ============================================================ the display

test("precondition: the card rendered its dial, its PA field and four nudges", () => {
  mount(37);
  assert(dial() != null, "no rotation dial - the fixture is wrong, not the component");
  assert(field() != null, "no PA field");
  eq(qa("[data-nudge]").length, 4, "the nudge count:");
});

test("an angle between the stops renders as itself, never as 0", () => {
  // RED under the named mutation 'ROTS.includes(v) ? v : 0' (the Dial handed
  // `value={ROTS.includes(rotationDeg) ? rotationDeg : 0}` again), observed
  // (7/14 passed; this case, the next three off-grid cases, the arrow keys,
  // the scrub and the typed 37.5 went red, and both controls stayed green):
  //
  //   x an angle between the stops renders as itself, never as 0: the dial's
  //     selected stop at 37 (expected 37, got 0)
  mount(37);
  eq(selected()?.getAttribute("data-value"), "37", "the dial's selected stop at 37");
  eq(track()?.getAttribute("aria-valuetext"), "37°", "what a screen reader hears at 37");
  eq(field()?.value, "37", "the PA field at 37");
  assert(/CAMERA ROTATION · 37°/.test(dial()?.textContent ?? ""), "the dial label lost the angle");
});

test("control: a stop renders as itself, and the dial carries no extra stop", () => {
  // Stays GREEN under 'ROTS.includes(v) ? v : 0': 30 is a stop, so the
  // mutation changes nothing here. That is what makes the case above a test of
  // the off-grid angle and not of the dial in general.
  mount(30);
  eq(selected()?.getAttribute("data-value"), "30", "the dial's selected stop at 30");
  eq(field()?.value, "30", "the PA field at 30");
  eq(stopValues().join(","), ROTS.join(","), "the stops at a stop");
});

test("the off-grid angle is a stop in its place, and a fraction reads as one", () => {
  // RED under "the held stop never follows" (below), observed:
  //
  //   x the off-grid angle is a stop in its place, and a fraction reads as
  //     one: the selected stop at a dragged 37.24 (expected 37.24, got 0)
  mount(37);
  eq(stopValues().join(","), "0,15,30,37,45,60,75,90,105,120,135,150,165", "the stops at 37");
  // A dragged angle is a float. The dial names it to a tenth; the value on
  // the stop, and so what is sent, is the angle itself.
  act(() => { fromCanvas(37.24); });
  eq(selected()?.getAttribute("data-value"), "37.24", "the selected stop at a dragged 37.24");
  eq(selected()?.textContent, "37.2°", "the stop's label at 37.24");
  eq(field()?.value, "37.2", "the PA field at 37.24");
});

test("an angle the finder's drag writes moves the held stop with it", () => {
  // RED under the mutation "the held stop never follows" (the render-phase
  // `setHeld(rotationDeg)` sync deleted, so only the mount's angle is held),
  // observed (11/14 passed):
  //
  //   x an angle the finder's drag writes moves the held stop with it: the
  //     selected stop after a drag to 52 (expected 52, got 0)
  mount(37);
  act(() => { fromCanvas(52); });
  eq(selected()?.getAttribute("data-value"), "52", "the selected stop after a drag to 52");
  assert(!stopValues().includes("37"), "the stop for the angle the drag left is still on the dial");
  eq(sent.length, 0, "the card sent a rotation for a drag it did not make:");
});

// ============================================================ touching it

test("tapping the stop it is on, or a scrub that goes nowhere, sends nothing", () => {
  // RED under 'ROTS.includes(v) ? v : 0', observed:
  //
  //   x tapping the stop it is on, or a scrub that goes nowhere, sends
  //     nothing: the dial after being touched (expected 37, got 0)
  mount(37);
  click(selected());
  eq(sent.length, 0, "a tap on the selected stop sent an angle:");
  pointer(track(), "pointerdown", 100);
  pointer(track(), "pointermove", 110);
  pointer(track(), "pointerup", 110);
  eq(sent.length, 0, "a 10 px scrub sent an angle:");
  eq(selected()?.getAttribute("data-value"), "37", "the dial after being touched");
});

test("the arrow keys move from the angle the frame is at, to its neighbours", () => {
  // RED under 'ROTS.includes(v) ? v : 0', observed (the dial sat on 0, so its
  // right neighbour was 15):
  //
  //   x the arrow keys move from the angle the frame is at, to its neighbours:
  //     ArrowRight from 37 (expected 45, got 15)
  mount(37);
  keydown(track(), "ArrowRight");
  eq(sent[0], 45, "ArrowRight from 37");
  mount(37);
  keydown(track(), "ArrowLeft");
  eq(sent[0], 30, "ArrowLeft from 37");
});

test("a scrub off the held stop keeps counting stops from where it began", () => {
  // RED under the mutation "the stops follow the value" (`rotationStops(
  // ROTS.includes(rotationDeg) ? null : rotationDeg)` in place of the held
  // stop), observed (13/14 passed: the 37 left the list the moment the drag
  // reached 45, the stops renumbered under the finger, and the next sample
  // landed on 60):
  //
  //   x a scrub off the held stop keeps counting stops from where it began:
  //     the angle after one stop's scrub (expected 45, got 60)
  mount(37);
  pointer(track(), "pointerdown", 200);
  pointer(track(), "pointermove", 136);   // one 64 px stop to the left
  pointer(track(), "pointermove", 130);   // still within that stop
  pointer(track(), "pointerup", 130);
  eq(sent[sent.length - 1], 45, "the angle after one stop's scrub");
  eq(selected()?.getAttribute("data-value"), "45", "the dial after one stop's scrub");
});

// ================================================ the nudges and the field

test("the nudges move by 1 and 15 from where the frame is, with no snap", () => {
  // RED under the mutation "a nudge snaps to the 15-degree grid" (the nudge
  // sending `wrapDeg(Math.round((rotationDeg + d) / 15) * 15)`), observed
  // (12/14 passed; the wrap case below went red with it, "-1 from 0
  // (expected 359, got 0)"):
  //
  //   x the nudges move by 1 and 15 from where the frame is, with no snap:
  //     after +1 from 37 (expected 38, got 45)
  mount(37);
  click(nudge(1));
  eq(sent[0], 38, "after +1 from 37");
  click(nudge(15));
  eq(sent[1], 53, "after +15 from 38");
  click(nudge(-1));
  eq(sent[2], 52, "after -1 from 53");
  click(nudge(-15));
  eq(sent[3], 37, "after -15 from 52");
  eq(field()?.value, "37", "the PA field after four nudges");
});

test("a nudge across north wraps into [0, 360), as the finder's drag does", () => {
  // RED under the mutation "the nudge does not wrap" (`rotationDeg + d` sent
  // bare), observed (13/14 passed):
  //
  //   x a nudge across north wraps into [0, 360), as the finder's drag does:
  //     -1 from 0 (expected 359, got -1)
  mount(0);
  click(nudge(-1));
  eq(sent[0], 359, "-1 from 0");
  mount(350);
  click(nudge(15));
  eq(sent[0], 5, "+15 from 350");
});

test("the PA field takes any angle, and folds one past a turn", () => {
  // RED under the mutation "the field commits unwrapped" (`onRotate(v)` in
  // place of `onRotate(wrapDeg(v))`), observed (12/14 passed; the 360 case
  // below went red with it):
  //
  //   x the PA field takes any angle, and folds one past a turn: 400 typed
  //     (expected 40, got 400)
  mount(37);
  typeInto(field(), "37.5");
  eq(sent[0], 37.5, "37.5 typed");
  eq(selected()?.getAttribute("data-value"), "37.5", "the dial after 37.5 was typed");
  typeInto(field(), "400");
  eq(sent[1], 40, "400 typed");
  eq(field()?.value, "40", "the PA field after 400 was typed");
});

test("a typed angle that folds onto the one held puts the held angle back in the box", () => {
  // RED under the mutation "no resetKey" (the `resetKey={typed}` prop
  // deleted), observed (13/14 passed):
  //
  //   x a typed angle that folds onto the one held puts the held angle back
  //     in the box: the PA field after 360 was typed at 0 (expected 0, got 360)
  mount(0);
  typeInto(field(), "360");
  eq(field()?.value, "0", "the PA field after 360 was typed at 0");
});

test("control: leaving the field untouched sends nothing", () => {
  mount(37.24);
  act(() => { field().dispatchEvent(new win.FocusEvent("focusout", { bubbles: true })); });
  keydown(field(), "Enter");
  eq(sent.length, 0, "an untouched field sent an angle:");
});

test("wrapDeg folds both ways and never answers 360", () => {
  eq(wrapDeg(-1), 359, "wrapDeg(-1)");
  eq(wrapDeg(365), 5, "wrapDeg(365)");
  eq(wrapDeg(-1e-9), 0, "wrapDeg(-1e-9)");
  eq(wrapDeg(37.24 + 1), 38.24, "wrapDeg(37.24 + 1)");
});

if (current) act(() => { current!.unmount(); });

const total = passed + failed;
console.log(`framingCardDial.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
