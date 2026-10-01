// flowLoop.ts - the rule that keeps a flow lane a lane: a FLOW wire may not
// close a circle (#149; spec 2026-09-23 section 1.4 item 5, S0 item 2). Pure:
// no store, no React, no DOM, so both editors and the store can share it.
//
// WHY IT EXISTS. A flow wire means "then", and there is exactly one run cursor.
// A drawn loop - FILTER CYCLE "complete" back into TARGET "arm" - reads on the
// canvas as "and round again", but the compiler walks the flow lane in
// topological order, a stage on a circle has no place in that order, and
// `flow_order` DROPS every stage on it (server compile.py). None of those
// stages shoots a frame, and the doctor - which reasons along wires that are
// all still there - has nothing to say.
// The server refuses such a graph at save and at /run (FlowGraph.
// validation_errors); this module refuses the WIRE, at the moment it is drawn,
// with the server's own sentence. A refusal first met at SAVE is a refusal
// about a wire the operator drew minutes ago and can no longer point at.
//
// EVENT WIRES ARE NEVER REFUSED, backward or not. An event wire means
// "whenever" and runs through the rule machinery, never the cursor, and the
// grammar leans on backward ones: REPORT "target done" -> POOL "advance" is how
// the campaign example hands out its next target, and the mosaic's panel loop
// (`pass -> next`, spec 1.4) is an event wire by design. For the same reason
// the walk follows flow wires only: a circle that closes through an event wire
// is not a circle the cursor can travel.
//
// A FLOW WIRE IS ONE WHOSE TWO PORTS ARE BOTH FLOW PORTS - the test the server's
// back-edge walk applies (`FlowGraph._flow_back_edges`), so the canvas refuses
// exactly the wires a save would. A wire whose ports disagree is the lane
// rule's to refuse, in its own words, never this one's.
//
// THREE CALLERS, ONE RULE. Both drop resolvers (the classic `FlowCanvas` and
// the #/next `canvasModel`) take the check as an optional argument that their
// surface builds from the live graph, and `flowsConnect` asks it itself,
// because tap-to-wire reaches the store without passing through any resolver.

import { NODE_DEFS } from "./nodeDefs";
import type { PortDir } from "./geometry";
import type { FlowEdgeRec, FlowNodeRec, PortKind } from "./flowsTypes";

/** The refusal, as a template. IDENTICAL to the server's constant (S0 task T1,
 *  `flows/models.py`), placeholders included, so the canvas toast and the 422
 *  carry one sentence. Filled with NODE_DEFS labels, source first. */
export const FLOW_LOOP_REFUSAL =
  "this flow loops back on itself at {src} -> {dst}; a flow lane runs once";

/** A wire that has been drawn but not yet added: an edge with no id. */
export type ProposedWire = Pick<FlowEdgeRec, "from" | "fromPort" | "to" | "toPort">;

/** A port's lane, read off a node list, or null when the node or the port is
 *  unknown. Nullable on purpose: a saved graph can name a port the vocabulary
 *  has since dropped, and "unknown" must never be read as a matching lane. */
export function portKindOf(
  nodes: readonly FlowNodeRec[],
  nodeId: string,
  portId: string,
  dir: PortDir,
): PortKind | null {
  return kindOn(nodes.find((x) => x.id === nodeId), portId, dir);
}

function kindOn(n: FlowNodeRec | undefined, portId: string, dir: PortDir): PortKind | null {
  const def = n ? NODE_DEFS[n.type] : undefined;
  if (!def) return null;
  const p = (dir === "in" ? def.ins : def.outs).find((q) => q.id === portId);
  return p ? p.kind : null;
}

/** The refusal for `wire`, or null when adding it closes no flow loop.
 *
 *  A flow wire (both ports flow ports, see the header) closes a loop exactly
 *  when its destination already reaches its source along flow wires; a wire
 *  from a stage into itself is the smallest case of that.
 *
 *  MEASURED ON THE GRAPH AS `flowsConnect` WOULD LEAVE IT, which is the edges as
 *  they stand. `flowsConnect` removes at most one wire, and only one INTO the
 *  destination's input, and a walk that starts at the destination never needs a
 *  wire back into it: any path that used one would pass through the destination
 *  twice and has a shorter path without it. So the incumbent's removal cannot
 *  change the answer, and the walk does not re-derive the store's replacement
 *  rule to prove it.
 *
 *  Only THIS wire is judged. A loop the graph already carries (a flow saved
 *  before the server refused them) does not refuse an unrelated wire: the
 *  operator has to be able to go on editing while they delete it. */
export function flowLoopRefusal(
  nodes: readonly FlowNodeRec[],
  edges: readonly FlowEdgeRec[],
  wire: ProposedWire,
): string | null {
  // One map, so judging all 800 permitted edges is not 800 scans of the nodes.
  const byId = new Map(nodes.map((n) => [n.id, n] as const));
  const isFlowWire = (w: ProposedWire): boolean =>
    kindOn(byId.get(w.from), w.fromPort, "out") === "flow"
    && kindOn(byId.get(w.to), w.toPort, "in") === "flow";
  if (!isFlowWire(wire)) return null;

  const next = new Map<string, string[]>();
  for (const e of edges) {
    if (!isFlowWire(e)) continue;
    const list = next.get(e.from);
    if (list) list.push(e.to);
    else next.set(e.from, [e.to]);
  }

  // Breadth-first from the destination. `seen` is what makes a graph that
  // already carries a loop terminate.
  const seen = new Set<string>([wire.to]);
  const queue = [wire.to];
  for (let i = 0; i < queue.length; i++) {
    const at = queue[i];
    if (at === wire.from) {
      // Both nodes are there and both types resolve: `isFlowWire(wire)` above
      // answered "flow" at each end, and `kindOn` answers that only for a node
      // on the graph whose type NODE_DEFS knows. Anything else returned null
      // at that gate, so the assertions below cannot see undefined.
      return FLOW_LOOP_REFUSAL
        // A function replacement, so a `$` in a label is never read as a
        // replacement pattern.
        .replace("{src}", () => labelOf(byId.get(wire.from)!))
        .replace("{dst}", () => labelOf(byId.get(wire.to)!));
    }
    for (const to of next.get(at) ?? []) {
      if (!seen.has(to)) {
        seen.add(to);
        queue.push(to);
      }
    }
  }
  return null;
}

/** The stage's vocabulary label. No id fallback: the only caller fills the
 *  sentence after the flow-wire gate, which a stage of unknown type never
 *  passes (see the call site), so a fallback here would be a branch nothing
 *  can reach. flowLoop.test.ts pins the gate that keeps it unreachable. */
function labelOf(n: FlowNodeRec): string {
  return NODE_DEFS[n.type].label;
}
