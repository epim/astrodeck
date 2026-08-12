// flowsSlice.test.ts — the graph-edit rules and the write discipline.
//   Run:  npx tsx src/components/flows/__tests__/flowsSlice.test.ts   (from ui/)
//
// Two kinds of assertion here, and the second kind is the point.
//
// The first kind checks the graph rules: one wire per input, a deleted node
// takes its wires, param coercion keyed off the DEFAULT's type. Each of those
// is a rule the SERVER also enforces, so getting it wrong here produces a graph
// that saves and then refuses to load.
//
// The second kind checks OBJECT IDENTITY. The README requires that a node
// status tick re-render one node rather than the canvas, and that holds only
// because every writer replaces one sub-object and leaves its siblings alone.
// Nothing about that is visible in behaviour — a slice that rebuilt the whole
// `flows` object on every write would pass every functional test in this file
// and silently re-render everything. Identity is the only way to see it.
/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- globals first
// The slice imports lib/flowsApi -> lib/api -> lib/base, and base.ts reads
// `window.location.pathname` AT MODULE SCOPE to derive the relay mount. So the
// import has to happen after a window exists, which means a dynamic import.
// Nothing here fakes behaviour the tests then assert on - no request is made.
(globalThis as any).window = {
  location: { pathname: "/", origin: "http://local" },
};
(globalThis as any).localStorage = {
  getItem: () => null, setItem() {}, removeItem() {},
};

const { createFlowsActions, FLOWS_INIT, LOG_RING } = await import("../flowsSlice");
const { NODE_DEFS } = await import("../nodeDefs");
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** A miniature store: the same set/get contract zustand hands the slice. */
function harness() {
  let state: FlowsHost;
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    state = { ...state, ...fn(state) } as FlowsHost;
  };
  const get = () => state;
  const actions = createFlowsActions(set, get);
  state = { ...actions, flows: { ...FLOWS_INIT } } as FlowsHost;
  return {
    get flows(): FlowsState { return state.flows; },
    a: actions,
  };
}

function withGraph(h: ReturnType<typeof harness>, nodes: string[]) {
  nodes.forEach((t, i) =>
    h.a.flowsAddNode(t as never, { x: i * 200, y: 0 }));
  return h.flows.graph.nodes.map((n) => n.id);
}

// ───────────────────────────────────────────────────────────── graph rules

test("one wire per input — a second wire into the same port REPLACES the first", () => {
  const h = harness();
  const [a, b, c] = withGraph(h, ["target", "capture", "target"]);
  h.a.flowsConnect(a, "target", b, "run");
  h.a.flowsConnect(c, "target", b, "run");
  const into = h.flows.graph.edges.filter((e) => e.to === b && e.toPort === "run");
  assert(into.length === 1, `expected 1 wire into run, got ${into.length}`);
  assert(into[0].from === c, "the LATER wire is the one that survives");
});

test("a different input port on the same node is untouched", () => {
  const h = harness();
  const [a, b, c] = withGraph(h, ["target", "capture", "cloudwatch"]);
  h.a.flowsConnect(a, "target", b, "run");
  h.a.flowsConnect(c, "in", b, "run2");
  assert(h.flows.graph.edges.length === 2, "replace must be per (node, port)");
});

test("deleting a node takes its wires with it", () => {
  // An edge left pointing at a node that is gone is exactly what the server's
  // FlowGraph.validation_errors() refuses — the graph would stop compiling
  // because of an action the operator took on purpose.
  const h = harness();
  const [a, b] = withGraph(h, ["target", "capture"]);
  h.a.flowsConnect(a, "target", b, "run");
  h.a.flowsSelect({ kind: "node", id: a });
  h.a.flowsDeleteSel();
  assert(h.flows.graph.nodes.length === 1, "the node is gone");
  assert(h.flows.graph.edges.length === 0, "and so is the wire that pointed at it");
});

test("deleting an edge leaves both its nodes alone", () => {
  const h = harness();
  const [a, b] = withGraph(h, ["target", "capture"]);
  h.a.flowsConnect(a, "target", b, "run");
  h.a.flowsSelect({ kind: "edge", id: h.flows.graph.edges[0].id });
  h.a.flowsDeleteSel();
  assert(h.flows.graph.nodes.length === 2, "nodes survive");
  assert(h.flows.graph.edges.length === 0, "edge gone");
});

test("deleting the node being edited closes the sheet", () => {
  const h = harness();
  const [a] = withGraph(h, ["target"]);
  h.a.flowsSetEditNode(a);
  h.a.flowsSelect({ kind: "node", id: a });
  h.a.flowsDeleteSel();
  assert(h.flows.editNode === null,
    "an edit sheet left open over a deleted node edits nothing");
});

// ───────────────────────────────────────────────────────── param coercion

test("a numeric field parses, and reverts to its default on garbage", () => {
  const h = harness();
  const [c] = withGraph(h, ["capture"]);
  h.a.flowsSetParam(c, "exposure", "240");
  assert(h.flows.graph.nodes[0].params.exposure === 240, "parsed to a number");
  h.a.flowsSetParam(c, "exposure", "banana");
  assert(h.flows.graph.nodes[0].params.exposure === NODE_DEFS.capture.params.exposure,
    "unparseable input reverts to the default, never NaN — a NaN here reaches "
    + "the compiler as a step with no exposure time");
});

test("capture.bin stays a STRING, because its default is one", () => {
  // The coercion keys off the type of the DEFAULT. bin is a select whose
  // options are strings; coercing it to a number would make the control stop
  // matching its own options.
  const h = harness();
  const [c] = withGraph(h, ["capture"]);
  h.a.flowsSetParam(c, "bin", "2");
  assert(h.flows.graph.nodes[0].params.bin === "2",
    `expected the string "2", got ${JSON.stringify(h.flows.graph.nodes[0].params.bin)}`);
});

test("a new node starts from the vocabulary's defaults, not an empty object", () => {
  const h = harness();
  withGraph(h, ["capture"]);
  assert(
    JSON.stringify(h.flows.graph.nodes[0].params)
      === JSON.stringify(NODE_DEFS.capture.params),
    "a node with no params renders every field blank and compiles to nothing");
});

// ─────────────────────────────────────────────────────────────── tap-to-wire

test("tap-to-wire arms only from an OUTPUT", () => {
  const h = harness();
  const [a, b] = withGraph(h, ["target", "capture"]);
  h.a.flowsTapPort(b, "run", "in");
  assert(h.flows.tapWire === null,
    "arming from an input leaves the operator holding a wire with no source");
  h.a.flowsTapPort(a, "target", "out");
  assert(h.flows.tapWire?.from === a, "an output arms it");
});

test("tapping an input while armed connects and disarms", () => {
  const h = harness();
  const [a, b] = withGraph(h, ["target", "capture"]);
  h.a.flowsTapPort(a, "target", "out");
  h.a.flowsTapPort(b, "run", "in");
  assert(h.flows.tapWire === null, "disarmed");
  assert(h.flows.graph.edges.length === 1, "and the wire exists");
});

// ───────────────────────────────────────────────────────────────── viewport

test("FIT on an empty graph leaves the viewport alone", () => {
  // "Fit nothing" has no meaningful pan. Snapping to a default would yank the
  // canvas out from under someone who pressed FIT before adding a node.
  const h = harness();
  const before = h.flows.pan;
  h.a.flowsFit({ width: 1000, height: 600 });
  assert(h.flows.pan === before, "pan identity unchanged");
  assert(h.flows.zoom === FLOWS_INIT.zoom, "zoom unchanged");
});

test("FIT on a real graph moves the viewport", () => {
  const h = harness();
  withGraph(h, ["dusk", "target", "capture"]);
  h.a.flowsFit({ width: 1000, height: 600 });
  assert(h.flows.zoom > 0, "a zoom was chosen");
});

// ───────────────────────────────────── the write discipline (identity tests)

test("a pan does not change the graph's identity", () => {
  const h = harness();
  withGraph(h, ["target"]);
  const graph = h.flows.graph;
  h.a.flowsSetPan({ x: 99, y: 99 });
  assert(h.flows.graph === graph,
    "a pan that rewrote `graph` would re-render every node in the canvas");
});

test("a graph edit does not change the viewport's identity", () => {
  const h = harness();
  const pan = h.flows.pan;
  withGraph(h, ["target"]);
  assert(h.flows.pan === pan, "adding a node must not disturb the viewport");
});

test("a ui patch touches nothing but ui", () => {
  const h = harness();
  withGraph(h, ["target"]);
  const { graph, pan, run } = h.flows;
  h.a.flowsSetUi({ tonightOpen: true });
  assert(h.flows.graph === graph && h.flows.pan === pan && h.flows.run === run,
    "opening a panel must not re-render the canvas");
  assert(h.flows.ui.tonightOpen, "and it did what it said");
});

test("a selection change does not disturb the graph", () => {
  const h = harness();
  const [a] = withGraph(h, ["target"]);
  const graph = h.flows.graph;
  h.a.flowsSelect({ kind: "node", id: a });
  assert(h.flows.graph === graph, "selecting is not editing");
  assert(h.flows.dirty === true, "…though adding the node did dirty it");
});

// ───────────────────────────────────────────────────────────────────── log

test("the log is a ring and drops the oldest", () => {
  const h = harness();
  for (let i = 0; i < LOG_RING + 25; i++) h.a.flowsAppendLog(`line ${i}`);
  assert(h.flows.logs.length === LOG_RING, `ring held at ${LOG_RING}`);
  assert(h.flows.logs[0].msg === "line 25", "the oldest 25 fell off the front");
  assert(h.flows.logs[LOG_RING - 1].msg === `line ${LOG_RING + 24}`, "newest last");
});

test("dirty starts false and a graph edit sets it", () => {
  const h = harness();
  assert(h.flows.dirty === false, "a freshly opened flow is clean");
  withGraph(h, ["target"]);
  assert(h.flows.dirty === true, "…and an edit is what makes it dirty");
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failures.length) { failures.forEach((f) => console.log(f)); process.exit(1); }
