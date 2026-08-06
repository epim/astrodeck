// stretchHistogramDom.test.tsx — the stretch handles, MOUNTED and PRESSED.
//
//   Run directly:  npx tsx src/components/preview/__tests__/stretchHistogramDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY A SECOND STRETCH FILE. stretchHistogram.test.tsx asserts the LAYOUT
// contract (containment box, chip anchoring) from static markup and needs no
// DOM. This file asserts the INTERACTION, which static markup cannot see: the
// pointer handlers used to live on the <svg>, and the three handles are drawn
// OVER that svg as siblings of it — not descendants — so a press that landed on
// a handle bubbled to the containing div and reached no listener at all. Every
// level line was a 32px dead column down the middle of its own control, and the
// only way to move a level was to press beside it and let scrub-nearest
// teleport it there. Nothing about that is visible in a rendered tree; it needs
// a real event dispatched at a real node.
//
// WHAT THIS CANNOT DO, stated so a pass is not over-read: jsdom implements no
// layout, so getBoundingClientRect is stubbed to a fixed 256px track below and
// the value-under-x arithmetic is exercised against that stub rather than
// against a browser's boxes. What IS real here is which node an event reaches,
// which handler runs, and what the component does in response.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Installed BEFORE the component is imported: lib/base reads window.location at
// module scope, and ui.tsx portals into document.body.
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

// jsdom has no pointer capture. Stub it and RECORD it: which node the press
// bound itself to is load-bearing here, because the handles are rebuilt on
// every `onStretch` and a capture held by a replaced node is a capture on
// nothing.
const captures: { node: any; id: number }[] = [];
win.Element.prototype.setPointerCapture = function (this: any, id: number) {
  captures.push({ node: this, id });
};
win.Element.prototype.releasePointerCapture = function (this: any, id: number) {
  const i = captures.findIndex((c) => c.node === this && c.id === id);
  if (i >= 0) captures.splice(i, 1);
};

// A 256px-wide track anchored at x=0, so `clientX` IS the value in 1/256ths.
// jsdom measures nothing, and the component divides by the track width — left
// alone, every value would come out NaN and each assertion below would be about
// the stub rather than the code.
const TRACK_W = 256;
win.Element.prototype.getBoundingClientRect = function () {
  return {
    left: 0, top: 0, right: TRACK_W, bottom: 72,
    width: TRACK_W, height: 72, x: 0, y: 0, toJSON() {},
  };
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "matchMedia",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { StretchHistogram } = await import("../StretchHistogram");
type PreviewInfo = import("../../../types").PreviewInfo;
type StretchParams = import("../../../types").StretchParams;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** A linear frame that does NOT clip, so the only chip a handle can carry is
 *  the drag readout — which is the signal every press assertion below reads. */
function frame(over: Partial<PreviewInfo> = {}): PreviewInfo {
  return {
    id: 12,
    stats: { min: 120, max: 30000, mean: 900, median: 810, std: 220 },
    histogram: [1, 40, 900, 400, 90, 12, 3, 1],
    histogram_domain: "linear",
    exposure_s: 60,
    gain: 100,
    binning: 1,
    data_width: 4000,
    data_height: 3000,
    display_width: 1400,
    display_height: 1050,
    mime: "image/jpeg",
    source: "alpaca",
    is_stretched: false,
    data_is_linear: true,
    has_lossless: true,
    full_well: null,
    auto_levels: { black: 0, mid: 0.5, white: 1 },
    ts: 1_700_000_000,
    ...over,
  };
}

const container = win.document.getElementById("root") as any;

function neutral(): StretchParams {
  return { auto: true, black: 0, mid: 0.5, white: 1, brightness: 0, contrast: 0, advancedOpen: true };
}

let rootRef: ReturnType<typeof createRoot> | null = null;
let cur: StretchParams = neutral();
let calls: Partial<StretchParams>[] = [];
let shown: PreviewInfo = frame();

function onStretch(s: Partial<StretchParams>): void {
  calls.push(s);
  cur = { ...cur, ...s };
}

/** Re-render with whatever `cur`/`shown` now hold. Component state (`active`,
 *  focus) survives this, which is the point — a drag IS state across renders. */
function rerender(): void {
  act(() => {
    rootRef!.render(createElement(StretchHistogram, {
      preview: shown, stretch: cur, onStretch,
    }));
  });
}

/** A FRESH component for the next test. `active` deliberately outlives a
 *  re-render, so a test that left a press open would silently arm the one
 *  after it (the trap slewPadDom.test.tsx documents). */
function remount(over: Partial<PreviewInfo> = {}): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  cur = neutral();
  calls = [];
  shown = frame(over);
  rootRef = createRoot(container);
  rerender();
}

const handle = (which: string) => container.querySelector(`[data-handle="${which}"]`);
const track = () => container.querySelector(".relative.select-none");

function pointer(type: string, id: number, clientX: number): any {
  const ev = new win.Event(type, { bubbles: true, cancelable: true });
  ev.pointerId = id;
  ev.pointerType = "touch";
  ev.clientX = clientX;
  ev.clientY = 36;
  ev.button = 0;
  ev.isPrimary = true;
  return ev;
}
function fire(node: any, type: string, clientX: number, id = 3): void {
  act(() => { node.dispatchEvent(pointer(type, id, clientX)); });
}

// --------------------------------------------------------------- preconditions
remount();

test("the histogram mounted with three reachable handles — otherwise nothing below means anything", () => {
  for (const which of ["black", "mid", "white"]) {
    assert(handle(which) != null, `no ${which} handle in the mounted DOM — the fixture is wrong, not the component`);
  }
  assert(track() != null, "no track box rendered");
  assert(container.querySelectorAll('[role="slider"]').length === 3,
    "Advanced is not open, so the handles under test are not on screen");
});

// ------------------------------------------------------------------ THE BUG
test("pressing ON a handle grabs it (the press used to reach nothing at all)", () => {
  remount();
  const mid = handle("mid");
  // 140/256 = 0.547 — inside the mid handle's 32px box but NOT on its value, so
  // a press that is merely "near" would be visible as a jump.
  fire(mid, "pointerdown", 140);
  // The drag readout only renders for the handle the component considers
  // ACTIVE, so its presence is proof the press was received and attributed.
  assert(/50%/.test(mid.textContent || ""),
    "pressing the mid handle did not start a drag on it — the press reached no handler, " +
    "which is the 32px dead column: the only way to move this level is to press beside it");
  fire(track(), "pointerup", 140);
});

test("a grab does not shift the level it grabbed", () => {
  remount();
  const white = handle("white");
  fire(white, "pointerdown", 250); // 250/256 = 0.977, white is at 1.0
  // PRECONDITION. "it did not move" is trivially true of a press that reached
  // nothing at all, which is exactly the bug this file exists for.
  assert(/100%/.test(white.textContent || ""),
    "the press never registered, so 'it did not move' proves nothing");
  assert(calls.length === 0,
    `grabbing the white handle moved it: ${JSON.stringify(calls)} — a press anywhere in ` +
    "the 32px box would yank the level up to 6% of the track away from where the finger landed");
  fire(track(), "pointerup", 250);
});

test("dragging after the grab moves that handle, and only that handle", () => {
  remount();
  fire(handle("white"), "pointerdown", 250);
  fire(track(), "pointermove", 192); // 192/256 = 0.75
  assert(calls.length > 0, "a pointermove after grabbing the white handle changed nothing");
  const last = calls[calls.length - 1];
  assert(last.white != null && Math.abs(last.white - 0.75) < 1e-6,
    `the drag should have set white to 0.75, got ${JSON.stringify(last)}`);
  assert(last.black === 0 && last.mid === 0.5,
    `dragging white must not move the other levels — got ${JSON.stringify(last)}`);
  fire(track(), "pointerup", 192);
});

test("releasing ends the drag (the readout goes away)", () => {
  remount();
  const mid = handle("mid");
  fire(mid, "pointerdown", 128);
  assert(/50%/.test(mid.textContent || ""), "the drag never started, so ending it proves nothing");
  fire(track(), "pointerup", 128);
  rerender();
  assert(!/50% ·/.test(handle("mid").textContent || ""),
    "the drag readout is still up after the release — the drag never ended");
});

// ------------------------------------------------- the gesture that still works
test("a press on bare track still scrubs the nearest handle to it (§11.5)", () => {
  remount();
  // The svg, not a handle: 64/256 = 0.25, whose nearest level is black at 0.
  fire(container.querySelector("svg"), "pointerdown", 64);
  assert(calls.length === 1, `a press on the histogram should move the nearest level, got ${calls.length} calls`);
  assert(calls[0].black != null && Math.abs(calls[0].black - 0.25) < 1e-6,
    `scrub-nearest should have moved black to 0.25 — got ${JSON.stringify(calls[0])}`);
  assert(calls[0].auto === false, "grabbing a level must flip Auto off (sticky manual)");
  fire(track(), "pointerup", 64);
});

// ------------------------------------------------------ focus across a re-render
test("a focused handle survives a re-render, so a second arrow key still lands", () => {
  remount();
  const before = handle("black");
  before.focus();
  // PRECONDITION. Without this the assertion after the re-render is unfalsifiable:
  // "focus did not move" is trivially true if focus was never there.
  assert(win.document.activeElement === before,
    "the handle never took focus, so nothing below is testing anything");

  shown = frame({ id: 13 }); // the 2s frame that re-renders this panel
  rerender();

  const after = handle("black");
  assert(before === after,
    "the black handle is a DIFFERENT DOM node after a re-render — React tore it down " +
    "and rebuilt it, which is what dropped keyboard focus mid-nudge");
  assert(win.document.activeElement === after,
    "focus left the handle when the panel re-rendered, so the next arrow key goes to the body");
});

test("arrow keys still nudge the focused handle", () => {
  remount();
  const black = handle("black");
  act(() => {
    const ev = new win.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true });
    black.dispatchEvent(ev);
  });
  assert(calls.length === 1, `ArrowRight on the black handle should nudge it, got ${calls.length} calls`);
  assert(calls[0].black != null && Math.abs(calls[0].black - 0.01) < 1e-9,
    `ArrowRight should step black by 0.01 — got ${JSON.stringify(calls[0])}`);
});

// ---------------------------------------------------------------- NINA is inert
test("a NINA frame's handles still refuse the press, and say why", () => {
  remount({ is_stretched: true, data_is_linear: false, histogram_domain: "display" });
  const mid = handle("mid");
  assert(mid.getAttribute("aria-disabled") === "true", "a NINA handle must announce itself disabled");
  assert(/NINA/.test(mid.getAttribute("aria-label") || ""),
    "the NINA handle's accessible name must carry the reason, not just the state");
  fire(mid, "pointerdown", 140);
  assert(calls.length === 0, "a NINA handle moved when pressed — these levels are fixed at the source");
  assert(!/50% ·/.test(mid.textContent || ""), "a NINA handle started a drag");
});

// ------------------------------------------------------------------- report
act(() => { rootRef!.unmount(); });
const total = passed + failed;
console.log(`stretchHistogramDom: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
