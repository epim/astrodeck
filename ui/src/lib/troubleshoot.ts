// troubleshoot.ts — the pure failure→{cause,fix,topic} map (NOV-9) and the
// browsable symptom→guide content behind the in-app Help view. Both are pure,
// data-only, tsx-testable: no React, no store, no I/O. diagnoseFailure() turns a
// raw error string (a sequence detail or a log message) into a plain cause +
// first fix + an in-app topic anchor; TROUBLESHOOTING is the reference content
// the Help view renders and that a diagnosis's topic deep-links into.
import type { TroubleshootTopic } from "../types";

export interface Diagnosis {
  /** Short sentence-case headline, e.g. "Plate-solve failed". */
  title: string;
  /** Plain-language most-likely cause (one sentence). */
  cause: string;
  /** The first concrete thing to try (one sentence). */
  fix: string;
  /** In-app Help topic to deep-link to, or null when there's no dedicated guide. */
  topic: TroubleshootTopic | null;
}

interface Rule { needles: string[]; diag: Diagnosis; }

// Ordered most-specific first; the first rule whose needles ALL match wins.
const RULES: readonly Rule[] = [
  { needles: ["plate", "solve"], diag: {
      title: "Plate-solve failed",
      cause: "The frame didn't have enough recognisable stars to match the sky — usually soft focus, too short an exposure, or cloud.",
      fix: "Refocus, raise the exposure a little and try again; if it still fails, solve manually from a bright named star.",
      topic: "wont-solve" } },
  { needles: ["cool"], diag: {
      title: "Cooler didn't reach target",
      cause: "The set-point may be colder than the cooler can hold tonight, or the camera isn't getting enough power.",
      fix: "Raise the target a few degrees (aim ~25–30°C below ambient) and give the camera its own 12V supply.",
      topic: "cooler-stuck" } },
  { needles: ["guid"], diag: {
      title: "Guiding failed",
      cause: "The guide star was lost — cloud, a cable snag, or a mount lurch pushed it off the guide sensor.",
      fix: "Pick a brighter guide star on the Guide page and re-run calibration if the mount was slewed.",
      topic: "guiding-lost" } },
  { needles: ["camera"], diag: {
      title: "Camera isn't responding",
      cause: "The imaging camera dropped its USB connection — often a marginal cable, a hub power dip, or the driver crashing.",
      fix: "Re-seat the camera USB cable (a powered hub helps), then reconnect it on the Equipment page.",
      topic: "camera-offline" } },
  { needles: ["slew"], diag: {
      title: "Mount move failed",
      cause: "The slew didn't complete — the mount may be parked, past a limit, or lost communication mid-move.",
      fix: "Unpark and confirm tracking on the Mount page, then retry the slew.",
      topic: "mount-move-failed" } },
  { needles: ["mount"], diag: {
      title: "Mount move failed",
      cause: "The mount refused or couldn't finish a move — it may be parked, past a limit, or not tracking.",
      fix: "Unpark and confirm tracking on the Mount page, then retry.",
      topic: "mount-move-failed" } },
  { needles: ["focus"], diag: {
      title: "Autofocus failed",
      cause: "The routine couldn't fit a V-curve — too few stars, the step size too large, or the focuser slipping.",
      fix: "Start from roughly-good focus, lower the step size and re-run on a star-rich field.",
      topic: "autofocus-failed" } },
  { needles: ["nina"], diag: {
      title: "NINA reported an error",
      cause: "The NINA bridge returned an error — the imaging PC, a device driver, or the sequence in NINA hit a problem.",
      fix: "Open NINA on the imaging PC, clear the error there, then resume.",
      topic: "nina-error" } },
];

const GENERIC: Diagnosis = {
  title: "The run stopped",
  cause: "Something interrupted the sequence and it couldn't continue.",
  fix: "Open the event log for the exact message, then retry once the cause is clear.",
  topic: null,
};

/**
 * Map a raw error string (a sequence `detail` or a log message) to a plain
 * cause + first fix + in-app topic. Case-insensitive; first matching rule wins;
 * unknown/empty input returns a safe generic diagnosis. Never throws.
 */
export function diagnoseFailure(raw: string | undefined): Diagnosis {
  const text = (raw ?? "").toLowerCase();
  if (!text.trim()) return GENERIC;
  for (const rule of RULES) {
    if (rule.needles.every((n) => text.includes(n))) return rule.diag;
  }
  return GENERIC;
}

export interface TroubleshootEntry {
  topic: TroubleshootTopic;
  /** The symptom in the operator's own words — the page heading. */
  symptom: string;
  /** A short plain-language explanation of what's going on. */
  cause: string;
  /** Ordered things to try, most-likely-first. */
  steps: readonly string[];
  /** Optional HELP glossary keys to cross-link ("learn the terms"). */
  seeAlso?: readonly string[];
}

export const TROUBLESHOOTING: readonly TroubleshootEntry[] = [
  { topic: "black-frame", symptom: "My image is completely black",
    cause: "The sensor isn't seeing light, or the display stretch is hiding what little there is.",
    steps: [
      "Take the lens/dust cap off and open the flat panel or focuser cover.",
      "Turn on Auto-stretch in the preview — a faint sky often looks black un-stretched.",
      "Raise the exposure (try 2–5s for framing) and set gain to a mid value.",
      "Confirm the camera is actually capturing on the Capture page, not just connected.",
    ], seeAlso: ["gain", "exposure"] },
  { topic: "star-trails", symptom: "Stars are streaks or short lines",
    cause: "The sky moved during the exposure — the mount wasn't tracking, wasn't guiding, or was bumped.",
    steps: [
      "Confirm the mount is tracking (not parked) on the Mount page.",
      "Turn on guiding for exposures longer than ~20–30s.",
      "Check polar alignment — poor alignment trails stars slowly even while tracking.",
      "Shorten the sub-exposure and take more frames if guiding isn't available.",
    ], seeAlso: ["guiding", "meridianFlip"] },
  { topic: "elongated-stars", symptom: "Stars look like small eggs, not round",
    cause: "Slight drift, tilt, or flexure during the sub — less severe than full trails.",
    steps: [
      "Recalibrate and tune guiding; check for a cable dragging on the mount.",
      "Reduce sub-exposure length until stars round up, then add more subs.",
      "If only the corners are elongated, check the camera is square to the focuser (tilt).",
    ], seeAlso: ["guiding", "hfr"] },
  { topic: "wont-solve", symptom: "Plate-solving keeps failing",
    cause: "The solver can't find enough stars to match — usually focus, exposure, or cloud.",
    steps: [
      "Refocus until stars are tight (watch HFR).",
      "Raise the solve exposure so more stars register.",
      "Make sure the entered focal length / pixel scale is roughly right.",
      "Wait out passing cloud, or solve manually from a bright named star.",
    ], seeAlso: ["plateSolve", "hfr"] },
  { topic: "camera-offline", symptom: "The camera keeps disconnecting",
    cause: "USB dropouts — a marginal cable, an unpowered hub, or a dip on the camera's 12V.",
    steps: [
      "Re-seat both ends of the camera USB cable.",
      "Use a powered USB hub, or a shorter / better-shielded cable.",
      "Give a cooled camera its own 12V supply — don't share a marginal rail.",
      "Reconnect the camera on the Equipment page.",
    ] },
  { topic: "guiding-lost", symptom: "Guiding drops out or the star is lost",
    cause: "The guide star disappeared — cloud, a cable snag, or a mount lurch pushed it off the sensor.",
    steps: [
      "Pick a brighter, more central guide star.",
      "Check for cables snagging as the mount tracks.",
      "Re-run guide calibration after any slew or meridian flip.",
    ], seeAlso: ["guiding"] },
  { topic: "cooler-stuck", symptom: "The cooler won't reach the set temperature",
    cause: "The target is colder than the cooler can hold tonight, or it's power-starved.",
    steps: [
      "Set the target ~25–30°C below the current ambient, not a fixed cold number.",
      "Give the camera a dedicated 12V supply rated for the cooler's draw.",
      "Let it settle a few minutes — the cooler ramps slowly to protect the sensor.",
    ], seeAlso: ["coolTo"] },
  { topic: "mount-move-failed", symptom: "The mount won't slew or a move failed",
    cause: "The mount is parked, past a limit, or lost communication mid-slew.",
    steps: [
      "Unpark the mount and confirm it's tracking on the Mount page.",
      "Check the mount's USB/serial connection on the Equipment page.",
      "Clear any limit or safety stop before retrying the slew.",
    ] },
  { topic: "autofocus-failed", symptom: "Autofocus can't find focus",
    cause: "The routine couldn't fit a clean V-curve — too few stars, wrong step size, or a slipping focuser.",
    steps: [
      "Start from roughly-good manual focus so the V-curve is in range.",
      "Lower the focuser step size for a finer search.",
      "Run on a star-rich field, away from the darkest patches of sky.",
    ], seeAlso: ["stepSize", "hfr"] },
  { topic: "nina-error", symptom: "NINA reported an error",
    cause: "The NINA bridge hit a problem on the imaging PC — a device driver or its own sequence.",
    steps: [
      "Open NINA on the imaging PC and read the error there.",
      "Reconnect the affected device in NINA, then resume.",
      "If NINA is unreachable, check the bridge address on the Equipment page.",
    ] },
];

/** Look up the full guide entry for a topic (or undefined). Pure. */
export function getTroubleshootEntry(
  topic: TroubleshootTopic | null | undefined,
): TroubleshootEntry | undefined {
  return topic ? TROUBLESHOOTING.find((e) => e.topic === topic) : undefined;
}
