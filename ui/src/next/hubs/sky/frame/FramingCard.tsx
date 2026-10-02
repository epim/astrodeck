// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
//
// THE ANGLE IS CONTINUOUS, AND THE DIAL SAYS SO (#173). The finder's rotate drag
// writes any angle (`SkyCanvas` hands back degrees in [0, 360)), and the dial's
// stops are multiples of 15. The dial used to be handed `ROTS.includes(v) ? v :
// 0`, so after a drag to 37 it sat on 0 under a label reading 37, and the first
// touch moved the frame from 0, not from 37: the angle the user framed was lost
// to a control that was only showing it. Now an angle that is not a stop gets a
// stop of its own, and the PA field under the dial takes any angle, with nudges
// of 1 and 15 that move from where the frame IS (spec 2.4: no 15-degree snap).
//
// SEND TO FLOW WIZARD IS THIS CARD'S FORWARD ACTION (#196, spec section 8 S6).
// It hands the framing as it stands (centre, PA, grid, overlap, field) to the
// shared wizard, which writes one TARGET block into a flow. The card used to
// promise an ORDER instead, because the framing's only way forward was the
// quick sheet's Plan side channel, shot panel-first (#154); that channel and
// the sentence describing it are gone, and the order a TARGET block is shot
// in is the engine's, which this card does not describe.

import { useState, type JSX } from "react";
import { ActionButton, Card, Dial, NumberField } from "../../../ui";
import { adjustedPa } from "../../../../lib/rotation";
import type { RotatorStatus } from "../../../../types";
import { MOSAIC_CHOICES, ROTS, commandedPa, framingMeta, overlapPercent } from "./mosaic";
import { SEND_TO_WIZARD } from "../sheets/quickCopy";

const MONO = "'IBM Plex Mono', ui-monospace, monospace";
const DISPLAY = "'Chakra Petch', system-ui, sans-serif";

/**
 * The sentence under the picker: how to frame, what each way forward keeps,
 * and the overlap, which nothing else on this card shows.
 *
 * The overlap is the SESSION's, printed from the prop, never a number typed
 * into the copy: the copy used to say "Panels overlap 15%" as a literal, and
 * FRAME mode re-set every session to 0.15 to keep the pitch in line with it
 * (spec 2.4's one constant ended that).
 */
export function framingNote(overlap: number): string {
  return "Drag the sky to shift the frame, turn the dial to rotate the camera. "
    + `Panels overlap ${overlapPercent(overlap)}%. `
    + `${SEND_TO_WIZARD} plans a flow from this framing: its centre, angle, grid and overlap. `
    + "DONE keeps the framing on the sky. "
    + "The dashed outline is the object's catalogued extent.";
}

/** The nudges under the PA field, in degrees. */
const NUDGES: readonly number[] = [-15, -1, 1, 15];

/** An angle folded into [0, 360), the range the finder's rotate drag writes.
 *  Rounded to a millionth so a run of nudges from a dragged 37.2398... cannot
 *  walk into 44.99999999 on float error; and a value that rounds to 360 is 0,
 *  or the field would print an angle the drag can never produce. */
export function wrapDeg(v: number): number {
  const w = Number((((v % 360) + 360) % 360).toFixed(6));
  return w >= 360 ? 0 : w;
}

/** One decimal only when there is one: `37°`, `37.2°`. A dragged 30.02 then
 *  reads "30.0°" beside the "30°" stop rather than as a second "30°". */
export function degLabel(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

/** The dial's stops: the twelve 15-degree framings, plus `held` in its place
 *  when it is not one of them, so the angle the card is at is always a stop
 *  the dial can sit on. */
export function rotationStops(held: number | null): number[] {
  if (held == null || ROTS.includes(held)) return [...ROTS];
  return [...ROTS, held].sort((a, b) => a - b);
}

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
  /** SEND TO FLOW WIZARD (#196). Optional so a caller that only draws the
   *  picker (a test of the dial) need not wire a door; the button is left
   *  out without it, never drawn dead. */
  onSendToWizard?: () => void;
  /** Why the wizard cannot be opened, said on a press; null when it can. */
  sendReason?: string | null;
  onExplain?: (reason: string) => void;
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
  onSendToWizard,
  sendReason = null,
  onExplain,
}: FramingCardProps): JSX.Element {
  const commandedPaDeg = commandedPa(rotationDeg);
  const hand = commandedPaDeg != null && rotator ? adjustedPa(commandedPaDeg, rotator, rotatorRange) : null;

  // The off-grid stop is HELD, not recomputed from `rotationDeg` on every
  // render. The Dial scrubs by index from where the drag began, so a stop list
  // that lost the 37 the moment the drag reached 45 would renumber every stop
  // under the finger, and the next pointer sample would land one stop further
  // on (60). Held, the list stays put through the gesture; it moves only when
  // an angle that is not a stop arrives, which the dial itself never sends.
  // Synced during render, React's own idiom for state that follows a prop.
  const [held, setHeld] = useState<number | null>(() => (ROTS.includes(rotationDeg) ? null : rotationDeg));
  if (!ROTS.includes(rotationDeg) && held !== rotationDeg) setHeld(rotationDeg);
  const stops = rotationStops(held);

  // A commit that wraps onto the angle already held (360 typed at 0) changes
  // nothing upstream, so the field would keep showing "360". Bumping this puts
  // the angle the card is really at back in the box.
  const [typed, setTyped] = useState(0);

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
          options={stops.map((v) => ({ value: v, label: `${degLabel(v)}°` }))}
          value={rotationDeg}
          onChange={onRotate}
          data-testid="sky-rot-dial"
        />

        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <NumberField
            label="PA"
            unit="°"
            ariaLabel="Camera position angle, degrees"
            value={Math.round(rotationDeg * 10) / 10}
            resetKey={typed}
            onCommit={(v) => { setTyped((n) => n + 1); onRotate(wrapDeg(v)); }}
            data-testid="sky-rot-deg"
          />
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,minmax(0,1fr))", gap: 6 }}>
            {NUDGES.map((d) => (
              <button
                key={d}
                type="button"
                className="nx-stepper-btn"
                data-nudge={d}
                aria-label={`Camera rotation ${d < 0 ? "minus" : "plus"} ${Math.abs(d)} degrees`}
                onClick={() => onRotate(wrapDeg(rotationDeg + d))}
                style={{ width: "100%", fontSize: 12 }}
              >
                {d < 0 ? `-${Math.abs(d)}` : `+${d}`}
              </button>
            ))}
          </div>
        </div>

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

        {onSendToWizard && (
          <ActionButton
            kind="primary"
            full
            data-testid="sky-send-to-wizard"
            lockedReason={sendReason}
            onExplain={onExplain}
            onPress={onSendToWizard}
          >
            {SEND_TO_WIZARD}
          </ActionButton>
        )}

        <div data-testid="sky-framing-note" style={{ fontSize: 11.5, color: "var(--text-faint)", lineHeight: 1.5 }}>
          {framingNote(overlap)}
        </div>
      </div>
    </Card>
  );
}
