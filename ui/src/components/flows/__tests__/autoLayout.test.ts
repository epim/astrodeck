// Tests for components/flows/autoLayout.ts — the phone FLOW tab's auto-laid
// zigzag graph.
//
// The centrepiece is §B: the M16 example laid out at the reference capture's
// width must reproduce MILESTONE2-CONTRACT §C.15's published numbers exactly
// (content y 18 / 117 / 236 / 355, columns 14 / 228). Those four y's are the
// only externally verifiable output this file has — they were measured off
// `screenshots/10-phone-flow-autograph-390px.png` — so they are pinned to the
// pixel, not to a tolerance.
//
// §A is the falsification block: event edges must NOT be on the run cursor's
// path. Adding or removing every event edge in M16 has to leave the order
// bit-identical, because an event edge spliced into the flow lane would draw a
// *rule* as though it were a stage of the night.
//
// Same inline-assert / `tsx` style as the rest of this UI (no vitest, no DOM):
//   npx tsx src/components/flows/__tests__/autoLayout.test.ts

import {
  AUTO_PAD,
  CLUSTER_GAP,
  FLOW_LANE_GAP,
  computeAutoLayout,
  flowOrder,
  layoutColumns,
  layoutHeight,
  portKindOf,
} from "../autoLayout";
import { NODE_DEFS } from "../nodeDefs";
import { nodeLayoutHeight } from "../geometry";
import type { FlowEdgeRec, FlowGraphRec, FlowNodeRec, FlowNodeType } from "../flowsTypes";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(a: T, b: T, msg: string): void {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw new Error(`${msg}: expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
  }
}

// ----------------------------------------------------------------- fixtures
const node = (id: string, type: FlowNodeType, x: number, y: number): FlowNodeRec =>
  ({ id, type, x, y, params: {} });
// `params` is empty everywhere below: auto-layout reads ports and coordinates
// and nothing else, so filling in M16's real exposure/filter values would only
// invite the reader to think they mattered here.
const edge = (from: string, fromPort: string, to: string, toPort: string): FlowEdgeRec =>
  ({ id: `${from}.${fromPort}->${to}.${toPort}`, from, fromPort, to, toPort });

/** The M16 example, transcribed from `server/astrodeck/flows/examples.py`
 *  `_m16()` — the same 16 nodes and 15 edges, in the same order, with the same
 *  canvas coordinates. It is the DoD's headline acceptance case and the graph
 *  the reference phone capture was taken of. Node/edge ORDER is load-bearing
 *  here (it is the tiebreak the topological seed falls back on and the order
 *  rule clusters are discovered in), so it is preserved verbatim. */
const M16: FlowGraphRec = {
  nodes: [
    node("n1", "dusk", 30, 50), node("n16", "dome", 260, 50),
    node("n17", "duskflats", 490, 50), node("n2", "target", 720, 50),
    node("n4", "slew", 950, 50), node("n5", "autofocus", 160, 320),
    node("n6", "guide", 390, 320), node("n7", "capture", 620, 320),
    node("n12", "report", 870, 320), node("n13", "cloudwatch", 30, 560),
    node("n14", "holdresume", 330, 590), node("n15", "calib", 620, 540),
    node("n18", "flatpanel", 330, 780), node("n10", "notify", 870, 620),
    node("n3", "safety", 30, 860), node("n19", "abort", 620, 880),
  ],
  edges: [
    edge("n1", "window", "n16", "run"), edge("n16", "open", "n17", "run"),
    edge("n17", "done", "n2", "arm"), edge("n2", "target", "n4", "run"),
    edge("n4", "centered", "n5", "run"), edge("n5", "focused", "n6", "run"),
    edge("n6", "guiding", "n7", "run"), edge("n7", "complete", "n12", "session"),
    edge("n13", "in", "n14", "pause"), edge("n13", "in", "n15", "do"),
    edge("n13", "in", "n10", "do"), edge("n13", "clear", "n14", "resume"),
    edge("n13", "clear", "n15", "stop"), edge("n18", "ready", "n15", "panel"),
    edge("n3", "unsafe", "n19", "do"),
  ],
};

const M16_LANE = ["n1", "n16", "n17", "n2", "n4", "n5", "n6", "n7", "n12"];
const ids = (ns: FlowNodeRec[]): string[] => ns.map((n) => n.id);

/** §C.15: the capture's screen y is the content y plus the 54px phone header
 *  row, and the contract quotes both series (18 → 72, 117 → 171, …). */
const PHONE_HEADER_H = 54;

// ============================================ §A  the order is the FLOW lane
test("M16's flow lane is dusk → dome → dusk flats → target → … → report", () => {
  eq(ids(flowOrder(M16, NODE_DEFS)), M16_LANE, "M16 flow order");
});

// The screenshot's own evidence: the first four cards, top to bottom, are
// DUSK WIN…, DOME CON…, DUSK FLATS, TARGET.
test("the first four laid-out stages are the four the reference capture shows", () => {
  eq(ids(flowOrder(M16, NODE_DEFS)).slice(0, 4), ["n1", "n16", "n17", "n2"],
     "first four cards");
});

test("event-only nodes never enter the flow lane", () => {
  const lane = new Set(ids(flowOrder(M16, NODE_DEFS)));
  for (const id of ["n13", "n14", "n15", "n18", "n10", "n3", "n19"]) {
    assert(!lane.has(id), `${id} has no flow port and must not be a lane stage`);
  }
});

// FALSIFICATION. Every event edge in M16 is deleted; then a fresh set of event
// edges is bolted on in a different order. If event edges were reaching the
// topological sort at all, at least one of these would move a stage.
test("FALSIFICATION: event edges are not on the cursor's path", () => {
  const isEvent = (e: FlowEdgeRec): boolean => {
    const from = M16.nodes.find((n) => n.id === e.from)!;
    return portKindOf(NODE_DEFS, from.type, e.fromPort, "out") === "event";
  };
  const flowOnly: FlowGraphRec = { nodes: M16.nodes, edges: M16.edges.filter((e) => !isEvent(e)) };
  eq(ids(flowOrder(flowOnly, NODE_DEFS)), M16_LANE, "stripping every event edge");

  // …and adding new ones, including two that run BACKWARDS along the lane
  // (report ← capture would invert the tail if it were read as a flow edge).
  const extra: FlowGraphRec = {
    nodes: M16.nodes,
    edges: [
      edge("n7", "frame", "n15", "do"),
      edge("n3", "unsafe", "n14", "pause"),
      ...M16.edges,
    ],
  };
  eq(ids(flowOrder(extra, NODE_DEFS)), M16_LANE, "adding event edges");
});

test("an edge naming a port the vocabulary does not have is ignored, not guessed", () => {
  const g: FlowGraphRec = {
    nodes: [node("a", "dusk", 0, 0), node("b", "report", 500, 0)],
    edges: [edge("a", "gone", "b", "session")],
  };
  // Both roots, so both are emitted — sorted by canvas x. If the unknown port
  // had been treated as flow, `b` would carry indegree 1 and follow `a` for the
  // wrong reason; if it had been treated as event it would look the same. The
  // point is that the phantom edge cannot splice a stage into the run.
  eq(ids(flowOrder(g, NODE_DEFS)), ["a", "b"], "unknown port");
  assert(portKindOf(NODE_DEFS, "dusk", "gone", "out") === null, "unknown port has no kind");
  assert(portKindOf(NODE_DEFS, "dusk", "window", "out") === "flow", "dusk.window is flow");
  assert(portKindOf(NODE_DEFS, "cloudwatch", "in", "out") === "event", "cloudwatch.in is event");
});

test("the seed order is the graph's own left-to-right reading order", () => {
  const right = node("r", "dusk", 900, 10);
  const left = node("l", "dusk", 20, 10);
  // Declared right-first; must still come out left-first.
  eq(ids(flowOrder({ nodes: [right, left], edges: [] }, NODE_DEFS)), ["l", "r"], "sorted by x");
  // Same x: y breaks the tie.
  const lo = node("lo", "dusk", 20, 400);
  const hi = node("hi", "dusk", 20, 10);
  eq(ids(flowOrder({ nodes: [lo, hi], edges: [] }, NODE_DEFS)), ["hi", "lo"], "then by y");
});

test("the order is a pure function of the record — same input, same output", () => {
  eq(ids(flowOrder(M16, NODE_DEFS)), ids(flowOrder(M16, NODE_DEFS)), "repeat call");
  const a = computeAutoLayout(M16, 392, NODE_DEFS);
  const b = computeAutoLayout(M16, 392, NODE_DEFS);
  eq(a, b, "repeat layout");
});

// A cycle cannot be drawn as a lane, but it must not hang the phone tab either.
// The guarantee is termination plus total placement, not an invented order.
test("a flow-edge cycle terminates and is left out of the lane", () => {
  const g: FlowGraphRec = {
    nodes: [
      node("a", "slew", 0, 0), node("b", "autofocus", 100, 0), node("c", "guide", 200, 0),
    ],
    edges: [
      edge("a", "centered", "b", "run"),
      edge("b", "focused", "c", "run"),
      edge("c", "guiding", "a", "run"),
    ],
  };
  const started = Date.now();
  eq(ids(flowOrder(g, NODE_DEFS)), [], "no node in a pure cycle reaches indegree 0");
  assert(Date.now() - started < 1000, "flowOrder must not spin on a cycle");
  // …and the layout still places all three, via the straggler pass.
  const out = computeAutoLayout(g, 392, NODE_DEFS);
  eq(Object.keys(out.pos).sort(), ["a", "b", "c"], "a cyclic graph still lays out");
});

test("a cycle downstream of a real root keeps the root and drops the loop", () => {
  const g: FlowGraphRec = {
    nodes: [
      node("root", "dusk", 0, 0), node("x", "slew", 100, 0), node("y", "autofocus", 200, 0),
    ],
    edges: [
      edge("root", "window", "x", "run"),
      edge("x", "centered", "y", "run"),
      edge("y", "focused", "x", "run"),
    ],
  };
  // `x` has indegree 2 and only ever loses one, so the loop never opens.
  eq(ids(flowOrder(g, NODE_DEFS)), ["root"], "root only");
  eq(Object.keys(computeAutoLayout(g, 392, NODE_DEFS).pos).sort(), ["root", "x", "y"],
     "every node still gets a position");
});

// ==================================== §B  the published M16 phone geometry
//
// MILESTONE2-CONTRACT §C.15: "verified to predict the M16 capture exactly
// (content y 18 / 117 / 236 / 355 → screen y 72 / 171 / 290 / 409; measured
// 72 / 169-172 / 290 / 409)".
//
// The width is 392, which is the reference PNG's own pixel width. §C.15 labels
// the same capture "vw=390" and quotes columns 14 / 228 — and 228 is only
// reachable at 392 (`392 - 14 - 150`). At a literal 390 the formula gives 226,
// which is ALSO what the PNG shows: its right-hand cards' border pixel is
// column 226, its left-hand cards' is column 14. Both readings are pinned
// below rather than one being picked; see the report's ambiguity note.
const M16_392 = computeAutoLayout(M16, 392, NODE_DEFS);

test("columns sit at 14 and 228 — the contract's published pair", () => {
  eq(layoutColumns(392), { colL: 14, colR: 228 }, "columns at the capture's width");
  eq([M16_392.pos.n1.x, M16_392.pos.n16.x, M16_392.pos.n17.x, M16_392.pos.n2.x],
     [14, 228, 14, 228], "the lane zigzags left/right/left/right");
});

test("content rows land on 18 / 117 / 236 / 355", () => {
  eq([M16_392.pos.n1.y, M16_392.pos.n16.y, M16_392.pos.n17.y, M16_392.pos.n2.y],
     [18, 117, 236, 355], "the contract's four content y's");
});

test("…which is screen 72 / 171 / 290 / 409 under the 54px phone header", () => {
  eq([M16_392.pos.n1.y, M16_392.pos.n16.y, M16_392.pos.n17.y, M16_392.pos.n2.y]
       .map((y) => y + PHONE_HEADER_H),
     [72, 171, 290, 409], "screen y as measured off the capture");
});

// The same layout at a literal 390px container. Documented, not preferred:
// this is what the reference PNG's pixels actually show.
test("at a literal 390px container the right column is 226 — the measured render", () => {
  eq(layoutColumns(390), { colL: 14, colR: 226 }, "390 - 14 - 150");
  const at390 = computeAutoLayout(M16, 390, NODE_DEFS);
  eq(at390.pos.n16.x, 226, "right column at 390");
  // Only the columns move; the whole y-series is width-independent.
  eq([at390.pos.n1.y, at390.pos.n16.y, at390.pos.n17.y, at390.pos.n2.y],
     [18, 117, 236, 355], "rows do not depend on width");
});

test("the whole flow lane, card by card", () => {
  eq(M16_LANE.map((id) => M16_392.pos[id]), [
    { x: 14, y: 18 },    // dusk        1 port row
    { x: 228, y: 117 },  // dome        2
    { x: 14, y: 236 },   // dusk flats  2
    { x: 228, y: 355 },  // target      2
    { x: 14, y: 474 },   // slew        2
    { x: 228, y: 593 },  // autofocus   2
    { x: 14, y: 712 },   // guide       2
    { x: 228, y: 831 },  // capture     3 (complete + frame)
    { x: 14, y: 970 },   // report      1
  ], "flow lane");
});

test("the lane's step is exactly the card height plus 34", () => {
  // Pinned as a literal first. Every step assertion below compares two derived
  // values, so it would stay green if the constant itself were changed — which
  // is precisely the edit README §5's unscoped "34px vertical gaps" invites.
  eq(FLOW_LANE_GAP, 34, "the flow-lane gap");
  const lane = flowOrder(M16, NODE_DEFS);
  for (let i = 1; i < lane.length; i++) {
    const step = M16_392.pos[lane[i].id].y - M16_392.pos[lane[i - 1].id].y;
    eq(step, layoutHeight(lane[i - 1], NODE_DEFS) + FLOW_LANE_GAP, `step ${i}`);
  }
});

// §G-22: README §5 says "34px vertical gaps" unscoped; the prototype uses 34
// for the flow lane and 22 everywhere else. The clusters are below the 540px
// fold in the reference capture, so this is the only place that pins it.
test("a rule cluster fans out into the opposite column at 22px, not 34", () => {
  // CLOUD WATCH is the first source (unplaced sources sort by canvas y, and it
  // is the topmost of the three), so it opens the cluster pass at the left
  // column and stacks HOLD / QUEUE / NOTIFY on the right.
  eq(M16_392.pos.n13, { x: 14, y: 1069 }, "CLOUD WATCH");
  eq(M16_392.pos.n14, { x: 228, y: 1069 }, "HOLD / RESUME level with its source");
  eq(M16_392.pos.n15, { x: 228, y: 1176 }, "CALIBRATION QUEUE");
  eq(M16_392.pos.n10, { x: 228, y: 1303 }, "NOTIFY");
  eq(CLUSTER_GAP, 22, "the cluster gap — a literal, for the same reason as above");
  eq(M16_392.pos.n15.y - M16_392.pos.n14.y,
     layoutHeight(M16.nodes.find((n) => n.id === "n14")!, NODE_DEFS) + CLUSTER_GAP,
     "cluster gap is CLUSTER_GAP");
  assert(CLUSTER_GAP !== FLOW_LANE_GAP, "the two gaps are different numbers (§G-22)");
});

test("a target another cluster already placed gets no second position", () => {
  // FLAT PANEL's only edge feeds `calib.panel`, and CLOUD WATCH placed calib
  // first. The panel still gets a slot of its own; the queue does not move.
  eq(M16_392.pos.n18, { x: 14, y: 1390 }, "FLAT PANEL opens its own cluster");
  eq(M16_392.pos.n15, { x: 228, y: 1176 }, "CALIBRATION QUEUE stays where it was");
  eq(M16_392.pos.n3, { x: 14, y: 1477 }, "SAFETY MONITOR");
  eq(M16_392.pos.n19, { x: 228, y: 1477 }, "ABORT + PARK, level with its source");
});

test("every node is placed exactly once, and the height covers the last card", () => {
  eq(Object.keys(M16_392.pos).length, M16.nodes.length, "one position per node");
  const lowest = Math.max(...Object.values(M16_392.pos).map((p) => p.y));
  assert(M16_392.height > lowest, `height ${M16_392.height} must clear the last card ${lowest}`);
  eq(M16_392.height, 1654, "M16 total scroll height");
});

test("unwired nodes stack in the left column at the bottom", () => {
  const g: FlowGraphRec = {
    nodes: [node("a", "dusk", 0, 0), node("s1", "notify", 40, 900), node("s2", "abort", 80, 900)],
    edges: [],
  };
  const out = computeAutoLayout(g, 392, NODE_DEFS);
  eq(out.pos.a, { x: 14, y: 18 }, "the one lane stage");
  eq(out.pos.s1.x, 14, "stragglers use the left column");
  eq(out.pos.s2.x, 14, "…all of them");
  eq(out.pos.s2.y - out.pos.s1.y,
     layoutHeight(g.nodes[1], NODE_DEFS) + CLUSTER_GAP, "stacked at the cluster gap");
});

// ==================================================== §C  widths and heights
test("the container width is clamped to 300…700", () => {
  eq(layoutColumns(100), layoutColumns(300), "narrower than 300 clamps up");
  eq(layoutColumns(0), layoutColumns(300), "a container measured before layout");
  eq(layoutColumns(2000), layoutColumns(700), "wider than 700 clamps down");
  eq(layoutColumns(300), { colL: 14, colR: 136 }, "300 - 14 - 150");
  eq(layoutColumns(700), { colL: 14, colR: 536 }, "700 - 14 - 150");
});

test("a non-finite width falls back to the clamp floor instead of poisoning every x", () => {
  eq(layoutColumns(Number.NaN), layoutColumns(300), "NaN");
  const out = computeAutoLayout(M16, Number.NaN, NODE_DEFS);
  for (const p of Object.values(out.pos)) {
    assert(Number.isFinite(p.x) && Number.isFinite(p.y), `non-finite position ${JSON.stringify(p)}`);
  }
  assert(Number.isFinite(out.height), "non-finite height");
});

test("the left column is the pad, at every width", () => {
  eq(AUTO_PAD, 14, "the pad, as a literal");
  for (const w of [0, 320, 390, 392, 563, 700, 1440]) {
    eq(layoutColumns(w).colL, AUTO_PAD, `colL at ${w}`);
  }
});

// §C.5: two card heights exist and they disagree by 18px. Reproducing both is
// the contract; collapsing them would move either the fit zoom or this layout.
test("layoutHeight is 37 + rows·20 + 8, and is NOT geometry's fit height", () => {
  const dusk = M16.nodes.find((n) => n.id === "n1")!;      // 1 port row
  const capture = M16.nodes.find((n) => n.id === "n7")!;   // 3 port rows
  eq(layoutHeight(dusk, NODE_DEFS), 37 + 1 * 20 + 8, "dusk");
  eq(layoutHeight(capture, NODE_DEFS), 37 + 3 * 20 + 8, "capture");
  eq(nodeLayoutHeight(dusk, NODE_DEFS) - layoutHeight(dusk, NODE_DEFS), 18,
     "the fit height is 18px taller — both are reproduced, in their own call sites");
});

test("a node type the vocabulary does not know is placed, not thrown on", () => {
  const g: FlowGraphRec = {
    nodes: [node("a", "dusk", 0, 0), node("x", "gizmo" as FlowNodeType, 10, 10)],
    edges: [],
  };
  const out = computeAutoLayout(g, 392, NODE_DEFS);
  eq(ids(flowOrder(g, NODE_DEFS)), ["a"], "an unknown type owns no flow port");
  assert(out.pos.x !== undefined, "…but it still gets a position");
  eq(out.pos.x.x, 14, "as a straggler in the left column");
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nautoLayout.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
