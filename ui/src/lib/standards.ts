// standards.ts — the rig's imaging standards, as data (#239 stage A).
//
// Split out of StandardsPanel for the same reason lib/wcsStamp.ts is split out
// of WcsStampPanel: everything worth testing lives here, DOM-free, so the
// anti-drift check against server/astrodeck/config.py runs under `npx tsx`
// without jsdom. Importing the panel instead pulls in the store and the api
// client and dies on `window`.
import type { StandardsConfig } from "../types";

/** The server's defaults, which are the values `SequencePlan` carried before
 *  #239 stage A moved these fields. A config written before the block existed
 *  has no key at all, so every read goes through `standardsOrDefault`. */
export const STANDARDS_DEFAULTS: StandardsConfig = {
  apply_filter_offsets: true,
  refocus_on_temp_delta_c: 0,
  min_stars: 0,
  max_guide_rms: 0,
  max_eccentricity: 0,
  max_consecutive_rejects: 10,
  max_consecutive_rejects_night: 20,
};

export function standardsOrDefault(s?: StandardsConfig): StandardsConfig {
  return { ...STANDARDS_DEFAULTS, ...(s ?? {}) };
}

export interface StandardsNumberField {
  key: keyof StandardsConfig;
  label: string;
  unit?: string;
  hint: string;
}

/** The numeric fields, in render order. Exported so the anti-drift test can
 *  compare this list against config.py rather than restating it a third time. */
export const STANDARDS_NUMBER_FIELDS: StandardsNumberField[] = [
  { key: "min_stars", label: "Reject below", unit: "stars",
    hint: "A frame with fewer stars than this is flagged. 0 turns it off. "
        + "Not the same setting as the WCS star floor under Connect." },
  { key: "max_guide_rms", label: "Reject above", unit: "″ RMS",
    hint: "Guide error while the frame was open. 0 turns it off." },
  { key: "max_eccentricity", label: "Reject rounder than", unit: "ecc",
    hint: "Median star eccentricity, 0 to 1 — catches trailing and tilt. "
        + "0 turns it off." },
  { key: "refocus_on_temp_delta_c", label: "Refocus after", unit: "°C drift",
    hint: "Re-run autofocus once the focuser has drifted this far since the "
        + "last one. 0 turns it off." },
  { key: "max_consecutive_rejects", label: "Give up on a step after",
    unit: "rejects",
    hint: "Consecutive rejects before the step is abandoned and the night "
        + "moves on. 0 turns it off." },
  { key: "max_consecutive_rejects_night", label: "End the night after",
    unit: "rejects",
    hint: "Consecutive rejects across all targets before the night is called "
        + "— usually cloud. 0 turns it off." },
];

/** Every field the panel renders, numeric plus the lone toggle. The toggle is
 *  listed here rather than inferred so the anti-drift test compares a real
 *  list against the server's, not a list derived from the same source. */
export const STANDARDS_ALL_FIELDS: (keyof StandardsConfig)[] = [
  ...STANDARDS_NUMBER_FIELDS.map((f) => f.key),
  "apply_filter_offsets",
];
