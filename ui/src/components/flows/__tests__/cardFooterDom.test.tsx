// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// cardFooterDom.test.tsx - the TARGET card's footer line on BOTH canvases,
// MOUNTED (#189 S4 item 6; spec 2026-09-23 flows mosaic, 1.2 "Card footer";
// S4 orchestrator ruling 1: a grid is written columns by rows).
//
//   Run directly:  node --import tsx src/components/flows/__tests__/cardFooterDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//   1. Both cards draw the TARGET line from targetSummary.ts: #/next the whole
//      `targetFooter`, which wraps - "M31 · rotate · 3x2 · PA 30.0 · 25%",
//      "M31 · one panel at a time · 3x2", "NGC 7331 · any angle" - and the
//      classic card, whose footer is ONE line, the same line fitted to it
//      (`fittedFooter`, S7 orchestrator ruling 9, #357): the overlap goes
//      first, then the name is shortened with an ellipsis, and the loop word
//      and the angle words are never cut. The budget cases below compute how
//      many characters that line holds from the mounted card's own classes,
//      hold the card's constant to it, and hold the ruling's cases to it.
//   2. The loop is read from the GRAPH: deleting the loop wire changes the line
//      on a card whose node object never changed, which only a subscription to
//      the graph's loop can do.
//   3. That subscription is a BOOLEAN. A graph write that leaves the loop alone
//      (another card dragged) must not re-render the TARGET card - the
//      re-render discipline both card files exist for.
//   4. CONTROL: every other card keeps its vocabulary `sum`, and the progress
//      chip and the loss mark are drawn as before.
//   5. #410: on a lane whose loop wire is stranded mid-lane (M12) the card
//      says "one panel at a time" and offers LOOP PANELS, and the press MOVES
//      the wire to the tail, keeping its id, so the card then says "rotate"
//      over a flow the compile accepts, on both cards.
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
// LOOP PANELS' press compiles (flowsApplyFraming); the answer is not read.
g.fetch = async () => {
  const data = { plan: {}, structural: [], issues: [], unmapped: [] };
  return {
    ok: true, status: 200, statusText: "OK", headers: { get: () => "application/json" },
    json: async () => data, text: async () => JSON.stringify(data),
  };
};

// ------------------------------------------------------------------- imports
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { NODE_DEFS } = await import("../nodeDefs");
const { FLOWS_INIT } = await import("../flowsSlice");
const ClassicCard = (await import("../FlowNodeCard")).default;
const { CLASSIC_FOOTER_CHARS } = await import("../FlowNodeCard");
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
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
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

/** The fixture's M31 (3x2, Rotate to PA 30, 25%) as each card draws it:
 *  #/next the whole line, which wraps; the classic card the line fitted to
 *  its 29 characters (ruling 9). Written out, not computed with the code
 *  under test. The rotating line is 34 characters, so its overlap goes and
 *  the rest, 28, fits with the name whole. The panel-first one is 31, and
 *  after the overlap it has none to give: one character of room for the
 *  name, so the grid goes before the name would be only an ellipsis
 *  (targetSummary.ts "PAST THE RULING, COMPUTED"). */
const ROTATING: Record<Which, string> = {
  classic: "M31 · rotate · 3x2 · PA 30.0",
  next: "M31 · rotate · 3x2 · PA 30.0 · 25%",
};
const PANEL_FIRST: Record<Which, string> = {
  classic: "M31 · one panel at a time",
  next: "M31 · one panel at a time · 3x2",
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
  // The records below were re-run in the private scratch copy
  // scratchpad/S5-LOOP-mut once the footer's loop word moved to second place
  // (#357), and again in S7-UCANVAS-mut once the classic card fitted its line
  // (S7 orchestrator ruling 9), so each quotes the strings the file now holds.
  //
  // MUTANT "rows x cols" (targetFooter writes `${rows}x${cols}`). Observed,
  // cardFooterDom.test 13/26 (every mosaic line that still shows its grid, on
  // both cards; the classic panel-first line has dropped it):
  //   x [classic] a rotating mosaic reads 'M31 · rotate · 3x2 · PA 30.0': the classic TARGET card's footer
  //     expected "M31 · rotate · 3x2 · PA 30.0"
  //     got      "M31 · rotate · 2x3 · PA 30.0"
  //   x [next] a rotating mosaic reads 'M31 · rotate · 3x2 · PA 30.0 · 25%': the next TARGET card's footer
  //     expected "M31 · rotate · 3x2 · PA 30.0 · 25%"
  //     got      "M31 · rotate · 2x3 · PA 30.0 · 25%"
  // MUTANTS "classic TARGET footer keeps sum" / "next TARGET footer keeps
  // sum" (the card draws `def.sum(node.params)` for every type; the classic
  // card fits nothing). Observed, 13/26 and 20/26, every TARGET case of that
  // card:
  //   x [classic] a rotating mosaic reads 'M31 · rotate · 3x2 · PA 30.0': the classic TARGET card's footer
  //     expected "M31 · rotate · 3x2 · PA 30.0"
  //     got      "M31"
  //   x [next] a rotating mosaic reads 'M31 · rotate · 3x2 · PA 30.0 · 25%': the next TARGET card's footer
  //     expected "M31 · rotate · 3x2 · PA 30.0 · 25%"
  //     got      "M31"
  test(`[${which}] a rotating mosaic reads '${ROTATING[which]}'`, () => {
    mount(which, [...LANE, LOOP]);
    eq(footerOf(which, "t"), ROTATING[which], `the ${which} TARGET card's footer`);
  });

  // MUTANT "loop read from params" (targetLoops answers isMultiPanel(node)).
  // First observed, cardFooterDom.test 6/10, with the design's strings; on the
  // re-run in S5-LOOP-mut (9/16):
  //   x [classic] deleting the loop wire reads 'M31 · one panel at a time ·
  //     3x2': the classic TARGET card's footer once its loop wire is gone
  //     expected "M31 · one panel at a time · 3x2"
  //     got      "M31 · rotate · 3x2 · PA 30.0 · 25%"
  //   (and the same line for [next], and the re-render case below: "the
  //   classic mosaic's card, and only it, re-renders when its loop goes:
  //   expected 3, got 2")
  // Re-run by a second verifier once LOOP PANELS landed on both cards: 8/10.
  // The re-render case stays green now, because the card's LOOP PANELS
  // selector flips when the loop goes and re-renders it anyway. The two
  // footer lines above still go red, and the branch case below holds the
  // footer's own subscription where LOOP PANELS cannot mask it. Re-run in
  // S7-UCANVAS-mut (17/26):
  //   x [classic] deleting the loop wire reads 'M31 · one panel at a time': the classic TARGET card's footer once its loop wire is gone
  //     expected "M31 · one panel at a time"
  //     got      "M31 · rotate · 3x2 · PA 30.0"
  //   x [next] deleting the loop wire reads 'M31 · one panel at a time · 3x2': the next TARGET card's footer once its loop wire is gone
  //     expected "M31 · one panel at a time · 3x2"
  //     got      "M31 · rotate · 3x2 · PA 30.0 · 25%"
  test(`[${which}] deleting the loop wire reads '${PANEL_FIRST[which]}'`, () => {
    mount(which, [...LANE, LOOP]);
    setFlows({ graph: { nodes: NODES, edges: LANE } });
    eq(footerOf(which, "t"), PANEL_FIRST[which],
      `the ${which} TARGET card's footer once its loop wire is gone`);
  });

  test(`[${which}] a single target reads 'NGC 7331 · any angle'`, () => {
    mount(which, LANE);
    eq(footerOf(which, "s"), "NGC 7331 · any angle", `the ${which} single target's footer`);
  });

  // MUTANTS "classic footer subscribes to the graph" / "next footer
  // subscribes to the graph" (the card selects `s.flows.graph` and passes it
  // to targetLoops in render). Observed, cardFooterDom.test 9/10 each, and
  // 25/26 each on the re-run in S7-UCANVAS-mut:
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
  // render), each run in a private scratch copy of ui/. First observed,
  // cardFooterDom.test 11/12 each; on the re-run in S5-LOOP-mut, 15/16 each;
  // on the re-run in S7-UCANVAS-mut, 25/26 each:
  //   x [classic] a branch at the tail ends the rotation, and the card says so: the classic TARGET card's footer once its lane branches
  //     expected "M31 · one panel at a time"
  //     got      "M31 · rotate · 3x2 · PA 30.0"
  //   x [next] a branch at the tail ends the rotation, and the card says so: the next TARGET card's footer once its lane branches
  //     expected "M31 · one panel at a time · 3x2"
  //     got      "M31 · rotate · 3x2 · PA 30.0 · 25%"
  test(`[${which}] a branch at the tail ends the rotation, and the card says so`, () => {
    const loopBtn = which === "classic" ? "[data-flows-loop]" : "[data-testid='flow-node-loop']";
    mount(which, [...LANE, LOOP]);
    eq(footerOf(which, "t"), ROTATING[which], "precondition: the mosaic rotates");
    eq(cardOf("t")?.querySelector(loopBtn) ?? null, null, "precondition: no LOOP PANELS on a looped mosaic");
    const cap: FlowNodeRec = { id: "cap", type: "capture", x: 480, y: 200, params: { ...NODE_DEFS.capture.params } };
    // AUTOFOCUS now feeds the cycle AND a CAPTURE: two stages at one depth.
    setFlows({ graph: { nodes: [...NODES, cap], edges: [...LANE, E("c", "af", "focused", "cap", "run"), LOOP] } });
    eq(cardOf("t")?.querySelector(loopBtn) ?? null, null,
      "precondition: a branched lane offers no LOOP PANELS, so that selector did not move");
    eq(footerOf(which, "t"), PANEL_FIRST[which],
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

// ================================================ the classic card's budget
//
// #357. The classic footer is ONE line, and the design's order put the loop
// word last: "M31 · 3x2 · PA 30.0 · 25% · rotate" is 34 characters, and the
// ellipsis fell inside "rotate". S5 moved the word second, and `truncate`
// still cut the overlap and the end of the PA on the S5/S6 probe's rotating
// 2x2 ("M31 · rotate · 2x2 · PA 55.0..."). S7 orchestrator ruling 9: the
// line is FITTED instead - the overlap goes first, then the name is
// shortened with an ellipsis, and the angle words (the mode and the PA) and
// the loop word are never cut; #/next keeps the whole line and wraps.
//
// The budget is computed here from the card as it renders - its width, its
// border, the footer's padding and type size, read off the mounted element -
// so a card that grows its padding or its type fails this case rather than
// leaving it grading a card nobody draws. IBM Plex Mono (Tailwind's
// `font-mono`, index.css `--font-mono`) advances 0.6 em per character, and
// Tailwind's spacing unit is 4 px. A line of 29 fits; a longer one would
// show 28 characters and the ellipsis, which is what a fitted line with a
// shortened name shows too.
const EIGHTH: FlowNodeRec = {
  id: "t", type: "target", x: 0, y: 0,
  params: { ...NODE_DEFS.target.params, name: "M31", rows: 2, cols: 3, overlap: 25,
    angle: "Rotate to PA", rotation: 55 },
};

/** How many characters the classic TARGET card's footer line shows, from the
 *  mounted card: (width - 2 x border - 2 x padding) / (font size x 0.6). */
function classicBudget(): { chars: number; why: string } {
  const card = cardOf("t");
  const line = card.querySelector(SUMMARY.classic);
  const footer = line.parentElement;
  ok(/(^|\s)truncate(\s|$)/.test(line.className), `the footer line no longer truncates: "${line.className}"`);
  ok(/(^|\s)font-mono(\s|$)/.test(footer.className), `the footer is no longer mono: "${footer.className}"`);
  const width = parseFloat(card.style.width);
  const border = /(^|\s)border(\s|$)/.test(card.className) ? 1 : 0;
  const pad = Number(/(?:^|\s)px-(\d+(?:\.\d+)?)(?:\s|$)/.exec(footer.className)?.[1] ?? NaN) * 4;
  const font = Number(/(?:^|\s)text-\[(\d+(?:\.\d+)?)px\](?:\s|$)/.exec(footer.className)?.[1] ?? NaN);
  const room = width - 2 * border - 2 * pad;
  const chars = Math.floor(room / (font * 0.6));
  return { chars, why: `${width} - 2 x ${border} - 2 x ${pad} = ${room} px of ${font} px mono` };
}

function mountEighth(): void {
  act(() => root.render(null));
  act(() => { useStore.setState({ flows: { ...FLOWS_INIT, graph: { nodes: [EIGHTH], edges: [] } } } as any); });
  act(() => root.render(createElement(ClassicCard as any, { node: EIGHTH })));
}

// The premise the issue computed (#357): 188 - 2 - 20 = 166 px, 29
// characters of 9.5 px mono. MUTANT "classic footer padding grown"
// (FlowNodeCard.tsx footer `px-2.5` -> `px-3`), first run in S5-LOOP-mut and
// re-run in S7-UCANVAS-mut once the card fitted its line. Observed,
// cardFooterDom.test 20/26 (this case, the next, and the four ruling-9 cases
// whose fitted line is 29 characters, one over the grown card's 28):
//   x [classic] the budget is 29 characters, computed from the card's width, border, padding and type: the classic footer's budget (188 - 2 x 1 - 2 x 12 = 162 px of 9.5 px mono)
//     expected 29
//     got      28
//   x [classic] ruling 9: NGC 7331 rotating, 3x2 at Rotate to PA 30, 25%: the classic footer: 29 characters, over the card's 28
test("[classic] the budget is 29 characters, computed from the card's width, border, padding and type", () => {
  mountEighth();
  const b = classicBudget();
  eq(b.chars, 29, `the classic footer's budget (${b.why})`);
});

// THE CARD FITS TO THE BUDGET IT HAS. `CLASSIC_FOOTER_CHARS` is spelled from
// numbers beside the classes (Tailwind builds only a class written out), so
// a class changed alone would leave the card fitting lines to a room it no
// longer has. Under the same mutant (S7-UCANVAS-mut, 20/26):
//   x [classic] the card fits its footer to the budget the mounted card has: CLASSIC_FOOTER_CHARS against the mounted card (188 - 2 x 1 - 2 x 12 = 162 px of 9.5 px mono)
//     expected 28
//     got      29
test("[classic] the card fits its footer to the budget the mounted card has", () => {
  mountEighth();
  const b = classicBudget();
  eq(CLASSIC_FOOTER_CHARS, b.chars, `CLASSIC_FOOTER_CHARS against the mounted card (${b.why})`);
});

// ================================================ ruling 9's cases
//
// Each TARGET mounted on both cards over the fixture's lane, looped or not.
// The expected lines are written out by hand. For the classic card each is
// also held to the rule: no longer than the budget read off the mounted card,
// the loop word and the angle words whole, and the whole line as the tooltip
// exactly when something was left out.

/** A name of exactly 30 characters. */
const NAME30 = "M31 Andromeda Galaxy North Arm";

interface FitCase {
  what: string;
  params: Record<string, string | number>;
  /** The lane's loop wire is drawn. */
  looped: boolean;
  classic: string;
  next: string;
  /** Words the classic line must hold whole. */
  whole: string[];
}
const FIT_CASES: FitCase[] = [
  // #357's evidence, 34 characters: the overlap goes and the rest fits.
  { what: "#357's probe, M31 2x2 at Rotate to PA 55, 25%",
    params: { name: "M31", rows: 2, cols: 2, overlap: 25, angle: "Rotate to PA", rotation: 55 },
    looped: true,
    classic: "M31 · rotate · 2x2 · PA 55.0",
    next: "M31 · rotate · 2x2 · PA 55.0 · 25%",
    whole: ["M31", "rotate", "PA 55.0"] },
  { what: "the eighth Example, M31 3x2 at Rotate to PA 55, 25%",
    params: { ...EIGHTH.params }, looped: true,
    classic: "M31 · rotate · 3x2 · PA 55.0",
    next: "M31 · rotate · 3x2 · PA 55.0 · 25%",
    whole: ["M31", "rotate", "PA 55.0"] },
  // 39 characters; 33 without the overlap, so the name gives four: room
  // 29 - 22 - 3 = 4, three of its characters and the ellipsis.
  { what: "NGC 7331 rotating, 3x2 at Rotate to PA 30, 25%",
    params: { name: "NGC 7331", rows: 2, cols: 3, overlap: 25, angle: "Rotate to PA", rotation: 30 },
    looped: true,
    classic: "NGC… · rotate · 3x2 · PA 30.0",
    next: "NGC 7331 · rotate · 3x2 · PA 30.0 · 25%",
    whole: ["rotate", "PA 30.0"] },
  // 36 characters and no overlap; one character of room beside the grid, so
  // the grid goes and the name gets seven: 29 - 19 - 3.
  { what: "NGC 7331 panel-first, 3x2",
    params: { name: "NGC 7331", rows: 2, cols: 3, overlap: 25, angle: "Rotate to PA", rotation: 30 },
    looped: false,
    classic: "NGC 73… · one panel at a time",
    next: "NGC 7331 · one panel at a time · 3x2",
    whole: ["one panel at a time"] },
  // The panel-first line, 31 characters: the grid goes, the name stays whole.
  { what: "the panel-first line, the eighth Example without its loop",
    params: { ...EIGHTH.params }, looped: false,
    classic: "M31 · one panel at a time",
    next: "M31 · one panel at a time · 3x2",
    whole: ["M31", "one panel at a time"] },
  // 42 characters: the name gives 17, sixteen and the ellipsis.
  { what: "a 30-character name, a single target at any angle",
    params: { name: NAME30, rows: 1, cols: 1, angle: "Any angle", rotation: -1 },
    looped: false,
    classic: "M31 Andromeda Ga… · any angle",
    next: `${NAME30} · any angle`,
    whole: ["any angle"] },
  // The mode is a word of the angle: "fixed PA 30.0" is never cut. Past the
  // overlap there is no room for a character of the name beside the grid
  // (29 - 28 - 3 < 2), so the grid goes and the name gets four.
  { what: "a 30-character name, a rotating mosaic with the camera fixed at PA 30",
    params: { name: NAME30, rows: 2, cols: 3, overlap: 25, angle: "Camera fixed at PA", rotation: 30 },
    looped: true,
    classic: "M31… · rotate · fixed PA 30.0",
    next: `${NAME30} · rotate · 3x2 · fixed PA 30.0 · 25%`,
    whole: ["rotate", "fixed PA 30.0"] },
  // CONTROL: the single-target line fits, and is drawn whole with no tooltip.
  { what: "the single-target line, NGC 7331 at any angle",
    params: { name: "NGC 7331", rows: 1, cols: 1, angle: "Any angle", rotation: -1 },
    looped: false,
    classic: "NGC 7331 · any angle",
    next: "NGC 7331 · any angle",
    whole: ["NGC 7331", "any angle"] },
];

test("precondition: the 30-character name is 30 characters", () => {
  eq(NAME30.length, 30, "NAME30's length");
});

/** The fitted case's TARGET on `which` card, over the fixture's lane. */
function drawn(which: Which, c: FitCase): { line: string; title: string | null; chars: number | null } {
  const t: FlowNodeRec = { ...T, params: { ...NODE_DEFS.target.params, ...c.params } };
  const nodes = [t, AF, CY];
  act(() => root.render(null));
  act(() => {
    useStore.setState({ flows: { ...FLOWS_INIT, graph: { nodes, edges: c.looped ? [...LANE, LOOP] : LANE } } } as any);
  });
  act(() => root.render(createElement(Fragment, null,
    nodes.map((n) => createElement(CARD[which] as any, { key: n.id, node: n })))));
  const el = cardOf("t")?.querySelector(SUMMARY[which]);
  return {
    line: el?.textContent ?? "",
    title: el?.getAttribute("title") ?? null,
    chars: which === "classic" ? classicBudget().chars : null,
  };
}

// MUTANT "name cut before the overlap" (targetSummary.ts fittedFooter: the
// name shortened first, against the line with its overlap, to one character
// and the ellipsis at least, and the overlap dropped only if the line is
// still over). Observed, cardFooterDom.test 16/26 (every classic line that is
// over the budget, and the classic per-card cases above):
//   x [classic] ruling 9: #357's probe, M31 2x2 at Rotate to PA 55, 25%: the classic footer: drew "M… · rotate · 2x2 · PA 55.0", not "M31 · rotate · 2x2 · PA 55.0"; "M31" is cut
//   x [classic] ruling 9: NGC 7331 rotating, 3x2 at Rotate to PA 30, 25%: the classic footer: drew "N… · rotate · 3x2 · PA 30.0", not "NGC… · rotate · 3x2 · PA 30.0"
//   x [classic] ruling 9: the panel-first line, the eighth Example without its loop: the classic footer: drew "M… · one panel at a time", not "M31 · one panel at a time"; "M31" is cut
// (Swapping the two rungs alone is not a mutant: with its overlap kept, the
// rest of a rotating line is 26 characters at least, "rotate · 2x2 · PA 0.0
// · 0%", which leaves 29 - 26 - 3 = 0 for the name, so the swapped order
// draws every line the same. Run as "rungs swapped" in S7-UCANVAS-mut:
// cardFooterDom.test 26/26.)
//
// MUTANT "angle cut" (fittedFooter: past the overlap, the line cut at its
// end with the ellipsis, as `truncate` cut it, instead of the name
// shortened). Observed, cardFooterDom.test 18/26:
//   x [classic] ruling 9: NGC 7331 rotating, 3x2 at Rotate to PA 30, 25%: the classic footer: drew "NGC 7331 · rotate · 3x2 · PA…", not "NGC… · rotate · 3x2 · PA 30.0"; "PA 30.0" is cut
//   x [classic] ruling 9: NGC 7331 panel-first, 3x2: the classic footer: drew "NGC 7331 · one panel at a ti…", not "NGC 73… · one panel at a time"; "one panel at a time" is cut
//   x [classic] ruling 9: a 30-character name, a single target at any angle: the classic footer: drew "M31 Andromeda Galaxy North A…", not "M31 Andromeda Ga… · any angle"; "any angle" is cut
//   x [classic] ruling 9: a 30-character name, a rotating mosaic with the camera fixed at PA 30: the classic footer: drew "M31 Andromeda Galaxy North A…", not "M31… · rotate · fixed PA 30.0"; "rotate" is cut; "fixed PA 30.0" is cut
//
// MUTANT "the name goes to the ellipsis before the grid" (targetSummary.ts
// nameFitted: `room >= 1` and an empty `kept` allowed, so a name with one
// character of room is the ellipsis alone, as the ruling read literally
// gives). Observed, cardFooterDom.test 21/26:
//   x [classic] ruling 9: NGC 7331 panel-first, 3x2: the classic footer: drew "… · one panel at a time · 3x2", not "NGC 73… · one panel at a time"
//   x [classic] ruling 9: the panel-first line, the eighth Example without its loop: the classic footer: drew "… · one panel at a time · 3x2", not "M31 · one panel at a time"; "M31" is cut
for (const c of FIT_CASES) {
  test(`[classic] ruling 9: ${c.what}`, () => {
    const { line, title, chars } = drawn("classic", c);
    const bad: string[] = [];
    if (line !== c.classic) bad.push(`drew "${line}", not "${c.classic}"`);
    if (chars !== null && line.length > chars) bad.push(`${line.length} characters, over the card's ${chars}`);
    for (const w of c.whole) if (!line.includes(w)) bad.push(`"${w}" is cut`);
    const wantTitle = line === c.next ? null : c.next;
    if (title !== wantTitle) bad.push(`its tooltip is ${JSON.stringify(title)}, not ${JSON.stringify(wantTitle)}`);
    ok(bad.length === 0, `the classic footer: ${bad.join("; ")}`);
  });
}

// #/next keeps wrapping: its footer is the whole line for every case. MUTANT
// "next card fits too" (FlowNode.tsx draws `fittedFooter(node, loops,
// 29).line`). Observed, cardFooterDom.test 21/26 (this case and the [next]
// per-card cases above):
//   x [next] ruling 9: the #/next card keeps the whole line and wraps: #357's probe, M31 2x2 at Rotate to PA 55, 25%: drew "M31 · rotate · 2x2 · PA 55.0", not "M31 · rotate · 2x2 · PA 55.0 · 25%"; the eighth Example, M31 3x2 at Rotate to PA 55, 25%: drew "M31 · rotate · 3x2 · PA 55.0", not "M31 · rotate · 3x2 · PA 55.0 · 25%"; NGC 7331 rotating, 3x2 at Rotate to PA 30, 25%: drew "NGC… · rotate · 3x2 · PA 30.0", not "NGC 7331 · rotate · 3x2 · PA 30.0 · 25%"; ...
test("[next] ruling 9: the #/next card keeps the whole line and wraps", () => {
  const bad = FIT_CASES.flatMap((c) => {
    const { line } = drawn("next", c);
    return line === c.next ? [] : [`${c.what}: drew "${line}", not "${c.next}"`];
  });
  ok(bad.length === 0, bad.join("; "));
});

// ======================================================= a stranded loop
//
// #410. TARGET -> AUTOFOCUS -> FILTER CYCLE -> CAPTURE, and the loop wire
// still leaving the cycle: a CAPTURE appended before the carry reached it, the
// shape a flow saved before S4 opens in (spec 1.5 item 6). compile.py calls it
// M12 and `/run` refuses it. LOOP PANELS was offered and its press added a
// second wire from the CAPTURE, leaving the stranded one, after which the card
// read "rotate" over a flow that still would not run.
//
// MUTANT "withLoop adds a second wire" (panelLane.ts withLoop's `true` branch
// as S4 built it), run in the private scratch copy scratchpad/S5-LOOP-mut.
// Observed, cardFooterDom.test 14/16 (the id of the second wire is minted by
// the store, so it differs run to run):
//   x [classic] LOOP PANELS on a stranded loop moves the wire to the tail, and
//     only then does the card say rotate: the classic press: the pass wires
//     into next
//     expected "loop:cap"
//     got      "loop:cy,e1_mul7zjwg:cap"
//   x [next] ... the next press: the pass wires into next
//     expected "loop:cap"
//     got      "loop:cy,e2_mul7zjx2:cap"
// "targetLoops ignores M12" (targetSummary.ts) leaves this case green (16/16):
// the stranded lane has no tail wire before the press, so `loopWires` alone
// already says no rotation, and after the press there is no stale wire left.
// The shape that mutant misreads, a tail wire beside a stale one, is held by
// panelLane.test.ts (the fixture's `rotates`) and targetSummary.test.ts.
const CAP: FlowNodeRec = { id: "cap", type: "capture", x: 720, y: 0, params: { ...NODE_DEFS.capture.params } };
for (const which of ["classic", "next"] as const) {
  await testAsync(`[${which}] LOOP PANELS on a stranded loop moves the wire to the tail, and only then does the card say rotate`, async () => {
    const loopBtn = which === "classic" ? "[data-flows-loop]" : "[data-testid='flow-node-loop']";
    const stranded = [...LANE, E("c", "cy", "complete", "cap", "run"), LOOP];
    act(() => root.render(null));
    act(() => {
      useStore.setState({ flows: { ...FLOWS_INIT, graph: { nodes: [...NODES, CAP], edges: stranded } } } as any);
    });
    act(() => root.render(createElement(Fragment, null,
      [...NODES, CAP].map((n) => createElement(CARD[which] as any, { key: n.id, node: n })))));
    eq(footerOf(which, "t"), PANEL_FIRST[which], `the ${which} card over a stranded loop (M12)`);
    const btn = cardOf("t")?.querySelector(loopBtn);
    ok(btn, `the ${which} card offers no LOOP PANELS on a stranded loop`);
    await act(async () => { btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
    const pass = useStore.getState().flows.graph.edges
      .filter((e: FlowEdgeRec) => e.fromPort === "pass" && e.to === "t" && e.toPort === "next")
      .map((e: FlowEdgeRec) => `${e.id}:${e.from}`);
    eq(pass.join(","), "loop:cap", `the ${which} press: the pass wires into next`);
    eq(footerOf(which, "t"), ROTATING[which], `the ${which} card once the loop is moved`);
    eq(cardOf("t")?.querySelector(loopBtn) ?? null, null, `the ${which} card still offers LOOP PANELS`);
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
