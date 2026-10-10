// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// sunWatch894Dom.test.tsx - the CLASSIC root shows the sun watch's state (#894).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/views/__tests__/sunWatch894Dom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The server publishes `status.sun_watch` (blind, position unknown, armed) and,
// until #894, no screen read it: a rig with no alert sink showed a quiet UI
// while the Sun approached a tube nothing was watching. Two places in this root
// now say it:
//
//   * the Monitor health strip, where "Night looks OK" used to print over a
//     blind net (the call site passes `status.sun_watch` to the ladder);
//   * `SunWatchBanner`, under the connection banner on every other view.
//
// Every case runs after the page it grades has been found (the control with a
// healthy net finds "Night looks OK" and no banner), so an absence cannot pass
// over a blank page.
//
// NAMED MUTANTS, each applied to a byte backup and restored byte-identical; the
// observed failure is quoted at the case it turns red.
//   C1 "the call site drops the state"   MonitorView.tsx: `sunWatch: status?.sun_watch,`
//                                        removed from the deriveHealthIssues call
//   C2 "the call site drops the mount"   MonitorView.tsx: `mountConnected: ...` removed
//   C3 "no banner"                       SunWatchBanner.tsx returns null
//   C4 "banner on Monitor too"           SunWatchBanner.tsx: the `view === "monitor"` line removed
//   C5 "banner for off"                  SunWatchBanner.tsx: `notice.tier !== 2` removed

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
// Cold-load reads are conveniences; failing them is a supported path.
win.fetch = () => Promise.reject(new Error("offline in this test"));

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "fetch",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const MonitorView = (await import("../MonitorView")).default;
const SunWatchBanner = (await import("../../components/SunWatchBanner")).default;
const { fmtSinceClock, SUN_WATCH_BLIND_STEP, SUN_WATCH_POSITION_STEP } =
  await import("../../lib/sunWatch");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const ADMIN = { role: "admin", email: null, caps: ["view.status", "control.mount"] };

/** Twelve minutes and a few seconds ago, so the age reads "12 min" for as long
 *  as this file takes to run. Epoch seconds, as the server sends them. */
const SINCE = Math.floor(Date.now() / 1000) - 12 * 60 - 5;
const CLOCK = fmtSinceClock(SINCE);

const NET = {
  blind: false, blind_since: null, position_unknown: false,
  position_unknown_since: null, last_position_at: SINCE, armed: true,
};
function status(sunWatch: Record<string, unknown> | undefined, mount = true): any {
  return {
    connected: mount ? { telescope: { name: "mount", kind: "sim", connected: true } } : {},
    looping: false,
    mode: "sim",
    ...(sunWatch ? { sun_watch: sunWatch } : {}),
  };
}
function set(state: Record<string, unknown>): void {
  act(() => { useStore.setState(state as never); });
}

act(() => {
  useStore.setState({
    principal: ADMIN, wsConnected: true, wsPhase: "up", telemetryStale: false,
    sequence: { state: "idle" }, resumeArm: null, safety: null, weather: null,
    status: status({ ...NET }), view: "monitor",
  } as never);
});

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const text = () => (container.textContent || "") as string;

// ============================================================ the Monitor strip

act(() => { root.render(createElement(MonitorView)); });

test("control: a healthy net leaves the strip saying the night looks OK", () => {
  assert(/Night looks OK/.test(text()),
    `the strip is not on the page, so no later absence means anything: ${text().slice(0, 200)}`);
  assert(!/Sun watch/.test(text()), "the strip names the sun watch with nothing to say");
});

test("blind: the strip says so, since when, and what to check; 'Night looks OK' is gone", () => {
  // C1 "the call site drops the state", observed (7/10 passed; the position
  // unknown and off cases fail with it):
  //   x blind: the strip says so, since when, and what to check; 'Night looks
  //   OK' is gone: the strip does not say the net is blind: Night looks OK...
  set({ status: status({ ...NET, blind: true, blind_since: SINCE }) });
  const t = text();
  assert(t.includes(`Sun watch is blind since ${CLOCK} (12 min)`),
    `the strip does not say the net is blind: ${t.slice(0, 300)}`);
  assert(t.includes(SUN_WATCH_BLIND_STEP), "the step is missing");
  assert(!/Night looks OK/.test(t), "'Night looks OK' printed over a blind net");
});

test("position unknown: the strip says the net is standing down, and the safe order", () => {
  set({ status: status({ ...NET, position_unknown: true, position_unknown_since: SINCE }) });
  const t = text();
  assert(t.includes(`Sun watch is standing down since ${CLOCK} (12 min)`),
    `the strip does not say the net is standing down: ${t.slice(0, 300)}`);
  assert(t.includes(SUN_WATCH_POSITION_STEP), "the safe order is missing");
  assert(!/Night looks OK/.test(t), "'Night looks OK' printed over a net that will not park");
});

test("off with a mount connected: a notice chip; off with no mount: nothing", () => {
  // C2 "the call site drops the mount", observed (9/10 passed):
  //   x off with a mount connected: a notice chip; off with no mount: nothing:
  //   no 'Sun watch is not running' chip
  set({ status: status({ ...NET, armed: false }) });
  assert(/Sun watch is not running/.test(text()), "no 'Sun watch is not running' chip");
  set({ status: status({ ...NET, armed: false }, false) });
  assert(!/Sun watch is not running/.test(text()),
    "a net that is not running was called out with no mount to protect");
  assert(/Night looks OK/.test(text()), "the calm line did not return");
});

// ============================================================= the banner

act(() => { root.render(createElement(SunWatchBanner)); });
const banner = () => container.querySelector('[data-testid="sun-watch-banner"]') as any;

test("on the Monitor view the banner stays out of the way (the strip says it)", () => {
  // C4 "banner on Monitor too", observed (9/10 passed):
  //   x on the Monitor view the banner stays out of the way (the strip says
  //   it): the banner repeats the strip
  set({ view: "monitor", status: status({ ...NET, blind: true, blind_since: SINCE }) });
  assert(banner() == null, "the banner repeats the strip");
});

test("control: a healthy net shows no banner on another view", () => {
  set({ view: "mount", status: status({ ...NET }) });
  assert(banner() == null, "a banner with nothing to say");
});

test("blind: the banner names it, since when, and the step, on any other view", () => {
  // C3 "no banner", observed (8/10 passed; the position unknown banner case
  // fails with it):
  //   x blind: the banner names it, since when, and the step, on any other
  //   view: no banner for a blind net
  set({ view: "mount", status: status({ ...NET, blind: true, blind_since: SINCE }) });
  const b = banner();
  assert(b != null, "no banner for a blind net");
  assert(b.getAttribute("data-kind") === "blind", `kind=${b.getAttribute("data-kind")}`);
  assert(b.textContent.includes(`Sun watch is blind since ${CLOCK} (12 min)`), b.textContent);
  assert(b.textContent.includes(SUN_WATCH_BLIND_STEP), "the step is missing");
});

test("position unknown: the banner names it with the safe order", () => {
  set({ view: "connect", status: status({ ...NET, position_unknown: true, position_unknown_since: SINCE }) });
  const b = banner();
  assert(b != null, "no banner for a net standing down");
  assert(b.getAttribute("data-kind") === "position_unknown", `kind=${b.getAttribute("data-kind")}`);
  assert(b.textContent.includes("will not park the tube"), b.textContent);
  assert(b.textContent.includes(SUN_WATCH_POSITION_STEP), "the safe order is missing");
});

test("'not running' is the strip's notice alone: no banner on every screen for a choice", () => {
  // C5 "banner for off", observed (9/10 passed):
  //   x 'not running' is the strip's notice alone: no banner on every screen
  //   for a choice: a banner for a net that was switched off
  set({ view: "mount", status: status({ ...NET, armed: false }) });
  assert(banner() == null, "a banner for a net that was switched off");
});

test("a status frame with no sun_watch key says nothing (an older server)", () => {
  set({ view: "mount", status: status(undefined) });
  assert(banner() == null, "a banner on the strength of a missing key");
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`sunWatch894Dom (classic): ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
