// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w19CaptureLiveRun.test.tsx - RIG - CAPTURE, gates and MOUNTED: the bench
// treats EVERY live run state as the run owning the camera and the mount, not
// only running and paused (#922, WP-161, wave 19).
//
//   Run directly:  npx tsx src/next/hubs/rig/capture/__tests__/w19CaptureLiveRun.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. Four hand-written copies of `state === "running" || state ===
// "paused"` stood in for "a run is live" on this screen: `exposeReason` and
// `videoRefusal` in captureGate.ts (the reason a shutter press is refused),
// `runOwnsMount` in CaptureScreen.tsx (the aim button's SLEW gate) and the same
// line in CaptureStage.tsx (the pointing line's "session running"). A cloud hold
// ("holding") and an abort's wind-down ("aborting") are `engine.running` on the
// rig, so during either the bench read the camera and the mount as free: CAPTURE
// and RECORD armed, SLEW armed, no notice under the controls, and the pointing
// line described a mount nobody was driving.
//
// WHAT IS WORTH ASSERTING. The pure gates over the whole union (a run that is
// not live refuses nothing, so a predicate that is always true fails), then the
// real screen per state: the CAPTURE button, the SLEW button, the notice and the
// pointing line, so each of the four sites is graded by what it does.
//
// MUTANT "running or paused only" (captureGate.ts `seqIsLive`, CaptureScreen's
// `runOwnsMount` and CaptureStage's `runOwnsMount` each made `... === "running"
// || ... === "paused"`, the unfixed text). Run from a byte backup, restored
// byte-identically (md5sum compared): see the report for the failing lines.

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
  { url: "http://local/#/rig/capture?target=Jupiter&ra=3.1&dec=17.2", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const asks: Array<{ url: string; method: string }> = [];
g.fetch = async (url: any, init: any) => {
  asks.push({ url: String(url), method: (init?.method ?? "GET").toUpperCase() });
  return { ok: true, status: 200, statusText: "OK", json: async () => ({ ok: true }) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { CaptureScreen } = await import("../CaptureScreen");
const gate = await import("../captureGate");
const {
  exposeReason, videoRefusal, sequenceNotice, seqIsLive,
  SEQUENCE_REASON, SESSION_OWNS_MOUNT_REASON,
} = gate;
type CaptureGateInput = import("../captureGate").CaptureGateInput;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

/** The whole `SequenceState["state"]` union, with whether the rig's
 *  `engine.running` is true in it. */
const STATES: Array<[string, boolean]> = [
  ["running", true], ["paused", true], ["holding", true], ["aborting", true],
  ["idle", false], ["complete", false], ["aborted", false], ["error", false],
  ["nina_native", false],
];

// ------------------------------------------------------------- the pure gates
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount"],
};
const STATUS = {
  connected: { camera: { connected: true } },
  looping: false,
  camera: { temperature: -10, can_cool: true, width: 100, height: 100, max_gain: 500 },
} as unknown as CaptureGateInput["status"];
function inp(seqState: string): CaptureGateInput {
  return {
    principal: OPERATOR as unknown as CaptureGateInput["principal"],
    status: STATUS,
    equipConnected: true,
    wsPhase: "up",
    polarBusy: false,
    seqState: seqState as CaptureGateInput["seqState"],
    exposureRaw: "60",
    gainRaw: "120",
    maxGain: 500,
    looping: false,
    pending: null,
  };
}
const VIDEO = {
  path: "native" as const, pathReason: "", serverRefusal: null,
  recording: false, capturing: false, starting: false,
};

for (const [state, live] of STATES) {
  test(`gate, run ${state}: a shutter press is ${live ? "refused: the run owns the camera" : "not refused by a run"}`, () => {
    eq(exposeReason(inp(state)), live ? SEQUENCE_REASON : null, `exposeReason while ${state}`);
  });
  test(`gate, run ${state}: RECORD is ${live ? "refused: the run owns the camera" : "not refused by a run"}`, () => {
    eq(videoRefusal(inp(state), VIDEO), live ? SEQUENCE_REASON : null, `videoRefusal while ${state}`);
  });
  test(`gate, run ${state}: the notice under the controls ${live ? "says the camera is reserved" : "is absent"}`, () => {
    const note = sequenceNotice(state as never);
    if (live) {
      assert(note != null && /camera reserved/.test(note),
        `no reservation notice while the run is ${state}: ${String(note)}`);
    } else {
      eq(note, null, `a reservation notice while the run is ${state}`);
    }
  });
}
test("gate: the four live notices are four different sentences, none of them the button reason", () => {
  const notes = ["running", "paused", "holding", "aborting"].map((s) => String(sequenceNotice(s as never)));
  eq(new Set(notes).size, 4, `the notices collapse into one another: ${JSON.stringify(notes)}`);
  assert(!notes.includes(SEQUENCE_REASON), "a notice repeats the button reason");
});
test("gate: no sequence at all is not a live run", () => {
  eq(seqIsLive(null), false, "null");
  eq(seqIsLive(undefined), false, "undefined");
});

// -------------------------------------------------------------- the real screen
const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const q = (sel: string) => container.querySelector(sel) as any;
const locked = (node: any): boolean => node?.getAttribute("aria-disabled") === "true";
const titleOf = (node: any): string => (node?.getAttribute("title") ?? "") as string;

function seed(sequence: Record<string, unknown>): void {
  act(() => {
    useStore.setState({
      principal: OPERATOR,
      equipConnected: true,
      wsPhase: "up",
      status: {
        connected: {
          camera: { connected: true, name: "sim" },
          telescope: { connected: true, name: "sim" },
        },
        looping: false,
        live_stack_active: false,
        busy_lanes: [],
        camera: {
          temperature: -10, can_cool: true, width: 1000, height: 1000,
          max_gain: 500, max_bin: 4,
          cooler: { on: true, power: 40, target_c: -10, at_target: true, can_report_power: true },
        },
        mount: { tracking: true, parked: false, slewing: false },
      },
      frameSettings: {
        capture: { exposure_s: 2, gain: 120, offset: 30, binning: 1, filter: null },
        focus: { exposure_s: 2, gain: 200, offset: 30, binning: 1, filter: null },
        solve: { exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null },
        guide: { exposure_s: 2, gain: 100, offset: 30, binning: 1, filter: null },
      },
      // The adopted target is the one the route names, which is what makes the
      // aim button a SLEW and not USE or AIM IN SKY.
      captureTarget: "Jupiter",
      previews: [],
      livePreviewId: null,
      selectedPreviewId: null,
      sequence,
      polar: {
        state: "idle", az_error: 0, alt_error: 0, total_error: 0, progress: 0,
        message: "", source: null,
      },
      toasts: [],
    } as never);
  });
}

seed({ state: "idle" });
await act(async () => { root.render(createElement(CaptureScreen)); });
await settle();

test("the screen rendered with a SLEW aim button, free on an idle rig (the precondition)", () => {
  assert(q('[data-testid="rig-capture"]') != null, "no rig-capture marker: the fixture is wrong");
  assert(q('[data-testid="capture-go"]') != null, "no CAPTURE button");
  assert(q('[data-testid="capture-aim"]') != null, "no aim button");
  assert(/SLEW/.test(String(q('[data-testid="capture-aim"]').textContent)),
    `the aim button is not a SLEW: "${q('[data-testid="capture-aim"]').textContent}"`);
  assert(!locked(q('[data-testid="capture-go"]')), `CAPTURE is locked on an idle rig: ${titleOf(q('[data-testid="capture-go"]'))}`);
  assert(!locked(q('[data-testid="capture-aim"]')), `SLEW is locked on an idle rig: ${titleOf(q('[data-testid="capture-aim"]'))}`);
});

for (const [state, live] of STATES) {
  await testAsync(`screen, run ${state}: CAPTURE, SLEW, the notice and the pointing line ${live ? "all say a run is live" : "say nothing about a run"}`, async () => {
    seed({ state, target: "M 31" });
    await settle();
    const go = q('[data-testid="capture-go"]');
    const aim = q('[data-testid="capture-aim"]');
    const stage = String(q('[data-testid="capture-stage"]')?.textContent ?? "");
    const screen = String(container.textContent ?? "");
    assert(go != null && aim != null, "the bench lost its controls");
    if (live) {
      assert(locked(go), `CAPTURE is ARMED while the run is ${state}: the run owns the camera`);
      eq(titleOf(go), SEQUENCE_REASON, `CAPTURE's reason while the run is ${state}`);
      assert(locked(aim), `SLEW is ARMED while the run is ${state}: the run owns the mount`);
      eq(titleOf(aim), SESSION_OWNS_MOUNT_REASON, `SLEW's reason while the run is ${state}`);
      assert(screen.includes(String(sequenceNotice(state as never))),
        `no reservation notice under the controls while the run is ${state}`);
      assert(/tracking M 31 . session running/.test(stage),
        `the pointing line does not say the session owns the mount while the run is ${state}: "${stage}"`);
    } else {
      assert(!locked(go), `CAPTURE is locked by a run that is ${state}: ${titleOf(go)}`);
      assert(!locked(aim), `SLEW is locked by a run that is ${state}: ${titleOf(aim)}`);
      assert(!/camera reserved/.test(screen), `a reservation notice while the run is ${state}`);
      assert(!/session running/.test(stage), `the pointing line claims a session while the run is ${state}: "${stage}"`);
    }
  });
}

await testAsync("a press on SLEW during a cloud hold says why and sends nothing to the mount", async () => {
  seed({ state: "holding", target: "M 31" });
  await settle();
  asks.length = 0;
  await act(async () => {
    q('[data-testid="capture-aim"]').dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
  eq(asks.filter((a) => a.method !== "GET").length, 0,
    `SLEW reached the rig during a hold: ${JSON.stringify(asks)}`);
  assert(!asks.some((a) => a.url.includes("/api/mount/goto")), "a goto was posted over a live run");
});

act(() => { root.unmount(); });

console.log(`w19CaptureLiveRun: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
