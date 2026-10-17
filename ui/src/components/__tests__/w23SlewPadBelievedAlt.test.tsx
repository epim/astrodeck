// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// WP-220 / #1021: the slew pad reads the mount's altitude through
// `believedPointing`, the one gate for the horizontal reading (#791), and not
// through `m.alt` behind a gate of its own.
//
//   Run directly:  npx tsx src/components/__tests__/w23SlewPadBelievedAlt.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The pad fed its altitude guard from `m?.alt ?? null` and printed
// its near-horizon line from `m && positionIsKnown && m.alt < MIN + 5`. While
// the position is unknown the hand-written `positionIsKnown` arm kept the
// pole's altitude (the site latitude, #140) off the page, and that one term was
// the only thing that did. And a reading that is not a finite number got
// through both: -Infinity printed "near horizon (-Infinity°)" and tripped the
// guard on a number nobody could read, and a null altitude satisfied `null <
// 15` and then threw inside `m.alt.toFixed`, taking the pad down.
//
// w16SlewPadPositionUnknown grades the unknown-position behaviour with the old
// gate in place. This file grades the same behaviour in a way that holds with
// the pad's own gate REMOVED (mutants m2 and m3 below), and the unreadable
// readings the old gate let through.
//
// WHAT EACH CASE HOLDS THE PAD TO:
//   1. control: a known low altitude is printed in the near-horizon line, and a
//      high one prints no line.
//   2. control: a known altitude under the limit stops a hold.
//   3. an altitude that is not a finite number (-Infinity, Infinity, NaN, null,
//      absent) prints no near-horizon line and no number, and the pad stays up.
//   4. such a reading is not an altitude, so it does not trip the guard: a hold
//      is driven (the controller's contract is that a null altitude means the
//      guard is not consulted, which is what NaN and an absent key always did).
//   5. an unknown position prints no altitude, whatever the home reading is,
//      and says the guard is off. Holds on the old gate and on the selector;
//      the mutants below that delete the pad's own gate are graded by it.
//   6. an unknown position does not trip the guard on the home reading.
//
// Named mutants (each an edit to SlewPad.tsx, run from a byte backup and
// restored byte-identically). The first failing assertion of each is quoted:
//   w23m1_raw_alt_old_gate -- the defect restored: `altRef` from `m?.alt ?? null`
//     and the line from `m && positionIsKnown && m.alt < MIN + 5`. 13/16: "x a
//     -Infinity altitude with the position known prints no line and no number,
//     and the pad stays up: the near-horizon line is up for a -Infinity
//     altitude: ..."; also the null case ("Cannot read properties of null
//     (reading 'toFixed')") and "x a -Infinity altitude does not trip the
//     guard: it is not an altitude: the pad flashed BELOW HORIZON on a reading
//     it could not print".
//   w23m2_raw_alt_gate_removed -- m1 with the pad's own `positionIsKnown &&`
//     also deleted, and `getPositionKnown` made `() => true`: the edit the
//     issue feared, on the code it feared it on. 10/16: m1's three, plus "x
//     position_known false, home altitude 12: no altitude is on the pad and the
//     guard-off note is: the near-horizon line is up for the home reading" (and
//     the same at 3), and "x position_known false: a hold on a home altitude
//     under the limit is driven, not tripped: the guard tripped on the mount's
//     home reading".
//   w23m3_line_raw_no_gate -- ONLY the near-horizon line on the raw altitude
//     with no gate of its own; the selector kept for the guard. 12/16: the
//     -Infinity and null line cases, and the home altitudes 12 and 3 ("the
//     near-horizon line is up for the home reading").
// With `getPositionKnown` alone made `() => true` the file stays green: the
// guard is fed from the selector, so that term is a second line behind it.

/* eslint-disable @typescript-eslint/no-explicit-any */

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
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};
win.Element.prototype.hasPointerCapture = function () { return true; };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { api } = await import("../../api");
const SlewPad = (await import("../SlewPad")).default;
const {
  slewRatesWithCeiling, ALT_GUARD_OFF_NOTE, ALT_GUARD_UNREADABLE_NOTE, MIN_SLEW_ALT_DEG,
} = await import("../../lib/slewController");

// Every write the pad makes, with its body: the guard assertions are about
// whether a NON-ZERO move left.
const posts: { path: string; body: any }[] = [];
(api as any).post = async (path: string, body?: any) => {
  posts.push({ path, body });
  return {};
};

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(a: T, b: T, msg: string): void {
  if (a !== b) throw new Error(`${msg} (expected ${String(b)}, got ${String(a)})`);
}

// A 1.44 deg/s mount: four rungs, the last one the ceiling.
const CEILING = 1.44;
const RATES = slewRatesWithCeiling(CEILING);

// Made-up, distinctive numbers, so a leak is a plain substring test. 71 is the
// altitude a mount reads at "home" in this fixture; it is nobody's site.
const HOME_ALT = 71;
const NEAR_ALT = 12;
const HIGH_ALT = 45;

function mountBlock(over: Record<string, unknown> = {}, drop: string[] = []) {
  const m: Record<string, unknown> = {
    ra_hours: 5.6, dec_deg: -5.4, ra_str: "05h35m", dec_str: "-05d23m",
    alt: HIGH_ALT, az: 120, tracking: true, parked: false, slewing: false,
    max_rate_deg_s: CEILING,
    ...over,
  };
  for (const k of drop) delete m[k];
  return m;
}
/** Wrapped in `act` here: a bare `setState` re-renders a mounted pad outside
 *  React's batch and every later case logs a not-wrapped warning. */
function seed(over: Record<string, unknown> = {}, drop: string[] = []): void {
  act(() => {
    useStore.setState({
      status: { connected: {}, looping: false, mode: "sim", mount: mountBlock(over, drop) },
      principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] },
      toasts: [],
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let root: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (root) act(() => { root!.unmount(); });
  root = createRoot(container);
  act(() => {
    root!.render(createElement(SlewPad as any, { rates: RATES, maxRateDegS: CEILING }));
  });
}
const radios = (): any[] =>
  [...container.querySelectorAll('[aria-label="Slew rate"] [role="radio"]')];
const arrow = (label: string): any => container.querySelector(`[aria-label="slew ${label}"]`);
const text = (): string => (container.textContent || "") as string;
const click = (node: any) => act(() => {
  node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
});
function pointer(type: string, id: number): any {
  const ev = new win.Event(type, { bubbles: true, cancelable: true });
  ev.pointerId = id;
  ev.pointerType = "touch";
  ev.button = 0;
  ev.isPrimary = true;
  return ev;
}
/** Press, and ALWAYS release: a press left up rejects every later press at the
 *  pad's multi-touch guard and the next case fails for the wrong reason. */
function press(label: string, id: number, between: () => void = () => {}): void {
  const node = arrow(label);
  assert(node != null, `no ${label} key on the pad (did the pad render?)`);
  act(() => { node.dispatchEvent(pointer("pointerdown", id)); });
  try { between(); }
  finally { act(() => { node.dispatchEvent(pointer("pointerup", id)); }); }
}
const movesPosted = () => posts.filter((p) => p.path === "/api/mount/move");
const liveMoves = () => movesPosted().filter((p) => p.body.rate_deg_s !== 0);
/** A continuous rung, so a press is a hold the guard can act on. */
function pickContinuous(): void {
  const r = radios();
  assert(r.length === 4, `the ladder the fixture built is not on the pad (${r.length} rungs)`);
  click(r[2]);
}

let nextId = 1;
/** Hold east once and report what the pad did: the non-zero moves it posted
 *  while the key was down, and whether it flashed the horizon trip. */
function holdEast(): { live: number; tripped: boolean } {
  posts.length = 0;
  let live = 0;
  press("east", nextId++, () => { live = liveMoves().length; });
  return { live, tripped: /BELOW HORIZON/.test(text()) };
}

// ---------------------------------------------------------------- 1. controls
seed({ alt: NEAR_ALT });
mount();

test("control: a KNOWN low altitude prints the near-horizon line", () => {
  assert(arrow("east") != null, "the pad did not render - the fixture is wrong");
  assert(new RegExp(`near horizon \\(${NEAR_ALT}°\\)`).test(text()),
    `the near-horizon line is gone for a known low altitude: ${text()}`);
  assert(!text().includes(ALT_GUARD_OFF_NOTE), "the guard-off note is up for a known position");
});

test("control: a KNOWN high altitude prints no near-horizon line", () => {
  seed({ alt: HIGH_ALT });
  assert(!/near horizon/.test(text()), `the line is up at ${HIGH_ALT} degrees: ${text()}`);
});

test("control: a KNOWN altitude just past the line's margin prints no line", () => {
  seed({ alt: MIN_SLEW_ALT_DEG + 5 + 1 });
  assert(!/near horizon/.test(text()), `the line is up past its margin: ${text()}`);
  seed({ alt: MIN_SLEW_ALT_DEG + 5 - 1 });
  assert(/near horizon/.test(text()), `the line is gone inside its margin: ${text()}`);
});

// ------------------------------------------------------------------ 2. guard
seed({ alt: 3 });
mount();
pickContinuous();

test("control: a KNOWN altitude under the limit stops a hold", () => {
  const r = holdEast();
  eq(r.live, 0, "a move was posted below the horizon limit");
  assert(r.tripped, "the pad did not say it stopped");
});

// ----------------------------------------------- 3. a reading that is no number
// Each of these is an altitude nobody can read. They cannot arrive over JSON
// today (the server sends rounded numbers, and a viewer's frame carries no key
// at all), so the pad is shown them directly: what it does with one is the
// contract of the gate it reads through.
const UNREADABLE: { name: string; over: Record<string, unknown>; drop: string[] }[] = [
  { name: "-Infinity", over: { alt: Number.NEGATIVE_INFINITY }, drop: [] },
  { name: "Infinity", over: { alt: Number.POSITIVE_INFINITY }, drop: [] },
  { name: "NaN", over: { alt: Number.NaN }, drop: [] },
  // `null < 15` is true, so the old line went on to call `null.toFixed`.
  { name: "null", over: { alt: null }, drop: [] },
  { name: "absent", over: {}, drop: ["alt"] },
];

for (const u of UNREADABLE) {
  test(`a ${u.name} altitude with the position known prints no line and no number, and the pad stays up`, () => {
    seed(u.over, u.drop);
    mount();
    assert(arrow("east") != null, `the pad did not render with a ${u.name} altitude`);
    assert(!/near horizon/.test(text()), `the near-horizon line is up for a ${u.name} altitude: ${text()}`);
    assert(!/\b(Infinity|NaN|null|undefined)\b/.test(text()), `the pad prints the reading: ${text()}`);
  });
}

// ------------------------------------- 4. such a reading is not driven unguarded
// #1044 (WP-229): the position is known and the altitude is not readable, so
// the guard has nothing to measure. A hold is REFUSED (never driven without a
// guard), nothing flashes BELOW HORIZON (no altitude was read), and the pad
// says why before any press.
for (const u of UNREADABLE) {
  test(`a ${u.name} altitude with the position known: a hold is refused and the pad says why`, () => {
    seed(u.over, u.drop);
    mount();
    pickContinuous();
    assert(text().includes(ALT_GUARD_UNREADABLE_NOTE),
      `the pad refuses holds without saying why: ${text()}`);
    assert(!text().includes(ALT_GUARD_OFF_NOTE), "the unknown-position note is up for a known position");
    const r = holdEast();
    assert(!r.tripped, "the pad flashed BELOW HORIZON on a reading it could not print");
    eq(r.live, 0, "a hold was driven with no horizon guard");
  });
}

test("control: a readable altitude prints no refusal note and the hold is driven", () => {
  seed({ alt: HIGH_ALT });
  mount();
  pickContinuous();
  assert(!text().includes(ALT_GUARD_UNREADABLE_NOTE), "the refusal note is up for a readable altitude");
  eq(holdEast().live, 1, "a hold on a readable altitude was refused");
});

// ------------------------------------------------------ 5. unknown: no altitude
// The pad's own `positionIsKnown` arm used to be all that kept these off the
// page. They are graded here without assuming it is still there.
for (const alt of [HOME_ALT, NEAR_ALT, 3, HIGH_ALT]) {
  test(`position_known false, home altitude ${alt}: no altitude is on the pad and the guard-off note is`, () => {
    seed({ alt, position_known: false });
    mount();
    assert(!/near horizon/.test(text()), `the near-horizon line is up for the home reading: ${text()}`);
    assert(!new RegExp(String.raw`(?<![\d.])${alt}\s*°`).test(text()),
      `the home altitude ${alt} is on the pad: ${text()}`);
    assert(text().includes(ALT_GUARD_OFF_NOTE), "the pad dropped the guard without saying so");
  });
}

// ------------------------------------------ 6. unknown: the guard reads nothing
test("position_known false: a hold on a home altitude under the limit is driven, not tripped", () => {
  seed({ alt: 3, position_known: false });
  mount();
  eq(radios()[3].getAttribute("aria-checked"), "true", "precondition: the ceiling rung is selected");
  const r = holdEast();
  assert(!r.tripped, "the guard tripped on the mount's home reading");
  eq(r.live, 1, "the pad refused to drive a reset mount home on an altitude it cannot trust");
});

if (root) act(() => { root!.unmount(); });

const total = passed + failed;
console.log(`w23SlewPadBelievedAlt.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
