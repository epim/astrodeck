// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w18MonitorViewPositionUnknown.test.tsx - WP-151 / #791: the classic Monitor's
// two readers of `status.mount.alt/az` while the mount does not know where it
// points.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/views/__tests__/w18MonitorViewPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// After a power cycle the AM5 reports its HOME position, the pole, wherever the
// tube is (`status.mount.position_known === false`, #144). Issue #791 listed the
// next UI's readers; the classic Monitor was missed and had two of its own:
//   * the sky-dome panel's `pointing` (it asks the server how clear the sky is
//     where the scope looks, `GET /api/cloudmap/at`, and prints the answer), and
//   * the "BELOW HORIZON - mount at N" chip, which printed the believed altitude.
// A known position does both - the control, without which "neither" proves
// nothing - and an unknown one does neither.
//
// Named mutants (the Monitor reads the raw angles again): in MonitorView.tsx
// replace `const believed = believedPointing(mount);` with
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
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = function () { return null; };

const asked: string[] = [];
const ok = (data: any) => ({
  ok: true, status: 200, statusText: "OK",
  headers: { get: () => "application/json" },
  json: async () => data, text: async () => JSON.stringify(data),
});
const domePayload = {
  enabled: true, observed_at: "2026-09-10T05:00:00Z", stale: false,
  alt_start: 6, alt_step: 6, az_step: 10,
  rows: Array.from({ length: 14 }, () => Array.from({ length: 36 }, () => 0.05)),
};
// The cloud panel's reads are answered; every other cold read fails, which is a
// supported path for each caller on this screen.
win.fetch = (url: any) => {
  const u = String(url);
  asked.push(u);
  if (u.includes("/api/cloudmap/dome")) return Promise.resolve(ok(domePayload));
  if (u.includes("/api/cloudmap/at")) {
    return Promise.resolve(ok({ enabled: true, ahead_s: 0, probability: 0.12, basis: "mask_only", crossing_km: null, beam_m: 200 }));
  }
  if (u.includes("/api/cloudmap")) {
    return Promise.resolve(ok({ enabled: true, platform: "G18", observed_at: null, age_s: null, stale: false, last_error: null, motion: null, credit: { source: "", url: "" } }));
  }
  return Promise.reject(new Error("offline in this test"));
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver", "HTMLCanvasElement",
  "requestAnimationFrame", "cancelAnimationFrame", "fetch", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const MonitorView = (await import("../MonitorView")).default;

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
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const text = () => (container.textContent || "") as string;

const MOUNT = {
  ra_hours: 3.5, dec_deg: 40, ra_str: "03:30:00", dec_str: "+40:00:00",
  tracking: true, parked: false, slewing: false, az: 120,
};

/** Mount a fresh Monitor with this mount block and report what the sky-dome
 *  panel asked for and printed, and whether the below-horizon chip is up. FRESH
 *  each time because the panel reads the pointing when it loads. */
async function run(mount: Record<string, unknown>): Promise<{ at: string[]; shown: boolean; chip: string | null }> {
  act(() => {
    useStore.setState({
      principal: { role: "admin", email: null, caps: ["view.status", "view.weather", "control.mount"] },
      wsConnected: true,
      sequence: { state: "idle" },
      resumeArm: null,
      status: { mode: "sim", busy: [], connected: {}, mount },
    } as never);
  });
  asked.length = 0;
  const root = createRoot(container);
  act(() => { root.render(createElement(MonitorView)); });
  try {
    await settle();
    assert(/Sky dome/.test(text()), "the classic Monitor rendered no sky-dome panel - the fixture is wrong, not the component");
    assert(asked.some((a) => /\/api\/cloudmap$/.test(a)),
      "the dome panel never loaded, so it never reached the pointing check");
    const at = asked.filter((a) => a.includes("/api/cloudmap/at"));
    const shown = /where you are pointing/.test(text());
    const m = /BELOW HORIZON[^\n]*/.exec(text());
    return { at, shown, chip: m ? m[0] : null };
  } finally {
    act(() => { root.unmount(); });
  }
}

// --------------------------------------------------------------- the control
{
  const r = await run({ ...MOUNT, alt: 45, position_known: true });
  test("control: a mount that knows where it points makes the dome ask how clear the sky is there", () => {
    assert(r.at.length > 0, "no /api/cloudmap/at request for a known position at alt 45");
  });
  test("control: and the panel prints the answer", () => {
    assert(r.shown, "the 'where you are pointing' line is missing for a known position");
  });
}
{
  const r = await run({ ...MOUNT, alt: 45 });
  test("control: an ABSENT flag (an engine older than #144) reads as known", () => {
    assert(r.at.length > 0 && r.shown, "an absent flag withheld the pointing");
  });
}
{
  const r = await run({ ...MOUNT, alt: -12, position_known: true });
  test("control: a known position below the horizon raises the chip, with the altitude", () => {
    assert(r.chip !== null && /-12/.test(r.chip), `no BELOW HORIZON chip for a known alt of -12: ${JSON.stringify(r.chip)}`);
  });
}

// ------------------------------------------------------------------ the bug
{
  const r = await run({ ...MOUNT, alt: 45, position_known: false });
  test("position_known false: the dome does not ask about the mount's home reading", () => {
    assert(r.at.length === 0, `the dome asked the server about the home pointing: ${JSON.stringify(r.at)}`);
  });
  test("position_known false: the panel does not print a 'where you are pointing' answer", () => {
    assert(!r.shown, "the panel describes the sky along a pointing the mount cannot vouch for");
  });
}
{
  const r = await run({ ...MOUNT, alt: -12, position_known: false });
  test("position_known false: a home reading under the horizon raises no chip and prints no altitude", () => {
    assert(r.chip === null, `the chip judges the horizon from the home reading: ${JSON.stringify(r.chip)}`);
  });
}

const total = passed + failed;
console.log(`w18MonitorViewPositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
