// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w14FlowRunFlushes.test.tsx - the classic RUN button saves the canvas before
// it starts a night, and an edited Example keeps it locked (#688, part 1 of 3;
// WP-86).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w14FlowRunFlushes.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. In the classic editor RUN posted `POST /api/flows/{id}/run`,
// which runs the STORED flow, with no save in front of it and no lock on an
// edited graph (only the #/next toolbar locked it). An operator who swapped a
// block and pressed RUN started the night on the graph they had just replaced,
// with the new one on screen. The store's `flowsRun` now saves first
// (`w14FlowsFlushBeforeRead.test.ts` pins that at the store); this file
// pins the DOOR: the shared `useFlowRunControls` hook, which the header, the
// phone MONITOR tab and every #/next RUN surface press, mounted against the
// real store with the PUT held open.
//
// The stubs replace `flowsApi`'s methods, not the network: the slice, the
// store and the hook are the production ones.
//
// Every case names the mutant it kills and quotes what that mutant produced
// when run from a byte-for-byte backup of the file named (restored and
// sha256-compared after each run).

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

// ------------------------------------------------------------------ imports
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { flowsApi } = await import("../../../lib/flowsApi");
const { useFlowRunControls } = await import("../flowRunControls");
const { RUN_UNSAVED_EXAMPLE_REASON, RUN_UNSAVED_REASON, unsavedRunReason } = await import("../flowsTypes");
const canvasModel = await import("../../../next/hubs/session/flows/canvas/canvasModel");
const { NODE_DEFS } = await import("../nodeDefs");
const { ConfirmCard } = await import("../../../next/shell/ConfirmCard");

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { await drain(); }
}
/** A PUT still held when a case ends (it failed before it answered it) is
 *  answered here: the slice keeps its save in a closure the whole file shares,
 *  and every later flush would wait on it for ever. */
async function drain(): Promise<void> {
  await act(async () => {
    for (const p of puts) p.resolve({ ...p.flow, updated_ts: 9 });
  });
  await act(async () => { await tick(); });
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
/** One macrotask: a NEGATIVE ("nothing was posted yet") is read after it. */
const tick = () => new Promise<void>((r) => setTimeout(r, 0));
async function until(cond: () => boolean, what: string, ms = 3000): Promise<void> {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await act(async () => { await tick(); });
  }
}

// ------------------------------------------------------------------ stubs
interface Put { flow: any; resolve: (v: unknown) => void; reject: (e: unknown) => void }
let puts: Put[] = [];
let runCalls = 0;
(flowsApi as any).save = (_id: string, flow: any) =>
  new Promise((resolve, reject) => { puts.push({ flow, resolve, reject }); });
(flowsApi as any).run = async () => {
  runCalls++;
  return { started: true, flow_id: "f1", frames: 10, unmapped: [] };
};
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };

// ------------------------------------------------------------------ fixture
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount"],
};
const STORED_GRAPH = {
  nodes: [{ id: "t1", type: "target", x: 0, y: 0, params: { ...NODE_DEFS.target.params } }],
  edges: [],
};
/** What the operator drew over it: the target gone, two filter cycles in. */
const CANVAS_GRAPH = {
  nodes: [
    { id: "c1", type: "cycle", x: 0, y: 0, params: { ...NODE_DEFS.cycle.params } },
    { id: "c2", type: "cycle", x: 0, y: 200, params: { ...NODE_DEFS.cycle.params } },
  ],
  edges: [],
};
function record(readonly: boolean) {
  return {
    id: "f1", name: "NGC7331 Preferential Filtering", folder: "My flows", tagline: "",
    graph: STORED_GRAPH, created_ts: 0, updated_ts: 0,
    last_run: null, last_result: "" as const, readonly,
  };
}

function seed(o: { dirty: boolean; readonly?: boolean; running?: boolean }): void {
  const flows = useStore.getState().flows;
  puts = [];
  runCalls = 0;
  act(() => { useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    confirm: null,
    toasts: [],
    status: { connected: { camera: { connected: true, name: "sim" } } },
    sequence: { state: "idle" },
    flows: {
      ...flows,
      record: record(o.readonly === true),
      graph: o.dirty ? CANVAS_GRAPH : STORED_GRAPH,
      dirty: o.dirty,
      sessionIds: [],
      logs: [],
      libraryError: null,
      run: { ...flows.run, phase: o.running ? "running" : "idle",
             startedAt: o.running ? Date.now() : null },
    },
  } as never); });
}

/** The shared RUN/STOP hook, bare: a button that presses `act`, and the lock
 *  reason the header and the monitor tab would print on it. */
function Probe(): any {
  const c = useFlowRunControls();
  return createElement(Fragment, null,
    createElement("button", {
      "data-testid": "probe-act", "data-reason": c.reason ?? "", onClick: c.act,
    }, c.running ? "STOP" : "RUN"),
    createElement("button", { "data-testid": "probe-start-over", onClick: c.startOver }, "START OVER"));
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 4; i++) await act(async () => { await tick(); });
};
async function mount(): Promise<void> {
  await act(async () => {
    root.render(createElement(Fragment, null,
      createElement(Probe, { key: "a" }), createElement(ConfirmCard, { key: "b" })));
  });
  await settle();
}
const btn = (): any => container.querySelector('[data-testid="probe-act"]');
const reasonOf = (): string => btn().getAttribute("data-reason");
async function press(testid = "probe-act"): Promise<void> {
  const el = container.querySelector(`[data-testid="${testid}"]`);
  assert(el != null, `no ${testid} to press`);
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}
const flowLog = (): string[] => (useStore.getState().flows.logs as any[]).map((l) => `${l.tone}: ${l.msg}`);
const toastTitles = (): string[] => (useStore.getState().toasts as any[]).map((t) => String(t.title));

await mount();

// ============================================================ 1. THE PRESS

// MUTANT "flowsRun skips the flush" (flowsSlice.ts: the `await
// saveBeforeRead();` ahead of `const after = get().flows;` in flowsRun made
// `void 0;`). Observed, 4/9 passed (every case that waits for the press's PUT
// times out, since none is sent):
//   x RUN on an edited flow does not post the run until its save has settled,
//     and the run is of the canvas: timed out waiting for the press's PUT
await testAsync("RUN on an edited flow does not post the run until its save has settled, and the run is of the canvas", async () => {
  seed({ dirty: true });
  await press();
  await until(() => puts.length === 1, "the press's PUT");
  await settle();
  eq(`PUT out, ${runCalls === 0 ? "no run posted yet" : `${runCalls} run(s) posted`}`,
    "PUT out, no run posted yet",
    "the run was posted while the PUT was still out: the rig would have started the stored flow");
  eq(puts[0].flow.graph.nodes.map((n: any) => n.type).join(","), "cycle,cycle",
    "the PUT did not carry the canvas");
  await act(async () => { puts[0].resolve({ ...puts[0].flow, updated_ts: 2 }); });
  await until(() => runCalls === 1, "the run to be posted once the PUT settled");
  eq(useStore.getState().flows.dirty, false, "the saved edit is still marked unsaved");
});

// MUTANT "the refusal removed" (flowsSlice.ts: `if (after.dirty) {` in flowsRun
// made `if (after.dirty && false) {`). Observed, 7/9 passed:
//   x RUN on a flow whose save failed starts nothing, says why on the flow log,
//     and says it where the operator is looking: a flow that did not save was
//     started, or its refusal went unsaid
//     expected "0 runs, bad: could not start: this flow has unsaved changes that did not save, toast Run not started"
//     got      "1 runs, , toast "
// MUTANT "the refusal without its toast" (flowsSlice.ts: the `enqueueToast`
// call in that block removed). Observed, 8/9 passed:
//     expected "0 runs, bad: could not start: this flow has unsaved changes that did not save, toast Run not started"
//     got      "0 runs, bad: could not start: this flow has unsaved changes that did not save, toast "
await testAsync("RUN on a flow whose save failed starts nothing, says why on the flow log, and says it where the operator is looking", async () => {
  seed({ dirty: true });
  await press();
  await until(() => puts.length === 1, "the press's PUT");
  await act(async () => { puts[0].reject(new Error("HTTP 422: a stage has no name")); });
  await settle();
  const refusal = flowLog().filter((l) => l.startsWith("bad: could not start"));
  const toast = toastTitles().includes("Run not started") ? "Run not started" : "";
  eq(`${runCalls} runs, ${refusal.join(" | ")}, toast ${toast}`,
    "0 runs, bad: could not start: this flow has unsaved changes that did not save, toast Run not started",
    "a flow that did not save was started, or its refusal went unsaid");
});

// MUTANT "no start latch" (flowRunControls.tsx: the `if (starting.current)
// return;` guard of `start` removed). Observed, 7/9 passed:
//   x a second press while the first press's save is out posts no second run:
//     the second press raced the first: the server would answer it with a
//     'could not start' under a night that did
//     expected "1 run(s)"
//     got      "2 run(s)"
// MUTANT "start never sets the latch" (the `starting.current = true;` line of
// `start` removed). Observed, 7/9 passed: the same case, same expected and got.
await testAsync("a second press while the first press's save is out posts no second run", async () => {
  seed({ dirty: true });
  await press();
  await until(() => puts.length === 1, "the first press's PUT");
  await press();
  await settle();
  await act(async () => { puts[0].resolve({ ...puts[0].flow, updated_ts: 2 }); });
  await until(() => runCalls >= 1, "a run to be posted");
  await settle();
  eq(`${runCalls} run(s)`, "1 run(s)",
    "the second press raced the first: the server would answer it with a 'could not start' under a night that did");
  // The latch is released: a press after the run settled is a press again.
  assert(btn() != null, "the probe is gone");
});

// MUTANT "START OVER never sets the latch" (flowRunControls.tsx: the
// `starting.current = true;` line of `startOver` removed, its guard left).
// Observed, 8/9 passed:
//   x a RUN pressed while START OVER's save is out posts no second run: RUN
//     raced START OVER's save: two starts, and the second is refused under a
//     night that did start
//     expected "1 run(s)"
//     got      "2 run(s)"
await testAsync("a RUN pressed while START OVER's save is out posts no second run", async () => {
  seed({ dirty: true });
  await press("probe-start-over");
  await until(() => container.querySelector('[data-testid="confirm-yes"]') !== null, "START OVER's confirm");
  await press("confirm-yes");
  await until(() => puts.length === 1, "START OVER's PUT");
  await press();
  await settle();
  await act(async () => { puts[0].resolve({ ...puts[0].flow, updated_ts: 2 }); });
  await until(() => runCalls >= 1, "a run to be posted");
  await settle();
  eq(`${runCalls} run(s)`, "1 run(s)",
    "RUN raced START OVER's save: two starts, and the second is refused under a night that did start");
});

// MUTANT "START OVER ignores the latch" (flowRunControls.tsx: the `if
// (starting.current) return;` guard of `startOver` removed, its set left).
// Observed, 8/9 passed:
//   x START OVER pressed while RUN's save is out asks nothing: no confirm opens
//     over a start that is already under way: START OVER offered to start a
//     night while RUN's start was still saving
//     expected "no confirm"
//     got      "a confirm opened"
await testAsync("START OVER pressed while RUN's save is out asks nothing: no confirm opens over a start that is already under way", async () => {
  seed({ dirty: true });
  await press();
  await until(() => puts.length === 1, "RUN's PUT");
  await press("probe-start-over");
  await settle();
  eq(container.querySelector('[data-testid="confirm-yes"]') === null ? "no confirm" : "a confirm opened",
    "no confirm", "START OVER offered to start a night while RUN's start was still saving");
  await act(async () => { puts[0].resolve({ ...puts[0].flow, updated_ts: 2 }); });
  await until(() => runCalls >= 1, "RUN to be posted");
  await settle();
  eq(`${runCalls} run(s)`, "1 run(s)", "the RUN that was under way did not post exactly once");
});

// ======================================================== 2. THE EXAMPLE LOCK

// MUTANT "no example lock on the hook" (flowRunControls.tsx: the `?? (running
// || !readonly ? null : unsavedRunReason(dirty, true))` arm of `reason`
// replaced by `?? null`). Observed, 8/9 passed:
//   x an edited example keeps RUN locked, with the sentence that names the way
//     out: RUN on an edited example is pressable: it would start the stored
//     example under the edits on screen
//     expected "This example flow cannot be saved, so RUN would start the stored version, not the one drawn here. Leave the flow and open it again to drop these edits."
//     got      ""
await testAsync("an edited example keeps RUN locked, with the sentence that names the way out", async () => {
  seed({ dirty: true, readonly: true });
  await settle();
  eq(reasonOf(), RUN_UNSAVED_EXAMPLE_REASON,
    "RUN on an edited example is pressable: it would start the stored example under the edits on screen");
});

// The lock is for the example alone, and for an EDITED one: three controls, so a
// lock that fired on every flow would pass the case above.
// MUTANT "the example lock fires on every flow" (flowRunControls.tsx:
// `unsavedRunReason(dirty, true)` made `unsavedRunReason(true, true)`).
// Observed, 8/9 passed:
//   x CONTROL: RUN is not locked for an unedited example, for an edited flow of
//     one's own, or for STOP: an unedited example was locked
//     expected ""
//     got      "This example flow cannot be saved, so RUN would start the stored version, not the one drawn here. Leave the flow and open it again to drop these edits."
await testAsync("CONTROL: RUN is not locked for an unedited example, for an edited flow of one's own, or for STOP", async () => {
  seed({ dirty: false, readonly: true });
  await settle();
  eq(reasonOf(), "", "an unedited example was locked");
  seed({ dirty: true, readonly: false });
  await settle();
  eq(reasonOf(), "", "an edited flow of one's own was locked: it is saved by the press, not refused");
  seed({ dirty: true, readonly: true, running: true });
  await settle();
  eq(reasonOf(), "", "STOP was locked by an unsaved example: a live run can always be stopped");
});

// MUTANT "the #/next toolbar words it its own way" (canvasModel.ts: the
// re-export of the three names from flowsTypes replaced by a local copy of
// `RUN_UNSAVED_EXAMPLE_REASON` with one word changed). Observed, 8/9 passed:
//   x the #/next toolbar and the classic header say one sentence about an edited
//     example: the toolbar's lock is worded differently from the header's
//     expected "This example flow cannot be saved, so RUN would start the stored version, not the one drawn here. Leave the flow and open it again to drop these edits."
//     got      "This example flow cannot be saved, so RUN would start the STORED version. /* MUTANT_W14 */"
await testAsync("the #/next toolbar and the classic header say one sentence about an edited example", async () => {
  eq(canvasModel.RUN_UNSAVED_EXAMPLE_REASON, RUN_UNSAVED_EXAMPLE_REASON,
    "the toolbar's lock is worded differently from the header's");
  eq(canvasModel.RUN_UNSAVED_REASON, RUN_UNSAVED_REASON, "the unsaved-flow lock is worded two ways");
  eq(canvasModel.unsavedRunReason(true, true), unsavedRunReason(true, true),
    "the two editors decide the example lock differently");
});

// The press goes straight to `act`, with none of the button's lock in front of
// it, so this is the store's own refusal of an Example that the lock would also
// have caught. Killed by the "refusal removed" mutant (7/9 passed):
//   x pressing RUN on an edited example posts nothing and sends no PUT: an
//     edited example was saved or started by a press that the lock should have
//     refused
//     expected "0 PUTs, 0 runs"
//     got      "0 PUTs, 1 runs"
await testAsync("pressing RUN on an edited example posts nothing and sends no PUT", async () => {
  seed({ dirty: true, readonly: true });
  await settle();
  await press();
  await settle();
  eq(`${puts.length} PUTs, ${runCalls} runs`, "0 PUTs, 0 runs",
    "an edited example was saved or started by a press that the lock should have refused");
});

// ------------------------------------------------------------------- report
act(() => root.unmount());
const total = passed + failed;
console.log(`w14FlowRunFlushes.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
