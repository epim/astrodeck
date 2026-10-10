// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// planTarget.ts - which target of the plan is exposing, from the sequence
// state's `target_index`, and the three things that field can say.

import type { SequenceState } from "../types";

/** The index of the plan target the run is exposing, or null when none is.
 *
 *  `target_index` says one of three things (#941):
 *
 *   * a number: that target of the plan is exposing;
 *   * null, the key present: a calibration set the plan does not hold is
 *     exposing (DUSK FLATS, day darks, a cloud-hold dark), and `target` names
 *     that set. NO plan target is active, so nothing is lit as "now" and no
 *     "k of N" is written;
 *   * absent: the run has not published a target yet, which every screen has
 *     always read as the first one.
 *
 *  `?? 0` read the second as the third, so the first target of the plan was
 *  lit and counted through the flats. */
export function activePlanTarget(
  seq: Pick<SequenceState, "target_index">,
): number | null {
  if (seq.target_index === null) return null;
  return typeof seq.target_index === "number" && Number.isFinite(seq.target_index)
    ? seq.target_index : 0;
}
