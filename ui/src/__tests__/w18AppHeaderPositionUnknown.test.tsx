// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w18AppHeaderPositionUnknown.test.tsx - WP-151 / #791: the classic header's
// "ALT n" while the mount does not know where it points.
//
//   Run directly:  npx tsx src/__tests__/w18AppHeaderPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// After a power cycle the AM5 reports its HOME position, the pole, wherever the
// tube is (`status.mount.position_known === false`, #144). At the pole the
// altitude IS the site latitude (#140), so the header's "ALT n" printed the
// latitude of the site as though it were the tube's height, on every classic
// view, for as long as the flag stayed false. This mounts the real `App` and
// flips the flag on a live store:
//   known (flag true, or ABSENT as an engine older than #144 sends it): ALT shows;
//   unknown (flag false): the number is gone and the header says "ALT unknown".
// The viewer shape (no alt at all) is graded by appHeaderDom.test.tsx; the case
// here that touches it is "a viewer never gets an ALT unknown for a flag that is
// true".
//
// Named mutant (header reads the raw altitude again): in App.tsx replace
// `const believed = believedPointing(status?.mount);` with
// `const believed = status?.mount ? { alt: status.mount.alt, az: status.mount.az } : null;`.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

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
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {}
  send() {}
  addEventListener() {}
  removeEventListener() {}
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

g.fetch = async (_url: any, _init?: any) => ({
  ok: false, status: 404, statusText: "Not Found",
  headers: { get: () => "application/json" },
  json: async () => ({}),
  text: async () => "",
});

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../store");
const App = (await import("../App")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;

// ------------------------------------------------------------------- fixture
// 52 is a number that appears nowhere else on the header, so a leak is a plain
// substring test. It stands in for "whatever the mount reports", which at the
// home position is the site latitude.
const ALT = 52;
function seed(mountExtra: Record<string, unknown>): void {
  act(() => {
    useStore.setState({
      authMethods: { methods: [], first_run: false } as any,
      principal: { role: "admin", email: null, caps: ["view.status", "view.preview", "view.site_derived"] } as any,
      wsPhase: "up",
      telemetryStale: false,
      equipConnected: false,
      view: "connect",
      runBanner: null,
      resumeArm: null,
      weather: null,
      sequence: { state: "idle" } as any,
      status: {
        connected: {},
        looping: false,
        mode: "sim",
        mount: {
          ra_hours: 1.5, dec_deg: 30, ra_str: "01:30:00", dec_str: "+30:00:00",
          tracking: true, parked: false, slewing: false,
          alt: ALT, az: 200,
          ...mountExtra,
        },
      } as any,
    } as never);
  });
}
const headerText = (): string => String(container.querySelector(".app-header")?.textContent ?? "");

win.location.hash = "#/classic";
seed({ position_known: true });
const root = createRoot(container);
act(() => { root.render(createElement(App)); });
await settle();

// --------------------------------------------------------------- the control
test("control: a mount that knows where it points shows its altitude", () => {
  assert(container.querySelector(".app-header") != null, "no .app-header - the render never reached the header");
  assert(headerText().includes(`ALT ${ALT}°`),
    `the header must show the altitude when the position is known - got "${headerText()}"`);
  assert(!/ALT unknown/.test(headerText()), "a known position must not read 'ALT unknown'");
});

// ------------------------------------------------------------------ the bug
seed({ position_known: false });
await settle();

test("position_known false: the header prints no altitude", () => {
  assert(!headerText().includes(String(ALT)),
    `the mount's home altitude is printed in the header as the tube's: "${headerText()}"`);
  assert(!/ALT \d/.test(headerText()), `an 'ALT n' span is still rendered - got "${headerText()}"`);
});

test("position_known false: the header says the altitude is unknown instead of vanishing", () => {
  assert(/ALT unknown/.test(headerText()),
    `an altitude that disappears with no word leaves the operator guessing - got "${headerText()}"`);
});

// ------------------------------------------------- recovery and older engines
seed({ position_known: true });
await settle();

test("clearing the flag brings the altitude back", () => {
  assert(headerText().includes(`ALT ${ALT}°`), `the altitude did not return - got "${headerText()}"`);
  assert(!/ALT unknown/.test(headerText()), "'ALT unknown' stuck after the position became known");
});

{
  // An engine older than #144 sends no flag: absent reads as KNOWN.
  const m: any = { ra_hours: 1.5, dec_deg: 30, ra_str: "01:30:00", dec_str: "+30:00:00",
    tracking: true, parked: false, slewing: false, alt: ALT, az: 200 };
  act(() => {
    useStore.setState({ status: { connected: {}, looping: false, mode: "sim", mount: m } as any } as never);
  });
  await settle();
  test("an ABSENT flag (an engine older than #144) reads as known", () => {
    assert(headerText().includes(`ALT ${ALT}°`), `absent flag hid the altitude - got "${headerText()}"`);
  });
}

{
  // A viewer never receives alt/az (api/redact.py), and its flag is true: the
  // header shows neither an ALT number nor 'ALT unknown'.
  act(() => {
    useStore.setState({
      status: { connected: {}, looping: false, mode: "sim", mount: {
        ra_hours: 1.5, dec_deg: 30, ra_str: "01:30:00", dec_str: "+30:00:00",
        tracking: true, parked: false, slewing: false, position_known: true,
      } } as any,
    } as never);
  });
  await settle();
  test("a viewer with a known position (no alt on the wire) sees no ALT span at all", () => {
    assert(!/ALT/.test(headerText()), `got "${headerText()}"`);
  });
}

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w18AppHeaderPositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
