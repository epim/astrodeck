// flowGraphExtras.ts - the three graph edits the quick-session sheet makes to a
// flow the SERVER generated (hub-sky plan D.4, D.6, GAP-ANALYSIS 7).
//
// WHY THE CLIENT TOUCHES A GRAPH AT ALL. It normally must not: `wizard.py` owns
// the shape and a second generator is the drift this project keeps re-finding.
// But `POST /api/flows/quick` takes four answers and none of them is "add dusk
// flats", "queue darks afterwards" or "make this a pool". Those three are
// ADDITIVE edits to a graph that already exists and already passed the doctor,
// expressed with the vocabulary the server itself published (`nodes.py`, mirrored
// in `components/flows/nodeDefs.ts`), and the result is saved back through the
// same `PUT /api/flows/{id}` the canvas uses - so the compiler, not this file,
// still has the last word on whether the night is runnable.
//
// FOUR TRAPS, all of which produce a graph that looks right and is not:
//
//  1. THE EDGE KEY IS `from`, NOT `from_`. `FlowEdge` sets `alias="from"` and
//     FastAPI serialises by alias; an edge built with the Python attribute name
//     round-trips through the server and every wire on it vanishes with NO error
//     anywhere. `flowsTypes.ts:34-38` says so, and a test here asserts the key.
//  2. INSERTING A STAGE MEANS RE-POINTING THE EDGE THAT WAS THERE. Adding
//     DUSK FLATS after DUSK WINDOW without moving `dusk.window -> target.arm`
//     leaves the flow cursor taking the old wire and the flats node never runs -
//     a node on the canvas that does nothing is worse than no node.
//  3. `duskflats.params.window` CARRIES U+2212 MINUS AND U+2026 ELLIPSIS
//     ("Sun −2° … −8°"). `nodes.py` stores the identical string and the compiler
//     MATCHES ON IT, so an ASCII tidy-up stops the twilight window resolving and
//     the flats shoot at the wrong sun altitude. `default_params` is copied from
//     `NODE_DEFS`, so the characters come from the vocabulary, never from a
//     literal typed here.
//  4. A CALIBRATION QUEUE IS DRIVEN BY AN EVENT, NOT BY THE FLOW CURSOR. All
//     three of `calib`'s inputs are event ports and all three are optional; the
//     wire that starts it is `report.done -> calib.do`, which is the same event
//     the campaign mechanism uses. Chaining it onto the flow lane would be a
//     kind mismatch the models refuse as a 500.
//
// Every function is PURE: graph in, new graph out, nothing mutated, no fetch.

import { NODE_DEFS } from "../../../../components/flows/nodeDefs";
import type {
  FlowEdgeRec, FlowGraphRec, FlowNodeRec, FlowNodeType,
} from "../../../../components/flows/flowsTypes";

/** A fresh node id that cannot collide with the server's `n1, n2, …` minting or
 *  with a second call on the same graph. */
function mintNodeId(g: FlowGraphRec, type: FlowNodeType): string {
  const taken = new Set(g.nodes.map((n) => n.id));
  let i = 1;
  let id = `q-${type}`;
  while (taken.has(id)) { id = `q-${type}-${i}`; i += 1; }
  return id;
}

function mintEdgeId(g: FlowGraphRec, tag: string): string {
  const taken = new Set(g.edges.map((e) => e.id));
  let i = 1;
  let id = `qe-${tag}`;
  while (taken.has(id)) { id = `qe-${tag}-${i}`; i += 1; }
  return id;
}

/** The node vocabulary's own defaults for a type, copied so the caller's edits
 *  cannot reach back into `NODE_DEFS`. This is where the U+2212 window string
 *  comes from (trap 3). */
function defaultParams(type: FlowNodeType): Record<string, string | number> {
  return { ...NODE_DEFS[type].params };
}

function firstFlowOut(type: FlowNodeType): string | null {
  return NODE_DEFS[type].outs.find((p) => p.kind === "flow")?.id ?? null;
}

function firstFlowIn(type: FlowNodeType): string | null {
  return NODE_DEFS[type].ins.find((p) => p.kind === "flow")?.id ?? null;
}

function nodeOfType(g: FlowGraphRec, type: FlowNodeType): FlowNodeRec | null {
  return g.nodes.find((n) => n.type === type) ?? null;
}

/** Place a new card clear of the ones already on the canvas. Cosmetic on the
 *  phone (the flow card lays itself out) and load-bearing the moment the same
 *  flow is opened on the tablet canvas, where two nodes at one point are one
 *  node as far as the operator can tell. */
function freeSpot(g: FlowGraphRec, y: number): { x: number; y: number } {
  const maxX = g.nodes.reduce((m, n) => Math.max(m, n.x), 0);
  return { x: maxX + 228, y };
}

/**
 * DUSK FLATS between the window opening and whatever the window fed.
 *
 * `dusk.window -> X` becomes `dusk.window -> duskflats.run` and
 * `duskflats.done -> X`. When the graph has no DUSK WINDOW - which a quick flow
 * always does, but a hand-edited one might not - the graph comes back unchanged
 * rather than gaining an orphan: a flats node wired to nothing would shoot no
 * flats and still put a stage on the card claiming it would.
 */
export function withDuskFlats(g: FlowGraphRec): FlowGraphRec {
  if (nodeOfType(g, "duskflats")) return g;
  const dusk = nodeOfType(g, "dusk");
  if (!dusk) return g;
  const outPort = firstFlowOut("dusk");
  const runPort = firstFlowIn("duskflats");
  const donePort = firstFlowOut("duskflats");
  if (!outPort || !runPort || !donePort) return g;

  const id = mintNodeId(g, "duskflats");
  const node: FlowNodeRec = {
    id,
    type: "duskflats",
    ...freeSpot(g, 60),
    params: defaultParams("duskflats"),
  };

  // The wires the window currently drives. Usually exactly one (the lane is a
  // chain), but every one of them is re-pointed: leaving a second edge on
  // `dusk.window` would fork the run cursor past the flats.
  const downstream = g.edges.filter((e) => e.from === dusk.id && e.fromPort === outPort);
  const edges: FlowEdgeRec[] = g.edges.map((e) =>
    downstream.includes(e) ? { ...e, from: id, fromPort: donePort } : e,
  );
  edges.push({
    id: mintEdgeId(g, "flats"),
    from: dusk.id,
    fromPort: outPort,
    to: id,
    toPort: runPort,
  });

  return { nodes: [...g.nodes, node], edges };
}

/**
 * A CALIBRATION QUEUE that starts when the night's report says the target is
 * done.
 *
 * Darks and bias `Always` rather than the shipped `If library stale`: the
 * operator ticked "darks after", which is a statement that tonight's library is
 * not trusted. Flats `Skip`, because flats need a panel wired to `calib.panel`
 * and this graph has none - offering them anyway is doctor rule 7's exact
 * complaint, a queue that would wait for a panel that never arrives.
 */
export function withDarksAfter(g: FlowGraphRec): FlowGraphRec {
  if (nodeOfType(g, "calib")) return g;
  const report = nodeOfType(g, "report");
  if (!report) return g;
  const donePort = NODE_DEFS.report.outs.find((p) => p.kind === "event")?.id;
  const doPort = NODE_DEFS.calib.ins.find((p) => p.id === "do")?.id;
  if (!donePort || !doPort) return g;

  const id = mintNodeId(g, "calib");
  const node: FlowNodeRec = {
    id,
    type: "calib",
    ...freeSpot(g, 380),
    params: { ...defaultParams("calib"), darks: "Always", bias: "Always", flats: "Skip" },
  };
  const edge: FlowEdgeRec = {
    id: mintEdgeId(g, "darks"),
    from: report.id,
    fromPort: donePort,
    to: id,
    toPort: doPort,
  };
  return { nodes: [...g.nodes, node], edges: [...g.edges, edge] };
}

/**
 * A TARGET POOL in front of the single target the flow was generated against.
 *
 * `POST /api/flows/quick` builds a one-target night, so the pool is added here:
 * `dusk.window -> pool.arm`, `pool.target -> <whatever the window fed>`, and
 * `report.done -> pool.advance` - the last of which IS the campaign mechanism
 * (`nodeDefs.ts:640-641`), an event wire back up the lane, which is legal
 * precisely because it is an event and leaves the flow lane a DAG.
 *
 * `members` is the pool's own comma list, in the order the operator queued them.
 */
export function withTargetPool(g: FlowGraphRec, memberNames: readonly string[]): FlowGraphRec {
  if (memberNames.length < 2) return g;
  if (nodeOfType(g, "pool")) return g;
  const dusk = nodeOfType(g, "dusk");
  if (!dusk) return g;
  const outPort = firstFlowOut("dusk");
  const armPort = firstFlowIn("pool");
  const targetPort = firstFlowOut("pool");
  if (!outPort || !armPort || !targetPort) return g;

  const id = mintNodeId(g, "pool");
  const node: FlowNodeRec = {
    id,
    type: "pool",
    ...freeSpot(g, 60),
    params: { ...defaultParams("pool"), members: memberNames.join(", ") },
  };

  const downstream = g.edges.filter((e) => e.from === dusk.id && e.fromPort === outPort);
  const edges: FlowEdgeRec[] = g.edges.map((e) =>
    downstream.includes(e) ? { ...e, from: id, fromPort: targetPort } : e,
  );
  edges.push({
    id: mintEdgeId(g, "pool"),
    from: dusk.id,
    fromPort: outPort,
    to: id,
    toPort: armPort,
  });

  // The advance wire, when there is a report to fire it. A pool with no advance
  // is legal (doctor rule 11 only asks once the night repeats), so its absence
  // is not a reason to refuse the pool.
  const report = nodeOfType(g, "report");
  const donePort = NODE_DEFS.report.outs.find((p) => p.kind === "event")?.id;
  const advancePort = NODE_DEFS.pool.ins.find((p) => p.id === "advance")?.id;
  if (report && donePort && advancePort) {
    edges.push({
      id: mintEdgeId({ nodes: g.nodes, edges }, "advance"),
      from: report.id,
      fromPort: donePort,
      to: id,
      toPort: advancePort,
    });
  }

  return { nodes: [...g.nodes, node], edges };
}
