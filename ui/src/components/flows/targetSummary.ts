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
// So the footer's "rotate" and the chip's words both come from `loopWires`
// (panelLane.ts, the mirror of compile.py `loop_wires`), which is exactly the
// wire the compile consumes as the entry's `loop` - the card cannot say
// "rotate" over a graph the run will shoot panel-first.

import { loopArc, type FlowTier, type LoopArc } from "./geometry";
import { NODE_DEFS, TARGET_ANGLES, targetAngle } from "./nodeDefs";
import { isMultiPanel, loopWires, panelLane, NEXT_PORT, type LaneGraph } from "./panelLane";
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
 *  tail of its own lane into its `next`. The graph, never the params, decides.
 *
 *  Cheap and `sum`-free on purpose: the cards call this from a store selector,
 *  which runs on every store write, and two test files count card renders
 *  through `NODE_DEFS.target.sum`. */
export function targetLoops(node: FlowNodeRec, graph: LaneGraph): boolean {
  return node.type === "target" && isMultiPanel(node) && loopWires(graph, node.id).length > 0;
}

/** The footer for a TARGET whose loopedness the caller already knows.
 *
 *  The cards call this rather than `targetSummary`: a card's selectors must
 *  return primitives (FlowNodeCard.tsx's header says why), and the graph is not
 *  a prop, so a card subscribes to `targetLoops`'s boolean and hands it here.
 *
 *  THE NAME IS THE VOCABULARY'S OWN SUM, called exactly once, so the TARGET's
 *  `sum` stays what nodeDefs.ts says it is and the render counters in
 *  flowNodeDom.test.tsx and flowProgressChip.test.tsx still see one call per
 *  card render. Empty segments are left out: a created TARGET has no name yet,
 *  and " · any angle" would read as a glitch. */
export function targetFooter(node: FlowNodeRec, loops: boolean): string {
  const p = node.params ?? {};
  const name = NODE_DEFS.target.sum(p);
  const parts: string[] = [name];
  if (isMultiPanel(node)) {
    const { rows, cols } = targetGrid(node);
    // COLUMNS BY ROWS (S4 orchestrator ruling 1): a grid is written the way it
    // is seen, width first, so three columns of two rows is "3x2".
    parts.push(`${cols}x${rows}`);
    if (loops) {
      parts.push(angleWords(p));
      const overlap = decimal(p.overlap);
      if (Number.isFinite(overlap)) parts.push(`${overlap}%`);
      parts.push("rotate");
    } else {
      parts.push("one panel at a time");
    }
  } else {
    parts.push(angleWords(p));
  }
  return parts.filter((s) => s !== "").join(SUMMARY_SEP);
}

/** The TARGET card's footer line (spec 1.2), sized to the 188 px card:
 *
 *      rotating mosaic   M31 · 3x2 · PA 30.0 · 25% · rotate
 *      mosaic, no loop   M31 · 3x2 · one panel at a time
 *      single target     NGC 7331 · any angle
 *
 *  For a TARGET only; every other card keeps its vocabulary `sum`.
 *
 *  "Sized to the 188 px card" is the spec's claim, and computed it does not
 *  hold on the classic card: 29 characters of 9.5 px mono fit, the rotating
 *  line is 34, and `truncate` cuts it inside "rotate" (#357). The strings are
 *  the spec's until that is ruled on. */
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
/** One character's advance in that font. IBM Plex Mono advances 0.6 em. */
const LOOP_CHIP_CHAR_W = LOOP_CHIP_FONT_PX * 0.6;

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

/** The arc for a wire into a TARGET's `next`, or null for any other wire (or
 *  one whose ports cannot be placed). The body it runs under is the TARGET,
 *  the lane it owns and the wire's source. `place` substitutes a layout's
 *  positions (the classic phone FLOW tab lays the graph out itself and never
 *  writes those positions back), applied to every card before any maths. */
export function loopArcOf(
  edge: FlowEdgeRec,
  graph: LaneGraph,
  tier: FlowTier,
  place: (n: FlowNodeRec) => FlowNodeRec = (n) => n,
): LoopArc | null {
  const to = loopArcTarget(edge, graph.nodes);
  const from = graph.nodes.find((n) => n.id === edge.from);
  if (!to || !from) return null;
  return loopArc(place(from), edge.fromPort, place(to), edge.toPort,
    panelLane(graph, to.id).map(place), NODE_DEFS, tier);
}

/** Is this the block's loop wire: a pass wire from the tail of a multi-panel
 *  block's lane into its `next`, the wire the compile consumes as `loop`. */
function isLoopWire(graph: LaneGraph, edge: FlowEdgeRec): boolean {
  const to = loopArcTarget(edge, graph.nodes);
  return !!to && isMultiPanel(to) && loopWires(graph, to.id).some((e) => e.id === edge.id);
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
 *  `flowsOpen`) and after the modal's DONE or LOOP PANELS
 *  (`flowsApplyFraming`), and not after any other edit or a save, so a 3x2
 *  entry can outlive a block re-gridded to 3x3 in the inspector and saved.
 *  The chip would then count panels the card's own footer contradicts. A
 *  skip edited the same way and saved still gets through until the flow is
 *  reopened (#356). */
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
 *  NO COUNT WHILE `dirty`, for the same reason the progress chip hides then
 *  (flowProgress.ts): the compile describes the graph as it last compiled,
 *  and an unsaved edit in the inspector - a skip typed, a grid changed - is
 *  not in it. Conservative after the modal's DONE, whose own compile does
 *  describe the draft: the count comes back at the save. */
export function loopChip(
  graph: LaneGraph,
  edge: FlowEdgeRec,
  plan: Record<string, unknown> | null | undefined,
  dirty: boolean,
): string | null {
  if (!isLoopWire(graph, edge)) return null;
  const to = loopArcTarget(edge, graph.nodes);
  const n = dirty || !to ? null : livePanels(plan, to);
  if (n === null) return LOOP_CHIP_WORDS;
  return `${LOOP_CHIP_WORDS}${SUMMARY_SEP}${n} panel${n === 1 ? "" : "s"}`;
}
