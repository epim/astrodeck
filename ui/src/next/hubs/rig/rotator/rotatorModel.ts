// rotatorModel.ts - the ROTATOR sheet's pure model (wave R7, T-R7-8).
//
// No React, no store, no fetch, no `window`: everything here is a function of
// its arguments, so it can be read by a test that never mounts a tree and it
// cannot drift with the component's render order.
//
// WHAT IS *NOT* HERE, DELIBERATELY. The angle maths is not re-derived. The
// mechanical/sky offset is not on the wire - `lib/rotation.ts` derives it from
// the two live values (`adjustedPa`, `mod360`) - and the dial's arc geometry is
// `lib/rotatorDial.ts` (`allowedSweepDeg`, `arcPath`, `polarXY`). Both are
// shared with the legacy card and with the server's own transcription of the
// same reference algorithm; a second copy here would be the exact defect the
// reuse map exists to prevent.

import { accessPhrase } from "../../../../lib/caps";
import type { RotatorConfig } from "../../../../types";

/** The config block's defaults, mirroring `server/astrodeck/config.py`'s
 *  `RotatorConfig`. Re-declared here rather than imported from
 *  `components/equipment/RotatorCard.tsx` (which also exports it): importing a
 *  constant out of a legacy presentation module drags that module, its `Panel`
 *  chrome and its whole Tailwind tree into the lazily-split `next` bundle. The
 *  legacy file keeps its own copy for `#/classic`. */
export const DEFAULT_ROTATOR_CFG: RotatorConfig = {
  range_type: "full",
  range_start_deg: 0,
  tolerance_deg: 1,
};

export const RANGE_OPTIONS: readonly RotatorConfig["range_type"][] =
  ["full", "half", "quarter"] as const;

export const RANGE_LABEL: Record<RotatorConfig["range_type"], string> = {
  full: "FULL",
  half: "HALF",
  quarter: "QUARTER",
};

/** `Number("")` is 0 - finite, plausible and WRONG - so a blank field would
 *  read as a real 0 degrees and command a rotation to it. Route every raw-text
 *  conversion through this instead (the guard from
 *  `components/equipment/RotatorCard.tsx:37`, kept verbatim), so a blank parses
 *  to NaN and is refused at the press. */
export const toNum = (raw: string): number => (raw.trim() === "" ? NaN : Number(raw));

// ------------------------------------------------------------- lock sentences
//
// TWO GATES, NEVER MERGED. Motion (move / halt / reverse / rotate-to-pa /
// sync-to-sky) needs `control.capture`; the range-of-motion CONFIG needs
// `config.backend`. The server enforces them separately
// (`server/astrodeck/api/app.py:6125-6199` vs the `config.backend`-gated
// `POST /api/config/rotator`), and an operator who holds the first and not the
// second must keep a fully live rotator - collapsing them to one lock would
// take the device away from the ordinary case.

export const MOTION_LOCK_NOTE =
  `moving the rotator needs ${accessPhrase("control.capture")}.`;

export const CONFIG_LOCK_NOTE =
  `changing the range of motion needs ${accessPhrase("config.backend")}.`;

/** The sentence a locked half of this sheet says, from the gate's reason.
 *
 *  When the blocker is the CAPABILITY, `lockReason()` returns only
 *  "needs operator or admin access" - true, but it does not say WHICH half of
 *  a sheet with two different capabilities went read-only, so the capability
 *  sentence replaces it. Every other reason the gate can return ("the rig is
 *  not reachable", "connect a camera first", "The rotator is already turning.")
 *  is already a complete fact about the rig and is passed through verbatim:
 *  wrapping it would produce "moving the rotator the rig is not reachable". */
export function lockNote(reason: string | null, capNote: string): string | null {
  if (!reason) return null;
  return reason.startsWith("needs ") ? capNote : reason;
}

/** Why GO has nothing to send yet. Stated rather than greyed out, because an
 *  empty field and a rejected one look identical once the button is dim. */
export const NO_TARGET_NOTE =
  "type a position angle, or drag the dial, and GO will send it.";

/** Why START cannot be edited while the sweep is the whole circle. Not a
 *  permission - a full 360 degree range has no beginning to set. */
export const FULL_RANGE_NOTE =
  "a full 360 degree sweep has no start angle - pick HALF or QUARTER to set one.";

/** The local in-flight guard. The rig answers one rotator command at a time and
 *  the second press would be thrown away, so the reason is stated instead. HALT
 *  never carries it: it is the way out of exactly this state. */
export const IN_FLIGHT_NOTE =
  "the last rotator command has not answered yet.";

/** The out-of-range warning, verbatim from the legacy card minus its warning
 *  glyph and with the em-dash fixed to a hyphen (house copy rule). It says the
 *  angle the rig will actually image at, which is the number the user needs and
 *  the one the field does not show. */
export function outOfRangeLine(targetPa: number, mappedPa: number): string {
  return `PA ${Math.round(targetPa)}° is outside the range of motion - `
    + `it will image as ${Math.round(mappedPa)}°.`;
}

// ------------------------------------------------------------------ dial stops
//
// The sheet has ONE dial and the selected readout tile decides what it edits
// (hub-rig.md 0.3). Two tiles are editable, so there are two stop tables.

export interface Stop { value: number; label: string }

/** MOVE TO: every whole degree of sky position angle.
 *
 *  360 stops is a lot of DOM for one control, and it is still the right answer:
 *  the `Dial` primitive maps a drag to an INDEX in this list
 *  (`from - round(dx / stopPx)`), so a windowed list that slid as the value
 *  changed would accelerate under the finger. The list is only built while the
 *  SKY PA tile is selected, and only one dial is ever mounted. */
export function paStops(): Stop[] {
  return Array.from({ length: 360 }, (_, i) => ({ value: i, label: `${i}°` }));
}

/** TOLERANCE: half a degree per stop up to 10, plus whatever is configured now
 *  if it is not on that grid - a dial whose current value is missing from its
 *  own options snaps the setting the moment it is touched. */
export function toleranceStops(current: number): Stop[] {
  const vals: number[] = [];
  for (let v = 0; v <= 10.0001; v += 0.5) vals.push(Number(v.toFixed(1)));
  if (Number.isFinite(current) && !vals.includes(current)) vals.push(current);
  vals.sort((a, b) => a - b);
  return vals.map((v) => ({ value: v, label: `${v}°` }));
}
