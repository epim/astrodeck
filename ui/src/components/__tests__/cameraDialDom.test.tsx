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
// React's ChangeEventPlugin falls back to an IE8 polyfill (`attachEvent`) unless
// it can see `oninput` on the document, and jsdom does not expose it. Opening
// OFFSET now puts focus in its field, so every later event reached for a method
// that has not existed since 2011 and printed a stack trace over a passing run.
// This is a faithful shim, not a workaround — every real browser has it.
if (!("oninput" in win.document)) win.document.oninput = null;
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
const { ARC_MAX_OPTIONS } = await import("../../lib/ringDial");

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
const mkCats = (o: { exposures?: number[]; filters?: string[] } = {}) =>
  cameraDialCategories({
    values: { exposure_s: 2, gain: 120, binning: 1, offset: 30, filter: null },
    exposures: o.exposures,
    filters: o.filters ?? ["L", "R"],
    currentFilter: "L",
    onExposure: (s) => calls.push(`exposure=${s}`),
    onGain: (v) => calls.push(`gain=${v}`),
    onBinning: (b) => calls.push(`binning=${b}`),
    onOffset: (o2) => calls.push(`offset=${o2}`),
    onFilter: (f) => calls.push(`filter=${f}`),
  });
const cats = mkCats();

const render = (c: any = cats) => {
  if (root) act(() => root!.unmount());
  win.document.getElementById("root").innerHTML = "";
  root = createRoot(win.document.getElementById("root"));
  act(() => root!.render(React.createElement(CameraDial, {
    categories: c, label: "Camera settings", summary: "2s g120",
  })));
};

const ring = (): any => win.document.querySelector("[data-ring-picker]");
const seats = (): string[] =>
  Array.from(win.document.querySelectorAll("[data-dial-slot]"))
    .map((d: any) => d.getAttribute("data-dial-seat"));
const key = (el: any, k: string) =>
  act(() => { el.dispatchEvent(new win.KeyboardEvent("keydown",
    { key: k, bubbles: true, cancelable: true })); });
const focused = (): any => win.document.activeElement;

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

// ═══════════════════════════════════ the ring, and the seat Back used to steal
//
// Reported 2026-08-08: "the exposures are too densely populated. I can't
// actually read any of them", and separately that FILT read "R G B S H O" with
// no L. Both are geometry. The arithmetic is pinned in lib/__tests__/
// ringDial.test.ts (chords, seats, reach); what is pinned HERE is that the
// component actually routes to the layout that arithmetic chose, and that the
// first option of a second ring exists and has a seat nobody else is standing on.

test("BACK HAS ITS OWN SEAT — it used to sit on the first option and hide it", () => {
  // The exact reported shape: a filter ring whose first entry is L. Four
  // filters plus Back is the arc's five seats, so this is the INLINE path —
  // the one that was broken, and the one the overlay does not cover.
  render(mkCats({ filters: ["L", "R", "G", "B"] }));
  act(() => disc().click());
  act(() => item("filter").click());
  assert.ok(item("L"), "L is missing from the filter ring");
  assert.deepEqual(items(), ["L", "R", "G", "B", "__back"]);

  const s = seats();
  assert.equal(s.length, 5, `expected 5 seats, got ${s.join("|")}`);
  assert.equal(new Set(s).size, s.length,
    `two chips share a seat: ${s.join("|")} — this is exactly the bug that ate L`);
  // …and the one that was collided with, named: Back keeps frac 0, the option
  // that used to be under it does not.
  assert.equal(
    win.document.querySelector('[data-dial-item="__back"]')
      .closest("[data-dial-slot]").getAttribute("data-dial-seat"), "0");
  assert.ok(
    item("L").closest("[data-dial-slot]").getAttribute("data-dial-seat") !== "0",
    "the first option is still standing on Back's seat");
});

test("every category's FIRST option is reachable, not just the filters", () => {
  // The collision ate index 0 of whatever the second ring held, so the check is
  // over every category that has one — the shortest exposure, the lowest gain,
  // bin 1, the first filter.
  const first: Record<string, string> = {
    exposure: "0.3", gain: "0", binning: "1", filter: "L",
  };
  render(mkCats({ filters: ["L", "R", "G", "B"] }));
  for (const [cat, id] of Object.entries(first)) {
    render(mkCats({ filters: ["L", "R", "G", "B"] }));
    act(() => disc().click());
    act(() => item(cat).click());
    assert.ok(item(id), `${cat}: first option "${id}" is not in the tree`);
    const box = item(id).closest("[data-dial-slot]");
    if (box) {
      assert.ok(box.getAttribute("data-dial-seat") !== "0",
        `${cat}: first option is on Back's seat`);
    }
  }
});

test("four options stay on the arc; five open the ring", () => {
  // The threshold, at the component. Four is the arc's capacity once Back has
  // taken a seat (ARC_MAX_OPTIONS), so it is the last count that stays put.
  assert.equal(ARC_MAX_OPTIONS, 4);

  render(mkCats({ exposures: [1, 2, 5, 10] }));
  act(() => disc().click());
  act(() => item("exposure").click());
  assert.ok(!ring(), "four options must NOT pop a dialog — the arc holds them");
  assert.deepEqual(items(), ["1", "2", "5", "10", "__back"]);

  render(mkCats({ exposures: [1, 2, 5, 10, 15] }));
  act(() => disc().click());
  act(() => item("exposure").click());
  assert.ok(ring(), "five options must open the ring — the arc cannot seat them");
});

test("thirteen exposures open a ring with the icon in the middle", () => {
  render();
  act(() => disc().click());
  act(() => item("exposure").click());
  assert.ok(ring(), "the reported case must not stay on the arc");
  assert.equal(ring().querySelector("[data-ring-fit]").getAttribute("data-ring-fit"),
    "ring", "a stage this size must get the ring, not the small-screen fallback");

  const hub = win.document.querySelector("[data-ring-hub]");
  assert.ok(hub, "no hub — the operator asked for the icon in the middle");
  assert.ok(/EXP/.test(hub.textContent), "the hub must name the category");
  assert.ok(/2s/.test(hub.textContent),
    "and carry the current value, so it is readable without hunting the ring");
  assert.ok(/back/i.test(hub.getAttribute("aria-label")),
    "the hub is the way back and must say so");
  assert.equal(hub.getAttribute("data-dial-item"), "__back");

  // Every value present, first and last included, each saying what it SETS.
  const ids = items();
  assert.ok(ids.includes("0.3"), `shortest exposure missing: ${ids.join(",")}`);
  assert.ok(ids.includes("300"), `longest exposure missing: ${ids.join(",")}`);
  assert.equal(ids.length, 14, "13 exposures + the hub");
  assert.equal(item("30").getAttribute("aria-label"), "EXP 30s",
    "an item must name what it sets, not just its own face");

  // The current value is the one the ring is anchored on, and it is marked.
  assert.ok(/text-accent/.test(item("2").getAttribute("class")),
    "the value in force must be findable without reading every label");
});

test("gain goes to the ring too — ten is over the line", () => {
  render();
  act(() => disc().click());
  act(() => item("gain").click());
  assert.ok(ring(), "ten gains overlap on the arc");
  assert.equal(items().length, 11, "10 gains + the hub");
  calls.length = 0;
  act(() => item("400").click());
  assert.deepEqual(calls, ["gain=400"]);
  assert.ok(!ring(), "picking a value dismisses the dialog");
});

test("the ring is not a one-way door, and Back lands you where you were", () => {
  render();
  act(() => disc().click());
  act(() => item("exposure").click());
  assert.ok(ring());
  act(() => win.document.querySelector("[data-ring-hub]").click());
  assert.ok(!ring(), "the hub must close the ring");
  assert.deepEqual(items(), ["exposure", "gain", "binning", "offset", "filter"]);
  assert.equal(focused(), item("exposure"),
    "focus must return to the chip you opened, not to <body>");
});

test("a screen too small for a legible ring DEGRADES, it does not vanish", () => {
  // A ring of 13 needs a 298px square (lib/ringDial.ringMinViewport). Below
  // that it must not draw a tighter ring — a tighter ring IS the bug — so the
  // same chips come back as a list. Every value must still be there and still
  // say what it sets.
  const w = win.innerWidth, h = win.innerHeight;
  Object.defineProperty(win, "innerWidth", { value: 280, configurable: true });
  Object.defineProperty(win, "innerHeight", { value: 260, configurable: true });
  try {
    render();
    act(() => disc().click());
    act(() => item("exposure").click());
    assert.ok(ring(), "the dialog must still open");
    assert.equal(ring().querySelector("[data-ring-fit]").getAttribute("data-ring-fit"),
      "list", "a 280px screen cannot hold a 298px ring and must say so");
    assert.equal(items().length, 14, "no value may be dropped to make it fit");
    assert.equal(item("300").getAttribute("aria-label"), "EXP 5m");
    assert.ok(win.document.querySelector("[data-ring-hub]"), "still a way back");
  } finally {
    Object.defineProperty(win, "innerWidth", { value: w, configurable: true });
    Object.defineProperty(win, "innerHeight", { value: h, configurable: true });
  }
});

// ------------------------------------------------------------ the keyboard

test("arrows walk the ring, and the roving tabindex follows", () => {
  render();
  act(() => disc().click());
  act(() => item("exposure").click());
  // Focus opens on the CURRENT value (2s), not on the first chip.
  assert.equal(focused(), item("2"), "focus must start on the value in force");
  assert.equal(item("2").getAttribute("tabindex"), "0");
  assert.equal(item("0.3").getAttribute("tabindex"), "-1",
    "only one chip may be in the tab order");

  key(focused(), "ArrowRight");
  assert.equal(focused(), item("5"), "ArrowRight goes one stop LONGER");
  key(focused(), "ArrowLeft");
  key(focused(), "ArrowLeft");
  assert.equal(focused(), item("1"), "ArrowLeft goes one stop shorter");
  key(focused(), "Home");
  assert.equal(focused(), item("0.3"));
  key(focused(), "ArrowLeft");
  assert.equal(focused(), item("300"), "a circle has no first item — it wraps");
  key(focused(), "ArrowRight");
  assert.equal(focused(), item("0.3"), "…and no last item either");
  key(focused(), "End");
  assert.equal(focused(), item("300"));
});

test("Tab stays inside the ring — the page behind it is not reachable", () => {
  render();
  act(() => disc().click());
  act(() => item("exposure").click());
  const hub = win.document.querySelector("[data-ring-hub]");
  assert.equal(focused(), item("2"));
  key(focused(), "Tab");
  assert.equal(focused(), hub, "Tab must reach the way out, not the page behind");
  key(focused(), "Tab");
  assert.equal(focused(), item("2"),
    "…and come back, rather than walking onto a page under a full-screen scrim");
});

test("Escape backs out one ring at a time and hands the disc back", () => {
  render();
  act(() => disc().click());
  act(() => item("exposure").click());
  assert.ok(ring());

  key(win, "Escape");
  assert.ok(!ring(), "Escape must leave the ring");
  assert.deepEqual(items(), ["exposure", "gain", "binning", "offset", "filter"]);

  key(win, "Escape");
  assert.equal(items().length, 0, "a second Escape closes the dial");
  assert.equal(focused(), disc(),
    "focus must land on the disc or the next Tab restarts at the top of the page");
});

test("dismissing the ring by tapping outside also restores focus", () => {
  render();
  act(() => disc().click());
  act(() => item("gain").click());
  const scrim = ring().querySelector("[aria-hidden]");
  act(() => scrim.dispatchEvent(new win.MouseEvent("pointerdown", { bubbles: true })));
  assert.equal(items().length, 0, "tapping off the ring closes the whole dial");
  assert.equal(focused(), disc());
});

test("the ring is a menu of menuitems, and the scrim is not a control", () => {
  render();
  act(() => disc().click());
  act(() => item("exposure").click());
  const menu = ring().querySelector('[role="menu"]');
  assert.ok(menu, "no menu role");
  assert.ok(/EXP/.test(menu.getAttribute("aria-label")),
    `the menu must name the category: ${menu.getAttribute("aria-label")}`);
  const buttons = Array.from(ring().querySelectorAll("button"));
  assert.ok(buttons.length > 0);
  assert.ok(buttons.every((b: any) => b.getAttribute("role") === "menuitem"),
    "every control in the ring must be a menuitem");
  assert.ok(buttons.every((b: any) => (b.getAttribute("aria-label") ?? "").length > 0),
    "an unnamed control in the dark is an unnamed control");
});

// ------------------------------------------------------------------- report
if (root) act(() => root!.unmount());
const total = passed + failed;
console.log(`cameraDialDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
