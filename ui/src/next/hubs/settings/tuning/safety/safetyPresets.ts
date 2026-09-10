// safetyPresets.ts - the safety PRESET table and the sentences that describe it
// (wave R7, T-R7-9; plan section 3.F1).
//
// RE-DERIVED, NOT IMPORTED. `hubs/rig/sheets/safety.tsx` carries its own copy of
// `PRESET_BLURB` for the read-only row it renders, and `components/settings/
// SafetyLimitsPanel.tsx` carries a third for `#/classic`. Importing across
// areas would pull a 1278-line sheet into this one's chunk to read three
// strings, so the wave's rule is: re-derive here, from the same source of truth
// both of those copied - the server's own `SAFETY_PRESETS` dict.
//
// THE SERVER OWNS WHAT A PRESET MEANS. `SafetyConfig._derive_preset_label`
// (server/astrodeck/config.py:228) runs on EVERY construction - disk load, API
// body, bare constructor - and rewrites `preset` to whichever entry the numbers
// match, or "custom" if none does. Two consequences this module is built on:
//
//   1. Picking a preset means WRITING ITS NUMBERS. Sending `{preset: "remote"}`
//      alone changes nothing: the server re-derives the label straight back off
//      the untouched numbers. So `presetPatch()` returns the values.
//   2. "Custom" is a REPORT, never a choice. There is no set of numbers that
//      means custom - custom is what the server says when no entry matches - so
//      the segmented control renders it as the current state with the reason it
//      cannot be picked, rather than as a button that silently does nothing.
//
// `PRESET_VALUES` mirrors `SAFETY_PRESETS` field for field. Any drift makes the
// label snap back to CUSTOM the moment the write lands, which is the visible
// symptom to look for if this table and config.py ever disagree.

import type { SafetyConfig } from "../../../../../types";

export type PresetName = SafetyConfig["preset"];

/** Field-for-field mirror of server `config.SAFETY_PRESETS`. Deliberately NOT a
 *  superset: `SafetyLimitsPanel.tsx:45` also wrote `resume_safe_consecutive: 3`
 *  under "remote", which the server's entry does not name - a value nobody
 *  asked to change, written on a rig where `resume_when_safe` is false and it
 *  can never be read. */
export const PRESET_VALUES: Record<"backyard" | "remote", Partial<SafetyConfig>> = {
  backyard: {
    on_unsafe: "pause",
    unsafe_consecutive: 3,
    resume_when_safe: true,
    resume_safe_consecutive: 3,
    max_pause_min: 120,
  },
  remote: {
    on_unsafe: "abort_park_warm",
    unsafe_consecutive: 2,
    resume_when_safe: false,
    max_pause_min: 0,
    close_dome_on_unsafe: true,
  },
};

export const PRESET_ORDER: readonly PresetName[] = ["backyard", "remote", "custom"];

export const PRESET_LABEL: Record<PresetName, string> = {
  backyard: "BACKYARD",
  remote: "REMOTE",
  custom: "CUSTOM",
};

/** What each preset is FOR - who is standing next to the rig, and what that
 *  means when the sky turns. Re-derived from `SafetyLimitsPanel.tsx:67`. */
export const PRESET_BLURB: Record<PresetName, string> = {
  backyard:
    "You are nearby. A cloud or rain trip pauses and waits, and picks back up when it clears.",
  remote:
    "Nobody is there. A trip ends the night: park the mount and ramp the camera's cooler back to "
    + "ambient rather than wait.",
  custom:
    "Your own combination. The response settings on the rig's safety sheet match neither preset.",
};

/** Why CUSTOM is shown but not pressable. The rig computes this label from the
 *  numbers; there is nothing to send that would produce it. */
export const CUSTOM_NOT_PICKABLE =
  "the rig reports CUSTOM whenever the response settings match neither preset - change one on the "
  + "safety sheet and it lands here by itself";

const ON_UNSAFE_WORD: Record<SafetyConfig["on_unsafe"], string> = {
  warn: "warn only",
  pause: "pause",
  park: "park the mount",
  abort_park_warm: "park and warm the camera",
};

/** The five numbers the label stands for, in one line, read from the block the
 *  rig actually holds. Without it the picker is three words with no consequence
 *  attached, and a preset is exactly its consequences. */
export function presetLine(s: SafetyConfig): string {
  const reads = s.unsafe_consecutive === 1 ? "read" : "reads";
  return [
    `${ON_UNSAFE_WORD[s.on_unsafe] ?? s.on_unsafe} after ${s.unsafe_consecutive} unsafe ${reads}`,
    s.resume_when_safe
      ? `resume after ${s.resume_safe_consecutive} safe reads`
      : "no automatic resume",
    s.max_pause_min > 0
      ? `give up after ${s.max_pause_min} min`
      : "no time limit on a hold",
    s.close_dome_on_unsafe ? "close the roof" : "leave the roof alone",
  ].join(" · ");
}

/** The values to send for `name`. `custom` has none - see the header. */
export function presetPatch(name: PresetName): Partial<SafetyConfig> | null {
  if (name === "custom") return null;
  return { ...PRESET_VALUES[name], preset: name };
}
