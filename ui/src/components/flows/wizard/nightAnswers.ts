// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// nightAnswers.ts - the NIGHT answers' constants and reader that the stepped
// Send to Flow Wizard sheet and the NEW FLOW sheets (classic and #/next) share.
//
// A LEAF, ON PURPOSE. It imports nothing: the classic flows view reaches it
// through FlowWizard.tsx, and the framing modules (`wizardModel.ts` pulls them
// in for the review's readouts) belong to the lazy framing chunk, not the
// classic flows chunk (classicFrameHost.test.tsx holds that). `wizardModel.ts`
// re-exports everything here, so its importers do not change.

/** wizard.py `CLOCK_TIME`: DUSK WINDOW's choice that reads its time from a
 *  clock field, for Start and for Stop alike. */
export const CLOCK_TIME = "Clock time";

/** wizard.py `DUSK_STOPS`: the Stop choices the wizard offers. The editor's
 *  third, "None", is not one: a run with no stop images into daylight and does
 *  not park at dawn, and the server refuses it for a wizard night. */
export const DUSK_STOPS = ["Dawn", "Clock time"] as const;
export type DuskStop = (typeof DUSK_STOPS)[number];

/** A typed clock time as the server reads it (`checked_clock`): trimmed,
 *  strict HH:MM on the 24-hour clock, or null. ASCII digits only, as there. */
export function clockOf(text: string): string | null {
  const t = text.trim();
  return /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(t) ? t : null;
}

/** A "Clock time" stop with no time is a boundary that never arrives, and the
 *  server refuses it, so the sheet says so before GENERATE. */
export const NEED_STOP_CLOCK =
  "Type the time the night stops, as HH:MM on the 24-hour clock (03:30): a Clock time stop has no other way to say when.";

/** wizard.py `MAX_TIME_MAX_MIN`, which is `Target.max_time_min`'s own ceiling:
 *  a day, in minutes. */
export const MAX_TIME_MAX_MIN = 1440;

/** A typed time bound (#1094, owner ruling D-D) as a whole number of minutes
 *  from 1 to `MAX_TIME_MAX_MIN`, or null for a blank (no bound) and for
 *  anything else. The generator's own reading (`checked_max_time`) refuses a
 *  fraction, a bool and a number out of range; ASCII digits only, as there. */
export function maxTimeOf(text: string): number | null {
  const t = text.trim();
  if (!/^[0-9]+$/.test(t)) return null;
  const n = Number(t);
  return n >= 1 && n <= MAX_TIME_MAX_MIN ? n : null;
}

/** A time bound that was typed and cannot be read. A blank is no bound and no
 *  gap; "0" is the generator's own "no bound", but the sheets ask for a blank,
 *  so it is a gap and says so. */
export function maxTimeUnreadable(text: string): boolean {
  return text.trim() !== "" && maxTimeOf(text) === null;
}

export const NEED_MAX_TIME =
  `Type the most minutes to spend on the target as a whole number from 1 to ${MAX_TIME_MAX_MIN}, or leave it blank for no limit.`;
