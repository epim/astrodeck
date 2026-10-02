// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w6CardOverhangStates.test.tsx - SESSION / FLOWS, the #/next card's three
// states that still reached past CARD_OVERHANG_PX (#554, backlog WP-48a
// fix (a): "The three loop-run card states fit CARD_OVERHANG_PX").
//
//   Run directly:  node --import tsx src/next/hubs/session/flows/canvas/__tests__/w6CardOverhangStates.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// #554 found three real card states that reach past the arc's drop even
// though `canvas/__tests__/loopArcNext.test.tsx` holds the run to
// `CARD_OVERHANG_PX`: that file's own reader "deliberately refuses a marks
// row with more than one pill" and assumes the footer wraps to exactly
// `FOOTER_LINES` - so none of the three states it pins ever reaches them.
// This file mounts each one directly and checks the DOM shape FlowNode.tsx
// now uses to stay inside the budget, rather than widening the budget to
// their worst case (the issue's other option, and a lower run on every
// graph the issue never needed one for).
//
// WHAT IS GUARDED
//
//   1. A chip beside a mark (two ~90 px pills in a 166 px row) draws as ONE
//      pill, carrying both facts, so the marks row never wraps to a second
//      line.
//   2. A selected TARGET that offers LOOP PANELS draws it beside EDIT STAGE
//      in one button row, not stacked in two.
//   3. A TARGET name long enough to wrap the footer past two lines is drawn
//      WHOLE in the DOM (cardFooterDom.test.tsx's "the #/next card keeps the
//      whole line and wraps" is a different file's pin this one must not
//      fight) and clamped to `FOOTER_LINES` only in what is PAINTED, via the
//      same CSS line-clamp FlowLibraryCard.tsx's tagline already uses.
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
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { FLOWS_INIT } = await import("../../../../../../components/flows/flowsSlice");
const { NODE_DEFS } = await import("../../../../../../components/flows/nodeDefs");
const { targetFooter } = await import("../../../../../../components/flows/targetSummary");
const { MARK_RIG } = await import("../canvasModel");
const { FlowNodeCard, CARD_OVERHANG_PX, FOOTER_LINES } = await import("../FlowNode");
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

const container = win.document.getElementById("root");
const root = createRoot(container);

/** Mounts one card, fresh, with the given flows-slice overrides. */
function mount(node: FlowNodeRec, flows: Record<string, unknown>): any {
  act(() => root.render(null));
  act(() => {
    useStore.setState({ flows: { ...FLOWS_INIT, ...flows } } as any);
  });
  act(() => root.render(createElement(FlowNodeCard, { node })));
  const card = container.querySelector(`[data-testid="flow-node"][data-node-id="${node.id}"]`);
  ok(card, `the card for ${node.id} did not mount`);
  return card;
}

// ======================================= state 1: a chip beside a mark

// MUTANT "state 1 merge disabled" (FlowNode.tsx `{chip && mark ? (` made
// `{chip && false ? (`, so the chip-and-mark branch never fires and the row
// falls back to drawing both pills separately). Run from a byte backup in
// this worktree, restored byte-identical after (sha256 matched, the mutant
// text grepped gone). Observed, w6CardOverhangStates.test 6/7:
//   x a chip beside a mark draws as one pill, not two: the marks row drew 2
//     pills (a second wraps the row to a second line, past CARD_OVERHANG_PX):
//     5/10 subs | FROM THE RIG
//     expected 1
//     got      2
test("a chip beside a mark draws as one pill, not two", () => {
  const node: FlowNodeRec = {
    id: "t1", type: "target", x: 0, y: 0,
    params: { ...NODE_DEFS.target.params, name: "M31" },
  };
  const card = mount(node, {
    graph: { nodes: [node], edges: [] },
    progress: { session: "s1", blocks: [{ node_id: "t1", kind: "target", banked: 5, total: 10 }] },
    compiled: {
      plan: {}, structural: [], issues: [],
      unmapped: [{ key: "nodes.target", level: "note", detail: "this run takes these from the rig" }],
      from: { nodes: [node], edges: [] },
    },
  });
  const marks = card.querySelector(".nx-flow-node-marks");
  ok(marks, "no marks row on the card");
  const pills = [...marks.querySelectorAll(".nx-pill")];
  eq(pills.length, 1,
    `the marks row drew ${pills.length} pills (a second wraps the row to a second line, past `
    + `CARD_OVERHANG_PX): ${pills.map((p: any) => p.textContent).join(" | ")}`);
  ok(String(pills[0].textContent).includes("5/10 subs"), "the chip's count is missing from the combined pill");
  ok(String(pills[0].textContent).includes(MARK_RIG), "the mark's word is missing from the combined pill");
  eq(card.querySelectorAll('[data-testid="flow-node-mark"]').length, 0,
    "a separate flow-node-mark pill means the row drew two, not one");
});

test("CONTROL: a chip with no mark is still its own pill", () => {
  const node: FlowNodeRec = {
    id: "t1b", type: "target", x: 0, y: 0,
    params: { ...NODE_DEFS.target.params, name: "M31" },
  };
  const card = mount(node, {
    graph: { nodes: [node], edges: [] },
    progress: { session: "s1", blocks: [{ node_id: "t1b", kind: "target", banked: 5, total: 10 }] },
  });
  const pills = [...card.querySelector(".nx-flow-node-marks").querySelectorAll(".nx-pill")];
  eq(pills.length, 1, "precondition: one pill with no mark");
  eq(String(pills[0].textContent), "5/10 subs", "the lone chip's own text changed");
});

// =============================== state 2: LOOP PANELS beside EDIT STAGE

// A multi-panel TARGET with a lane and no loop wire yet, so `offersLoopPanels`
// is true - the same shape `offersLoopPanels`'s own module comment describes,
// and `loopArcNext.test.tsx`'s eighth Example lane with its "loop" edge left
// out, which is the one thing that makes the button offer itself.
function laneGraph(): { nodes: FlowNodeRec[]; edges: FlowEdgeRec[] } {
  const nodes: FlowNodeRec[] = [
    {
      id: "n2", type: "target", x: 270, y: 60,
      params: {
        ...NODE_DEFS.target.params, name: "M31", rows: 2, cols: 3,
        angle: "Rotate to PA", rotation: 55,
      },
    },
    { id: "n5", type: "autofocus", x: 510, y: 60, params: { ...NODE_DEFS.autofocus.params } },
    { id: "n6", type: "guide", x: 750, y: 60, params: { ...NODE_DEFS.guide.params } },
    { id: "n7", type: "cycle", x: 990, y: 60, params: { ...NODE_DEFS.cycle.params } },
  ];
  const edges: FlowEdgeRec[] = [
    { id: "e2", from: "n2", fromPort: "target", to: "n5", toPort: "run" },
    { id: "e3", from: "n5", fromPort: "focused", to: "n6", toPort: "run" },
    { id: "e4", from: "n6", fromPort: "guiding", to: "n7", toPort: "run" },
    // Deliberately no "pass -> next" loop wire: that absence is what makes
    // `offersLoopPanels(n2)` true (a press would add it).
  ];
  return { nodes, edges };
}

// MUTANT "state 2 fold disabled" (FlowNode.tsx `{offersLoop && selected ? (`
// made `{offersLoop && false ? (`, so the shared row never draws and the
// footer falls back to its two separate buttons). Run from a byte backup in
// this worktree, restored byte-identical after (sha256 matched, the mutant
// text grepped gone). Observed, w6CardOverhangStates.test 6/7:
//   x a selected TARGET offering LOOP PANELS shows it beside EDIT STAGE, in
//     one row: LOOP PANELS and EDIT STAGE are drawn as two separate 44 px
//     rows stacked in the footer (94 px with the gap), past what
//     CARD_OVERHANG_PX pays for (one 44 px row)
//     expected 0
//     got      2
test("a selected TARGET offering LOOP PANELS shows it beside EDIT STAGE, in one row", () => {
  const { nodes, edges } = laneGraph();
  const node = nodes[0];
  const card = mount(node, {
    graph: { nodes, edges },
    sel: { kind: "node", id: "n2" },
  });
  const loopBtn = card.querySelector('[data-testid="flow-node-loop"]');
  const editBtn = card.querySelector('[data-testid="flow-node-edit-cta"]');
  ok(loopBtn, "precondition: LOOP PANELS did not offer itself on this lane");
  ok(editBtn, "precondition: EDIT STAGE did not show on the selected card");

  const foot = card.querySelector(".nx-flow-node-foot");
  const directButtons = [...foot.children].filter((c: any) => c.classList.contains("nx-btn"));
  eq(directButtons.length, 0,
    "LOOP PANELS and EDIT STAGE are drawn as two separate 44 px rows stacked in the footer "
    + "(94 px with the gap), past what CARD_OVERHANG_PX pays for (one 44 px row)");

  const row = loopBtn.closest('[data-testid="flow-node-actions-row"]');
  ok(row, "no shared row wraps LOOP PANELS when EDIT STAGE is also offered");
  eq(editBtn.closest('[data-testid="flow-node-actions-row"]'), row,
    "LOOP PANELS and EDIT STAGE are not sharing the same row");
  eq([...foot.children].filter((c: any) => c.getAttribute("data-testid") === "flow-node-actions-row").length, 1,
    "more than one action row reaches the foot");
});

test("CONTROL: EDIT STAGE alone is still a direct row of the foot", () => {
  const { nodes, edges } = laneGraph();
  // Add the loop wire so LOOP PANELS no longer offers itself; only
  // selection remains.
  edges.push({ id: "loop", from: "n7", fromPort: "pass", to: "n2", toPort: "next" });
  const node = nodes[0];
  const card = mount(node, {
    graph: { nodes, edges },
    sel: { kind: "node", id: "n2" },
  });
  ok(!card.querySelector('[data-testid="flow-node-loop"]'),
    "precondition: the loop wire already at the tail means no offer");
  const editBtn = card.querySelector('[data-testid="flow-node-edit-cta"]');
  ok(editBtn, "EDIT STAGE did not show on the selected card");
  eq(editBtn.parentElement, card.querySelector(".nx-flow-node-foot"),
    "EDIT STAGE alone moved out of the foot's direct children");
  ok(!editBtn.closest('[data-testid="flow-node-actions-row"]'), "EDIT STAGE alone was wrapped in an action row");
});

// ========================================= state 3: a name past two lines

// A name long enough that the eighth Example's own 27-characters-a-line
// budget (NODE_W_WIDE's 166 px room at 10 px mono) would wrap it to four
// lines or more - #554's own words for this state.
const LONG_NAME = "NGC 7000 North America Nebula In The Constellation Of Cygnus Deep Field Mosaic Field";

// MUTANT "state 3 clamp disabled" (FlowNode.tsx the summary span's
// `display: "-webkit-box"` made `display: "block"`, so the line-clamp
// properties below it do nothing and the footer paints however many lines
// the name needs). Run from a byte backup in this worktree, restored
// byte-identical after (sha256 matched, the mutant text grepped gone).
// Observed, w6CardOverhangStates.test 6/7:
//   x a TARGET name past two lines is drawn WHOLE and clamped to
//     FOOTER_LINES, not cut: the footer line is missing display: -webkit-box
//     expected -webkit-box
//     got      block
test("a TARGET name past two lines is drawn WHOLE and clamped to FOOTER_LINES, not cut", () => {
  const node: FlowNodeRec = {
    id: "t3", type: "target", x: 0, y: 0,
    params: { ...NODE_DEFS.target.params, name: LONG_NAME },
  };
  const full = targetFooter(node, false);

  const card = mount(node, { graph: { nodes: [node], edges: [] } });
  const summary = card.querySelector('[data-testid="flow-node-summary"]');
  ok(summary, "no footer line on the card");

  // THE DOM KEEPS THE WHOLE LINE. cardFooterDom.test.tsx's "[next] ruling 9:
  // the #/next card keeps the whole line and wraps" pins exactly this: unlike
  // the classic card's one-liner (S7 orchestrator ruling 9), #/next never
  // cuts a word out of a TARGET's footer. A fix for #554 that ellipsized the
  // name here would turn that file red.
  eq(String(summary.textContent), full,
    "the long name's footer was cut rather than clamped - this fights cardFooterDom.test.tsx's "
    + "own pin that #/next always keeps the whole line");

  // AND WHAT IS PAINTED IS BOUNDED. The line itself clamps to FOOTER_LINES,
  // the same CSS line-clamp FlowLibraryCard.tsx's tagline uses, so the card's
  // drawn height stays inside CARD_OVERHANG_PX regardless of how long a name
  // the operator types. On the element itself, not a wrapper -
  // loopArcNext.test.tsx's reader sizes `.nx-flow-node-foot`'s direct
  // children by class, and a wrapper div around the summary line is one it
  // does not know how to size.
  eq(summary.parentElement, card.querySelector(".nx-flow-node-foot"),
    "the footer line is no longer a direct child of the foot - loopArcNext.test.tsx's reader "
    + "cannot size whatever now wraps it");
  const s = summary.style as any;
  eq(s.display, "-webkit-box", "the footer line is missing display: -webkit-box");
  eq(String(s.WebkitLineClamp), String(FOOTER_LINES),
    "the footer line's clamp is not set to FOOTER_LINES");
  eq(s.overflow, "hidden", "the footer line does not hide the overflow it clamps");
});

test("CONTROL: a short TARGET name is unaffected by the clamp box", () => {
  const node: FlowNodeRec = {
    id: "t3b", type: "target", x: 0, y: 0,
    params: { ...NODE_DEFS.target.params, name: "M31" },
  };
  const card = mount(node, { graph: { nodes: [node], edges: [] } });
  const text = String(card.querySelector('[data-testid="flow-node-summary"]').textContent);
  eq(text, targetFooter(node, false), "a short name's footer changed when nothing should fit it");
});

// ---------------------------------------------------------------- precondition
// CARD_OVERHANG_PX itself is untouched by any of the three fixes above - they
// keep the card inside it rather than changing what it pays for.
test("precondition: CARD_OVERHANG_PX is a positive budget, unchanged by this fix", () => {
  ok(CARD_OVERHANG_PX > 0, `CARD_OVERHANG_PX is not positive: ${CARD_OVERHANG_PX}`);
  eq(FOOTER_LINES, 2, "the two-line footer budget moved");
});

// ----------------------------------------------------------------- report
act(() => root.render(null));
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw6CardOverhangStates.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
