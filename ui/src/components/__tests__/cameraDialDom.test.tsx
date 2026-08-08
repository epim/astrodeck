// cameraDialDom.test.tsx — the fan-out camera dial, MOUNTED.
//
//   Run directly:  npx tsx src/components/__tests__/cameraDialDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Asked for verbatim (2026-08-08 00:16): "click the icon and have the exposure
// setting categories fan out, and then I can pick the category (exposure,
// gain, binning, offset, etc) which then fans out further to present me
// options for that thing. for offset the only option should be a text entry
// box to set the offset."
//
// So the contract pinned here is TWO RINGS and one exception: categories, then
// that category's values, except offset which is a field.

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
  "Element", "Node", "Event", "KeyboardEvent", "CustomEvent", "MouseEvent",
  "localStorage", "getComputedStyle", "matchMedia",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
// A REAL clock and a real cancel: the two-frame bloom schedules through rAF,
// and a stub that hands the callback a constant 0 leaves transitions that
// never settle (the trap the reticle test documents).
g.requestAnimationFrame = (cb: (t: number) => void) =>
  setTimeout(() => cb(win.performance.now()), 0) as unknown as number;
g.cancelAnimationFrame = (id: number) => clearTimeout(id as unknown as NodeJS.Timeout);
g.IS_REACT_ACT_ENVIRONMENT = true;
// The dial sizes its arc from a ResizeObserver; jsdom has none. Report a stage
// big enough for the full radius so `fits` is true.
g.ResizeObserver = class {
  cb: any;
  constructor(cb: any) { this.cb = cb; }
  observe() { this.cb([{ contentRect: { width: 420, height: 300 } }]); }
  disconnect() {}
};

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
  deepEqual(a: unknown, b: unknown, msg?: string) {
    const ja = JSON.stringify(a), jb = JSON.stringify(b);
    if (ja !== jb) throw new Error(msg ?? `${ja} !== ${jb}`);
  },
};

const CameraDial = (await import("../ui/CameraDial")).default;
const { dialPolar, dialRadius, dialFractions } =
  await import("../ui/CameraDial");
const { cameraDialCategories } = await import("../ui/CameraPickers");

// A FRESH MOUNT PER CASE. The dial owns `open`/`openCat` internally, so
// re-rendering into a shared root carries the previous case's ring state into
// the next one — the first draft's later cases were tapping a disc that was
// already open, which CLOSES it, and then finding nothing to click.
let root: ReturnType<typeof createRoot> | null = null;
const disc = (): any => win.document.querySelector("[data-dial-disc]");
const item = (id: string): any =>
  win.document.querySelector(`[data-dial-item="${id}"]`);
const items = (): string[] =>
  Array.from(win.document.querySelectorAll("[data-dial-item]"))
    .map((b: any) => b.getAttribute("data-dial-item"));

// The calls the dial makes, in order.
const calls: string[] = [];
const cats = cameraDialCategories({
  values: { exposure_s: 2, gain: 120, binning: 1, offset: 30, filter: null },
  filters: ["L", "R"],
  currentFilter: "L",
  onExposure: (s) => calls.push(`exposure=${s}`),
  onGain: (v) => calls.push(`gain=${v}`),
  onBinning: (b) => calls.push(`binning=${b}`),
  onOffset: (o) => calls.push(`offset=${o}`),
  onFilter: (f) => calls.push(`filter=${f}`),
});

const render = () => {
  if (root) act(() => root!.unmount());
  win.document.getElementById("root").innerHTML = "";
  root = createRoot(win.document.getElementById("root"));
  act(() => root!.render(React.createElement(CameraDial, {
    categories: cats, label: "Camera settings", summary: "2s g120",
  })));
};

// --------------------------------------------------------------- pure geometry

test("the arc opens up-and-left, where a corner-pivoting thumb can reach", () => {
  const left = dialPolar(0, 140);
  const up = dialPolar(1, 140);
  assert.equal(left.x, -140, "frac 0 is due left");
  assert.equal(left.y, -0, "…and level with the disc");
  assert.equal(up.x, -0, "frac 1 is due up");
  assert.equal(up.y, -140);
});

test("a stage too small for a usable arc gets no dial at all", () => {
  // 0.39·R is the chord between neighbours; below R=124 two 48px items overlap
  // and a mistap in the dark costs a real setting.
  assert.ok(dialRadius({ w: 200, h: 160 }, { right: 12, bottom: 12 }) < 124);
  assert.equal(dialRadius({ w: 420, h: 300 }, { right: 12, bottom: 12 }), 140);
  assert.equal(dialRadius(null, { right: 12, bottom: 12 }), 140,
    "unmeasured assumes the full arc — the ring is closed on first paint anyway");
});

test("items are spread evenly with one at the top", () => {
  assert.deepEqual(dialFractions(5), [0, 0.25, 0.5, 0.75, 1]);
  assert.deepEqual(dialFractions(1), [1]);
});

// ------------------------------------------------------------ the two rings

test("the closed dial is one disc that names what it controls", () => {
  render();
  assert.ok(disc(), "no disc");
  assert.equal(items().length, 0, "nothing fans out until it is tapped");
  assert.ok(/Camera settings/.test(disc().getAttribute("aria-label")));
  assert.ok(/2s g120/.test(disc().getAttribute("aria-label")),
    "the face states the settings as they stand");
});

test("tapping the disc fans out the CATEGORIES", () => {
  render();
  act(() => disc().click());
  assert.deepEqual(items(), ["exposure", "gain", "binning", "offset", "filter"]);
});

test("tapping a category fans out ITS values, and picking one applies", () => {
  render();
  act(() => disc().click());
  act(() => item("exposure").click());
  const faces = Array.from(win.document.querySelectorAll("[data-dial-item]"))
    .map((b: any) => (b.textContent ?? "").trim());
  assert.ok(faces.includes("5s") && faces.includes("2m"),
    `exposure values missing: ${faces.join(",")}`);
  assert.ok(!items().includes("gain"), "the categories are replaced, not stacked");

  calls.length = 0;
  act(() => item("5").click());
  assert.deepEqual(calls, ["exposure=5"]);
  assert.equal(items().length, 0, "picking a value closes the dial");
});

test("a second ring is not a one-way door", () => {
  render();
  act(() => disc().click());
  act(() => item("gain").click());
  assert.ok(item("__back"), "no way back to the categories");
  act(() => item("__back").click());
  assert.deepEqual(items(), ["exposure", "gain", "binning", "offset", "filter"]);
});

test("OFFSET is a text box, not a ring of guesses", () => {
  render();
  act(() => disc().click());
  act(() => item("offset").click());
  const entry = win.document.querySelector("[data-dial-entry]");
  assert.ok(entry, "offset did not open an entry");
  const input = entry.querySelector("input");
  assert.ok(input, "no field");
  assert.equal(input.value, "30", "the field is seeded with the current offset");
  // …and no value ring beside it: back is the only other control.
  assert.deepEqual(items(), ["__back"]);

  calls.length = 0;
  input.value = "57";
  const set = Array.from(entry.querySelectorAll("button"))
    .find((b: any) => (b.textContent ?? "").trim() === "Set");
  act(() => (set as any).click());
  assert.deepEqual(calls, ["offset=57"]);
});

test("the filter ring names real filters and never an abstraction", () => {
  render();
  act(() => disc().click());
  act(() => item("filter").click());
  const faces = Array.from(win.document.querySelectorAll("[data-dial-item]"))
    .map((b: any) => (b.textContent ?? "").trim());
  assert.ok(faces.includes("L") && faces.includes("R"), faces.join(","));
  assert.ok(!faces.some((f: string) => /as-is/i.test(f)),
    "the ring must not offer an abstraction as a filter");
});

test("tapping the disc again closes everything", () => {
  render();
  act(() => disc().click());
  act(() => item("binning").click());
  act(() => disc().click());
  assert.equal(items().length, 0);
});

// ------------------------------------------------------------------- report
if (root) act(() => root!.unmount());
const total = passed + failed;
console.log(`cameraDialDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
