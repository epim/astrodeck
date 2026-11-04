// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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

import { AUTO_RESUME_CHOICES, NODE_DEFS, createParams } from "../../../../../components/flows/nodeDefs";
import type { FlowNodeRec } from "../../../../../components/flows/flowsTypes";
import {
  CLOCK_TIME, DUSK_STOPS, NEED_STOP_CLOCK, clockOf, type DuskStop,
} from "../../../../../components/flows/wizard/wizardModel";
import type { FlowWizardAnswers } from "../../../../../lib/flowsApi";

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

/** Questions 4 and 5 (#718): how the night ends and whether the flow resumes on
 *  later nights. The choices are the stepped Send to Flow Wizard sheet's own
 *  (`DUSK_STOPS`, `AUTO_RESUME_CHOICES`), imported and never copied. */
export { AUTO_RESUME_CHOICES, CLOCK_TIME, DUSK_STOPS };
export type { DuskStop };

/** What each stop choice does, so the choice says something the words do not.
 *  Rendered as the option's `sub`; never sent to the server. */
export const STOP_SUB: Record<DuskStop, string> = {
  Dawn: "images until dawn, then parks",
  "Clock time": "stops at a time you type",
};

/** What each auto-resume choice does (the stepped sheet's review wording).
 *  Rendered as the option's `sub`; never sent to the server. */
export const RESUME_SUB: Record<(typeof AUTO_RESUME_CHOICES)[number], string> = {
  On: "resumes on later nights until every frame is taken",
  Off: "tonight only; continue it by hand",
};

export interface NightAnswers {
  stop: DuskStop;
  /** Typed text; read by `clockOf` and asked only beside "Clock time". */
  stopClock: string;
  autoResume: boolean;
}

const DUSK_DEFAULTS = NODE_DEFS.dusk.params as { stop: string; autoResume: string };

/** The answers the sheet opens with: DUSK WINDOW's own defaults, which is what a
 *  flow generated without these two questions has always had. */
export const NIGHT_DEFAULTS: NightAnswers = {
  stop: DUSK_DEFAULTS.stop as DuskStop,
  stopClock: "",
  autoResume: DUSK_DEFAULTS.autoResume !== AUTO_RESUME_CHOICES[1],
};

/** Why GENERATE cannot go: a "Clock time" stop with no valid time is a boundary
 *  that never arrives, and the server refuses it. Null when nothing is missing. */
export function nightGap(n: NightAnswers): string | null {
  return n.stop === CLOCK_TIME && clockOf(n.stopClock) === null ? NEED_STOP_CLOCK : null;
}

/** The one body GENERATE posts. The night's two answers go up ONLY when they
 *  differ from DUSK WINDOW's defaults, as the stepped sheet's `wizardBody`
 *  does: the server reads a missing key as "not given", so a sheet nobody
 *  changed them in posts the three-answer body it always did. A clock goes
 *  with its "Clock time" and with nothing else. */
export function wizardBody(
  kind: string, autos: readonly string[], target: string, night: NightAnswers,
): FlowWizardAnswers {
  const body: FlowWizardAnswers = { kind, options: [...autos], target: target.trim() };
  if (night.stop !== NIGHT_DEFAULTS.stop) {
    body.stop = night.stop;
    const clock = clockOf(night.stopClock);
    if (night.stop === CLOCK_TIME && clock !== null) body.stop_clock = clock;
  }
  if (night.autoResume !== NIGHT_DEFAULTS.autoResume) {
    body.auto_resume = night.autoResume ? AUTO_RESUME_CHOICES[0] : AUTO_RESUME_CHOICES[1];
  }
  return body;
}

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

// A TARGET and a CAPTURE LOOP, unwired, at these coordinates. Two nodes rather
// than none because an empty canvas gives the operator nothing to drag a wire
// from. It drew a SLEW until the integration of mosaic S3: SLEW + CENTER is
// part of the TARGET block now, a legacy type the palette no longer offers
// (spec 1.7), so a blank flow opened with L1's note telling the operator to
// delete it (#340). Both nodes are CREATED, so they take `createParams`: a
// TARGET with no name and no coordinates, not M31's (#190), counting accepted
// subs (ruling 2).
export const BLANK_NODES: ReadonlyArray<{ type: "target" | "capture"; x: number; y: number }> = [
  { type: "target", x: 60, y: 120 },
  { type: "capture", x: 320, y: 120 },
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
    params: createParams(n.type),
  }));
}
