// escalationModel.ts - the vocabulary and the sentences of the run-recovery
// policy (wave R7, T-R7-9; plan section 3.F2).
//
// Every string here is lifted from `components/settings/EscalationPanel.tsx`
// with two changes, both required by the wave's copy rules: em-dashes become
// hyphens, and the curly quotes inside the watchdog hint become straight ones.
// Nothing else is reworded - these sentences say what a failure COSTS, which is
// the only reason a first-night rig and an unattended remote one want different
// answers here.
//
// The action words are short because the control is a segmented picker in a
// 360 px sheet; the full consequence sentence is rendered under the picker for
// whichever option is selected, so no information moved into a tooltip.

import type { EscalationConfig } from "../../../../../types";

export type Action = EscalationConfig["cooling_action"];
export type HfrAction = EscalationConfig["hfr_reject_action"];

/** The server's own three-word vocabulary (config.py `EscalationConfig`), kept
 *  verbatim so the UI and the night log call the same thing the same name. */
export const ACTION_LABEL: Record<Action, string> = {
  warn: "WARN",
  skip: "SKIP",
  abort: "END RUN",
};

export const ACTION_BLURB: Record<Action, string> = {
  warn: "Log it and carry on. The run keeps going and the failure is in the night log.",
  skip: "Skip this target and move on to the next one in the plan.",
  abort: "End the run. Nothing else is attempted tonight.",
};

export const HFR_LABEL: Record<HfrAction, string> = {
  warn: "KEEP",
  discard: "DISCARD",
  retake: "RETAKE",
};

export const HFR_BLURB: Record<HfrAction, string> = {
  warn: "Keep the frame and log a warning. Nothing is re-shot.",
  discard: "Discard the frame. The target ends the night one sub short.",
  retake: "Shoot a replacement, up to the retake limit below.",
};

export const ACTION_OPTIONS: { value: Action; label: string }[] =
  (["warn", "skip", "abort"] as Action[]).map((v) => ({ value: v, label: ACTION_LABEL[v] }));

export const HFR_OPTIONS: { value: HfrAction; label: string }[] =
  (["warn", "discard", "retake"] as HfrAction[]).map((v) => ({ value: v, label: HFR_LABEL[v] }));

// ------------------------------------------------------------------ copy

export const ESC_TITLE = "When something fails";
export const ESC_EYEBROW = "Run recovery";

export const ESC_INTRO =
  "Failures that are not weather. Everything defaults to logging a warning and carrying on, which "
  + "is the right answer while you are sitting next to the rig and the wrong one for an unattended "
  + "night.";

export const GROUP_BEFORE = "Before the run starts";
export const GROUP_DURING = "During the run";
export const GROUP_RECOVERY = "When the link drops";

/** Switch titles are rendered by `Switch` itself, which does NOT uppercase
 *  them in CSS, so the design casing is typed here. The words are the legacy
 *  panel's own. */
export const COOLING_TITLE = "REQUIRE THE CAMERA TO BE AT TEMPERATURE";
export const COOLING_BLURB =
  "Darks only match lights taken at the same sensor temperature. If the cooler has not settled, "
  + "the calibration library will not match.";
export const COOLING_ACTION_TITLE = "If the camera is not at temperature";

export const GUIDING_TITLE = "REQUIRE GUIDING";
export const GUIDING_BLURB =
  "Refuse to start long exposures unguided. Leave off for short subs on a well-aligned mount, or "
  + "for a rig with no guide camera.";
export const GUIDING_ACTION_TITLE = "If guiding is unavailable";

export const AF_TITLE = "Autofocus failed";
export const AF_BLURB =
  "A sweep found no minimum - usually thin cloud or a starless narrowband field. Carrying on keeps "
  + "the old focus position.";

export const HFR_TITLE = "A frame failed the quality gate";
export const HFR_BLURB_ROW =
  "Bloated stars from wind, a satellite, or a guiding excursion. Discard drops the sub; retake "
  + "shoots a replacement.";

export const RETAKE_TITLE = "Retake limit per target";
export const RETAKE_HINT =
  "Stops a windy night burning the whole session on replacements that also fail.";

export const WATCHDOG_TITLE = "No-progress watchdog";
export const WATCHDOG_HINT =
  "Treat the run as unsafe if no frame has been saved for this long. Catches the silent hangs a "
  + "per-failure rule cannot see - a wedged filter wheel, a mount that never finishes slewing. What "
  + "happens next is whatever your 'When conditions are unsafe' action says, so a stall pauses, "
  + "parks or aborts the same way rain does.";

export const RECONNECT_TITLE = "RECONNECT AND RESUME AFTER A DROPOUT";
export const RECONNECT_BLURB =
  "Network-attached (Alpaca) devices only. A USB device that vanishes is not recoverable this way.";

export const RETRIES_TITLE = "Reconnect attempts";
export const RETRIES_HINT =
  "How many times to retry before giving up and following the failure action above.";

export const ESC_EMPTY_TITLE = "NO RECOVERY POLICY YET";
export const ESC_EMPTY_HINT =
  "The rig has not sent its escalation block. Nothing here is editable until it does.";

export const SAVE_FAILED = "Could not save. The rig still has the value shown.";

// ----------------------------------------------------------- derived lines

/** What the watchdog number MEANS right now, in the same words the engine
 *  would use. `0` is not silence: a wedged run simply waits. */
export function watchdogLine(seconds: number): string {
  const min = Math.round((seconds || 0) / 60);
  return min > 0
    ? `${min} min with nothing saved counts as unsafe, and your unsafe action takes it from there.`
    : "Off - a wedged run waits until you notice.";
}

/** Why a sub-control is inert while its parent switch is off. It is a REASON,
 *  not a native `disabled`: the value stays readable and a press says why. */
export function dependsOn(parentOn: boolean, sentence: string): string | null {
  return parentOn ? null : sentence;
}

export const COOLING_OFF_REASON =
  "the camera-temperature check is off, so there is no failure to act on";
export const GUIDING_OFF_REASON =
  "the guiding check is off, so there is no failure to act on";
export const RETAKE_OFF_REASON =
  "the quality gate is not set to RETAKE, so no replacement frames are shot";
export const RETRIES_OFF_REASON =
  "auto-reconnect is off, so nothing is retried";

/** The one read-only sentence for this editor. Rendered through `LockNote`,
 *  which prefixes "Read-only - ", so it starts lower-case.
 *
 *  When the lock is the CAPABILITY it names the capability id as well as the
 *  roles that hold it (`EscalationPanel.tsx:352`'s sentence, which is the one
 *  a user can act on: it says which account to sign in with). When the lock is
 *  anything else - the link is down, a lane is busy - `gate.ts` already wrote
 *  that reason and this repeats it verbatim rather than blaming the role. */
export function escalationLockSentence(reason: string, phrase: string): string {
  return reason === `needs ${phrase}`
    ? `changing these needs ${phrase} (config.alerts)`
    : reason;
}
