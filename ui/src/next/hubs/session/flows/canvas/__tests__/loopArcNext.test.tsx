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
// ONE PROMISE IS THIS CANVAS'S OWN (#357). Its card grows past the formula box
// the arc routes under - the footer wraps, a pill joins the marks row, the
// selected card shows its 44 px EDIT STAGE - so its layer lowers the run by
// how far a card can reach (`CARD_OVERHANG_PX`, FlowNode.tsx). The card's
// height is computed below from the MOUNTED card's classes and the
// stylesheets that size them, never typed in, and the run is held to it.
//
// DELIBERATE PIN CHANGES (H4-UCANVAS, #357). Three pins moved with the run,
// each recorded where it stands: the arc's path, the chip's place and the
// remove control's place. Before this change they were the canvas's run at
// y 231 (the FILTER CYCLE's formula bottom 203 + 28); they are now y 332, 101
// px lower, 101 being the card's overhang the classes give. Unchanged, this
// file went 7/10 against the lowered run:
//   x the eighth Example's loop wire is drawn as the back-arc: the loop wire's path
//     expected M1178 167 L1192 167 Q1202 167, 1202 177 L1202 221 Q1202 231, 1192 231 L256 231 Q246 231, 246 221 L246 137 Q246 127, 256 127 L270 127
//     got      M1178 167 L1192 167 Q1202 167, 1202 177 L1202 322 Q1202 332, 1192 332 L256 332 Q246 332, 246 322 L246 137 Q246 127, 256 127 L270 127
//   x the chip sits in the middle of the run, below the cards: the chip's place
//     expected translate(724,231)
//     got      translate(724,332)
//   x the selected loop wire's remove control sits on the arc, clear of every card: the control's place
//     expected 1202,199
//     got      1202,249.5
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
const { edgePath, loopArc, nodeLayoutHeight, portPos, LOOP_ARC_DROP } =
  await import("../../../../../../components/flows/geometry");
const { panelLane } = await import("../../../../../../components/flows/panelLane");
const { LOOP_ARC_DASH, loopArcOf, loopChipBox } =
  await import("../../../../../../components/flows/targetSummary");
const { FlowWireLayer, FlowWireDelete } = await import("../FlowWires");
const { FlowNodeCard } = await import("../FlowNode");
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

const container = win.document.getElementById("root");
const root = createRoot(container);

// ======================================== the #/next card, as drawn (#357)
//
// The card's height is not measured (jsdom lays nothing out, and the arc may
// not read the DOM anyway): it is COMPUTED here from the mounted card's own
// elements and the stylesheet rules their classes name - canvas.css for the
// card, next.css for `Mono`, `Pill` and `ActionButton`, and Tailwind's
// preflight, which makes every box border-box and sets the line-height the
// footer's text inherits. Every element the card draws must be one this
// reader knows how to size, so a card that grows a new row fails here rather
// than leaving the run held to a card nobody draws. IBM Plex Mono advances
// 0.6 em a character, as the classic budget reads it (cardFooterDom.test.tsx).
// The reader was checked once against a real browser (H4-UCANVAS: a scratch
// harness mounting this card with the built stylesheets, Chromium at 1440 x
// 900): the budget card measured 224 px, its footer line wrapping after
// "PA", and an idle FILTER CYCLE 153 (143 + 10), the numbers the classes give.
const { readFileSync } = await import("node:fs");
const { createRequire } = await import("node:module");
const uncomment = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, "");
const STYLES = uncomment(["../canvas.css", "../../../../../next.css"]
  .map((rel) => readFileSync(new URL(rel, import.meta.url), "utf8")).join("\n"));
const PREFLIGHT = uncomment(readFileSync(
  createRequire(import.meta.url).resolve("tailwindcss/preflight.css"), "utf8"));

/** The declarations of the first rule whose selector is exactly `sel`. */
function rule(css: string, sel: string): Record<string, string> {
  const esc = sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`(?:^|[\\s}])${esc}\\s*\\{([^}]*)\\}`).exec(css);
  ok(m, `no rule for ${sel}`);
  const out: Record<string, string> = {};
  for (const decl of m![1].split(";")) {
    const i = decl.indexOf(":");
    if (i > 0) out[decl.slice(0, i).trim()] = decl.slice(i + 1).trim();
  }
  return out;
}
function px(v: string | undefined, what: string): number {
  const m = /^(\d*\.?\d+)px$/.exec(v ?? "");
  ok(m, `${what} is not a px length: ${v}`);
  return Number(m![1]);
}
/** A `padding` shorthand as [top, right, bottom, left]. */
function sides(v: string | undefined, what: string): number[] {
  const p = (v ?? "").split(/\s+/).map((t) => (t === "0" ? 0 : px(t, what)));
  ok(p.length >= 1 && p.length <= 4, `${what}: ${v}`);
  return [p[0], p[1] ?? p[0], p[2] ?? p[0], p[3] ?? p[1] ?? p[0]];
}
/** Lines a greedy word wrap makes of `text` at `chars` a line. */
function wrapped(text: string, chars: number): number {
  let lines = 1;
  let len = 0;
  for (const w of text.split(" ")) {
    const need = len === 0 ? w.length : len + 1 + w.length;
    if (need <= chars || len === 0) len = need;
    else { lines++; len = w.length; }
  }
  return lines;
}

/** The mounted card's height, from its classes, with the working. */
function drawnHeight(card: any): { h: number; why: string; lines: number } {
  const lineH = Number(/html,\s*:host\s*\{[^}]*line-height:\s*([\d.]+)/.exec(PREFLIGHT)?.[1]);
  ok(lineH > 0, "precondition: the preflight's html line-height");
  ok(/box-sizing:\s*border-box/.test(PREFLIGHT), "precondition: the preflight's border-box");
  const border = px(rule(STYLES, ".nx-flow-node").border?.split(/\s+/)[0], ".nx-flow-node border");
  const kids = [...card.children] as any[];
  eq(kids.map((k) => k.className).join(" | "),
    "nx-flow-node-head | nx-flow-node-ports | nx-flow-node-foot", "the card's three blocks");
  const [, ports, foot] = kids;
  const headH = px(rule(STYLES, ".nx-flow-node-head").height, ".nx-flow-node-head height");
  const portPad = sides(rule(STYLES, ".nx-flow-node-ports").padding, ".nx-flow-node-ports padding");
  const rows = ([...ports.children] as any[]).map((r) => px(r.style.height, "a port row's height"));
  const portsH = portPad[0] + portPad[2] + rows.reduce((a, b) => a + b, 0);
  const footRule = rule(STYLES, ".nx-flow-node-foot");
  const footPad = sides(footRule.padding, ".nx-flow-node-foot padding");
  const gap = px(footRule.gap, ".nx-flow-node-foot gap");
  const room = parseFloat(card.style.width) - 2 * border - footPad[1] - footPad[3];
  let lines = 0;
  const parts = ([...foot.children] as any[]).map((el): [string, number] => {
    const cls = el.classList;
    if (cls.contains("nx-mono")) {
      const font = parseFloat(el.style.fontSize);
      const n = wrapped(el.textContent, Math.floor(room / (font * 0.6)));
      lines += n;
      return [`${n} x ${font * lineH} (mono)`, n * font * lineH];
    }
    if (cls.contains("nx-flow-node-marks")) {
      const pills = [...el.children] as any[];
      if (pills.length === 0) return ["0 (empty marks row)", 0];
      ok(pills.length === 1 && pills[0].classList.contains("nx-pill"),
        `this reader lays out one .nx-pill in the marks row, not: ${pills.map((x) => x.className).join(", ")}`);
      const pill = px(rule(STYLES, ".nx-pill").height, ".nx-pill height");
      return [`${pill} (pill)`, pill];
    }
    if (cls.contains("nx-btn")) {
      eq(el.getAttribute("data-size"), "md", "the footer's button size");
      const button = px(rule(STYLES, ".nx-btn").height, ".nx-btn height");
      return [`${button} (button)`, button];
    }
    throw new Error(`the card's footer draws an element this reader cannot size: ${String(el.outerHTML).slice(0, 120)}`);
  });
  const footH = footPad[0] + footPad[2] + parts.reduce((a, [, h]) => a + h, 0) + gap * (parts.length - 1);
  const h = 2 * border + headH + portsH + footH;
  const why = `2 x ${border} + ${headH} + (${portPad[0]} + ${rows.join(" + ")} + ${portPad[2]}) + `
    + `(${footPad[0]} + ${parts.map(([w]) => w).join(" + ")} + ${parts.length - 1} x ${gap} + ${footPad[2]})`;
  return { h, why, lines };
}

/** The #/next TARGET card at the budget `CARD_OVERHANG_PX` names: its footer
 *  wrapped (the eighth Example's line is 34 characters), a pill in its marks
 *  row (the progress chip) and EDIT STAGE (it is selected). */
function mountWorstCard(): any {
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: {
        ...FLOWS_INIT, graph: GRAPH, sel: { kind: "node", id: "n2" },
        progress: { session: "s1", blocks: [{ node_id: "n2", kind: "target", banked: 212, total: 315 }] },
      },
    } as any);
  });
  act(() => root.render(createElement(FlowNodeCard, { node: nodeOf("n2") })));
  const card = container.querySelector('[data-testid="flow-node"][data-node-id="n2"]');
  ok(card, "the #/next TARGET card did not mount");
  return card;
}

let overhang: { px: number; why: string } | null = null;
/** How far the #/next card as drawn at the budget reaches below its formula
 *  box: the class-computed height less `nodeLayoutHeight`. Computed once. */
function cardOverhang(): { px: number; why: string } {
  if (overhang) return overhang;
  const card = mountWorstCard();
  const d = drawnHeight(card);
  eq(d.lines, 2, "precondition: the eighth Example's footer wraps to two lines, the budget's");
  ok(card.querySelector('[data-testid="flow-node-progress"]'), "precondition: the pill");
  ok(card.querySelector('[data-testid="flow-node-edit-cta"]'), "precondition: EDIT STAGE");
  const formula = nodeLayoutHeight(nodeOf("n2"), NODE_DEFS);
  overhang = { px: d.h - formula, why: `${d.why} = ${d.h}, less the formula's ${formula}` };
  act(() => root.render(null));
  return overhang;
}

/** The arc this canvas should draw: `loopArc` over the Example's lane with
 *  the run `LOOP_ARC_DROP` below the card AS DRAWN, the drop taken from the
 *  class-computed overhang above, not from the code's constant. */
function expectedArc(): NonNullable<ReturnType<typeof loopArc>> {
  return loopArc(nodeOf("n7"), "pass", nodeOf("n2"), "next",
    panelLane({ nodes: NODES, edges: EDGES }, "n2"), NODE_DEFS, "tablet",
    { drop: LOOP_ARC_DROP + cardOverhang().px })!;
}

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
  const want = expectedArc();
  mount();
  eq(visible("loop").getAttribute("d"), want.d, "the loop wire's path");
  ok(visible("loop").closest('[data-testid="flow-loop-arc"]'), "the loop wire is not marked as the loop arc");
  eq(container.querySelector('path[data-wire][data-edge-id="loop"]').getAttribute("d"),
    want.d, "the hit band follows the arc, or a click on the drawn wire selects nothing");
});

// ======================================== the run clears the card as drawn

// #357. The run lies `LOOP_ARC_DROP` below the lowest of the body cards'
// boxes AS DRAWN at the budget: each card's formula box grown by the overhang
// computed above from the mounted card's classes (2 x 1 + 32 + (5 + 60 + 2)
// + (3 + 2 x 15 + 26 + 44 + 2 x 6 + 8) = 224 for the TARGET, less the
// formula's 123: 101). On the eighth Example the FILTER CYCLE is lowest, its
// formula bottom 60 + 143 = 203, so the card as drawn reaches 304 and the run
// is 332; the chip, 14 px tall, is centred on it.
//
// MUTANT "#/next drop back to LOOP_ARC_DROP (28)" (FlowWires.tsx
// `nextLoopArc`: `return loop && lowerLoopRun(loop, CARD_OVERHANG_PX);` made
// `return loop;`), run in the private copy scratchpad/H4-UCANVAS-mut.
// Observed, loopArcNext.test 7/12 (this case, and the path, chip, control and
// CONTROL cases, the last "the run did not move"):
//   x the run clears the #/next card's worst-case box: the run at y 231 (its
//     chip from y 224) is behind n7, which as drawn reaches y 304 (overhang
//     101: 2 x 1 + 32 + (5 + 20 + 20 + 20 + 2) + (3 + 2 x 15 (mono) + 26
//     (pill) + 44 (button) + 2 x 6 + 8) = 224, less the formula's 123)
// MUTANT "the budget one footer line short" (FlowNode.tsx `FOOTER_LINES = 2`
// made `1`, so the constant no longer matches the card its classes draw: the
// run still clears the card, by 13 px, and the pin is what sees it).
// Observed, loopArcNext.test 8/12 (this case, and the path, chip and control
// cases, at 317 and 242):
//   x the run clears the #/next card's worst-case box: the run 28 px under the
//     card as drawn (overhang 101: 2 x 1 + 32 + (5 + 20 + 20 + 20 + 2) + (3 +
//     2 x 15 (mono) + 26 (pill) + 44 (button) + 2 x 6 + 8) = 224, less the
//     formula's 123)
//     expected 332
//     got      317
test("the run clears the #/next card's worst-case box", () => {
  const o = cardOverhang();
  mount();
  const d = visible("loop").getAttribute("d") ?? "";
  // The run: the one straight horizontal stretch of the path, between the
  // two lower corners.
  const runs = [...d.matchAll(/Q(-?[\d.]+) (-?[\d.]+), (-?[\d.]+) (-?[\d.]+) L(-?[\d.]+) (-?[\d.]+)/g)]
    .filter((m) => m[4] === m[6] && m[3] !== m[5]).map((m) => Number(m[4]));
  const runY = Math.max(...runs);
  ok(Number.isFinite(runY), `the arc has no run: ${d}`);
  const body = ["n2", ...panelLane(GRAPH, "n2").map((n) => n.id)];
  eq(body.join(","), "n2,n5,n6,n7", "precondition: the body is the TARGET, AF, GUIDE and the CYCLE");
  const drawn = body.map((id) => {
    const n = nodeOf(id);
    return { id, bottom: n.y + nodeLayoutHeight(n, NODE_DEFS) + o.px };
  });
  const lowest = drawn.reduce((a, b) => (b.bottom > a.bottom ? b : a));
  const chip = loopChipBox("every pass: next panel");
  ok(runY - chip.h / 2 > lowest.bottom,
    `the run at y ${runY} (its chip from y ${runY - chip.h / 2}) is behind ${lowest.id}, `
    + `which as drawn reaches y ${lowest.bottom} (overhang ${o.px}: ${o.why})`);
  eq(runY, lowest.bottom + LOOP_ARC_DROP,
    `the run ${LOOP_ARC_DROP} px under the card as drawn (overhang ${o.px}: ${o.why})`);
});

// The #/next arc is `loopArcOf`'s with only its run lowered: the resolver
// that decides the body and the legs is the classic layer's, so the two
// canvases cannot route the loop apart. The CONTROL for the mutants above:
// everything but the run and the ends of the corners beside it is what the
// canvas draws.
test("CONTROL: the #/next arc keeps loopArcOf's anchors and legs; only the run moves", () => {
  mount();
  const canvas = loopArcOf(edgeOf("loop"), GRAPH, "tablet")!;
  const nums = (s: string): number[] => (s.match(/-?\d*\.?\d+/g) ?? []).map(Number);
  const got = nums(visible("loop").getAttribute("d") ?? "");
  const was = nums(canvas.d);
  eq(got.length, was.length, "the same commands");
  const moved = got.flatMap((v, i) => (v === was[i] ? [] : [was[i]]));
  ok(moved.length > 0, "the run did not move");
  ok(moved.every((y) => y === canvas.runY || y === canvas.runY - 10),
    `only the run's height and its corners' tops moved: from ${moved.join(", ")}`);
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
// DELIBERATE PIN CHANGE (H4-UCANVAS, #357): the run is now 101 px lower, the
// card's overhang as drawn (see "the run clears the #/next card's worst-case
// box"), so 203 + 101 + 28 = 332; x is unchanged. Re-run in
// scratchpad/H4-UCANVAS-mut, the same mutant observed, 11/12:
//   x the chip sits in the middle of the run, below the cards: the chip's place
//     expected translate(724,332)
//     got      translate(1202,249.5)
test("the chip sits in the middle of the run, below the cards", () => {
  mount();
  const chip = container.querySelector('[data-testid="flow-loop-chip"]');
  ok(chip, "no chip on the Example's loop wire");
  eq(chip.getAttribute("transform"), "translate(724,332)", "the chip's place");
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
// DELIBERATE PIN CHANGE (H4-UCANVAS, #357): the handle is the middle of the
// drop of the arc drawn here, which now runs from the pass port at y 167 down
// to the lowered run at 332, so (1202, 249.5); and the box is held clear of
// every card as drawn at the budget as well as of its formula box. Re-run in
// scratchpad/H4-UCANVAS-mut, the same mutant observed, 11/12:
//   x the selected loop wire's remove control sits on the arc, clear of every card: the control's place
//     expected 1202,249.5
//     got      724,147
// And MUTANT "#/next control on the canvas's arc" (`FlowWireDelete` in
// FlowWires.tsx asks `loopArcOf(edge, { nodes, edges }, tier)` again while
// the layer draws the lowered arc), observed, 11/12:
//   x the selected loop wire's remove control sits on the arc, clear of every card: the control's place
//     expected 1202,249.5
//     got      1202,199
test("the selected loop wire's remove control sits on the arc, clear of every card", () => {
  const want = expectedArc();
  const o = cardOverhang();
  mount({ sel: { kind: "edge", id: "loop" } }, "loop");
  const cut = container.querySelector('[data-testid="flow-wire-delete"]');
  ok(cut, "no remove control for the selected loop wire");
  const m = /translate3d\(([-\d.]+)px,([-\d.]+)px,0\)/.exec(cut.getAttribute("style") ?? "");
  ok(m, `the control carries no world position: ${cut.getAttribute("style")}`);
  eq(`${m![1]},${m![2]}`, `${want.handle.x},${want.handle.y}`, "the control's place");
  eq(`${m![1]},${m![2]}`, "1202,249.5", "the control's place, worked by hand");
  // 22 px across, centred: its box must not overlap any card's formula box,
  // nor that box grown to the card as drawn at the budget.
  const [cx, cy] = [Number(m![1]), Number(m![2])];
  for (const n of NODES) {
    const w = 188;
    const h = 37 + ((NODE_DEFS as any)[n.type].ins.length + (NODE_DEFS as any)[n.type].outs.length) * 20 + 26;
    for (const [what, hh] of [["formula", h], ["as drawn", h + o.px]] as const) {
      const clear = cx + 11 <= n.x || cx - 11 >= n.x + w || cy + 11 <= n.y || cy - 11 >= n.y + hh;
      ok(clear, `the remove control at (${cx}, ${cy}) covers ${n.type.toUpperCase()} ${n.id} (${what})`);
    }
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
