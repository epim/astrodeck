// polarQuickBarDom.test.tsx — the Align screen's sticky status strip, MOUNTED.
//
//   Run directly:  npx tsx src/components/__tests__/polarQuickBarDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The strip exists because of three findings from a real alignment night
// (2026-08-07): no way to tell a 15 s solve from a hang, the error number
// below the fold on a phone, and no control over the solve frame's imaging
// settings. Each test here is one of those findings, asserted at the DOM.

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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  // Node >=21 defines `navigator` as a getter-only global; defineProperty
  // works for every key (same pattern as slewPadDom.test.tsx).
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.IS_REACT_ACT_ENVIRONMENT = true;   // React 18: makes act() flush updates

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
const assert = {
  ok(cond: unknown, msg?: string) { if (!cond) throw new Error(msg ?? "not ok"); },
  equal(a: unknown, b: unknown, msg?: string) {
    if (a !== b) throw new Error(msg ?? `${String(a)} !== ${String(b)}`);
  },
  match(text: string, re: RegExp, msg?: string) {
    if (!re.test(text)) throw new Error(msg ?? `no match for ${re} in: ${text.slice(0, 200)}`);
  },
  deepEqual(a: unknown, b: unknown, msg?: string) {
    const ja = JSON.stringify(a), jb = JSON.stringify(b);
    if (ja !== jb) throw new Error(msg ?? `${ja} !== ${jb}`);
  },
};

const { api } = await import("../../api");
const { useStore } = await import("../../store");
const { PolarQuickBar } = await import("../PolarQuickBar");

// PUT recorder — the strip's only write path.
const puts: Array<{ path: string; body: unknown }> = [];
(api as any).put = async (path: string, body?: unknown) => {
  puts.push({ path, body });
  return {};
};

// The filter picker reads the wheel from status; give it a real-shaped one
// with an opaque Dark slot the picker must NOT offer.
useStore.setState({
  status: {
    filterwheel: {
      position: 0,
      names: ["L", "R", "G", "B", "Dark"],
      opaque: [false, false, false, false, true],
    },
  } as any,
  // capAllowed fails CLOSED on a null principal, so the settings buttons
  // would be disabled and their clicks silent — grant mount control the same
  // way slewPadDom.test.tsx does.
  principal: { role: "operator", email: null,
               caps: ["view.status", "control.mount"] },
} as any);

const root = createRoot(win.document.getElementById("root"));
const render = (polar: any) =>
  act(() => root.render(React.createElement(PolarQuickBar, { polar })));

const text = () => win.document.body.textContent ?? "";
const buttons = (): any[] =>
  Array.from(win.document.querySelectorAll("button"));

// ----------------------------------------------------------------------------

test("idle renders nothing — the start screen stays clean", () => {
  render({ state: "idle", total_error: 0, az_error: 0, alt_error: 0 });
  assert.equal(win.document.getElementById("root").children.length, 0);
});

test("a running solve names its activity instead of looking hung", () => {
  render({
    state: "running", phase: "measuring", point_index: 0,
    activity: "solving", total_error: 0, az_error: 0, alt_error: 0,
  });
  assert.match(text(), /solving…/);
});

test("the error number is on the strip, in degrees once it stops being a bolt turn", () => {
  render({
    state: "running", phase: "adjusting",
    total_error: 96.6, az_error: 5.0, alt_error: 96.4,
  });
  assert.match(text(), /1\.6°/, "96.6' must render as degrees");

  render({
    state: "running", phase: "adjusting",
    total_error: 4.2, az_error: 4.0, alt_error: 1.3,
  });
  assert.match(text(), /4\.2′/, "bolt-sized errors keep the arcminute");
});

test("the settings fold PUTs the tapped value and offers no opaque filter", () => {
  render({
    state: "running", phase: "adjusting",
    total_error: 4.2, az_error: 4.0, alt_error: 1.3,
    solve_settings: { exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null },
  });
  // open the fold via its summary chip (reads "0.3s · g200 · b1")
  const summary = buttons().find((b) => /0\.3s/.test(b.textContent ?? ""));
  assert.ok(summary, "the settings chip must exist");
  act(() => summary.click());

  const two = buttons().find((b) => (b.textContent ?? "").trim() === "2s");
  assert.ok(two, "exposure presets must be offered");
  act(() => two.click());
  assert.deepEqual(puts.at(-1), {
    path: "/api/polar/solve-settings", body: { exposure_s: 2 },
  });

  const labels = buttons().map((b) => (b.textContent ?? "").trim());
  assert.ok(labels.includes("L"), "real filters offered");
  assert.ok(!labels.includes("Dark"),
    "an opaque slot in the picker would offer a dark frame as a solve");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`polarQuickBarDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
