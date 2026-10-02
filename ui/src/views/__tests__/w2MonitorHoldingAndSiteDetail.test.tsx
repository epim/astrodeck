// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w2MonitorHoldingAndSiteDetail.test.tsx - WP-17 (a) and (b) on the classic
// Monitor dashboard.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/views/__tests__/w2MonitorHoldingAndSiteDetail.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// (a) #259: `runActive` omitted "holding", so the whole of a cloud hold (up to
//     CLOUD_MAX_HOLD_MIN, 45 min default) hid the controls row - no Abort, no
//     Pause - over a rig that was still live: probing the sky on a timer and
//     shooting hold darks. Abort is the one way to stop a run over plain HTTP
//     when the WebSocket is down (A2).
// (b) #258: ResumeArm's `hold.site_detail` carries the numbers behind a
//     words-only `hold.reason` (the altitude, the floor, the wait until the
//     target rises). The classic Monitor's RUN ARMED paragraph rendered
//     `reason` alone, so an operator entitled to the numbers (the server
//     already withholds the key from a viewer) saw no ETA at all.
//
// NAMED MUTANTS. Each was run from a byte copy of MonitorView.tsx and the file
// was restored byte-identical afterwards (sha256 checked). The observed
// failure is quoted at the test it turns red.
//   H1 "drop holding from runActive"   `const runActive = running || paused || aborting;`
//   H2 "show reason alone"             the site_detail branch removed from the
//                                       RUN ARMED paragraph

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
// Cold-load reads (/api/monitor/snapshot, /api/dome/state) are conveniences;
// failing them is a supported path and keeps this test on the two items above.
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

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

function set(state: Record<string, unknown>): void {
  act(() => { useStore.setState(state as never); });
}

const ADMIN = { role: "admin", email: null, caps: ["view.status", "control.mount"] };

act(() => {
  useStore.setState({
    principal: ADMIN,
    wsConnected: true,
    sequence: { state: "idle" },
    resumeArm: null,
  } as never);
});

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(MonitorView)); });

const text = () => (container.textContent || "") as string;
const pauseBtn = () => container.querySelector('[aria-label="Pause sequence"]') as any;
const resumeBtn = () => container.querySelector('[aria-label="Resume sequence"]') as any;
const abortBtn = () => container.querySelector('[aria-label="Abort sequence"]') as any;
/** The Progress panel: the <section> whose title is "Progress". */
const progressPanel = (): any =>
  [...container.querySelectorAll("section")].find((s: any) =>
    (s.querySelector(".panel-title")?.textContent || "") === "Progress");

// ============================================================ (a) #259 holding

test("precondition: an idle rig shows neither Pause nor Abort", () => {
  // Without this, "holding keeps them" would pass over a component that
  // always renders both, which proves nothing about the hold specifically.
  assert(pauseBtn() == null, "Pause is on screen with no run at all - the fixture is wrong");
  assert(abortBtn() == null, "Abort is on screen with no run at all - the fixture is wrong");
});

test("control: a RUNNING sequence shows both Pause and Abort", () => {
  set({ sequence: { state: "running" } });
  assert(pauseBtn() != null, "precondition failed: Pause is missing while running");
  assert(abortBtn() != null, "precondition failed: Abort is missing while running");
});

test("a CLOUD HOLD keeps Abort and Pause on screen (#259)", () => {
  // H1 "drop holding from runActive", observed:
  //   w2MonitorHoldingAndSiteDetail: 6/7 passed
  //   x a CLOUD HOLD keeps Abort and Pause on screen (#259): no Pause while holding
  set({ sequence: { state: "holding", detail: "held for cloud - waiting for clear sky" } });
  assert(/HOLDING/.test(text()), "precondition: the badge does not say HOLDING");
  assert(pauseBtn() != null, "no Pause while holding");
  assert(abortBtn() != null, "no Abort while holding - the one way to stop a run "
    + "when the WS is down just vanished");
});

test("while holding, Resume is not offered in Pause's place (the run was never paused)", () => {
  assert(resumeBtn() == null, "a HOLD is being shown as a PAUSE");
});

test("control: a terminal state shows neither Pause nor Abort again", () => {
  set({ sequence: { state: "complete" } });
  assert(pauseBtn() == null, "Pause outlived the run");
  assert(abortBtn() == null, "Abort outlived the run");
});

// ====================================================== (b) #258 site_detail

const ARMED = {
  id: "sess-1", name: "M31 LRGB", owed: 58, accepted: 132, total: 190,
  origin: "plan", origin_id: "",
};
const STALE_HOLD = {
  reason: "M31 is below its start floor; not slewing yet",
  since: 1_700_000_000, retry_at: 1_700_000_600,
  session_id: "sess-1", session_name: "M31 LRGB", owed: 58,
};
// A made-up target and made-up numbers - not the real site (project rule).
const SITE_DETAIL =
  "M31 is at 12 deg, below its 20 deg start floor (it reaches 20 deg in about 2 h)";

function arm(over: Record<string, unknown> = {}): any {
  return { armed: ARMED, hold: null, recovering: false, recovery: null, ...over };
}

test("control: a stale hold with no site_detail shows the reason alone, as before", () => {
  set({ sequence: { state: "idle" }, resumeArm: arm({ hold: STALE_HOLD }) });
  const p = progressPanel().textContent as string;
  assert(p.includes(`Holding: ${STALE_HOLD.reason}.`),
    `the plain hold (no site_detail) changed shape: ${p}`);
  assert(!p.includes(SITE_DETAIL), "a site_detail string appeared with no field on the wire");
});

test("a hold's site_detail is shown beside the reason, for an entitled principal (#258)", () => {
  // H2 "show reason alone", observed:
  //   x a hold's site_detail is shown beside the reason, for an entitled
  //   principal (#258): site_detail is missing from the panel even though the
  //   server sent it
  set({ resumeArm: arm({ hold: { ...STALE_HOLD, site_detail: SITE_DETAIL } }) });
  const p = progressPanel().textContent as string;
  assert(p.includes(`Holding: ${STALE_HOLD.reason} - ${SITE_DETAIL}.`),
    `site_detail is missing from the panel even though the server sent it: ${p}`);
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`w2MonitorHoldingAndSiteDetail: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
