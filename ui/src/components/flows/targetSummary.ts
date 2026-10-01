// targetSummary.ts - what the canvas says about a TARGET block: the card's
// footer line, and the dashed loop wire that makes it a rotating mosaic (#189
// S4 item 6; spec 2026-09-23 flows mosaic, 1.2 "Card footer" and 1.4 "How it
// is drawn"). Pure: no store, no React, no DOM.
//
// ONE MODULE FOR BOTH CANVASES. The classic card (FlowNodeCard.tsx) and the
// #/next card (canvas/FlowNode.tsx) draw the same footer, and the classic wire
// layer (FlowWireLayer.tsx) and the #/next one (canvas/FlowWires.tsx) draw the
// same arc and chip. #/next may not import a classic PRESENTATION module
// (canvasModel.ts's header says why: it would drag the legacy tree into the
// split bundle), so everything the four share lives here, beside the other
// pure modules both already import (nodeDefs, geometry, panelLane).
//
// THE LOOP IS READ FROM THE GRAPH. Whether a mosaic rotates is the loop wire,
// not a param: spec 1.4 makes the wire "the single source of truth for rotate
// panels every pass", and deleting it is how an operator turns rotation off.
// So the footer's "rotate" and the chip's words both come from `targetLoops`:
// `loopWires` (panelLane.ts, the mirror of compile.py `loop_wires`), which is
// exactly the wire the compile consumes as the entry's `loop`, and no M12
// beside it (`midLanePassWires`, #410) - the card cannot say "rotate" over a
// graph the run will shoot panel-first, or refuse to shoot at all.

import {
  loopArc, nodeLayoutHeight, type EdgeMode, type FlowTier, type LoopArc, type LoopArcOpts,
} from "./geometry";
import { AUTO_PAD, FLOW_LANE_GAP, layoutHeight } from "./autoLayout";
import { NODE_DEFS, TARGET_ANGLES, targetAngle } from "./nodeDefs";
import {
  isMultiPanel, loopWires, midLanePassWires, panelLane, NEXT_PORT, type LaneGraph,
} from "./panelLane";
import type { FlowEdgeRec, FlowNodeRec } from "./flowsTypes";

/** The separator every card footer already uses between facts (nodeDefs.ts's
 *  `sum` bodies: "clouds/rain/wind · fail closed", "±1.2′ · ASTAP"). The spec
 *  writes it as "." only because its text is ASCII. */
export const SUMMARY_SEP = " · ";

// ------------------------------------------------------------- reading params
/** A whole number as Python's `int()` reads a string (compile.py `_grid_dim`,
 *  mirrored in panelLane.ts, whose reader is private): "3.0" and "1e1" are not
 *  whole numbers there, so they read as 1 here too, and the card never names a
 *  grid the compile does not build. */
const PY_INT = /^\s*[+-]?\d+\s*$/;

function gridDim(v: string | number | undefined): number {
  const n = typeof v === "number" ? v
    : typeof v === "string" && PY_INT.test(v) ? Number(v.trim())
      : NaN;
  return Number.isInteger(n) && n >= 1 ? n : 1;
}

/** A decimal as Python's `float()` reads what an operator types (nodeDefs.ts
 *  reads `rotation` the same way). `Number("")` would be 0, which for a
 *  rotation is north up and for an overlap is no overlap at all. */
const DECIMAL = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;

function decimal(v: string | number | undefined): number {
  if (typeof v === "number") return v;
  const s = String(v ?? "").trim();
  return DECIMAL.test(s) ? Number(s) : NaN;
}

/** The block's grid, rows and columns, as the compile reads it. */
export function targetGrid(node: FlowNodeRec): { rows: number; cols: number } {
  const p = node.params ?? {};
  return { rows: gridDim(p.rows), cols: gridDim(p.cols) };
}

/** The angle as the compile reads it (compile.py `angle_code` over
 *  `target_angle`): a stored choice this build offers, otherwise what the
 *  rotation alone says. A newer build's fourth word is not guessed at.
 *
 *  TRIMMED, because `angle_code` strips the stored word before looking it up:
 *  a hand-edited " Camera fixed at PA " is a fixed camera to the run, and read
 *  untrimmed it would fall through to the rotation and put "PA 23.4", a
 *  rotation the run never commands, on the card. */
function angleWords(p: Record<string, string | number>): string {
  let angle = targetAngle(p).trim();
  if (!(TARGET_ANGLES as readonly string[]).includes(angle)) {
    angle = targetAngle({ rotation: p.rotation });
  }
  if (angle === "Any angle") return "any angle";
  // An unreadable rotation is -1 to the compile, as it is here: the card shows
  // the number the run would read, and the doctor says what is wrong with it.
  const r = decimal(p.rotation);
  const pa = `PA ${(Number.isFinite(r) ? r : -1).toFixed(1)}`;
  return angle === "Camera fixed at PA" ? `fixed ${pa}` : pa;
}

// --------------------------------------------------------------- the footer
/** True when the block rotates its panels: a mosaic with a loop wire from the
 *  tail of its own lane into its `next`, and no pass wire into that `next`
 *  from a stage before the tail. The graph, never the params, decides.
 *
 *  THE SECOND CONDITION IS M12 (#410). A pass wire from mid-lane is a refusal
 *  (compile.py `lane_refusals`): the run does not rotate the panels, it does
 *  not start. `loopWires` alone finds a tail wire standing beside a stale one
 *  (a flow saved before S4, or LOOP PANELS as S4 built it), and the footer
 *  read "rotate" and the arc "every pass: next panel" over a flow `/run`
 *  refuses. So the card says what the RUN does: one panel at a time until the
 *  lane is one loop, which LOOP PANELS then offers to make.
 *
 *  Cheap and `sum`-free on purpose: the cards call this from a store selector,
 *  which runs on every store write, and two test files count card renders
 *  through `NODE_DEFS.target.sum`. */
export function targetLoops(node: FlowNodeRec, graph: LaneGraph): boolean {
  return node.type === "target" && isMultiPanel(node) && loopWires(graph, node.id).length > 0
    && midLanePassWires(graph, node.id).length === 0;
}

/** The footer's facts, each a whole segment, in the order the line says them.
 *  Null where the block has no such fact: a single target has no loop word,
 *  no grid and no overlap, and a panel-first mosaic no angle or overlap. */
interface FooterParts {
  /** The TARGET's own `sum`; "" before the block is named. */
  name: string;
  /** "rotate" or "one panel at a time": what tells the two mosaic lines apart. */
  loop: string | null;
  size: string | null;
  /** "any angle", "PA 30.0" or "fixed PA 30.0": the mode and the PA, one segment. */
  angle: string | null;
  overlap: string | null;
}

/** THE NAME IS THE VOCABULARY'S OWN SUM, called exactly once, so the TARGET's
 *  `sum` stays what nodeDefs.ts says it is and the render counters in
 *  flowNodeDom.test.tsx and flowProgressChip.test.tsx still see one call per
 *  card render. */
function footerParts(node: FlowNodeRec, loops: boolean): FooterParts {
  const p = node.params ?? {};
  const name = NODE_DEFS.target.sum(p);
  if (!isMultiPanel(node)) {
    return { name, loop: null, size: null, angle: angleWords(p), overlap: null };
  }
  const { rows, cols } = targetGrid(node);
  // COLUMNS BY ROWS (S4 orchestrator ruling 1): a grid is written the way it
  // is seen, width first, so three columns of two rows is "3x2".
  const size = `${cols}x${rows}`;
  if (!loops) return { name, loop: "one panel at a time", size, angle: null, overlap: null };
  const overlap = decimal(p.overlap);
  return {
    name, loop: "rotate", size, angle: angleWords(p),
    overlap: Number.isFinite(overlap) ? `${overlap}%` : null,
  };
}

/** Segments joined with the separator. Empty ones are left out: a created
 *  TARGET has no name yet, and " · any angle" would read as a glitch. */
function joined(...segments: (string | null)[]): string {
  return segments.filter((s): s is string => s != null && s !== "").join(SUMMARY_SEP);
}

/** The footer for a TARGET whose loopedness the caller already knows: the
 *  whole line, as the #/next card draws it (it wraps, so nothing is cut).
 *
 *  The cards call this rather than `targetSummary`: a card's selectors must
 *  return primitives (FlowNodeCard.tsx's header says why), and the graph is not
 *  a prop, so a card subscribes to `targetLoops`'s boolean and hands it here.
 *
 *  THE WORD THAT TELLS THE TWO MOSAIC LINES APART COMES SECOND (#357), right
 *  after the name. The design's order, "M31 · 3x2 · PA 30.0 · 25% · rotate",
 *  put it where the classic card's one line was cut, so a rotating mosaic
 *  read like a panel-first one, the one thing this line was added to say.
 *  The classic card now draws `fittedFooter` instead, which never cuts it. */
export function targetFooter(node: FlowNodeRec, loops: boolean): string {
  const f = footerParts(node, loops);
  return joined(f.name, f.loop, f.size, f.angle, f.overlap);
}

// ---------------------------------------------- the footer on one short line
//
// S7 orchestrator ruling 9 (#357). The classic card's footer is ONE line of
// 9.5 px mono inside `px-2.5` on a 188 px card: 166 px, 29 characters at the
// font's 0.6 em advance (`CLASSIC_FOOTER_CHARS`, FlowNodeCard.tsx, which
// cardFooterDom.test.tsx holds to the mounted card's classes). CSS `truncate`
// cut whatever reached the end, so reordering alone (S5) still lost the
// overlap and the end of the PA on the #357 probe's rotating 2x2
// ("M31 · rotate · 2x2 · PA 55.0...") and "one panel at a time" behind any
// name longer than 6. The ruling: when the line does not fit, the overlap
// goes first, then the name is shortened with an ellipsis, and the angle
// words (the mode and the PA) and the loop word are never cut. #/next keeps
// the whole line and wraps.
//
// PAST THE RULING, COMPUTED. After the overlap, the panel-first line is
// "name · one panel at a time · 3x2", which leaves ONE character of room for
// the name (29 - 28): read literally, every panel-first mosaic's name would
// be the ellipsis alone, and two blocks on one canvas would read the same.
// So the name keeps at least one of its characters, and when it cannot, the
// grid - the one segment the ruling neither drops nor protects, and one the
// modal and the inspector both show - gives way before it, and the name is
// fitted again: "M31 · one panel at a time", "NGC 73… · one panel at a time".
// The name is left out only when not one of its characters fits even then,
// which takes angle words no rotator's PA reaches: "rotate · fixed PA
// 359.9" is 23 and leaves the name two characters and the ellipsis, while
// "fixed PA 12345.6" leaves one character of room, too little for a
// character and the ellipsis. Past that, a line is over the budget only
// when "rotate" and the angle alone are, a rotation typed with a dozen
// digits, and `truncate` still stands behind it.

/** What a shortened name ends with, one advance of the mono font like any
 *  other character. */
export const FOOTER_ELLIPSIS = "…";

/** The line `name` leads, shortened with the ellipsis to fit `chars`, or
 *  null when not one character of it fits beside `rest`. */
function nameFitted(name: string, rest: (string | null)[], chars: number): string | null {
  const tail = joined(...rest);
  const whole = joined(name, tail);
  if (whole.length <= chars) return whole;
  if (name === "") return null;
  const room = chars - tail.length - (tail === "" ? 0 : SUMMARY_SEP.length);
  // At least one character of the name, then the ellipsis. Trimmed, so a
  // name cut at a space reads "NGC…", not "NGC …".
  const kept = name.slice(0, room - 1).trimEnd();
  return room >= 2 && kept !== "" ? joined(kept + FOOTER_ELLIPSIS, tail) : null;
}

/** The TARGET footer fitted to `chars` characters, for the classic card
 *  (ruling 9, above): `line` is what it draws and `full` the whole line, for
 *  its tooltip. The name is still the one `sum` call. */
export function fittedFooter(
  node: FlowNodeRec, loops: boolean, chars: number,
): { line: string; full: string } {
  const f = footerParts(node, loops);
  const full = joined(f.name, f.loop, f.size, f.angle, f.overlap);
  if (full.length <= chars) return { line: full, full };
  const line =
    // 1. The overlap goes, and then, if that is not enough, the name is
    //    shortened.
    nameFitted(f.name, [f.loop, f.size, f.angle], chars)
    // 2. The grid, when not one character of the name fits beside it.
    ?? nameFitted(f.name, [f.loop, f.angle], chars)
    // 3. The name, when none fits even then.
    ?? joined(f.loop, f.angle);
  return { line, full };
}

/** One character's advance in IBM Plex Mono (Tailwind's `font-mono`,
 *  index.css `--font-mono`), in em. */
export const MONO_ADVANCE_EM = 0.6;

/** How many characters of `fontPx` mono fit in `px`. */
export function monoChars(px: number, fontPx: number): number {
  return Math.floor(px / (fontPx * MONO_ADVANCE_EM));
}

/** The TARGET card's footer line (spec 1.2):
 *
 *      rotating mosaic   M31 · rotate · 3x2 · PA 30.0 · 25%
 *      mosaic, no loop   M31 · one panel at a time · 3x2
 *      single target     NGC 7331 · any angle
 *
 *  For a TARGET only; every other card keeps its vocabulary `sum`. The
 *  design wrote the mosaic lines with the loop word last and said they were
 *  "sized to the 188 px card"; computed, they are not (#357), which is why
 *  `targetFooter` puts that word second and the classic card fits the line
 *  with `fittedFooter`. */
export function targetSummary(node: FlowNodeRec, graph: LaneGraph): string {
  return targetFooter(node, targetLoops(node, graph));
}

// ------------------------------------------------------------ the loop wire
/** The loop arc's dash: a dash-dot, which no other wire on either canvas
 *  draws. Resting event wires are `4 5` and a live wire of either lane is
 *  `7 6`, and the night palette collapses every lane hue into one red, so the
 *  arc is told apart by this silhouette and by its chip, never by colour.
 *  It does NOT switch to `7 6` while the run is on - that is exactly when the
 *  night palette is up - so activity shows as width, full colour and the march
 *  instead. */
export const LOOP_ARC_DASH = "10 4 2 4";

/** The chip's words (spec 1.4: "every pass: next panel . 6 panels"). */
export const LOOP_CHIP_WORDS = "every pass: next panel";

/** The chip's type size: the card footer's 9.5 px mono, so the wire's label
 *  reads at the same size as the cards around it. */
export const LOOP_CHIP_FONT_PX = 9.5;
/** One character's advance in that font. */
const LOOP_CHIP_CHAR_W = LOOP_CHIP_FONT_PX * MONO_ADVANCE_EM;

/** The chip's box around its text: computed, not measured, like every other
 *  box on the canvas - the text is monospace, so its width is its length. */
export function loopChipBox(text: string): { w: number; h: number } {
  return { w: Math.ceil(text.length * LOOP_CHIP_CHAR_W) + 12, h: 14 };
}

/** The TARGET whose `next` this wire enters, when it is an EVENT wire, or
 *  null. Every such wire is drawn as the arc: whether or not the compile loops
 *  on it, it runs backward from a stage to the block the stage hangs off, and
 *  the stock curve would cross the lane. Only an event wire, because a flow
 *  wire cannot enter an event input (validation refuses it) and one that
 *  arrived anyway is the lane's own business. */
export function loopArcTarget(edge: FlowEdgeRec, nodes: readonly FlowNodeRec[]): FlowNodeRec | null {
  if (edge.toPort !== NEXT_PORT) return null;
  const to = nodes.find((n) => n.id === edge.to);
  if (!to || to.type !== "target") return null;
  const from = nodes.find((n) => n.id === edge.from);
  const port = from ? NODE_DEFS[from.type]?.outs.find((p) => p.id === edge.fromPort) : undefined;
  return port?.kind === "event" ? to : null;
}

// ------------------------------------------------ the arc on the column layout
//
// The classic phone FLOW tab (#360) lays the graph out in two columns
// (autoLayout.ts `computeAutoLayout`) inside a container that clips anything
// past its edges, and the canvas's arc does not fit it. Its legs stood
// `LOOP_ARC_STUB` (24 px) outside the outermost body cards, and the columns
// stand `AUTO_PAD` (14 px) inside the container, so both legs were drawn
// outside it and cut off: the loop read as two stubs with nothing joining
// them. And its run lay 28 px under the lowest body card, where the column
// layout had already put the NEXT card in flow order (the REPORT after the
// tail, 16 px down in the other column), so the run crossed it.
//
// So on this surface the legs stand half the gutter out, inside the
// container, and the run lies in the middle of the gap the column layout
// leaves under a flow-lane card, below every card from the TARGET down to
// the tail and above the one that follows. Any card the run's span would
// still meet (a stage a flow cycle left out of the flow order is stacked
// 4 px under the one before it) pushes the run below it (`LoopArcOpts`).

/** How far outside the columns the legs stand on the column layout: half of
 *  `AUTO_PAD`, so each leg is as far inside the container as it is outside
 *  the cards. */
export const COLUMN_LOOP_STUB = AUTO_PAD / 2;

/** How much taller a card's formula box (`nodeLayoutHeight`, the fit's pad)
 *  is than the height the column layout spaces it by (`layoutHeight`, its
 *  own pad): the same for every card, since both are the header plus the
 *  rows plus a pad. Read off a card with no rows rather than restated. */
const BOX_OVER_SPACING = ((): number => {
  const bare = { id: "", type: "" as FlowNodeRec["type"], x: 0, y: 0, params: {} };
  return nodeLayoutHeight(bare, NODE_DEFS) - layoutHeight(bare, NODE_DEFS);
})();

/** How far below the lowest body card the run lies on the column layout: the
 *  middle of the gap between two consecutive flow-lane cards' boxes, which is
 *  `FLOW_LANE_GAP` less what each box stands taller than its spacing (34 - 18
 *  = 16 px, so 8). A card is `layoutHeight` + `FLOW_LANE_GAP` below the one
 *  before it in flow order, whichever column, and every card before the
 *  lowest body card ends above it, so the middle of that gap is clear. */
export const COLUMN_LOOP_DROP = (FLOW_LANE_GAP - BOX_OVER_SPACING) / 2;

/** The arc for a wire into a TARGET's `next`, or null for any other wire (or
 *  one whose ports cannot be placed). The body it runs under is the TARGET,
 *  the lane it owns and the wire's source. `place` substitutes a layout's
 *  positions (the classic phone FLOW tab lays the graph out itself and never
 *  writes those positions back), applied to every card before any maths.
 *
 *  `mode` is the surface's (geometry `EdgeMode`): "canvas" draws the arc as
 *  spec 1.4 has it; "phone-flow", the column layout, draws it inside the
 *  container and clear of every card it lays out (#360, above). */
export function loopArcOf(
  edge: FlowEdgeRec,
  graph: LaneGraph,
  tier: FlowTier,
  place: (n: FlowNodeRec) => FlowNodeRec = (n) => n,
  mode: EdgeMode = "canvas",
): LoopArc | null {
  const to = loopArcTarget(edge, graph.nodes);
  const from = graph.nodes.find((n) => n.id === edge.from);
  if (!to || !from) return null;
  const opts: LoopArcOpts = mode === "phone-flow"
    ? { stub: COLUMN_LOOP_STUB, drop: COLUMN_LOOP_DROP, avoid: graph.nodes.map(place) }
    : {};
  return loopArc(place(from), edge.fromPort, place(to), edge.toPort,
    panelLane(graph, to.id).map(place), NODE_DEFS, tier, opts);
}

/** Is this the block's loop wire: a pass wire from the tail of a multi-panel
 *  block's lane into its `next`, the wire the compile consumes as `loop`,
 *  on a block that rotates (`targetLoops`). A tail wire beside a stale
 *  mid-lane one is `loop` to the compile and M12 to `to_plan` (#410), and a
 *  chip over it would promise the rotation the card's footer denies. */
function isLoopWire(graph: LaneGraph, edge: FlowEdgeRec): boolean {
  const to = loopArcTarget(edge, graph.nodes);
  return !!to && targetLoops(to, graph) && loopWires(graph, to.id).some((e) => e.id === edge.id);
}

/** A grid side the compile wrote: a whole number of at least one. */
const side = (v: unknown): number | null =>
  typeof v === "number" && Number.isInteger(v) && v >= 1 ? v : null;

/** The live panels the compile answer's entry for this block holds: rows x
 *  cols minus the panels its parsed skip names (compile.py `_target_entry`,
 *  `mosaic.skip` is `parse_skip`'s answer, each panel once). Null when there
 *  is no answer, no entry, no mosaic in it, a shape this reader cannot count,
 *  or a grid that is no longer the block's.
 *
 *  THE GRID CHECK. The compile runs when a flow is opened (flowsSlice
 *  `flowsOpen`), after the modal's DONE or LOOP PANELS (`flowsApplyFraming`)
 *  and after a save (#356), never on an inspector edit, so a 3x2 entry
 *  outlives a block re-gridded to 3x3 until the next of those lands. The chip
 *  asks whether the answer is the graph's own first (`loopChip`'s `stale`,
 *  from `compiledIsCurrent`), which already withholds such an entry; this
 *  check is kept beside it so that no caller that hands in an answer without
 *  asking can count panels the card's own footer contradicts. */
export function livePanels(
  plan: Record<string, unknown> | null | undefined, target: FlowNodeRec,
): number | null {
  const targets = plan?.targets;
  if (!Array.isArray(targets)) return null;
  const entry = targets.find((t): t is Record<string, unknown> =>
    !!t && typeof t === "object" && (t as Record<string, unknown>).node_id === target.id);
  const mosaic = entry?.mosaic as Record<string, unknown> | null | undefined;
  if (!mosaic || typeof mosaic !== "object") return null;
  const rows = side(mosaic.rows);
  const cols = side(mosaic.cols);
  const skip = mosaic.skip;
  if (rows === null || cols === null || !Array.isArray(skip)) return null;
  const grid = targetGrid(target);
  if (grid.rows !== rows || grid.cols !== cols) return null;
  const live = rows * cols - skip.length;
  return live >= 0 ? live : null;
}

/** The loop wire's label chip, or null for a wire that is not a loop wire.
 *
 *      every pass: next panel · 6 panels     after a compile
 *      every pass: next panel                before one, or while it is stale
 *
 *  NO CHIP ON A WIRE THE RUN DOES NOT LOOP ON: a pass wire from mid-lane (M12),
 *  one from another block's lane, one into a 1x1 block. It is still drawn as
 *  the arc, but "every pass: next panel" over it would promise a rotation the
 *  run will not make; the doctor names what is wrong with it.
 *
 *  NO COUNT WHILE `stale`: the answer in hand does not describe the graph on
 *  screen, because an edit - a skip typed, a grid changed - landed after the
 *  compile it came from was sent. Both canvases pass `!compiledIsCurrent`
 *  (flowsSlice.ts), which compares the graph the compile SENT with the graph
 *  on screen (#356, S7). They passed `dirty` until S7, which says whether the
 *  graph is SAVED, not whether it was COMPILED, and is wrong both ways: a
 *  skip edit then SAVE clears it a round trip before the save's own compile
 *  lands, so the chip drew the old count over the new skip, and the modal's
 *  DONE compiles the unsaved draft, so its count was withheld although it
 *  described exactly what was on screen. */
export function loopChip(
  graph: LaneGraph,
  edge: FlowEdgeRec,
  plan: Record<string, unknown> | null | undefined,
  stale: boolean,
): string | null {
  if (!isLoopWire(graph, edge)) return null;
  const to = loopArcTarget(edge, graph.nodes);
  const n = stale || !to ? null : livePanels(plan, to);
  if (n === null) return LOOP_CHIP_WORDS;
  return `${LOOP_CHIP_WORDS}${SUMMARY_SEP}${n} panel${n === 1 ? "" : "s"}`;
}
