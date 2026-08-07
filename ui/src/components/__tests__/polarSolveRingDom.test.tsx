// polarSolveRingDom.test.tsx — the reticle's capture/solve progress ring.
//
//   Run directly:  npx tsx src/components/__tests__/polarSolveRingDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Operator feedback 2026-08-07 12:50: "an indicator telling us where we are in
// the latest plate solve … so the user understands that something is
// happening, and that's why the alignment errors haven't shown up yet."
// The contract pinned here: capturing draws a ring whose FILL DURATION is the
// frame's own exposure; solving orbits indeterminately; and the word beneath
// is the state, so a reduced-motion user who never sees the animation still
// gets the fact.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
const assert = {
  ok(cond: unknown, msg?: string) { if (!cond) throw new Error(msg ?? "not ok"); },
  equal(a: unknown, b: unknown, msg?: string) {
    if (a !== b) throw new Error(msg ?? `${String(a)} !== ${String(b)}`);
  },
};

const PolarSolveRing = (await import("../PolarSolveRing")).default;

const root = createRoot(win.document.getElementById("root"));
const render = (props: any) =>
  act(() => root.render(React.createElement(PolarSolveRing, props)));
const q = (sel: string): any => win.document.querySelector(sel);

// ----------------------------------------------------------------------------

test("no activity renders nothing — the idle reticle stays clean", () => {
  render({ activity: null, exposureS: 0.3 });
  assert.equal(win.document.getElementById("root").children.length, 0);
});

test("capturing fills the ring on the exposure's own clock", () => {
  render({ activity: "exposing", exposureS: 2 });
  const wrap = q('[data-solve-ring="capturing"]');
  assert.ok(wrap, "no capturing ring rendered");
  assert.ok(/capturing/.test(wrap.textContent), "the word is the state");
  const fill = wrap.querySelector(".solve-ring-fill");
  assert.ok(fill, "no fill circle — the ring would never move");
  assert.equal(fill.style.animationDuration, "2s",
    "the fill must run exactly as long as the shutter is open");
});

test("solving orbits — indeterminate, because ASTAP's runtime is unknowable", () => {
  render({ activity: "solving", exposureS: 2 });
  const wrap = q('[data-solve-ring="solving"]');
  assert.ok(wrap, "no solving ring rendered");
  assert.ok(/solving/.test(wrap.textContent), "the word is the state");
  assert.ok(wrap.querySelector("svg.solve-ring-spin"), "no orbit class");
  assert.ok(!wrap.querySelector(".solve-ring-fill"),
    "a filling ring here would claim a progress nobody measured");
});

test("the ring never eats a tap meant for the reticle", () => {
  render({ activity: "solving", exposureS: 2 });
  const wrap = q("[data-solve-ring]");
  assert.ok(wrap.className.includes("pointer-events-none"), wrap.className);
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`polarSolveRingDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
