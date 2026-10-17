// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// WP-229 / #1043: the classic MountView Altitude and Azimuth stats read the
// horizontal reading through `believedPointing` (#791), the one gate, and not
// through `m.alt` / `m.az` behind a hand-written `positionIsKnown` arm.
//
//   Run directly:  npx tsx src/views/__tests__/w24MountViewBelievedAlt.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `typeof m.alt === "number"` is true for NaN and Infinity, so the
// stat printed `NaN°`; an operator whose frame carried no usable angle read
// "hidden", the word for "you may not see this"; and the tone read raw `m.alt`.
// The `positionIsKnown` arm was the only thing keeping the pole's altitude (the
// site latitude, #140) off the page.
//
// WHAT EACH CASE HOLDS THE VIEW TO (the mount sheet's three cases, #1023):
//   1. control: a known position prints both angles.
//   2. an angle that is not a finite number (NaN, Infinity, -Infinity, null,
//      absent) prints no number, for a holder of view.site_derived: "not
//      reported". The tone does not read the raw value either.
//   3. a principal without view.site_derived reads "hidden".
//   4. an unknown position reads "unknown" and prints no angle, whatever the
//      home reading is.
//
// Named mutants (each an edit to MountView.tsx, run from a byte backup and
// restored byte-identically):
//   w24m1_raw_alt -- the defect restored: the Altitude stat prints
//     `typeof m.alt === "number" ? m.alt + "deg" : "hidden"`. Fails case 2.

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

g.fetch = async (url: string) => {
  const json = String(url).includes("/api/catalog") ? [] : { started: "ok" };
  return { ok: true, status: 200, statusText: "OK", json: async () => json } as any;
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const MountView = (await import("../MountView")).default;

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

// Made-up numbers. 71 / 233 is the "home" reading in this fixture; it is nobody's
// site, and seeing it while the position is unknown is the leak.
const KNOWN_ALT = 45;
const KNOWN_AZ = 120;
const HOME_AZ = 233;

const DERIVED = {
  role: "operator", email: null,
  caps: ["view.status", "view.site_derived", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status"] };

function seed(
  mountOver: Record<string, unknown> = {}, drop: string[] = [], principal: unknown = DERIVED,
): void {
  const m: Record<string, unknown> = {
    ra_hours: 5.6, dec_deg: -5.4, ra_str: "05h35m", dec_str: "-05d23m",
    alt: KNOWN_ALT, az: KNOWN_AZ, tracking: true, parked: false,
    slewing: false, can_find_home: true, can_set_tracking_rate: true,
    tracking_rate: "sidereal", max_rate_deg_s: 1.44,
    ...mountOver,
  };
  for (const k of drop) delete m[k];
  act(() => {
    useStore.setState({
      status: { connected: {}, looping: false, mode: "sim", busy_lanes: [], mount: m },
      principal, authGate: "open", toasts: [], confirm: null,
    } as never);
  });
}

seed();
const container = win.document.getElementById("root") as any;
let root: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (root) act(() => { root!.unmount(); });
  root = createRoot(container);
  act(() => { root!.render(createElement(MountView)); });
}
mount();
await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

const text = () => (container.textContent || "") as string;
const labelNode = (label: string): any =>
  [...container.querySelectorAll("*")].filter(
    (n: any) => n.children.length === 0 && (n.textContent || "").trim() === label)[0];
const statValue = (label: string): string => {
  const el = labelNode(label);
  return el ? ((el.parentElement?.textContent || "").replace(label, "").trim()) : "";
};

test("control: a known position prints both angles", () => {
  eq(statValue("Altitude"), `${KNOWN_ALT}°`, "the Altitude stat");
  eq(statValue("Azimuth"), `${KNOWN_AZ}°`, "the Azimuth stat");
});

const UNREADABLE: { name: string; over: Record<string, unknown>; drop: string[] }[] = [
  { name: "NaN", over: { alt: Number.NaN }, drop: [] },
  { name: "Infinity", over: { alt: Number.POSITIVE_INFINITY }, drop: [] },
  { name: "-Infinity", over: { alt: Number.NEGATIVE_INFINITY }, drop: [] },
  { name: "null", over: { alt: null }, drop: [] },
  { name: "absent", over: {}, drop: ["alt"] },
];
for (const u of UNREADABLE) {
  test(`a ${u.name} altitude with the position known and access held reads not reported`, () => {
    seed(u.over, u.drop);
    mount();
    eq(statValue("Altitude"), "not reported", "the Altitude stat");
    assert(!/\b(Infinity|NaN|null|undefined)\b/.test(text()), `the view prints the reading: ${text()}`);
  });
}

test("an unreadable azimuth reads not reported on both stats (one pointing, one gate)", () => {
  seed({ az: Number.POSITIVE_INFINITY });
  mount();
  eq(statValue("Azimuth"), "not reported", "the Azimuth stat");
  eq(statValue("Altitude"), "not reported", "the Altitude stat");
  assert(!/Infinity/.test(text()), `the view prints the reading: ${text()}`);
});

test("a -Infinity altitude warns about nothing: the tone does not read the raw value", () => {
  seed({ alt: Number.NEGATIVE_INFINITY });
  mount();
  const cell = labelNode("Altitude")?.parentElement;
  assert(cell != null && !/text-warn/.test(cell.innerHTML),
    `the Altitude stat is warn-toned on a value nobody sees: ${cell?.innerHTML}`);
});

test("a principal without view.site_derived reads hidden", () => {
  seed({}, ["alt", "az"], VIEWER);
  mount();
  eq(statValue("Altitude"), "hidden", "the Altitude stat");
  eq(statValue("Azimuth"), "hidden", "the Azimuth stat");
});

for (const alt of [71, 3]) {
  test(`position_known false, home reading ${alt}: unknown, and no angle on the page`, () => {
    seed({ alt, az: HOME_AZ, position_known: false });
    mount();
    eq(statValue("Altitude"), "unknown", "the Altitude stat");
    eq(statValue("Azimuth"), "unknown", "the Azimuth stat");
    assert(!new RegExp(String.raw`(?<![\d.])(${alt}|${HOME_AZ})\s*°`).test(text()),
      `the home reading is on the page: ${text()}`);
  });
}

if (root) act(() => { root!.unmount(); });

const total = passed + failed;
console.log(`w24MountViewBelievedAlt.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
