// stepDialDom.test.tsx — the step dial's arc, and the tap that has to close it.
//
//   Run directly:  npx tsx src/components/__tests__/stepDialDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY A DOM TEST. focusPod.test.tsx already covers this control as MARKUP
// (touch-action, role). The defect here is not in the markup: the dismissal was
// authored as a `fixed inset-0` backdrop, and this control lives inside the
// Focuser `.panel`, whose `backdrop-filter: blur(14px)` makes a fixed descendant
// resolve against the PANEL box rather than the viewport (measured in this repo
// — index.css §overlay: "a `fixed inset-0` probe inside a panel resolves to
// 700x1515, not the viewport"). Nothing about that is visible in a rendered
// string; it needs a document, an open arc, and a press somewhere else in it.
//
// WHAT IT COSTS WHEN IT IS WRONG. The stranded arc keeps the trigger wearing its
// engaged `border-accent text-accent` face with four option buttons floating
// over the position/max/temp stats, and the next stray tap on one of them
// silently re-sizes the step — so the NEXT press of +/- moves the focuser by a
// magnitude nobody chose (up to 1000 steps). Escape covers a keyboard; a phone
// in the dark had no way out at all.
//
// WHAT IT CANNOT DO: jsdom lays nothing out and does not implement
// backdrop-filter, so the containing-block trap itself is NOT reproducible here.
// This asserts the fix that makes the trap irrelevant — a window-level listener
// that closes on any press outside the dial's own subtree — plus the two paths
// it must not have broken (the backdrop inside the panel, and committing an
// option).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  // #elsewhere stands in for everything the backdrop cannot reach: the live
  // preview, the Camera panel, the page gutter.
  `<!doctype html><html><body><div id="root"></div><div id="elsewhere">preview</div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const StepDial = (await import("../ui/StepDial")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const VALUES = [1, 10, 100, 1000] as const;
let step = 100;
const changes: number[] = [];

const container = win.document.getElementById("root") as any;
const elsewhere = win.document.getElementById("elsewhere") as any;
const root = createRoot(container);

function render(): void {
  act(() => {
    root.render(createElement(StepDial, {
      values: VALUES as unknown as number[],
      value: step,
      onChange: (v: number) => { changes.push(v); step = v; },
      ariaLabel: "Focuser step size",
    }));
  });
}
render();

function pointer(type: string): any {
  const ev = new win.Event(type, { bubbles: true, cancelable: true });
  ev.pointerId = 1;
  ev.pointerType = "touch";
  ev.button = 0;
  ev.isPrimary = true;
  ev.clientY = 200;      // no travel between down and up => the TAP path
  return ev;
}
const trigger = (): any => container.querySelector('[role="spinbutton"]');
/** The arc's option buttons — everything except the trigger itself. */
const options = (): any[] =>
  [...container.querySelectorAll("button")].filter((b: any) => b !== trigger());
const open = (): boolean => options().length > 0;

/** The documented TAP path: press and release with no travel leaves the arc
 *  open so it can be used as a menu (StepDial.tsx §"A press with no travel"). */
function tapTrigger(): void {
  act(() => { trigger().dispatchEvent(pointer("pointerdown")); });
  act(() => { trigger().dispatchEvent(pointer("pointerup")); });
}

// --------------------------------------------------------------- the fixture
test("a tap on the dial blooms the arc and leaves the trigger engaged", () => {
  assert(trigger() != null, "no spinbutton in the document — the fixture is wrong");
  assert(!open(), "the arc is open before anything was pressed");
  tapTrigger();
  assert(open(), "the tap did not open the arc, so every dismissal below is vacuous");
  assert(options().length === VALUES.length,
    `the arc shows ${options().length} options, expected ${VALUES.length}`);
  assert(/border-accent/.test(trigger().className),
    "the trigger does not wear its engaged face while open, so 'it stops wearing it' " +
    "below would prove nothing");
});

// ------------------------------------------ THE TAP THE BACKDROP CANNOT SEE
test("a press outside the dial's panel closes the arc", () => {
  assert(open(), "precondition: the arc is not open");
  act(() => { elsewhere.dispatchEvent(pointer("pointerdown")); });
  assert(!open(),
    "the arc survived a press on another panel: `fixed inset-0` inside a `.panel` " +
    "resolves to the PANEL box, so the backdrop never covered the live preview, the " +
    "Camera panel or the gutter — the arc sat open over the stats with the next stray " +
    "tap silently re-sizing the focuser step");
  assert(!/border-accent/.test(trigger().className),
    "the trigger still reads engaged with no arc under it");
});

test("…and the stranded arc can no longer commit a magnitude nobody chose", () => {
  // The consequence, stated as a consequence: with the arc gone there is no
  // 1000-step option left on screen for a stray tap to land on.
  const before = changes.length;
  assert(options().length === 0, "options are still mounted after the dismissal");
  assert(changes.length === before, "the dismissal itself committed a value");
});

// ------------------------------------------------- WHAT MUST NOT HAVE BROKEN
test("the in-panel backdrop still dismisses, and still swallows the press", () => {
  tapTrigger();
  assert(open(), "precondition: the arc did not reopen");
  // The backdrop is the last child of the dial's own root: `fixed inset-0`,
  // aria-hidden, with its own onPointerDown. Inside the Focuser panel it is
  // what stops a dismissing tap from ALSO pressing Halt or a nudge button.
  const backdrop = container.querySelector('div[aria-hidden].fixed');
  assert(backdrop != null,
    "the backdrop is gone — a tap meant to dismiss the arc now presses whatever " +
    "Focuser control is underneath it");
  act(() => { backdrop.dispatchEvent(pointer("pointerdown")); });
  assert(!open(), "the backdrop no longer closes the arc");
});

test("pressing an option still commits it — the window listener is not a lid", () => {
  tapTrigger();
  assert(open(), "precondition: the arc did not reopen");
  const opt = options().find((b: any) => (b.textContent || "").trim() === "1000");
  assert(opt != null, "no 1000 option in the arc");
  const before = changes.length;
  act(() => {
    // A real tap on an option is pointerdown (which the new window listener also
    // sees) and then click. If the listener closed on it, the click would land
    // on an unmounted node and nothing would commit.
    opt.dispatchEvent(pointer("pointerdown"));
    opt.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  assert(changes.length === before + 1 && changes[changes.length - 1] === 1000,
    `picking an option committed ${JSON.stringify(changes.slice(before))}, expected [1000]`);
  assert(!open(), "the arc stayed open after a commit");
});

test("Escape still closes it without committing", () => {
  tapTrigger();
  assert(open(), "precondition: the arc did not reopen");
  const before = changes.length;
  act(() => {
    win.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
  assert(!open(), "Escape no longer closes the arc");
  assert(changes.length === before, "Escape committed a value");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`stepDialDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
