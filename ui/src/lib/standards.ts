// standards.ts — the rig's imaging standards, as data (#239 stage A).
//
// Split out of StandardsPanel for the same reason lib/wcsStamp.ts is split out
// of WcsStampPanel: everything worth testing lives here, DOM-free, so the
// anti-drift check against server/astrodeck/config.py runs under `npx tsx`
// without jsdom. Importing the panel instead pulls in the store and the api
// client and dies on `window`.
import type { AppConfig, StandardsConfig } from "../types";

/** The server's defaults, which are the values `SequencePlan` carried before
 *  #239 stage A moved these fields. A config written before the block existed
 *  has no key at all, so every read goes through `standardsOrDefault`. */
export const STANDARDS_DEFAULTS: StandardsConfig = {
  apply_filter_offsets: true,
  refocus_on_temp_delta_c: 0,
  min_stars: 0,
  max_guide_rms: 0,
  // GN-04 (2026-09-06): on by default. The server's own default and its
  // schema-2 migration both say 0.65; a stale 0 here would show the gate as
  // off on a rig where it is on.
  max_eccentricity: 0.65,
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
  { key: "max_eccentricity", label: "Maximum star elongation", unit: "ecc",
    hint: "Eccentricity runs from 0 (round) toward 1 (elongated). Reject a frame "
        + "when its median is above this limit. A frame is also rejected when "
        + "more than a quarter of its stars sit 0.15 above this, which is "
        + "what a staircase trail looks like. Clean nights read 0.5 to 0.56; "
        + "0.65 is the default. 0 turns it off." },
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

// ---------------------------------------------------------------- plan editor
// The plan editor's half of the layering (#239 stage A). Twelve plan fields are
// `null` when they mean "inherit the rig's standard", and every control used to
// fall back to a HARDCODED literal - so a rig whose star floor is 40 would have
// shown a plan editor reading 0, which is the losing layer on screen and
// exactly how Polar ran simulated for weeks.

/** The rig-level value for one moved field, read from the block that owns it. */
export function rigValue(
  cfg: AppConfig | null | undefined, field: PolicyField,
): number | boolean {
  const std = standardsOrDefault(cfg?.standards);
  switch (field) {
    case "dither_pixels": return cfg?.guide?.dither_pixels ?? 3;
    case "recover_guiding": return cfg?.guide?.recover_guiding ?? true;
    case "cool_timeout_s": return cfg?.cooling?.cool_timeout_s ?? 600;
    case "hfr_reject_factor": return cfg?.escalation?.hfr_reject_factor ?? 0;
    case "meridian_flip_warn_min": return cfg?.safety?.meridian_flip_warn_min ?? 15;
    default: return std[field as keyof typeof std];
  }
}

export type PolicyField =
  | "dither_pixels" | "recover_guiding" | "cool_timeout_s" | "hfr_reject_factor"
  | "meridian_flip_warn_min" | "apply_filter_offsets" | "refocus_on_temp_delta_c"
  | "min_stars" | "max_guide_rms" | "max_eccentricity"
  | "max_consecutive_rejects" | "max_consecutive_rejects_night";

export const POLICY_FIELDS: PolicyField[] = [
  "dither_pixels", "recover_guiding", "cool_timeout_s", "hfr_reject_factor",
  "meridian_flip_warn_min", "apply_filter_offsets", "refocus_on_temp_delta_c",
  "min_stars", "max_guide_rms", "max_eccentricity",
  "max_consecutive_rejects", "max_consecutive_rejects_night",
];

/** What the editor should SHOW for one field, and whether it is the rig's. */
export function planPolicy(
  plan: Record<string, unknown> | null | undefined,
  cfg: AppConfig | null | undefined,
  field: PolicyField,
): { value: number | boolean; inherited: boolean } {
  const own = plan?.[field];
  // `null`/`undefined` is the ONLY absence. An explicit 0 or false is a choice
  // about this plan and must display as this plan's, never as the rig's.
  if (own === null || own === undefined) {
    return { value: rigValue(cfg, field), inherited: true };
  }
  return { value: own as number | boolean, inherited: false };
}
