// cardLoopPanels.test.tsx - the one-tap LOOP PANELS on the TARGET card of BOTH
// canvases, MOUNTED (#189 S4 item 6; spec 2026-09-23 flows mosaic, 1.4 "When
// the wire is added": "the one-tap LOOP PANELS button on the card").
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/cardLoopPanels.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//   1. THE BUTTON IS OFFERED ONLY WHERE A PRESS DOES SOMETHING: a multi-panel
//      TARGET that owns a lane with a single tail and has no loop wire yet.
//      Never on a mosaic that already loops (a press would do nothing, or add
//      doctor M4's second wire), never on a single target or a block that owns
//      no stage, never on any other card.
//   2. ONE PRESS IS ONE `flowsApplyFraming(id, {}, true)`: one graph write,
//      one compile, and the wire leaves the lane's TAIL. From the first stage
//      the block owns it would be M12, a danger the doctor refuses to run.
//   3. THE OFFER IS A BOOLEAN SUBSCRIPTION TO THE GRAPH. The button appears
//      when the loop wire is deleted on a card whose node object never
//      changed, and a graph write that leaves the offer alone (another card
//      dragged) does not re-render the TARGET card: the re-render discipline
//      both card files exist for.
//   4. CONTROLS: the classic phone card, which has no footer, carries no
//      button and does not even ask for one (it does not wake when the offer
//      flips); and both cards' tooltips say the same sentence (the two files
//      may not import each other, so the words are written twice).
//
// Renders are counted the way cardFooterDom.test.tsx counts them: through
// `NODE_DEFS.target.sum`, which each card's footer calls once per render.
// Every mutant below was run in a private scratch copy of ui/ (#254), and the
// failure it produced is quoted verbatim.

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

// ------------------------------------------------------------- the fake rig
/** Every compile the press started, counted at the network. */
let compiles = 0;
g.fetch = async (url: string) => {
  if (url === "/api/flows/compile") compiles++;
  const data = url === "/api/flows/compile"
    ? { plan: {}, structural: [], issues: [], unmapped: [] } : { ok: true };
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
};

// ------------------------------------------------------------------- imports
const { createElement, act, Fragment, Profiler } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { NODE_DEFS } = await import("../nodeDefs");
const { FLOWS_INIT } = await import("../flowsSlice");
const ClassicCard = (await import("../FlowNodeCard")).default;
const { FlowNodeCard: NextCard, LOOP_PANELS_LABEL, LOOP_PANELS_WHY } =
  await import("../../../next/hubs/session/flows/canvas/FlowNode");
type FlowNodeRec = import("../flowsTypes").FlowNodeRec;
type FlowEdgeRec = import("../flowsTypes").FlowEdgeRec;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg}\n    expected ${JSON.stringify(b)}\n    got      ${JSON.stringify(a)}`);
}
function ok(cond: unknown, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ------------------------------------------------------------------- fixture
/** M31, three columns by two rows, then a CAPTURE LOOP and a FILTER CYCLE. The
 *  CAPTURE is the first stage the block owns and HAS a "pass done", so a wire
 *  from it is a real, wrong wire (M12), not one refused for a missing port. */
const T: FlowNodeRec = {
  id: "t", type: "target", x: 0, y: 0,
  params: { ...NODE_DEFS.target.params, name: "M31", rows: 2, cols: 3 },
};
const C1: FlowNodeRec = { id: "c1", type: "capture", x: 240, y: 0, params: { ...NODE_DEFS.capture.params } };
const CY: FlowNodeRec = { id: "cy", type: "cycle", x: 480, y: 0, params: { ...NODE_DEFS.cycle.params } };
/** A single target with a lane of its own. */
const S: FlowNodeRec = {
  id: "s", type: "target", x: 0, y: 300,
  params: { ...NODE_DEFS.target.params, name: "NGC 7331" },
};
const SC: FlowNodeRec = { id: "sc", type: "cycle", x: 240, y: 300, params: { ...NODE_DEFS.cycle.params } };
const E = (id: string, from: string, fromPort: string, to: string, toPort: string): FlowEdgeRec =>
  ({ id, from, fromPort, to, toPort });
const LANE: FlowEdgeRec[] = [
  E("a", "t", "target", "c1", "run"), E("b", "c1", "complete", "cy", "run"),
  E("sa", "s", "target", "sc", "run"),
];
const LOOP = E("loop", "cy", "pass", "t", "next");
const NODES = [T, C1, CY, S, SC];

const renders = { target: 0 };
{
  const orig = NODE_DEFS.target.sum;
  (NODE_DEFS.target as any).sum = (p: any) => { renders.target++; return orig(p); };
}

type Which = "classic" | "next";
const CARD = { classic: ClassicCard, next: NextCard } as const;
const BUTTON: Record<Which, string> = {
  classic: "[data-flows-loop]",
  next: "[data-testid='flow-node-loop']",
};

const container = win.document.getElementById("root");
const root = createRoot(container);

/** Every argument list `flowsApplyFraming` was called with. */
let framed: unknown[][] = [];
const realApply = useStore.getState().flowsApplyFraming;

/** A parent with NO store subscription: anything that re-renders after a
 *  store write re-rendered because the card itself asked to. */
function Deck({ which, nodes, phone }: { which: Which; nodes: FlowNodeRec[]; phone: boolean }) {
  return createElement(Fragment, null,
    nodes.map((n) => createElement(CARD[which] as any, { key: n.id, node: n, phone })));
}

function mount(which: Which, edges: FlowEdgeRec[], opts: { nodes?: FlowNodeRec[]; phone?: boolean } = {}): void {
  const nodes = opts.nodes ?? NODES;
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: { ...FLOWS_INIT, record: { id: "f", name: "M31" }, graph: { nodes, edges } },
      // A spy that forwards: the real action does the write.
      flowsApplyFraming: ((...args: any[]) => { framed.push(args); return (realApply as any)(...args); }),
    } as any);
  });
  framed = [];
  renders.target = 0;
  act(() => root.render(createElement(Deck, { which, nodes, phone: opts.phone ?? false })));
}
function setGraph(nodes: FlowNodeRec[], edges: FlowEdgeRec[]): void {
  act(() => {
    useStore.setState({ flows: { ...useStore.getState().flows, graph: { nodes, edges } } } as any);
  });
}
const cardOf = (id: string): any => container.querySelector(`[data-node-id="${id}"]`);
const buttonOf = (which: Which, id: string): any => cardOf(id)?.querySelector(BUTTON[which]) ?? null;
const settle = async (): Promise<void> => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

for (const which of ["classic", "next"] as const) {
  await test(`[${which}] a mosaic that owns a lane and has no loop wire offers LOOP PANELS`, () => {
    mount(which, LANE);
    const btn = buttonOf(which, "t");
    ok(btn != null, `the ${which} TARGET card of a looping-less mosaic offers no LOOP PANELS`);
    eq(String(btn.textContent).trim(), LOOP_PANELS_LABEL, `the ${which} button's label`);
    ok(String(btn.getAttribute("aria-label")).includes(LOOP_PANELS_WHY),
      `the ${which} button's name says what it wires, got "${btn.getAttribute("aria-label")}"`);
  });

  // MUTANTS "LOOP PANELS shown while the wire exists", one per card (the
  // card's offer ignores the loop wire: `loopSource(graph, id) !== null`).
  // Observed, cardLoopPanels.test 10/13 each; for the classic card:
  //   x [classic] a mosaic that already loops is offered nothing: the classic
  //     card offers LOOP PANELS on a mosaic whose loop wire is already there
  //   x [classic] one press is one flowsApplyFraming(id, {}, true): one
  //     write, one compile, from the TAIL: the classic button stayed after it
  //     did its work
  //   x [classic] deleting the loop wire brings the button, and a drag
  //     elsewhere does not re-render the TARGET: precondition: a looped
  //     mosaic offered LOOP PANELS
  //   (and the same three lines for [next], "the next card", "the next
  //   button")
  await test(`[${which}] a mosaic that already loops is offered nothing`, () => {
    mount(which, [...LANE, LOOP]);
    ok(cardOf("t") != null, "precondition: the TARGET card did not render");
    ok(buttonOf(which, "t") == null,
      `the ${which} card offers LOOP PANELS on a mosaic whose loop wire is already there`);
  });

  // MUTANTS "button adds the wire from the first owned stage", one per card
  // (the press wires `panelLane(graph, id)[0]`'s "pass done" to the TARGET
  // through `flowsConnect`, M12 by panelLane). Observed, cardLoopPanels.test
  // 12/13 each:
  //   x [classic] one press is one flowsApplyFraming(id, {}, true): one
  //     write, one compile, from the TAIL: the classic press wires the lane's
  //     TAIL; from any earlier stage it is M12
  //     expected "cy.pass -> t.next"
  //     got      "c1.pass -> t.next"
  //   (and the same for [next]: "the next press wires the lane's TAIL")
  await test(`[${which}] one press is one flowsApplyFraming(id, {}, true): one write, one compile, from the TAIL`, async () => {
    mount(which, LANE);
    const btn = buttonOf(which, "t");
    ok(btn != null, "precondition: no LOOP PANELS button");
    let writes = 0;
    const unsub = useStore.subscribe((s: any, prev: any) => {
      if (s.flows.graph !== prev.flows.graph) writes++;
    });
    const before = compiles;
    act(() => { btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
    await settle();
    unsub();

    // The wire first, so a press that wired the wrong stage says which.
    const added = (useStore.getState().flows.graph.edges as FlowEdgeRec[])
      .filter((e) => !LANE.some((x) => x.id === e.id));
    eq(added.map((e) => `${e.from}.${e.fromPort} -> ${e.to}.${e.toPort}`).join(" | "), "cy.pass -> t.next",
      `the ${which} press wires the lane's TAIL; from any earlier stage it is M12`);
    eq(JSON.stringify(framed), JSON.stringify([["t", {}, true]]),
      `the ${which} press is exactly one flowsApplyFraming(id, {}, true)`);
    eq(writes, 1, `the ${which} press wrote the graph more than once (or not at all)`);
    eq(compiles - before, 1, `the ${which} press did not start exactly one compile`);
    ok(buttonOf(which, "t") == null, `the ${which} button stayed after it did its work`);
  });

  // MUTANTS "offer is not a subscription", one per card (the card reads the
  // offer once, `useState(() => offersLoopPanels(getState().flows.graph, id))`).
  // Observed, cardLoopPanels.test 10/13 for the classic card:
  //   x [classic] one press is one flowsApplyFraming(id, {}, true): one
  //     write, one compile, from the TAIL: the classic button stayed after it
  //     did its work
  //   x [classic] deleting the loop wire brings the button, and a drag
  //     elsewhere does not re-render the TARGET: the classic card did not
  //     offer LOOP PANELS once the loop wire was deleted
  //   x CONTROL: the classic phone card does not wake when the offer flips:
  //     precondition: a lane ending on a SLEW still offered LOOP PANELS
  // and 11/13 for [next], the same first two lines ("the next button", "the
  // next card").
  // MUTANTS "offer subscribes to the graph object", one per card (the card
  // selects `s.flows.graph` and asks `offersLoopPanels` in render). Observed,
  // cardLoopPanels.test 11/13 for the classic card:
  //   x [classic] deleting the loop wire brings the button, and a drag
  //     elsewhere does not re-render the TARGET: the classic TARGET cards
  //     re-rendered for a drag of another card
  //     expected 0
  //     got      2
  //   x CONTROL: the classic phone card does not wake when the offer flips:
  //     the phone TARGET card woke for an offer it never draws
  //     expected 0
  //     got      1
  // and 12/13 for [next], the first failure only ("the next TARGET cards").
  await test(`[${which}] deleting the loop wire brings the button, and a drag elsewhere does not re-render the TARGET`, () => {
    mount(which, [...LANE, LOOP]);
    ok(buttonOf(which, "t") == null, "precondition: a looped mosaic offered LOOP PANELS");
    setGraph(NODES, LANE);
    ok(buttonOf(which, "t") != null,
      `the ${which} card did not offer LOOP PANELS once the loop wire was deleted`);
    const before = renders.target;
    // Another card dragged: every TARGET card's offer is unchanged.
    setGraph(NODES.map((n) => (n.id === "c1" ? { ...n, x: n.x + 40 } : n)), LANE);
    eq(renders.target - before, 0,
      `the ${which} TARGET cards re-rendered for a drag of another card`);
  });

  // MUTANTS "offer ignores the multi-panel check", one per card (the offer
  // asks `withLoop` of the block read as 2 rows). Observed,
  // cardLoopPanels.test 12/13 each:
  //   x [classic] CONTROL: a single target, a mosaic with no stage, and every
  //     other card offer nothing: the classic single target offers LOOP
  //     PANELS
  //   (and the same for [next], "the next single target")
  await test(`[${which}] CONTROL: a single target, a mosaic with no stage, and every other card offer nothing`, () => {
    mount(which, LANE);
    ok(buttonOf(which, "s") == null, `the ${which} single target offers LOOP PANELS`);
    for (const id of ["c1", "cy", "sc"]) {
      ok(buttonOf(which, id) == null, `the ${which} ${id} card offers LOOP PANELS`);
    }
    // The same mosaic with its lane cut away owns no stage.
    mount(which, [], { nodes: [T] });
    ok(cardOf("t") != null, "precondition: the bare TARGET card did not render");
    ok(buttonOf(which, "t") == null, `the ${which} card offers LOOP PANELS on a mosaic that owns no stage`);
    // A branched lane has no single tail, so no wire can be its loop wire.
    mount(which, [...LANE, E("x", "t", "target", "cy", "run")].filter((e) => e.id !== "b"));
    ok(buttonOf(which, "t") == null, `the ${which} card offers LOOP PANELS on a branched lane`);
  });
}

await test("CONTROL: the classic phone card has no footer, so no LOOP PANELS", () => {
  mount("classic", LANE, { phone: true });
  ok(cardOf("t") != null, "precondition: the phone TARGET card did not render");
  ok(buttonOf("classic", "t") == null, "the 150 px phone card grew a footer button its layout budgets no room for");
});

// The phone card's offer selector is `!phone && ...`, and the button sits in
// the `!phone` footer, so dropping that guard changes no markup: the card
// would ask `withLoop` on every graph write and wake whenever the offer flips,
// for a button it never draws. The phone card has no footer and so never
// calls `sum`, which leaves the render counter above blind to it; a Profiler
// around each card counts its commits instead. The desktop card, under the
// same write, is the control that the count can see a wake at all.
//
// THE WRITE MUST FLIP THE OFFER AND NOTHING ELSE THE CARD DRAWS. A Profiler
// counts a commit anywhere under it, the card's ports included, so wiring the
// TARGET's own lane (its "target" output) wakes the phone card whatever the
// offer does: the first draft of this case did that and was red on the
// unmutated code. Appending a legacy SLEW after the FILTER CYCLE leaves every
// TARGET port and the (absent) loop wire alone and makes the tail a stage
// with no "pass done", so a press could add nothing and the offer goes.
// MUTANT "offer asked on the phone card" (the classic selector without
// `!phone`). Observed, cardLoopPanels.test 12/13:
//   x CONTROL: the classic phone card does not wake when the offer flips:
//     the phone TARGET card woke for an offer it never draws
//     expected 0
//     got      1
await test("CONTROL: the classic phone card does not wake when the offer flips", () => {
  const SL: FlowNodeRec = { id: "sl", type: "slew", x: 720, y: 0, params: { ...NODE_DEFS.slew.params } };
  const commits: Record<string, number> = {};
  const onRender = (id: string): void => { commits[id] = (commits[id] ?? 0) + 1; };
  const ProfiledDeck = ({ phone }: { phone: boolean }) => createElement(Fragment, null,
    NODES.map((n) => createElement(Profiler, { key: n.id, id: n.id, onRender },
      createElement(ClassicCard as any, { node: n, phone }))));
  for (const phone of [false, true]) {
    act(() => root.render(null));
    act(() => {
      useStore.setState({
        flows: { ...FLOWS_INIT, record: { id: "f", name: "M31" }, graph: { nodes: NODES, edges: LANE } },
      } as any);
    });
    act(() => root.render(createElement(ProfiledDeck, { phone })));
    if (!phone) ok(buttonOf("classic", "t") != null, "precondition: the desktop card did not offer LOOP PANELS");
    const before = commits.t ?? 0;
    setGraph([...NODES, SL], [...LANE, E("s1", "cy", "complete", "sl", "run")]);
    const woke = (commits.t ?? 0) - before;
    if (phone) {
      eq(woke, 0, "the phone TARGET card woke for an offer it never draws");
    } else {
      ok(buttonOf("classic", "t") == null, "precondition: a lane ending on a SLEW still offered LOOP PANELS");
      eq(woke, 1, "precondition: the desktop TARGET card's offer went and the Profiler did not see it wake");
    }
  }
});

// MUTANT "classic tooltip words drift" ("every pass" -> "each pass" in the
// classic card's LOOP_PANELS_WHY). Observed, cardLoopPanels.test 11/13:
//   x [classic] a mosaic that owns a lane and has no loop wire offers LOOP
//     PANELS: the classic button's name says what it wires, got "LOOP PANELS:
//     Wire the panel lane's last stage 'pass done' to this TARGET's 'next
//     panel', so each pass moves to the next panel"
//   x CONTROL: both cards' tooltips say the same sentence: the classic card's
//     words drifted from the #/next card's LOOP_PANELS_WHY
//     expected "Wire the panel lane's last stage 'pass done' to this TARGET's
//       'next panel', so every pass moves to the next panel"
//     got      "Wire the panel lane's last stage 'pass done' to this TARGET's
//       'next panel', so each pass moves to the next panel"
await test("CONTROL: both cards' tooltips say the same sentence", () => {
  mount("classic", LANE);
  eq(buttonOf("classic", "t")?.getAttribute("title"), LOOP_PANELS_WHY,
    "the classic card's words drifted from the #/next card's LOOP_PANELS_WHY");
});

act(() => root.render(null));

// ------------------------------------------------------------------- report
const total = passed + failed;
for (const f of failures) console.log(f);
console.log(`cardLoopPanels.test: ${passed}/${total} passed`);
export const result = { passed, failed, total };
