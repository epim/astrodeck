// guiderModel.ts - the Guider sheet's pure sentences and readouts
// (plan hub-rig.md B.7). No React, no store, no fetch: every function here is
// data in, string out, so the copy that used to be tangled through
// `views/GuideView.tsx`'s 1570-line render can be read - and asserted - on its
// own.
//
// WHY THESE PARTICULAR SENTENCES ARE PURE FUNCTIONS. Each one is a decision
// that was got wrong at least once in the shipped view, and the record of what
// it should say lives in a comment beside the code that says it:
//
//   `startingUp` reads the BUSY LANE, never the phase hint (GuideView.tsx:126-140).
//   The native guider sets `_phase_hint = "calibrating"` before a walk and
//   clears it only on the success path, so a walk that times out leaves the hint
//   standing on every 2 s status frame afterwards. Trusting it locked Start,
//   Stop and Force-Recalibrate for the rest of the session - the three controls
//   that recover the rig. The lane is the cross-provider truth; `phase` only
//   names WHICH step, which is what makes these reasons worth reading.
//
//   `stopExtra` is the one that must almost always be null. Stop is an escape
//   hatch and is never gated on the lane it exists to end (plan section C's
//   never-blocked list). The single case where it IS refused is a NATIVE
//   guider's star-search / calibration walk, where `stop_guiding` sets a flag
//   the walk never checks - so the press would be swallowed, and dimming it with
//   the reason is the honest answer.
//
//   `calibrateExtra` blocks a press that would not merely 409. `/api/guide/
//   calibrate` spawns with `replace=True`, so a second press CANCELS the walk in
//   flight and restarts it from zero.
//
// Copy: hyphens, never em-dashes (ARCHITECTURE.md non-negotiable 5). The
// sentences are the shipped ones with that one substitution.

import type { CalibrationReport, GuideStats } from "../../../../types";

// ------------------------------------------------------------ the phase words

/** What the `guide` lane is doing right now, in the user's words. Three of the
 *  reasons below quote it, so the sheet never offers two different accounts of
 *  the same operation (GuideView.tsx:186-191). */
export function startingWhat(phase: string): string {
  if (phase === "calibrating") {
    return "Calibrating the guider - the mount is learning which way it moves";
  }
  if (phase === "finding") return "Looking for a guide star";
  return "Guiding is already starting";
}

export interface GuideActionInput {
  /** `GuideStats.guiding` - the loop is up and locked. */
  guiding: boolean;
  /** The `guide` BUSY LANE (plus the local arm latch), never the phase hint.
   *  See the module header. */
  startingUp: boolean;
  /** The lane as the server reports it right now, with no local latch - Stop's
   *  copy must follow the rig, not an optimistic click of ours. */
  laneLive: boolean;
  /** `GuideStats.phase`, or "" when the guider publishes none (every bridge
   *  guider). Only ever used to NAME a step, never to decide one. */
  phase: string;
  /** A start/recalibrate request is still in its round trip. */
  acting: boolean;
}

/** LOOP + PICK STAR's caller-specific reason, after cap and role. */
export function startExtra(i: GuideActionInput): string | null {
  if (i.guiding) return "Guiding is already running";
  // Pressing again here is answered `'guide' is already running` - a 409 the
  // user reads as a fault, half a minute into a start that is going fine.
  if (i.startingUp) return `${startingWhat(i.phase)}. Guiding begins on its own when it finishes.`;
  if (i.acting) return "Still working on the last command";
  return null;
}

/** STOP GUIDING's caller-specific reason. Null in every state the press would
 *  actually reach the rig - including a busy `guide` lane, which is exactly
 *  what Stop is for. */
export function stopExtra(i: GuideActionInput): string | null {
  if (i.guiding) return null;
  if (i.laneLive) {
    // Inside the lane the NATIVE guider polls nothing: `stop_guiding` sets
    // `_stop`, the walk never looks at it, and `start_guiding` clears it again
    // on the way into the loop - so the press is swallowed and guiding starts
    // anyway. A bridge guider (PHD2/NINA) publishes no phase and its stop DOES
    // abort a start, so it keeps a live Stop.
    if (i.phase === "calibrating" || i.phase === "finding") {
      return `${startingWhat(i.phase)}. This step cannot be interrupted - Stop works once guiding is running.`;
    }
    return null;
  }
  // Lane finished, `guiding` not yet true: the loop IS up and hunting its lock,
  // and Stop ends it. Only a genuinely idle guider has nothing to stop.
  if (i.phase === "finding") return null;
  return "Guiding is not running - there is nothing to stop";
}

/** RECALIBRATE's caller-specific reason. */
export function calibrateExtra(i: GuideActionInput): string | null {
  if (i.startingUp) {
    return `${startingWhat(i.phase)}. Pressing again cancels it and starts the calibration over.`;
  }
  if (i.acting) return "Still working on the last command";
  return null;
}

/** DITHER NOW's caller-specific reason. */
export function ditherExtra(guiding: boolean): string | null {
  return guiding ? null : "Start guiding first - a dither nudges the star and re-settles";
}

// -------------------------------------------------------------- the live line

export interface LiveLineInput {
  connected: boolean;
  /** `guideNarration(...).phaseText` - the plain-language state. */
  phaseText: string;
  stats: GuideStats | null;
  /** "″" or "px", per `GuideStats.is_arcsec`. */
  unit: string;
}

/** The ONE live line in the sheet header. It is state, never a caption: it must
 *  carry numbers the body does not repeat verbatim (plan section 0.2). When the
 *  guider is measuring, that is the RMS and the star SNR; when it is not, the
 *  phase alone is the whole of what is known. */
export function liveLine(i: LiveLineInput): string {
  if (!i.connected) return "not connected";
  const s = i.stats;
  const parts = [i.phaseText.toLowerCase()];
  if (s && s.guiding && s.rms_total > 0) {
    parts.push(`${s.rms_total.toFixed(2)}${i.unit} RMS`);
    if (s.snr > 0) parts.push(`star SNR ${s.snr.toFixed(0)}`);
  }
  return parts.join(" · ");
}

// ------------------------------------------------------------ the calibration

/** The CALIBRATION row's sub-line, built only from fields the report actually
 *  carries. `px/step` is NOT one of them (`CalibrationReport` is
 *  `{is_valid, ortho_error_deg, declination_deg, pier_side, binning,
 *  advisories, source}`, types.ts:269-277), so it is not printed - a number the
 *  wire does not carry is the one thing a calibration line must never invent. */
export function calibrationLine(report: CalibrationReport | null): string {
  if (!report || !report.is_valid) return "not calibrated";
  const parts = [
    `ortho ${report.ortho_error_deg.toFixed(1)}°`,
    `bin ${report.binning}x`,
  ];
  if (report.declination_deg != null) parts.push(`dec ${report.declination_deg.toFixed(0)}°`);
  if (report.pier_side && report.pier_side !== "unknown") parts.push(`pier ${report.pier_side}`);
  return parts.join(" · ");
}

/** The calibration LED's three states. Orthogonality above ~10 degrees means
 *  the RA/Dec axes are not square - the classic sign of a poor or
 *  wrong-declination calibration (GuideView.tsx:412-424). */
export function calibrationTone(report: CalibrationReport | null): "good" | "warn" | "bad" {
  if (!report || !report.is_valid) return "bad";
  return report.ortho_error_deg > 10 ? "warn" : "good";
}

/** The half of CLEAR CALIBRATION this sheet is evidence of. `GET /api/guide/
 *  calibration` reads the ENGINE's live in-memory calibration; `DELETE` only
 *  unlinks the PERSISTED file. The numbers above the note therefore do not
 *  change, and saying WHICH copy went is the difference between a control that
 *  looks broken and one the user can plan around (GuideView.tsx:152-172). */
export function savedCalClearedNote(guiding: boolean): string {
  return guiding
    ? "The saved copy was cleared. These numbers are the calibration guiding is "
      + "using right now - it keeps them until it stops, then the next start "
      + "calibrates from scratch. RECALIBRATE replaces them now."
    : "The saved copy was cleared. These numbers are what the last run measured, "
      + "still held in memory; nothing is stored, so the next start calibrates "
      + "from scratch.";
}

// ------------------------------------------------------- guide-camera preview

export interface PreviewNote {
  tone: "warn" | "dim";
  text: string;
}

/** The guide preview's 4-state honesty narration, transcribed from
 *  `views/CaptureView.tsx:114-140`, which is the only place it has ever been
 *  rendered although all three mount points (Capture, Guide, Polar) need it.
 *
 *  The four states are all load-bearing and are NOT interchangeable:
 *    refused      the server said why there is no picture, in its own words.
 *    checked      it decoded the frame and found it VARIES, so a dark preview
 *                 is a dark sky and not an empty buffer.
 *    not checked  bytes forwarded without the server being able to read them.
 *    silent       nobody has asked this camera recently - the normal state of a
 *                 collapsed panel, and the one that must print nothing.
 *
 *  `srcName` is `guide_camera.preview_source`, NOT `.name`: the hub picks those
 *  two by opposite rules, so on a rig running PHD2 with a guide camera also
 *  assigned they are different instruments. */
export function previewNote(
  reason: string,
  ok: boolean | undefined,
  srcName: string,
): PreviewNote | null {
  if (reason) return { tone: "warn", text: reason };
  if (ok === true) {
    return {
      tone: "dim",
      text: "Checked - this frame varies, so a dark preview is the sky and not an empty buffer.",
    };
  }
  if (ok === false) {
    return {
      tone: "warn",
      text: `Not checked - ${srcName} sent bytes this server could not decode. `
        + "Your browser may render them; nothing here vouches for what is in them.",
    };
  }
  return null;
}

// ------------------------------------------------------------------ the tiles

/** EXPOSURE's sub-line. The bands are the design's own (fast / balanced /
 *  slow), attached to the number rather than narrating it. */
export function exposureSub(seconds: number): string {
  if (seconds < 2) return "fast · chases seeing";
  if (seconds === 2) return "balanced";
  return "slow · smooths seeing";
}

/** The STAR tile's value. Only `snr` is on the stats bus (plan E16), so the
 *  prototype's "mag 8.1 · 3.1 px" clauses are dropped rather than invented. */
export function starValue(stats: GuideStats | null): string {
  if (!stats || !(stats.snr > 0)) return "--";
  return `SNR ${stats.snr.toFixed(0)}`;
}

/** Both axes at once, and the note that says so. AGGRESSION and MIN MOVE are
 *  per-axis params on the wire (`ra_params` / `dec_params`); one dial writing
 *  one number has to write both, and the user has to be told where the per-axis
 *  values live (plan E15). */
export const BOTH_AXES_NOTE =
  "Sets both axes. Per-axis values are in the tuning editor.";

/** MIN MOVE is in guide-camera PIXELS, on every rig, whatever the RMS is
 *  reported in. `docs/native-parity/algorithms/phd2-guiding.md:744` lists it as
 *  "Min move (px)", and the engine's `min_move` is compared against a pixel
 *  offset. Printing a prime on it would be the UX-15 defect (pixels labelled
 *  arcsec) in a second place. */
export const MIN_MOVE_UNIT = "px";
