// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// WP-126 / #144 (UI half): the slew pad while the mount does not know where it
// points, MOUNTED in a real document.
//
//   Run directly:  npx tsx src/components/__tests__/w16SlewPadPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS PINS. After a power cycle the AM5 reports its home position wherever
// the tube is, and the server publishes `status.mount.position_known = false`
// (WP-103). The pad is hosted by BOTH UIs, so what it does about that is graded
// here once rather than once per host:
//
//   * it selects the ceiling rung ONCE on the transition (an operator who has
//     to drive the tube home by eye wants the fast rung, not the tap-only GUIDE
//     default), and then leaves the selection alone;
//   * its altitude guard stops reading the believed position: the believed
//     altitude at the pole is the site latitude, so a guard on it either
//     refuses a hold the tube could safely make or waves one through;
//   * it never prints an altitude while the position is unknown (the near-
//     horizon line), and says the guard is off instead;
//   * in NINA mode a tap is a relative goto from the believed position, which
//     is exactly the step the rest of the package locks, so it is refused with
//     the reason.
//
// Named mutants (one-statement source changes in SlewPad.tsx; each run from a
// byte backup inside the worktree, restored byte-identically, mutant text
// grepped out). The first failing assertion of each is quoted verbatim:
//   w16p1_ceiling_not_selected -- the transition effect's `setRateIdx(...)`
//     made `void fastestRungIndex`. "x the transition to unknown selects the
//     ceiling rung: the pad did not move to the ceiling rung when the mount
//     stopped knowing where it points (expected 3, got 0)".
//   w16p2_ceiling_forced_every_frame -- the effect's `was &&` dropped, so every
//     render while unknown re-forces the rung. "x it is selected ONCE: the
//     operator's own pick survives every later frame: precondition: the pick
//     took (expected 1, got 3)".
//   w16p3_guard_not_dropped -- the controller's `getPositionKnown` made
//     `() => true`. "x with the position UNKNOWN the same press drives the tube:
//     the pad refused to drive a reset mount home on an altitude it cannot trust
//     (expected 1, got 0)".
//   w16p4_near_horizon_kept -- the near-horizon line's `positionIsKnown &&`
//     dropped. "x with the position unknown no altitude is printed and the pad
//     says the guard is off: the pad printed a believed altitude while the
//     position is unknown: ..." (the pad's text follows).
//   w16p5_nina_tap_goto_unguarded -- the NINA refusal's `if (!knownRef.current)`
//     made `if (false)`. "x NINA: a tap is a relative goto from the believed
//     position, so it is refused: a goto was computed from the believed
//     position: [{"path":"/api/mount/goto",...}]".
//   w16p6_mount_edge_missed -- `useRef(true)` made `useRef(false)`, so a pad
//     mounted into an already-unknown state never sees an edge. "x a pad mounted
//     into an already-unknown position selects the ceiling rung at once: a pad
//     opened on a reset mount sits on the tap-only rung (expected 3, got 0)".
//   w16p7_guard_off_note_dropped -- the note's `!positionIsKnown &&` made
//     `false &&`. "x with the position unknown no altitude is printed and the
//     pad says the guard is off: the pad dropped the guard without saying so".
//   w16p8_refusal_through_the_humanizer -- the refusal sent through `showToast`
//     instead of `enqueueToast`. "x NINA: a tap is a relative goto from the
//     believed position, so it is refused: the refusal did not say why:
//     Plate-solve failed - check focus/exposure, or solve manually."
//     (Observed before #792 narrowed the humanizer. `showToast` now passes this
//     sentence whole, so that mutant is no longer red; the assertions below stay
//     as the check that the refusal reaches the toast whole.)
//   h1m1_reason_goto_back -- (lib/slewController.ts) POSITION_UNKNOWN_STEPS_REASON
//     back to the round-3 "Go to a target away from the pole and solve and sync
//     there, ..." (#850). 12/13: "x NINA: a tap is a relative goto from
//     the believed position, so it is refused: the refusal advises a goto
//     (/go to a target/i): Steps and nudges are measured ...".

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
  slewRatesWithCeiling, POSITION_UNKNOWN_STEPS_REASON, ALT_GUARD_OFF_NOTE,
} = await import("../../lib/slewController");

// Every write the pad makes, recorded with its body: the guard assertions are
// about whether a NON-ZERO move left, and the NINA ones about whether a goto did.
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
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(a: T, b: T, msg: string): void {
  if (a !== b) throw new Error(`${msg} (expected ${String(b)}, got ${String(a)})`);
}

// A 1.44 deg/s mount: four rungs, the last one the ceiling.
const CEILING = 1.44;
const RATES = slewRatesWithCeiling(CEILING);

function mountBlock(over: Record<string, unknown> = {}) {
  return {
    ra_hours: 5.6, dec_deg: -5.4, ra_str: "05h35m", dec_str: "-05d23m",
    alt: 45, az: 120, tracking: true, parked: false, slewing: false,
    max_rate_deg_s: CEILING,
    ...over,
  };
}
/** Wrapped in `act` here, not at each call site: a bare `setState` re-renders
 *  the still-mounted pad outside React's batch and every case after it logs a
 *  not-wrapped warning that buries a real one. */
function seed(mount: Record<string, unknown> = {}, mode = "sim"): void {
  act(() => {
    useStore.setState({
      status: { connected: {}, looping: false, mode, mount: mountBlock(mount) },
      principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] },
      toasts: [],
    } as never);
  });
}
const frame = (mount: Record<string, unknown>, mode = "sim"): void => seed(mount, mode);

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
const checkedIdx = (): number =>
  radios().findIndex((r: any) => r.getAttribute("aria-checked") === "true");
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
  assert(node != null, `no ${label} key on the pad`);
  act(() => { node.dispatchEvent(pointer("pointerdown", id)); });
  try { between(); }
  finally { act(() => { node.dispatchEvent(pointer("pointerup", id)); }); }
}
const toasts = (): string => ((useStore.getState().toasts ?? []) as { title?: string }[])
  .map((t) => t.title ?? "").join(" | ");
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};
const movesPosted = () => posts.filter((p) => p.path === "/api/mount/move");

// ----------------------------------------------------------------- precondition
seed();
mount();

test("the pad mounted, with a four-rung ladder and a known position", () => {
  for (const d of ["north", "south", "east", "west"]) {
    assert(arrow(d) != null, `no ${d} arrow - the fixture is wrong, not the component`);
  }
  eq(radios().length, 4, "the ladder the fixture built is not on the pad");
  eq(checkedIdx(), 0, "a known position must leave the tap-only GUIDE rung selected");
  assert(!text().includes(ALT_GUARD_OFF_NOTE),
    "the horizon-guard-off note is up for a mount that knows where it points");
});

// ------------------------------------------------- the ceiling rung, once
test("a known position never moves the rate selection", () => {
  click(radios()[1]);
  frame({ alt: 46 });
  frame({ alt: 47 });
  eq(checkedIdx(), 1, "the operator's own pick was overwritten with the position known");
  click(radios()[0]);
});

test("the transition to unknown selects the ceiling rung", () => {
  eq(checkedIdx(), 0, "precondition: GUIDE selected");
  frame({ position_known: false });
  eq(checkedIdx(), 3,
    "the pad did not move to the ceiling rung when the mount stopped knowing where it points");
  assert(/1\.44 deg\/s/.test(radios()[checkedIdx()].textContent || ""),
    "the selected rung is not the mount's ceiling");
});

test("it is selected ONCE: the operator's own pick survives every later frame", () => {
  click(radios()[1]);                        // the operator picks 8x SID
  eq(checkedIdx(), 1, "precondition: the pick took");
  frame({ position_known: false, alt: 44 });
  frame({ position_known: false, alt: 43 });
  eq(checkedIdx(), 1,
    "a status frame re-forced the ceiling rung over the operator's choice");
});

test("clearing and re-latching is a NEW transition", () => {
  frame({ position_known: true });
  eq(checkedIdx(), 1, "clearing the latch must not touch the selection");
  frame({ position_known: false });
  eq(checkedIdx(), 3, "a second reset did not select the ceiling rung again");
  frame({ position_known: true });
  click(radios()[0]);
});

test("a pad mounted into an already-unknown position selects the ceiling rung at once", () => {
  seed({ position_known: false });
  mount();
  eq(checkedIdx(), 3, "a pad opened on a reset mount sits on the tap-only rung");
  seed();
  mount();
});

test("an engine that sends no flag is never treated as unknown", () => {
  const m: Record<string, unknown> = mountBlock();
  delete m.position_known;
  act(() => {
    useStore.setState({
      status: { connected: {}, looping: false, mode: "sim", mount: m },
    } as never);
  });
  eq(checkedIdx(), 0, "an absent flag moved the selection");
  assert(!text().includes(ALT_GUARD_OFF_NOTE), "an absent flag switched the guard off");
});

// ---------------------------------------------------------- the altitude guard
test("with the position KNOWN a hold below the horizon limit is refused (the control)", () => {
  seed({ alt: 3 });
  mount();
  click(radios()[2]);                         // a continuous rate
  posts.length = 0;
  press("east", 1);
  assert(movesPosted().every((p) => p.body.rate_deg_s === 0),
    `a move was posted below the horizon limit: ${JSON.stringify(movesPosted())}`);
  assert(/BELOW HORIZON/.test(text()), "the pad did not say it stopped");
});

test("with the position UNKNOWN the same press drives the tube", () => {
  seed({ alt: 3, position_known: false });
  mount();
  assert(checkedIdx() === 3, "precondition: the ceiling rung is selected");
  posts.length = 0;
  press("east", 2, () => {
    const live = movesPosted().filter((p) => p.body.rate_deg_s !== 0);
    eq(live.length, 1,
      "the pad refused to drive a reset mount home on an altitude it cannot trust");
    eq(live[0].body.rate_deg_s, CEILING, "the hold was not at the ceiling rung");
    eq(live[0].body.axis, "ra", "the east key did not move the RA axis");
  });
  assert(!/BELOW HORIZON/.test(text()), "the guard tripped on a believed altitude");
});

// ---------------------------------------------------------- the near-horizon line
test("with the position known the near-horizon line prints the altitude (the control)", () => {
  seed({ alt: 12 });
  mount();
  assert(/near horizon \(12/.test(text()), `the line is gone for a known position: ${text()}`);
});

test("with the position unknown no altitude is printed and the pad says the guard is off", () => {
  seed({ alt: 12, position_known: false });
  mount();
  assert(!/near horizon/.test(text()),
    `the pad printed a believed altitude while the position is unknown: ${text()}`);
  assert(!/12\s*°/.test(text()), "the believed altitude is on the pad");
  assert(text().includes(ALT_GUARD_OFF_NOTE),
    "the pad dropped the guard without saying so");
});

// ------------------------------------------------------------- NINA mode taps
await testAsync("NINA: a tap is a relative goto from the believed position, so it is refused", async () => {
  seed({ position_known: false }, "nina");
  mount();
  posts.length = 0;
  act(() => { useStore.setState({ toasts: [] } as never); });
  press("east", 3);
  await settle();
  assert(!posts.some((p) => p.path === "/api/mount/goto"),
    `a goto was computed from the believed position: ${JSON.stringify(posts)}`);
  assert(toasts().includes("measured from where the mount thinks it points"),
    `the refusal did not say why: ${toasts()}`);
  // The WHOLE sentence, not a prefix: a message sent through `showToast` runs
  // through `humanizeLog`, which once truncated past 140 characters and
  // rewrote anything containing both "plate" and "solve" to "Plate-solve failed
  // - check focus", the opposite of what the refusal says. It now maps only a
  // line that IS a failed solve and shortens only past CLIP_AT, so neither can
  // touch this reason; the asserts stay so a reword that makes it a failure
  // line, or a later humanizer rule that reads it, is caught here.
  assert(toasts().includes(POSITION_UNKNOWN_STEPS_REASON),
    `the refusal is not the shared reason, whole: ${toasts()}`);
  assert(!/Plate-solve failed/.test(toasts()),
    "the refusal was rewritten into a plate-solve failure by the toast humanizer");
  // #850: the pad refused a relative goto because the position is unknown, so
  // its reason must not then advise a goto or a slew as the way out.
  for (const re of [/go to a target/i, /target away from the pole/i, /goto/i, /go-to/i, /slew/i]) {
    assert(!re.test(toasts()), `the refusal advises a goto (${re}): ${toasts()}`);
  }
});

await testAsync("NINA: with the position known the same tap is the goto it always was (the control)", async () => {
  seed({}, "nina");
  mount();
  posts.length = 0;
  press("east", 4);
  await settle();
  assert(posts.some((p) => p.path === "/api/mount/goto"),
    `the NINA tap no longer reaches the goto route: ${JSON.stringify(posts)}`);
});

if (root) act(() => { root!.unmount(); });

const total = passed + failed;
console.log(`w16SlewPadPositionUnknown.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
