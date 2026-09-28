// countsNotice.ts - the one line both editors show while a flow still counts
// every sub taken (#189; spec Revision 2, ruling 2; S4 orchestrator ruling 8).
// Pure: no store, no React, no DOM.
//
// THE RULING. Every new TARGET and POOL counts accepted subs only, and the
// choice is withdrawn from the editor. A flow saved before that keeps its
// meaning on load (it counts attempts, rejected subs included) until it is
// next saved: the server's save switches it (`save_rules.prepare_save`), for
// every writer at once. While it has not been switched, both editors and the
// phone stage list carry one line saying so, and saving is what ends it.
//
// WHY THE LINE READS THE GRAPH, NOT THE SERVER'S NOTE. The server's read puts
// the same sentence in `migrated` on open (`store._migrate`), and the editor
// keeps that note (`flows.countsNote`). But the note is a fact about the file
// AS OPENED, and a save that switches the counts does not reopen the flow: a
// line driven by the note alone would go on telling the operator that saving
// switches a flow that is already switched. So whether the line shows is
// decided by the graph the editor holds, by the server's own rule
// (`counts_attempts`), and the note decides only the addendum: whether the
// flow has a dormant session, which only the server's route knows
// (`get_flow` adds `COUNTS_DORMANT_ADDENDUM`).
//
// THE SENTENCES ARE THE SERVER'S, copied, and __tests__/countsNotice.test.ts
// PARSES store.py and app.py and compares, as flowSettingsParity.test.ts does
// for FLOW_SETTINGS: the ruling fixes the words, and two copies drift.

import type { FlowGraphRec, FlowNodeRec } from "./flowsTypes";

/** Ruling 2's line, verbatim (server `flows/store.py` `COUNTS_NOTE`). */
export const COUNTS_NOTE =
  "This flow counts every sub taken, rejected ones included. New flows "
  + "count accepted subs only, and saving this flow switches it.";

/** Ruling 2's second sentence, said when the flow has a dormant session
 *  (server `api/app.py` `COUNTS_DORMANT_ADDENDUM`). The session's ledger is
 *  counted by its frozen plan until CONTINUE recounts it, which asks first
 *  only when the recount changes the total (S4 orchestrator ruling 2). */
export const COUNTS_DORMANT_ADDENDUM = "Its armed session keeps its count until you CONTINUE.";

/** The one `counts` value that counts accepted subs (server `save_rules.py`
 *  `ACCEPTED_SUBS`, nodeDefs `COUNT_MODES[1]`). */
export const ACCEPTED_SUBS = "Accepted subs";

/** The node types that carry `counts` (server `save_rules.py`
 *  `COUNTED_TYPES`). */
export const COUNTED_TYPES: ReadonlySet<string> = new Set(["target", "pool"]);

/** True when a node COUNTS EVERY SUB TAKEN (server `save_rules.counts_attempts`):
 *  a TARGET or POOL whose `counts` is anything but "Accepted subs". A missing
 *  key is the old meaning, and so is a value this build does not offer,
 *  because the plan counts accepted subs only when a block asks in those
 *  words. The same test the server's read uses for its note and its save for
 *  the switch, so the line never promises a switch the save does not make. */
export function countsAttempts(node: Pick<FlowNodeRec, "type" | "params">): boolean {
  if (!COUNTED_TYPES.has(node.type)) return false;
  const p = node.params as Record<string, unknown> | undefined;
  return (p && typeof p === "object" ? p.counts : undefined) !== ACCEPTED_SUBS;
}

/** The persistent line, or null when no TARGET or POOL in `graph` counts
 *  attempts.
 *
 *  `serverNote` is the counts note the server's read carried when the flow
 *  was opened (`flows.countsNote`), or null. When it carries the dormant
 *  addendum, the line does too. It never makes the line show by itself. */
export function countsNotice(
  graph: Pick<FlowGraphRec, "nodes"> | null | undefined, serverNote: string | null | undefined,
): string | null {
  const nodes = Array.isArray(graph?.nodes) ? graph!.nodes : [];
  if (!nodes.some(countsAttempts)) return null;
  return typeof serverNote === "string" && serverNote.includes(COUNTS_DORMANT_ADDENDUM)
    ? `${COUNTS_NOTE} ${COUNTS_DORMANT_ADDENDUM}`
    : COUNTS_NOTE;
}

/** `graph` with the save's counts switch applied, as the server applied it
 *  (`save_rules.prepare_save`: every TARGET and POOL that counts attempts
 *  gets "Accepted subs"), or `graph` itself, the same object, when no node
 *  counts attempts.
 *
 *  For the editor to hold what the server now stores once a save's answer
 *  says it switched the counts (`flowsSave`). Without it the canvas keeps
 *  the old value: the line above would stay up over a switched flow, the
 *  draft compile would count attempts, and every later save would switch it
 *  again and say so again. Spreads the graph, so its `settings` survive. */
export function acceptCounts<G extends FlowGraphRec>(graph: G): G {
  if (!graph.nodes.some(countsAttempts)) return graph;
  return {
    ...graph,
    nodes: graph.nodes.map((n) => (countsAttempts(n)
      ? { ...n, params: { ...n.params, counts: ACCEPTED_SUBS } } : n)),
  };
}
