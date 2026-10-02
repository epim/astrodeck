// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w4SelfWireAndLaneChecks.test.ts - flowsConnect runs the lane and self-wire
// checks for BOTH gestures, and the self-wire toast exists (#197; backlog
// plan WP-36 (a), docs/superpowers/plans/2026-09-30-open-issue-backlog.md).
//
//   Run:  node --import tsx src/components/flows/__tests__/w4SelfWireAndLaneChecks.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT (#197). Both drop resolvers (classic FlowCanvas.tsx
// `resolveWireDrop`, #/next canvasModel.ts's copy) refuse a self-wire
// SILENTLY and a lane mismatch WITH A SENTENCE before `flowsConnect` is ever
// called - but tap-to-wire (`flowsTapPort`) has no resolver of its own and
// called `flowsConnect` directly with NEITHER check applied. So a tap could
// wire a stage to itself, or a flow output straight into an event input (a
// graph the server's own FlowGraph.validation_errors refuses at save), with
// nothing said at the time. `FlowCanvas.tsx`'s own rule-3 comment claimed tap
// toasts "Can't wire a stage to itself" - true nowhere in ui/src before this.
//
// THE FIX. `flowsConnect` now runs both checks itself (`flowLoop.ts`'s
// `laneMismatchRefusal` and a bare `from === to` test), so every gesture gets
// the same answer whether or not it passed through a resolver first. A drag
// never trips either branch in practice - the resolvers' own rules 3 and 4
// filter both cases out before `flowsConnect` is ever called - so the cases
// below exercise what only tap (or a bare `flowsConnect` call, which is what
// a resolver is standing in front of) can reach.
//
// Every mutant below was run in a private scratch copy of ui/ under the
// session scratchpad, restored byte-identically (sha256 checked) afterwards,
// never in the shared tree. The failure it produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- globals first
// flowsSlice.ts imports lib/flowsApi -> lib/api -> lib/base, and base.ts reads
// `window.location.pathname` AT MODULE SCOPE to derive the relay mount - the
// same minimal stub flowsSlice.test.ts uses, no jsdom needed since nothing
// here touches the DOM.
(globalThis as any).window = {
  location: { pathname: "/", origin: "http://local" },
};
(globalThis as any).localStorage = {
  getItem: () => null, setItem() {}, removeItem() {},
};

const { createFlowsActions, FLOWS_INIT } = await import("../flowsSlice");
const { SELF_WIRE_REFUSAL } = await import("../flowLoop");
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;
type FlowToast = import("../flowsSlice").FlowToast;
type FlowGraphRec = import("../flowsTypes").FlowGraphRec;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

/** A miniature store, the same set/get contract zustand hands the slice
 *  (flowsSlice.test.ts's own harness), with a spy standing in for the
 *  store's one toast model so the self-wire toast can be counted exactly. */
function harness(graph: FlowGraphRec) {
  let state: FlowsHost;
  const toasts: FlowToast[] = [];
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    state = { ...state, ...fn(state) } as FlowsHost;
  };
  const get = () => state;
  const actions = createFlowsActions(set, get);
  state = {
    ...actions,
    enqueueToast: (t: FlowToast) => { toasts.push(t); },
    flows: { ...FLOWS_INIT, graph },
  } as FlowsHost;
  return {
    get flows(): FlowsState { return state.flows; },
    a: actions,
    toasts,
  };
}

// CAPTURE LOOP: a flow output ("complete") and a flow input ("run") on the
// same node - the smallest self-wire there is, the same fixture
// flowLoop.test.ts's own self-wire case uses for `flowLoopRefusal`.
const SELF_GRAPH: FlowGraphRec = {
  nodes: [{ id: "k", type: "capture" as never, x: 0, y: 0, params: {} }],
  edges: [],
};

// TARGET POOL "advance" is an EVENT input; FILTER CYCLE "complete" is a FLOW
// output - the exact pair flowLoop.test.ts's own lane-mismatch case uses, and
// the one #197 names ("tapping a flow output onto an event input").
const LANE_GRAPH: FlowGraphRec = {
  nodes: [
    { id: "p", type: "pool" as never, x: 0, y: 0, params: {} },
    { id: "cy", type: "cycle" as never, x: 250, y: 0, params: {} },
  ],
  edges: [],
};

// ========================================================== 1. self-wire

test("tap: a stage wired into itself is refused with the toast, and connects nothing", () => {
  // MUTANT "no self-wire check in flowsConnect" (the `if (from === to) {...}`
  // block removed from flowsSlice.ts flowsConnect). Observed (2 failed; this
  // case and the bare-call case below):
  //   x tap: a stage wired into itself is refused with the toast, and connects nothing: a self-wire must raise exactly one toast
  //     expected 1
  //     got      0
  const h = harness(SELF_GRAPH);
  h.a.flowsTapPort("k", "complete", "out");
  eq(h.flows.tapWire?.from, "k", "precondition: the output armed the tap");
  h.a.flowsTapPort("k", "run", "in");
  eq(h.flows.tapWire, null, "the arm is spent either way");
  eq(h.toasts.length, 1, "a self-wire must raise exactly one toast");
  eq(h.toasts[0]?.title, SELF_WIRE_REFUSAL,
    "the toast must be the one FlowCanvas.tsx's own comment already promised");
  eq(h.toasts[0]?.level, "warning", "the toast is a warning, not news");
  eq(h.flows.graph.edges.length, 0, "a self-wire must not connect");
  eq(h.flows.dirty, false, "a refused wire must not mark the flow as edited");
});

test("flowsConnect refuses a self-wire from ANY caller, not only a tap", () => {
  // Proves the check lives in flowsConnect itself (#197's fix shape: "runs
  // the lane and self-wire checks for both drag and tap"), not in the tap
  // arm/disarm dance above it. A drop resolver never reaches this branch in
  // practice - its own rule 3 refuses the same case first - but the store's
  // guard must still hold for whatever calls flowsConnect directly.
  //
  // MUTANT "no self-wire check in flowsConnect" (same as above). Observed:
  //   x flowsConnect refuses a self-wire from ANY caller, not only a tap: a bare flowsConnect(k, k) call must raise the toast too
  //     expected 1
  //     got      0
  const h = harness(SELF_GRAPH);
  h.a.flowsConnect("k", "complete", "k", "run");
  eq(h.toasts.length, 1, "a bare flowsConnect(k, k) call must raise the toast too");
  eq(h.flows.graph.edges.length, 0, "and must connect nothing");
});

// ====================================================== 2. lane mismatch

test("tap: a flow output onto an event input leaves the graph identical and logs the lane sentence (#197)", () => {
  // The exact case #197's suggested fix names as the test to write.
  //
  // MUTANT "no lane check on the tap path" (the `const lane =
  // laneMismatchRefusal(...); if (lane) {...}` block removed from
  // flowsSlice.ts flowsConnect). Observed (3 failed; this case, the reverse
  // direction below and the bare-call case):
  //   x tap: a flow output onto an event input leaves the graph identical and logs the lane sentence (#197): a lane mismatch must connect nothing, edges now: cy.complete->p.advance
  const h = harness(LANE_GRAPH);
  const graph = h.flows.graph;
  h.a.flowsTapPort("cy", "complete", "out");
  h.a.flowsTapPort("p", "advance", "in");
  eq(h.flows.tapWire, null, "the arm is spent either way");
  eq(h.flows.graph === graph, true,
    `a lane mismatch must connect nothing, edges now: ${h.flows.graph.edges.map((e) => `${e.from}.${e.fromPort}->${e.to}.${e.toPort}`).join(", ")}`);
  eq(h.flows.dirty, false, "a refused wire must not mark the flow as edited");
  const last = h.flows.logs[h.flows.logs.length - 1];
  eq(last?.msg, "Flow output can't feed an event input",
    "the lane sentence must reach the flow log, the same words a drag drop toasts");
  eq(last?.tone, "warn", "the refusal is a warning");
  // The lane check and the self-wire toast are two different gates: a lane
  // mismatch is logged, not toasted (tap has no toast wired for it, same as
  // the loop refusal beside it), so no toast should appear here.
  eq(h.toasts.length, 0, "a lane mismatch is logged, not toasted");
});

test("tap: an event output onto a flow input is refused the same way, the other direction", () => {
  // CONTROL for direction: CONDITION "fire" is an event output; TARGET
  // "arm" is a flow input.
  const h = harness({
    nodes: [
      { id: "co", type: "condition" as never, x: 0, y: 0, params: {} },
      { id: "t", type: "target" as never, x: 250, y: 0, params: {} },
    ],
    edges: [],
  });
  h.a.flowsTapPort("co", "fire", "out");
  h.a.flowsTapPort("t", "arm", "in");
  eq(h.flows.graph.edges.length, 0, "an event output must not feed a flow input either");
  eq(h.flows.logs[h.flows.logs.length - 1]?.msg, "Event output can't feed a flow input",
    "the reverse sentence");
});

test("flowsConnect refuses a lane mismatch from ANY caller, not only a tap", () => {
  // MUTANT "no lane check on the tap path" (same as above). Observed:
  //   x flowsConnect refuses a lane mismatch from ANY caller, not only a tap: a bare flowsConnect call across lanes must connect nothing
  //     expected 0
  //     got      1
  const h = harness(LANE_GRAPH);
  h.a.flowsConnect("cy", "complete", "p", "advance");
  eq(h.flows.graph.edges.length, 0, "a bare flowsConnect call across lanes must connect nothing");
});

// ============================================================= controls

test("CONTROL: a matching lane and two different nodes still connect, and say nothing", () => {
  // Neither new gate may refuse an ordinary wire - the mutant "refuse every
  // tap" would pass every case above and only this one would catch it.
  const h = harness({
    nodes: [
      { id: "t", type: "target" as never, x: 0, y: 0, params: {} },
      { id: "c", type: "capture" as never, x: 250, y: 0, params: {} },
    ],
    edges: [],
  });
  h.a.flowsTapPort("t", "target", "out");
  h.a.flowsTapPort("c", "run", "in");
  eq(h.flows.graph.edges.length, 1, "an ordinary tap-to-wire must still connect");
  eq(h.toasts.length, 0, "a successful connect is silent");
});

// ------------------------------------------------------------------ report
console.log(`\nw4SelfWireAndLaneChecks: ${passed} passed, ${failed} failed`);
if (failures.length) { failures.forEach((f) => console.log(f)); process.exit(1); }
export default { passed, failed, total: passed + failed };
