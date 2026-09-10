// CalibrationMatrixCard.tsx - LIBRARY HEALTH, rebuilt (parity row A23).
//
// It renders `GET /api/calibration/health` through `flowsFetchCalHealth`, and
// the reason this is more than a four-column grid is that the endpoint's most
// important fields are not rows:
//
//   * `planned: false` means no lights are planned yet, so there is nothing to
//     calibrate FOR. An empty grid with no sentence under it says the opposite
//     of the truth - it says you have everything.
//   * `counts_masters_only: true` means `have` counts stacked masters only, so
//     a row reads MISSING where raw subs are sitting on disk. Unlabelled, that
//     is a confident wrong number.
//   * `assumed` is the offset and sensor temperature the demand was computed
//     with, because the node vocabulary has no offset param and the compiler
//     never emits a cooling setpoint. Both are invented values if you do not
//     say they are assumptions.
//   * a NULL `calHealth` is a request that FAILED. `flowsSlice` leaves it null
//     precisely so this component can say "could not read the library" rather
//     than draw the empty matrix that means something else entirely.
//
// `verdictVar`, `calHealthNotes` and `readCalRow` are imported, not rebuilt:
// they are the verdict-to-token map, the three route-level flags as sentences,
// and the four columns read out of an eighteen-key row object. Only the chrome
// is new. `plainDashes` is applied to the shared sentences because the legacy
// copy still carries em-dashes and `#/classic` renders the same strings.

import { useEffect, useState, type JSX } from "react";

import {
  calHealthNotes, readCalRow, verdictVar,
} from "../../../../../components/flows/CalibrationMatrix";
import { useStore } from "../../../../../store";
import { Card, Label, Mono } from "../../../../ui";
import { plainDashes } from "./tonightModel";

export function CalibrationMatrixCard({ className = "" }: {
  className?: string;
}): JSX.Element {
  const health = useStore((s) => s.flows.calHealth);
  const flowId = useStore((s) => s.flows.record?.id ?? null);
  const fetchHealth = useStore((s) => s.flowsFetchCalHealth);
  // `calHealth` is null both before the first answer and after a failed one,
  // and those two are not the same sentence. Nothing else on the surface can
  // tell them apart, so the component remembers whether it has asked yet.
  const [asked, setAsked] = useState(false);

  useEffect(() => {
    let live = true;
    setAsked(false);
    // `flowsFetchCalHealth` swallows its own errors (that is what leaves
    // calHealth null), so this settles either way and never rejects.
    void fetchHealth().finally(() => { if (live) setAsked(true); });
    return () => { live = false; };
  }, [fetchHealth, flowId]);

  const rows = health?.rows ?? [];

  return (
    <Card className={`nx-tn-cal ${className}`.trim()} data-testid="flow-calibration">
      <Label size={10}>LIBRARY HEALTH</Label>

      {rows.map((r, i) => {
        const row = readCalRow(r, i);
        return (
          <div key={row.key} className="nx-tn-cal-row" data-testid={`flow-cal-row-${i}`}>
            <span className="nx-tn-cal-kind">{row.label}</span>
            {/* The 1fr track truncates rather than growing: a grid item's
                default min-width is `auto`, so without the class's `min-width:0`
                the longest summary pushes the whole sheet sideways. */}
            <span className="nx-tn-cal-summary">{row.summary}</span>
            <span className="nx-tn-cal-qty">{row.quantity}</span>
            {/* Verdict is a WORD as well as a colour - OK / STALE / MISSING all
                collapse toward coral under the night palette. */}
            <span className="nx-tn-cal-verdict" style={{ color: verdictVar(row.verdict) }}>
              {row.verdict}
            </span>
          </div>
        );
      })}

      {!asked && !health ? (
        <Mono size={10} tone="dim">Reading the calibration library...</Mono>
      ) : (
        calHealthNotes(health).map((n) => (
          <p
            key={n.key}
            className="nx-tn-cal-note"
            data-tone={n.tone}
            data-testid={`flow-cal-note-${n.key}`}
          >
            {plainDashes(n.text)}
          </p>
        ))
      )}

      <p className="nx-tn-cal-note" data-tone="faint">
        Drives the queue's 'if stale' decisions - this is the calibration
        library the run will match against, not a copy of it.
      </p>
    </Card>
  );
}

export default CalibrationMatrixCard;
