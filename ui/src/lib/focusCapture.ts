// focusCapture.ts — taking a frame FROM the Focus screen, and refusing to sweep
// on numbers nobody measured.
//
// Why this exists: on 2026-07-31, at the telescope, the Focus screen had no way
// to start the camera. Its live preview therefore read "No capture yet" from
// dusk until the user gave up — and deriveAutofocusParams (lib/autofocus.ts),
// which copies the sweep's exposure, gain and binning FROM THAT LIVE FRAME,
// silently fell through to its office defaults: 2s, gain 120, bin 2.
//
// At 2s / gain 120 / bin 2 that field measured 8 stars. At 4s / gain 220 / bin 1
// the same field measures 2100. Two autofocus runs — four minutes each — died on
// that alone, and the screen during and after them was indistinguishable from a
// run that had copied a real frame.
//
// So two things live here, and they are the same bug from both ends:
//   1. the camera controls the screen never had (presets, single, loop, stop)
//   2. a sweep that states where its numbers came from, and will not start when
//      the answer is "nowhere".
//
// Pure: no React, no DOM, no fetch — tested with npx tsx (lib/focusMove.ts is
// the precedent, written for this same class of bug two hours earlier).

import type { AfParamSource } from "./autofocus";

// ------------------------------------------------------------------ presets
/** The exposures a focus check is actually made of.
 *
 *  1s is a bright star through a Bahtinov mask; 10s is hunting for anything
 *  measurable in a faint field with a slow scope. Past 10s a focus loop stops
 *  being a feedback loop — you cannot turn a knob against a 30-second delay —
 *  and below 1s the star profile is seeing noise rather than the optics. */
export const FOCUS_EXPOSURE_PRESETS = [1, 2, 3, 5, 10] as const;

/** The gain a focus frame starts at. Deliberately higher than Capture's 120:
 *  a focus frame is thrown away, so its noise costs nothing and its job is
 *  purely to show as many stars as possible to measure. 120 is what the sweep
 *  guessed on 2026-07-31 and 8 stars is what it got.
 *
 *  DOCUMENTATION ONLY since #176 (2026-08-08). The live value is the `focus`
 *  SCOPE (server/astrodeck/config.py FrameSettingsConfig.focus), which starts
 *  at this same 200; nothing reads this constant to render or send a frame any
 *  more, because a client-side default is exactly what made every screen
 *  disagree with the rig after a reload. Kept because the REASON the focus
 *  scope's gain is not the capture scope's belongs somewhere a reader of this
 *  module will find it. */
export const FOCUS_DEFAULT_GAIN = 200;

// ------------------------------------------------------------------ the POST
export interface FocusCaptureBody {
  exposure_s: number;
  gain: number;
  offset: number;
  binning: number;
  save: boolean;
}

/**
 * Body for POST /api/capture and /api/capture/loop from the Focus screen.
 *
 * `save` is false and is NOT a toggle. Two reasons, and the second is the one
 * that matters: focus frames are diagnostics rather than data, AND
 * /api/capture/loop drops `save` on the floor — `hub.start_loop` has no save
 * parameter at all — so a switch here would be lit for something the server
 * never does. A control that claims an effect it does not have is exactly the
 * failure this whole round is about.
 */
export function focusCaptureBody(p: {
  exposureS: number;
  gain: number;
  binning: number;
  offset?: number;
}): FocusCaptureBody {
  return {
    exposure_s: p.exposureS,
    gain: Math.max(0, Math.round(p.gain)),
    offset: Math.max(0, Math.round(p.offset ?? 30)),
    binning: Math.max(1, Math.round(p.binning)),
    save: false,
  };
}

// ------------------------------------------------------- blocked, and why
/**
 * Why the Focus screen cannot start an exposure, or null when it can.
 *
 * Same house rule as every other gate on this screen (§11.8): a blocked control
 * resolves to a SENTENCE, never a bare boolean, because `disabled` takes the
 * control and its reason out of the accessibility tree and `title=` never fires
 * on the tablet this rig is driven from.
 *
 * Order is by how big a fact it is about the rig: permission, then hardware,
 * then who else owns the camera, then what is typed in the boxes.
 */
export function focusCaptureBlocker(p: {
  /** Already-composed read-only sentence, or null. */
  readOnlyReason: string | null;
  hasCamera: boolean;
  polarBusy: boolean;
  sequenceOwnsCamera: boolean;
  autofocusRunning: boolean;
  exposureInvalid: boolean;
  gainInvalid: boolean;
  /** The sensor's gain ceiling, so a rejected gain can name the limit instead
   *  of saying "fix it" and leaving the user to guess which end is wrong. */
  gainMax?: number | null;
}): string | null {
  if (p.readOnlyReason) return p.readOnlyReason;
  if (!p.hasCamera) return "No camera is connected — connect one on the Equipment page";
  if (p.polarBusy) return "Polar alignment owns the camera right now";
  if (p.sequenceOwnsCamera) return "A sequence owns the camera — stop it first";
  // The sweep exposes continuously for minutes; a manual frame would queue
  // behind the exposure guard and land whenever the sweep let go of it.
  if (p.autofocusRunning) return "Autofocus owns the camera until the sweep finishes";
  if (p.exposureInvalid) return "Type an exposure in seconds first — the box above is empty or not a number";
  if (p.gainInvalid) {
    return p.gainMax != null && p.gainMax > 0
      ? `Gain must be a number between 0 and ${p.gainMax} (this camera's ceiling)`
      : "Gain must be a number of 0 or more";
  }
  return null;
}

/** Tapping a preset while a loop is running. The loop keeps its ORIGINAL
 *  exposure until it is restarted — `hub.start_loop` closes over the arguments
 *  it was given — so a preset tap that only changed a highlight would be a
 *  control that looks applied and is ignored. Restart instead, and say so
 *  before the tap rather than after. */
export function presetAction(looping: boolean, exposureS: number): {
  restartLoop: boolean;
  hint: string;
} {
  return looping
    ? { restartLoop: true, hint: `Restart the loop at ${exposureS}s` }
    : { restartLoop: false, hint: `Use ${exposureS}s for the next frame` };
}

// ----------------------------------------------------------- a frame in flight
/** How long after the exposure should have finished before an absent frame is
 *  a fault rather than a slow readout. A full-frame USB2 download of a 60MP
 *  sensor is tens of seconds; past this something dropped it. Matches
 *  CaptureView's DOWNLOAD_WATCHDOG_MS. */
export const FRAME_READOUT_GRACE_MS = 60_000;

/**
 * What to say between tapping Single and the frame arriving.
 *
 * POST /api/capture returns the moment the task is spawned — before the shutter
 * opens — so without this the tap and no tap look identical for the length of
 * the exposure. Exactly the failure the Go button had (lib/focusMove.ts), on
 * the control next to it.
 */
export function frameWaitNote(p: {
  /** ms epoch when the server ACCEPTED the exposure, or null. */
  startedAt: number | null;
  exposureS: number;
  now: number;
}): { text: string; tone: "info" | "warn" } | null {
  if (p.startedAt == null) return null;
  const elapsedMs = Math.max(0, p.now - p.startedAt);
  const exposureMs = Math.max(0, p.exposureS * 1000);
  if (elapsedMs < exposureMs) {
    const left = Math.ceil((exposureMs - elapsedMs) / 1000);
    return { text: `exposing · ${left}s left`, tone: "info" };
  }
  if (elapsedMs < exposureMs + FRAME_READOUT_GRACE_MS) {
    return { text: "reading out…", tone: "info" };
  }
  // Both numbers, as with a refused focuser move: "no frame" is not actionable,
  // "no frame in 74s for a 4s exposure" points at the camera link.
  return {
    text: `no frame in ${Math.round(elapsedMs / 1000)}s for a ${p.exposureS}s exposure `
      + "— the camera may have dropped it",
    tone: "warn",
  };
}

// --------------------------------------------------- what the sweep will use
export type SweepBasis = "backend" | "manual" | AfParamSource;

export interface SweepReadiness {
  basis: SweepBasis;
  /** Non-null: the sweep must NOT start, and this sentence says what to do
   *  instead. Rendered through the hero's own locked treatment. */
  block: string | null;
  /** The numbers, exactly as they will be sent. */
  summary: string;
  /** Where those numbers came from, in one clause. */
  provenance: string;
  /** What would make them trustworthy, when they are not. */
  warn: string | null;
}

/**
 * State the sweep's parameters and their provenance, and decide whether it may
 * start at all.
 *
 * THE REFUSAL, and why it is a refusal rather than a warning: with no live
 * frame the sweep is not "using defaults", it is using numbers that nothing on
 * this rig, this night, has confirmed produce a measurable star. Refusing costs
 * essentially nothing now that the camera controls are on this same panel —
 * one tap of a preset produces the frame — and it cannot block a sweep that
 * would have worked, because the sweep's own first act is to expose with that
 * same camera at those same settings. A camera that cannot deliver a frame here
 * cannot deliver the sweep's probe either. Before tonight there was no way to
 * take that frame from this screen, which is the only reason the blind fallback
 * ever seemed reasonable.
 *
 * A frame that EXISTS but is star-poor is a different case and proceeds with a
 * warning, mirroring the server's own split (focus/native.py MIN_STARS_TO_SWEEP
 * refuses, SPARSE_FIELD_WARN warns and continues): the client counted stars in
 * a frame taken at different settings from the ones the sweep will use, and the
 * server re-probes with better information before it commits to anything.
 *
 * With the settings panel open the user is steering explicitly — their numbers,
 * their call — so it never blocks, but it still says that nothing has been
 * measured, because that is still true.
 *
 * THE REFUSAL DEFERS. "Tap a preset above, then Single" is only true advice
 * while Single is tappable. With no camera connected, or polar alignment or a
 * sequence holding it, the first cut of this function still printed that
 * sentence on the hero while the Single control two inches above it read "No
 * camera is connected" — a blocked control resolving to a reason that is not
 * merely incomplete but WRONG, and pointing at another blocked control. So it
 * takes the capture blocker and states that instead: the reason nothing has
 * been measured is the reason nothing CAN be measured.
 *
 * And when a BACKEND runs the sweep (NINA), none of this applies: focus/
 * autofocus.py routes `choice.kind == "backend"` straight to the backend's own
 * autofocus and drops every parameter on the way. Blocking there would refuse a
 * sweep that was never going to use our numbers — so instead it says who is
 * choosing, which is the honest answer and is also news to anyone who has been
 * typing into the settings panel on a NINA rig.
 */
export function sweepReadiness(p: {
  /** The autofocus settings panel is open, so the user's fields are in force. */
  manual: boolean;
  hasLiveFrame: boolean;
  liveStars: number | null;
  params: { exposure_s: number; gain: number; binning: number; step: number };
  source: AfParamSource;
  /** Why this screen cannot open the shutter right now (focusCaptureBlocker's
   *  sentence), or null when it can. NOT optional: the refusal's instruction is
   *  "tap a preset, then Single", and a caller that cannot say whether Single
   *  works is a caller that cannot know whether that instruction is possible. */
  captureBlocked: string | null;
  /** False when a backend owns autofocus and our exposure/gain/binning are
   *  never sent. Optional: absent means the native/sim path, which does use
   *  them. */
  paramsSent?: boolean;
}): SweepReadiness {
  const { exposure_s, gain, binning, step } = p.params;
  const summary = `${exposure_s}s · gain ${gain} · bin ${binning} · ${step}-step sweep`;

  if (p.paramsSent === false) {
    return {
      basis: "backend",
      block: null,
      summary: "the backend picks the exposure, gain and binning",
      provenance: "your settings on this screen are not sent to it",
      warn: null,
    };
  }

  if (p.manual) {
    return {
      basis: "manual",
      block: null,
      summary,
      provenance: "your settings — the panel below is open, so these win",
      warn: p.hasLiveFrame
        ? null
        : "No frame has been taken yet, so nothing has confirmed these settings show stars.",
    };
  }

  if (!p.hasLiveFrame) {
    return {
      basis: "no-frame",
      // Two different sentences because they are two different situations. The
      // first is a missing tap; the second is a rig that cannot take the frame
      // at all, and telling that user to "tap Single" would point them at a
      // control that is itself locked with a different reason.
      block: p.captureBlocked
        ? "Autofocus copies the live frame's exposure, gain and binning, and no "
          + `frame has been taken yet: ${p.captureBlocked}.`
        : "Take a frame first: autofocus copies the live frame's exposure, gain and "
          + "binning, and there is no frame to copy. Tap a preset above, then Single.",
      summary,
      // The numbers are already on the summary line beside this, so the warning
      // slot stays empty rather than printing them a second time — the refusal
      // above says what to do and that is the only thing left to say.
      provenance: "nothing measured — defaults, not a match to your sky tonight",
      warn: null,
    };
  }

  if (p.source === "sparse") {
    const n = p.liveStars ?? 0;
    return {
      basis: "sparse",
      block: null,
      summary,
      // Gain and binning ARE copied from a star-poor frame — they are settings
      // the user chose, not measurements the frame failed to make. Only the
      // exposure falls back, because copying an exposure that produced n stars
      // is copying the thing that did not work.
      provenance: `the live frame measured only ${n} stars, so its gain and binning `
        + "are copied but the exposure fell back to a default",
      warn:
        `A sweep defocuses on purpose, and defocusing finds FEWER stars than ${n}, `
        + "never more. Take a longer or higher-gain frame first and this copies it.",
    };
  }

  return {
    basis: "measured",
    block: null,
    summary,
    provenance: `copied from the live frame (${p.liveStars ?? 0} stars)`,
    warn: null,
  };
}

// ------------------------------------------------- the sweep's own frames
/**
 * What the Live Preview panel should say while a sweep is running.
 *
 * Observed 2026-07-31: for four minutes the sweep exposed continuously and the
 * Focus screen's preview said "No capture yet" the entire time. The user watched
 * a black box with no way to tell a working sweep from a hung one. The sweep's
 * frames SHOULD appear here — the server does not publish a `preview` event for
 * them today (focus/native.py exposes through `camera.expose` directly, unlike
 * plate-solve and rotate which both call `hub._publish_preview`) — so until it
 * does, this says which of the two situations we are actually in instead of
 * letting an empty stage imply the camera is idle.
 *
 * @param framesSinceStart new live-preview ids seen since the sweep began.
 *   >0 means frames ARE arriving — but see `loopRunning` before believing they
 *   are the sweep's.
 * @param loopRunning a capture loop is running. /api/capture/loop refuses only
 *   for polar and sequences (api/app.py), NOT for a running sweep, so a loop
 *   started from the Capture screen keeps publishing previews right through an
 *   autofocus run. Its frames are not the sweep's, and silencing this note on
 *   them would let a user read someone else's frames as evidence the sweep is
 *   delivering — the same substitution the note exists to prevent.
 */
export function sweepPreviewNote(p: {
  running: boolean;
  pointsMeasured: number;
  framesSinceStart: number;
  loopRunning?: boolean;
}): string | null {
  if (!p.running) return null;
  // Frames that belong to a running loop are not evidence about the sweep.
  const loopFrames = !!p.loopRunning && p.framesSinceStart > 0;
  if (p.framesSinceStart > 0 && !loopFrames) return null;
  // Name the owner when there is one. "No frame from the sweep has reached this
  // screen" is false-sounding with pictures visibly landing on the stage, and a
  // sentence the screen contradicts is a sentence nobody reads again.
  const whose = loopFrames
    ? "the frames on the stage are your capture loop's, not the sweep's"
    : "no frame from the sweep has reached this screen";
  if (p.pointsMeasured <= 0) {
    return loopFrames
      ? `Autofocus is exposing its first frame — ${whose}.`
      : "Autofocus is exposing its first frame — nothing has come back yet.";
  }
  return `${p.pointsMeasured} ${p.pointsMeasured === 1 ? "point" : "points"} measured, `
    + `but ${whose}. The V-curve below is the live evidence — it advances once `
    + "per point.";
}
