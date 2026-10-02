// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
//      compile has counted the block, and loses the count once an edit makes
//      the graph on screen one that compile was not asked about
//      (`compiledIsCurrent`, #356, S7).
//   4. The classic phone FLOW tab draws the arc from its own column layout,
//      inside its container and clear of every card it lays out (#360),
//      on the real `computeAutoLayout` at 360, 375 and 414 px.
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
/** The graph every canvas case mounts, ONE object, so a compile answer can
 *  say it was compiled from it (`FlowCompiled.from`), as the slice stamps
 *  every answer with the graph its request sent. */
const GRAPH = { nodes: NODES, edges: EDGES };

/** compile_plan's entry for the block: three columns by two rows, 2-3 skipped. */
const PLAN = { targets: [{ node_id: "n2", mosaic: { rows: 2, cols: 3, skip: [[2, 3]] } }] };
/** That answer as the slice keeps it, compiled from the graph on screen. */
const COMPILED = { plan: PLAN, structural: [], issues: [], unmapped: [], from: GRAPH };

const container = win.document.getElementById("root");
const root = createRoot(container);

function mount(flows: Record<string, unknown> = {}, props: Record<string, unknown> = {}): void {
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: { ...FLOWS_INIT, graph: GRAPH, ...flows },
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
//   (and the phone FLOW case as it then stood, since replaced by the #360
//   cases below: got M170 627 C188 697, 182 157, 200 227)
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
// got every pass: next panel · 5 panels"), goes red in this file too. Since
// S7 the chip withholds its count by the answer's graph, not by `dirty`
// (#356); the same mutant on the new flag, "count while stale" (loopChip
// ignores `stale`), re-run in S7-UCANVAS-mut, 18/19:
//   x the chip: no count before a compile, the compile's count after, none over an edit: an edit the compile was not asked about
//     expected every pass: next panel
//     got      every pass: next panel · 5 panels
test("night mode: the arc differs from an event wire by its dash and its chip, not its stroke", () => {
  win.document.documentElement.classList.add("night");
  try {
    mount({ compiled: COMPILED, dirty: false });
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

// AN EDIT IS A NEW GRAPH OBJECT, as the slice's `touch` writes it, and the
// answer then describes the graph before it. Whether it is saved is not the
// question (#356, S7; loopChipCurrent.test.ts drives the real slice through
// a save and the modal's DONE).
test("the chip: no count before a compile, the compile's count after, none over an edit", () => {
  mount();
  eq(chipText(), "every pass: next panel", "before any compile");
  setFlows({ compiled: COMPILED });
  eq(chipText(), "every pass: next panel · 5 panels", "after the compile: 3x2 less 2-3");
  setFlows({ graph: { nodes: NODES, edges: EDGES }, dirty: true });
  eq(chipText(), "every pass: next panel", "an edit the compile was not asked about");
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

// ====================================== the classic phone FLOW tab (#360)
//
// The tab lays the graph out in two columns (`computeAutoLayout`) inside a
// container that clips what crosses its edges (`overflow-x-hidden`), and until
// #360 it drew the canvas's arc there: both legs 24 px outside the outermost
// cards, which stand 14 px inside the container, so both were cut off, and a
// run 28 px under the tail, inside the REPORT the layout puts 16 px below it.
// The case that stood here computed its expected path with the same `loopArc`
// the layer calls, and its own expected string held a leg left of x 0: it
// graded the wiring, not what the operator sees. These cases mount the real
// tab, lay it out with the real `computeAutoLayout` at three phone widths, and
// ask the drawn path itself: every point inside the container, none inside a
// card's formula box, the legs no further out than half the gutter, and the
// run under every card from the TARGET down to the tail and above the one the
// layout puts after it.
//
// MUTANT "desktop stub on the phone tab" (targetSummary.ts loopArcOf: the
// column layout's `stub` left out, so the legs stand LOOP_ARC_STUB out), run
// in the private scratch copy scratchpad/S5-LOOP-mut. Observed,
// loopArcDom.test 15/19 (the three widths and the stacked-card case):
//   x [phone 360 px] the arc stays inside the container, outside every card,
//     its legs half the gutter out: (360.6, 661.0) is outside [0, 360]; the
//     drop stands 24 px right of the cards; the rise stands 24 px left of the
//     cards
//     path M164 661 L360 661 Q370 661, 370 671 L370 695 Q370 705, 360 705 L0
//     705 Q-10 705, -10 695 L-10 214 Q-10 204, 0 204 L196 204
//   x [phone 375 px] ... (375.6, 661.0) is outside [0, 375]; ...
//   x [phone 414 px] ... (414.6, 661.0) is outside [0, 414]; ...
//
// MUTANT "desktop drop on the phone tab" (loopArcOf: the column layout's
// `drop` left out, so the run lies LOOP_ARC_DROP under the tail and is pushed
// under every card below it). Observed, loopArcDom.test 16/19:
//   x [phone 360 px] the run passes under every card from the TARGET down to
//     the tail, above the next: the run at 1137 is not above the REPORT (top
//     713)
//   (and the same at 375 and 414 px). A run pushed under the whole graph
//   still crosses no card, so only this case sees the long way round.
//
// MUTANT "no avoid on the phone tab" (loopArcOf: the column layout's
// `avoid` left out). The eighth Example's run lies in the gap under the tail,
// so it stays green there; the stacked-card case below goes red. Observed,
// loopArcDom.test 18/19 (geometry.ts "avoid ignored" fails it the same way):
//   x [phone 375 px] a source the layout stacks over another card: the run
//     goes under that card too: (155.5, 467.0) is inside CAPTURE b [x 14..164,
//     y 463..606]
//     path M164 423 L358 423 Q368 423, 368 433 L368 457 Q368 467, 358 467 L17
//     467 Q7 467, 7 457 L7 88.5 Q7 85, 10.5 85 L14 85

const { computeAutoLayout, layoutColumns, AUTO_PAD } = await import("../autoLayout");
const { cardBox } = await import("../geometry");
const { loopChipBox } = await import("../targetSummary");
const FlowPhoneGraph = (await import("../FlowPhoneGraph")).default;

// The tab measures its container (clientWidth, through a ResizeObserver);
// jsdom lays nothing out, so both are supplied, for that container only.
let phoneW = 390;
Object.defineProperty(win.HTMLElement.prototype, "clientWidth", {
  configurable: true,
  get(this: any) { return this.getAttribute?.("data-flows-phone-tab") === "flow" ? phoneW : 0; },
});
g.ResizeObserver = class { observe() {} disconnect() {} };

/** Every drawn point of a path of M, L and Q commands (all the arc emits),
 *  sampled 32 times per segment. */
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

type Box = { id: string; type: string; x: number; y: number; w: number; h: number };

/** The tab mounted at `w` px over `graph`: the loop wire's drawn path, the
 *  chip's place and words, and every card's formula box where the layout put
 *  it. */
function mountPhone(w: number, graph: { nodes: FlowNodeRec[]; edges: FlowEdgeRec[] }, loopId = "loop") {
  phoneW = w;
  act(() => root.render(null));
  act(() => { useStore.setState({ flows: { ...FLOWS_INIT, graph } } as any); });
  act(() => root.render(React.createElement(FlowPhoneGraph)));
  const hit = container.querySelector(`path[data-wire][data-edge-id="${loopId}"]`);
  ok(hit, `the tab drew no wire ${loopId}`);
  const d: string = hit.parentElement.querySelector("[data-loop-arc-path]")?.getAttribute("d") ?? "";
  ok(d, `the tab did not draw ${loopId} as the loop arc`);
  const layout = computeAutoLayout(graph, w, NODE_DEFS);
  // THE TAB LAID ITSELF OUT AT `w`, so the boxes graded below are the cards
  // it drew: each card stands where the layout at `w` puts it. The width
  // reaches the tab only through the stubbed clientWidth, and without this a
  // tab that kept its seeded 390 px layout was graded against 414 px boxes
  // and passed. MUTANT "phone tab ignores its width" (FlowPhoneGraph.tsx:
  // `computeAutoLayout` handed 390 whatever the container measured), run by
  // the S5-LOOP verifier in a private scratch copy of ui/: before this check,
  // loopArcDom.test 16/19 with every 414 px case green; with it, 9/19:
  //   x [phone 414 px] the arc stays inside the container, outside every
  //     card, its legs half the gutter out: the tab did not lay out at 414
  //     px: n2 at (226, 137), the layout at 414 px puts it at (250, 137); n6
  //     at (226, 415), the layout at 414 px puts it at (250, 415)
  //   (and the same for the run and the chip cases at 360, 375 and 414 px,
  //   and the stacked-card case: "n7 at (226, 157), the layout at 375 px
  //   puts it at (211, 157)")
  const stray = graph.nodes.flatMap((n) => {
    const m = /translate3d\(([-\d.]+)px,([-\d.]+)px,0\)/.exec(
      container.querySelector(`[data-node-id="${n.id}"]`)?.getAttribute("style") ?? "");
    const p = layout.pos[n.id];
    return m && Number(m[1]) === p.x && Number(m[2]) === p.y ? []
      : [`${n.id} at ${m ? `(${m[1]}, ${m[2]})` : "nowhere"}, the layout at ${w} px puts it at (${p.x}, ${p.y})`];
  });
  ok(stray.length === 0, `the tab did not lay out at ${w} px: ${stray.slice(0, 2).join("; ")}`);
  const boxes: Box[] = graph.nodes.map((n) => ({
    id: n.id, type: n.type, ...cardBox({ ...n, ...layout.pos[n.id] }, NODE_DEFS, "phone"),
  }));
  const chip = container.querySelector("[data-loop-chip]");
  const at = /translate\(([-\d.]+),([-\d.]+)\)/.exec(chip?.getAttribute("transform") ?? "");
  return {
    d, boxes, layout,
    wc: Math.max(300, Math.min(w, 700)),
    chip: chip && at ? { x: Number(at[1]), y: Number(at[2]), text: chip.textContent ?? "" } : null,
  };
}
const inside = (p: { x: number; y: number }, b: Box) =>
  p.x > b.x && p.x < b.x + b.w && p.y > b.y && p.y < b.y + b.h;

for (const w of [360, 375, 414]) {
  test(`[phone ${w} px] the arc stays inside the container, outside every card, its legs half the gutter out`, () => {
    const { d, boxes, wc } = mountPhone(w, { nodes: NODES, edges: EDGES });
    eq(wc, layoutColumns(w).colR + 150 + AUTO_PAD, "precondition: the container is the layout's width");
    const pts = samplePath(d);
    const bad: string[] = [];
    const out = pts.find((p) => p.x < 0 || p.x > wc);
    if (out) bad.push(`(${out.x.toFixed(1)}, ${out.y.toFixed(1)}) is outside [0, ${wc}]`);
    for (const p of pts) {
      const b = boxes.find((q) => inside(p, q));
      if (b) { bad.push(`(${p.x.toFixed(1)}, ${p.y.toFixed(1)}) is inside ${b.type.toUpperCase()} ${b.id} [x ${b.x}..${b.x + b.w}, y ${b.y}..${b.y + b.h}]`); break; }
    }
    const right = Math.max(...boxes.map((b) => b.x + b.w));
    const left = Math.min(...boxes.map((b) => b.x));
    const xs = pts.map((p) => p.x);
    if (Math.max(...xs) - right > AUTO_PAD / 2) bad.push(`the drop stands ${Math.max(...xs) - right} px right of the cards`);
    if (left - Math.min(...xs) > AUTO_PAD / 2) bad.push(`the rise stands ${left - Math.min(...xs)} px left of the cards`);
    ok(bad.length === 0, `${bad.join("; ")}\n    path ${d}`);
  });

  test(`[phone ${w} px] the run passes under every card from the TARGET down to the tail, above the next`, () => {
    const { d, boxes, layout } = mountPhone(w, { nodes: NODES, edges: EDGES });
    const runY = Math.max(...samplePath(d).map((p) => p.y));
    const top = layout.pos.n2.y;
    const tail = layout.pos.n7.y;
    const between = boxes.filter((b) => b.y >= top && b.y <= tail);
    eq(between.map((b) => b.id).join(","), "n2,n5,n6,n7", "precondition: the TARGET, its lane and the tail");
    const over = between.filter((b) => b.y + b.h >= runY).map((b) => `${b.id} (bottom ${b.y + b.h})`);
    ok(over.length === 0, `the run at ${runY} does not pass under ${over.join(", ")}`);
    // The REPORT, the card the layout puts after the tail, stays below it: the
    // run takes the gap under the tail rather than the long way round.
    const report = boxes.find((b) => b.id === "n12")!;
    ok(runY < report.y, `the run at ${runY} is not above the REPORT (top ${report.y})`);
  });

  test(`[phone ${w} px] the chip sits inside the container and on no card`, () => {
    const { chip, boxes, wc } = mountPhone(w, { nodes: NODES, edges: EDGES });
    ok(chip, "no chip on the eighth Example's loop wire");
    const box = loopChipBox(chip!.text);
    const x0 = chip!.x - box.w / 2; const x1 = chip!.x + box.w / 2;
    const y0 = chip!.y - box.h / 2; const y1 = chip!.y + box.h / 2;
    ok(x0 >= 0 && x1 <= wc, `the chip spans x ${x0}..${x1}, outside [0, ${wc}]`);
    const under = boxes.filter((b) => x0 < b.x + b.w && x1 > b.x && y0 < b.y + b.h && y1 > b.y)
      .map((b) => `${b.type.toUpperCase()} ${b.id}`);
    ok(under.length === 0, `the chip [x ${x0}..${x1}, y ${y0}..${y1}] lies over ${under.join(", ")}`);
  });
}

// A stage a flow cycle keeps out of the flow order is placed by the layout's
// last pass, stacked 4 px under the one before it in the left column. Such a
// stage's pass wire into the TARGET's `next` puts the run's first place, the
// gap under the lowest body card, inside the stage stacked under it. Only a
// graph saved before S0 refused flow cycles has one, and the editor still
// opens it.
test("[phone 375 px] a source the layout stacks over another card: the run goes under that card too", () => {
  const t: FlowNodeRec = { ...nodeOf("n2"), x: 0, y: 0 };
  const cy: FlowNodeRec = { ...nodeOf("n7"), x: 240, y: 0 };
  const a: FlowNodeRec = { id: "a", type: "capture", x: 0, y: 300, params: { ...NODE_DEFS.capture.params } };
  const b: FlowNodeRec = { id: "b", type: "capture", x: 240, y: 300, params: { ...NODE_DEFS.capture.params } };
  const graph = {
    nodes: [t, cy, a, b],
    edges: [E("e1", "n2", "target", "n7", "run"), E("ab", "a", "complete", "b", "run"),
      E("ba", "b", "complete", "a", "run"), E("loop", "a", "pass", "n2", "next")],
  };
  const { d, boxes, wc } = mountPhone(375, graph);
  const ba = boxes.find((q) => q.id === "a")!;
  const bb = boxes.find((q) => q.id === "b")!;
  ok(bb.x === ba.x && bb.y > ba.y + ba.h - 1 && bb.y < ba.y + ba.h + 8,
    `precondition: b is stacked just under a (a [y ${ba.y}..${ba.y + ba.h}], b [y ${bb.y}..${bb.y + bb.h}])`);
  const pts = samplePath(d);
  const bad: string[] = [];
  for (const p of pts) {
    if (p.x < 0 || p.x > wc) { bad.push(`(${p.x}, ${p.y}) is outside [0, ${wc}]`); break; }
    const hitBox = boxes.find((q) => inside(p, q));
    if (hitBox) { bad.push(`(${p.x.toFixed(1)}, ${p.y.toFixed(1)}) is inside ${hitBox.type.toUpperCase()} ${hitBox.id} [x ${hitBox.x}..${hitBox.x + hitBox.w}, y ${hitBox.y}..${hitBox.y + hitBox.h}]`); break; }
  }
  ok(bad.length === 0, `${bad.join("; ")}\n    path ${d}`);
});

// CONTROL: the canvas keeps the spec's arc (1.4): the layer drawn with no
// `auto` puts the Example's run 28 px under the cycle and its drop 24 px
// right of it, as `loopArc` with no options does.
test("CONTROL: on the canvas the arc is still the spec's, LOOP_ARC_DROP down and LOOP_ARC_STUB out", () => {
  mount();
  eq(visible("loop").getAttribute("d"), EXPECTED_ARC.d, "the canvas arc");
  eq(EXPECTED_ARC.runY, 60 + 143 + 28, "precondition: the run under the FILTER CYCLE");
  eq(EXPECTED_ARC.handle.x, 990 + 188 + 24, "precondition: the drop right of the FILTER CYCLE");
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
