// slewPadDom.test.tsx — the slew pad, MOUNTED, re-rendered, and released.
//
//   Run directly:  npx tsx src/components/__tests__/slewPadDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY A SECOND SLEWPAD FILE. slewPad.test.tsx asserts a PROXY for the hazard:
// that React is handed a host <button> at each slot and that no element in the
// pad's tree changes type between two render passes. That is real and it caught
// the bug — but it is one level of indirection from the thing that actually
// hurts somebody, which is a DOM NODE being destroyed while a thumb is on it.
// This file asserts the property itself, by mounting the component into a real
// document and re-rendering it the way telemetry does every 2 s.
//
// The two files are kept apart on purpose. The proxy file needs no jsdom, runs
// in milliseconds, and covers the WHOLE tree, so an inline component added
// anywhere in SlewPad later still trips it. This file covers only the four
// arrows, but covers them for real. Neither subsumes the other.
//
// WHAT THIS CANNOT DO, stated so nobody mistakes a pass here for proof:
// jsdom implements no pointer capture at all (setPointerCapture is not on its
// Element), so the capture is STUBBED below and its semantics are not under
// test. What is under test is the precondition capture depends on — that the
// node holding it is still mounted and still the same object after a re-render
// — plus the consequence: a release delivered to that node still stops the
// slew. Testing capture ROUTING itself (the browser sending pointerup to the
// captured element even when the finger has left it) needs a real browser.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Installed BEFORE the store or the component are imported: the api client
// reads window.location at module scope, and the component's module-level
// constants read matchMedia. run-tests.mjs gives this file its own process, so
// planting globals here cannot leak into another suite.
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

// jsdom has no pointer capture. Stub it and RECORD it, so a test can ask which
// node the press bound itself to — that node's fate is the whole hazard.
const captures: { node: any; id: number }[] = [];
win.Element.prototype.setPointerCapture = function (this: any, id: number) {
  captures.push({ node: this, id });
};
win.Element.prototype.releasePointerCapture = function (this: any, id: number) {
  const i = captures.findIndex((c) => c.node === this && c.id === id);
  if (i >= 0) captures.splice(i, 1);
};
win.Element.prototype.hasPointerCapture = function (this: any, id: number) {
  return captures.some((c) => c.node === this && c.id === id);
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  // Node >=21 defines `navigator` as a getter-only global, so a plain
  // assignment throws. defineProperty works for every key, so use it for all
  // of them rather than special-casing the one that bites today.
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;   // React 18: makes act() flush updates

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const SlewPad = (await import("../SlewPad")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** A mount above the horizon, unparked, with control.mount — the only state in
 *  which the pad renders arrows at all. `alt` is a parameter because bumping it
 *  is the cheapest honest way to simulate the 2 s telemetry frame: it is a real
 *  field the pad really subscribes to, not a synthetic poke. */
function seed(alt: number): void {
  useStore.setState({
    status: {
      connected: {}, looping: false, mode: "sim",
      mount: {
        ra_hours: 5.6, dec_deg: -5.4, ra_str: "05h35m", dec_str: "-05°23'",
        alt, az: 120, tracking: true, parked: false, slewing: false,
      },
    },
    principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] },
  } as never);
}

seed(45);
const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(SlewPad)); });

const arrow = (label: string) => container.querySelector(`[aria-label="slew ${label}"]`);
/** The 2 s status frame, which is what made this hazard fire in the field. */
const telemetryTick = (alt: number) => { act(() => { seed(alt); }); };

function pointer(type: string, id: number): any {
  const ev = new win.Event(type, { bubbles: true, cancelable: true });
  ev.pointerId = id;
  ev.pointerType = "touch";
  ev.button = 0;
  ev.isPrimary = true;
  return ev;
}

/** Press, and ALWAYS release — even if the assertions in between throw.
 *
 *  `activePointerId` is a ref on SlewPad that outlives everything here, and
 *  onPointerDown rejects a press while it is set (F-A4, the multi-touch guard).
 *  So a test that presses and never releases silently disarms every test after
 *  it: the next press returns at the guard, takes no capture, starts no hold,
 *  and its assertions fail for a reason that has nothing to do with the code
 *  under test. That happened while writing this file and cost two false
 *  failures — hence a helper rather than discipline. */
function press(node: any, id: number, between: () => void): void {
  act(() => { node.dispatchEvent(pointer("pointerdown", id)); });
  try { between(); }
  finally { act(() => { node.dispatchEvent(pointer("pointerup", id)); }); }
}

// A CONTINUOUS rate, selected once for every press below. The pad defaults to
// SLEW_RATES[0] = "pulse", which is tap-only: beginPress starts no hold, so
// nothing is ever "held" and any assertion about releasing a hold is vacuously
// true whether the bug is present or not. It is also the only configuration in
// which this hazard can actually run a mount away from you.
const rateButtons = [...container.querySelectorAll('[role="radio"]')];
const continuousRate: any = rateButtons.find((b: any) => /0\.5/.test(b.textContent || ""));
act(() => {
  continuousRate?.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
});

// ------------------------------------------------------------------- the pad
test("the pad actually mounted — four arrows and a stop bar in a real document", () => {
  // The anti-blank-page guard. Every assertion below is about a node surviving;
  // if the pad never rendered, "no node" would pass several of them vacuously.
  for (const d of ["north", "south", "east", "west"]) {
    assert(arrow(d) != null, `no ${d} arrow in the mounted DOM — the fixture is wrong, not the component`);
  }
  assert(/stop/i.test(container.textContent || ""), "no STOP control rendered — this is not the live pad");
});

// -------------------------------------------------------------- THE HAZARD
test("a telemetry frame does not replace the button under your thumb", () => {
  const before = arrow("north");
  telemetryTick(46);
  const after = arrow("north");
  assert(before === after,
    "the north <button> is a DIFFERENT DOM node after a 2s telemetry frame — " +
    "React unmounted and remounted it, which releases the pointer capture that " +
    "is the only thing binding the slew to the finger");
});

test("no arrow is replaced by a re-render, in any direction", () => {
  const before = ["north", "south", "east", "west"].map(arrow);
  telemetryTick(47);
  const after = ["north", "south", "east", "west"].map(arrow);
  before.forEach((b, i) => {
    assert(b === after[i],
      `the ${["north", "south", "east", "west"][i]} button was replaced by a re-render`);
  });
});

test("the pad is on a continuous rate — otherwise the hold tests below prove nothing", () => {
  assert(continuousRate != null, "no continuous slew rate offered by the pad");
  assert(continuousRate.getAttribute("aria-checked") === "true",
    "the rate radio did not take the click, so the pad is still on tap-only pulse " +
    "and every 'is it still held' assertion below would pass vacuously");
});

test("a press survives a telemetry frame: same node, still mounted, still captured", () => {
  captures.length = 0;
  const node = arrow("north");
  press(node, 7, () => {
    assert(captures.length === 1, `press took ${captures.length} pointer captures, expected 1`);
    assert(captures[0].node === node, "capture was taken on some other element than the pressed button");

    telemetryTick(48);   // the frame that used to destroy it mid-press

    assert(container.contains(node),
      "the pressed button is no longer in the document after a telemetry frame — " +
      "its pointer capture died with it and nothing is bound to the finger");
    assert(arrow("north") === node, "the north slot now holds a different node than the one pressed");
    assert(node.hasPointerCapture(7), "the pressed node no longer holds the capture");
  });
});

test("releasing the node you pressed still stops the slew after a re-render", () => {
  // THE CONSEQUENCE, not the mechanism. The release is dispatched at the node
  // captured at pointerdown — which is what a browser does even once the finger
  // has slid off it. If a re-render replaced that node, the event lands on a
  // detached element React is not listening to: endPress never runs, and the
  // 600ms keepalive keeps feeding the server's 1200ms deadman, so the mount
  // keeps slewing. This assertion IS "does the telescope stop".
  captures.length = 0;
  const node = arrow("north");

  act(() => { node.dispatchEvent(pointer("pointerdown", 7)); });
  // POSITIVE CONTROL. Without it, "nothing is held afterwards" is unfalsifiable.
  assert(/border-accent/.test(node.className),
    "the press did not start a hold, so 'nothing is held afterwards' would prove nothing");

  telemetryTick(49);
  const held = captures[0]?.node ?? node;
  act(() => { held.dispatchEvent(pointer("pointerup", 7)); });

  const stillActive = ["north", "south", "east", "west"]
    .map(arrow)
    .filter((el: any) => el && /border-accent/.test(el.className));
  assert(stillActive.length === 0,
    `${stillActive.length} arrow(s) still show the held state after the release — ` +
    "endPress did not run, so nothing told the mount to stop");
});

test("after that release the pad accepts a NEW press", () => {
  // activePointerId outlives an arrow remount, so a lost release leaves it set
  // and every later press is rejected at the guard — the pad goes dead until
  // STOP. A second press taking capture is how we know it was cleared.
  captures.length = 0;
  const node = arrow("south");
  press(node, 11, () => {
    assert(captures.length === 1,
      "a fresh press took no capture — activePointerId was never cleared, so the pad is dead");
  });
});

// ------------------------------------------------- THE LOST RELEASE (#38)
test("locking mid-press does not leave the pad dead for the rest of the night", () => {
  // The screen lock engages with a finger down. The lock overlay then swallows
  // the pointerup, so the handler that clears `activePointerId` never runs —
  // and that ref is the multi-touch guard every later press is checked against.
  // Left set, the pad looks entirely alive: arrows still depress, the keyboard
  // path still works, and not one press reaches the mount.
  captures.length = 0;
  const held = arrow("east");
  act(() => { held.dispatchEvent(pointer("pointerdown", 21)); });

  // PRECONDITIONS. A press that took no capture never set the ref, and a press
  // that started no hold is not the mid-press state the lock has to interrupt;
  // either way the assertion at the end would pass with the fix reverted.
  assert(captures.length === 1, "the press took no pointer capture — nothing was ever bound");
  assert(/border-accent/.test(held.className),
    "the press started no hold, so there is no interrupted press under test");

  // NO pointerup is dispatched here, deliberately: that is the whole failure.
  act(() => { useStore.setState({ locked: true } as never); });
  assert(!/border-accent/.test(arrow("east").className),
    "locking did not stop the slew — the lock effect never ran, so this test proves nothing");

  act(() => { useStore.setState({ locked: false } as never); });

  captures.length = 0;
  press(arrow("north"), 22, () => {
    assert(captures.length === 1,
      "the pad rejected a fresh press after unlock — activePointerId still owns the id of a " +
      "finger that is long gone, so every arrow animates and the mount never moves");
  });
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`slewPadDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
