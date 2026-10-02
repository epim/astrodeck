// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w5StaleStopConfirm.test.tsx - the stale STOP (#647, W5 integration).
//
//   Run directly:  npx tsx src/next/hubs/session/flows/canvas/__tests__/w5StaleStopConfirm.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `flowsRun` (flowsSlice.ts) sets `flows.run.phase` to "running"
// optimistically the instant its POST returns, before the engine's first
// publish - and until this fix nothing ever reset it back once that run
// ended. `useFlowRunControls`'s `running` (flowRunControls.tsx:290, the line
// number #647 was filed against) read `isRunPhaseLive(phase) || ours`, so
// after a run on a page completed, the button kept reading STOP forever -
// including after navigating away and back - while `act()` (#162) always
// decided the ACTION on `ours` alone, which by then was false. A press on
// that stale STOP therefore called `start()`, and `FlowCanvasToolbar.runArm`
// skipped CONFIRM RUN because the LABEL read STOP: a single tap on a button
// that said STOP silently started a brand new run.
//
// THE FIX, in three parts (plan text quoted in each case below):
//   1. `flows.run.startedAt` stamps the optimistic guess; `running` trusts it
//      for only `RUN_PHASE_BRIDGE_MS`, and `flowsSlice.ts`'s `onSequence`
//      clears both fields outright the moment the server reports the open
//      flow's run has ended - a GLOBAL subscription, so it fires whether or
//      not this toolbar is mounted to see it happen.
//   2. `runArm` is gated on `stopsOnPress` (`useFlowRunControls`'s own
//      `ours`, the same source `act()` decides on), never on `copy.verb`.
//
// NAMED MUTANTS, each run from a byte backup of the file named and restored
// byte-identical (sha256 + grep verified):
//
//   M1 "`|| ours` precedence restored" (flowRunControls.tsx): `running`
//   reverted to `isRunPhaseLive(phase) || ours;`, dropping the
//   `RUN_PHASE_BRIDGE_MS` bound entirely. Observed:
//     x a completed run plus hash navigation back to the canvas shows RUN
//       and not running (#647): the stale STOP survived a remount: "STOP"
//
//   M2 "confirm skipped when the label reads STOP" (FlowCanvasToolbar.tsx):
//   `runArm`'s gate reverted to `if (copy.verb === "STOP") return
//   undefined;`. Observed:
//     x any start passes the confirm, even while the label reads STOP
//       (#647): the first tap already posted a run with no confirm - #647

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
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
const asked: { url: string; method: string }[] = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  asked.push({ url, method: init?.method ?? "GET" });
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => ({ ok: true, started: true, flow_id: "flow-m16", frames: 10, unmapped: [] }),
    text: async () => "{}",
  };
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { FlowCanvasToolbar } = await import("../FlowCanvasToolbar");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

// ------------------------------------------------------------------ fixture
const GRAPH = { nodes: [{ id: "n1", type: "dusk", x: 0, y: 0, params: {} }], edges: [] };
const RECORD = {
  id: "flow-m16", name: "M16 - full-service night", folder: "My flows",
  tagline: "dusk to dawn", graph: GRAPH, created_ts: 1, updated_ts: 2,
  last_run: null, last_result: "", readonly: false,
};
const ADMIN_CAPS = [
  "view.status", "view.preview", "control.mount", "control.capture",
  "view.site_derived",
];

function seed(flows: Record<string, unknown> = {}): void {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      principal: { role: "admin", email: null, caps: ADMIN_CAPS } as never,
      authGate: "open",
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      sequence: { state: "idle" } as never,
      flows: {
        ...s.flows,
        record: RECORD as never,
        graph: JSON.parse(JSON.stringify(GRAPH)),
        dirty: false,
        sel: null, editNode: null, wire: null, tapWire: null,
        statuses: {}, logs: [], compiled: null,
        progress: null, sessionIds: [],
        pan: { x: 20, y: 10 }, zoom: 0.5,
        run: { ...s.flows.run, phase: "idle", startedAt: null, etaS: null },
        ui: { ...s.flows.ui, notesOpen: false, logOpen: false },
        ...flows,
      } as never,
    } as never);
  });
}

async function mount(): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(FlowCanvasToolbar as any)); });
  await settle();
}

// ========================================== 1. the clear survives a remount
await testAsync(
  "a completed run plus hash navigation back to the canvas shows RUN and not running (#647)",
  async () => {
    // A real, live run of THIS flow.
    seed({
      run: { ...useStore.getState().flows.run, phase: "running", startedAt: Date.now() },
      sessionIds: ["sess-done"],
    });
    act(() => {
      useStore.setState({ sequence: { state: "running", session: { id: "sess-done" } } } as never);
    });
    await mount();
    assert(/STOP/.test(tid("flow-run").textContent),
      `premise: a live run of this flow must read STOP, got "${tid("flow-run").textContent}"`);

    // The server reports the run over. `flowsSlice.ts`'s `onSequence`
    // subscription is GLOBAL (wired once at store creation), so this fires
    // and clears `flows.run.phase` whether or not the canvas is mounted to
    // see it happen - exactly the gap between "the run ends" and "the
    // operator navigates back" that #647 was filed against.
    act(() => { useStore.setState({ sequence: { state: "complete" } } as never); });
    await settle();
    eq(useStore.getState().flows.run.phase, "idle",
      "flowsSlice.ts's onSequence did not clear the optimistic phase once the run ended");

    // Hash navigation away and back: a fresh mount of the same toolbar, the
    // way a route change unmounts and remounts it.
    await mount();

    eq(tid("flow-toolbar").getAttribute("data-flows-run"), "idle",
      "the optimistic phase still reads live after the server reported the run over");
    const run = tid("flow-run");
    assert(!/STOP/.test(run.textContent),
      `the stale STOP survived a remount: "${run.textContent}"`);
    assert(/RUN/.test(run.textContent), `the button does not read RUN: "${run.textContent}"`);
    eq(run.getAttribute("data-armed"), "false", "RUN must still be a two-tap arm after the remount");
  },
);

// ============================ 1b. the bridge itself is bounded, in isolation
//
// Test 1 above goes through a REAL server transition (`sequence` live, then
// not), which `flowsSlice.ts`'s `onSequence` clears `phase` for on its own -
// so that test alone cannot tell this fix apart from one that clears `phase`
// but never bounds it by time at all. This case removes the transition
// entirely: `phase` is seeded "running" with a `startedAt` already past
// `RUN_PHASE_BRIDGE_MS`, and `sequence` has been idle from the very first
// write - `onSequence` never saw a live run to clear, because (as far as this
// store is concerned) there never was one. Only the BRIDGE'S OWN bound can
// make this read RUN.
await testAsync(
  "an optimistic phase past the bridge window reads RUN with no transition ever observed (#647)",
  async () => {
    const { RUN_PHASE_BRIDGE_MS } = await import("../../../../../../components/flows/flowRunControls");
    seed({
      run: {
        ...useStore.getState().flows.run, phase: "running",
        startedAt: Date.now() - RUN_PHASE_BRIDGE_MS - 1_000,
      },
      sessionIds: [],
    });
    // `sequence` is set ONCE, already idle - no live-to-ended transition for
    // `onSequence` to ever have fired on.
    act(() => { useStore.setState({ sequence: { state: "idle" } } as never); });
    await mount();

    eq(tid("flow-toolbar").getAttribute("data-flows-run"), "running",
      "precondition: flowsSlice.ts's own clear fired with no transition to clear - this case tests nothing");
    const run = tid("flow-run");
    assert(!/STOP/.test(run.textContent),
      `an optimistic phase older than RUN_PHASE_BRIDGE_MS still reads STOP: "${run.textContent}"`);
    assert(/RUN/.test(run.textContent), `the button does not read RUN: "${run.textContent}"`);
    eq(run.getAttribute("data-armed"), "false", "RUN must be a two-tap arm");
  },
);

// ======================================= 2. the confirm agrees with the action
await testAsync(
  "any start passes the confirm, even while the label reads STOP (#647)",
  async () => {
    // The exact gap #647 exploited: `phase` is a FRESH optimistic "running"
    // (inside RUN_PHASE_BRIDGE_MS of its own startedAt), so `running` and
    // the copy's verb both read STOP - but no server answer has ever named
    // a live session of this flow's (`sessionIds` is empty, `sequence` is
    // idle), so the press `act()` takes is really a START.
    seed({
      run: { ...useStore.getState().flows.run, phase: "running", startedAt: Date.now() },
      sessionIds: [],
    });
    act(() => { useStore.setState({ sequence: { state: "idle" } } as never); });
    await mount();

    const run = tid("flow-run");
    assert(/STOP/.test(run.textContent),
      `premise: the label must read STOP for this case to test anything, got "${run.textContent}"`);
    eq(run.getAttribute("data-armed"), "false",
      "a press whose real action is a start must still be a two-tap arm, whatever the label reads");

    const posts = asked.filter((a) => a.method === "POST").length;
    click(run);
    await settle();

    eq(tid("flow-run").getAttribute("data-armed"), "true",
      "the first tap must arm, never fire the start outright");
    // The gate, not the wording: `copy.verb` can still read STOP this close
    // to the press (the display bridges it, same as the glyph), so the word
    // CONFIRM is what this case grades - that a confirmation step exists at
    // all for a press whose real action is a start, "whatever the label
    // says". `runArm`'s own test (canvasDom.test.tsx) already pins the exact
    // "CONFIRM RUN"/"CONFIRM CONTINUE" wording for the ordinary, non-bridging
    // cases.
    assert(/^CONFIRM /.test(String(tid("flow-run").textContent)),
      `the armed label must still read as a confirm, got "${tid("flow-run").textContent}"`);
    eq(asked.filter((a) => a.method === "POST").length, posts,
      "the first tap already posted a run with no confirm - #647");
  },
);

// ------------------------------------------------------------------- report
if (root) await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`w5StaleStopConfirm.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
