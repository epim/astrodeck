// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w5PolarEasedScaleFinite.test.tsx - #269: PolarReticle must never hand a
// ring or a tier-zone circle a non-finite strokeOpacity (or radius), even
// when the animation clock disagrees with itself about where "now" is on the
// very first frame of a rung change.
//
//   Run directly: npx tsx src/components/__tests__/w5PolarEasedScaleFinite.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Issue #269. Observed as a React warning ("Received NaN for the
// `strokeOpacity` attribute") during the polar cases of
// ledBesideItsWord.test.tsx, intermittently - every run made just after that
// test file was edited, and in a T6 mutant's output, never on a plain
// rerun. The issue's own diagnosis: jsdom's requestAnimationFrame clock
// disagrees with Node's performance.now(), so useEasedScale's first tick can
// compute an elapsed time that is negative or not a number at all.
//
// This drives that disagreement DIRECTLY and deterministically rather than
// hoping to reproduce the timing race: a requestAnimationFrame stub that
// QUEUES callbacks instead of firing them, so the test controls exactly what
// timestamp the reticle's tween receives on its first frame - the one frame
// #269 names.
//
// Fix shape, plan's WP-43 (c): "Guard against from <= 0 and non-finite
// results," inside useEasedScale (polar.tsx).
//
// Named mutant "drop the eased-value guard" (polar.tsx, useEasedScale):
//   const v = Number.isFinite(v0) && v0 > 0 ? v0 : target;
//   -> const v = v0;
// Observed red, both cases together:
//   x a backwards-stepping clock does not leave the ring set holding a NaN
//   strokeOpacity or radius: NaN first frame: circle (r="NaN" stroke-opacity="1"
//   fill="null"): r is not finite
//   deeply-negative first frame: circle (r="NaN" stroke-opacity="0.05"
//   fill="var(--warn)"): r is not finite

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

// NOT reduced motion: the tween under test only runs when animation is on.
// polarReticleDom.test.tsx's sibling file forces reduced motion ON instead,
// precisely because it is testing geometry, not this tween.
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
]) {
  const v = k === "window" ? win : win[k];
  // Node >=21 defines `navigator` as a getter-only global; defineProperty
  // works for every key (same pattern as the rest of this directory).
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

// THE CONTROLLED CLOCK. requestAnimationFrame QUEUES its callback instead of
// firing it, so the test can hand that callback whatever timestamp it likes -
// in particular, one that disagrees with performance.now() about where "now"
// is, which is the exact disagreement #269 attributes the warning to.
let queue: Array<(t: number) => void> = [];
g.requestAnimationFrame = (cb: (t: number) => void) => { queue.push(cb); return queue.length; };
g.cancelAnimationFrame = () => {};
g.IS_REACT_ACT_ENVIRONMENT = true; // React 18: makes act() flush updates

// ------------------------------------------------------------------- imports
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";

// House harness (run-tests.mjs scores the printed tally / exported result):
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const { PolarReticle } = await import("../polar");

/** Fire every queued frame with the same `now`, as one React update - a
 *  shared bad clock reaching every pending tween at once, same as a real
 *  disagreeing clock would. */
function fireQueue(now: number): void {
  const due = queue;
  queue = [];
  act(() => { for (const cb of due) cb(now); });
}

/** Every numeric attribute the ring/tier drawing can leave non-finite: the
 *  `r` and `stroke-opacity` of every <circle> that sets a stroke-opacity at
 *  all. The dot and the pole target never set strokeOpacity, so this is
 *  exactly the set useEasedScale's `smax` feeds (via `k`, `tierEdge` and
 *  `prog`) - not the dot's position, which is a different hook
 *  (useEasedPoint) and a different, unrelated concern. */
function badRingAttrs(root: ParentNode): { bad: string[]; checked: number } {
  const bad: string[] = [];
  let checked = 0;
  for (const el of Array.from(root.querySelectorAll("circle"))) {
    const so = el.getAttribute("stroke-opacity");
    if (so == null) continue; // the dot and the pole target: not under test
    checked++;
    const r = el.getAttribute("r");
    const tag = `r=${JSON.stringify(r)} stroke-opacity=${JSON.stringify(so)} fill=${JSON.stringify(el.getAttribute("fill"))}`;
    if (r == null || !Number.isFinite(Number(r))) bad.push(`circle (${tag}): r is not finite`);
    if (!Number.isFinite(Number(so))) bad.push(`circle (${tag}): stroke-opacity is not finite`);
  }
  return { bad, checked };
}

/** Mount small (rung settles at 1'), re-render at a ~70' error (a deliberate
 *  zoom OUT big enough that boundaryArcmin steps out at once - see its own
 *  "error outgrew the ring" branch), fire the FIRST queued frame with
 *  `badNow`, and report every non-finite ring/tier attribute found right
 *  after. */
function runWithBadFirstFrame(badNow: number): { bad: string[]; checked: number } {
  queue = [];
  const box = win.document.createElement("div");
  win.document.body.appendChild(box);
  const root = createRoot(box);
  act(() => { root.render(React.createElement(PolarReticle, { az: 0, alt: 0, active: true })); });
  // Discard whatever the first mount queued. useEasedPoint always schedules
  // once on mount (it has no `from === to` early return); useEasedScale does
  // not, because `from === target` trivially on a first render (nothing to
  // tween FROM yet - see its own early `if (from === target) return`). Only
  // the SECOND render's frames - the ones a genuine rung CHANGE queues - are
  // what this test is about.
  queue = [];
  act(() => { root.render(React.createElement(PolarReticle, { az: 50, alt: 50, active: true })); });
  assert(queue.length > 0,
    "precondition: moving from a ~0' to a ~70' error did not queue an animation frame - " +
    "the rung did not change, so the first-frame clock below is never exercised");
  fireQueue(badNow);
  const result = badRingAttrs(box);
  act(() => { root.unmount(); });
  box.remove();
  return result;
}

test("a backwards-stepping clock does not leave the ring set holding a NaN strokeOpacity or radius", () => {
  // Case 1: #269's literal description - no usable timestamp at all. jsdom
  // can hand a callback something that is not a real number in some code
  // paths; `NaN` reproduces exactly what that does to `now - t0`.
  const nan = runWithBadFirstFrame(NaN);
  assert(nan.checked > 0,
    "anti-vacuity guard: no ring/tier circle was found to check - a pass here would prove nothing");
  assert(nan.bad.length === 0, `NaN first frame: ${nan.bad.join("; ")}`);

  // Case 2: a clock reading far BEHIND performance.now() rather than
  // undefined - finite, but still makes `now - t0` deeply negative. This is
  // the harder case for a guard that only checks Number.isFinite: the eased
  // value does not come out NaN here, it UNDERFLOWS to exactly 0 (100 raised
  // to a very large negative exponent rounds to 0 in double precision), and
  // 0 is a perfectly finite number that still breaks `k = R / smax` the
  // moment it is used as a scale. Only a guard that also checks the result
  // is POSITIVE catches this one.
  const deeplyNegative = runWithBadFirstFrame(-1e12);
  assert(deeplyNegative.checked > 0,
    "anti-vacuity guard: no ring/tier circle was found to check - a pass here would prove nothing");
  assert(deeplyNegative.bad.length === 0, `deeply-negative first frame: ${deeplyNegative.bad.join("; ")}`);
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`w5PolarEasedScaleFinite.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
