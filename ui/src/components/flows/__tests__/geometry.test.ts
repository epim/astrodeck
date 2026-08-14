// Unit tests for components/flows/geometry.ts — the canvas maths.
//
// The fixture is the real `example-m16` graph (server/astrodeck/flows/
// examples.py), because MILESTONE2-CONTRACT §D.2 works its fit numbers on that
// exact node set and says "Use this as the geometry.test.ts fixture", and §D.3
// works two of its edges and says "Pin these in geometry.test.ts". Those worked
// numbers are the reference captures' numbers: the zoom chip in captures 02,
// 03, 07 and 08 literally reads 46%, so a fit that returns anything but
// 0.4583… is a fit that no longer matches the approved design.
//
// Nothing here asserts "it returned a string". A path that is a string and a
// path that is the DESIGNED curve differ by the whole point of the file.
//
// Same tiny inline-assert harness as the rest of this UI (no vitest):
//   npx tsx src/components/flows/__tests__/geometry.test.ts

import {
  nodeW,
  clampZoom,
  nodeRows,
  nodeLayoutHeight,
  fitViewHeight,
  portPos,
  edgePath,
  fitView,
  NODE_W_WIDE,
  NODE_W_PHONE,
  ZOOM_MIN,
  ZOOM_MAX,
  FIT_ZOOM_MAX,
  type PortTable,
  type Point,
} from "../geometry";
import { NODE_DEFS } from "../nodeDefs";
import type { FlowNodeRec, FlowNodeType } from "../flowsTypes";

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
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function near(a: number, b: number, tol: number, msg = ""): void {
  if (!(Math.abs(a - b) <= tol)) {
    throw new Error(`${msg} expected ~${b} (±${tol}), got ${a}`);
  }
}

// --------------------------------------------------------------- fixtures
// The port table, transcribed from server/astrodeck/flows/nodes.py. Only ids
// and their ORDER matter to geometry — an output's row index is offset by the
// input count of the same node, so a table with the right ports in the wrong
// order would put every output wire on the wrong row.
const PORTS: PortTable = {
  dusk: { ins: [], outs: [{ id: "window" }] },
  target: { ins: [{ id: "arm" }], outs: [{ id: "target" }] },
  safety: { ins: [], outs: [{ id: "unsafe" }] },
  cloudwatch: { ins: [], outs: [{ id: "in" }, { id: "clear" }] },
  dome: { ins: [{ id: "run" }], outs: [{ id: "open" }] },
  flatpanel: { ins: [], outs: [{ id: "ready" }] },
  slew: { ins: [{ id: "run" }], outs: [{ id: "centered" }] },
  autofocus: { ins: [{ id: "run" }], outs: [{ id: "focused" }] },
  guide: { ins: [{ id: "run" }], outs: [{ id: "guiding" }] },
  capture: {
    ins: [{ id: "run" }],
    outs: [{ id: "complete" }, { id: "frame" }],
  },
  duskflats: { ins: [{ id: "run" }], outs: [{ id: "done" }] },
  calib: {
    ins: [{ id: "do" }, { id: "stop" }, { id: "panel" }],
    outs: [],
  },
  pool: { ins: [{ id: "arm" }], outs: [{ id: "target" }] },
  cycle: { ins: [{ id: "run" }], outs: [{ id: "body" }, { id: "complete" }] },
  condition: { ins: [{ id: "events" }], outs: [{ id: "fire" }] },
  holdresume: { ins: [{ id: "pause" }, { id: "resume" }], outs: [] },
  notify: { ins: [{ id: "do" }], outs: [] },
  refocus: { ins: [{ id: "do" }], outs: [] },
  abort: { ins: [{ id: "do" }], outs: [] },
  report: { ins: [{ id: "session" }], outs: [] },
};

// The table above is a hand copy, and a hand copy is exactly the thing that
// drifts. Every worked number below is computed THROUGH it, so if PORTS and
// NODE_DEFS disagree about a port count or a port order, this file keeps
// reporting green about a card shape the app never renders — the run's real
// anchors come from NODE_DEFS. `nodeDefs.test.ts` proves NODE_DEFS against
// nodes.py; this proves PORTS against NODE_DEFS, which is what makes the chain
// reach the server. PORTS stays hand-written rather than being replaced by
// NODE_DEFS: the worked examples are only readable if the reader can see the
// row order they were computed from.
test("the fixture port table still matches NODE_DEFS — ids and ORDER, both ways", () => {
  const shape = (d: { ins: readonly { id: string }[]; outs: readonly { id: string }[] }) =>
    `${d.ins.map((p) => p.id).join(",")} | ${d.outs.map((p) => p.id).join(",")}`;
  const defTypes = Object.keys(NODE_DEFS).sort();
  const fixTypes = Object.keys(PORTS).sort();
  eq(fixTypes.join(","), defTypes.join(","), "node types in the fixture vs NODE_DEFS:");
  for (const t of defTypes as FlowNodeType[]) {
    eq(shape(PORTS[t]), shape(NODE_DEFS[t]), `${t} ports (in | out):`);
  }
});

// geometry.ts takes its port table as an argument specifically so it never
// imports the vocabulary. That decoupling is only worth having if NODE_DEFS
// really does satisfy `PortTable` — which is a claim in a comment until
// something passes it. This is the assignment that makes `tsc` check it.
const NODE_DEFS_IS_A_PORT_TABLE: PortTable = NODE_DEFS;

test("NODE_DEFS is usable as geometry's PortTable at run time, not just to tsc", () => {
  // A structural type says the shape fits; it does not say the values are
  // there. Anchor a real port through the real vocabulary.
  const p = portPos(mk("n7", "capture", 620, 320), "frame", "out", NODE_DEFS_IS_A_PORT_TABLE, "desktop");
  // capture is ins:[run], outs:[complete, frame] — "frame" is row 2 of 3.
  eq(p?.x, 620 + NODE_W_WIDE, "capture.frame anchors at the card's right edge");
  eq(p?.y, 320 + 37 + 2 * 20 + 10, "capture.frame sits on the third port row");
});

function mk(id: string, type: FlowNodeType, x: number, y: number): FlowNodeRec {
  return { id, type, x, y, params: {} };
}

// example-m16's node coordinates, verbatim from examples.py:66-73.
const M16: FlowNodeRec[] = [
  mk("n1", "dusk", 30, 50),
  mk("n16", "dome", 260, 50),
  mk("n17", "duskflats", 490, 50),
  mk("n2", "target", 720, 50),
  mk("n4", "slew", 950, 50),
  mk("n5", "autofocus", 160, 320),
  mk("n6", "guide", 390, 320),
  mk("n7", "capture", 620, 320),
  mk("n12", "report", 870, 320),
  mk("n13", "cloudwatch", 30, 560),
  mk("n14", "holdresume", 330, 590),
  mk("n15", "calib", 620, 540),
  mk("n18", "flatpanel", 330, 780),
  mk("n10", "notify", 870, 620),
  mk("n3", "safety", 30, 860),
  mk("n19", "abort", 620, 880),
];
const byId = (id: string): FlowNodeRec => {
  const n = M16.find((m) => m.id === id);
  if (!n) throw new Error(`fixture has no node ${id}`);
  return n;
};

/** The canvas box in the reference captures (§D.2). Not 924×540 — that is the
 *  whole JPEG; 486 is the canvas inside it. */
const CAPTURE_RECT = { width: 924, height: 486 };

const pos = (
  nodeId: string,
  portId: string,
  dir: "in" | "out",
  tier: "phone" | "tablet" | "desktop" = "tablet",
): Point => {
  const p = portPos(byId(nodeId), portId, dir, PORTS, tier);
  if (!p) throw new Error(`no anchor for ${nodeId}.${portId} (${dir})`);
  return p;
};

// ------------------------------------------------------------- card metrics
test("nodeW is device-based: 188 desktop/tablet, 150 phone", () => {
  eq(nodeW("desktop"), NODE_W_WIDE, "desktop");
  eq(nodeW("tablet"), NODE_W_WIDE, "tablet — the reference captures' tier");
  eq(nodeW("phone"), NODE_W_PHONE, "phone");
  eq(NODE_W_WIDE, 188);
  eq(NODE_W_PHONE, 150);
});

test("nodeRows counts inputs THEN outputs, never interleaved", () => {
  eq(nodeRows(byId("n1"), PORTS), 1, "dusk: 0 in + 1 out");
  eq(nodeRows(byId("n4"), PORTS), 2, "slew: 1 in + 1 out");
  eq(nodeRows(byId("n7"), PORTS), 3, "capture: 1 in + 2 out");
  eq(nodeRows(byId("n15"), PORTS), 3, "calib: 3 in + 0 out");
});

test("nodeLayoutHeight is 37 + rows·20 + 26 (the FIT budget, not the DOM)", () => {
  eq(nodeLayoutHeight(byId("n1"), PORTS), 83, "1 row");
  eq(nodeLayoutHeight(byId("n4"), PORTS), 103, "2 rows");
  eq(nodeLayoutHeight(byId("n7"), PORTS), 123, "3 rows");
  // The +8 sibling formula belongs to autoLayout.ts; if this file ever drifts
  // to +8 the fit zoom leaves 46% and every reference capture stops matching.
  assert(
    nodeLayoutHeight(byId("n4"), PORTS) !== 37 + 2 * 20 + 8,
    "fit height must be the +26 variant, not autoLayout's +8",
  );
});

test("fitViewHeight is the same function under §C.5's name", () => {
  eq(fitViewHeight, nodeLayoutHeight, "one implementation, two names");
  eq(fitViewHeight(byId("n7"), PORTS), 123);
});

// ------------------------------------------------------------- port anchors
test("an input anchors on the LEFT edge at n.y + 37 + idx·20 + 10", () => {
  // dome.run is input index 0 — §D.3's worked p2 for edge (a).
  const p = pos("n16", "run", "in");
  eq(p.x, 260, "left edge = the node's own x");
  eq(p.y, 97, "50 + 37 + 0 + 10");
});

test("an output anchors on the RIGHT edge at n.x + nodeW", () => {
  // dusk has no inputs, so its single output is row 0 — §D.3's worked p1 (a).
  const p = pos("n1", "window", "out");
  eq(p.x, 218, "30 + 188");
  eq(p.y, 97, "50 + 37 + 0 + 10");
});

test("an output's row is offset by the node's INPUT count", () => {
  // slew is 1 in + 1 out, so `centered` sits on row 1, not row 0 — this is the
  // half of the formula a naive port-index implementation gets wrong.
  const p = pos("n4", "centered", "out");
  eq(p.x, 1138, "950 + 188");
  eq(p.y, 117, "50 + 37 + 1·20 + 10");
});

test("the second input of a node is one 20px row lower", () => {
  eq(pos("n14", "pause", "in").y, 637, "holdresume.pause = row 0");
  eq(pos("n14", "resume", "in").y, 657, "holdresume.resume = row 1");
  eq(pos("n14", "resume", "in").x, 330, "both on the left edge");
});

test("the phone tier moves the output edge in to 150px", () => {
  eq(pos("n1", "window", "out", "phone").x, 30 + 150);
  eq(pos("n16", "run", "in", "phone").x, 260, "inputs do not move");
});

test("a port the vocabulary does not have yields null, not a header anchor", () => {
  // The prototype falls through with idx === -1, which places the wire 20px
  // above row 0 — inside the card header, indistinguishable from a real
  // anchor. A saved graph can name a port that no longer exists.
  eq(portPos(byId("n1"), "nope", "out", PORTS, "tablet"), null, "unknown id");
  eq(portPos(byId("n1"), "window", "in", PORTS, "tablet"), null,
     "an output id asked for as an input is not an input");
  eq(portPos(byId("n15"), "do", "out", PORTS, "tablet"), null,
     "calib has no outputs at all");
});

// -------------------------------------------------------------- edge paths
test("§D.3 worked edge (a): n1.window → n16.run, the short-span kink", () => {
  const p1 = pos("n1", "window", "out");
  const p2 = pos("n16", "run", "in");
  // dx = 42, so c floors at 46 and the control points CROSS (264 > 214).
  eq(edgePath(p1, p2, "canvas"), "M218 97 C264 97, 214 97, 260 97");
});

test("§D.3 worked edge (b): n4.centered → n5.run, the right-then-back loop", () => {
  const p1 = pos("n4", "centered", "out");
  const p2 = pos("n5", "run", "in");
  // dx = 978 → c = 489. The curve leaves the graph on both sides. It is the
  // design (capture 02), not a routing bug.
  eq(edgePath(p1, p2, "canvas"), "M1138 117 C1627 117, -329 367, 160 367");
});

test("c floors at 46 below |dx| = 92 and tracks |dx|·0.5 above it", () => {
  const at = (dx: number): string =>
    edgePath({ x: 0, y: 0 }, { x: dx, y: 0 }, "canvas");
  eq(at(92), "M0 0 C46 0, 46 0, 92 0", "exactly at the floor");
  eq(at(200), "M0 0 C100 0, 100 0, 200 0", "above it: 200·0.5");
  eq(at(10), "M0 0 C46 0, -36 0, 10 0", "below it: still 46, so it crosses");
  // Right-to-left: |dx| is absolute, and +c/−c do NOT swap.
  eq(at(-200), "M0 0 C100 0, -300 0, -200 0", "the backwards sweep");
});

test("the event lane and the flow lane get byte-identical paths", () => {
  // An event wire differs from a flow wire in stroke colour and dash array,
  // both applied by the wire layer. Geometry never sees the kind — so a test
  // that "covers event vs flow" has to assert exactly that, or it is asserting
  // a difference that does not exist.
  const flowEdge = edgePath(pos("n1", "window", "out"),
                            pos("n16", "run", "in"), "canvas");
  const eventEdge = edgePath({ x: 218, y: 97 }, { x: 260, y: 97 }, "canvas");
  eq(eventEdge, flowEdge, "same endpoints ⇒ same path, whatever the lane");

  // And the real M16 event wire, pinned by number: cloudwatch.in → hold.pause.
  const p1 = pos("n13", "in", "out");
  const p2 = pos("n14", "pause", "in");
  eq(p1.x, 218, "30 + 188");
  eq(p1.y, 607, "560 + 37 + 0 + 10 — the first of cloudwatch's two outputs");
  eq(p2.y, 637, "590 + 37 + 0 + 10");
  // dx = 112 → c = 56, above the floor.
  eq(edgePath(p1, p2, "canvas"), "M218 607 C274 607, 274 637, 330 637");
  // The second event output is one row down, and lands on the second input.
  eq(edgePath(pos("n13", "clear", "out"), pos("n14", "resume", "in"), "canvas"),
     "M218 627 C274 627, 274 657, 330 657");
});

test("phone-flow bends vertically and is a DIFFERENT curve, not a tweak", () => {
  const p1 = pos("n1", "window", "out");
  const p2 = pos("n16", "run", "in");
  const canvas = edgePath(p1, p2, "canvas");
  const phone = edgePath(p1, p2, "phone-flow");
  assert(canvas !== phone, "the two modes must not collapse into one curve");
  // dy = 0 → vy clamps UP to 24. ±18 horizontal, always +18/−18.
  eq(phone, "M218 97 C236 121, 242 73, 260 97");
});

test("phone-flow vy clamps to 24…70 around |dy|·0.4", () => {
  const at = (dy: number): string =>
    edgePath({ x: 0, y: 0 }, { x: 100, y: dy }, "phone-flow");
  eq(at(100), "M0 0 C18 40, 82 60, 100 100", "mid-range: 100·0.4 = 40");
  eq(at(50), "M0 0 C18 24, 82 26, 100 50", "20 → clamped up to 24");
  eq(at(250), "M0 0 C18 70, 82 180, 100 250", "100 → clamped down to 70");
  // Upward wire: |dy| is absolute, so vy is positive and the S still opens
  // downward from the source — the sweep the FLOW tab is designed around.
  eq(at(-250), "M0 0 C18 70, 82 -320, 100 -250", "upward, same ±vy signs");
});

// ------------------------------------------------------------------- fit
test("§D.2 worked fit: M16 in the reference captures' 924×486 canvas", () => {
  const f = fitView(M16, CAPTURE_RECT, PORTS, "tablet");
  assert(f !== null, "a 16-node graph must fit");
  // bbox: x0 = 0, x1 = 1168, y0 = 20, y1 = 993 — height is the binding axis,
  // so zoom is exactly (486 − 40) / (993 − 20).
  eq(f!.zoom, 446 / 973, "the height term wins");
  near(f!.zoom, 0.4584, 5e-5, "zoom");
  eq(Math.round(f!.zoom * 100), 46, "the zoom chip reads 46% in captures 02/03/07/08");
  near(f!.pan.x, 194.308325, 1e-6, "pan.x");
  near(f!.pan.y, -6.167523, 1e-6, "pan.y");
  // The three derived positions §D.2 checks against the captures.
  near(30 * f!.zoom + f!.pan.x, 208, 0.6, "DUSK WINDOW's left edge on screen");
  near(188 * f!.zoom, 86, 0.5, "a card is ~86 screen px wide");
  // §D.2's "top at y≈71" is measured in the CAPTURE, not in the canvas: the
  // canvas sits below the 54px header row (§C.3), and 486 + 54 = 540, which is
  // exactly the reference JPEGs' height. In canvas space the top is 16.75, and
  // 16.75 + 54 = 70.75 ≈ 71. Asserting the canvas number against 71 directly
  // would be asserting the header twice.
  const FLOW_HEADER_H = 54;
  near(50 * f!.zoom + f!.pan.y, 16.75, 0.05, "DUSK WINDOW's top in canvas space");
  near(50 * f!.zoom + f!.pan.y + FLOW_HEADER_H, 71, 0.6, "…and in capture space");
  eq(CAPTURE_RECT.height + FLOW_HEADER_H, 540, "the captures are 924×540");
});

test("the fit pan reserves 34px where the fit scale reserved 40", () => {
  // §D.2: "Note the asymmetry and reproduce it." If both used 40 the graph
  // would sit 3px higher than every capture; if both used 34 the zoom would
  // change. Recomputing pan.y with 40 must NOT reproduce the answer.
  const f = fitView(M16, CAPTURE_RECT, PORTS, "tablet")!;
  const spanY = 993 - 20;
  const withPan34 = (486 - 34 - spanY * f.zoom) / 2 - 20 * f.zoom;
  const withPan40 = (486 - 40 - spanY * f.zoom) / 2 - 20 * f.zoom;
  near(f.pan.y, withPan34, 1e-9, "pan uses 34");
  assert(Math.abs(f.pan.y - withPan40) > 2, "pan must not use the scale's 40");
});

test("fit-to-view on an empty node set returns null, never NaN", () => {
  // Math.min() of nothing is +Infinity: without the guard the bbox, the zoom
  // and both pan components all come back Infinity/NaN, and a NaN transform
  // blanks the canvas with no error anywhere.
  eq(fitView([], CAPTURE_RECT, PORTS, "tablet"), null, "empty graph");
  eq(fitView([], { width: 0, height: 0 }, PORTS, "phone"), null, "empty + no box");
});

test("a zero-sized container still yields finite numbers", () => {
  // The canvas is measured before layout settles on first paint.
  const f = fitView(M16, { width: 0, height: 0 }, PORTS, "tablet");
  assert(f !== null, "nodes exist, so there is a framing");
  eq(f!.zoom, ZOOM_MIN, "the degenerate scale clamps to the floor");
  assert(Number.isFinite(f!.pan.x) && Number.isFinite(f!.pan.y),
         `pan must stay finite, got ${f!.pan.x},${f!.pan.y}`);
});

test("fit clamps UP to 0.35 in a container far too small", () => {
  const f = fitView(M16, { width: 100, height: 100 }, PORTS, "tablet")!;
  eq(f.zoom, ZOOM_MIN, "0.0617 → 0.35");
  // The pan must be computed from the CLAMPED zoom, not the raw ratio.
  near(f.pan.x, (100 - 1168 * ZOOM_MIN) / 2, 1e-9, "pan.x from the clamp");
  near(f.pan.y, (100 - 34 - 973 * ZOOM_MIN) / 2 - 20 * ZOOM_MIN, 1e-9, "pan.y");
});

test("fit clamps DOWN to 1.15 — its own ceiling, not the wheel's 1.6", () => {
  const f = fitView(M16, { width: 4000, height: 3000 }, PORTS, "tablet")!;
  eq(f.zoom, FIT_ZOOM_MAX, "3.04 → 1.15");
  assert(f.zoom < ZOOM_MAX, "fit must never reach the wheel's 1.6 ceiling");
  near(f.pan.x, 1328.4, 1e-9, "(4000 − 1168·1.15)/2");
  near(f.pan.y, 900.525, 1e-9, "(3000 − 34 − 973·1.15)/2 − 20·1.15");
});

test("fit measures the card width per tier (150 on phone, 188 elsewhere)", () => {
  // A tall narrow box makes WIDTH the binding axis, which is the only way the
  // card width shows up in the answer. §D.2's snippet hardcodes 188; the
  // prototype calls nodeW(). Resolved toward nodeW() — see geometry.ts.
  const rect = { width: 600, height: 2000 };
  const wide = fitView(M16, rect, PORTS, "tablet")!;
  const phone = fitView(M16, rect, PORTS, "phone")!;
  eq(wide.zoom, 600 / (950 + 188 + 30), "bbox spans 1168 with 188px cards");
  eq(phone.zoom, 600 / (950 + 150 + 30), "bbox spans 1130 with 150px cards");
  assert(phone.zoom > wide.zoom, "narrower cards fit at a larger zoom");
});

test("clampZoom is the wheel/pinch/button clamp: 0.35 – 1.6", () => {
  eq(clampZoom(5), ZOOM_MAX, "1.6 ceiling");
  eq(clampZoom(0.01), ZOOM_MIN, "0.35 floor");
  eq(clampZoom(0.92), 0.92, "the initial zoom passes through untouched");
  eq(ZOOM_MIN, 0.35);
  eq(ZOOM_MAX, 1.6);
  eq(FIT_ZOOM_MAX, 1.15);
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ngeometry.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
