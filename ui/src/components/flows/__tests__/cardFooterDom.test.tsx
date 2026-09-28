// cardFooterDom.test.tsx - the TARGET card's footer line on BOTH canvases,
// MOUNTED (#189 S4 item 6; spec 2026-09-23 flows mosaic, 1.2 "Card footer";
// S4 orchestrator ruling 1: a grid is written columns by rows).
//
//   Run directly:  node --import tsx src/components/flows/__tests__/cardFooterDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//   1. Both cards draw `targetFooter` for a TARGET, so the classic canvas and
//      #/next read the same line: "M31 · 3x2 · PA 30.0 · 25% · rotate",
//      "M31 · 3x2 · one panel at a time", "NGC 7331 · any angle".
//   2. The loop is read from the GRAPH: deleting the loop wire changes the line
//      on a card whose node object never changed, which only a subscription to
//      the graph's loop can do.
//   3. That subscription is a BOOLEAN. A graph write that leaves the loop alone
//      (another card dragged) must not re-render the TARGET card - the
//      re-render discipline both card files exist for.
//   4. CONTROL: every other card keeps its vocabulary `sum`, and the progress
//      chip and the loss mark are drawn as before.
//
// targetSummary.test.ts holds the formatter; this file holds that the cards
// call it and stay narrow while doing so. Renders are counted the way
// flowNodeDom.test.tsx and flowProgressChip.test.tsx count them: through
// `NODE_DEFS.<type>.sum`, which each card's footer calls once per render.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The #/next card's tree imports `.css` files.
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

// ------------------------------------------------------------------- imports
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { NODE_DEFS } = await import("../nodeDefs");
const { FLOWS_INIT } = await import("../flowsSlice");
const ClassicCard = (await import("../FlowNodeCard")).default;
const { FlowNodeCard: NextCard } = await import("../../../next/hubs/session/flows/canvas/FlowNode");
type FlowNodeRec = import("../flowsTypes").FlowNodeRec;
type FlowEdgeRec = import("../flowsTypes").FlowEdgeRec;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg}\n    expected ${JSON.stringify(b)}\n    got      ${JSON.stringify(a)}`);
}
function ok(cond: unknown, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ------------------------------------------------------------------- fixture
/** M31, three columns by two rows, Rotate to PA 30 at 25% overlap, then
 *  AUTOFOCUS and a FILTER CYCLE, and the cycle's "pass done" wired back to the
 *  TARGET's "next panel". */
const T: FlowNodeRec = {
  id: "t", type: "target", x: 0, y: 0,
  params: { ...NODE_DEFS.target.params, name: "M31", rows: 2, cols: 3, overlap: 25,
    angle: "Rotate to PA", rotation: 30 },
};
const AF: FlowNodeRec = { id: "af", type: "autofocus", x: 240, y: 0, params: { ...NODE_DEFS.autofocus.params } };
const CY: FlowNodeRec = { id: "cy", type: "cycle", x: 480, y: 0, params: { ...NODE_DEFS.cycle.params } };
const S: FlowNodeRec = {
  id: "s", type: "target", x: 0, y: 300,
  params: { ...NODE_DEFS.target.params, name: "NGC 7331", angle: "Any angle", rotation: -1 },
};
const E = (id: string, from: string, fromPort: string, to: string, toPort: string): FlowEdgeRec =>
  ({ id, from, fromPort, to, toPort });
const LANE: FlowEdgeRec[] = [E("a", "t", "target", "af", "run"), E("b", "af", "focused", "cy", "run")];
const LOOP = E("loop", "cy", "pass", "t", "next");
const NODES = [T, AF, CY, S];

const renders: Record<string, number> = { target: 0, autofocus: 0, cycle: 0 };
for (const t of ["target", "autofocus", "cycle"] as const) {
  const orig = NODE_DEFS[t].sum;
  (NODE_DEFS[t] as any).sum = (p: any) => { renders[t]++; return orig(p); };
}

type Which = "classic" | "next";
const CARD = { classic: ClassicCard, next: NextCard } as const;
const SUMMARY: Record<Which, string> = {
  classic: "[data-flow-summary]",
  next: "[data-testid='flow-node-summary']",
};
const CHIP: Record<Which, string> = {
  classic: "[data-flow-progress]",
  next: "[data-testid='flow-node-progress']",
};

const container = win.document.getElementById("root");
const root = createRoot(container);

/** A parent with NO store subscription: anything that re-renders after a
 *  store write re-rendered because the card itself asked to. */
function Deck({ which }: { which: Which }) {
  return createElement(Fragment, null,
    NODES.map((n) => createElement(CARD[which] as any, { key: n.id, node: n })));
}

function mount(which: Which, edges: FlowEdgeRec[], flows: Record<string, unknown> = {}): void {
  act(() => root.render(null));
  act(() => {
    useStore.setState({ flows: { ...FLOWS_INIT, graph: { nodes: NODES, edges }, ...flows } } as any);
  });
  for (const k of Object.keys(renders)) renders[k] = 0;
  act(() => root.render(createElement(Deck, { which })));
}
function setFlows(p: Record<string, unknown>): void {
  act(() => { useStore.setState({ flows: { ...useStore.getState().flows, ...p } } as any); });
}
const cardOf = (id: string): any => container.querySelector(`[data-node-id="${id}"]`);
const footerOf = (which: Which, id: string): string | null =>
  cardOf(id)?.querySelector(SUMMARY[which])?.textContent ?? null;

for (const which of ["classic", "next"] as const) {
  // MUTANT "rows x cols" (targetFooter writes `${rows}x${cols}`). Observed,
  // cardFooterDom.test 6/10 (this case and the next, on both cards):
  //   x [classic] a rotating mosaic reads 'M31 · 3x2 · PA 30.0 · 25% ·
  //     rotate': the classic TARGET card's footer
  //     expected "M31 · 3x2 · PA 30.0 · 25% · rotate"
  //     got      "M31 · 2x3 · PA 30.0 · 25% · rotate"
  //   (and the same line for [next])
  // MUTANTS "classic TARGET footer keeps sum" / "next TARGET footer keeps
  // sum" (the card draws `def.sum(node.params)` for every type). Observed,
  // 7/10, the three TARGET cases of that card:
  //   x [classic] a rotating mosaic reads ...: the classic TARGET card's footer
  //     expected "M31 · 3x2 · PA 30.0 · 25% · rotate"
  //     got      "M31"
  test(`[${which}] a rotating mosaic reads 'M31 · 3x2 · PA 30.0 · 25% · rotate'`, () => {
    mount(which, [...LANE, LOOP]);
    eq(footerOf(which, "t"), "M31 · 3x2 · PA 30.0 · 25% · rotate", `the ${which} TARGET card's footer`);
  });

  // MUTANT "loop read from params" (targetLoops answers isMultiPanel(node)).
  // Observed, cardFooterDom.test 6/10:
  //   x [classic] deleting the loop wire reads 'M31 · 3x2 · one panel at a
  //     time': the classic TARGET card's footer once its loop wire is gone
  //     expected "M31 · 3x2 · one panel at a time"
  //     got      "M31 · 3x2 · PA 30.0 · 25% · rotate"
  //   (and the same line for [next], and the re-render case below: "the
  //   classic mosaic's card, and only it, re-renders when its loop goes:
  //   expected 3, got 2")
  // Re-run by a second verifier once LOOP PANELS landed on both cards: 8/10.
  // The re-render case stays green now, because the card's LOOP PANELS
  // selector flips when the loop goes and re-renders it anyway. The two
  // footer lines above still go red, and the branch case below holds the
  // footer's own subscription where LOOP PANELS cannot mask it.
  test(`[${which}] deleting the loop wire reads 'M31 · 3x2 · one panel at a time'`, () => {
    mount(which, [...LANE, LOOP]);
    setFlows({ graph: { nodes: NODES, edges: LANE } });
    eq(footerOf(which, "t"), "M31 · 3x2 · one panel at a time",
      `the ${which} TARGET card's footer once its loop wire is gone`);
  });

  test(`[${which}] a single target reads 'NGC 7331 · any angle'`, () => {
    mount(which, LANE);
    eq(footerOf(which, "s"), "NGC 7331 · any angle", `the ${which} single target's footer`);
  });

  // MUTANTS "classic footer subscribes to the graph" / "next footer
  // subscribes to the graph" (the card selects `s.flows.graph` and passes it
  // to targetLoops in render). Observed, cardFooterDom.test 9/10 each:
  //   x [classic] a graph write that leaves the loop alone does not re-render
  //     the TARGET card: the classic TARGET cards re-rendered for a drag of
  //     another card
  //     expected 2
  //     got      4
  //   (and the same line for [next] under its own mutant)
  test(`[${which}] a graph write that leaves the loop alone does not re-render the TARGET card`, () => {
    mount(which, [...LANE, LOOP]);
    eq(renders.target, 2, "precondition: one render per TARGET card (the mosaic and the single)");
    const moved = NODES.map((n) => (n.id === "cy" ? { ...n, x: 520 } : n));
    setFlows({ graph: { nodes: moved, edges: [...LANE, LOOP] } });
    eq(renders.target, 2, `the ${which} TARGET cards re-rendered for a drag of another card`);
    setFlows({ graph: { nodes: moved, edges: LANE } });
    eq(renders.target, 3, `the ${which} mosaic's card, and only it, re-renders when its loop goes`);
  });

  // THE FOOTER'S OWN SUBSCRIPTION. Deleting the loop wire also turns LOOP
  // PANELS on, and that selector re-renders the card by itself, so the delete
  // case above cannot tell whether the footer subscribes to the loop. A branch
  // at the tail can: the lane stops being one chain, so it has no tail, no
  // loop wire (panelLane `loopWires`, compile.py `loop_wires`) and nothing for
  // LOOP PANELS to add either. The loop wire stays in the graph, the TARGET's
  // node object never changes, and only the footer's loop selector moves.
  // Added by the S4-UARC verifier. MUTANTS "classic footer reads the loop
  // unsubscribed" / "next footer reads the loop unsubscribed" (the card's
  // `loops` is `targetLoops(node, useStore.getState().flows.graph)`, read in
  // render), each run in a private scratch copy of ui/. Observed,
  // cardFooterDom.test 11/12 each:
  //   x [classic] a branch at the tail ends the rotation, and the card says
  //     so: the classic TARGET card's footer once its lane branches
  //     expected "M31 · 3x2 · one panel at a time"
  //     got      "M31 · 3x2 · PA 30.0 · 25% · rotate"
  //   (and the same line for [next] under its own mutant)
  test(`[${which}] a branch at the tail ends the rotation, and the card says so`, () => {
    const loopBtn = which === "classic" ? "[data-flows-loop]" : "[data-testid='flow-node-loop']";
    mount(which, [...LANE, LOOP]);
    eq(footerOf(which, "t"), "M31 · 3x2 · PA 30.0 · 25% · rotate", "precondition: the mosaic rotates");
    eq(cardOf("t")?.querySelector(loopBtn) ?? null, null, "precondition: no LOOP PANELS on a looped mosaic");
    const cap: FlowNodeRec = { id: "cap", type: "capture", x: 480, y: 200, params: { ...NODE_DEFS.capture.params } };
    // AUTOFOCUS now feeds the cycle AND a CAPTURE: two stages at one depth.
    setFlows({ graph: { nodes: [...NODES, cap], edges: [...LANE, E("c", "af", "focused", "cap", "run"), LOOP] } });
    eq(cardOf("t")?.querySelector(loopBtn) ?? null, null,
      "precondition: a branched lane offers no LOOP PANELS, so that selector did not move");
    eq(footerOf(which, "t"), "M31 · 3x2 · one panel at a time",
      `the ${which} TARGET card's footer once its lane branches`);
  });

  // CONTROL: every other card's footer is its vocabulary sum, and the chip
  // and the loss mark are what they were.
  test(`[${which}] CONTROL: other cards keep their sum; the chip and the loss mark are unchanged`, () => {
    mount(which, [...LANE, LOOP], {
      dirty: false,
      progress: {
        flow_id: "f1",
        session: { id: "s1", status: "dormant", nights: 1, count_mode: "accepted" },
        blocks: [{
          node_id: "s", name: "NGC 7331", kind: "target", banked: 212, owed: 103, total: 315,
          panels: [{ target_id: "x", name: "NGC 7331", row: 0, col: 0, banked: 212, owed: 103, total: 315, steps: [] }],
        }],
        orphaned: { frames: 0, steps: 0 },
      },
      compiled: {
        plan: {}, structural: [], issues: [],
        unmapped: [{ key: "nodes.cycle.dither", detail: "not honoured", level: "danger" }],
      },
    });
    eq(footerOf(which, "cy"), NODE_DEFS.cycle.sum(CY.params), `the ${which} FILTER CYCLE's footer`);
    eq(footerOf(which, "af"), NODE_DEFS.autofocus.sum(AF.params), `the ${which} AUTOFOCUS's footer`);
    eq(cardOf("s")?.querySelector(CHIP[which])?.textContent ?? null, "212/315 subs",
      `the ${which} single target's progress chip`);
    ok(cardOf("cy")?.querySelector("[data-node-loss='danger']"),
      `the ${which} FILTER CYCLE lost its loss mark`);
  });
}

// ----------------------------------------------------------------- report
act(() => root.render(null));
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ncardFooterDom.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
