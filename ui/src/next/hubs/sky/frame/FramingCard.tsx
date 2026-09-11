// FramingCard.tsx - the mosaic picker, the rotation dial and the honesty note
// that appear under the finder while FRAME is on (hub-sky plan C).
//
// THE ROTATION NOTE IS THE PART THAT MATTERS. `framing.rotation_deg` is the same
// field the Atlas writes, and the engine rotates ONLY when a goto carries it
// (`hub.goto_and_center` runs `rotate_to_pa` when `rotation_deg is not None and
// rot is not None and rot.connected`). So a dial at 30 degrees means two
// completely different nights depending on whether a rotator is in the rig, and
// the card says which one out loud:
//
//   with a rotator     the rotator is named, and warned about if 30 is outside
//                      its range of motion (it will image at the mapped angle)
//   without one        the angle is MANUAL - set the camera before the run
//
// AND IT NAMES THE SLEW THAT DOES NOT SEND IT. The note used to open "Go to this
// target - and every slew in a run - sends PA 30°", which was two claims: one the
// generated flow now keeps (`flowGraphExtras.withRotation` writes the angle onto
// the TARGET node, which `to_plan` passes through as `rotation_deg`) and one
// nothing in the new UI keeps - neither `POST /api/mount/goto` call in Rig sends
// a rotation. Promising the second cost nothing to write and would cost a night
// to discover, so the sentence says which slew is which (review #3).
//
// The 0.5 degree dead-band is lifted verbatim from `AtlasView.tsx:794`, and it
// is load-bearing: rotation starts at 0 for every framing session, so treating 0
// as a commanded angle would bolt a rotate-to-PA loop onto every "just show me
// this" tap.

import type { JSX } from "react";
import { Card, Dial } from "../../../ui";
import { adjustedPa } from "../../../../lib/rotation";
import type { RotatorStatus } from "../../../../types";
import { MOSAIC_CHOICES, ROTS, commandedPa, framingMeta } from "./mosaic";

const MONO = "'IBM Plex Mono', ui-monospace, monospace";
const DISPLAY = "'Chakra Petch', system-ui, sans-serif";

export const FRAMING_NOTE =
  "Drag the sky to shift the frame, turn the dial to rotate the camera. " +
  "DONE keeps the framing: it stays on the sky and goes into the flow. " +
  "Panels overlap 15%; the flow centres on each panel in turn and cycles panels " +
  "every pass, so a clouded-out night still leaves every panel with data. " +
  "The dashed outline is the object's catalogued extent.";

export interface FramingCardProps {
  targetName: string;
  cols: number;
  rows: number;
  rotationDeg: number;
  fovXDeg: number;
  fovYDeg: number;
  /** The framing session's own overlap - never a constant re-asserted here, or
   *  a wrong one would still print the right number. */
  overlap: number;
  rotator: RotatorStatus | null;
  rotatorRange: { range_type: "full" | "half" | "quarter"; range_start_deg: number };
  onMosaic: (cols: number, rows: number) => void;
  onRotate: (deg: number) => void;
}

export function FramingCard({
  targetName,
  cols,
  rows,
  rotationDeg,
  fovXDeg,
  fovYDeg,
  overlap,
  rotator,
  rotatorRange,
  onMosaic,
  onRotate,
}: FramingCardProps): JSX.Element {
  const commandedPaDeg = commandedPa(rotationDeg);
  const hand = commandedPaDeg != null && rotator ? adjustedPa(commandedPaDeg, rotator, rotatorRange) : null;

  return (
    <Card tone="accent" className="nx-sky-framing" data-testid="sky-framing">
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 10, letterSpacing: ".2em", color: "var(--accent)", whiteSpace: "nowrap" }}>
            FRAMING · {targetName}
          </div>
          <div
            data-testid="sky-framing-meta"
            style={{ fontFamily: MONO, fontSize: 10, color: "var(--text-dim)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}
          >
            {framingMeta(cols, rows, rotationDeg, fovXDeg, fovYDeg, overlap)}
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,minmax(0,1fr))", gap: 6 }}>
          {MOSAIC_CHOICES.map((m) => {
            const on = m.cols === cols && m.rows === rows;
            const n = m.cols * m.rows;
            return (
              <button
                key={`${m.cols}x${m.rows}`}
                type="button"
                data-mosaic={`${m.cols}x${m.rows}`}
                aria-pressed={on}
                onClick={() => onMosaic(m.cols, m.rows)}
                style={{
                  height: 44, borderRadius: 10,
                  border: `1px solid ${on ? "var(--accent)" : "var(--line-bright)"}`,
                  background: on ? "color-mix(in srgb, var(--accent) 12%, transparent)" : "var(--bg-raise)",
                  color: on ? "var(--accent)" : "var(--text)",
                  display: "flex", flexDirection: "column",
                  alignItems: "center", justifyContent: "center", gap: 2, cursor: "pointer",
                }}
              >
                <span style={{ fontFamily: MONO, fontSize: 12 }}>{m.cols}×{m.rows}</span>
                <span style={{ fontFamily: MONO, fontSize: 10, color: "var(--text-faint)" }}>
                  {n === 1 ? "single" : `${n} panels`}
                </span>
              </button>
            );
          })}
        </div>

        <Dial<number>
          label={`CAMERA ROTATION · ${Math.round(rotationDeg)}°`}
          hint="drag or tap"
          options={ROTS.map((v) => ({ value: v, label: `${v}°` }))}
          value={ROTS.includes(rotationDeg) ? rotationDeg : 0}
          onChange={onRotate}
          data-testid="sky-rot-dial"
        />

        {commandedPaDeg != null && (
          <p data-testid="sky-rot-note" style={{ fontSize: 11.5, color: "var(--text-faint)", lineHeight: 1.5, margin: 0 }}>
            {rotator ? (
              <>
                Every slew the generated flow makes sends PA {Math.round(commandedPaDeg)}° to{" "}
                {rotator.name}, which rotates before it centres. A Go to from Rig - Mount does
                not: that one leaves the camera where it is.
                {hand?.adjusted && (
                  <span style={{ color: "var(--warn)" }}>
                    {" "}Outside the range of motion - it will image as {Math.round(hand.target)}°.
                  </span>
                )}
              </>
            ) : (
              <>
                Camera angle is manual - set your camera to PA {Math.round(commandedPaDeg)}° before
                the run; there is no rotator in the rig.
              </>
            )}
          </p>
        )}

        <div style={{ fontSize: 11.5, color: "var(--text-faint)", lineHeight: 1.5 }}>{FRAMING_NOTE}</div>
      </div>
    </Card>
  );
}
