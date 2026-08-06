// holdButtonDom.test.tsx — HoldButton, MOUNTED, held, and interrupted.
//
//   Run directly:  npx tsx src/components/__tests__/holdButtonDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS IS ABOUT. HoldButton is the confirm on Abort sequence, Disconnect
// All, "Connect this rig", park-while-imaging and the permanent frame purge —
// every action in the app that is expensive to undo. Its whole contract is
// "this fires only while a finger is on the glass for `holdMs`". Two ways it
// could fire without one, both fixed on 2026-08-05:
//
//   1. progress was `performance.now() - start`, which measures the CLOCK. rAF
//      does not run while a tab is hidden and can be starved for most of a
//      second by a GC pause, so the first frame after the gap saw
//      elapsed >= holdMs and fired.
//   2. a press interrupted by the tab going away delivers no pointerup and no
//      pointercancel, so the hold sat there and completed itself later.
//
// The rAF driver below is MANUAL: jsdom's own rAF is a 16ms timer, which cannot
// express "no frames ran for a second and then one did" — the exact shape of
// the bug. Driving frames by hand is what makes the stall reproducible at all.

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

// --- a hand-cranked clock + frame pump ---------------------------------------
// `clock` is what the component reads through performance.now(); `frame()` runs
// exactly one animation frame. Nothing advances unless a test says so, so
// "700ms of wall clock passed but only one frame ran" is expressible.
let clock = 1000;
const frames: FrameRequestCallback[] = [];
win.performance.now = () => clock;
win.requestAnimationFrame = ((cb: FrameRequestCallback) => {
  frames.push(cb);
  return frames.length;
}) as any;
win.cancelAnimationFrame = ((id: number) => { delete frames[id - 1]; }) as any;

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "performance",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { HoldButton } = await import("../ui");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** Run the frames that are pending, advancing the clock by `stepMs` before
 *  each. A frame scheduled BY a frame runs on the next call, exactly as a
 *  browser would. */
function pump(count: number, stepMs: number): void {
  act(() => {
    for (let i = 0; i < count; i++) {
      const due = frames.splice(0, frames.length).filter(Boolean);
      if (due.length === 0) break;
      clock += stepMs;
      for (const cb of due) cb(clock);
    }
  });
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

let confirms = 0;
/** The rendered face carries `bind.hintLabel` while armed, which is how the
 *  caption assertions read it — the same way monitor.tsx and ConfirmDialog do. */
function Harness({ holdMs, disabled = false }: { holdMs: number; disabled?: boolean }) {
  return createElement(HoldButton, {
    label: "Abort sequence",
    holdMs,
    disabled,
    onConfirm: () => { confirms++; },
    children: (bind: any) => createElement(
      "button",
      {
        type: "button",
        "data-testid": "hold",
        "aria-label": bind["aria-label"],
        "data-progress": String(bind.progress),
        "data-armed": String(bind.armed),
        onPointerDown: bind.onPointerDown,
        onPointerUp: bind.onPointerUp,
        onKeyDown: bind.onKeyDown,
        onKeyUp: bind.onKeyUp,
        // Every real call site spreads this; without it the element's own blur
        // is unobservable and the keyboard arming outlives leaving the control.
        onBlur: bind.onBlur,
        // ...and every real call site ALSO puts `disabled` on the native button,
        // which is what removes the pointer exit when it flips mid-hold.
        disabled,
      },
      bind.armed ? bind.hintLabel : "Abort",
    ),
  } as any);
}

const HOLD_MS = 700;
act(() => { root.render(createElement(Harness, { holdMs: HOLD_MS })); });
const btn = () => container.querySelector('[data-testid="hold"]') as any;

function pointer(type: string, id = 1): any {
  const ev = new win.Event(type, { bubbles: true, cancelable: true });
  ev.pointerId = id;
  ev.pointerType = "touch";
  ev.button = 0;
  ev.isPrimary = true;
  return ev;
}
function key(type: string, k: string, repeat = false): any {
  const ev = new win.KeyboardEvent(type, { key: k, bubbles: true, cancelable: true, repeat });
  return ev;
}
const progress = () => Number(btn().getAttribute("data-progress"));

// --------------------------------------------------------------- the fixture
test("the button mounted and the hand-cranked frame pump drives it", () => {
  assert(btn() != null, "no hold button in the document — the fixture is wrong, not the component");
  confirms = 0;
  act(() => { btn().dispatchEvent(pointer("pointerdown")); });
  pump(3, 20);
  assert(progress() > 0,
    "three 20ms frames advanced no progress — the pump is not driving the component, " +
    "so every 'it did not fire' assertion below would pass vacuously");
  act(() => { btn().dispatchEvent(pointer("pointerup")); });
  assert(confirms === 0, "a 60ms hold fired the confirm");
});

test("a genuine, fully-watched hold still fires — the control must keep working", () => {
  confirms = 0;
  act(() => { btn().dispatchEvent(pointer("pointerdown")); });
  pump(60, 16);  // ~960ms of watched frames over a 700ms hold
  assert(confirms === 1, `a full 60-frame hold fired ${confirms} times, expected 1`);
  act(() => { btn().dispatchEvent(pointer("pointerup")); });
});

// ----------------------------------------------------- THE HAZARD: the stall
test("a one-second main-thread stall does not complete the hold by itself", () => {
  confirms = 0;
  act(() => { btn().dispatchEvent(pointer("pointerdown")); });
  pump(2, 16);                       // watched: ~32ms
  const watched = progress();
  assert(watched > 0 && watched < 0.2,
    `precondition: the hold should be barely started, got ${watched}`);
  pump(1, 1000);                     // one frame, a second of wall clock later
  assert(confirms === 0,
    "the hold FIRED across a 1s stall — nobody held anything for 700ms, the clock did");
  assert(progress() < 0.5,
    `unwatched time bought ${progress()} of the hold; only frames we saw may count`);
  act(() => { btn().dispatchEvent(pointer("pointerup")); });
});

// --------------------------------------- THE HAZARD: the press that went away
test("hiding the tab mid-press cancels the hold instead of finishing it", () => {
  confirms = 0;
  act(() => { btn().dispatchEvent(pointer("pointerdown")); });
  pump(5, 16);
  // POSITIVE CONTROL: without this, "it did not fire" proves nothing — a press
  // that never started could not fire either.
  assert(btn().getAttribute("data-armed") === "true",
    "the press never armed the hold, so the cancellation below is unfalsifiable");

  act(() => {
    Object.defineProperty(win.document, "visibilityState", {
      value: "hidden", configurable: true,
    });
    win.document.dispatchEvent(new win.Event("visibilitychange", { bubbles: true }));
  });
  assert(btn().getAttribute("data-armed") === "false",
    "the hold is still armed after the tab was hidden — no pointerup or pointercancel " +
    "is coming, so it is now a confirm waiting to happen with no finger down");

  // And it must stay dead when the frames the browser owed us finally run.
  pump(60, 16);
  assert(confirms === 0,
    "the hold completed itself after the tab was hidden — this is Abort / Disconnect " +
    "All firing on a screen the user is not even looking at");
  Object.defineProperty(win.document, "visibilityState", {
    value: "visible", configurable: true,
  });
  act(() => { btn().dispatchEvent(pointer("pointerup")); });
});

test("losing window focus mid-press cancels it too", () => {
  confirms = 0;
  act(() => { btn().dispatchEvent(pointer("pointerdown")); });
  pump(5, 16);
  assert(btn().getAttribute("data-armed") === "true", "precondition: the hold never started");
  act(() => { win.dispatchEvent(new win.Event("blur")); });
  pump(60, 16);
  assert(confirms === 0, "the hold fired after the window lost focus");
  assert(btn().getAttribute("data-armed") === "false", "still armed after blur");
  act(() => { btn().dispatchEvent(pointer("pointerup")); });
});

// ------------------------------------------------- the keyboard two-step copy
test("armed by keyboard, the caption says PRESS AGAIN — not HOLD", () => {
  confirms = 0;
  act(() => { btn().dispatchEvent(key("keydown", "Enter")); });
  assert(btn().getAttribute("data-armed") === "true",
    "Enter did not arm the two-step, so there is no caption to check");
  const caption = (btn().textContent || "").trim();
  assert(/PRESS AGAIN/i.test(caption),
    `armed by keyboard the caption reads "${caption}" — it must ask for the second ` +
    "PRESS, because holding is the one input this path drops (e.repeat)");
  assert(!/^HOLD TO/i.test(caption),
    `the caption still instructs "${caption}", which the keyboard path ignores`);
  assert(/ABORT SEQUENCE/i.test(caption),
    `the caption "${caption}" no longer says what the second press will do`);
});

test("a held key does not confirm, and the second press does", () => {
  confirms = 0;
  act(() => { btn().dispatchEvent(key("keydown", "Enter", true)); });  // auto-repeat
  assert(confirms === 0, "an auto-repeat keydown confirmed — a held key is not two presses");
  act(() => { btn().dispatchEvent(key("keydown", "Enter")); });
  assert(confirms === 1, `the deliberate second press fired ${confirms} times, expected 1`);
  assert(btn().getAttribute("data-armed") === "false", "still armed after confirming");
});

test("resting caption is still HOLD TO …", () => {
  // The pointer affordance must not have been collateral damage.
  act(() => { btn().dispatchEvent(pointer("pointerdown")); });
  pump(2, 16);
  const caption = (btn().textContent || "").trim();
  act(() => { btn().dispatchEvent(pointer("pointerup")); });
  assert(/^HOLD TO ABORT SEQUENCE$/i.test(caption),
    `holding by pointer shows "${caption}", expected the HOLD TO … affordance`);
});

// ------------------------------------------------------------------- report
// ------------------------------------------- going disabled mid-hold (class C)
test("a control that goes disabled mid-hold does not complete itself", () => {
  // THE HOLE: `disabled` used to be an ENTRY condition only. Every call site
  // also puts it on the native <button>, and a disabled button stops dispatching
  // pointer events — so the user's pointerup never arrived, and pointerup was
  // the only exit. The rAF ran on over a greyed-out button and fired anyway.
  //
  // Reachable in one gesture on Settings > Profiles: renaming a profile commits
  // on BLUR, which the pointerdown's focus default action triggers one frame
  // into a 700ms hold, disabling that row's button.
  confirms = 0;
  act(() => { root.render(createElement(Harness, { holdMs: HOLD_MS, disabled: false })); });
  act(() => { btn().dispatchEvent(pointer("pointerdown")); });
  pump(5, 16);
  assert(btn().getAttribute("data-armed") === "true",
    "precondition: the hold never started, so nothing below would prove anything");

  act(() => { root.render(createElement(Harness, { holdMs: HOLD_MS, disabled: true })); });
  pump(80, 16);   // well past the 700ms hold

  assert(confirms === 0,
    "the hold completed on a DISABLED button — the destructive action fired on a " +
    "press the user had already abandoned, with the control greyed out and dead to touch");
  assert(btn().getAttribute("data-armed") === "false",
    "the fill kept sweeping across a disabled button");
  act(() => { root.render(createElement(Harness, { holdMs: HOLD_MS, disabled: false })); });
});

// ------------------------------------- leaving the control disarms it (class C)
test("tabbing away from an armed control disarms the keyboard two-step", () => {
  // `blur` does not bubble, so HoldButton's window-level listener never sees
  // focus move between two elements in the same page. The button went on
  // reading "PRESS AGAIN TO ABORT" with no focus — and tabbing back inside the
  // 3s window made a SINGLE Enter fire, collapsing the two-step confirm to one.
  confirms = 0;
  act(() => { btn().dispatchEvent(key("keydown", "Enter")); });
  assert(btn().getAttribute("data-armed") === "true",
    "precondition: the first Enter did not arm, so the disarm below proves nothing");

  act(() => { btn().dispatchEvent(new win.FocusEvent("focusout", { bubbles: true })); });
  assert(btn().getAttribute("data-armed") === "false",
    "still armed after focus left the control — it wears PRESS AGAIN with no focus");

  // The load-bearing half: the NEXT Enter must be a first press, not a confirm.
  act(() => { btn().dispatchEvent(key("keydown", "Enter")); });
  assert(confirms === 0,
    "a single Enter after tabbing back ABORTED THE RUN — the two-step confirm " +
    "had silently become one step");
  assert(btn().getAttribute("data-armed") === "true", "that Enter should have re-armed");
  act(() => { btn().dispatchEvent(new win.FocusEvent("focusout", { bubbles: true })); });
});

act(() => { root.unmount(); });

const total = passed + failed;
console.log(`holdButtonDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
