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
//   4. CONTROL: any other selected wire's control is still at its anchors'
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

for (const w of [360, 375, 414]) {
  test(`[phone FLOW ${w} px] the 26 px remove control lies inside the container and still covers the wire`, () => {
    phoneW = w;
    act(() => root.render(null));
    act(() => {
      useStore.setState({
        flows: { ...FLOWS_INIT, graph: { nodes: NODES, edges: EDGES }, sel: { kind: "edge", id: "loop" } },
      } as any);
    });
    act(() => root.render(React.createElement(FlowPhoneGraph)));
    const wc = layoutColumns(w).colR + 150 + AUTO_PAD;
    eq(wc, w, "precondition: the layout fills the container");
    // The tab laid itself out at `w`: the TARGET stands where the layout at
    // `w` puts it, so the control below was placed for this width.
    const layout = computeAutoLayout({ nodes: NODES, edges: EDGES }, w, NODE_DEFS);
    const tStyle = container.querySelector('[data-node-id="n2"]')?.getAttribute("style") ?? "";
    ok(tStyle.includes(`translate3d(${layout.pos.n2.x}px,${layout.pos.n2.y}px,0)`),
      `precondition: the tab did not lay out at ${w} px: ${tStyle}`);
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
    ok(bad.length === 0, `${bad.join("; ")}\n    path ${d}`);
  });
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
