// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowWireDeleteLoop.test.tsx - the classic remove control of a panel loop
// wire sits ON the arc, MOUNTED (#355; spec 2026-09-23 flows mosaic, 1.4 "How
// it is drawn").
//
//   Run directly:  node --import tsx src/components/flows/__tests__/flowWireDeleteLoop.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY. FlowWireDelete places its control at the midpoint of the wire's two
// port anchors, which is exactly on every bezier `edgePath` draws (the file's
// own header proves it). The panel loop is not a bezier: it drops below the
// lane's cards, runs left and rises into the TARGET's `next`. On the eighth
// Example the anchors' midpoint is (724, 147), the gap between AUTOFOCUS
// (x 510..698) and GUIDE (x 750..938) at port height, while the arc's run is
// at y 231 and its drop at x 1202: a remove control floating between two
// stages with no wire under it. #/next moved its control onto the arc's
// `handle` in S4 (canvas/__tests__/loopArcNext.test.tsx); the classic canvas
// kept the midpoint until #355.
//
// WHAT IS GUARDED
//   1. The selected loop wire's control is at the arc's handle, (1202, 199),
//      and its 22 px box covers no card.
//   2. On the phone FLOW tab the control covers the arc that tab DRAWS, from
//      the same positions and the same column-layout arc (#360): a control
//      placed from the canvas's arc would stand off the drawn wire.
//   3. And there its 26 px box lies inside the tab's container, which clips,
//      at 360, 375 and 414 px (#428): centred on the drop, 7 px inside the
//      edge, it reached 6 px past it and was drawn cut off.
//   4. And there the box meets no card the tab lays out (#502): moved in off
//      the edge it reached 12 px into the right column, and on a lane whose
//      tail sits in that column the middle of the drop is beside the tail,
//      so the box covered the tail's corner. Graded on the eighth Example and
//      on such a lane, at the same three widths, with a named mutant for each
//      of the three conditions.
//   5. CONTROL: any other selected wire's control is still at its anchors'
//      midpoint.
//
// Each named mutation was run in a private scratch copy of ui/ (never the
// shared tree, #254), and what it turned red is recorded beside the test.

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
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";

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
function ok(cond: unknown, msg: string): void {
  if (!cond) throw new Error(msg);
}

const { useStore } = await import("../../../store");
const { NODE_DEFS } = await import("../nodeDefs");
const { FLOWS_INIT } = await import("../flowsSlice");
const { cardBox, loopArc, portPos } = await import("../geometry");
const { computeAutoLayout } = await import("../autoLayout");
const { panelLane } = await import("../panelLane");
const FlowWireLayer = (await import("../FlowWireLayer")).default;
const FlowWireDelete = (await import("../FlowWireDelete")).default;
type FlowNodeRec = import("../flowsTypes").FlowNodeRec;
type FlowEdgeRec = import("../flowsTypes").FlowEdgeRec;

// ------------------------------------------------------------------- fixture
// The eighth Example (server flows/examples.py `_m31_mosaic`), the layout
// loopArc.test.ts holds to the server file.
const ROWS: [string, string, number, number][] = [
  ["n1", "dusk", 30, 60], ["n2", "target", 270, 60],
  ["n5", "autofocus", 510, 60], ["n6", "guide", 750, 60],
  ["n7", "cycle", 990, 60], ["n12", "report", 1230, 60],
  ["n3", "safety", 30, 400], ["n11", "abort", 270, 400],
  ["n8", "condition", 990, 400], ["n9", "refocus", 1230, 400],
];
const NODES: FlowNodeRec[] = ROWS.map(([id, type, x, y]) => ({
  id, type: type as FlowNodeRec["type"], x, y,
  params: type === "target"
    ? { ...NODE_DEFS.target.params, name: "M31", rows: 2, cols: 3, angle: "Rotate to PA", rotation: 55 }
    : { ...(NODE_DEFS as any)[type].params },
}));
const E = (id: string, from: string, fromPort: string, to: string, toPort: string): FlowEdgeRec =>
  ({ id, from, fromPort, to, toPort });
const EDGES: FlowEdgeRec[] = [
  E("e1", "n1", "window", "n2", "arm"), E("e2", "n2", "target", "n5", "run"),
  E("e3", "n5", "focused", "n6", "run"), E("e4", "n6", "guiding", "n7", "run"),
  E("e5", "n7", "complete", "n12", "session"),
  E("loop", "n7", "pass", "n2", "next"),
  E("frame", "n7", "frame", "n8", "events"), E("e8", "n8", "fire", "n9", "do"),
  E("e9", "n3", "unsafe", "n11", "do"),
];
const nodeOf = (id: string) => NODES.find((n) => n.id === id)!;
const edgeOf = (id: string) => EDGES.find((e) => e.id === id)!;

const container = win.document.getElementById("root");
const root = createRoot(container);

/** The layer and the remove control for `edgeId`, as a canvas mounts them. */
function mount(edgeId: string, props: Record<string, unknown> = {}): void {
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: { ...FLOWS_INIT, graph: { nodes: NODES, edges: EDGES }, sel: { kind: "edge", id: edgeId } },
    } as any);
  });
  act(() => root.render(React.createElement(React.Fragment, null,
    React.createElement(FlowWireLayer, props),
    React.createElement(FlowWireDelete, { edge: edgeOf(edgeId), ...props }))));
}

/** The control's world position, from its transform. */
function controlAt(): { x: number; y: number } {
  const cut = container.querySelector("[data-flows-wire-selected]");
  ok(cut, "no remove control for the selected wire");
  const m = /translate3d\(([-\d.]+)px,([-\d.]+)px,0\)/.exec(cut.getAttribute("style") ?? "");
  ok(m, `the control carries no world position: ${cut.getAttribute("style")}`);
  return { x: Number(m![1]), y: Number(m![2]) };
}

/** Every drawn point of a path of M, L and Q commands, 32 per segment. */
function samplePath(d: string): { x: number; y: number }[] {
  const tokens = d.match(/[MLQ]|-?\d*\.?\d+(?:e[-+]?\d+)?/gi) ?? [];
  const out: { x: number; y: number }[] = [];
  let i = 0;
  let cur = { x: 0, y: 0 };
  let cmd = "";
  const pt = () => ({ x: Number(tokens[i++]), y: Number(tokens[i++]) });
  while (i < tokens.length) {
    if (/^[MLQ]$/i.test(tokens[i])) cmd = tokens[i++].toUpperCase();
    if (cmd === "M") { cur = pt(); out.push(cur); continue; }
    if (cmd === "L") {
      const p = pt();
      for (let k = 1; k <= 32; k++) out.push({ x: cur.x + (p.x - cur.x) * k / 32, y: cur.y + (p.y - cur.y) * k / 32 });
      cur = p;
      continue;
    }
    if (cmd === "Q") {
      const c = pt(); const p = pt();
      for (let k = 1; k <= 32; k++) {
        const t = k / 32; const u = 1 - t;
        out.push({ x: u * u * cur.x + 2 * u * t * c.x + t * t * p.x, y: u * u * cur.y + 2 * u * t * c.y + t * t * p.y });
      }
      cur = p;
      continue;
    }
    throw new Error(`the arc has a command this reader does not know: ${cmd} in ${d}`);
  }
  return out;
}

// ============================================================ the canvas

// Computed from the card formula by hand, so the test does not restate the
// code: the FILTER CYCLE at (990, 60) is 188 wide and 4 port rows (37 + 80 +
// 26 = 143) tall; its `pass` port, its fourth row after one input, is at
// (990 + 188, 60 + 37 + 3 x 20 + 10) = (1178, 167). The drop stands 24 px
// right of the cycle, x 1202, and the run 28 px under it, y 231, so the
// middle of the drop is (1202, (167 + 231) / 2) = (1202, 199). The anchors'
// midpoint, with `next` at (270, 60 + 37 + 20 + 10) = (270, 127), is
// ((1178 + 270) / 2, (167 + 127) / 2) = (724, 147).
//
// MUTANT "classic control at the anchor midpoint" (FlowWireDelete.tsx: the
// control placed at `wireMidpoint(a.p1, a.p2)` for every wire, as it was
// before #355), run in the private scratch copy scratchpad/S5-LOOP-mut.
// Observed, flowWireDeleteLoop.test 1/3, with the issue's numbers:
//   x the selected loop wire's remove control sits on the arc's handle, clear
//     of every card: the control's place
//     expected 1202,199
//     got      724,147
//   x [phone FLOW 375 px] the control sits on the arc the tab draws: the
//     control at (187.5, 432.5) is not on the drawn arc M164 661 L358 661
//     Q368 661, 368 671 L368 695 Q368 705, 358 705 L17 705 Q7 705, 7 695 L7
//     214 Q7 204, 17 204 L211 204
test("the selected loop wire's remove control sits on the arc's handle, clear of every card", () => {
  mount("loop");
  const at = controlAt();
  eq(`${at.x},${at.y}`, "1202,199", "the control's place");
  // The layer drew the arc through that point.
  const d = container.querySelector('[data-loop-arc] [data-loop-arc-path]')?.getAttribute("d") ?? "";
  ok(samplePath(d).some((p) => Math.abs(p.x - at.x) < 0.5 && Math.abs(p.y - at.y) < 0.5),
    `the control at (${at.x}, ${at.y}) is not on the drawn arc ${d}`);
  // 22 px across, centred: its box covers no card's formula box.
  for (const n of NODES) {
    const b = cardBox(n, NODE_DEFS, "desktop");
    const clear = at.x + 11 <= b.x || at.x - 11 >= b.x + b.w || at.y + 11 <= b.y || at.y - 11 >= b.y + b.h;
    ok(clear, `the remove control at (${at.x}, ${at.y}) covers ${n.type.toUpperCase()} ${n.id}`);
  }
  // And the formula agrees with the hand computation, so the two cannot drift.
  const arc = loopArc(nodeOf("n7"), "pass", nodeOf("n2"), "next",
    panelLane({ nodes: NODES, edges: EDGES }, "n2"), NODE_DEFS, "desktop")!;
  eq(`${arc.handle.x},${arc.handle.y}`, "1202,199", "precondition: loopArc's handle");
});

test("CONTROL: any other selected wire's remove control is at its anchors' midpoint", () => {
  mount("frame");
  const p1 = portPos(nodeOf("n7"), "frame", "out", NODE_DEFS, "desktop")!;
  const p2 = portPos(nodeOf("n8"), "events", "in", NODE_DEFS, "desktop")!;
  const at = controlAt();
  eq(`${at.x},${at.y}`, `${(p1.x + p2.x) / 2},${(p1.y + p2.y) / 2}`, "the frame wire's control");
});

// ====================================================== the phone FLOW tab

// The phone FLOW tab hands the control the layout's positions and `auto`, so
// it must cover the arc THAT tab draws: inside the container, half the
// gutter outside the columns (#360). A control placed from the canvas's arc
// over the same positions stands 17 px right of the drawn drop, off the wire.
// Since #428 it stands `LOOP_CONTROL_INSET_AUTO` (6 px) in from the drop,
// at the handle's height, so its box, not its centre, is what lies on the
// wire: the drop runs through it 7 px inside its outer side.
//
// MUTANT "control from the canvas arc on the phone tab" (FlowWireDelete.tsx:
// `wireLoopArc(edge, { nodes, edges }, tier, false, positions)`, the column
// layout not asked). First observed 2/3 with the control on the drop; re-run
// in S7-UCANVAS-mut with the #428 inset, flowWireDeleteLoop.test 2/6 (this
// case and the three below):
//   x [phone FLOW 375 px] the control covers the arc the tab draws, at its handle: the control's x
//     expected 362
//     got      379
//   x [phone FLOW 375 px] the 26 px remove control lies inside the container and still covers the wire: the control spans x 366..392, outside [0, 375]
// MUTANT "desktop stub on the phone tab" (targetSummary.ts loopArcOf, see
// loopArcDom.test.tsx) moves the drawn arc and the control together, so only
// the pinned x and the container see it here; re-run in S7-UCANVAS-mut,
// 2/6:
//   x [phone FLOW 375 px] the control covers the arc the tab draws, at its handle: the control's x
//     expected 362
//     got      379
//   x [phone FLOW 360 px] the 26 px remove control lies inside the container and still covers the wire: the control spans x 351..377, outside [0, 360]
test("[phone FLOW 375 px] the control covers the arc the tab draws, at its handle", () => {
  const layout = computeAutoLayout({ nodes: NODES, edges: EDGES }, 375, NODE_DEFS);
  mount("loop", { tier: "phone", auto: true, positions: layout.pos });
  const at = controlAt();
  const d = container.querySelector('[data-loop-arc] [data-loop-arc-path]')?.getAttribute("d") ?? "";
  ok(d, "the tab drew no loop arc");
  ok(samplePath(d).some((p) => Math.abs(p.x - at.x) <= 13 && Math.abs(p.y - at.y) <= 13),
    `the 26 px control at (${at.x}, ${at.y}) covers none of the drawn arc ${d}`);
  // 6 px in from the drop, which is the column's right edge (375 - 14) plus
  // half the gutter.
  eq(at.x, 375 - 14 + 7 - 6, "the control's x");
});

// ================================ the phone FLOW tab's own container (#428)
//
// The tab lays the graph out itself and clips at its container's edges
// (FlowPhoneGraph.tsx, `overflow-x-hidden`), so these cases mount the REAL
// tab, with the loop wire selected, at three phone widths, and read the
// control the tab drew: its box (its own width and height, centred on its
// place) must lie inside [0, wc], wc the container the layout fills, and a
// point of the arc the tab drew must lie inside that box, or the operator
// sees a control beside the wire rather than on it.
//
// MUTANT "centred on the leg" (FlowWireDelete.tsx: the loop's control at
// `loop.handle` on every surface, the #428 inset left out, as S5 left it).
// Observed, flowWireDeleteLoop.test 2/6 (the three widths, and the 375 px
// case above on its pinned x, "expected 362, got 368"):
//   x [phone FLOW 360 px] the 26 px remove control lies inside the container and still covers the wire: the control spans x 340..366, outside [0, 360]
//     path M164 661 L343 661 Q353 661, 353 671 L353 695 Q353 705, 343 705 L17 705 Q7 705, 7 695 L7 214 Q7 204, 17 204 L196 204
//   x [phone FLOW 375 px] the 26 px remove control lies inside the container and still covers the wire: the control spans x 355..381, outside [0, 375]
//     path M164 661 L358 661 Q368 661, 368 671 L368 695 Q368 705, 358 705 L17 705 Q7 705, 7 695 L7 214 Q7 204, 17 204 L211 204
//   x [phone FLOW 414 px] the 26 px remove control lies inside the container and still covers the wire: the control spans x 394..420, outside [0, 414]
//     path M164 661 L397 661 Q407 661, 407 671 L407 695 Q407 705, 397 705 L17 705 Q7 705, 7 695 L7 214 Q7 204, 17 204 L250 204

const { layoutColumns, AUTO_PAD } = await import("../autoLayout");
const FlowPhoneGraph = (await import("../FlowPhoneGraph")).default;

// The tab measures its container (clientWidth, through a ResizeObserver);
// jsdom lays nothing out, so both are supplied, for that container only.
let phoneW = 390;
Object.defineProperty(win.HTMLElement.prototype, "clientWidth", {
  configurable: true,
  get(this: any) { return this.getAttribute?.("data-flows-phone-tab") === "flow" ? phoneW : 0; },
});
g.ResizeObserver = class { observe() {} disconnect() {} };

// ===================================== ... and meets no card there (#502)
//
// The same mounted tab, and a third condition: the control's box meets no
// card the tab lays out, each card's formula box (`cardBox`) at the layout's
// position. The eighth Example puts the lane's tail, FILTER CYCLE, in the
// LEFT column, where nothing stands beside the drop. The second lane,
// DUSK -> TARGET -> AUTOFOCUS -> FILTER CYCLE -> REPORT (#502's own case),
// puts the tail in the RIGHT column, beside the drop: the drop runs from the
// cycle's pass port (y 522) to the run (y 566), inside the cycle's height
// (415..558), so no height on it clears the cycle. The rise, at x 7 beside
// the left column, runs from the run up to the TARGET's `next` (y 204); its
// middle, 385, is beside AUTOFOCUS (276..399), and the nearest clear height
// is 399 + 13 = 412, the box held inside the container at x 13. Worked by
// hand from the layout's positions, the same at every width, since the left
// column never moves.
//
// RENAMED (H4-UCANVAS, #502): these cases were "[phone FLOW <w> px] the 26 px
// remove control lies inside the container and still covers the wire", on
// the eighth Example alone; the records above that quote the old name are
// what those mutants turned red then.
//
// Each named mutation was run in the private copy scratchpad/H4-UCANVAS-mut
// (geometry.ts `loopControlSpot`), one per condition:
//
// MUTANT "the box centred on the leg" (`const cx = Math.min(x1 - half,
// Math.max(x0 + half, a.x));` made `const cx = a.x;`: the box not held inside
// the container). Observed, flowWireDeleteLoop.test 2/9, every one of the six
// on the container alone (and the 375 px case above, "expected 362, got
// 368"); each line is followed by its path, of which two:
//   x [phone FLOW 360 px, the eighth Example] the 26 px remove control lies inside the container, covers the wire and meets no card: the control spans x 340..366, outside [0, 360]
//   x [phone FLOW 375 px, the eighth Example] the 26 px remove control lies inside the container, covers the wire and meets no card: the control spans x 355..381, outside [0, 375]
//     path M164 661 L358 661 Q368 661, 368 671 L368 695 Q368 705, 358 705 L17 705 Q7 705, 7 695 L7 214 Q7 204, 17 204 L211 204
//   x [phone FLOW 414 px, the eighth Example] the 26 px remove control lies inside the container, covers the wire and meets no card: the control spans x 394..420, outside [0, 414]
//   x [phone FLOW 360 px, a lane whose tail sits in the right column] the 26 px remove control lies inside the container, covers the wire and meets no card: the control spans x -6..20, outside [0, 360]
//   x [phone FLOW 375 px, a lane whose tail sits in the right column] the 26 px remove control lies inside the container, covers the wire and meets no card: the control spans x -6..20, outside [0, 375]
//     path M361 522 L364.5 522 Q368 522, 368 525.5 L368 556 Q368 566, 358 566 L17 566 Q7 566, 7 556 L7 214 Q7 204, 17 204 L211 204
//   x [phone FLOW 414 px, a lane whose tail sits in the right column] the 26 px remove control lies inside the container, covers the wire and meets no card: the control spans x -6..20, outside [0, 414]
// MUTANT "held a whole box in" (the same line with `size` for `half`: the
// box moved in so far the leg no longer runs through it). Observed,
// flowWireDeleteLoop.test 2/9, every one of the six on the wire alone (and
// the 375 px case above, "the 26 px control at (349, 683) covers none of
// the drawn arc"):
//   x [phone FLOW 360 px, the eighth Example] the 26 px remove control lies inside the container, covers the wire and meets no card: the control [x 321..347, y 670..696] covers none of the arc
//   x [phone FLOW 375 px, the eighth Example] the 26 px remove control lies inside the container, covers the wire and meets no card: the control [x 336..362, y 670..696] covers none of the arc
//   x [phone FLOW 414 px, the eighth Example] the 26 px remove control lies inside the container, covers the wire and meets no card: the control [x 375..401, y 670..696] covers none of the arc
//   x [phone FLOW 360 px, a lane whose tail sits in the right column] the 26 px remove control lies inside the container, covers the wire and meets no card: the control [x 13..39, y 399..425] covers none of the arc
//   x [phone FLOW 375 px, a lane whose tail sits in the right column] the 26 px remove control lies inside the container, covers the wire and meets no card: the control [x 13..39, y 399..425] covers none of the arc
//   x [phone FLOW 414 px, a lane whose tail sits in the right column] the 26 px remove control lies inside the container, covers the wire and meets no card: the control [x 13..39, y 399..425] covers none of the arc
// MUTANT "no card rules a height out" (`.filter((c) => c.x < cx + half &&
// c.x + c.w > cx - half)` made `.filter(() => false)`: the box at the drop's
// middle, #428's place, whatever stands there, which is #502 as filed).
// Observed, flowWireDeleteLoop.test 6/9, the right-column tail on the cards
// alone, the eighth Example green:
//   x [phone FLOW 360 px, a lane whose tail sits in the right column] the 26 px remove control lies inside the container, covers the wire and meets no card: the control [x 334..360, y 531..557] meets CYCLE cy [x 196..346, y 415..558]
//     path M346 522 L349.5 522 Q353 522, 353 525.5 L353 556 Q353 566, 343 566 L17 566 Q7 566, 7 556 L7 214 Q7 204, 17 204 L196 204
//   x [phone FLOW 375 px, a lane whose tail sits in the right column] the 26 px remove control lies inside the container, covers the wire and meets no card: the control [x 349..375, y 531..557] meets CYCLE cy [x 211..361, y 415..558]
//     path M361 522 L364.5 522 Q368 522, 368 525.5 L368 556 Q368 566, 358 566 L17 566 Q7 566, 7 556 L7 214 Q7 204, 17 204 L211 204
//   x [phone FLOW 414 px, a lane whose tail sits in the right column] the 26 px remove control lies inside the container, covers the wire and meets no card: the control [x 388..414, y 531..557] meets CYCLE cy [x 250..400, y 415..558]
//     path M400 522 L403.5 522 Q407 522, 407 525.5 L407 556 Q407 566, 397 566 L17 566 Q7 566, 7 556 L7 214 Q7 204, 17 204 L250 204
// The two place pins at the end of the case see the choice between legs and
// heights (loopArc.test.ts's mutants "the rise before the drop", observed
// here 5/9 with the eighth Example's control at "13,454.5", and "the first
// clear height, not the nearest", 6/9 with the right-column tail's at
// "13,263").

/** #502's lane: the tail, FILTER CYCLE, the fourth card of the flow order,
 *  so the column layout puts it in the right column. */
const RIGHT_TAIL: { nodes: FlowNodeRec[]; edges: FlowEdgeRec[] } = {
  nodes: ([["d", "dusk"], ["t", "target"], ["af", "autofocus"], ["cy", "cycle"], ["r", "report"]] as const)
    .map(([id, type], i) => ({
      id, type, x: 30 + i * 240, y: 60,
      params: type === "target"
        ? { ...NODE_DEFS.target.params, name: "M31", rows: 2, cols: 3, angle: "Rotate to PA", rotation: 55 }
        : { ...(NODE_DEFS as any)[type].params },
    })),
  edges: [
    E("e1", "d", "window", "t", "arm"), E("e2", "t", "target", "af", "run"),
    E("e3", "af", "focused", "cy", "run"), E("e4", "cy", "complete", "r", "session"),
    E("loop", "cy", "pass", "t", "next"),
  ],
};

const LANES: [string, { nodes: FlowNodeRec[]; edges: FlowEdgeRec[] }, string, string][] = [
  ["the eighth Example", { nodes: NODES, edges: EDGES }, "n2", "n7"],
  ["a lane whose tail sits in the right column", RIGHT_TAIL, "t", "cy"],
];

for (const [lane, graph, target, tail] of LANES) {
  for (const w of [360, 375, 414]) {
    test(`[phone FLOW ${w} px, ${lane}] the 26 px remove control lies inside the container, covers the wire and meets no card`, () => {
      phoneW = w;
      act(() => root.render(null));
      act(() => {
        useStore.setState({
          flows: { ...FLOWS_INIT, graph, sel: { kind: "edge", id: "loop" } },
        } as any);
      });
      act(() => root.render(React.createElement(FlowPhoneGraph)));
      const { colL, colR } = layoutColumns(w);
      const wc = colR + 150 + AUTO_PAD;
      eq(wc, w, "precondition: the layout fills the container");
      // The tab laid itself out at `w`: the TARGET stands where the layout at
      // `w` puts it, so the control below was placed for this width.
      const layout = computeAutoLayout(graph, w, NODE_DEFS);
      const tStyle = container.querySelector(`[data-node-id="${target}"]`)?.getAttribute("style") ?? "";
      ok(tStyle.includes(`translate3d(${layout.pos[target].x}px,${layout.pos[target].y}px,0)`),
        `precondition: the tab did not lay out at ${w} px: ${tStyle}`);
      eq(layout.pos[tail].x, graph === RIGHT_TAIL ? colR : colL,
        `precondition: the tail's column (${graph === RIGHT_TAIL ? "right" : "left"})`);
      const cut = container.querySelector("[data-flows-wire-selected]");
      ok(cut, "the tab drew no remove control for the selected loop wire");
      const at = controlAt();
      const size = parseFloat(cut.style.width);
      eq(`${size} x ${parseFloat(cut.style.height)}`, "26 x 26", "precondition: the phone FLOW control's size");
      const box = { x0: at.x - size / 2, x1: at.x + size / 2, y0: at.y - size / 2, y1: at.y + size / 2 };
      const d = container.querySelector("[data-loop-arc-path]")?.getAttribute("d") ?? "";
      ok(d, "the tab drew no loop arc");
      const bad: string[] = [];
      if (box.x0 < 0 || box.x1 > wc) bad.push(`the control spans x ${box.x0}..${box.x1}, outside [0, ${wc}]`);
      if (!samplePath(d).some((p) => p.x > box.x0 && p.x < box.x1 && p.y > box.y0 && p.y < box.y1)) {
        bad.push(`the control [x ${box.x0}..${box.x1}, y ${box.y0}..${box.y1}] covers none of the arc`);
      }
      for (const n of graph.nodes) {
        const b = cardBox({ ...n, ...layout.pos[n.id] }, NODE_DEFS, "phone");
        if (box.x0 < b.x + b.w && box.x1 > b.x && box.y0 < b.y + b.h && box.y1 > b.y) {
          bad.push(`the control [x ${box.x0}..${box.x1}, y ${box.y0}..${box.y1}] meets `
            + `${n.type.toUpperCase()} ${n.id} [x ${b.x}..${b.x + b.w}, y ${b.y}..${b.y + b.h}]`);
        }
      }
      ok(bad.length === 0, `${bad.join("; ")}\n    path ${d}`);
      // Where it stands, worked by hand: on the eighth Example #428's place, 6
      // px in from the middle of the drop (the drop's middle is clear there);
      // on the right-column tail the rise's nearest clear height (above).
      eq(`${at.x},${at.y}`, graph === RIGHT_TAIL ? "13,412" : `${w - 14 + 7 - 6},683`, "the control's place");
    });
  }
}

// ----------------------------------------------------------------- report
act(() => root.render(null));
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nflowWireDeleteLoop.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
