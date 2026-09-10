// calibrationModel.ts - the copy and the field table for the Calibration and
// Sky-pack tuning area (wave R7, T-R7-14; plan sections 3.F15, 3.F16, 3.F17).
//
// Pure: no React, no store, no fetch. It exists so a test can grade the
// sentences the screens print against the same constants the screens render,
// rather than against a second copy typed into the test - which is how a
// rebuild loses a warning nobody notices is gone.
//
// PROVENANCE. Every hint below is transcribed from
// `components/settings/CalibrationTolerancesPanel.tsx` and
// `components/settings/SkyAtlasPanel.tsx`, which stay untouched for `#/classic`.
// Two em-dashes in the legacy strings are hyphens here (the copy rule), and
// nothing else changed: these sentences are the only place the app explains why
// a master from the wrong temperature is worse than no master at all.

import { accessPhrase } from "../../../../../lib/caps";
import type { CalibrationBuildReport } from "../../../../../lib/calibrationLibrary";
import type { CalibrationConfig } from "../../../../../types";

// ------------------------------------------------------------ master library

export const LIB_TITLE = "MASTER LIBRARY";

export const LIB_INTRO =
  "Master darks, flats and bias built from the calibration frames under your capture folder. "
  + "Rebuild after capturing new calibration frames.";

export const LIB_EMPTY_TITLE = "NO MASTERS YET";

export const LIB_EMPTY_HINT =
  "Capture darks, flats or bias frames, then rebuild - the library is built from what is already "
  + "on disk, not from a separate download.";

export const BUILD_LABEL = "REBUILD LIBRARY";

export const BUILD_FAILED = "Could not build. The library still holds what is listed below.";

export const LIB_LOAD_RETRY = "RETRY";

/** A load failure must never render as an empty library: "no masters yet" is an
 *  instruction to go and capture some, and following it would waste a night. */
export function libraryLoadError(detail: string): string {
  return `Couldn't load masters: ${detail}. The rig may still have them.`;
}

export const LIB_LOAD_FALLBACK = "the server did not say why";

/** What the build actually did. The legacy panel said this in a toast that left
 *  after 2.8 s; the same numbers are the only evidence that a rebuild which
 *  changed nothing found nothing to change, so they stay on screen. */
export function buildReportLine(r: CalibrationBuildReport): string {
  const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? "" : "s"}`;
  return `Built ${plural(r.masters_built, "master")} from ${plural(r.frames_indexed, "frame")} `
    + `in ${plural(r.buckets, "matching group")}.`;
}

/** Heading for one type group, with the count of rows actually shown. */
export function groupHeading(type: string, count: number): string {
  return `${type.toUpperCase()}S (${count})`;
}

export function deleteConfirmTitle(): string {
  return "Delete this master?";
}

export const DELETE_CONFIRM_BODY_TAIL =
  "The frames it was built from stay on disk, so a rebuild can make it again.";

export const DELETE_FAILED = "Could not delete. The master is still in the library.";

/** The library's one read-only sentence. `LockNote` prefixes "Read-only - ",
 *  and `lockReason()` already writes the clause, so this only names WHICH
 *  actions the clause is about - and only when the clause is the capability
 *  one, because "building or deleting masters the rig is not reachable" is not
 *  a sentence. */
export function libraryLockSentence(reason: string): string {
  return reason === `needs ${accessPhrase("control.capture")}`
    ? `building and deleting masters needs ${accessPhrase("control.capture")} (control.capture)`
    : reason;
}

// -------------------------------------------------------- matching tolerances

export const TOL_TITLE = "MATCHING AND STACKING";

export const TOL_INTRO =
  "How close a master has to be to the light it corrects before it is reused. Loosen these to get "
  + "more nights out of one set of darks; tighten them if you see residual amp glow or mismatched "
  + "noise.";

/** The server's own defaults (`config.CalibrationConfig`), repeated here so the
 *  panel is never blank on a cold open - the WS bootstrap payload omits the
 *  block until it has been written once. */
export const TOL_DEFAULTS: CalibrationConfig = {
  exposure_tol_pct: 5,
  temp_tol_c: 2,
  temp_bin_c: 5,
  stack_sigma: 3,
  max_stack_frames: 100,
};

export interface ToleranceField {
  key: keyof CalibrationConfig;
  label: string;
  unit?: string;
  hint: string;
  /** The full sentence a screen reader hears: the visible label is an eyebrow
   *  and the unit is decoration. */
  aria: string;
  min: number;
  max: number;
  step: number;
  integer?: boolean;
}

export const TOLERANCE_FIELDS: readonly ToleranceField[] = [
  {
    key: "exposure_tol_pct",
    label: "Exposure tolerance",
    unit: "%",
    aria: "Exposure tolerance, percent",
    min: 0, max: 100, step: 1,
    hint: "A master dark may differ from the light's exposure by this much. Dark current scales "
      + "with time, so 5% is tight and 20% starts to show.",
  },
  {
    key: "temp_tol_c",
    label: "Temperature tolerance",
    unit: "C",
    aria: "Temperature tolerance, degrees Celsius",
    min: 0, max: 50, step: 0.5,
    hint: "A master may differ from the light's sensor temperature by this much. Dark current "
      + "roughly doubles every 6 C, so this is the setting that matters most.",
  },
  {
    key: "temp_bin_c",
    label: "Temperature bin",
    unit: "C",
    aria: "Temperature bin width, degrees Celsius",
    min: 0, max: 50, step: 0.5,
    hint: "Width of the bucket frames are grouped into when a master is built. Must be at least "
      + "the tolerance above, or frames that matched end up in different stacks.",
  },
  {
    key: "stack_sigma",
    label: "Sigma clip",
    aria: "Sigma clip threshold",
    min: 0.5, max: 10, step: 0.5,
    hint: "How far from the median a pixel may be before it is thrown out when combining. Lower "
      + "rejects more aggressively - good against satellites, bad if you have few frames.",
  },
  {
    key: "max_stack_frames",
    label: "Frames per master",
    aria: "Maximum frames per master",
    min: 1, max: 1000, step: 10, integer: true,
    hint: "Cap on how many frames go into one master. Past a point more frames buy almost no "
      + "noise reduction and cost real memory.",
  },
];

/** The one rule pydantic cannot express, checked here for a live warning AND
 *  server-side in `set_calibration` - because a warning the user can click past
 *  is not a guard. */
export function binTooNarrow(d: CalibrationConfig): boolean {
  return d.temp_bin_c < d.temp_tol_c;
}

export function binWarning(d: CalibrationConfig): string {
  return `The temperature bin (${d.temp_bin_c} C) is narrower than the match tolerance `
    + `(${d.temp_tol_c} C). Two frames could match each other and still be stacked separately, `
    + `halving the depth of every master. Raise the bin to at least ${d.temp_tol_c} C.`;
}

/** The header readout: the two numbers that decide whether last week's darks
 *  get reused tonight, without opening a field. */
export function toleranceSummary(d: CalibrationConfig): string {
  return `${d.exposure_tol_pct}% exposure - ${d.temp_tol_c} C temperature`;
}

export const TOL_SAVE_LABEL = "SAVE";
export const TOL_RESET_LABEL = "RESET TO DEFAULTS";
export const TOL_DIRTY_NOTE = "Unsaved changes - the rig still matches on the saved numbers.";
export const TOL_SAVED_NOTE = "Saved. The rig is matching on these numbers now.";
export const TOL_SAVE_FAILED = "Could not save. The rig still has the numbers it had before.";

export const TOL_CLEAN_REASON = "nothing has changed since the last save";
export const TOL_BUSY_REASON = "the last save is still in flight";
export const TOL_ALREADY_DEFAULT_REASON = "these are already the defaults";
export const TOL_BIN_REASON =
  "the temperature bin is narrower than the match tolerance - raise the bin first";

export function toleranceLockSentence(reason: string): string {
  return reason === `needs ${accessPhrase("config.site_optics")}`
    ? `changing the matching tolerances needs ${accessPhrase("config.site_optics")} `
      + "(config.site_optics)"
    : reason;
}

export function toleranceForbidden(): string {
  return `Refused - ${accessPhrase("config.site_optics")} is needed here.`;
}

// --------------------------------------------------------------- sky pack

export const ONLINE_TITLE = "ONLINE SURVEY FETCH";
export const ONLINE_SWITCH_LABEL = "Online survey fetch (CDS)";
export const ONLINE_BLURB =
  "When on, small fields load full-resolution imagery from CDS; the offline pack remains the "
  + "fallback.";

export const PACK_TITLE = "OFFLINE PACK";

export const PACK_DOWNLOAD_LABEL = "DOWNLOAD OFFLINE SKY PACK (~250 MB)";
export const PACK_UPDATE_LABEL = "UPDATE OFFLINE SKY PACK";
export const PACK_DELETE_LABEL = "DELETE PACK";

export const PACK_PROGRESS_ARIA = "Pack download progress";

export const PACK_RETRY_HINT =
  "Some tiles failed - running the download again resumes and retries them.";

export const PACK_DELETE_TITLE = "Delete offline sky pack?";
export const PACK_DELETE_BODY =
  "The Atlas will have no survey imagery until it's downloaded again (or online fetch is enabled).";
export const PACK_DELETE_CONFIRM = "Delete pack";
export const PACK_DELETED_TOAST = "offline sky pack deleted";

export const PACK_ATTRIBUTION =
  "DSS2 imagery (c) AAO/STScI, served from CDS/ESA HiPS mirrors.";

export const PACK_STATUS_FALLBACK = "couldn't load pack status";

export const PACK_BUSY_REASON = "the last change is still in flight";
export const PACK_FETCHING_REASON =
  "a download is already running - the progress bar above is it";

export const SURVEY_SAVE_FAILED = "Could not save. Online fetch is still set the way it was.";
export const PACK_FETCH_FAILED = "Could not start the download. Nothing was fetched.";
export const PACK_DELETE_FAILED = "Could not delete. The pack is still on disk.";

/** 507 is the one failure a user can act on without a log: the pack needs about
 *  250 MB and the capture volume has not got it. */
export const PACK_NO_SPACE =
  "Not enough space on the capture volume for the pack (it needs about 250 MB).";

export function surveyLockSentence(reason: string): string {
  return reason === `needs ${accessPhrase("config.site_optics")}`
    ? `changing survey settings and the offline pack needs ${accessPhrase("config.site_optics")} `
      + "(config.site_optics)"
    : reason;
}

export function surveyForbidden(): string {
  return `Refused - ${accessPhrase("config.site_optics")} is needed here.`;
}
