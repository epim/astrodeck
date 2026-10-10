// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w18LivePositionUnknown.test.tsx - WP-151 / #791: MONITOR - LIVE's "the mount is
// pointing below the horizon" pill while the mount does not know where it points.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/monitor/live/__tests__/w18LivePositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// After a power cycle the AM5 reports its HOME position, the pole, wherever the
// tube is (`status.mount.position_known === false`, #144). The pill judged the
// horizon from `mount.alt` without reading the flag, so a reset mount whose home
// reading sits under the horizon (the home pole is under a southern site's
// horizon, say) raised "the mount is pointing below the horizon" on the screen a
// run is watched from - a verdict about a tube nobody had located.
//
// This mounts the real screen with the mount block in four states and reads the
// pill: a known position below the horizon raises it (the control), an unknown
// one does not, and neither does a known one above the horizon.
//
// Named mutant (screen reads the raw altitude again): in LiveScreen.tsx replace
// `const believed = believedPointing(mount);` with
// `const believed = mount ? { alt: mount.alt, az: mount.az } : null;`.

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.scrollTo = () => {};
win.WebSocket = class { close() {} addEventListener() {} send() {} };

// Every cold read fails, which is a supported path for each caller on this
// screen; the snapshot read in particular then leaves the seeded store alone.
const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
win.fetch = (url: any) => {
  const u = String(url);
  if (u.includes("/api/logs")) return Promise.resolve(ok([]));
  return Promise.reject(new Error("offline in this test"));
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver", "requestAnimationFrame",
  "cancelAnimationFrame", "fetch", "location",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { LiveScreen } = await import("../LiveScreen");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
async function settle(): Promise<void> {
  await act(async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0)); });
}

const container = win.document.getElementById("root") as any;
const byId = (id: string): any => container.querySelector(`[data-testid="${id}"]`);

const MOUNT = {
  ra_hours: 3.5, dec_deg: 40, ra_str: "03:30:00", dec_str: "+40:00:00",
  tracking: true, parked: false, slewing: false, az: 120,
};

/** Mount a fresh screen with this mount block and report whether the pill is up. */
async function pill(mount: Record<string, unknown>): Promise<boolean> {
  act(() => {
    useStore.setState({
      principal: {
        role: "operator", email: "op@rig",
        caps: ["view.status", "view.preview", "control.capture", "control.guide", "control.mount"],
      },
      authGate: "open",
      equipConnected: true,
      wsPhase: "up",
      wsConnected: true,
      preview: null,
      snapshotPreviewId: null,
      // A saved site: the pill is withheld for the placeholder site (#633).
      config: { site: { is_default: false } },
      status: { mode: "sim", busy: [], connected: {}, mount },
    } as never);
  });
  const root = createRoot(container);
  act(() => { root.render(createElement(LiveScreen)); });
  try {
    await settle();
    assert(byId("monitor-live") != null, "precondition: MONITOR - LIVE did not render");
    return byId("monitor-below-horizon") != null;
  } finally {
    act(() => { root.unmount(); });
  }
}

// --------------------------------------------------------------- the control
{
  const up = await pill({ ...MOUNT, alt: -12, position_known: true });
  test("control: a mount that knows where it points, below the horizon, raises the pill", () => {
    assert(up, "no below-horizon pill for a known position at alt -12, so the cases below prove nothing");
  });
}
{
  const up = await pill({ ...MOUNT, alt: -12 });
  test("control: an ABSENT flag (an engine older than #144) reads as known", () => {
    assert(up, "an absent flag withheld the pill");
  });
}
{
  const up = await pill({ ...MOUNT, alt: 40, position_known: true });
  test("control: a known position above the horizon raises no pill", () => {
    assert(!up, "the pill is up for a mount at alt 40");
  });
}

// ------------------------------------------------------------------ the bug
{
  const up = await pill({ ...MOUNT, alt: -12, position_known: false });
  test("position_known false: a home reading under the horizon raises no pill", () => {
    assert(!up, "the pill judges the horizon from the mount's home reading");
  });
}

const total = passed + failed;
console.log(`w18LivePositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
