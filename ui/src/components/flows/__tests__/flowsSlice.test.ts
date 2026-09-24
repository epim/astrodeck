// flowsSlice.test.ts — the graph-edit rules and the write discipline.
//   Run:  npx tsx src/components/flows/__tests__/flowsSlice.test.ts   (from ui/)
//
// Two kinds of assertion here, and the second kind is the point.
//
// The first kind checks the graph rules: one wire per flow input, a deleted node
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
type FlowGraphRec = import("../flowsTypes").FlowGraphRec;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** A miniature store: the same set/get contract zustand hands the slice.
 *  `graph` seeds a hand-built graph, for wiring no editor action can draw -
 *  the campaign's two event feeds into one input, for one. */
function harness(graph?: FlowGraphRec) {
  let state: FlowsHost;
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    state = { ...state, ...fn(state) } as FlowsHost;
  };
  const get = () => state;
  const actions = createFlowsActions(set, get);
  state = { ...actions, flows: { ...FLOWS_INIT, ...(graph ? { graph } : {}) } } as FlowsHost;
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

test("one wire per flow input — a second wire into the same port REPLACES the first", () => {
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

// ───────────────────────── event fan-in and the loop guard (#152, #149)
//
// THE FIXTURE is a hand-built copy of the campaign example's calibration
// corner. Server examples.py:200-204 wires TWO event feeds into CALIBRATION
// QUEUE "do": CLOUD WATCH "clouds in" (`("n13", "in", "n15", "do")`, line 200)
// and PARK + CLOSE "closed" (`("n21", "closed", "n15", "do")`, line 203), and
// FlowGraph.validation_errors (models.py:149-167) allows it because an event
// input means "whenever" and fans in. It is SEEDED rather than drawn through
// flowsConnect because drawing it is exactly what the old store could not do:
// the second feed deleted the first.
const campaignCalib = (): FlowGraphRec => ({
  nodes: [
    { id: "n1", type: "dusk", x: 30, y: 60, params: {} },
    { id: "n13", type: "cloudwatch", x: 30, y: 590, params: {} },
    { id: "n15", type: "calib", x: 580, y: 610, params: {} },
    { id: "n18", type: "flatpanel", x: 30, y: 810, params: {} },
    { id: "n21", type: "parkclose", x: 950, y: 570, params: {} },
    { id: "n30", type: "condition", x: 310, y: 900, params: {} },
  ],
  edges: [
    { id: "c200", from: "n13", fromPort: "in", to: "n15", toPort: "do" },
    { id: "c201", from: "n13", fromPort: "clear", to: "n15", toPort: "stop" },
    { id: "c202", from: "n18", fromPort: "ready", to: "n15", toPort: "panel" },
    { id: "c203a", from: "n1", fromPort: "nightend", to: "n21", toPort: "do" },
    { id: "c203b", from: "n21", fromPort: "closed", to: "n15", toPort: "do" },
  ],
});

test("a third wire into calib.do leaves all three: an EVENT input fans in (#152)", () => {
  // MUTANT "filter on any input" (the replacement ignores the input's lane,
  // which is what flowsConnect did before #152). Observed:
  //   x a third wire into calib.do leaves all three: an EVENT input fans in (#152): calib.do must keep every feed, got 1: n30
  const h = harness(campaignCalib());
  h.a.flowsConnect("n30", "fire", "n15", "do");
  const into = h.flows.graph.edges.filter((e) => e.to === "n15" && e.toPort === "do");
  assert(into.length === 3,
    `calib.do must keep every feed, got ${into.length}: ${into.map((e) => e.from).join(", ")}`);
  assert(into.some((e) => e.id === "c200") && into.some((e) => e.id === "c203b"),
    "the campaign's own two feeds must survive the new one untouched");
  assert(h.flows.graph.edges.length === 6, "and no other wire was disturbed");
});

test("a second wire into target.arm replaces the first: a FLOW input still takes one", () => {
  // The server refuses two wires into one flow input (models.py:163-166,
  // "is wired twice"), so the store must keep replacing there.
  //
  // MUTANT "never filter" (every wire is appended). Observed (2 failed; the
  // older flow-input case above goes red with it):
  //   x a second wire into target.arm replaces the first: a FLOW input still takes one: target.arm must hold one wire, got 2
  const h = harness({
    nodes: [
      { id: "d", type: "dusk", x: 0, y: 0, params: {} },
      { id: "m", type: "dome", x: 250, y: 0, params: {} },
      { id: "t", type: "target", x: 500, y: 0, params: {} },
    ],
    edges: [{ id: "k1", from: "d", fromPort: "window", to: "t", toPort: "arm" }],
  });
  h.a.flowsConnect("m", "open", "t", "arm");
  const into = h.flows.graph.edges.filter((e) => e.to === "t" && e.toPort === "arm");
  assert(into.length === 1, `target.arm must hold one wire, got ${into.length}`);
  assert(into[0].from === "m", "the LATER wire is the one that survives");
});

test("tapping cycle.complete then target.arm is refused: graph unchanged, sentence logged (#149)", () => {
  // Tap-to-wire reaches flowsConnect WITHOUT passing through either drop
  // resolver, so the store's own guard is the only thing between the tap and a
  // flow that compiles to nothing. target.arm is occupied (dusk feeds it): an
  // unguarded connect would REPLACE that wire with the loop, so the edge count
  // alone would not show the damage - the identity of the graph does.
  //
  // MUTANT "no loop guard in flowsConnect". Observed - the dusk wire is gone
  // and the loop took its place:
  //   x tapping cycle.complete then target.arm is refused: graph unchanged, sentence logged (#149): the graph must not be rewritten, edges now: t.target->c.run, c.complete->t.arm
  //
  // MUTANT "log the refusal at info tone". Observed:
  //   x tapping cycle.complete then target.arm is refused: graph unchanged, sentence logged (#149): the refusal is a warning, got tone "info"
  const h = harness({
    nodes: [
      { id: "d", type: "dusk", x: 0, y: 0, params: {} },
      { id: "t", type: "target", x: 250, y: 0, params: {} },
      { id: "c", type: "cycle", x: 500, y: 0, params: {} },
    ],
    edges: [
      { id: "k1", from: "d", fromPort: "window", to: "t", toPort: "arm" },
      { id: "k2", from: "t", fromPort: "target", to: "c", toPort: "run" },
    ],
  });
  const graph = h.flows.graph;
  h.a.flowsTapPort("c", "complete", "out");
  h.a.flowsTapPort("t", "arm", "in");
  assert(h.flows.graph === graph,
    `the graph must not be rewritten, edges now: ${h.flows.graph.edges.map((e) => `${e.from}.${e.fromPort}->${e.to}.${e.toPort}`).join(", ")}`);
  assert(h.flows.dirty === false, "a refused wire must not mark the flow as edited");
  assert(h.flows.tapWire === null, "the arm is spent either way");
  const last = h.flows.logs[h.flows.logs.length - 1];
  assert(last?.msg === "this flow loops back on itself at FILTER CYCLE -> TARGET; a flow lane runs once",
    `the refusal must reach the flow log in the server's words, got ${JSON.stringify(last?.msg)}`);
  assert(last.tone === "warn", `the refusal is a warning, got tone ${JSON.stringify(last.tone)}`);
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failures.length) { failures.forEach((f) => console.log(f)); process.exit(1); }
