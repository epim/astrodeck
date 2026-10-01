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
//
// THE SAME IS TRUE OF THE LANE CHECK AND THE SELF-WIRE REFUSAL (#197). A drag
// refuses both BEFORE `flowsConnect` is ever called (`resolveWireDrop`'s own
// rules 3 and 4), so neither case reaches the store from a drag. Tap-to-wire
// (`flowsTapPort`) has no resolver of its own and calls `flowsConnect`
// directly, so without a second gate THERE a flow output tapped onto an event
// input would wire, and a stage tapped into itself would too — the defect
// #197 found. `flowsConnect` now runs `laneMismatchRefusal` and a bare `from
// === to` test itself, so every gesture gets the same answer whether or not
// it passed through a resolver first; `laneMismatchRefusal` is exported here
// so EVERY caller shares its sentence instead of spelling it out again. Rule
// 3 (self-wire) is still a silent one-line check each resolver keeps for
// itself — there is no sentence to share, since a drag never shows one. Rule
// 4 (lane mismatch) is not: `FlowCanvas.tsx`'s classic resolver and
// `canvasModel.ts`'s #/next one both call this function directly (backlog
// WP-36, #197 follow-up, 2026-09-30 — canvasModel.ts used to spell its own
// copy of the sentence instead).

import { NODE_DEFS } from "./nodeDefs";
import type { PortDir } from "./geometry";
import type { FlowEdgeRec, FlowNodeRec, PortKind } from "./flowsTypes";

/** The toast tap-to-wire shows for a self-wire (#197, `flowsConnect`). A drag
 *  refuses the same case SILENTLY (`resolveWireDrop` rule 3, each resolver's
 *  own one-line check), run before `flowsConnect` is ever called, so this
 *  sentence is only ever seen from a tap: an operator who taps a second port
 *  on the stage they just armed meant it, unlike a drag that ends where it
 *  started, and the asymmetry is deliberate. */
export const SELF_WIRE_REFUSAL = "Can't wire a stage to itself";

/** The sentence for a lane mismatch — a flow port wired to an event port, or
 *  the reverse — the same words `resolveWireDrop` toasts on a drag (rule 4),
 *  in both the classic and the #/next resolver, which call this function
 *  directly rather than keeping their own copy of it (backlog WP-36, #197
 *  follow-up, 2026-09-30). Null when `kOut` and `kIn` agree, or when either
 *  is unknown: a port a saved graph names that the vocabulary has since
 *  dropped is the caller's own refusal to make, never this one's. */
export function laneMismatchRefusal(
  kOut: PortKind | null, kIn: PortKind | null,
): string | null {
  if (!kOut || !kIn || kOut === kIn) return null;
  return `${kOut === "flow" ? "Flow" : "Event"} output can't feed `
    + `${kIn === "flow" ? "a flow" : "an event"} input`;
}

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
