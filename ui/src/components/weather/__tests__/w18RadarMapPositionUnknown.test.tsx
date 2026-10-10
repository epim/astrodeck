// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w18RadarMapPositionUnknown.test.tsx - WP-151 / #791: the radar map's scope
// overlay while the mount does not know where it points.
//
//   Run directly:  npx tsx src/components/weather/__tests__/w18RadarMapPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// After a power cycle the AM5 reports its HOME position, the pole, wherever the
// tube is, and the server latches `status.mount.position_known` false (#144).
// `RadarMap` drew an azimuth wedge, the cloud-deck pierce points and an
// "Az .. - Alt .." chip from `mount.alt` / `mount.az` without reading that flag,
// so a reset mount drew a confident sight line toward the pole, and the chip
// printed an altitude that, at the pole, is the site latitude (#140).
//
// This mounts the real component with the mount block in three states and
// asserts what is DRAWN, not what the source says:
//   known (flag true, and flag ABSENT as an engine older than #144 sends it):
//     the chip, the wedge and the high-cloud marker are all there - the control,
//     without which "nothing is drawn" proves nothing;
//   unknown (flag false): none of them, and the chip names the cause.
//
// Named mutant (radar map reads the raw angles again): in RadarMap.tsx replace
// `const pointing = believedPointing(mount);` with
// `const pointing = mount ? { alt: mount.alt, az: mount.az } : null;`.

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};
// jsdom lays nothing out, so the map box measures 0 and the overlay is never
// projected - which would make "nothing is drawn" vacuously true.
Object.defineProperty(win.HTMLElement.prototype, "clientWidth", {
  get: () => 512, configurable: true,
});

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const RadarMap = (await import("../RadarMap")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;

/** The scope's reading. alt 45 is high enough that every cloud deck has a pierce
 *  point inside the clamp; az 120 gives the wedge a definite direction. */
function seed(mount: Record<string, unknown> | null): void {
  act(() => {
    useStore.setState({
      // Somewhere in Colorado - deliberately nowhere near the real rig.
      site: { latitude: 40.0, longitude: -105.25, is_default: false, horizon_min_deg: 15 },
      weather: null,
      status: mount === null ? null : { mount } as never,
    } as never);
  });
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(RadarMap)); });
}
const MOUNT = {
  ra_hours: 3.5, dec_deg: 40, ra_str: "03:30:00", dec_str: "+40:00:00",
  tracking: true, parked: false, slewing: false, alt: 45, az: 120,
};

const text = (): string => (container.textContent || "").replace(/\s+/g, " ");
const wedge = () => container.querySelector('svg path[opacity="0.35"]');
const highCloud = () =>
  [...container.querySelectorAll("svg text")].find((t: any) => /high cloud/.test(t.textContent || "")) ?? null;
const chip = () =>
  [...container.querySelectorAll("span.mono")]
    .find((n: any) => /Az \d+|no mount|position unknown/.test(n.textContent || "")) ?? null;

// --------------------------------------------------------------- the control
test("control: a mount that knows where it points draws the chip, the wedge and the high-cloud marker", () => {
  seed({ ...MOUNT, position_known: true });
  assert((chip()?.textContent || "") === "Az 120° · Alt 45°",
    `the chip must carry the reading when the position is known - got ${JSON.stringify(chip()?.textContent)}`);
  assert(wedge() !== null, "no azimuth wedge for a known position, so the fixture draws nothing and the tests below prove nothing");
  assert(highCloud() !== null, "no high-cloud pierce marker for a known position at alt 45");
});

test("control: an ABSENT flag (an engine older than #144) reads as known", () => {
  seed({ ...MOUNT });
  assert((chip()?.textContent || "") === "Az 120° · Alt 45°",
    `only an explicit false may withhold the reading - got ${JSON.stringify(chip()?.textContent)}`);
  assert(wedge() !== null && highCloud() !== null, "an absent flag must not hide the overlay");
});

// ------------------------------------------------------------------ the bug
test("position_known false: the Az/Alt chip prints no angle and names the cause", () => {
  seed({ ...MOUNT, position_known: false });
  const c = chip()?.textContent || "";
  assert(c === "position unknown",
    `the home reading is the pole, not the tube - the chip must say the position is unknown, got ${JSON.stringify(c)}`);
  assert(!/\b45\b/.test(text()) && !/Alt \d/.test(text()),
    "the believed altitude is still printed somewhere on the panel");
});

test("position_known false: no azimuth wedge toward the home azimuth", () => {
  seed({ ...MOUNT, position_known: false });
  assert(wedge() === null, "an azimuth wedge is drawn from the mount's home reading");
});

test("position_known false: no cloud-deck pierce points or sight line", () => {
  seed({ ...MOUNT, position_known: false });
  assert(highCloud() === null, "a high-cloud pierce marker is drawn from the mount's home reading");
  assert(container.querySelector('svg line[stroke-dasharray="4 3"]') === null,
    "the dashed sight-line ray is drawn from the mount's home reading");
});

test("no mount block at all still says 'no mount' (the unknown wording is not a catch-all)", () => {
  seed(null);
  assert((chip()?.textContent || "") === "no mount", `got ${JSON.stringify(chip()?.textContent)}`);
});

// ------------------------------------------------------------------- report
act(() => { rootRef!.unmount(); });
const total = passed + failed;
console.log(`w18RadarMapPositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
