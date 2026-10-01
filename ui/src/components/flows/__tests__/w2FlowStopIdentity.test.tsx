// w2FlowStopIdentity.test.tsx - the classic STOP never aborts a run this flow
// did not start (#162).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w2FlowStopIdentity.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `useFlowRunControls`'s `act()` decided START vs STOP on
// `running`, which is `isRunPhaseLive(flows.run.phase) || ours` - a DISPLAY
// flag, OR'd with the client's own phase latch so the button reads STOP the
// instant a press lands, before the engine's first publish. Nothing ever
// resets that latch (NowEmpty's RUN_PHASE_GRACE_MS is the one path that does,
// and only for its own RUN press), so once a run on this page has ended,
// `running` stays true forever and `act()` kept calling `stop()`: a STOP press
// posts `/api/sequence/abort` to whatever the engine is doing NEXT, including a
// plan started elsewhere. `ours` (`flowRunLive` over `knownSessions`) is the
// half of `running` that is grounded in the rig's own state - the session the
// sequence state publishes now is one of THIS flow's - so `act()` is fixed to
// decide the ACTION on `ours`, leaving `running`'s DISPLAY untouched.
//
// THE FIX SHAPE (backlog ruling, WP-16 (a), owner-approved 2026-09-30): "The
// classic STOP ... compare[s] run identity, the pattern of the Send-to-Wizard
// fix [#454], so STOP never posts an abort to a run this flow did not start."

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
g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asks.push({ url: u, method });
  if (method === "POST" && u.includes("/api/sequence/abort")) {
    return { ok: true, status: 200, statusText: "OK", json: async () => ({ stopped: true }) };
  }
  if (method === "POST" && u.includes("/api/flows/f1/run")) {
    return { ok: true, status: 200, statusText: "OK", json: async () => ({ started: true, flow_id: "f1", frames: 10, unmapped: [] }) };
  }
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};
const posted = (path: string) => asks.filter((a) => a.method === "POST" && a.url.includes(path));

// ------------------------------------------------------------------ imports
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { useFlowRunControls } = await import("../flowRunControls");
const { ConfirmCard } = await import("../../../next/shell/ConfirmCard");

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

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

/** Seeds a store where a run on THIS page ended a while ago: the client's own
 *  phase latch never reset (that is #162's other, un-owned half), but the
 *  session this flow knows as its own ("sess-old") is NOT the one the engine
 *  reports live now - `otherLive` names a DIFFERENT session, live, or null for
 *  no live run anywhere. */
function seed(otherLive: string | null): void {
  const flows = useStore.getState().flows;
  useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    confirm: null,
    toasts: [],
    status: { connected: { camera: { connected: true, name: "sim" } } },
    sequence: otherLive === null
      ? { state: "idle" }
      : { state: "running", session: { id: otherLive } },
    flows: {
      ...flows,
      record: RECORD,
      graph: RECORD.graph,
      sessionIds: ["sess-old"],
      // THE LATCH: a run started and finished on this page. Nothing resets
      // this - it is the half of #162 this WP does not own.
      run: { ...flows.run, phase: "running" },
    },
  } as never);
}

/** The shared RUN/STOP hook, bare. */
function Probe(): any {
  const c = useFlowRunControls();
  return createElement("button", { "data-testid": "probe-act", onClick: c.act }, c.running ? "STOP" : "RUN");
}

const container = win.document.getElementById("root") as any;
let root: any = null;
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
async function mount(otherLive: string | null): Promise<void> {
  if (root) await act(async () => { root.unmount(); });
  asks = [];
  seed(otherLive);
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(Fragment, null,
      createElement(Probe, { key: "a" }), createElement(ConfirmCard, { key: "b" })));
  });
  await settle();
}
async function press(): Promise<void> {
  const btn = container.querySelector('[data-testid="probe-act"]');
  assert(btn != null, "no probe button to press");
  await act(async () => {
    btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
}

// ===================================================== 1. THE DEFECT, FIXED
//
// MUTANT "act decides on running" (flowRunControls.tsx: `act`'s
// `void (ours ? stop() : start())` reverted to `void (running ? stop() : start())`).
// Observed, byte-for-byte restored and sha256-compared afterward:
//   x the latch alone, with no live run anywhere, never posts an abort: an
//     abort was posted to a run this flow did not start/was not running
//     expected []
//     got      [{"url":"/api/sequence/abort","method":"POST"}]
await testAsync("the latch alone, with no live run anywhere, never posts an abort", async () => {
  await mount(null);
  assert(useStore.getState().flows.run.phase === "running",
    "precondition: the stale latch reads running");
  await press();
  const aborts = posted("/api/sequence/abort").map((a) => ({ url: a.url, method: a.method }));
  assert(aborts.length === 0,
    `an abort was posted to a run this flow did not start/was not running\n  expected []\n  got      ${JSON.stringify(aborts)}`);
});

// Same defect, worse: ANOTHER flow's run is genuinely live, and the stale
// latch still makes the button read (and act like) STOP over it.
await testAsync("the latch alone, over a DIFFERENT flow's live run, never posts an abort to it", async () => {
  await mount("sess-other");
  await press();
  const aborts = posted("/api/sequence/abort");
  assert(aborts.length === 0,
    "an abort was posted to another flow's live run, which this STOP has no business touching");
  // The press fell through to START instead (the engine is free to refuse it,
  // which is harmless and already logged - never silent and never a stranger's
  // abort).
  assert(posted("/api/flows/f1/run").length === 1,
    "a press that is not really a STOP should try to START, not do nothing silently");
});

// ================================================================ 2. CONTROL
//
// A real, live run of THIS flow is still stopped. Without this control, a fix
// that made `act` never abort (rather than deciding correctly) would pass the
// two cases above for the wrong reason.
await testAsync("CONTROL: a live run that really is this flow's is still stopped", async () => {
  await mount("sess-old");
  await press();
  eq(posted("/api/sequence/abort").length, 1, "a real STOP must still post the abort");
});
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

// ------------------------------------------------------------------- report
if (root) await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`w2FlowStopIdentity.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
