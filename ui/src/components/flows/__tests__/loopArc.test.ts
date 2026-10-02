// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// loopArc.test.ts - the panel loop's back-arc, as a FORMULA (#189 S4 item 6;
// spec 2026-09-23 flows mosaic, 1.4 "How it is drawn", and S4's test "the
// back-arc stays below the body cards (a formula test)").
//
//   Run directly:  node --import tsx src/components/flows/__tests__/loopArc.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS PINNED. The dashed "pass done" wire runs from the tail of a TARGET's
// panel lane back to that TARGET's `next` input, which sits to the LEFT of
// every stage the wire leaves. The stock S-bezier (`edgePath`) for a
// right-to-left span sweeps back across the lane's cards, which is the drawing
// 1.4 replaces: the arc leaves the source port, drops to
// `max(bottom of the body cards) + 28`, runs left, and rises into `next`. The
// bottoms come from the card formula (`nodeLayoutHeight`), never from the DOM,
// so the tests below compute every card's box from the same formula and ask
// two questions of the emitted path string itself: where the horizontal run
// lies, and whether any point of any segment falls inside a card.
//
// THE FIXTURES. The eighth Example ("M31 3x2, rotating", server
// astrodeck/flows/examples.py `_m31_mosaic`), whose four lane cards stand in
// one row, and a lane whose FILTER CYCLE stands lower than its TARGET, so the
// cycle's bottom and not the TARGET's decides the run. The Example is
// transcribed here and held to the server file by the first test, so a moved
// card in examples.py fails this file rather than leaving it grading a layout
// nobody ships.
//
// NAMED MUTATIONS, each run in a private scratch copy of ui/ (never the
// shared tree), with the observed failure recorded beside the test it turns
// red:
//   "stock edgePath S-bezier"  - loopArc's `d` is `edgePath(p1, p2, "canvas")`
//   "TARGET card bottom only"  - the run is the TARGET's bottom + 28, the lane
//                                ignored
//   "drop and rise at the end cards only" - the legs placed outside the source
//                                and the TARGET alone, the rest of the lane
//                                ignored (added by the S4-UARC verifier)
//   "avoid ignored"            - a surface's `avoid` cards never push the run
//                                (#360, S5-LOOP)

import { readFileSync } from "node:fs";

import {
  cardBox, loopArc, loopArcPath, loopControlSpot, lowerLoopRun, edgePath, portPos,
  LOOP_ARC_DROP, LOOP_ARC_STUB, NODE_W_PHONE, NODE_W_WIDE,
  type CardBox, type Point,
} from "../geometry";
import { NODE_DEFS } from "../nodeDefs";
import { panelLane } from "../panelLane";
import { AUTO_PAD, computeAutoLayout } from "../autoLayout";
import { COLUMN_LOOP_DROP, COLUMN_LOOP_STUB } from "../targetSummary";
import type { FlowEdgeRec, FlowNodeRec, FlowNodeType } from "../flowsTypes";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg}\n    expected ${String(b)}\n    got      ${String(a)}`);
}
function assert(cond: unknown, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ---------------------------------------------------------------- fixtures
type NodeRow = [string, FlowNodeType, number, number];
type EdgeRow = [string, string, string, string];

/** examples.py `_m31_mosaic`, node tuples and wires in the file's order. */
const M31_MOSAIC_NODES: NodeRow[] = [
  ["n1", "dusk", 30, 60], ["n2", "target", 270, 60],
  ["n5", "autofocus", 510, 60], ["n6", "guide", 750, 60],
  ["n7", "cycle", 990, 60], ["n12", "report", 1230, 60],
  ["n3", "safety", 30, 400], ["n11", "abort", 270, 400],
  ["n8", "condition", 990, 400], ["n9", "refocus", 1230, 400],
];
const M31_MOSAIC_EDGES: EdgeRow[] = [
  ["n1", "window", "n2", "arm"], ["n2", "target", "n5", "run"],
  ["n5", "focused", "n6", "run"], ["n6", "guiding", "n7", "run"],
  ["n7", "complete", "n12", "session"],
  ["n7", "pass", "n2", "next"],
  ["n7", "frame", "n8", "events"], ["n8", "fire", "n9", "do"],
  ["n3", "unsafe", "n11", "do"],
];

function graphOf(nodes: NodeRow[], edges: EdgeRow[]): {
  nodes: FlowNodeRec[]; edges: FlowEdgeRec[];
} {
  return {
    nodes: nodes.map(([id, type, x, y]) => ({
      id, type, x, y,
      params: type === "target"
        ? { ...NODE_DEFS.target.params, rows: 2, cols: 3 }
        : { ...NODE_DEFS[type].params },
    })),
    edges: edges.map(([from, fromPort, to, toPort], i) => ({
      id: `e${i}`, from, fromPort, to, toPort,
    })),
  };
}

const M31 = graphOf(M31_MOSAIC_NODES, M31_MOSAIC_EDGES);
const byId = (g: { nodes: FlowNodeRec[] }, id: string): FlowNodeRec => {
  const n = g.nodes.find((m) => m.id === id);
  if (!n) throw new Error(`precondition: no node ${id}`);
  return n;
};

/** TARGET -> AUTOFOCUS -> FILTER CYCLE, the cycle standing 50 px LOWER than
 *  the TARGET. The TARGET's formula bottom is 60 + 123 = 183 and the cycle's
 *  110 + 143 = 253, so a run placed under the TARGET alone (183 + 28 = 211)
 *  lies inside the cycle's box, which spans y 110 to 253. */
const TALL = graphOf(
  [["t", "target", 100, 60], ["af", "autofocus", 340, 60], ["cy", "cycle", 580, 110]],
  [["t", "target", "af", "run"], ["af", "focused", "cy", "run"], ["cy", "pass", "t", "next"]],
);

// ------------------------------------------------------------ path reading
/** Every drawn point of an SVG path built from M, L, Q and C commands (the
 *  only ones the arc and the stock bezier emit), sampled 64 times per
 *  segment. Sampling the curve is what makes the question "does any point of
 *  the path fall inside a card" answerable from the string alone. */
function samplePath(d: string): Point[] {
  const tokens = d.match(/[MLQC]|-?\d*\.?\d+(?:e[-+]?\d+)?/gi) ?? [];
  const out: Point[] = [];
  let i = 0;
  let cur: Point = { x: 0, y: 0 };
  const num = (): number => Number(tokens[i++]);
  const pt = (): Point => ({ x: num(), y: num() });
  let cmd = "";
  while (i < tokens.length) {
    if (/^[MLQC]$/i.test(tokens[i])) cmd = tokens[i++].toUpperCase();
    if (cmd === "M") { cur = pt(); out.push(cur); continue; }
    if (cmd === "L") {
      const p = pt();
      for (let k = 1; k <= 64; k++) {
        const t = k / 64;
        out.push({ x: cur.x + (p.x - cur.x) * t, y: cur.y + (p.y - cur.y) * t });
      }
      cur = p;
      continue;
    }
    if (cmd === "Q") {
      const c = pt(); const p = pt();
      for (let k = 1; k <= 64; k++) {
        const t = k / 64; const u = 1 - t;
        out.push({ x: u * u * cur.x + 2 * u * t * c.x + t * t * p.x,
                   y: u * u * cur.y + 2 * u * t * c.y + t * t * p.y });
      }
      cur = p;
      continue;
    }
    if (cmd === "C") {
      const c1 = pt(); const c2 = pt(); const p = pt();
      for (let k = 1; k <= 64; k++) {
        const t = k / 64; const u = 1 - t;
        out.push({
          x: u * u * u * cur.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * p.x,
          y: u * u * u * cur.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * p.y,
        });
      }
      cur = p;
      continue;
    }
    throw new Error(`the path has a command this reader does not know: ${cmd} in ${d}`);
  }
  return out;
}

/** The horizontal straight segments of a path, as [y, xFrom, xTo]. */
function horizontalRuns(d: string): [number, number, number][] {
  const tokens = d.match(/[MLQC]|-?\d*\.?\d+(?:e[-+]?\d+)?/gi) ?? [];
  const runs: [number, number, number][] = [];
  let i = 0;
  let cur: Point = { x: 0, y: 0 };
  let cmd = "";
  const num = (): number => Number(tokens[i++]);
  while (i < tokens.length) {
    if (/^[MLQC]$/i.test(tokens[i])) cmd = tokens[i++].toUpperCase();
    const arity = cmd === "C" ? 3 : cmd === "Q" ? 2 : 1;
    let p: Point = cur;
    for (let k = 0; k < arity; k++) p = { x: num(), y: num() };
    if (cmd === "L" && p.y === cur.y && p.x !== cur.x) runs.push([p.y, cur.x, p.x]);
    cur = p;
  }
  return runs;
}

const inside = (p: Point, b: CardBox): boolean =>
  p.x > b.x && p.x < b.x + b.w && p.y > b.y && p.y < b.y + b.h;

/** The first sampled point of `d` inside any of `cards`' formula boxes, named,
 *  or null. */
function firstIntrusion(d: string, cards: FlowNodeRec[], tier: "desktop" | "phone" = "desktop"): string | null {
  const boxes = cards.map((n) => [n, cardBox(n, NODE_DEFS, tier)] as const);
  for (const p of samplePath(d)) {
    for (const [n, b] of boxes) {
      if (inside(p, b)) {
        return `(${p.x.toFixed(1)}, ${p.y.toFixed(1)}) is inside ${n.type.toUpperCase()} `
          + `${n.id} [x ${b.x}..${b.x + b.w}, y ${b.y}..${b.y + b.h}]`;
      }
    }
  }
  return null;
}

// ======================================================= the eighth Example

// Guards the transcription, not the arc: a card moved in examples.py would
// otherwise leave the formula test below grading a layout nobody ships. RED
// under a hand edit of the server file (n7's y 60 -> 64 in a scratch copy of
// examples.py read by a scratch copy of this file), observed (the file's list
// is printed as "expected"):
//   x the transcribed eighth Example is examples.py's `_m31_mosaic`: nodes
//     expected n1 dusk 30 60|n2 target 270 60|n5 autofocus 510 60|n6 guide 750 60|
//              n7 cycle 990 64|n12 report 1230 60|n3 safety 30 400|n11 abort 270 400|
//              n8 condition 990 400|n9 refocus 1230 400
//     got      n1 dusk 30 60|n2 target 270 60|n5 autofocus 510 60|n6 guide 750 60|
//              n7 cycle 990 60|n12 report 1230 60|n3 safety 30 400|n11 abort 270 400|
//              n8 condition 990 400|n9 refocus 1230 400
test("the transcribed eighth Example is examples.py's `_m31_mosaic`", () => {
  const src = readFileSync(
    new URL("../../../../../server/astrodeck/flows/examples.py", import.meta.url), "utf8");
  const start = src.indexOf("def _m31_mosaic");
  const end = src.indexOf("\ndef ", start + 1);
  assert(start >= 0 && end > start, "precondition: examples.py still defines _m31_mosaic");
  const body = src.slice(start, end);
  const nodes = [...body.matchAll(/\("(\w+)", "(\w+)", (\d+), (\d+)\)/g)]
    .map((m) => `${m[1]} ${m[2]} ${m[3]} ${m[4]}`).join("|");
  const edges = [...body.matchAll(/\("(\w+)", "(\w+)", "(\w+)", "(\w+)"\)/g)]
    .map((m) => `${m[1]}.${m[2]}>${m[3]}.${m[4]}`).join("|");
  eq(M31_MOSAIC_NODES.map((r) => r.join(" ")).join("|"), nodes, "nodes");
  eq(M31_MOSAIC_EDGES.map(([a, ap, b, bp]) => `${a}.${ap}>${b}.${bp}`).join("|"), edges, "edges");
});

// MUTANT "TARGET card bottom only" (loopArc's run is
// `Math.max(p1.y, p2.y, body[0].y + body[0].h) + LOOP_ARC_DROP`, body[0] being
// the TARGET: the lane is not read). Observed, loopArc.test 8/11:
//   x the eighth Example: the run lies at max(bottom of the owned cards) + 28:
//     the run sits under the lowest owned card, the FILTER CYCLE (60 + 143)
//     expected 231
//     got      211
// MUTANT "stock edgePath S-bezier" (loopArc's `d` is
// `edgePath(p1, p2, "canvas")`). Observed, loopArc.test 4/11:
//   x the eighth Example: the run lies at max(bottom of the owned cards) + 28:
//     one straight horizontal run at the run's height, from right of the
//     cycle to left of the TARGET; runs found: []
test("the eighth Example: the run lies at max(bottom of the owned cards) + 28", () => {
  const lane = panelLane(M31, "n2");
  eq(lane.map((n) => n.id).join(","), "n5,n6,n7", "precondition: the lane is AF, GUIDE, CYCLE");
  const arc = loopArc(byId(M31, "n7"), "pass", byId(M31, "n2"), "next", lane, NODE_DEFS, "desktop");
  assert(arc, "no arc for the Example's own loop wire");
  // Computed from the formula by hand, so the test does not restate the
  // implementation: TARGET 3 rows (37 + 60 + 26 = 123, bottom 183), AUTOFOCUS
  // and GUIDE 3 rows since they gained `pass` (#331; 123, bottom 183), FILTER
  // CYCLE 4 rows (143, bottom 203).
  eq(arc!.runY, 203 + 28, "the run sits under the lowest owned card, the FILTER CYCLE (60 + 143)");
  const runs = horizontalRuns(arc!.d).filter(([y]) => y === 231);
  const cycleRight = 990 + NODE_W_WIDE;
  assert(runs.some(([, a, b]) => Math.max(a, b) > cycleRight && Math.min(a, b) < 270),
    "one straight horizontal run at the run's height, from right of the cycle to left of "
    + `the TARGET; runs found: ${JSON.stringify(horizontalRuns(arc!.d))}`);
});

// MUTANT "stock edgePath S-bezier". Observed:
//   x the eighth Example: no point of the arc falls inside an owned card: a
//     point of the loop wire
//     expected null
//     got      (1163.8, 160.8) is inside CYCLE n7 [x 990..1178, y 60..203]
// (and the same shape in the phone-tier, lower-cycle and CONTROL cases below,
// and in the two end/handle cases). Under "TARGET card bottom only" this case
// stays green - 211 clears every card of a one-row lane - which is why the
// lower-cycle case below exists.
test("the eighth Example: no point of the arc falls inside an owned card", () => {
  const lane = panelLane(M31, "n2");
  const d = loopArcPath(byId(M31, "n7"), "pass", byId(M31, "n2"), "next", lane, NODE_DEFS, "desktop");
  assert(d, "no path for the Example's own loop wire");
  const hit = firstIntrusion(d!, [byId(M31, "n2"), ...lane]);
  eq(hit, null, "a point of the loop wire");
});

// MUTANT "TARGET card bottom only". Observed (the run is 245, not 211: the
// cycle's own pass anchor, 110 + 37 + 3 x 20 + 10 = 217, is in the max):
//   x a lane with a card lower than its TARGET: the run clears that card: the
//     run
//     expected 281
//     got      245
//   x a lane with a card lower than its TARGET: no point of the arc falls
//     inside a card: a point of the loop wire
//     expected null
//     got      (760.3, 245.0) is inside CYCLE cy [x 580..768, y 110..253]
// "stock edgePath S-bezier" turns the second red too:
//     got      (757.6, 202.9) is inside CYCLE cy [x 580..768, y 110..253]
test("a lane with a card lower than its TARGET: the run clears that card", () => {
  const lane = panelLane(TALL, "t");
  const arc = loopArc(byId(TALL, "cy"), "pass", byId(TALL, "t"), "next", lane, NODE_DEFS, "desktop");
  assert(arc, "no arc");
  eq(arc!.runY, 110 + 143 + 28, "the run");
});

test("a lane with a card lower than its TARGET: no point of the arc falls inside a card", () => {
  const lane = panelLane(TALL, "t");
  const d = loopArcPath(byId(TALL, "cy"), "pass", byId(TALL, "t"), "next", lane, NODE_DEFS, "desktop");
  assert(d, "no path");
  eq(firstIntrusion(d!, [byId(TALL, "t"), ...lane]), null, "a point of the loop wire");
});

// CONTROL: when the TARGET is the lowest card the two rules agree, so the
// run is the TARGET's bottom + 28 - the max includes the TARGET itself, which
// the arc passes under on its way to `next`.
test("CONTROL: a TARGET lower than its lane puts the run under the TARGET", () => {
  const g = graphOf(
    [["t", "target", 100, 200], ["cy", "cycle", 340, 60]],
    [["t", "target", "cy", "run"], ["cy", "pass", "t", "next"]],
  );
  const arc = loopArc(byId(g, "cy"), "pass", byId(g, "t"), "next", panelLane(g, "t"), NODE_DEFS, "desktop");
  eq(arc!.runY, 200 + 123 + LOOP_ARC_DROP, "the run under a TARGET lower than its lane");
  eq(firstIntrusion(arc!.d, g.nodes), null, "a point of the loop wire");
});

// THE LEGS STAND OUTSIDE THE WHOLE BODY, not outside the two end cards. A lane
// need not be drawn left to right: a stage moved right of the tail, or left of
// the TARGET, and below the port the arc leaves or enters, stands exactly where
// a leg placed by the end cards alone would run. Every fixture above is drawn
// left to right, where the two rules agree, so this case is the only one that
// can tell them apart. Both stages sit below their port, so the short stubs at
// port height (which loopArc's comment says can still meet a card drawn level
// with them) stay clear and only the legs are graded. Added by the S4-UARC
// verifier. MUTANT "drop and rise at the end cards only" (dropX is
// `Math.max(p1.x, from.x + nodeW(tier)) + LOOP_ARC_STUB` and riseX is
// `Math.min(p2.x, to.x) - LOOP_ARC_STUB`), run in a private scratch copy of
// ui/. Observed, loopArc.test 11/13 (the two tests below; re-run by a second
// verifier after AUTOFOCUS gained its third row, #331, which is why its box
// is 123 tall):
//   x a stage right of the tail: the drop stands outside it: a stage right of
//     the tail, below its pass port
//     expected null
//     got      (552.0, 231.6) is inside AUTOFOCUS af [x 500..688, y 230..353]
//   x a stage left of the TARGET: the rise stands outside it: a stage left of
//     the TARGET, below its next port
//     expected null
//     got      (276.0, 371.2) is inside AUTOFOCUS af [x 100..288, y 250..373]
test("a stage right of the tail: the drop stands outside it", () => {
  // TARGET -> AUTOFOCUS -> FILTER CYCLE, the AUTOFOCUS moved right of the
  // cycle (the tail) and below its pass port (y 167): AUTOFOCUS spans x 500 to
  // 688, so the drop belongs at 688 + 24, not at the cycle's 528 + 24.
  const g = graphOf(
    [["t", "target", 100, 60], ["cy", "cycle", 340, 60], ["af", "autofocus", 500, 230]],
    [["t", "target", "af", "run"], ["af", "focused", "cy", "run"], ["cy", "pass", "t", "next"]],
  );
  const lane = panelLane(g, "t");
  eq(lane.map((n) => n.id).join(","), "af,cy", "precondition: the lane is AF, then the CYCLE (the tail)");
  const arc = loopArc(byId(g, "cy"), "pass", byId(g, "t"), "next", lane, NODE_DEFS, "desktop")!;
  eq(firstIntrusion(arc.d, g.nodes), null, "a stage right of the tail, below its pass port");
  eq(arc.handle.x, 500 + NODE_W_WIDE + LOOP_ARC_STUB, "the drop's x");
});

test("a stage left of the TARGET: the rise stands outside it", () => {
  // The mirror: AUTOFOCUS moved left of the TARGET and below its `next` port
  // (y 127). It spans x 100 to 288, so the rise belongs at 100 - 24, not at
  // the TARGET's 300 - 24.
  const g = graphOf(
    [["t", "target", 300, 60], ["af", "autofocus", 100, 250], ["cy", "cycle", 540, 60]],
    [["t", "target", "af", "run"], ["af", "focused", "cy", "run"], ["cy", "pass", "t", "next"]],
  );
  const lane = panelLane(g, "t");
  eq(lane.map((n) => n.id).join(","), "af,cy", "precondition: the lane is AF, then the CYCLE (the tail)");
  const arc = loopArc(byId(g, "cy"), "pass", byId(g, "t"), "next", lane, NODE_DEFS, "desktop")!;
  eq(firstIntrusion(arc.d, g.nodes), null, "a stage left of the TARGET, below its next port");
});

// ============================================================ the two ends

test("the arc leaves the source port and ends in the `next` port, both heading right", () => {
  const lane = panelLane(M31, "n2");
  const from = byId(M31, "n7");
  const to = byId(M31, "n2");
  const arc = loopArc(from, "pass", to, "next", lane, NODE_DEFS, "desktop")!;
  const p1 = portPos(from, "pass", "out", NODE_DEFS, "desktop")!;
  const p2 = portPos(to, "next", "in", NODE_DEFS, "desktop")!;
  const pts = samplePath(arc.d);
  eq(`${pts[0].x} ${pts[0].y}`, `${p1.x} ${p1.y}`, "the first point is the pass port");
  const last = pts[pts.length - 1];
  eq(`${last.x} ${last.y}`, `${p2.x} ${p2.y}`, "the last point is the next port");
  // Leaving to the RIGHT of the tail and arriving from the LEFT of the TARGET
  // is what keeps both ends off the cards they belong to.
  assert(pts[1].x > p1.x && pts[1].y === p1.y, "the arc leaves the pass port heading right");
  const before = pts[pts.length - 2];
  assert(before.x < p2.x && before.y === p2.y, "the arc enters the next port heading right");
});

test("the chip's place is on the run and the handle's is on the drop, both on the path", () => {
  const lane = panelLane(M31, "n2");
  const arc = loopArc(byId(M31, "n7"), "pass", byId(M31, "n2"), "next", lane, NODE_DEFS, "desktop")!;
  const pts = samplePath(arc.d);
  const onPath = (q: Point) => pts.some((p) => Math.abs(p.x - q.x) < 1 && Math.abs(p.y - q.y) < 1);
  eq(arc.label.y, arc.runY, "the chip sits on the run");
  assert(onPath(arc.label), `the chip's point ${JSON.stringify(arc.label)} is not on the path`);
  assert(onPath(arc.handle), `the handle's point ${JSON.stringify(arc.handle)} is not on the path`);
  // The handle stands clear of the tail's card: the remove control is 22 px
  // across, and the drop is LOOP_ARC_STUB right of the card's edge.
  eq(arc.handle.x, 990 + NODE_W_WIDE + LOOP_ARC_STUB, "the handle's x is the drop's");
});

test("the phone tier measures the cards 150 px wide", () => {
  const lane = panelLane(M31, "n2");
  const arc = loopArc(byId(M31, "n7"), "pass", byId(M31, "n2"), "next", lane, NODE_DEFS, "phone")!;
  eq(arc.handle.x, 990 + NODE_W_PHONE + LOOP_ARC_STUB, "the drop hugs a 150 px card");
  eq(firstIntrusion(arc.d, [byId(M31, "n2"), ...lane], "phone"), null, "a point of the loop wire");
});

test("a port the vocabulary does not have draws no arc", () => {
  const lane = panelLane(M31, "n2");
  eq(loopArc(byId(M31, "n7"), "nope", byId(M31, "n2"), "next", lane, NODE_DEFS, "desktop"), null,
    "an unknown source port");
  eq(loopArcPath(byId(M31, "n7"), "pass", byId(M31, "n2"), "nope", lane, NODE_DEFS, "desktop"), null,
    "an unknown destination port");
});

// ============================================== a surface's own numbers (#360)
//
// `loopArc`'s `opts` bend the stub, the drop and what the run must pass under
// for a surface the canvas's numbers do not fit: the classic phone FLOW tab's
// column layout (targetSummary `loopArcOf` in "phone-flow" mode, graded on the
// mounted tab by loopArcDom.test.tsx). Here, the formula.
//
// MUTANT "avoid ignored" (geometry.ts loopArc: every `avoid` card filtered
// out, so the run is the body's bottom + drop whatever lies there), run in the
// private scratch copy scratchpad/S5-LOOP-mut. Observed, loopArc.test 16/17
// (and loopArcDom.test.tsx's stacked-card case with it):
//   x opts: a card the run would cross pushes it below that card, drop clear,
//     and no further: a run that would cross the card goes under it
//     expected 350
//     got      231

test("opts: the stub and the drop move the legs and the run by exactly what they say", () => {
  const lane = panelLane(M31, "n2");
  const arc = loopArc(byId(M31, "n7"), "pass", byId(M31, "n2"), "next", lane, NODE_DEFS, "desktop",
    { stub: 7, drop: 8 })!;
  eq(arc.handle.x, 990 + NODE_W_WIDE + 7, "the drop 7 px right of the cycle");
  eq(arc.runY, 203 + 8, "the run 8 px under the cycle");
  const xs = samplePath(arc.d).map((p) => p.x);
  eq(Math.min(...xs), 270 - 7, "the rise 7 px left of the TARGET");
});

test("opts: a card the run would cross pushes it below that card, drop clear, and no further", () => {
  // The eighth Example's lane with a REPORT placed 16 px under the cycle, as
  // the column layout places the card after the tail: a run 8 px under the
  // cycle clears it (8 px above it); one 28 px under would be inside it.
  const lane = panelLane(M31, "n2");
  const under: FlowNodeRec = { ...byId(M31, "n12"), x: 800, y: 203 + 16 };
  const fits = loopArc(byId(M31, "n7"), "pass", byId(M31, "n2"), "next", lane, NODE_DEFS, "desktop",
    { drop: 8, avoid: [under] })!;
  eq(fits.runY, 203 + 8, "a run that fits in the gap stays there");
  const deep = loopArc(byId(M31, "n7"), "pass", byId(M31, "n2"), "next", lane, NODE_DEFS, "desktop",
    { avoid: [under] })!;
  const bottom = under.y + cardBox(under, NODE_DEFS, "desktop").h;
  eq(deep.runY, bottom + LOOP_ARC_DROP, "a run that would cross the card goes under it");
  eq(firstIntrusion(deep.d, [byId(M31, "n2"), ...lane, under]), null, "a point of the pushed arc");
  // A second card under the first, 4 px apart: the run goes under both.
  const below: FlowNodeRec = { ...under, id: "x2", y: bottom + 4 };
  const both = loopArc(byId(M31, "n7"), "pass", byId(M31, "n2"), "next", lane, NODE_DEFS, "desktop",
    { avoid: [under, below] })!;
  eq(both.runY, below.y + cardBox(below, NODE_DEFS, "desktop").h + LOOP_ARC_DROP, "under a stack of two");
});

test("CONTROL: a card beside the run's span, or already clear below it, does not move the run", () => {
  const lane = panelLane(M31, "n2");
  // Right of the drop leg (x 1202): the run never reaches it.
  const beside: FlowNodeRec = { ...byId(M31, "n12"), x: 1210, y: 210 };
  // Under the run by more than the drop.
  const clear: FlowNodeRec = { ...byId(M31, "n12"), x: 600, y: 231 + LOOP_ARC_DROP };
  const arc = loopArc(byId(M31, "n7"), "pass", byId(M31, "n2"), "next", lane, NODE_DEFS, "desktop",
    { avoid: [beside, clear, ...M31.nodes] })!;
  eq(arc.runY, 231, "the canvas run, whatever else is on the surface");
});

test("the column layout's numbers: legs half the gutter out, the run in the middle of its gap", () => {
  // Measured on the real layout of the eighth Example at 375 px, not restated:
  // the gap between the tail's formula box and the box of the card the layout
  // puts after it, and the gutter between the columns and the container.
  const lay = computeAutoLayout(M31, 375, NODE_DEFS);
  const box = (id: string) => cardBox({ ...byId(M31, id), ...lay.pos[id] }, NODE_DEFS, "phone");
  const tail = box("n7");
  const next = box("n12");
  eq(COLUMN_LOOP_DROP * 2, next.y - (tail.y + tail.h), "twice the drop is the gap under the tail");
  eq(COLUMN_LOOP_STUB * 2, AUTO_PAD, "twice the stub is the gutter");
  eq(tail.x, AUTO_PAD, "precondition: the left column stands AUTO_PAD in");
});

// ================================== a lowered run (#357) and a clear spot (#502)
//
// `lowerLoopRun` is how the #/next layer keeps its run clear of a card that
// grows past the formula box (canvas/__tests__/loopArcNext.test.tsx grades it
// mounted); `loopControlSpot` is where the classic phone FLOW tab puts the
// loop's remove control (flowWireDeleteLoop.test.tsx grades it mounted, one
// mutant per condition). Here, the formula of each, on the eighth Example's
// desktop arc: the drop at x 1202 from y 167 to 231, the rise at x 246 from
// y 127 to 231.

// CONTROL for every `lowerLoopRun` mutant, and for the refactor that moved
// the chip and the handle into one builder: lowered by nothing, the arc is
// the arc, and it is still the route the tests above pin.
test("CONTROL: lowerLoopRun by 0 hands back the same route", () => {
  const arc = loopArc(byId(M31, "n7"), "pass", byId(M31, "n2"), "next", panelLane(M31, "n2"),
    NODE_DEFS, "desktop")!;
  const same = lowerLoopRun(arc, 0);
  eq(same.d, arc.d, "the path");
  eq(JSON.stringify([same.runY, same.label, same.handle]),
    JSON.stringify([231, { x: 724, y: 231 }, { x: 1202, y: 199 }]), "the run, the chip, the handle");
});

// MUTANT "only the drop's foot lowered" (lowerLoopRun moves the bottom of the
// drop and not the far end of the run, so the run slants), run in the private
// copy scratchpad/H4-UCANVAS-mut. Observed, loopArc.test 19/20 (and
// loopArcNext.test.tsx 9/12, its path, run and CONTROL cases):
//   x lowerLoopRun: the run, its chip and the handle move; the anchors and the
//     legs' x do not: one straight run at 332 from right of the cycle to left
//     of the TARGET: [[167,1178,1192],[127,256,270]]
test("lowerLoopRun: the run, its chip and the handle move; the anchors and the legs' x do not", () => {
  const arc = loopArc(byId(M31, "n7"), "pass", byId(M31, "n2"), "next", panelLane(M31, "n2"),
    NODE_DEFS, "desktop")!;
  const low = lowerLoopRun(arc, 101);
  eq(low.runY, 332, "the run 101 px lower");
  const runs = horizontalRuns(low.d).filter(([y]) => y === 332);
  assert(runs.some(([, a, b]) => Math.max(a, b) > 1178 && Math.min(a, b) < 270),
    `one straight run at 332 from right of the cycle to left of the TARGET: ${JSON.stringify(horizontalRuns(low.d))}`);
  eq(JSON.stringify([low.label, low.handle]),
    JSON.stringify([{ x: 724, y: 332 }, { x: 1202, y: (167 + 332) / 2 }]), "the chip and the handle");
  const pts = samplePath(low.d);
  eq(`${pts[0].x} ${pts[0].y}|${pts[pts.length - 1].x} ${pts[pts.length - 1].y}`,
    "1178 167|270 127", "the two anchors");
  eq(Math.max(...pts.map((p) => p.x)), 1202, "the drop's x");
  eq(Math.min(...pts.map((p) => p.x)), 246, "the rise's x");
});

// The remove control's place on the same arc, a 26 px box, with made-up
// cards standing beside the legs. Each mutant run in the private copy
// scratchpad/H4-UCANVAS-mut. MUTANT "the rise before the drop" (the two legs
// tried in the other order). Observed, loopArc.test 19/20 (and
// flowWireDeleteLoop.test.tsx 5/9, the eighth Example's phone control moved
// to the rise):
//   x loopControlSpot: the nearest clear height to a leg's middle, the drop
//     first, else none: no card: the drop's middle
//     expected 1202,199
//     got      246,179
// MUTANT "the first clear height, not the nearest" (the candidates taken in
// order, unsorted). Observed, loopArc.test 19/20 (and
// flowWireDeleteLoop.test.tsx 6/9, the right-column tail's control at
// 13,263):
//   x loopControlSpot: the nearest clear height to a leg's middle, the drop
//     first, else none: the nearer end of the band the card rules out
//     expected 1202,213
//     got      1202,177
// MUTANT "a place over a card rather than none" (the drop's middle returned
// when no height on either leg is clear). Observed, loopArc.test 19/20
// (flowWireDeleteLoop.test.tsx 9/9: its lanes always have a clear height,
// and FlowWireDelete's own fallback is that same place):
//   x loopControlSpot: the nearest clear height to a leg's middle, the drop
//     first, else none: no clear height on either leg
//     expected none
//     got      1202,199
// And the three #502 mutants flowWireDeleteLoop.test.tsx records, one per
// condition, each turn one case here red: "the box centred on the leg" and
// "held a whole box in" the container case ("expected 1195,199", got
// 1202,199 and 1182,199), "no card rules a height out" the band case
// ("expected 1202,213, got 1202,199").
test("loopControlSpot: the nearest clear height to a leg's middle, the drop first, else none", () => {
  const arc = loopArc(byId(M31, "n7"), "pass", byId(M31, "n2"), "next", panelLane(M31, "n2"),
    NODE_DEFS, "desktop")!;
  const at = (cards: CardBox[], x1 = 10_000): string => {
    const p = loopControlSpot(arc, 26, 0, x1, cards);
    return p ? `${p.x},${p.y}` : "none";
  };
  // Nothing beside it: the middle of the drop, which is the handle.
  eq(at([]), "1202,199", "no card: the drop's middle");
  // A card beside the drop from y 190 to 200 rules out centres from 177 to
  // 213; 213 is nearer the middle (199) than 177.
  const low: CardBox = { x: 1190, y: 190, w: 60, h: 10 };
  eq(at([low]), "1202,213", "the nearer end of the band the card rules out");
  // A card beside the whole drop: the rise's middle, (127 + 231) / 2.
  const wall: CardBox = { x: 1190, y: 100, w: 60, h: 200 };
  eq(at([wall]), "246,179", "the drop blocked: the rise's middle");
  // Both legs blocked: none.
  eq(at([wall, { x: 230, y: 100, w: 30, h: 200 }]), "none", "no clear height on either leg");
  // A container edge 6 px right of the drop holds the box in by 7.
  eq(at([], 1208), "1195,199", "held inside the container");
});

// CONTROL: the stock bezier is untouched. Every other wire still draws with
// `edgePath`, whose §D.3 worked strings geometry.test.ts pins.
test("CONTROL: edgePath still draws the stock S-bezier", () => {
  eq(edgePath({ x: 218, y: 97 }, { x: 260, y: 97 }, "canvas"),
    "M218 97 C264 97, 214 97, 260 97", "§D.3's worked short span");
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nloopArc.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
