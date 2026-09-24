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

// node:fs reads examples.py for the campaign fixture below. It needs no window,
// so it can be a static import.
// @ts-ignore  no @types/node guaranteed; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

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
type FlowNodeType = import("../flowsTypes").FlowNodeType;
type NodeDef = import("../nodeDefs").NodeDef;

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
  // Replacement is per (node, port). POOL is the node with a flow input and an
  // event input side by side, so a replace on `arm` next to a wire into
  // `advance` is where a per-node filter would show.
  //
  // This case used to wire CAPTURE "run2", a port the vocabulary does not
  // have. Once #152 stopped clearing inputs whose lane cannot be resolved, the
  // filter never ran for "run2", and the case passed whatever the filter
  // compared. MUTANT "replace per node, not per port" (flowsSlice.ts: drop
  // `&& e.toPort === toPort` from the filter) left the old case green -
  // observed "21 passed, 0 failed". Against this one, observed (1 failed;
  // node ids are minted per run):
  //   x a different input port on the same node is untouched: replacing into pool.arm must leave the wire into pool.advance alone, edges now: n5_mufioc6e.open->n6_mufioc6e.arm
  const h = harness();
  const [d, m, p, r] = withGraph(h, ["dusk", "dome", "pool", "report"]);
  h.a.flowsConnect(r, "done", p, "advance");
  h.a.flowsConnect(d, "window", p, "arm");
  h.a.flowsConnect(m, "open", p, "arm");
  const edges = h.flows.graph.edges;
  assert(edges.some((e) => e.to === p && e.toPort === "advance"),
    "replacing into pool.arm must leave the wire into pool.advance alone, edges now: "
    + edges.map((e) => `${e.from}.${e.fromPort}->${e.to}.${e.toPort}`).join(", "));
  const arm = edges.filter((e) => e.to === p && e.toPort === "arm");
  assert(arm.length === 1 && arm[0].from === m,
    `pool.arm is a flow input and still takes one wire, the later one; got ${arm.map((e) => e.from).join(", ")}`);
  assert(edges.length === 2, "replace must be per (node, port)");
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
// THE FIXTURE IS THE SERVER'S CAMPAIGN EXAMPLE, READ OUT OF examples.py.
// `_campaign` wires TWO event feeds into CALIBRATION QUEUE "do": CLOUD WATCH
// "clouds in" (`("n13", "in", "n15", "do")`) and PARK + CLOSE "closed"
// (`("n21", "closed", "n15", "do")`), and FlowGraph.validation_errors
// (models.py) allows it because an event input means "whenever" and fans in.
// It is SEEDED rather than drawn through flowsConnect because drawing it is
// exactly what the old store could not do: the second feed deleted the first.
//
// It used to be a hand-typed copy of that corner, citing examples.py by line
// number. A copy agrees with the server only on the day it is typed: the
// example could lose its second feed and the #152 case would go on proving
// fan-in for a graph nobody ships. So the node and edge tuples are PARSED, the
// way nodeDefs.test.ts reads nodes.py, and the builder refuses to hand out a
// graph whose premise no longer holds. Do not replace the parser with a
// literal fixture.

const EXAMPLES_PY_REL = "../../../../../server/astrodeck/flows/examples.py";

/** examples.py, read when a fixture is built rather than at module scope, so a
 *  missing file fails every case that stands on it with a sentence while the
 *  cases that do not still run. */
function readExamplesPy(): string {
  try {
    return readFileSync(new URL(EXAMPLES_PY_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    // A MISSING examples.py must fail, never skip. A skipped fixture reads as a
    // green #152 case while the graph it was bound to is gone.
    throw new Error(
      `cannot read ${EXAMPLES_PY_REL} - the campaign fixture is built from the `
      + `server's campaign example and cannot be built without it: ${(e as Error).message}`);
  }
}

/** Strip `#` comments, leaving double-quoted strings intact, so the server can
 *  annotate its tuple lists: a comment is neither read as a wire nor refused
 *  as unreadable by `pyTuples`, and a tuple commented out is gone. (Without
 *  this, `pyTuples` refuses the `#` - it fails closed, not open.) */
function stripPyComments(src: string): string {
  let out = "";
  let inStr = false;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (inStr) {
      out += c;
      if (c === "\\") { out += src[++i] ?? ""; continue; }
      if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') { inStr = true; out += c; continue; }
    if (c === "#") { while (i < src.length && src[i] !== "\n") i++; out += "\n"; continue; }
    out += c;
  }
  return out;
}

/** Index of the `]` closing the `[` at `open`, string-aware. */
function closingBracket(src: string, open: number): number {
  let depth = 0;
  let inStr = false;
  for (let i = open; i < src.length; i++) {
    const c = src[i];
    if (inStr) {
      if (c === "\\") { i++; continue; }
      if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') inStr = true;
    else if (c === "[") depth++;
    else if (c === "]" && --depth === 0) return i;
  }
  throw new Error(`examples.py _campaign: no closing bracket for the list at ${open}`);
}

/** Every tuple of one shape in a list body. Whatever is left over must be list
 *  punctuation: anything else (a name, a single-quoted string, a tuple of a new
 *  arity) is a construct this parser cannot honestly read, and skipping it
 *  would hand out a graph missing something the server has. */
function pyTuples(list: string, shape: RegExp, what: string): string[][] {
  const rows = Array.from(list.matchAll(shape), (m) => m.slice(1));
  const rest = list.replace(shape, "");
  assert(/^[\s,]*$/.test(rest),
    `examples.py _campaign ${what} holds something this parser cannot read: `
    + JSON.stringify(rest.replace(/^[\s,]+|[\s,]+$/g, "").replace(/\s+/g, " ").slice(0, 80)));
  return rows;
}

const NODE_TUPLE = /\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)/g;
const EDGE_TUPLE = /\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)/g;

interface CampaignExample {
  nodes: { id: string; type: string; x: number; y: number }[];
  edges: { from: string; fromPort: string; to: string; toPort: string }[];
}

/** The campaign example's node and edge tuples: the two lists that open
 *  `_graph(` inside `def _campaign`, and only there - the cycle example wires
 *  `("n13", "in", "n15", "do")` too, so a file-wide scan would find it twice. */
function campaignExample(): CampaignExample {
  const src = readExamplesPy();
  const def = src.indexOf("\ndef _campaign(");
  assert(def >= 0,
    "examples.py has no `def _campaign(` - the campaign example this fixture is built from has moved or been renamed");
  const next = src.indexOf("\ndef ", def + 1);
  const fn = src.slice(def, next < 0 ? undefined : next);
  // Start at the call, past the docstring: the comment stripper knows only
  // double-quoted strings, and everything from `_graph(` on is made of them.
  const call = fn.indexOf("graph=_graph(");
  assert(call >= 0, "examples.py _campaign no longer builds its graph with `graph=_graph(`");
  const body = stripPyComments(fn.slice(call + "graph=_graph(".length));
  const nodesOpen = body.search(/\S/);
  assert(body[nodesOpen] === "[", "examples.py _campaign: `_graph(` no longer opens with the node list");
  const nodesClose = closingBracket(body, nodesOpen);
  const gap = /^\s*,\s*\[/.exec(body.slice(nodesClose + 1));
  assert(gap !== null, "examples.py _campaign: the node list is no longer followed by the edge list");
  const edgesOpen = nodesClose + gap![0].length;
  const edgesClose = closingBracket(body, edgesOpen);
  return {
    nodes: pyTuples(body.slice(nodesOpen + 1, nodesClose), NODE_TUPLE, "node list")
      .map(([id, type, x, y]) => ({ id, type, x: Number(x), y: Number(y) })),
    edges: pyTuples(body.slice(edgesOpen + 1, edgesClose), EDGE_TUPLE, "edge list")
      .map(([from, fromPort, to, toPort]) => ({ from, fromPort, to, toPort })),
  };
}

/** The campaign example as a store graph, plus the test's own CONDITION n30
 *  to draw a third feed from. Refuses when the premise the #152 case stands on
 *  is gone, naming what changed. */
const campaignCalib = (): FlowGraphRec => {
  const ex = campaignExample();
  const feeds = ex.edges.filter((e) => e.to === "n15" && e.toPort === "do");
  const hasFeed = (from: string, port: string) =>
    feeds.some((e) => e.from === from && e.fromPort === port);
  assert(feeds.length === 2 && hasFeed("n13", "in") && hasFeed("n21", "closed"),
    "the campaign example (examples.py _campaign) no longer wires its two feeds, CLOUD WATCH "
    + `n13.in and PARK + CLOSE n21.closed, into CALIBRATION QUEUE n15.do; it wires ${feeds.length}: `
    + `${feeds.map((e) => `${e.from}.${e.fromPort}`).join(", ") || "none"}. The #152 fan-in case is `
    + "built on that pair and would be proving fan-in for a graph the server no longer ships");
  // flowsConnect never clears an input it cannot resolve either, so on a
  // calib.do the UI vocabulary does not know, the #152 case would stay green on
  // that rule and prove nothing about event fan-in. Bind it to the event lane.
  const calib = ex.nodes.find((n) => n.id === "n15");
  const kind = calib ? (NODE_DEFS as Record<string, NodeDef | undefined>)[calib.type]
    ?.ins.find((q) => q.id === "do")?.kind : undefined;
  assert(kind === "event",
    `the campaign example's n15 is ${calib ? `type "${calib.type}"` : "gone"}, and the UI `
    + `vocabulary reads its "do" input as ${JSON.stringify(kind ?? null)}, not "event": the #152 `
    + "case would pass on the unresolvable-input rule instead of the event rule");
  assert(!ex.nodes.some((n) => n.id === "n30"),
    "the campaign example now has its own n30; the test's CONDITION needs another id");
  return {
    // params are left empty: flowsConnect reads only ids, types and ports.
    nodes: ex.nodes
      .map((n) => ({ ...n, type: n.type as FlowNodeType, params: {} }))
      .concat([{ id: "n30", type: "condition", x: 310, y: 900, params: {} }]),
    edges: ex.edges.map((e) => ({ id: `${e.from}.${e.fromPort}->${e.to}.${e.toPort}`, ...e })),
  };
};

test("the campaign fixture is parsed from examples.py, which still feeds n15.do twice (#152)", () => {
  // Every mutant below was run from a byte backup of the file it names and the
  // file restored byte-identical (sha256 checked). Each fails this case AND the
  // fan-in case below with the same sentence (2 failed, 22 passed).
  //
  // MUTANT "the example loses the day shift" (examples.py: delete
  // `("n21", "closed", "n15", "do")`). Observed:
  //   x the campaign fixture is parsed from examples.py, which still feeds n15.do twice (#152): the campaign example (examples.py _campaign) no longer wires its two feeds, CLOUD WATCH n13.in and PARK + CLOSE n21.closed, into CALIBRATION QUEUE n15.do; it wires 1: n13.in. The #152 fan-in case is built on that pair and would be proving fan-in for a graph the server no longer ships
  // Commenting the tuple out instead (`# ("n21", ...)` at the end of its line)
  // gave the same sentence.
  //
  // MUTANT "the server renames calib" (examples.py: `("n15", "calib", 580,
  // 610)` -> `("n15", "calibration", 580, 610)`). Observed:
  //   x the campaign fixture is parsed from examples.py, which still feeds n15.do twice (#152): the campaign example's n15 is type "calibration", and the UI vocabulary reads its "do" input as null, not "event": the #152 case would pass on the unresolvable-input rule instead of the event rule
  // With the lane check ALSO removed from campaignCalib (`assert(true || kind
  // === "event", ...)`), observed "24 passed, 0 failed": the fan-in case stays
  // green on a calib.do nobody can resolve, which is why the check is there.
  //
  // MUTANT "a tuple the parser cannot read" (examples.py: `("n3", "unsafe",
  // "n19", "do")` written with single quotes, same meaning to Python).
  // Observed:
  //   x the campaign fixture is parsed from examples.py, which still feeds n15.do twice (#152): examples.py _campaign edge list holds something this parser cannot read: "('n3', 'unsafe', 'n19', 'do')"
  // With the leftover check in pyTuples ALSO removed, observed "24 passed, 0
  // failed": the n3 -> n19 wire was dropped from the fixture without a word.
  //
  // MUTANT "examples.py is missing" (this file: EXAMPLES_PY_REL pointed at
  // examples_gone.py). Observed (the checkout's absolute path shown as <repo>),
  // with the other 22 cases still run:
  //   x the campaign fixture is parsed from examples.py, which still feeds n15.do twice (#152): cannot read ../../../../../server/astrodeck/flows/examples_gone.py - the campaign fixture is built from the server's campaign example and cannot be built without it: ENOENT: no such file or directory, open '<repo>\server\astrodeck\flows\examples_gone.py'
  //
  // CONTROL (examples.py: `  # the day shift` appended to the line carrying
  // the n21 -> n15 wire). Observed "24 passed, 0 failed": a comment in the
  // list is neither a wire nor refused.
  const g = campaignCalib();
  // Parser sanity that does not pin the example's size: a node tuple the
  // parser dropped would leave a wire pointing at a node that is not there.
  const ids = new Set(g.nodes.map((n) => n.id));
  const dangling = g.edges.filter((e) => !ids.has(e.from) || !ids.has(e.to));
  assert(dangling.length === 0,
    `parsed wires point at nodes the parse did not find: ${dangling.map((e) => e.id).join(", ")}`);
  assert(g.nodes.length > 5 && g.edges.length > 5,
    `the campaign parse came back implausibly small: ${g.nodes.length} nodes, ${g.edges.length} wires`);
});

test("a third wire into calib.do leaves all three: an EVENT input fans in (#152)", () => {
  // MUTANT "filter on any input" (flowsSlice.ts: drop `flowInput &&` from the
  // filter, so the replacement ignores the input's lane, which is what
  // flowsConnect did before #152). Re-run on the parsed fixture; observed (3
  // failed, with the two unresolvable-input cases below):
  //   x a third wire into calib.do leaves all three: an EVENT input fans in (#152): calib.do must keep every feed, got 1: n30
  const h = harness(campaignCalib());
  const before = h.flows.graph.edges;
  h.a.flowsConnect("n30", "fire", "n15", "do");
  const into = h.flows.graph.edges.filter((e) => e.to === "n15" && e.toPort === "do");
  assert(into.length === 3,
    `calib.do must keep every feed, got ${into.length}: ${into.map((e) => e.from).join(", ")}`);
  assert(into.some((e) => e.from === "n13" && e.fromPort === "in")
    && into.some((e) => e.from === "n21" && e.fromPort === "closed"),
    "the campaign's own two feeds must survive the new one untouched");
  assert(h.flows.graph.edges.length === before.length + 1
    && before.every((e) => h.flows.graph.edges.includes(e)),
    "and no other wire was disturbed");
});

test("an input on a node type the vocabulary does not have is never cleared (#152)", () => {
  // A saved graph can outlive its vocabulary, and portKindOf reads null for a
  // node whose type NODE_DEFS no longer has. Null is not "flow": the server
  // refuses a second wire only into a FLOW input, so replacing here would
  // delete a wire the operator drew on a guess, and say nothing.
  //
  // MUTANT "anything not an event input is a flow input" (flowsSlice.ts
  // `=== "flow"` -> `!== "event"`, from a byte backup, restored byte-identical).
  // Before these two cases it survived this whole file, observed "21 passed,
  // 0 failed". Now observed (22 passed, 2 failed: this case and the
  // dropped-port one; the event fan-in and flow-replace cases stay green, so
  // they are the controls):
  //   x an input on a node type the vocabulary does not have is never cleared (#152): an input whose lane cannot be resolved must keep every wire, got 1: m
  const RETIRED = "meridianflip";
  assert(!(RETIRED in NODE_DEFS),
    `"${RETIRED}" is in NODE_DEFS now; this case needs a type the vocabulary does not have`);
  const h = harness({
    nodes: [
      { id: "d", type: "dusk", x: 0, y: 0, params: {} },
      { id: "m", type: "dome", x: 250, y: 0, params: {} },
      { id: "r", type: RETIRED as FlowNodeType, x: 500, y: 0, params: {} },
    ],
    edges: [{ id: "k1", from: "d", fromPort: "window", to: "r", toPort: "run" }],
  });
  const incumbent = h.flows.graph.edges[0];
  h.a.flowsConnect("m", "open", "r", "run");
  const into = h.flows.graph.edges.filter((e) => e.to === "r" && e.toPort === "run");
  assert(into.length === 2,
    `an input whose lane cannot be resolved must keep every wire, got ${into.length}: ${into.map((e) => e.from).join(", ")}`);
  assert(into.includes(incumbent), "the wire already there survives untouched");
  assert(into.some((e) => e.from === "m"), "and the new wire was added");
});

test("an input port the vocabulary dropped is never cleared (#152)", () => {
  // The other null in portKindOf: a node type the vocabulary knows, naming an
  // input it no longer has. Same rule, other branch.
  //
  // MUTANT "anything not an event input is a flow input" (as above). Observed:
  //   x an input port the vocabulary dropped is never cleared (#152): a dropped input must keep every wire, got 1: s
  const DROPPED = "trigger";
  assert(!NODE_DEFS.capture.ins.some((q) => q.id === DROPPED),
    `capture has a "${DROPPED}" input now; this case needs a port the vocabulary dropped`);
  const h = harness({
    nodes: [
      { id: "t", type: "target", x: 0, y: 0, params: {} },
      { id: "s", type: "slew", x: 250, y: 0, params: {} },
      { id: "c", type: "capture", x: 500, y: 0, params: {} },
    ],
    edges: [{ id: "k1", from: "t", fromPort: "target", to: "c", toPort: DROPPED }],
  });
  const incumbent = h.flows.graph.edges[0];
  h.a.flowsConnect("s", "centered", "c", DROPPED);
  const into = h.flows.graph.edges.filter((e) => e.to === "c" && e.toPort === DROPPED);
  assert(into.length === 2,
    `a dropped input must keep every wire, got ${into.length}: ${into.map((e) => e.from).join(", ")}`);
  assert(into.includes(incumbent), "the wire already there survives untouched");
});

test("a second wire into target.arm replaces the first: a FLOW input still takes one", () => {
  // The server refuses two wires into one flow input (models.py:163-166,
  // "is wired twice"), so the store must keep replacing there.
  //
  // MUTANT "never filter" (every wire is appended). Observed (3 failed; the
  // older flow-input case and the pool per-port case above go red with it):
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
