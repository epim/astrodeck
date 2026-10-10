// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w19AtPositionUnknown.test.tsx - WP-160 / #913: the guided "is the telescope
// at the target?" checks while the mount does not know where it points.
//
//   Run directly:  npx tsx src/guided/__tests__/w19AtPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `atPosition(status.mount, target)` decides three guided questions: whether
// the first-image page may say "Ready for a first exposure" (GuidedFirstImage),
// whether the guided Focus page may shoot and complete the focus fact
// (FocusView), and whether a guided slew has arrived (motion.ts slewAndWait).
// After a power cycle the AM5 reports its HOME position, the pole, wherever the
// tube is (`status.mount.position_known === false`, #144), so a target near
// the pole read as arrived from that reading. #791 left it, and so did this
// file's subject until #913. Each surface below changes its answer when the
// flag flips and the coordinates do not, which is the whole claim: the reading
// is the same and the mount no longer vouches for it.
//
// The target is Polaris-like (0.7 degrees from the pole), the one place the
// home reading and a real target coincide. Every case asks the SAME question
// of a known position first (the control), so "it did not arrive" cannot be a
// fixture that never could.
//
// Named mutant (atPosition reads the raw reading again): in guided/setup.ts
// replace `const here = believedRaDec(position);` with
// `const here = position && Number.isFinite(position.ra_hours) && Number.isFinite(position.dec_deg) ? position : null;`.

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
win.HTMLCanvasElement.prototype.getContext = () => null;
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};

const POLARIS = { ra_hours: 2.53, dec_deg: 89.26 };
// The mount's home reading at power-up: the pole, with an RA that is only the
// sidereal clock. Within a degree of Polaris, which is the point.
const HOME = { ra_hours: 11.2, dec_deg: 89.9 };

function respond(body: any): any {
  return { ok: true, status: 200, statusText: "OK", headers: { get: () => null }, json: async () => body };
}
const posts: string[] = [];
win.fetch = async (url: any, init: any = {}) => {
  const path = String(url);
  if ((init.method ?? "GET").toUpperCase() === "POST") posts.push(path);
  if (path === "/api/guided/first-targets") {
    return respond({
      picks: [{ id: "NGC 0001", name: "Polaris field", type: "Star", ra_hours: POLARIS.ra_hours, dec_deg: POLARIS.dec_deg, alt: 47, az: 0 }],
      reason: null, simulation: true,
    });
  }
  if (path === "/api/mount/goto") {
    // The mount slews and arrives, on a position it vouches for.
    useStore.setState({ status: slewStatus({ slewing: true, pointing: { verified: false }, position_known: true }) } as never);
    useStore.setState({ status: slewStatus({ position_known: true }) } as never);
  }
  return respond({ started: "capture" });
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "Image", "URL", "fetch", "Blob",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { useGuidedSetup, atPosition } = await import("../setup");
const { useExperience } = await import("../experience");
const { slewAndWait } = await import("../motion");
const FirstImage = (await import("../GuidedFirstImage")).default;
const FocusView = (await import("../../views/FocusView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const container = win.document.getElementById("root") as any;
const text = (): string => container.textContent || "";
const button = (t: string): any =>
  [...container.querySelectorAll("button")].find((b: any) => (b.textContent || "").includes(t));

const settle = async (ms = 0) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };

function mountBlock(over: Record<string, unknown>): Record<string, unknown> {
  return { ...POLARIS, slewing: false, parked: false, pointing: { verified: true }, ...over };
}

// ============================================================ atPosition itself
await test("control: a known position at the target is at the target", () => {
  assert(atPosition({ ...POLARIS, position_known: true } as any, POLARIS), "known and on target");
  assert(atPosition({ ...POLARIS } as any, POLARIS), "an absent flag (an engine older than #144) reads as known");
  assert(atPosition(POLARIS, POLARIS), "a bare sky position has no flag and counts as known");
});

await test("control: a known position far from the target is not at the target", () => {
  assert(!atPosition({ ra_hours: 20, dec_deg: 10, position_known: true } as any, POLARIS), "known and far");
});

await test("position_known false: the mount's home reading is not 'at the target' even where they coincide", () => {
  // The two are within a degree of each other, so on the raw reading this is true.
  assert(atPosition(HOME, POLARIS), "fixture check: the raw home reading does sit within a degree of the target");
  assert(!atPosition({ ...HOME, position_known: false } as any, POLARIS),
    "an unknown position was reported as at the target");
  assert(!atPosition({ ...POLARIS, position_known: false } as any, POLARIS),
    "an unknown position with the target's own coordinates was reported as at the target");
});

// ================================================== motion.ts: a guided slew
const slewStatus = (over: Record<string, unknown>) => ({
  mount: mountBlock(over), connected: { camera: { connected: true }, telescope: { connected: true } },
  busy_lanes: [], mode: "sim",
});
function resetStore(over: Record<string, unknown> = {}): void {
  useStore.setState({
    wsPhase: "up", telemetryStale: false,
    sequence: { state: "idle" }, polar: { state: "idle" }, focus: null, filterOffsetsLearn: { state: "idle" },
    status: slewStatus({ ra_hours: 20, dec_deg: 10, ...over }),
    principal: { role: "admin", email: null, caps: ["control.mount", "control.capture", "view.status"] },
    previews: [], livePreviewId: null,
  } as never);
}

await test("control: a guided slew that arrives at the target on a known position completes", async () => {
  resetStore();
  const waiting = slewAndWait(POLARIS, false, new AbortController().signal);
  act(() => { useStore.setState({ status: slewStatus({ position_known: true }) } as never); });
  await waiting;
});

await test("position_known false: a guided slew does not complete on the home reading", async () => {
  resetStore();
  const abort = new AbortController();
  const waiting = slewAndWait(POLARIS, false, abort.signal);
  // The mount reports its home reading, within a degree of the target, and has
  // stopped moving: on the raw reading that is an arrival.
  act(() => { useStore.setState({ status: slewStatus({ ...HOME, position_known: false }) } as never); });
  const stop = setTimeout(() => abort.abort(), 700);
  let outcome = "completed";
  try { await waiting; } catch (e) { outcome = (e as Error).message; }
  clearTimeout(stop);
  // It ends in the position refusal (#986) rather than waiting out the abort:
  // the move cannot be confirmed from a position nobody vouches for, and the
  // wait says so at once. "completed" is the failure this test exists for.
  assert(outcome !== "completed",
    `the guided slew confirmed an arrival from a position the mount cannot vouch for (${outcome})`);
  assert(/no longer knows where the telescope points/.test(outcome),
    `the guided slew did not end as the position refusal (${outcome})`);
});

// ================================================ GuidedFirstImage, mounted
{
  resetStore({ position_known: true });
  useGuidedSetup.setState({ location: true, horizon: true, focus: true, alignment: true } as never);
  const root = createRoot(container);
  await act(async () => { root.render(createElement(FirstImage)); });
  await settle();
  await act(async () => { button("Polaris field")?.click(); });
  await act(async () => { button("Point telescope at this target")?.click(); await new Promise((r) => setTimeout(r, 650)); });

  await test("control: at the target on a known position, the page is ready for a first exposure", () => {
    assert(button("Polaris field") != null, "the target list never rendered - the fixture is wrong, not the component");
    assert(/Ready for a first exposure/.test(text()), `the page never reached the ready state: ${text().slice(0, 300)}`);
    assert(button("Take my first image") != null, "no capture button at the target");
  });

  await act(async () => { useStore.setState({ status: slewStatus({ position_known: false }) } as never); });
  await settle();

  await test("position_known false: the page stops saying the telescope is at the target and offers no exposure", () => {
    assert(!/Ready for a first exposure/.test(text()),
      "the page still claims the telescope is at the target when the mount cannot say where it points");
    assert(button("Take my first image") == null, "an exposure is still offered from an unknown position");
  });

  await act(async () => { useStore.setState({ status: slewStatus({ position_known: true }) } as never); });
  await settle();
  await test("clearing the flag brings the ready state back", () => {
    assert(/Ready for a first exposure/.test(text()), `got: ${text().slice(0, 300)}`);
  });
  await act(async () => { root.unmount(); });
}

// ======================================================== guided FocusView
{
  const FIELD_REASON = "Point the telescope at the alignment field first.";
  const focusStatus = (mount: Record<string, unknown>) => ({
    connected: { camera: true, focuser: true }, mode: "sim", looping: false, busy_lanes: [],
    camera: { max_gain: 500, max_bin: 4 },
    focuser: { position: 12000, max: 40000, moving: false, temperature: 5.5 },
    mount: mountBlock(mount),
  });
  useStore.setState({
    status: focusStatus({ position_known: true }),
    principal: { role: "operator", email: null, caps: ["view.status", "control.capture"] },
    wsPhase: "up", telemetryStale: false,
  } as never);
  useExperience.setState({ mode: "guided", home: false, wizard: "focus" } as never);
  useGuidedSetup.setState({ location: true, horizon: true, focus: false, alignment: false, field: { ...POLARIS }, recovering: false, savingChecks: false } as never);
  const root = createRoot(container);
  await act(async () => { root.render(createElement(FocusView)); });
  await settle();

  await test("control: at the alignment field on a known position, the guided star image is offered", () => {
    const b = button("Take a star image");
    assert(b != null, `no guided star-image button - the fixture never reached the guided Focus page: ${text().slice(0, 200)}`);
    assert(b.title !== FIELD_REASON, `the field reason is shown at the field: "${b.title}"`);
  });

  await act(async () => { useStore.setState({ status: focusStatus({ position_known: false }) } as never); });
  await settle();

  await test("position_known false: the guided star image is blocked with the field reason", () => {
    const b = button("Take a star image");
    assert(b != null, "the star-image button vanished");
    assert(b.title === FIELD_REASON,
      `the guided Focus page treats an unknown position as at the field (title "${b.title}")`);
    assert(b.disabled === true, "the guided star image is still pressable from an unknown position");
  });

  await act(async () => { useStore.setState({ status: focusStatus({ position_known: true }) } as never); });
  await settle();
  await test("clearing the flag lifts the block", () => {
    assert(button("Take a star image").title !== FIELD_REASON, "the field reason stuck after the position became known");
  });
  await act(async () => { root.unmount(); });
}

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w19AtPositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
