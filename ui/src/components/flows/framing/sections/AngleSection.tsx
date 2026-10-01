// AngleSection.tsx - the Target modal's ANGLE section (#189 S4 item 1; spec
// 2026-09-23 flows mosaic, 2.4 ANGLE).
//
// Three segments, ANY ANGLE / ROTATE TO / CAMERA FIXED AT, each locked with
// its reason where the model says so (framingModel `angleLocks`): ANY ANGLE on
// a grid ("a grid is laid out at one camera angle"), ROTATE TO on a profile
// with no rotator. A locked segment is a HonestButton: pressing it explains
// and changes nothing, and its reason is its title, so the reason is on the
// control itself and not in a caption beside it.
//
// Then a continuous degree field with nudges of 1 and 15 (no 15-degree snap:
// the old dial's snap is issue I-24), USE MEASURED from `status.sky_angle`,
// the convention line and the tolerance line. The sky's rotate handle and
// its `[` `]` keys turn the same angle; they live on the canvas.
//
// A GRID WITH NO ANGLE IS OFFERED THE MEASURED ONE, NEVER GIVEN IT (S5
// orchestrator ruling 1, #411). While the draft is a grid at ANY ANGLE, the
// USE MEASURED row becomes the offer, its button naming exactly what a press
// writes, "ROTATE TO 37.2 deg" or "CAMERA FIXED AT 37.2 deg" (framingModel
// `angleOffer`), with the measurement's line under it saying where the number
// came from. Nothing is written until it is pressed. The readout strip carries
// the same offer, since on a phone this section is below GRID, off screen.

import type { JSX } from "react";
import { HonestButton } from "../../../ui";
import { TARGET_ANGLES } from "../../nodeDefs";
import type { TargetAngle } from "../framingModel";

/** How the angle is measured, and what is not yet known about the rotator
 *  (spec 2.4): the one line that stops a rotator-sense guess passing as a
 *  fact. */
export const ANGLE_CONVENTION =
  "degrees N through W, clockwise on this chart, as the plate solver reports (CROTA2); the rotator's sense is under test (#145)";

/** The segment labels, in TARGET_ANGLES order. */
export const SEGMENT_LABEL: Record<TargetAngle, string> = {
  "Any angle": "ANY ANGLE",
  "Rotate to PA": "ROTATE TO",
  "Camera fixed at PA": "CAMERA FIXED AT",
};

export const NUDGES = [-15, -1, 1, 15] as const;

export interface AngleSectionProps {
  /** The draft's angle mode (framingModel `angleOf`). */
  mode: string;
  /** The degree field's text, as typed. */
  rotation: string;
  locks: Record<TargetAngle, string | null>;
  /** The USE MEASURED chip's text, or null with no measurement. */
  measured: string | null;
  /** The measured angle offered to a grid that owes one (framingModel
   *  `angleOffer`'s label), or null. In place of the plain chip. */
  offer: string | null;
  tolerance: string | null;
  /** Why the degree field and the nudges cannot act (ANY ANGLE holds no
   *  angle), or null. */
  degreeLock: string | null;
  onMode: (mode: TargetAngle) => void;
  onRotation: (text: string) => void;
  onNudge: (delta: number) => void;
  onUseMeasured: () => void;
  onOffer: () => void;
  explain: (reason: string) => void;
}

export function AngleSection(p: AngleSectionProps): JSX.Element {
  return (
    <section className="tfs-section" aria-labelledby="tfs-angle-h" data-testid="framing-angle">
      <h3 id="tfs-angle-h" className="tfs-section-h">ANGLE</h3>
      {/* A group of HonestButtons rather than a radiogroup: HonestButton is
          the one control that keeps a locked choice's reason on the control,
          and a radio role nested inside a button is not a radio. The chosen
          segment says so in words for a screen reader. */}
      <div className="tfs-row tfs-segments" role="group" aria-label="Camera angle">
        {TARGET_ANGLES.map((m) => (
          <HonestButton
            key={m}
            className={`tfs-btn tfs-seg ${p.mode === m ? "tfs-on" : ""}`}
            reason={p.locks[m]}
            onClick={() => p.onMode(m)}
            onExplain={p.explain}
          >
            <span data-angle={m} data-chosen={p.mode === m ? "true" : undefined}>{SEGMENT_LABEL[m]}</span>
            {p.mode === m && <span className="sr-only"> (chosen)</span>}
          </HonestButton>
        ))}
      </div>
      <div className="tfs-row">
        <label className="tfs-label" htmlFor="tfs-angle-deg">DEGREES</label>
        <input
          id="tfs-angle-deg"
          className="field tfs-input tfs-num"
          type="text"
          inputMode="decimal"
          value={p.rotation}
          readOnly={p.degreeLock !== null}
          aria-readonly={p.degreeLock !== null || undefined}
          title={p.degreeLock ?? undefined}
          onChange={(e) => p.onRotation(e.target.value)}
        />
      </div>
      <div className="tfs-row tfs-chips" role="group" aria-label="Nudge the angle">
        {NUDGES.map((d) => (
          <HonestButton key={d} className="tfs-btn" reason={p.degreeLock}
            onClick={() => p.onNudge(d)} onExplain={p.explain}>
            {d > 0 ? `+${d}` : `${d}`}
          </HonestButton>
        ))}
      </div>
      {p.offer ? (
        <>
          <div className="tfs-row">
            <span className="tfs-label">USE MEASURED</span>
            <button type="button" className="tfs-btn tfs-on" data-testid="framing-angle-offer" onClick={p.onOffer}>
              {p.offer}
            </button>
          </div>
          {p.measured && <div className="tfs-row tfs-note">{p.measured}</div>}
        </>
      ) : p.measured && (
        <div className="tfs-row">
          <button type="button" className="tfs-btn tfs-chip" data-testid="framing-use-measured"
            onClick={p.onUseMeasured}>
            USE MEASURED
          </button>
          <span className="tfs-note">{p.measured}</span>
        </div>
      )}
      <div className="tfs-row tfs-note">{ANGLE_CONVENTION}</div>
      {p.tolerance && <div className="tfs-row tfs-note" data-testid="framing-tolerance">{p.tolerance}</div>}
    </section>
  );
}

export default AngleSection;
