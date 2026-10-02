// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// geometry.ts — the canvas's pure maths: card metrics, port anchors, wire
// paths, fit-to-view. No React, no store, no DOM.
//
// Every number here is transcribed from the approved prototype
// ("AstroDeck Flows.dc.html") and ratified by MILESTONE2-CONTRACT §D.2 / §D.3.
// None of it is derived from a measured element: §C.5 is explicit that "the
// spacing model is the formula" and that measuring the DOM to lay out is
// forbidden — a card's height must be knowable before it renders, because
// fit-to-view runs in the same tick that loads a flow.
//
// Deliberately decoupled from `nodeDefs.ts`: everything that needs the node
// vocabulary takes it as a `PortTable` argument. `NODE_DEFS` satisfies that
// type structurally, so call sites just pass it. The reason is not purity for
// its own sake — it is that this file must typecheck and its tests must run
// with nothing else from the Flows surface present.
import type { FlowNodeRec, FlowNodeType } from "./flowsTypes";

/** Viewport tier, per §C.1's `useFlowsTier()`. Declared here rather than
 *  imported so geometry stays leaf-level; the hook returns this same union. */
export type FlowTier = "phone" | "tablet" | "desktop";

export type PortDir = "in" | "out";

/** Which bezier a wire gets.
 *
 *  "canvas" is every pan/zoom surface — desktop, tablet, AND the phone CANVAS
 *  tab, which §C.5 calls "the full editor". "phone-flow" is only the phone
 *  FLOW tab, the auto-laid-out column view (the prototype's `autoMode()`),
 *  where the graph reads top-to-bottom and a horizontal control point would
 *  sweep the wire off-screen. */
export type EdgeMode = "canvas" | "phone-flow";

export interface Point {
  x: number;
  y: number;
}

/** The canvas element's box. Only the size is used — a DOMRect satisfies it
 *  structurally, so callers pass `el.getBoundingClientRect()` unchanged. */
export interface Rect {
  width: number;
  height: number;
}

/** The only part of a `NodeDef` geometry needs: how many port rows, and in
 *  what order. Order is load-bearing — inputs occupy rows 0..ins.length-1 and
 *  outputs continue from there, so an output's row index depends on the
 *  *input* count of the same node (§D.3). */
export interface NodePortShape {
  readonly ins: readonly { readonly id: string }[];
  readonly outs: readonly { readonly id: string }[];
}

/** `NODE_DEFS` from nodeDefs.ts satisfies this structurally. */
export type PortTable = Readonly<Record<FlowNodeType, NodePortShape>>;

export interface FitView {
  pan: Point;
  zoom: number;
}

// ------------------------------------------------------------------ constants
// Card metrics, prototype lines 837 (`nodeW`) and 905-912 (`portPos`).
/** §C.5: "188px desktop/tablet, 150px phone — device-based, not tab-based." */
export const NODE_W_WIDE = 188;
export const NODE_W_PHONE = 150;
/** Header block above the first port row: 1px border + 32px header + 5px
 *  top padding minus the 1px the design absorbs. It is a single constant in
 *  the prototype and is reproduced as one. */
export const NODE_HEADER_H = 37;
/** One port row. `h-5` in the card markup. */
export const PORT_ROW_H = 20;
/** Half a row: the dot's centre within its own row. */
export const PORT_ROW_CENTER = 10;

// Zoom limits, prototype lines 1015-1021 (wheel + buttons) and 1029 (fit).
/** Wheel and pinch clamp. §D.2: "Clamp 0.35 – 1.6." */
export const ZOOM_MIN = 0.35;
export const ZOOM_MAX = 1.6;
/** Fit never zooms past 1.15 even when three nodes could fill the canvas —
 *  a graph blown up to 1.6 reads as broken, not as fitted (README §Canvas:
 *  "FIT = bounding box + 30px margin, zoom ≤1.15"). */
export const FIT_ZOOM_MAX = 1.15;

/** Margin left around the bounding box on all four sides. */
export const FIT_PAD = 30;
/** Height the fit *scale* holds back, and the (different) height the fit *pan*
 *  holds back. §D.2: "the fit scale reserves 40px of height, the fit pan
 *  reserves 34px, and the log strip is 30px — none of the three matches.
 *  Note the asymmetry and reproduce it." Collapsing them to one number would
 *  move every reference capture's graph by a few pixels. */
export const FIT_SCALE_RESERVE_Y = 40;
export const FIT_PAN_RESERVE_Y = 34;

// ------------------------------------------------------------------ metrics
export function nodeW(tier: FlowTier): number {
  return tier === "phone" ? NODE_W_PHONE : NODE_W_WIDE;
}

/** Clamp for wheel, pinch and the ± buttons — one definition so the three
 *  call sites cannot drift apart. */
export function clampZoom(z: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
}

/** Port rows on a card: every input, then every output. Never interleaved. */
export function nodeRows(node: FlowNodeRec, defs: PortTable): number {
  const d = defs[node.type];
  return d ? d.ins.length + d.outs.length : 0;
}

/** The card height fit-to-view lays out against: `37 + rows·20 + 26`.
 *
 *  ⚠ Two heights for one card exist in the prototype and they disagree by 2px:
 *  `fit()` uses +26 (line 1025) and `computeAutoLayout()` uses +8 (line 1043).
 *  §C.5 rules: "Reproduce both, in their own call sites." This file owns the
 *  fit one; `autoLayout.ts` owns the +8 one. Neither equals the rendered DOM
 *  stack (~64 + 20·rows desktop) — they are layout budgets, not measurements,
 *  and unifying them would shift the fit zoom off the reference captures' 46%. */
export function nodeLayoutHeight(node: FlowNodeRec, defs: PortTable): number {
  return NODE_HEADER_H + nodeRows(node, defs) * PORT_ROW_H + 26;
}

/** §C.5 names this function `geometry.fitViewHeight()` while §A.1 names it
 *  `nodeLayoutHeight`. Same function, both names resolve. */
export const fitViewHeight = nodeLayoutHeight;

// ------------------------------------------------------------------ anchors
/** World-space anchor of one named port (prototype lines 905-912).
 *
 *      in : { x: n.x,          y: n.y + 37 + idx * 20 + 10 }
 *      out: { x: n.x + nodeW,  y: n.y + 37 + (ins.length + outIdx) * 20 + 10 }
 *
 *  ⚠ A 1px offset is baked in and README §3 ratifies it (§D.3). The input
 *  dot's visual centre is at `n.x + 1` and the output's at `n.x + 187`, and
 *  the first row's visual centre is `n.y + 48`, so every wire attaches 1px
 *  outside its dot. Do not "correct" it: the anchor formula is the published
 *  contract and the reference captures were taken with the offset present.
 *
 *  Returns null when the node type or the port id is unknown. The prototype
 *  instead falls through with `idx === -1`, which anchors the wire 20px HIGHER
 *  than the first row — i.e. inside the card header, where it looks like a
 *  deliberate anchor rather than a missing port. A saved graph can reference a
 *  port the vocabulary has since dropped, and a wire we cannot place is one
 *  the caller must skip, not draw somewhere plausible. */
export function portPos(
  node: FlowNodeRec,
  portId: string,
  dir: PortDir,
  defs: PortTable,
  tier: FlowTier,
): Point | null {
  const d = defs[node.type];
  if (!d) return null;
  if (dir === "in") {
    const idx = d.ins.findIndex((p) => p.id === portId);
    if (idx < 0) return null;
    return {
      x: node.x,
      y: node.y + NODE_HEADER_H + idx * PORT_ROW_H + PORT_ROW_CENTER,
    };
  }
  const idx = d.outs.findIndex((p) => p.id === portId);
  if (idx < 0) return null;
  return {
    x: node.x + nodeW(tier),
    // Outputs continue the same row stack the inputs started.
    y:
      node.y +
      NODE_HEADER_H +
      (d.ins.length + idx) * PORT_ROW_H +
      PORT_ROW_CENTER,
  };
}

// -------------------------------------------------------------------- wires
/** The cubic bezier between two anchors (prototype lines 920-929).
 *
 *  canvas:      c  = max(46, |dx| * 0.5)         control points purely horizontal
 *  phone-flow:  vy = clamp(|dy| * 0.4, 24, 70)   control points ±18 horizontal
 *
 *  The wire's LANE does not enter this function. A flow wire and an event wire
 *  between the same two points get byte-identical paths; the lane shows up as
 *  stroke colour and the dash array, applied by the wire layer.
 *
 *  Two shapes fall out that look like bugs and are not (§D.3):
 *   * short spans: `c` floors at 46, so for |dx| < 92 the control points CROSS
 *     and the wire kinks. "Do not clamp it away."
 *   * long spans: a right-to-left wire sweeps far right and comes back —
 *     "It is the design, not a bug — do not 'fix' the routing."
 *  On phone-flow the ±18 is always +18 on the source and −18 on the target
 *  regardless of direction, which is what produces the wide S-sweep for a
 *  right-column → left-column wire.
 *
 *  Separator formatting follows §D.3's literal strings ("C264 97, 214 97,
 *  260 97" — comma then space). The prototype emits no space after the comma;
 *  the SVG path grammar treats the two as identical, and the contract is the
 *  higher authority, so its exact text is what we emit and what the test pins. */
export function edgePath(p1: Point, p2: Point, mode: EdgeMode): string {
  if (mode === "phone-flow") {
    const vy = Math.max(24, Math.min(70, Math.abs(p2.y - p1.y) * 0.4));
    return (
      `M${p1.x} ${p1.y} C${p1.x + 18} ${p1.y + vy}, ` +
      `${p2.x - 18} ${p2.y - vy}, ${p2.x} ${p2.y}`
    );
  }
  const c = Math.max(46, Math.abs(p2.x - p1.x) * 0.5);
  return (
    `M${p1.x} ${p1.y} C${p1.x + c} ${p1.y}, ` +
    `${p2.x - c} ${p2.y}, ${p2.x} ${p2.y}`
  );
}

// ----------------------------------------------------------------- loop arc
// The panel loop (#189 S4 item 6; spec 2026-09-23 flows mosaic, 1.4 "How it is
// drawn"). The dashed "pass done" wire runs from the tail of a TARGET's panel
// lane back to that TARGET's `next` input, which sits LEFT of every stage the
// wire leaves. `edgePath` for that right-to-left span is the "long spans" shape
// its own comment describes - a sweep far right and back - and on a lane it
// passes straight through the cards between the two ends. So this wire gets its
// own route: out of the source port, down below the cards, left, and up into
// `next`.
//
// THE CARDS' BOTTOMS COME FROM THE FORMULA. `nodeLayoutHeight` is a budget, not
// a measurement (its comment says how far it sits from the rendered stack), and
// the spec says the arc is "computed from the card formula and never measured
// from the DOM": the path must be knowable in the same tick a flow loads, like
// every other number in this file.

/** How far below the lowest body card the arc's run lies (spec 1.4: "drops to
 *  y = max(bottom of the owned body cards) + 28 px"). */
export const LOOP_ARC_DROP = 28;
/** The straight stub out of the source port, and into `next`, before the arc
 *  turns. The drop and the rise stand this far outside the outermost body
 *  cards, which is also what keeps a 22 px remove control on the drop clear of
 *  the tail's card. */
export const LOOP_ARC_STUB = 24;
/** Corner radius. A square corner reads as a routing glitch; a larger radius
 *  would eat a short stub. */
export const LOOP_ARC_RADIUS = 10;

/** A card's box by the formula: its stored position, the tier's width and the
 *  fit height. What the arc routes around, and what its tests measure against,
 *  so the two cannot disagree about where a card is. */
export interface CardBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function cardBox(node: FlowNodeRec, defs: PortTable, tier: FlowTier): CardBox {
  return { x: node.x, y: node.y, w: nodeW(tier), h: nodeLayoutHeight(node, defs) };
}

/** How one surface bends the arc, where the canvas's numbers do not fit it.
 *  Every field defaults to the canvas's (spec 1.4), so a caller that passes
 *  nothing draws the arc the spec describes.
 *
 *  The phone FLOW tab is the one caller that passes any (#360): its column
 *  layout leaves a 14 px gutter at each edge of a container that clips what
 *  crosses it, so a 24 px stub puts both legs outside, and it interleaves
 *  every card, so the card after the tail in flow order stands 16 px below
 *  the tail's box, where a run 28 px down lands inside it. */
export interface LoopArcOpts {
  /** How far outside the outermost body cards the two legs stand
   *  (`LOOP_ARC_STUB` when absent). */
  stub?: number;
  /** How far below the lowest body card, and at least how far from any card
   *  in `avoid`, the run lies (`LOOP_ARC_DROP` when absent). */
  drop?: number;
  /** Cards outside the body the run must not cross, each by its formula box.
   *  A card the run's span would meet pushes the run below it, `drop` clear
   *  of it; one that already lies `drop` clear, or beside the span, does not.
   *  None when absent: on the canvas the spec promises to clear the body
   *  only, and the operator arranges the rest. */
  avoid?: readonly FlowNodeRec[];
}

/** The arc and the two places a layer hangs things on it. */
export interface LoopArc {
  /** The SVG path, both ends on their port anchors. */
  d: string;
  /** The height of the horizontal run. */
  runY: number;
  /** The middle of the run: where the label chip sits. */
  label: Point;
  /** The middle of the drop: a point on the wire, outside every body card and
   *  away from the chip, where a remove control can sit. The straight-line
   *  midpoint of the two anchors (`wireMidpoint`) is NOT on this path - for a
   *  lane drawn left to right it lands among the lane's cards, at port height,
   *  where no wire is drawn. */
  handle: Point;
  /** The route before its corners are rounded: the source anchor, the top
   *  and the bottom of the drop, the two ends of the run, the top of the rise
   *  and the `next` anchor, in that order. `d` is drawn through exactly these
   *  six points, so a surface that hangs something on a leg (`loopControlSpot`)
   *  or moves the run (`lowerLoopRun`) reads the route rather than reversing
   *  it out of the path string. */
  corners: readonly Point[];
}

/** The route of one loop wire, or null when either port cannot be placed.
 *
 *  `from` is the stage the wire leaves (normally the tail of `to`'s panel lane),
 *  `to` the TARGET whose `next` it enters, and `lane` the stages `to` owns. The
 *  BODY is all three - the TARGET, its lane and the source - because the run
 *  passes under every one of them, the TARGET included: `next` is on the
 *  TARGET's left edge, so the arc reaches it from below-left.
 *
 *      run   = max(bottom of every body card, both anchors) + LOOP_ARC_DROP
 *      drop  = max(right edge of every body card, the source anchor) + STUB
 *      rise  = min(left edge of every body card, the next anchor) - STUB
 *
 *  Taking the extremes of the whole body rather than of the two end cards is
 *  what keeps the two vertical legs off every card, whatever order the lane is
 *  drawn in; the run is below all of them by construction. Only the two short
 *  horizontal stubs at port height could meet a card, and only one drawn right
 *  of the tail or left of the TARGET at that height, which no generator or
 *  Example lays out. Ends as it starts, on the anchors `portPos` gives, so the
 *  wire attaches exactly where every other wire does.
 *
 *  `opts` bends those three numbers for a surface the canvas's do not fit
 *  (`LoopArcOpts`): a shorter stub, a shorter drop, and cards outside the
 *  body the run must pass under rather than through. */
export function loopArc(
  from: FlowNodeRec,
  fromPort: string,
  to: FlowNodeRec,
  toPort: string,
  lane: readonly FlowNodeRec[],
  defs: PortTable,
  tier: FlowTier,
  opts: LoopArcOpts = {},
): LoopArc | null {
  const p1 = portPos(from, fromPort, "out", defs, tier);
  const p2 = portPos(to, toPort, "in", defs, tier);
  if (!p1 || !p2) return null;
  const stub = opts.stub ?? LOOP_ARC_STUB;
  const drop = opts.drop ?? LOOP_ARC_DROP;

  // One box per card: the tail is in the lane AND the source, and a hand-built
  // graph can name the TARGET twice.
  const seen = new Set<string>();
  const body: CardBox[] = [];
  for (const n of [to, ...lane, from]) {
    if (seen.has(n.id)) continue;
    seen.add(n.id);
    body.push(cardBox(n, defs, tier));
  }

  let runY = Math.max(p1.y, p2.y, ...body.map((b) => b.y + b.h)) + drop;
  const dropX = Math.max(p1.x, ...body.map((b) => b.x + b.w)) + stub;
  const riseX = Math.min(p2.x, ...body.map((b) => b.x)) - stub;

  // UNDER, NOT THROUGH, every other card the run's span meets. Each pass drops
  // the run below the cards it would cross, which none of them can then block
  // again, so the walk ends within one pass per card. A body card never
  // blocks: the run already lies `drop` below every one of them.
  const others = (opts.avoid ?? [])
    .map((n) => cardBox(n, defs, tier))
    .filter((b) => b.x < dropX && b.x + b.w > riseX);
  for (;;) {
    const hit = others.filter((b) => runY - drop < b.y + b.h && runY + drop > b.y);
    if (hit.length === 0) break;
    runY = Math.max(...hit.map((b) => b.y + b.h)) + drop;
  }

  return arcThrough([
    p1,
    { x: dropX, y: p1.y },
    { x: dropX, y: runY },
    { x: riseX, y: runY },
    { x: riseX, y: p2.y },
    p2,
  ]);
}

/** The arc drawn through six corners (`LoopArc.corners`), and the two places
 *  a layer hangs things on it, read off those corners: the chip in the middle
 *  of the run, the handle in the middle of the drop. One builder, so an arc
 *  whose run was moved (`lowerLoopRun`) cannot put its chip or its handle
 *  where the route no longer runs. */
function arcThrough(corners: readonly Point[]): LoopArc {
  const [p1, , bottom, runEnd] = corners;
  const runY = bottom.y;
  return {
    d: roundedPath(corners, LOOP_ARC_RADIUS),
    runY,
    label: { x: (bottom.x + runEnd.x) / 2, y: runY },
    handle: { x: bottom.x, y: (p1.y + runY) / 2 },
    corners,
  };
}

/** `arc` with its run `by` px lower: the same two anchors, the same legs, a
 *  longer drop and rise, and the chip and the handle moved with the run.
 *
 *  For a surface whose cards stand taller than the formula box the arc routes
 *  under (#357): the #/next card as drawn can reach `CARD_OVERHANG_PX` below
 *  its `nodeLayoutHeight` (canvas/FlowNode.tsx), and its layer lowers the run
 *  by exactly that, so the run keeps `LOOP_ARC_DROP` below the card as drawn
 *  where the canvas keeps it below the formula. Since nothing on the canvas
 *  bends the run but the drop (no `avoid` cards there), this is the arc a
 *  drop of `LOOP_ARC_DROP + by` would draw; the run is lowered rather than the
 *  drop passed in because `targetSummary.loopArcOf`, which decides which
 *  wires are arcs and which cards are the body, is the one resolver both
 *  layers ask, and it takes no drop. Still the card formula, never the DOM.
 *  A `by` of 0 hands back the same route. */
export function lowerLoopRun(arc: LoopArc, by: number): LoopArc {
  const [p1, top, bottom, runEnd, riseTop, p2] = arc.corners;
  return arcThrough([
    p1, top,
    { x: bottom.x, y: bottom.y + by },
    { x: runEnd.x, y: runEnd.y + by },
    riseTop, p2,
  ]);
}

/** Where a `size` px square remove control can sit on a loop arc, or null
 *  when there is no such place on either leg.
 *
 *  The place asked for (#502), each an inequality on the control's box:
 *
 *    - inside [x0, x1], the container the surface clips at;
 *    - over the wire: a leg runs through the box;
 *    - over no card: the box meets none of `cards` (touching an edge is not
 *      meeting it).
 *
 *  THE LEGS, NOT THE RUN. The chip sits in the middle of the run, so the
 *  control is sought on the drop first, then on the rise. On each, the box
 *  is centred on the leg and moved in just far enough to stay inside
 *  [x0, x1]; the column layout stands each leg `COLUMN_LOOP_STUB` (7 px)
 *  inside its container, so a 26 px box is moved in 6 px
 *  (`LOOP_CONTROL_INSET_AUTO`, FlowWireDelete.tsx) and the leg still runs
 *  through it 7 px inside its outer side. Every card that stands beside the
 *  box rules out the heights at which the box would meet it; of the heights
 *  left on the leg, the one nearest the leg's middle wins (the higher one on
 *  a tie), so where the middle is clear - the drop on the eighth Example -
 *  the control sits where `handle` puts it.
 *
 *  On the phone FLOW tab's column layout the drop stands beside the tail's
 *  card when the tail sits in the right column: the handle, the middle of the
 *  drop, fell inside the tail's height and the box covered its corner. The
 *  rise then stands beside the left column, whose cards leave a card's height
 *  of room across from each card of the right column. */
export function loopControlSpot(
  arc: LoopArc,
  size: number,
  x0: number,
  x1: number,
  cards: readonly CardBox[],
): Point | null {
  const half = size / 2;
  const [, top, bottom, runEnd, riseTop] = arc.corners;
  for (const [a, b] of [[top, bottom], [riseTop, runEnd]] as const) {
    const cx = Math.min(x1 - half, Math.max(x0 + half, a.x));
    const lo = Math.min(a.y, b.y);
    const hi = Math.max(a.y, b.y);
    // Each card beside the box rules out the open band of centre heights at
    // which the two would overlap.
    const blocked = cards
      .filter((c) => c.x < cx + half && c.x + c.w > cx - half)
      .map((c) => [c.y - half, c.y + c.h + half] as const);
    const clear = (y: number) => blocked.every(([t, u]) => y <= t || y >= u);
    // The nearest clear height to the middle is the middle itself or the end
    // of a band: the clear set's edges are band ends.
    const mid = (lo + hi) / 2;
    const best = [mid, ...blocked.flat()]
      .filter((y) => y >= lo && y <= hi && clear(y))
      .sort((p, q) => Math.abs(p - mid) - Math.abs(q - mid) || p - q)[0];
    if (best !== undefined) return { x: cx, y: best };
  }
  return null;
}

/** Just the path of `loopArc`, for a layer that hangs nothing on it. */
export function loopArcPath(
  from: FlowNodeRec,
  fromPort: string,
  to: FlowNodeRec,
  toPort: string,
  lane: readonly FlowNodeRec[],
  defs: PortTable,
  tier: FlowTier,
  opts: LoopArcOpts = {},
): string | null {
  return loopArc(from, fromPort, to, toPort, lane, defs, tier, opts)?.d ?? null;
}

/** A polyline with each interior corner rounded by a quadratic of radius `r`,
 *  shrunk to half the shorter neighbouring segment so a short stub never
 *  overshoots. The corners are axis-aligned, so every coordinate stays a
 *  whole number when the anchors are. Same separators as `edgePath`. */
function roundedPath(pts: readonly Point[], r: number): string {
  let d = `M${pts[0].x} ${pts[0].y}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const a = pts[i - 1];
    const b = pts[i];
    const c = pts[i + 1];
    const lin = Math.hypot(b.x - a.x, b.y - a.y);
    const lout = Math.hypot(c.x - b.x, c.y - b.y);
    const k = Math.min(r, lin / 2, lout / 2);
    if (!(k > 0)) {
      d += ` L${b.x} ${b.y}`;
      continue;
    }
    const sx = b.x - ((b.x - a.x) / lin) * k;
    const sy = b.y - ((b.y - a.y) / lin) * k;
    const ex = b.x + ((c.x - b.x) / lout) * k;
    const ey = b.y + ((c.y - b.y) / lout) * k;
    d += ` L${sx} ${sy} Q${b.x} ${b.y}, ${ex} ${ey}`;
  }
  const z = pts[pts.length - 1];
  return `${d} L${z.x} ${z.y}`;
}

// -------------------------------------------------------------- fit to view
/** Pan + zoom that frames every node in `rect` (prototype lines 1022-1031).
 *
 *  Returns null for an empty node set: the prototype's `fit()` bails on
 *  `!ns.length` and changes nothing, and there is no defined framing for a
 *  graph with no bounding box. Null means "keep the current view" — which is
 *  also the only answer that cannot be a NaN pan, since the bbox would come
 *  from Math.min() of nothing (+Infinity) and every subsequent term would be
 *  Infinity or NaN. A blank new flow gets `zoom 0.95` and no fit at all
 *  (§D.2), so this path is reached, not theoretical.
 *
 *  ⚠ §D.2's snippet hardcodes the card width as 188 while the prototype calls
 *  `nodeW()`, which is 150 on phone — the two disagree only on the phone
 *  CANVAS tab, where fit IS reachable. Ambiguous; resolved toward the
 *  prototype (`nodeW(tier)`), because at tablet/desktop it reduces to exactly
 *  the contract's 188 and reproduces the worked M16 numbers, while 188 on a
 *  150px card would frame 38px of empty space per node on a phone. */
export function fitView(
  nodes: readonly FlowNodeRec[],
  rect: Rect,
  defs: PortTable,
  tier: FlowTier,
): FitView | null {
  if (nodes.length === 0) return null;

  const w = nodeW(tier);
  const heights = nodes.map((n) => nodeLayoutHeight(n, defs));
  const x0 = Math.min(...nodes.map((n) => n.x)) - FIT_PAD;
  const y0 = Math.min(...nodes.map((n) => n.y)) - FIT_PAD;
  const x1 = Math.max(...nodes.map((n) => n.x + w)) + FIT_PAD;
  const y1 = Math.max(...nodes.map((n, i) => n.y + heights[i])) + FIT_PAD;

  // Both spans are >= 2·FIT_PAD + one card, so neither divisor can be zero.
  const spanX = x1 - x0;
  const spanY = y1 - y0;
  const zoom = Math.min(
    FIT_ZOOM_MAX,
    Math.max(
      ZOOM_MIN,
      Math.min(rect.width / spanX, (rect.height - FIT_SCALE_RESERVE_Y) / spanY),
    ),
  );

  return {
    zoom,
    pan: {
      x: (rect.width - spanX * zoom) / 2 - x0 * zoom,
      // The pan reserves 34px where the scale reserved 40 — see the constants.
      y: (rect.height - FIT_PAN_RESERVE_Y - spanY * zoom) / 2 - y0 * zoom,
    },
  };
}
