// panelLane.ts - which TARGET a stage belongs to, and which stage is the last
// one of that block's panel lane (#151, #189; spec 2026-09-23 flows mosaic,
// 1.4 and 1.5). Pure: no store, no React, no DOM.
//
// A SECOND TRANSCRIPTION OF server/astrodeck/flows/compile.py, on purpose and
// under the same guard as nodeDefs.ts. The compile decides which stages a
// mosaic's panels own (`owner_of`) and where the loop wire must leave
// (`lane_tail`); the editor needs the same two answers the moment a wire is
// drawn, to carry the panel loop to a stage the operator appends (1.5 item 6),
// and no route serves them. Two copies drift, so __tests__/panelLane.test.ts
// grades this file against server/tests/fixtures/panel_lane_cases.json, the
// worked cases the Python tests grade compile.py against. Change the rule
// there first; this file follows.
//
// THE WALK IS A CHAIN, NOT A SET. `ownerOf` climbs a stage's single flow
// parent, then that parent's, through lane nodes only, and stops at the first
// TARGET or POOL. The doctor's upstream walk (doctor.py
// `_flow_upstream_types`) collects every type above a node through ANY node,
// which answers "did the cursor pass a guider?" but cannot name an owner: on
// TARGET -> CYCLE -> DOME -> CAPTURE it finds the TARGET above the DOME and
// hands the CAPTURE to every panel. Here the DOME ends the lane and the
// CAPTURE is nobody's (M13).

import { NODE_DEFS } from "./nodeDefs";
import type { FlowEdgeRec, FlowNodeRec } from "./flowsTypes";

/** What every function here reads: the graph's nodes and wires. A
 *  `FlowGraphRec` is one; `settings` plays no part in a lane. */
export interface LaneGraph {
  nodes: readonly FlowNodeRec[];
  edges: readonly FlowEdgeRec[];
}

/** LANE NODES (compile.py `LANE_TYPES`, spec 1.5 item 1): the stages that
 *  happen TO a panel. Legacy SLEW is one because every saved flow still draws
 *  it between the TARGET and its stages; if it ended the lane, all of those
 *  stages would belong to no target. Every other node with a flow port ends a
 *  lane. */
export const LANE_TYPES: ReadonlySet<string> =
  new Set(["autofocus", "guide", "slew", "capture", "cycle"]);

/** The blocks a lane hangs off (compile.py `OWNER_TYPES`). */
export const OWNER_TYPES: ReadonlySet<string> = new Set(["target", "pool"]);

/** The loop wire's two ends, `<tail>.pass -> <block>.next` (spec 1.4). Matched
 *  as strings, as compile.py does, so a wire the operator drew is read the
 *  same whatever the vocabulary of the build reading it. */
export const PASS_PORT = "pass";
export const NEXT_PORT = "next";

/** A whole number as Python's `int()` reads a string: digits with an optional
 *  sign and surrounding space. "3.0" and "1e1" are NOT, because `int("3.0")`
 *  raises and compile.py's `_num` then answers its default. */
const PY_INT = /^\s*[+-]?\d+\s*$/;

/** One side of a TARGET's grid, read as compile.py `_grid_dim` reads it:
 *  missing, text that is not a whole number, 0, a negative, 1.9, NaN and
 *  infinity all read as 1, the meaning every flow saved before mosaics has. */
function gridDim(v: string | number | undefined): number {
  const n = typeof v === "number" ? v
    : typeof v === "string" && PY_INT.test(v) ? Number(v.trim())
      : NaN;
  return Number.isInteger(n) && n >= 1 ? n : 1;
}

/** True for a TARGET with more than one panel (compile.py `is_multi_panel`).
 *  A POOL has no grid, so rows and cols on one never make it a mosaic. */
export function isMultiPanel(node: FlowNodeRec): boolean {
  if (node.type !== "target") return false;
  const p = node.params ?? {};
  return gridDim(p.rows) * gridDim(p.cols) > 1;
}

/** A port's lane on a node, or null when the type or the port is unknown
 *  (server `port_kind`). */
function kindOf(n: FlowNodeRec, portId: string, dir: "in" | "out") {
  const def = NODE_DEFS[n.type];
  if (!def) return null;
  const p = (dir === "in" ? def.ins : def.outs).find((q) => q.id === portId);
  return p ? p.kind : null;
}

interface LaneIndex {
  byId: Map<string, FlowNodeRec>;
  /** Each node's FLOW parents, one entry per parent node. */
  parents: Map<string, FlowNodeRec[]>;
}

/** compile.py `_lane_index`: nodes by id (a duplicate id keeps its first
 *  node) and flow parents by child. A wire counts only when it is a FLOW wire
 *  at BOTH ends: a flow output into an event input is refused by validation,
 *  and reading it as a step of the cursor would make the TARGET it enters
 *  through `next` the child of its own tail. The same parent wired twice is
 *  one parent. */
function laneIndex(g: LaneGraph): LaneIndex {
  const byId = new Map<string, FlowNodeRec>();
  for (const n of g.nodes) if (!byId.has(n.id)) byId.set(n.id, n);
  const parents = new Map<string, FlowNodeRec[]>();
  for (const e of g.edges) {
    const src = byId.get(e.from);
    const dst = byId.get(e.to);
    if (!src || !dst) continue;
    if (kindOf(src, e.fromPort, "out") !== "flow" || kindOf(dst, e.toPort, "in") !== "flow") continue;
    const ups = parents.get(dst.id);
    if (!ups) parents.set(dst.id, [src]);
    else if (!ups.some((p) => p.id === src.id)) ups.push(src);
  }
  return { byId, parents };
}

/** compile.py `_walk_to_owner`: the owner, and how many wires the walk climbed
 *  to reach it.
 *
 *  THE SEEN-SET IS WHAT MAKES IT RETURN. Two stages feeding each other with
 *  nothing feeding them each have exactly one flow parent, so a walk with no
 *  memory goes round them for ever. Validation refuses that loop at save, and
 *  `flowsConnect` refuses the wire (#149), but a graph saved before either
 *  still opens in the editor. */
function walkToOwner(id: string, idx: LaneIndex): [FlowNodeRec | null, number] {
  const node = idx.byId.get(id);
  if (!node || !LANE_TYPES.has(node.type)) return [null, 0];
  const seen = new Set<string>([node.id]);
  let cur = node;
  let depth = 0;
  for (;;) {
    const ups = idx.parents.get(cur.id) ?? [];
    // None: the chain ran out. Two: two answers, and picking one would hand
    // the stage to a block by wire order (only a hand-built graph has two).
    if (ups.length !== 1) return [null, 0];
    const up = ups[0];
    depth += 1;
    if (OWNER_TYPES.has(up.type)) return [up, depth];
    if (!LANE_TYPES.has(up.type) || seen.has(up.id)) return [null, 0];
    seen.add(up.id);
    cur = up;
  }
}

/** The TARGET or POOL a lane node belongs to, or null (compile.py `owner_of`,
 *  spec 1.5 item 3). Null when the walk meets any other node type, runs out
 *  of parents or meets two, and for a node that is not itself a lane node. */
export function ownerOf(g: LaneGraph, nodeId: string): FlowNodeRec | null {
  return walkToOwner(nodeId, laneIndex(g))[0];
}

/** compile.py `_lane_members`: `[depth, node]` for every lane node the block
 *  owns, nearest first, then canvas x, y and id. Defined by OWNERSHIP, so the
 *  lane this file reports is exactly the set of stages `ownerOf` gives the
 *  block, even in a graph built outside the editor. */
function laneMembers(g: LaneGraph, ownerId: string): [number, FlowNodeRec][] {
  const idx = laneIndex(g);
  const out: [number, FlowNodeRec][] = [];
  for (const n of idx.byId.values()) {
    const [up, depth] = walkToOwner(n.id, idx);
    if (up && up.id === ownerId) out.push([depth, n]);
  }
  return out.sort(([da, a], [db, b]) =>
    da - db || a.x - b.x || a.y - b.y || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

/** The block's panel lane (compile.py `panel_lane`, spec 1.5 item 2): every
 *  lane node it owns, nearest first. For an unbranched lane that is chain
 *  order. */
export function panelLane(g: LaneGraph, ownerId: string): FlowNodeRec[] {
  return laneMembers(g, ownerId).map(([, n]) => n);
}

/** True when the lane is not one chain (compile.py `lane_branched`, spec 1.5
 *  item 4, M12). Every member's one parent is the block or another member, so
 *  the lane is a tree rooted at the block, and a tree is a chain exactly when
 *  no two members sit at the same distance from the root. */
export function laneBranched(g: LaneGraph, ownerId: string): boolean {
  const depths = laneMembers(g, ownerId).map(([d]) => d);
  return new Set(depths).size !== depths.length;
}

/** The lane's last stage, the one the loop wire must leave (compile.py
 *  `lane_tail`, spec 1.5 items 4-5). Null for an empty lane, and null for a
 *  branched one, where "the last stage" has two answers. Any lane node can be
 *  the tail; only CAPTURE LOOP and FILTER CYCLE have a `pass` output to give. */
export function laneTail(g: LaneGraph, ownerId: string): FlowNodeRec | null {
  const members = laneMembers(g, ownerId);
  if (members.length === 0) return null;
  const depths = members.map(([d]) => d);
  if (new Set(depths).size !== depths.length) return null;
  return members[members.length - 1][1];
}

/** The block's loop wires (compile.py `loop_wires`, spec 1.4 item 1): `pass`
 *  wires from its lane's TAIL into its own `next`. A pass wire from an earlier
 *  stage is not one (M12). Structural only: whether the block has more than
 *  one panel is the caller's question. */
export function loopWires(g: LaneGraph, ownerId: string): FlowEdgeRec[] {
  const tail = laneTail(g, ownerId);
  if (!tail) return [];
  return g.edges.filter((e) => e.from === tail.id && e.fromPort === PASS_PORT
    && e.to === ownerId && e.toPort === NEXT_PORT);
}
