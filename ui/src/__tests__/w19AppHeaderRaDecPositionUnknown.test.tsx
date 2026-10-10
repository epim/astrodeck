// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w19AppHeaderRaDecPositionUnknown.test.tsx - WP-160 / #913: the classic
// header's RA/Dec summary while the mount does not know where it points.
//
//   Run directly:  npx tsx src/__tests__/w19AppHeaderRaDecPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// #791 gated the header's "ALT n" and left the RA/Dec text beside it. After a
// power cycle the AM5 reports its HOME position, the pole, wherever the tube is
// (`status.mount.position_known === false`, #144), and the RA there follows the
// site's sidereal clock, so the header printed a precise position that was not
// the tube's on every classic view for as long as the flag stayed false. This
// mounts the real `App` and flips the flag on a live store:
//   known (flag true, or ABSENT as an engine older than #144 sends it): RA/Dec shows;
//   unknown (flag false): the text is gone and the header says "RA/Dec unknown".
//
// Named mutant (header prints the raw reading again): in App.tsx replace
// `{believedEq ? (` with `{status.mount.ra_str ? (` and the span's
// `{believedEq.ra_str} {believedEq.dec_str}` with
// `{status.mount.ra_str} {status.mount.dec_str}`.

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
// The strings are distinctive so a leak is a plain substring test. They stand
// in for "whatever the mount reports", which at the home position is the pole.
const RA = "01:30:00";
const DEC = "+30:00:00";
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
          ra_hours: 1.5, dec_deg: 30, ra_str: RA, dec_str: DEC,
          tracking: true, parked: false, slewing: false,
          alt: 52, az: 200,
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
test("control: a mount that knows where it points shows its RA and Dec", () => {
  assert(container.querySelector(".app-header") != null, "no .app-header - the render never reached the header");
  assert(headerText().includes(`${RA} ${DEC}`),
    `the header must show the RA/Dec when the position is known - got "${headerText()}"`);
  assert(!/RA\/Dec unknown/.test(headerText()), "a known position must not read 'RA/Dec unknown'");
});

// ------------------------------------------------------------------ the bug
seed({ position_known: false });
await settle();

test("position_known false: the header prints no RA or Dec", () => {
  assert(!headerText().includes(RA) && !headerText().includes(DEC),
    `the mount's home reading is printed in the header as the tube's: "${headerText()}"`);
});

test("position_known false: the header says the RA/Dec is unknown instead of vanishing", () => {
  assert(/RA\/Dec unknown/.test(headerText()),
    `a position that disappears with no word leaves the operator guessing - got "${headerText()}"`);
});

// ------------------------------------------------- recovery and older engines
seed({ position_known: true });
await settle();

test("clearing the flag brings the RA and Dec back", () => {
  assert(headerText().includes(`${RA} ${DEC}`), `the RA/Dec did not return - got "${headerText()}"`);
  assert(!/RA\/Dec unknown/.test(headerText()), "'RA/Dec unknown' stuck after the position became known");
});

{
  // An engine older than #144 sends no flag: absent reads as KNOWN.
  const m: any = { ra_hours: 1.5, dec_deg: 30, ra_str: RA, dec_str: DEC,
    tracking: true, parked: false, slewing: false, alt: 52, az: 200 };
  act(() => {
    useStore.setState({ status: { connected: {}, looping: false, mode: "sim", mount: m } as any } as never);
  });
  await settle();
  test("an ABSENT flag (an engine older than #144) reads as known", () => {
    assert(headerText().includes(`${RA} ${DEC}`), `absent flag hid the RA/Dec - got "${headerText()}"`);
  });
}

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w19AppHeaderRaDecPositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
