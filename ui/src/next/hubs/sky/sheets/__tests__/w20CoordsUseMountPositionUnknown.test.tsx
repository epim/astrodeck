// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w20CoordsUseMountPositionUnknown.test.tsx - WP-173 / #928: the COORDINATES
// sheet's USE MOUNT copies the mount's RA/Dec into the two inputs only while the
// mount knows where it points.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/w20CoordsUseMountPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// A mount that does not know where it points (`position_known === false`, #144)
// reports its HOME position, the pole, wherever the tube is. USE MOUNT typed
// that into the RA and Dec fields as though the operator had chosen it, and the
// read-back then resolved it into a target IMAGE THIS POSITION would offer. It
// now refuses out loud, with the position-unknown reason, and the fields stay
// as they were.
//
// Named mutant (the sheet copies the raw reading again): in coords.tsx
// `useMountPosition`, replace `const here = believedRaDec(mount);` with
// `const here = mount;`.

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky/coords" },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// The read-back lookup is not under test: every catalogue query answers "nothing".
g.fetch = async () =>
  ({ ok: true, status: 200, statusText: "OK", json: async () => ({ results: [], notes: [] }) });

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { CoordsSheet } = await import("../coords");
const { POSITION_UNKNOWN_COPY_DETAIL } = await import("../../../../../lib/slewController");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// ------------------------------------------------------------------ fixtures
// 5.5 h is 05h 30m and -5.4 deg is -05 24: what the inputs take when the
// mount's reading is copied.
const MOUNT = { ra_hours: 5.5, dec_deg: -5.4, ra_str: "05h 30m", dec_str: "-05d 24m" };

function seed(mount: Record<string, unknown> | null): void {
  act(() => {
    useStore.setState({
      principal: {
        role: "admin", email: "admin@example.test",
        caps: ["view.status", "control.capture", "control.mount"],
      },
      authGate: "open",
      wsPhase: "up",
      equipConnected: true,
      toasts: [],
      status: {
        connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [],
        ...(mount === null ? {} : {
          mount: { alt: 50, az: 90, tracking: true, parked: false, slewing: false, ...mount },
        }),
      },
      site: { latitude: 47.6, longitude: -122.3, is_default: false, horizon_min_deg: 15 },
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
const raInput = (): any => container.querySelector('[data-testid="coords-ra-input"]');
const decInput = (): any => container.querySelector('[data-testid="coords-dec-input"]');
const useMountButton = (): any => container.querySelector('[data-testid="use-mount"]');
const toasts = (): any[] => (useStore.getState() as any).toasts;

/** Mount the sheet fresh for this mount block and press USE MOUNT. */
async function press(mount: Record<string, unknown> | null): Promise<{
  present: boolean; ra: string; dec: string; toasts: any[];
}> {
  seed(mount);
  const root = createRoot(container);
  await act(async () => { root.render(createElement(CoordsSheet, { params: {}, depth: 0 } as any)); });
  const btn = useMountButton();
  if (btn) {
    await act(async () => { btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
  }
  const out = {
    present: btn != null,
    ra: (raInput()?.value ?? "") as string,
    dec: (decInput()?.value ?? "") as string,
    toasts: toasts(),
  };
  await act(async () => { root.unmount(); });
  return out;
}

// --------------------------------------------------------------- the control
{
  const r = await press({ ...MOUNT, position_known: true });
  test("control: the sheet has a USE MOUNT button", () => {
    assert(r.present, "no use-mount control - the fixture is wrong, not the sheet");
  });
  test("control: a mount that knows where it points fills both fields, silently", () => {
    assert(/^05h\s?30m/.test(r.ra), `the RA field: "${r.ra}"`);
    assert(/^-05/.test(r.dec), `the Dec field: "${r.dec}"`);
    assert(r.toasts.length === 0, `a toast was raised for a good copy: ${JSON.stringify(r.toasts)}`);
  });
}
{
  const r = await press({ ...MOUNT });
  test("control: an ABSENT flag (an engine older than #144) reads as known", () => {
    assert(/^05h\s?30m/.test(r.ra), `the RA field with no position_known key: "${r.ra}"`);
  });
}
{
  const r = await press(null);
  test("control: no mount block keeps its own sentence and fills nothing", () => {
    assert(r.present, "no use-mount control");
    assert(r.ra === "" && r.dec === "", `the fields were filled with no mount: "${r.ra}" "${r.dec}"`);
    assert(r.toasts.length === 1 && r.toasts[0].title === "No mount position reported yet.",
      `the no-mount toast: ${JSON.stringify(r.toasts)}`);
  });
}

// ------------------------------------------------------------------ the bug
{
  const r = await press({ ...MOUNT, position_known: false });
  test("position_known false: USE MOUNT is still on the sheet", () => {
    assert(r.present, "no use-mount control - the fixture is wrong, not the sheet");
  });
  test("position_known false: USE MOUNT does not type the home reading into the fields", () => {
    assert(r.ra === "" && r.dec === "",
      `the mount's home reading was copied in as a position: "${r.ra}" "${r.dec}"`);
  });
  test("position_known false: USE MOUNT says why, with the position-unknown reason", () => {
    assert(r.toasts.length === 1, `expected one toast, got ${JSON.stringify(r.toasts)}`);
    const t = r.toasts[0];
    assert(/does not know where it points/.test(t.title), `the toast gives no reason: ${t.title}`);
    assert(t.detail === POSITION_UNKNOWN_COPY_DETAIL, `the toast detail: ${t.detail}`);
    assert(t.title !== "No mount position reported yet.",
      "the toast says nothing was reported, when the mount reported its home position");
  });
}

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w20CoordsUseMountPositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
