// flowLane.ts - the flow card's two lists, derived from the graph the server
// saved (hub-sky plan D.5, T-SKY-3). Pure: a graph in, rows out, no DOM.
//
// WHAT THE CARD IS. `screenshots/06-flow-card.png` shows a vertical lane of
// stages with a wire between them and, under it, a "RULES · EVENT WIRES" list of
// when/then pairs. Those are not two decorations of one list: they are the two
// KINDS of port the vocabulary has. A `flow` port carries the single run cursor
// ("then"); an `event` port fires any number of times ("whenever"), through
// completely different engine machinery (`nodes.py`'s own module docstring).
// Drawing an event wire in the lane would show a rule as a stage in the night.
//
// THE ORDER IS NOT MINE. `autoLayout.flowOrder` already implements Kahn's
// algorithm over flow-kind edges only, seeded from the graph's own left-to-right
// reading order, and it is tested. Re-implementing a topological sort here would
// be a second answer to "what runs first" - and the interesting case (a node
// inside a flow cycle never reaches indegree 0 and is therefore ABSENT) is
// behaviour a fresh sort would get differently.

import { NODE_DEFS } from "../../../../components/flows/nodeDefs";
import { flowOrder } from "../../../../components/flows/autoLayout";
import type { FlowGraphRec, FlowNodeType } from "../../../../components/flows/flowsTypes";
import type { FlowCompileResult } from "../../../../lib/flowsApi";
import { MOSAIC_FOOTNOTE } from "./quickCopy";

export interface LaneCard {
  /** Node id, or `mosaic` for the one synthetic card (H.6). */
  id: string;
  label: string;
  sum: string;
  /** A CSS custom-property NAME with its leading `--`, used as `var(...)`. */
  colorVar: string;
  /** Present only on the synthetic card: it is drawn, not compiled. */
  footnote?: string;
}

export interface RuleRow {
  id: string;
  /** The node the event leaves. */
  srcLabel: string;
  /** That port's own label - "clouds in", "target done". */
  when: string;
  /** The node the event reaches. */
  actLabel: string;
  /** What that node will do, from its own params. */
  sum: string;
  colorVar: string;
}

function defOf(type: FlowNodeType) {
  return NODE_DEFS[type];
}

/**
 * The stage lane.
 *
 * A node whose type this build's vocabulary does not carry is SKIPPED rather
 * than drawn as a blank card: the graph came off the wire from a server that may
 * be newer than this bundle, and a card with no label and no summary tells the
 * operator nothing while implying the stage is empty.
 */
export function laneCards(graph: FlowGraphRec): LaneCard[] {
  return flowOrder(graph, NODE_DEFS)
    .filter((n) => NODE_DEFS[n.type] != null)
    .map((n) => {
      const d = defOf(n.type);
      let sum = "";
      try {
        sum = d.sum(n.params ?? {});
      } catch {
        // A summary is a convenience; a params object off the wire that its own
        // formatter cannot read must not blank the whole card.
        sum = "";
      }
      return { id: n.id, label: d.label, sum, colorVar: d.colorVar };
    });
}

/**
 * The synthetic MOSAIC card, inserted after TARGET.
 *
 * H.6: `nodeDefs` has 21 node types and none of them is `mosaic`. The engine's
 * mosaic mechanism is N plan targets sharing a `mosaic_group`, so the panels are
 * real and the STAGE is not - which is what the footnote says, on the card,
 * rather than in a release note nobody reads.
 */
export function withMosaicCard(
  cards: LaneCard[],
  cols: number,
  rows: number,
): LaneCard[] {
  const panels = cols * rows;
  if (!(panels > 1)) return cards;
  const card: LaneCard = {
    id: "mosaic",
    label: `MOSAIC ${cols}×${rows}`,
    sum: `${panels} panels · 15% overlap · centre per panel · cycle panels each pass`,
    colorVar: "--accent-dim",
    footnote: MOSAIC_FOOTNOTE,
  };
  const at = cards.findIndex((c) => c.label === "TARGET" || c.label === "TARGET POOL");
  if (at < 0) return [card, ...cards];
  return [...cards.slice(0, at + 1), card, ...cards.slice(at + 1)];
}

/** One row per EVENT edge - the "whenever" half of the graph. */
export function ruleRows(graph: FlowGraphRec): RuleRow[] {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const out: RuleRow[] = [];
  for (const e of graph.edges) {
    const from = byId.get(e.from);
    const to = byId.get(e.to);
    if (!from || !to) continue;
    const fd = NODE_DEFS[from.type];
    const td = NODE_DEFS[to.type];
    if (!fd || !td) continue;
    const port = fd.outs.find((p) => p.id === e.fromPort);
    if (!port || port.kind !== "event") continue;
    let sum = "";
    try {
      sum = td.sum(to.params ?? {});
    } catch {
      sum = "";
    }
    out.push({
      id: e.id,
      srcLabel: fd.label,
      when: port.label,
      actLabel: td.label,
      sum,
      colorVar: td.colorVar,
    });
  }
  return out;
}

// ------------------------------------------------------- the validation chip

export type DoctorState = "unknown" | "valid" | "open";

export interface DoctorChip {
  state: DoctorState;
  label: string;
}

/**
 * THREE states, not two.
 *
 * "The compiler has not answered yet" must never render as if it had. A chip
 * that showed GRAPH VALID while `compiled` was still null would be the app
 * saying the doctor passed a graph it has not seen - and RUN NOW sits directly
 * under it.
 */
export function doctorChip(
  compiled: FlowCompileResult | null,
  compiling: boolean,
): DoctorChip {
  if (compiling || compiled == null) return { state: "unknown", label: "NOT CHECKED" };
  const n = (compiled.structural?.length ?? 0) + (compiled.issues?.length ?? 0);
  if (n === 0) return { state: "valid", label: "GRAPH VALID" };
  return { state: "open", label: `${n} OPEN CHECK${n > 1 ? "S" : ""}` };
}

/** The clause the footer sentence carries, from the compiler's own counts. */
export function issuesLine(compiled: FlowCompileResult | null, compiling: boolean): string {
  if (compiling || compiled == null) return "The doctor has not answered for this graph yet.";
  const structural = compiled.structural?.length ?? 0;
  const issues = compiled.issues?.length ?? 0;
  const n = structural + issues;
  if (n === 0) return "The doctor found nothing to fix.";
  return `The doctor has ${n} open check${n > 1 ? "s" : ""} on it.`;
}
