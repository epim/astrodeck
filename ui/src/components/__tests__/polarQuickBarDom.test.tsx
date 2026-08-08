// polarQuickBarDom.test.tsx — the Align screen's sticky status strip, MOUNTED.
//
//   Run directly:  npx tsx src/components/__tests__/polarQuickBarDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The strip exists because of three findings from a real alignment night
// (2026-08-07): no way to tell a 15 s solve from a hang, the error number
// below the fold on a phone, and no control over the solve frame's imaging
// settings. The 20:09 refinement replaced cycling badges with the Capture
// screen's PickerButton pop-outs (a 0.3→120 s change was eleven cycle-taps),
// added the long-exposure tail (narrowband-over-OSC solves at minutes per
// frame) and a custom exposure box. Each test is one finding, at the DOM.

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
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "KeyboardEvent", "PointerEvent",
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
    if (!re.test(text)) throw new Error(msg ?? `no match for ${re} in: ${text.slice(0, 250)}`);
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
  // capAllowed fails CLOSED on a null principal — grant mount control the
  // same way slewPadDom.test.tsx does.
  principal: { role: "operator", email: null,
               caps: ["view.status", "control.mount"] },
} as any);

const root = createRoot(win.document.getElementById("root"));
const render = (polar: any) =>
  act(() => root.render(React.createElement(PolarQuickBar, { polar })));

const text = () => win.document.body.textContent ?? "";
const picker = (label: string): any =>
  Array.from(win.document.querySelectorAll("button[aria-haspopup='listbox']"))
    .find((b: any) => (b.getAttribute("aria-label") ?? "").startsWith(label));
const option = (label: string): any =>
  Array.from(win.document.querySelectorAll("[role='option']"))
    .find((o: any) => (o.textContent ?? "").replace("•", "").trim() === label);

const IDLE = { state: "idle", total_error: 0, az_error: 0, alt_error: 0 };
const LIVE = {
  state: "running", phase: "adjusting",
  total_error: 4.2, az_error: 4.0, alt_error: 1.3,
};

// ----------------------------------------------------------------------------

test("idle shows the pickers to an operator — the FIRST frame is settable too", () => {
  render(IDLE);
  assert.ok(picker("EXP"), "an operator's idle Align screen must offer the pickers");
  assert.match(text(), /idle/i);
});

test("a viewer's idle screen stays clean — no dead controls", () => {
  const prev = useStore.getState().principal;
  useStore.setState({ principal: { role: "viewer", email: null,
                                   caps: ["view.status"] } } as any);
  render(IDLE);
  assert.equal(win.document.getElementById("root").children.length, 0);
  useStore.setState({ principal: prev } as any);
});

test("a running solve names its activity instead of looking hung", () => {
  render({
    state: "running", phase: "measuring", point_index: 0,
    activity: "solving", total_error: 0, az_error: 0, alt_error: 0,
  });
  assert.match(text(), /solving…/);
});

test("the capture chip says capturing — the same word the ring uses", () => {
  render({
    state: "running", phase: "measuring", point_index: 0,
    activity: "exposing", total_error: 0, az_error: 0, alt_error: 0,
  });
  assert.match(text(), /capturing…/);
  assert.ok(!/exposing/.test(text()), "the wire word must not leak to the UI");
});

test("the number AND the az/alt split ride the strip at every width", () => {
  render({
    state: "running", phase: "adjusting",
    total_error: 96.6, az_error: 5.0, alt_error: 96.4,
  });
  assert.match(text(), /1\.6°/, "96.6' must render as degrees");
  render(LIVE);
  assert.match(text(), /4\.2′/, "bolt-sized errors keep the arcminute");
  // 20:09: on a tall phone this strip is the only readout on the first
  // screenful, so the split may not hide behind a breakpoint.
  assert.match(text(), /az 4\.0′/);
  assert.match(text(), /alt 1\.3′/);
});

test("a picker opens a menu and one tap PUTs the chosen preset", () => {
  render({ ...LIVE, solve_settings: {
    exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null } });
  act(() => picker("EXP").click());
  assert.ok(option("2s"), "the exposure menu must list the presets");
  act(() => option("2s").click());
  assert.deepEqual(puts.at(-1), {
    path: "/api/polar/solve-settings", body: { exposure_s: 2 },
  });
  assert.ok(!option("2s"), "single-select closes on choice");
});

test("the long tail exists — narrowband-over-OSC solves at minutes per frame", () => {
  render({ ...LIVE, solve_settings: {
    exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null } });
  act(() => picker("EXP").click());
  for (const label of ["10s", "30s", "1m", "2m", "5m"]) {
    assert.ok(option(label), `preset ${label} missing`);
  }
  act(() => option("5m").click());
  assert.deepEqual(puts.at(-1), {
    path: "/api/polar/solve-settings", body: { exposure_s: 300 },
  });
});

test("the custom exposure box PUTs an arbitrary value and closes the menu", () => {
  render({ ...LIVE, solve_settings: {
    exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null } });
  act(() => picker("EXP").click());
  const input = win.document.querySelector(
    'input[aria-label="Custom exposure in seconds"]');
  assert.ok(input, "no custom exposure box");
  input.value = "45";   // uncontrolled by design: Set reads the box directly
  const setBtn = Array.from(win.document.querySelectorAll("button"))
    .find((b: any) => (b.textContent ?? "").trim() === "Set");
  act(() => (setBtn as any).click());
  assert.deepEqual(puts.at(-1), {
    path: "/api/polar/solve-settings", body: { exposure_s: 45 },
  });
});

test("gain and binning pick from menus too — no cycling", () => {
  render({ ...LIVE, solve_settings: {
    exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null } });
  act(() => picker("GAIN").click());
  act(() => option("400").click());
  assert.deepEqual(puts.at(-1), {
    path: "/api/polar/solve-settings", body: { gain: 400 },
  });
  act(() => picker("BIN").click());
  act(() => option("2×2").click());
  assert.deepEqual(puts.at(-1), {
    path: "/api/polar/solve-settings", body: { binning: 2 },
  });
});

test("the filter menu offers as-is and every real slot — never Dark", () => {
  render({ ...LIVE, solve_settings: {
    exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: "B" } });
  act(() => picker("FILT").click());
  const labels = Array.from(win.document.querySelectorAll("[role='option']"))
    .map((o: any) => (o.textContent ?? "").trim());
  assert.ok(labels.some((l: string) => l === "as-is"), String(labels));
  assert.ok(labels.some((l: string) => l === "L"), String(labels));
  assert.ok(!labels.some((l: string) => l.includes("Dark")),
    "an opaque slot in the picker would offer a dark frame as a solve");
  act(() => option("as-is").click());
  assert.deepEqual(puts.at(-1), {
    path: "/api/polar/solve-settings", body: { filter: null },
  });
});

test("an open menu is nudged back inside the viewport", () => {
  /* 2026-08-07 20:53, on a real S25 Ultra: the right-aligned FILT picker sits
     near the LEFT edge of a phone, so `right-0` hung its 190px panel off the
     left of the window — the operator saw an empty black rectangle, because
     every option's text was outside the screen. PickerButton now measures
     after paint and translates the minimum amount to fit. */
  render({ ...LIVE, solve_settings: {
    exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null } });
  // jsdom reports zero-size rects, so the panel's geometry is stubbed at the
  // PROTOTYPE (React mounts a fresh node on every open, so a per-node stub
  // would measure the previous panel). 120px off the left edge + the 8px
  // margin = a 128px correction.
  const realRect = win.Element.prototype.getBoundingClientRect;
  win.Element.prototype.getBoundingClientRect = function () {
    if (this.getAttribute?.("role") === "listbox") {
      return { left: -120, right: 70, top: 44, bottom: 300,
               width: 190, height: 256, x: -120, y: 44 };
    }
    return realRect.call(this);
  };
  try {
    const filt = picker("FILT");
    assert.ok(filt, "no filter picker");
    act(() => filt.click());
    const panel = win.document.querySelector("[role='listbox']");
    assert.ok(panel, "the menu never opened");
    assert.equal(panel.style.transform, "translateX(128px)",
      "a menu hanging off the left edge must be pushed back inside");
    act(() => filt.click());          // close
  } finally {
    win.Element.prototype.getBoundingClientRect = realRect;
  }
});

test("a finished session keeps the verdict AND the pickers — the next run starts here", () => {
  render({ state: "done", total_error: 0.8, az_error: 0.5, alt_error: 0.6 });
  assert.match(text(), /0\.8′/);
  assert.ok(picker("EXP"),
    "the re-run after a verdict is exactly when the settings get changed");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`polarQuickBarDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
