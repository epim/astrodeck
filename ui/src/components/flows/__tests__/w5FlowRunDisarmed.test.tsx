// w5FlowRunDisarmed.test.tsx - the classic flow run controls show D-04's
// disarmed-session warning for POST /api/flows/{id}/run (#643, W5
// integration, remainder of WP-65).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w5FlowRunDisarmed.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE GAP WP-65 LEFT (its own commit message): starting or resuming a run is
// a SINGLETON (`SequenceEngine.start`), so the route that starts one answers
// with a `disarmed` list naming every OTHER session it just turned
// auto-resume off for (#595, backlog ruling D-04). WP-65 wired this into the
// #/next Now empty state's two direct calls, but could not reach the classic
// flow run controls: their RUN path goes through the store action
// `flowsSlice.ts`'s `flowsRun`, which posted `POST /api/flows/{id}/run` and
// then discarded the response entirely on a successful start (`return
// null`), before `flowRunControls.tsx` - the file this warning has to be
// shown from - ever saw the `disarmed` field. WP-65 filed #643 for exactly
// this and left the call site untouched, per the hard rule against reaching
// into a file owned by a different package.
//
// THE FIX: `flowsRun` now returns `{ kind: "started", disarmed }` instead of
// `null` when (and only when) the response's `disarmed` list is non-empty;
// `runAnsweringQuestions` (flowRunControls.tsx) treats that as the end of the
// loop rather than a question, and hands it back on `RunOutcome.disarmed`;
// `useFlowRunControls`'s `start`/`startOver` show it as a warning toast
// worded by the same `lib/disarmed.ts` helper `NowEmpty.tsx` uses, so the two
// surfaces can never say it differently.
//
// NAMED MUTANT, run from a byte backup of flowsSlice.ts and restored
// byte-identical (sha256 + grep verified): `flowsRun`'s
//   return disarmed && disarmed.length > 0 ? { kind: "started", disarmed } : null;
// reverted to a bare `return null;` (WP-65's original discard). Observed:
//   x RUN on a flow that disarms another session shows a warning naming it
//     (#643): no new disarmed-warning toast after a start that disarmed one: []

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
// `/api/flows/f1/run` answers `disarmed` only on its FIRST call, so the same
// mounted probe can also prove the negative control: a later start with no
// `disarmed` key adds no second warning.
interface Ask { url: string; method: string }
let asks: Ask[] = [];
let runCalls = 0;
g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asks.push({ url: u, method });
  if (method === "POST" && u.includes("/api/flows/f1/run")) {
    runCalls++;
    const out: any = { started: true, flow_id: "f1", frames: 10, unmapped: [] };
    if (runCalls === 1) out.disarmed = [{ id: "sess-3", name: "Deep sky log" }];
    return { ok: true, status: 200, statusText: "OK", json: async () => out };
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

function seed(): void {
  const flows = useStore.getState().flows;
  useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    confirm: null,
    toasts: [],
    status: { connected: { camera: { connected: true, name: "sim" } } },
    sequence: { state: "idle" },
    flows: {
      ...flows,
      record: RECORD,
      graph: RECORD.graph,
      sessionIds: [],
      run: { ...flows.run, phase: "idle" },
    },
  } as never);
}

/** The shared RUN/STOP hook, bare - same probe shape as w2FlowStopIdentity. */
function Probe(): any {
  const c = useFlowRunControls();
  return createElement("button", { "data-testid": "probe-act", onClick: c.act }, c.running ? "STOP" : "RUN");
}

/** The warning toasts naming a disarmed session - never the plain "RUN
 *  started" line, which this screen does not even print (flowRunControls has
 *  no success toast of its own; the flow log line is the only other record). */
function disarmedToasts(): Array<{ title: string; detail?: string }> {
  const toasts = useStore.getState().toasts as Array<{ level: string; title: string; detail?: string }>;
  return toasts.filter((t) => t.level === "warning" && /disarmed/i.test(t.title));
}

const container = win.document.getElementById("root") as any;
let root: any = null;
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
async function mount(): Promise<void> {
  asks = [];
  runCalls = 0;
  seed();
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

await mount();

await testAsync(
  "precondition: the probe is idle and RUN is what a press does",
  async () => {
    assert(container.querySelector('[data-testid="probe-act"]').textContent === "RUN",
      "the probe does not start out reading RUN");
  },
);

// ====================================================== 1. RUN, disarms one
await testAsync(
  "RUN on a flow that disarms another session shows a warning naming it (#643)",
  async () => {
    const before = disarmedToasts().length;
    await press();

    assert(posted("/api/flows/f1/run").length === 1, "RUN did not POST the run route");
    const warnings = disarmedToasts();
    assert(warnings.length === before + 1,
      `no new disarmed-warning toast after a start that disarmed one: ${JSON.stringify(warnings)}`);
    const w = warnings[warnings.length - 1];
    assert((w.detail ?? "").includes("Deep sky log"),
      `the warning does not name the disarmed session: "${w.detail}"`);
    assert((w.detail ?? "") === "Auto-resume was turned off for: Deep sky log.",
      `the warning does not use the shared lib/disarmed.ts wording: "${w.detail}"`);
  },
);

// =============================================== 2. control: nothing to say
await testAsync(
  "a SECOND run with no disarmed key adds no second warning (control)",
  async () => {
    // A fresh probe: the first run above left `flows.run.phase === "running"`,
    // which this suite's own #162 fix (w2FlowStopIdentity) means `ours` -
    // never that stale phase alone - decides the next press. Re-seeding idle
    // keeps this case about the disarmed warning, not about run identity.
    if (root) await act(async () => { root.unmount(); });
    await mount();
    // Burn the first (disarming) call so the SECOND is the one under test.
    await press();
    if (root) await act(async () => { root.unmount(); });
    seed();
    root = createRoot(container);
    await act(async () => {
      root.render(createElement(Fragment, null,
        createElement(Probe, { key: "a" }), createElement(ConfirmCard, { key: "b" })));
    });
    await settle();

    const before = disarmedToasts().length;
    await press();
    assert(posted("/api/flows/f1/run").length === 2, "the second RUN did not POST");
    assert(disarmedToasts().length === before,
      "a run that disarmed nobody still produced a disarmed-warning toast");
  },
);

// ------------------------------------------------------------------- report
if (root) await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`w5FlowRunDisarmed.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
