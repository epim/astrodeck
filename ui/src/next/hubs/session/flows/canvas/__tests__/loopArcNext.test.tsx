// loopArcNext.test.tsx - SESSION / FLOWS, the #/next wire layer draws the panel
// loop as the back-arc, with its own dash and its label chip, and puts the
// selected loop wire's remove control on the arc, MOUNTED (#189 S4 item 6;
// spec 2026-09-23 flows mosaic, 1.4 "How it is drawn").
//
//   Run directly:  node --import tsx src/next/hubs/session/flows/canvas/__tests__/loopArcNext.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The classic layer's file (components/flows/__tests__/loopArcDom.test.tsx)
// holds the same promises there. Both layers draw from the pure
// `targetSummary.ts` and `geometry.ts`, so what can differ is only whether a
// layer CALLS them - which is what the named mutation of this file removes.
//
// Convention: shell-and-tests.md section 4 - jsdom by hand, createRoot + act,
// native events, printed tally plus the `{ passed, failed, total }` export.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------ imports
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { FLOWS_INIT } = await import("../../../../../../components/flows/flowsSlice");
const { NODE_DEFS } = await import("../../../../../../components/flows/nodeDefs");
const { edgePath, loopArc, portPos } =
  await import("../../../../../../components/flows/geometry");
const { panelLane } = await import("../../../../../../components/flows/panelLane");
const { LOOP_ARC_DASH } = await import("../../../../../../components/flows/targetSummary");
const { FlowWireLayer, FlowWireDelete } = await import("../FlowWires");
type FlowNodeRec = import("../../../../../../components/flows/flowsTypes").FlowNodeRec;
type FlowEdgeRec = import("../../../../../../components/flows/flowsTypes").FlowEdgeRec;

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

// ------------------------------------------------------------------- fixture
// The eighth Example (server flows/examples.py `_m31_mosaic`), the layout
// components/flows/__tests__/loopArc.test.ts holds to the server file.
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
const PLAN = { targets: [{ node_id: "n2", mosaic: { rows: 2, cols: 3, skip: [] } }] };
/** One graph object, so the answer below can say it was compiled from it
 *  (`FlowCompiled.from`, which `compiledIsCurrent` compares, #356 S7). */
const GRAPH = { nodes: NODES, edges: EDGES };
const COMPILED = { plan: PLAN, structural: [], issues: [], unmapped: [], from: GRAPH };

const EXPECTED_ARC = loopArc(nodeOf("n7"), "pass", nodeOf("n2"), "next",
  panelLane({ nodes: NODES, edges: EDGES }, "n2"), NODE_DEFS, "tablet")!;

const container = win.document.getElementById("root");
const root = createRoot(container);

function mount(flows: Record<string, unknown> = {}, selectedEdge: string | null = null): void {
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: { ...FLOWS_INIT, graph: GRAPH, ...flows },
    } as any);
  });
  act(() => root.render(createElement(Fragment, null,
    createElement(FlowWireLayer, { tier: "tablet" }),
    selectedEdge ? createElement(FlowWireDelete, { edge: edgeOf(selectedEdge), tier: "tablet" }) : null,
  )));
}
function setFlows(p: Record<string, unknown>): void {
  act(() => { useStore.setState({ flows: { ...useStore.getState().flows, ...p } } as any); });
}

function visible(edgeId: string): any {
  const hit = container.querySelector(`path[data-wire][data-edge-id="${edgeId}"]`);
  ok(hit, `no wire drawn for ${edgeId}`);
  const vis = hit.parentElement.querySelectorAll("path")[1];
  ok(vis, `no visible path for ${edgeId}`);
  return vis;
}
const chipText = (): string | null =>
  container.querySelector('[data-testid="flow-loop-chip"] text')?.textContent ?? null;

// ========================================================== the arc is drawn

// MUTANT "next surface keeps edgePath" (FlowWires.tsx's loop branch draws
// `edgePath(a.p1, a.p2, "canvas")`). Observed, loopArcNext.test 8/9:
//   x the eighth Example's loop wire is drawn as the back-arc: the loop wire's
//     path
//     expected M1178 167 L1192 167 Q1202 167, 1202 177 L1202 221 Q1202 231,
//              1192 231 L256 231 Q246 231, 246 221 L246 137 Q246 127, 256 127
//              L270 127
//     got      M1178 167 C1632 167, -184 127, 270 127
test("the eighth Example's loop wire is drawn as the back-arc", () => {
  mount();
  eq(visible("loop").getAttribute("d"), EXPECTED_ARC.d, "the loop wire's path");
  ok(visible("loop").closest('[data-testid="flow-loop-arc"]'), "the loop wire is not marked as the loop arc");
  eq(container.querySelector('path[data-wire][data-edge-id="loop"]').getAttribute("d"),
    EXPECTED_ARC.d, "the hit band follows the arc, or a click on the drawn wire selects nothing");
});

test("CONTROL: every other wire is still the stock bezier, and every wire is still drawn", () => {
  mount();
  eq(container.querySelectorAll('[data-testid="flow-wire"]').length, EDGES.length,
    "one hit path per wire, the loop's included (the parity harness counts these)");
  for (const e of EDGES.filter((x) => x.id !== "loop")) {
    const p1 = portPos(nodeOf(e.from), e.fromPort, "out", NODE_DEFS, "tablet")!;
    const p2 = portPos(nodeOf(e.to), e.toPort, "in", NODE_DEFS, "tablet")!;
    eq(visible(e.id).getAttribute("d"), edgePath(p1, p2, "canvas"), `wire ${e.id}`);
  }
});

// =============================================== night: dash and label, not hue

// MUTANT "next arc keeps the event dash" (FlowLoopArcBase draws
// `wireDash(kind, active)`). Observed, loopArcNext.test 7/9:
//   x night mode: the arc differs from an event wire by its dash and its chip,
//     not its stroke: the arc's own dash
//     expected 10 4 2 4
//     got      4 5
//   x while the run is live the arc keeps its dash; a plain wire goes to 7 6:
//     the live arc's dash
//     expected 10 4 2 4
//     got      7 6
// "count while dirty" goes red here too (8/9: "an unsaved edit: expected
// every pass: next panel, got every pass: next panel · 6 panels").
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
    eq(chipText(), "every pass: next panel · 6 panels", "the arc's label");
  } finally {
    win.document.documentElement.classList.remove("night");
  }
});

test("while the run is live the arc keeps its dash; a plain wire goes to 7 6", () => {
  mount({ run: { ...FLOWS_INIT.run, phase: "running" }, statuses: { n7: "ok" } });
  eq(visible("frame").getAttribute("stroke-dasharray"), "7 6", "precondition: a live event wire");
  eq(visible("loop").getAttribute("stroke-dasharray"), LOOP_ARC_DASH, "the live arc's dash");
  eq(visible("loop").getAttribute("class"), "nx-flow-wire nx-flow-wire-march", "live shows as the march");
});

// ================================================================= the chip

// DELIBERATE PIN CHANGE (S7 integration, #356): an edit used to be modelled
// as `dirty: true` alone, because the chip withheld its count by `dirty`.
// Since S7 both wire layers withhold it by `compiledIsCurrent`, which asks
// whether the answer was compiled from the graph on screen, so an edit is a
// NEW graph object (as every store edit makes one) and the answer carries the
// graph it came `from`. Unchanged, this file went 8/10 against the S7 layers.
test("the chip: no count before a compile, the compile's count after, none over an edit", () => {
  mount();
  eq(chipText(), "every pass: next panel", "before any compile");
  setFlows({ compiled: COMPILED });
  eq(chipText(), "every pass: next panel · 6 panels", "after the compile: 3x2, nothing skipped");
  setFlows({ graph: { nodes: NODES, edges: EDGES }, dirty: true });
  eq(chipText(), "every pass: next panel", "an edit the compile was not asked about");
});

// The chip names the run, so it sits on the run: in the middle of it, under
// every card. The numbers are worked by hand from the card formula, not read
// back from `loopArc`: the run is the FILTER CYCLE's bottom (60 + 143) + 28 =
// 231, and it spans the rise at 270 - 24 = 246 to the drop at 990 + 188 + 24
// = 1202, so its middle is x 724. Added by the S4-UARC verifier, because the
// classic file pinned the chip's place and this one did not. MUTANT "next chip
// at the handle" (FlowWires.tsx hands the chip `loop.handle` for
// `loop.label`), run in a private scratch copy of ui/. Observed,
// loopArcNext.test 9/10:
//   x the chip sits in the middle of the run, below the cards: the chip's
//     place
//     expected translate(724,231)
//     got      translate(1202,199)
test("the chip sits in the middle of the run, below the cards", () => {
  mount();
  const chip = container.querySelector('[data-testid="flow-loop-chip"]');
  ok(chip, "no chip on the Example's loop wire");
  eq(chip.getAttribute("transform"), "translate(724,231)", "the chip's place");
});

// MUTANT "chip on every arc" (loopChip asks only `loopArcTarget`). Observed,
// loopArcNext.test 8/9:
//   x a pass wire into a 1x1 block is drawn as the arc but carries no chip: a
//     chip promising 'every pass' over one panel with nothing to rotate between
//     expected null
//     got      [object SVGGElement]
test("a pass wire into a 1x1 block is drawn as the arc but carries no chip", () => {
  const nodes = NODES.map((n) => (n.id === "n2" ? { ...n, params: { ...n.params, rows: 1, cols: 1 } } : n));
  mount({ graph: { nodes, edges: EDGES } });
  ok(visible("loop").closest('[data-testid="flow-loop-arc"]'), "the wire is still the arc");
  eq(container.querySelector('[data-testid="flow-loop-chip"]'), null,
    "a chip promising 'every pass' over one panel with nothing to rotate between");
});

// ========================================================= the remove control

// MUTANT "next remove control at the anchor midpoint" (FlowWireDelete uses
// `wireMidpoint(a.p1, a.p2)` for every wire). Observed, loopArcNext.test 8/9:
//   x the selected loop wire's remove control sits on the arc, clear of every
//     card: the control's place
//     expected 1202,199
//     got      724,147
// 724,147 is the gap between AUTOFOCUS and GUIDE at port height, with no wire
// under it.
test("the selected loop wire's remove control sits on the arc, clear of every card", () => {
  mount({ sel: { kind: "edge", id: "loop" } }, "loop");
  const cut = container.querySelector('[data-testid="flow-wire-delete"]');
  ok(cut, "no remove control for the selected loop wire");
  const m = /translate3d\(([-\d.]+)px,([-\d.]+)px,0\)/.exec(cut.getAttribute("style") ?? "");
  ok(m, `the control carries no world position: ${cut.getAttribute("style")}`);
  eq(`${m![1]},${m![2]}`, `${EXPECTED_ARC.handle.x},${EXPECTED_ARC.handle.y}`, "the control's place");
  // 22 px across, centred: its box must not overlap any card's formula box.
  const [cx, cy] = [Number(m![1]), Number(m![2])];
  for (const n of NODES) {
    const w = 188;
    const h = 37 + ((NODE_DEFS as any)[n.type].ins.length + (NODE_DEFS as any)[n.type].outs.length) * 20 + 26;
    const clear = cx + 11 <= n.x || cx - 11 >= n.x + w || cy + 11 <= n.y || cy - 11 >= n.y + h;
    ok(clear, `the remove control at (${cx}, ${cy}) covers ${n.type.toUpperCase()} ${n.id}`);
  }
});

test("CONTROL: any other selected wire's remove control is at its anchors' midpoint", () => {
  mount({ sel: { kind: "edge", id: "frame" } }, "frame");
  const cut = container.querySelector('[data-testid="flow-wire-delete"]');
  const p1 = portPos(nodeOf("n7"), "frame", "out", NODE_DEFS, "tablet")!;
  const p2 = portPos(nodeOf("n8"), "events", "in", NODE_DEFS, "tablet")!;
  ok((cut.getAttribute("style") ?? "").includes(`translate3d(${(p1.x + p2.x) / 2}px,${(p1.y + p2.y) / 2}px,0)`),
    `the frame wire's control: ${cut.getAttribute("style")}`);
});

test("a click on the arc selects its wire", () => {
  mount();
  act(() => {
    container.querySelector('path[data-wire][data-edge-id="loop"]')
      .dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  const sel = useStore.getState().flows.sel;
  eq(sel?.kind === "edge" ? sel.id : null, "loop", "the selected wire");
});

// ----------------------------------------------------------------- report
act(() => root.render(null));
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nloopArcNext.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
