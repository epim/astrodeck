// loopArcDom.test.tsx - the classic wire layer draws the panel loop as the
// back-arc, with its own dash and its label chip, MOUNTED (#189 S4 item 6;
// spec 2026-09-23 flows mosaic, 1.4 "How it is drawn").
//
//   Run directly:  node --import tsx src/components/flows/__tests__/loopArcDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE, on the surface an operator sees:
//   1. The eighth Example's loop wire is drawn as `loopArc`, not `edgePath`,
//      and every other wire is still drawn as `edgePath` (control).
//   2. In the night palette every lane is one red, so the arc must be told
//      apart by its dash and its chip: the same stroke as a resting event wire,
//      a dash no other wire has, and the dash kept while the run is live.
//   3. The chip reads "every pass: next panel", then " · N panels" once a
//      compile has counted the block, and loses the count over an unsaved edit.
//   4. The phone FLOW tab routes the arc from its own auto-layout positions.
//
// geometry and targetSummary hold the formula and the words (loopArc.test.ts,
// targetSummary.test.ts); this file holds that the layer draws them.
// #/next's layer has its own file (canvas/__tests__/loopArcNext.test.tsx).

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
const { edgePath, loopArc, portPos } = await import("../geometry");
const { panelLane } = await import("../panelLane");
const { LOOP_ARC_DASH } = await import("../targetSummary");
const FlowWireLayer = (await import("../FlowWireLayer")).default;
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

/** compile_plan's entry for the block: three columns by two rows, 2-3 skipped. */
const PLAN = { targets: [{ node_id: "n2", mosaic: { rows: 2, cols: 3, skip: [[2, 3]] } }] };

const container = win.document.getElementById("root");
const root = createRoot(container);

function mount(flows: Record<string, unknown> = {}, props: Record<string, unknown> = {}): void {
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: { ...FLOWS_INIT, graph: { nodes: NODES, edges: EDGES }, ...flows },
    } as any);
  });
  act(() => root.render(React.createElement(FlowWireLayer, props)));
}
function setFlows(p: Record<string, unknown>): void {
  act(() => { useStore.setState({ flows: { ...useStore.getState().flows, ...p } } as any); });
}

/** The VISIBLE path of an edge: the second path in its group (the first is
 *  the transparent hit band). */
function visible(edgeId: string): any {
  const hit = container.querySelector(`path[data-wire][data-edge-id="${edgeId}"]`);
  ok(hit, `no wire drawn for ${edgeId}`);
  const vis = hit.parentElement.querySelectorAll("path")[1];
  ok(vis, `no visible path for ${edgeId}`);
  return vis;
}
const chipText = (): string | null =>
  container.querySelector("[data-loop-chip] text")?.textContent ?? null;

const EXPECTED_ARC = loopArc(nodeOf("n7"), "pass", nodeOf("n2"), "next",
  panelLane({ nodes: NODES, edges: EDGES }, "n2"), NODE_DEFS, "desktop")!;

// ========================================================== the arc is drawn

// MUTANT "classic surface keeps edgePath" (FlowWireLayer's loop branch draws
// `edgePath(a.p1, a.p2, mode)`). Observed, loopArcDom.test 7/9:
//   x the eighth Example's loop wire is drawn as the back-arc: the loop wire's
//     path
//     expected M1178 167 L1192 167 Q1202 167, 1202 177 L1202 221 Q1202 231,
//              1192 231 L256 231 Q246 231, 246 221 L246 137 Q246 127, 256 127
//              L270 127
//     got      M1178 167 C1632 167, -184 127, 270 127
//   (and the phone FLOW case: got M170 627 C188 697, 182 157, 200 227)
test("the eighth Example's loop wire is drawn as the back-arc", () => {
  mount();
  eq(visible("loop").getAttribute("d"), EXPECTED_ARC.d, "the loop wire's path");
  ok(visible("loop").closest("[data-loop-arc]"), "the loop wire is not marked as the loop arc");
  eq(container.querySelector('path[data-wire][data-edge-id="loop"]').getAttribute("d"),
    EXPECTED_ARC.d, "the hit band follows the arc, or a click on the drawn wire selects nothing");
});

test("CONTROL: every other wire is still the stock bezier", () => {
  mount();
  for (const e of EDGES.filter((x) => x.id !== "loop")) {
    const from = nodeOf(e.from);
    const to = nodeOf(e.to);
    const p1 = portPos(from, e.fromPort, "out", NODE_DEFS, "desktop")!;
    const p2 = portPos(to, e.toPort, "in", NODE_DEFS, "desktop")!;
    eq(visible(e.id).getAttribute("d"), edgePath(p1, p2, "canvas"), `wire ${e.id}`);
  }
  eq(container.querySelectorAll("[data-loop-arc]").length, 1, "one loop arc in the Example");
});

// =============================================== night: dash and label, not hue

// Under `:root.night` every lane token is one red (index.css), and jsdom
// computes no colours, so what can be held here is the structure the palette
// cannot touch. MUTANT "arc keeps the event dash" (FlowLoopArc draws
// `wireDash(kind, active)`). Observed, loopArcDom.test 7/9:
//   x night mode: the arc differs from an event wire by its dash and its chip,
//     not its stroke: the arc's own dash
//     expected 10 4 2 4
//     got      4 5
//   x while the run is live the arc keeps its dash; a plain wire goes to 7 6:
//     the live arc's dash
//     expected 10 4 2 4
//     got      7 6
// The chip's count, under "count ignores skip" (7/9: "expected every pass:
// next panel · 5 panels, got ... · 6 panels" here and in the chip case) and
// "count while dirty" (8/9: "an unsaved edit: expected every pass: next panel,
// got every pass: next panel · 5 panels"), goes red in this file too.
test("night mode: the arc differs from an event wire by its dash and its chip, not its stroke", () => {
  win.document.documentElement.classList.add("night");
  try {
    mount({ compiled: { plan: PLAN, structural: [], issues: [], unmapped: [] }, dirty: false });
    const arc = visible("loop");
    const event = visible("frame");
    eq(arc.getAttribute("stroke"), event.getAttribute("stroke"),
      "the arc's stroke is the event lane's: no hue of its own to lose at night");
    eq(event.getAttribute("stroke-dasharray"), "4 5", "precondition: a resting event wire");
    eq(arc.getAttribute("stroke-dasharray"), LOOP_ARC_DASH, "the arc's own dash");
    ok(arc.getAttribute("stroke-dasharray") !== event.getAttribute("stroke-dasharray"),
      "the arc and an event wire share a dash");
    eq(chipText(), "every pass: next panel · 5 panels", "the arc's label");
  } finally {
    win.document.documentElement.classList.remove("night");
  }
});

test("while the run is live the arc keeps its dash; a plain wire goes to 7 6", () => {
  mount({ run: { ...FLOWS_INIT.run, phase: "running" }, statuses: { n7: "busy" } });
  eq(visible("frame").getAttribute("stroke-dasharray"), "7 6", "precondition: a live event wire");
  eq(visible("loop").getAttribute("stroke-dasharray"), LOOP_ARC_DASH, "the live arc's dash");
  eq(visible("loop").getAttribute("stroke-width"), "2.5", "live shows as width");
  eq(visible("loop").getAttribute("class"), "flow-wire-march", "and as the march");
});

// ================================================================= the chip

test("the chip: no count before a compile, the compile's count after, none over an edit", () => {
  mount();
  eq(chipText(), "every pass: next panel", "before any compile");
  setFlows({ compiled: { plan: PLAN, structural: [], issues: [], unmapped: [] } });
  eq(chipText(), "every pass: next panel · 5 panels", "after the compile: 3x2 less 2-3");
  setFlows({ dirty: true });
  eq(chipText(), "every pass: next panel", "an unsaved edit");
});

test("the chip sits on the run, centred", () => {
  mount();
  const chip = container.querySelector("[data-loop-chip]");
  eq(chip.getAttribute("transform"), `translate(${EXPECTED_ARC.label.x},${EXPECTED_ARC.label.y})`,
    "the chip's place");
});

// MUTANT "chip on every arc" (loopChip asks only `loopArcTarget`). Observed,
// loopArcDom.test 8/9:
//   x a pass wire from mid-lane is drawn as the arc but carries no chip: a
//     chip promising 'every pass' over a wire the run does not loop on
//     expected null
//     got      [object SVGGElement]
test("a pass wire from mid-lane is drawn as the arc but carries no chip", () => {
  const cap: FlowNodeRec = { id: "cap", type: "capture", x: 1230, y: 200, params: { ...NODE_DEFS.capture.params } };
  const edges = [...EDGES.filter((e) => e.id !== "e5"), E("e10", "n7", "complete", "cap", "run")];
  mount({ graph: { nodes: [...NODES, cap], edges } });
  ok(visible("loop").closest("[data-loop-arc]"), "the mid-lane wire is still the arc");
  eq(container.querySelector("[data-loop-chip]"), null,
    "a chip promising 'every pass' over a wire the run does not loop on");
});

// ======================================================== selection and phone

test("a click on the arc selects its wire", () => {
  mount();
  act(() => {
    container.querySelector('path[data-wire][data-edge-id="loop"]')
      .dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  const sel = useStore.getState().flows.sel;
  eq(sel?.kind === "edge" ? sel.id : null, "loop", "the selected wire");
});

test("the phone FLOW tab routes the arc from its auto-layout positions", () => {
  const positions = Object.fromEntries(NODES.map((n, i) => [n.id, { x: 20 + (i % 2) * 180, y: 40 + i * 120 }]));
  mount({}, { tier: "phone", auto: true, positions });
  const placed = (id: string) => ({ ...nodeOf(id), ...positions[id] });
  const want = loopArc(placed("n7"), "pass", placed("n2"), "next",
    ["n5", "n6", "n7"].map(placed), NODE_DEFS, "phone")!;
  eq(visible("loop").getAttribute("d"), want.d, "the arc on the auto-graph");
});

// ----------------------------------------------------------------- report
act(() => root.render(null));
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nloopArcDom.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
