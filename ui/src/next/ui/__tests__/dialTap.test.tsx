// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// dialTap.test.tsx - a tap on a Dial stop sets the value, the way drag, the
// arrow keys and the text input already do (#656).
//
//   Run directly:  npx tsx src/next/ui/__tests__/dialTap.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT (found 2026-10-01 against the rebuilt user docs' "build and run a
// simple Flow" procedure, reproduced twice: a plain click and a forced click
// at a stop's measured coordinates). primitivesDom.test.tsx's "Dial selects by
// TAP on a stop" dispatches a bare synthetic `click` event straight at the
// stop and passes - but a real finger never gets a free click like that. The
// browser synthesizes one from pointerdown/pointerup, and `onPointerDown`
// calls `setPointerCapture` on the TRACK for the whole gesture; in some
// browsers that also retargets the synthesized click's hit test onto the
// track, so the stop's own onClick is never reached. Arrow keys and the text
// input are unaffected because neither goes through click at all.
//
// THE REPRODUCTION. Dispatch pointerdown then pointerup alone - no click event
// anywhere in this file - and mock `elementFromPoint` the way
// wireDropLoop.test.ts does for a wire drop's hit test, since jsdom does no
// layout and cannot answer "what is under this point" on its own.
//
// Every mutation named below ran in a private scratch copy of ui/ (never the
// shared tree), from a byte-for-byte backup of Dial.tsx, sha256-verified
// restored afterward, with the mutant text grepped gone.

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
// The Dial calls both behind a guard; jsdom implements neither.
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

// jsdom does no layout, so "what real element sits under this point" has to be
// told to it - the same stand-in wireDropLoop.test.ts and canvasDom.test.tsx
// use for a wire drop's hit test. Each case below points it at the stop a real
// finger would have landed on.
let hitTarget: any = null;
win.document.elementFromPoint = () => hitTarget;

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "getComputedStyle", "matchMedia",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { Dial } = await import("../index");

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
/** `picked`/`explained` are built fresh per case, so comparing them is
 *  comparing their CONTENTS - joined to a string, the same way
 *  framingCardDial.test.tsx compares `stopValues()`, rather than by reference
 *  (two arrays with the same values are never `===`). */
function eqList(got: string[], want: string[], msg: string): void {
  eq(got.join(","), want.join(","), msg);
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const render = (el: any) => { act(() => { root.render(el); }); };
const q = (sel: string): any => container.querySelector(sel);
const qa = (sel: string): any[] => Array.from(container.querySelectorAll(sel));

/** A pointer step, named by its real event type ("pointerdown" / "pointerup" /
 *  "pointercancel"). React listens by TYPE, so a MouseEvent under that name
 *  reaches the matching handler with a real clientX/clientY - the same trick
 *  primitivesDom.test.tsx and wireDropLoop.test.ts both use for a pointer
 *  event jsdom has no constructor for. There is no `click` helper in this
 *  file at all: that omission is the whole point of it. */
const pointer = (el: any, type: string, clientX = 0, clientY = 0) => {
  act(() => {
    el.dispatchEvent(new win.MouseEvent(type, { bubbles: true, cancelable: true, clientX, clientY }));
  });
};

// A made-up, site-independent set of stops - the mechanism under test does not
// care what a Dial is for, and naming them A-D keeps this file about the tap
// itself rather than any one device sheet's values.
const OPTS = [
  { value: "A", label: "Stop A" },
  { value: "B", label: "Stop B" },
  { value: "C", label: "Stop C" },
  { value: "D", label: "Stop D" },
];

let picked: string[] = [];
function mount(value: string, extra: Record<string, unknown> = {}): void {
  picked = [];
  render(createElement(Dial as any, {
    label: "TEST DIAL", options: OPTS, value,
    onChange: (v: string) => { picked.push(v); },
    "data-testid": "test-dial",
    ...extra,
  }));
}

const track = () => q(".nx-dial-track");
const stop = (value: string) => q(`.nx-dial-stop[data-value="${value}"]`);

// ============================================================ the tap itself

test("precondition: the dial rendered its four stops", () => {
  mount("A");
  assert(track() != null, "no dial track - the fixture is wrong, not the component");
  eq(qa(".nx-dial-stop").length, 4, "not every option rendered a stop");
});

test("a tap on a stop sets the value with no click event anywhere (#656)", () => {
  // Named mutant: the tap handler removed - endDrag reverted to doing nothing
  // but release pointer capture, as it did before this fix. RED under that
  // mutant, observed:
  //   x a tap on a stop sets the value with no click event anywhere (#656):
  //     tapping stop C changed nothing (expected C, got )
  mount("A");
  hitTarget = stop("C");
  pointer(track(), "pointerdown", 200, 10);
  pointer(track(), "pointerup", 200, 10);
  eqList(picked, ["C"], "tapping stop C changed nothing");
});

test("the tap resolves from the RELEASE position, not the press position", () => {
  // Matches FlowCanvasSurface's own wire-drop contract: "resolved on the
  // pointerup coordinates, against the real element under them" - a few
  // pixels of finger drift between press and release (under the existing 3 px
  // move threshold) must not freeze the tap to wherever the finger first
  // touched down.
  mount("A");
  hitTarget = stop("B");
  pointer(track(), "pointerdown", 100, 10); // touches down over B
  hitTarget = stop("D");
  pointer(track(), "pointerup", 102, 10);   // lifts 2px later, over D
  eqList(picked, ["D"], "the tap used the press position instead of the release position");
});

test("control: tapping the already-selected stop sends nothing", () => {
  mount("B");
  hitTarget = stop("B");
  pointer(track(), "pointerdown", 50, 10);
  pointer(track(), "pointerup", 50, 10);
  eqList(picked, [], "a tap on the selected stop sent a value");
});

test("control: a real drag still commits through onPointerMove, and release does not re-fire it", () => {
  mount("A");
  pointer(track(), "pointerdown", 300, 10);
  pointer(track(), "pointermove", 236, 10);   // one 64px stop left -> B
  eqList(picked, ["B"], "the drag itself did not commit");
  // If release re-resolved a tap by position on top of a finished drag, this
  // would pick D instead of leaving the drag's own B alone.
  hitTarget = stop("D");
  pointer(track(), "pointerup", 236, 10);
  eqList(picked, ["B"], "the release re-applied a tap on top of a completed drag");
});

test("pointercancel never commits, even positioned over a different stop", () => {
  // Named mutant: `e.type !== "pointerup" ||` deleted from endDrag's guard
  // (leaving only `if (d.moved || blocked()) return;`). RED under that
  // mutant, observed:
  //   x pointercancel never commits, even positioned over a different stop:
  //     a cancelled gesture changed the value (expected , got D)
  mount("A");
  hitTarget = stop("D");
  pointer(track(), "pointerdown", 10, 10);
  pointer(track(), "pointercancel", 999, 10);
  eqList(picked, [], "a cancelled gesture changed the value");
});

test("a lock that arrives between press and release blocks the tap and explains why", () => {
  // Same timing class as review #75 (a drag that begins live and is locked
  // mid-scrub): the permission has to be read at RELEASE time, not only at
  // the press that started the gesture.
  // Named mutant: `|| blocked()` deleted from endDrag's guard (leaving only
  // `if (e.type !== "pointerup" || d.moved) return;`). RED under that mutant,
  // observed:
  //   x a lock that arrives between press and release blocks the tap and
  //     explains why: the blocked tap said nothing (expected a flow owns the
  //     mount, got )
  const explained: string[] = [];
  mount("A");
  hitTarget = stop("C");
  pointer(track(), "pointerdown", 1, 1);
  render(createElement(Dial as any, {
    label: "TEST DIAL", options: OPTS, value: "A",
    onChange: (v: string) => { picked.push(v); },
    lockedReason: "a flow owns the mount",
    onExplain: (r: string) => { explained.push(r); },
    "data-testid": "test-dial",
  }));
  pointer(track(), "pointerup", 1, 1);
  eqList(picked, [], "a lock that arrived mid-tap still committed");
  eqList(explained, ["a flow owns the mount"], "the blocked tap said nothing");
});

const total = passed + failed;
console.log(`dialTap.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
