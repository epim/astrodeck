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
