// CentringSection.tsx - the Target modal's CENTRING section (#189 S4 item 1;
// spec 2026-09-23 flows mosaic, 2.4 CENTRING, 1.7).
//
// The tolerance in arcminutes and the number of tries, whose missing-key
// defaults (1.2' and 3) are what the hub's centring already does (0.02 deg,
// 3 tries), and what to do with a panel that will not centre or reach its
// angle. The choices are the TARGET node's own (nodeDefs), so the modal and
// the inspector offer the same three words and store the same string, and so
// is the label: S4 wrote its own, "IF A PANEL WILL NOT CENTRE", which dropped
// the half of what the choice governs that is the angle check at every hop
// (5.6), so the label is now the field's, in the section's capitals (#413).

import type { JSX } from "react";
import { NODE_DEFS } from "../../nodeDefs";
import { Stepper } from "./GridSection";

const IF_NOT_CENTRED_FIELD = NODE_DEFS.target.fields.find((f) => f.key === "ifNotCentred");
const IF_NOT_CENTRED: readonly string[] = IF_NOT_CENTRED_FIELD?.options ?? [];
/** "IF A PANEL WILL NOT CENTRE OR REACH ITS ANGLE": the inspector's label. */
export const IF_NOT_CENTRED_LABEL = (IF_NOT_CENTRED_FIELD?.label ?? "").toUpperCase();

/** What "Auto" means, which the word alone does not say (spec 2.4). */
export const AUTO_MEANS = "Auto skips a mosaic panel this pass and shoots a single target anyway";

/** Tenths of an arcminute: the tolerance's stepper moves in these. */
const TOL_STEP = 0.1;

export interface CentringSectionProps {
  /** Arcminutes. */
  tolerance: number;
  tries: number;
  ifNotCentred: string;
  onTolerance: (arcmin: number) => void;
  onTries: (n: number) => void;
  onIfNotCentred: (v: string) => void;
  explain: (reason: string) => void;
}

export function CentringSection(p: CentringSectionProps): JSX.Element {
  // The stepper adds in binary floating point; a tolerance shown as
  // 1.3000000000000003' is a number nobody typed.
  const tenth = (v: number) => Math.round(v * 10) / 10;
  return (
    <section className="tfs-section" aria-labelledby="tfs-centring-h" data-testid="framing-centring">
      <h3 id="tfs-centring-h" className="tfs-section-h">CENTRING</h3>
      <Stepper label="WITHIN" value={tenth(p.tolerance)} min={0.1} max={30} step={TOL_STEP} unit="'"
        lock={null} onChange={(v) => p.onTolerance(tenth(v))} explain={p.explain} />
      <Stepper label="TRIES" value={p.tries} min={1} max={10} lock={null}
        onChange={p.onTries} explain={p.explain} />
      <div className="tfs-row">
        <label className="tfs-label" htmlFor="tfs-if-not">{IF_NOT_CENTRED_LABEL}</label>
        <select id="tfs-if-not" className="field tfs-input" value={p.ifNotCentred}
          onChange={(e) => p.onIfNotCentred(e.target.value)}>
          {IF_NOT_CENTRED.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
      {p.ifNotCentred === "Auto" && <div className="tfs-row tfs-note">{AUTO_MEANS}</div>}
    </section>
  );
}

export default CentringSection;
