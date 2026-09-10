// wizardModel.ts - the guided NEW FLOW sheet's constants and its blank graph.
//
// THE GENERATOR LIVES ON THE SERVER, AND STAYS THERE.
// `server/astrodeck/flows/wizard.py` owns the rules; `POST /api/flows/wizard`
// is the wire. The sheet asks and the server answers: the kind strings and the
// automation labels below ARE that module's own constants, which is why neither
// end re-types them and why a TypeScript re-implementation of `generate()`
// would be the third transcription of one rule set.
//
// Pure and separate from `wizard.tsx` so the payload the sheet sends can be
// graded without a DOM.

import { NODE_DEFS } from "../../../../../components/flows/nodeDefs";
import type { FlowNodeRec } from "../../../../../components/flows/flowsTypes";

/** Question 1. Order and labels from the flows contract; `Deep-sky target` is
 *  the default. */
export const KINDS = ["Deep-sky target", "Best of several", "EAA quick look"] as const;
export type WizardKind = (typeof KINDS)[number];

/** What each kind produces, so the choice says something the three words do
 *  not. Rendered as the option's `sub`; never sent to the server. */
export const KIND_SUB: Record<WizardKind, string> = {
  "Deep-sky target": "one object, all night",
  "Best of several": "a pool, ranked at dusk",
  "EAA quick look": "short subs, live stack",
};

/** Question 2, in the contract's order. `Guiding` and `HFR watchdog` start on. */
export const AUTOMATIONS = [
  "Guiding", "Dusk flats", "Dome",
  "Cloud-dodge calibration", "HFR watchdog", "Notify my phone",
] as const;
export const AUTOMATION_DEFAULTS: readonly string[] = ["Guiding", "HFR watchdog"];

/** The literal run of spaces is part of the placeholder - it is what separates
 *  the single-target example from the comma-list one without a second field. */
export const TARGET_PLACEHOLDER = "M16    ·    or: M16, M17, M8, NGC 6946";

/** The only thing that can stop GENERATE FLOW now is a capability or a request
 *  already in flight. Kept as one exported string so the button's reason and
 *  any test asserting on it cannot drift apart. */
export const GENERATE_FAILED = "Could not generate the flow";

/** The blank path's own failure, same rule. */
export const BLANK_FAILED = "Could not start a blank flow";

/** Both buttons refuse while a request is in flight. A hyphen, not the legacy
 *  em-dash (ARCHITECTURE.md section 0 rule 5). */
export const BUSY_REASON = "Already creating a flow - one moment.";

/** What GENERATE actually does, said once. Carries the two facts the three
 *  questions above do not: the graph comes back complete and editable, and the
 *  doctor grades it. Hyphen, not the legacy em-dash. */
export const WIZARD_NOTE =
  "Generates a complete, valid graph - then everything is just stages and wires "
  + "you can rearrange. The doctor will flag anything risky.";

/** START BLANK is real: `POST /api/flows` is the same route the generated
 *  record would have been saved through. */
export const BLANK_NAME = "Untitled flow";
export const BLANK_TAGLINE = "Started blank";

// A TARGET and a SLEW, unwired, at these coordinates. Two nodes rather than
// none because an empty canvas gives the operator nothing to drag a wire from.
const BLANK_NODES: ReadonlyArray<{ type: "target" | "slew"; x: number; y: number }> = [
  { type: "target", x: 60, y: 120 },
  { type: "slew", x: 320, y: 120 },
];

// Node ids are minted client-side and are only local handles. Prefixed for the
// reason `flowsSlice`'s `nextNodeId` documents: a collision with a server-minted
// id would silently rewire a graph, so it is made impossible rather than
// unlikely. Not shared with the slice's counter - these ids never coexist with
// slice-minted ones in the same graph, they ARE the graph.
let blankSeq = 0;

export function blankNodes(): FlowNodeRec[] {
  return BLANK_NODES.map((n) => ({
    id: `w${++blankSeq}_${Date.now().toString(36)}`,
    type: n.type,
    x: n.x,
    y: n.y,
    params: { ...NODE_DEFS[n.type].params },
  }));
}
