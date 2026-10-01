// w2FlowCardRunIdentity.test.tsx - the Sky flow card reads run state off the
// rig's own identity, never off the client's phase latch alone (#162).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/sheets/__tests__/w2FlowCardRunIdentity.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT (adjudication comment on #162, filed while verifying #454): two
// readers on this card used `isRunPhaseLive(flows.run.phase)` alone - the
// client's own latch, never reset once a run on this page has ended:
//
//   - `running` (the RUN NOW / RUN IN PROGRESS label, and the camera-required
//     check `runBlockedReason` skips while "running"): once a run on this page
//     ends, the card stays mislabelled RUN IN PROGRESS and stays pressable, so
//     a later press - with no camera even connected - goes ahead and POSTS a
//     start.
//   - `started()`, read right after `runAnsweringQuestions` resolves to decide
//     whether the press actually started something: a REFUSAL after one run on
//     this page has already succeeded (a run already live, the horizon, a held
//     camera) was read as a start, and the card navigated to Session - Now
//     with no word that nothing happened.
//
// THE FIX SHAPE (backlog ruling, WP-16 (a), owner-approved 2026-09-30): "the
// Sky flow card compare[s] run identity, the pattern of the Send-to-Wizard
// fix [#454]". `running` is read off `flowRunLive` over `knownSessions` (the
// session the sequence state publishes now is one of THIS flow's) rather than
// the latch; `started()` is read off whether `flows.run` CHANGED OBJECT
// IDENTITY during the press (flowsRun writes a new one only on a real start),
// exactly as SendToWizardSheet.tsx's RUN already does.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky/quick/flow?id=f1", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------ the fake server
interface Ask { url: string; method: string }
let asks: Ask[] = [];
/** Set per-test: the run route's one answer, or null for the ordinary start. */
let runAnswer: (() => any) | null = null;
g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asks.push({ url: u, method });
  if (method === "POST" && u.includes("/api/flows/f1/run")) {
    if (runAnswer) return runAnswer();
    return { ok: true, status: 200, statusText: "OK", json: async () => ({ started: true, flow_id: "f1", frames: 10, unmapped: [] }) };
  }
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};
const runPosts = () => asks.filter((a) => a.method === "POST" && a.url.includes("/api/flows/f1/run"));

// ------------------------------------------------------------------ imports
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { FlowCardSheet } = await import("../flow");
const { ConfirmCard } = await import("../../../../shell/ConfirmCard");

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

// ------------------------------------------------------------------ fixture
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount"],
};
const RECORD = {
  id: "f1", name: "M31 mosaic", folder: "My flows", tagline: "",
  graph: { nodes: [], edges: [] }, created_ts: 0, updated_ts: 0,
  last_run: null, last_result: "" as const, readonly: false,
};

/** A store where a run on THIS page ended a while ago: `flows.run.phase` is
 *  still "running" (the un-owned half of #162 - nothing resets it), but no
 *  camera is connected and the rig's own published state names no live run of
 *  this flow, so neither label nor lock should trust the latch. */
function seed(): void {
  const flows = useStore.getState().flows;
  useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    confirm: null,
    toasts: [],
    status: { connected: { camera: { connected: false, name: null } } },
    sequence: { state: "idle" },
    flows: {
      ...flows,
      record: RECORD,
      graph: RECORD.graph,
      sessionIds: ["sess-old"],
      compiled: { plan: {}, structural: [], issues: [], unmapped: [] },
      compiling: false,
      run: { ...flows.run, phase: "running" },
    },
  } as never);
}

const container = win.document.getElementById("root") as any;
let root: any = null;
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
async function mount(): Promise<void> {
  if (root) await act(async () => { root.unmount(); });
  win.location.hash = "#/sky/quick/flow?id=f1";
  asks = [];
  seed();
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(Fragment, null,
      createElement(FlowCardSheet, { key: "a", params: { id: "f1" }, depth: 1 as const }),
      createElement(ConfirmCard, { key: "b" })));
  });
  await settle();
}
const btn = () => container.querySelector('[data-testid="flow-run"]') as any;

// ============================================== 1. THE LABEL AND THE LOCK
//
// MUTANT "running reads the latch" (flow.tsx: `running` reverted to
// `isRunPhaseLive(phase)`). Observed, byte-for-byte restored and
// sha256-compared afterward:
//   x after a run on this page has ended, with no live run of this flow, the
//     card reads RUN NOW and needs a camera: the button still read RUN IN
//     PROGRESS off the stale latch, with no camera required to press it
//     expected "RUN NOW"
//     got      "RUN IN PROGRESS"
await testAsync("after a run on this page has ended, with no live run of this flow, the card reads RUN NOW and needs a camera", async () => {
  await mount();
  eq(useStore.getState().flows.run.phase, "running", "precondition: the stale latch reads running");
  eq(btn().textContent, "RUN NOW",
    "the button still read RUN IN PROGRESS off the stale latch, with no camera required to press it");
  eq(btn().getAttribute("aria-disabled"), "true", "no camera is connected, so RUN NOW must be locked");
  assert(/No camera is connected/.test(btn().getAttribute("title") ?? ""),
    "the locked reason must name the missing camera, not treat this as an already-live STOP");
});

// ==================================================== 2. THE POST-PRESS READ
//
// MUTANT "started reads the latch" (flow.tsx: the before/after compare
// reverted to the bare `started()` read of `flows.run.phase`). Observed:
//   x a press the engine refuses, after an earlier run on this page has ended,
//     does not navigate away and says so: the stale latch from the earlier run
//     made a REFUSED press read as a start
//     expected "#/sky/quick/flow?id=f1"
//     got      "#/session/now"
await testAsync("a press the engine refuses, after an earlier run on this page has ended, does not navigate away and says so", async () => {
  // Give this press a camera, so the only thing under test is the refusal
  // itself, not the lock from case 1.
  act(() => {
    useStore.setState({ status: { connected: { camera: { connected: true, name: "sim" } } } } as never);
  });
  await settle();
  runAnswer = () => ({
    ok: false, status: 409, statusText: "Conflict",
    json: async () => ({ detail: { code: "session_changed", detail: "a session is already live" } }),
  });
  const before = win.location.hash;
  await act(async () => {
    btn().dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
  eq(runPosts().length, 1, "precondition: the press reached the run route");
  eq(win.location.hash, before,
    "the stale latch from the earlier run made a REFUSED press read as a start");
  const toasts = (useStore.getState() as any).toasts ?? [];
  assert(toasts.some((t: any) => /did not start/i.test(t.title ?? "")),
    "a refused press must say so, not navigate away in silence");
  runAnswer = null;
});

// ------------------------------------------------------------------- report
if (root) await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`w2FlowCardRunIdentity.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
