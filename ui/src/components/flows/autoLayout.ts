// autoLayout.ts — the phone FLOW tab's auto-laid zigzag graph.
//
// The phone FLOW tab (§C.5 / README §5: "the happy medium") does not pan, zoom
// or free-drag. It lays the graph out for one thumb: flow-lane stages in
// topological order alternating between a left and a right column, then one
// "rule cluster" per event source, then anything still unplaced. The page just
// scrolls, so the layout has to produce a total height as well as positions.
//
// Every number here is transcribed from the approved prototype
// ("AstroDeck Flows.dc.html", `computeAutoLayout()` lines 840-859 and
// `flowOrder()` lines 1057-1069) and ratified by MILESTONE2-CONTRACT §C.15.
// Nothing is measured: §C.5 is explicit that "the spacing model is the formula"
// and that measuring the DOM to lay out is forbidden — the container must be
// able to reserve its scroll height before a single card renders.
//
// Deliberately decoupled from `nodeDefs.ts`, exactly as `geometry.ts` is: the
// node vocabulary arrives as a `LayoutPortTable` argument and `NODE_DEFS`
// satisfies it structurally, so call sites just pass it. That keeps this file
// (and its test) runnable with nothing else from the Flows surface present.
import type { FlowGraphRec, FlowNodeRec, FlowNodeType, PortKind } from "./flowsTypes";
import { NODE_HEADER_H, NODE_W_PHONE, PORT_ROW_H, nodeRows, type Point } from "./geometry";

/** What auto-layout needs from a `NodeDef`: the port ids (for edge lookup),
 *  their `kind` (flow vs event — the two passes are completely different), and
 *  the counts (row height). A superset of `geometry.NodePortShape`, so a value
 *  of this type can be handed straight to `geometry.nodeRows`. */
export interface LayoutPortShape {
  readonly ins: readonly { readonly id: string; readonly kind: PortKind }[];
  readonly outs: readonly { readonly id: string; readonly kind: PortKind }[];
}

/** `NODE_DEFS` from nodeDefs.ts satisfies this structurally. */
export type LayoutPortTable = Readonly<Record<FlowNodeType, LayoutPortShape>>;

export interface AutoLayout {
  /** Content-space position per node id. Content space is the scrolling
   *  graph's own box: the reference capture's screen y is content y + 54,
   *  the 54px phone header row above it (§C.15). */
  pos: Record<string, Point>;
  /** Total scroll height the container must reserve. */
  height: number;
}

// ------------------------------------------------------------------ constants
/** Left/right margin. Prototype line 841 (`const pad = 14`). */
export const AUTO_PAD: number = 14;

/** The container width is clamped before the columns are derived. 300 keeps a
 *  320px-wide phone from producing overlapping columns; 700 is the same
 *  threshold `device()` uses to stop calling a viewport a phone (line 833), so
 *  a wide container does not stretch the zigzag into two lonely edges. */
export const AUTO_MIN_W: number = 300;
export const AUTO_MAX_W: number = 700;

/** Floor on the gap between the two columns (prototype line 843). Dead at any
 *  clamped width — `300 - 14 - 150 = 136` already exceeds `14 + 56` — but it is
 *  reproduced because it is the prototype's own guard against a narrower node
 *  width being introduced later. */
export const AUTO_MIN_COL_SPAN: number = 56;

/** First row's content y: `pad + 4` (line 845) = 18. The +4 has no separate
 *  source; it is part of that one expression. */
export const AUTO_FIRST_Y: number = AUTO_PAD + 4;

/** Vertical gap between consecutive FLOW-LANE cards (line 846).
 *
 *  ⚠ README §5 says "34px vertical gaps" without scoping it, but the prototype
 *  uses 34 for the flow lane ONLY and 22 for the other two passes (lines
 *  855-858). §G-22 records the disagreement; the contract rules for the code.
 *  The reference capture only shows the flow lane, so 34 is the number the
 *  M16 y-series (18 / 117 / 236 / 355) actually pins. */
export const FLOW_LANE_GAP: number = 34;

/** Vertical gap inside a rule cluster and between unwired stragglers. */
export const CLUSTER_GAP: number = 22;

/** Slack below the last card: room for the pinned `+ ADD STAGE` and the bottom
 *  tab bar's safe-area inset (line 859, `this.autoH = y + 90`). */
export const AUTO_TAIL_H: number = 90;

/** Card height for SPACING purposes: `37 + rows·20 + 8` (line 844).
 *
 *  ⚠ This is 18px smaller than `geometry.nodeLayoutHeight`'s `+26`, and both
 *  are larger than the rendered box (the DUSK WINDOW card measures 62px in
 *  `10-phone-flow-autograph-390px.png` against H = 65). §C.5: "Reproduce both,
 *  in their own call sites." Unifying them would move every card in the
 *  reference capture. */
const CARD_PAD_H = 8;

/** Sort key offset for an event source that the flow lane did not place
 *  (prototype line 849, `1e9 + a.y`). Sorting by canvas y keeps an unplaced
 *  source ordered against its peers while guaranteeing it sorts after every
 *  placed one — placed nodes carry a real content y, which is small. */
const UNPLACED_SORT_BASE = 1e9;

// ------------------------------------------------------------------ helpers
/** The declared kind of one named port, or null when the type or the port id is
 *  unknown.
 *
 *  Null is not the same as "flow": a graph off the wire can name a port the
 *  vocabulary has since dropped, and both passes below deliberately ignore such
 *  an edge rather than guess which lane it belonged to. Guessing "flow" would
 *  splice a phantom stage into the run cursor's path. */
export function portKindOf(
  defs: LayoutPortTable,
  type: FlowNodeType,
  portId: string,
  dir: "in" | "out",
): PortKind | null {
  const d = defs[type];
  if (!d) return null;
  const p = (dir === "in" ? d.ins : d.outs).find((q) => q.id === portId);
  return p ? p.kind : null;
}

/** The height auto-layout advances by after a card. See `CARD_PAD_H`. */
export function layoutHeight(node: FlowNodeRec, defs: LayoutPortTable): number {
  return NODE_HEADER_H + nodeRows(node, defs) * PORT_ROW_H + CARD_PAD_H;
}

/** The two column x's for a given container width.
 *
 *  ⚠ `containerWidth` must be the CONTAINER, not the window. §C.15: at 667px
 *  (phone landscape) the app's 72px rail is present and `p-4` takes 32 more,
 *  leaving ~563px — measuring `window.innerWidth` there would push the right
 *  column off the visible area.
 *
 *  A non-finite width (a container measured before it has been laid out) falls
 *  back to the clamp floor, which is exactly where a 0-width container already
 *  lands. NaN would otherwise poison every x in the result with no error. */
export function layoutColumns(containerWidth: number): { colL: number; colR: number } {
  const w = Number.isFinite(containerWidth) ? containerWidth : AUTO_MIN_W;
  const wc = Math.max(AUTO_MIN_W, Math.min(w, AUTO_MAX_W));
  const colL = AUTO_PAD;
  const colR = Math.max(colL + AUTO_MIN_COL_SPAN, wc - AUTO_PAD - NODE_W_PHONE);
  return { colL, colR };
}

// ------------------------------------------------------------------ ordering
/** Topological order over the FLOW-lane edges only.
 *
 *  Event edges are excluded on purpose: they are "whenever", not "then", and
 *  the run cursor never travels along one (nodes.py's own module docstring
 *  splits the two across different engine machinery). Threading an event edge
 *  into this order would draw a rule as if it were a stage in the night.
 *
 *  Only nodes that own at least one flow port take part; a pure event node
 *  (CLOUD WATCH, NOTIFY, SAFETY MONITOR …) is placed by the cluster pass
 *  instead. Kahn's algorithm, seeded from indegree 0 sorted by canvas (x, y) —
 *  the graph's own left-to-right reading order — so the result is a pure
 *  function of the record and never depends on object key order.
 *
 *  ⚠ A node inside a flow-edge CYCLE never reaches indegree 0 and is therefore
 *  ABSENT from this list. That is the prototype's behaviour and it does not
 *  hang: every iteration consumes one queue slot, and a node is enqueued at
 *  most once per incoming edge, so the loop is bounded by |V| + |E|.
 *  `computeAutoLayout`'s final pass catches the omitted nodes, so a cyclic
 *  graph still lays out — it just does not zigzag. */
export function flowOrder(graph: FlowGraphRec, defs: LayoutPortTable): FlowNodeRec[] {
  const byId = new Map<string, FlowNodeRec>();
  for (const n of graph.nodes) byId.set(n.id, n);

  // Adjacency is precomputed rather than re-filtered per node (the prototype
  // does `flowEdges.filter(...)` inside the loop). Insertion order matches the
  // edge array, so the emitted order is identical — just not quadratic.
  const outEdges = new Map<string, string[]>();
  const indeg = new Map<string, number>();

  const nodes = graph.nodes.filter((n) => {
    const d = defs[n.type];
    return !!d && (d.ins.some((p) => p.kind === "flow") || d.outs.some((p) => p.kind === "flow"));
  });
  for (const n of nodes) indeg.set(n.id, 0);

  for (const e of graph.edges) {
    const from = byId.get(e.from);
    if (!from || portKindOf(defs, from.type, e.fromPort, "out") !== "flow") continue;
    const list = outEdges.get(e.from);
    if (list) list.push(e.to);
    else outEdges.set(e.from, [e.to]);
    // Only edges landing on a flow-capable node count. A duplicate edge counts
    // twice on both sides of the ledger, so it cancels out (as it does in the
    // prototype) rather than stranding its target.
    const cur = indeg.get(e.to);
    if (cur != null) indeg.set(e.to, cur + 1);
  }

  const queue = nodes
    .filter((n) => indeg.get(n.id) === 0)
    // Array.prototype.sort has been required to be stable since ES2019, so two
    // nodes at the same (x, y) keep their order in `graph.nodes`. That is a
    // deterministic tiebreak the record itself supplies; inventing one (by id,
    // say) would reorder graphs the prototype ordered differently.
    .sort((a, b) => a.x - b.x || a.y - b.y);

  const out: FlowNodeRec[] = [];
  const seen = new Set<string>();
  for (let head = 0; head < queue.length; head++) {
    const n = queue[head];
    if (seen.has(n.id)) continue;
    seen.add(n.id);
    out.push(n);
    for (const toId of outEdges.get(n.id) ?? []) {
      const m = byId.get(toId);
      if (!m || seen.has(m.id)) continue;
      const left = (indeg.get(m.id) ?? 0) - 1;
      indeg.set(m.id, left);
      if (left <= 0) queue.push(m);
    }
  }
  return out;
}

// ------------------------------------------------------------------ layout
/** Position every node of `graph` for the phone FLOW tab, plus the total
 *  scroll height.
 *
 *  Three passes, in this order — the order is the whole design, because each
 *  pass only places nodes the earlier ones left alone:
 *
 *   1. **Flow lane.** `flowOrder()`, alternating left/right column, 34px gaps.
 *      This is the night, read top to bottom.
 *   2. **Rule clusters.** One per event SOURCE, ordered by where the flow lane
 *      put it (unplaced sources last, by canvas y). The source sits in one
 *      column and its not-yet-placed targets stack in the opposite one, 22px
 *      apart, so a fan-out reads spatially: CLOUD WATCH visibly branches to
 *      HOLD + QUEUE + NOTIFY. An already-placed target just gets a wire.
 *   3. **Stragglers.** Anything still unplaced — including every node inside a
 *      flow-edge cycle — stacks in the left column, 22px apart.
 *
 *  `containerWidth` is the graph container's width. Node width is fixed at the
 *  phone's 150px: this layout only ever runs when `device() === "phone"` and
 *  the FLOW tab is showing (prototype `autoMode()`, line 838), and §C.5 states
 *  node width is device-based, not tab-based. */
export function computeAutoLayout(
  graph: FlowGraphRec,
  containerWidth: number,
  defs: LayoutPortTable,
): AutoLayout {
  const { colL, colR } = layoutColumns(containerWidth);
  const byId = new Map<string, FlowNodeRec>();
  for (const n of graph.nodes) byId.set(n.id, n);

  // A Map, not a plain object: a node id of "constructor" or "toString" reads
  // truthy off Object.prototype, and every "has this been placed yet?" test
  // below would answer yes for a node nothing had placed.
  const pos = new Map<string, Point>();
  let y = AUTO_FIRST_Y;

  // 1 — the flow lane
  flowOrder(graph, defs).forEach((n, i) => {
    pos.set(n.id, { x: i % 2 ? colR : colL, y });
    y += layoutHeight(n, defs) + FLOW_LANE_GAP;
  });

  // 2 — rule clusters, one per event source
  const eventEdges = graph.edges.filter((e) => {
    const from = byId.get(e.from);
    return !!from && portKindOf(defs, from.type, e.fromPort, "out") === "event";
  });
  const sortKey = (n: FlowNodeRec): number => {
    const p = pos.get(n.id);
    return p ? p.y : UNPLACED_SORT_BASE + n.y;
  };
  const sources = [...new Set(eventEdges.map((e) => e.from))]
    .map((id) => byId.get(id))
    .filter((n): n is FlowNodeRec => !!n)
    // Sorted ONCE, against the flow lane's positions — not re-read as the loop
    // places things, which would make a cluster's order depend on the cluster
    // before it.
    .sort((a, b) => sortKey(a) - sortKey(b));

  for (const src of sources) {
    let srcPos = pos.get(src.id);
    if (!srcPos) {
      srcPos = { x: colL, y };
      pos.set(src.id, srcPos);
    }
    const tx = srcPos.x === colL ? colR : colL;
    // The cluster starts level with its source, never above the running
    // cursor — a source the flow lane placed near the top must not stack its
    // targets back up over stages already laid out.
    let ty = Math.max(y, srcPos.y);
    const targets = [...new Set(eventEdges.filter((e) => e.from === src.id).map((e) => e.to))]
      .map((id) => byId.get(id))
      .filter((t): t is FlowNodeRec => !!t && !pos.has(t.id));
    for (const t of targets) {
      pos.set(t.id, { x: tx, y: ty });
      ty += layoutHeight(t, defs) + CLUSTER_GAP;
    }
    y = Math.max(ty, srcPos.y + layoutHeight(src, defs) + CLUSTER_GAP);
  }

  // 3 — unwired stragglers (and anything a cycle kept out of the flow lane)
  for (const n of graph.nodes) {
    if (pos.has(n.id)) continue;
    pos.set(n.id, { x: colL, y });
    y += layoutHeight(n, defs) + CLUSTER_GAP;
  }

  return { pos: Object.fromEntries(pos), height: y + AUTO_TAIL_H };
}
