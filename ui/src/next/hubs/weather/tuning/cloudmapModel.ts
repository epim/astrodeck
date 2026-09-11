// cloudmapModel.ts - every decision and every sentence the rebuilt Cloud model
// panel prints (wave R7, T-R7-15; plan section 3.F19).
//
// Pure: no React, no store, no fetch. `megabytesPerHour` and the platform notes
// are lifted verbatim in BEHAVIOUR from `components/settings/CloudmapPanel.tsx`
// (which is untouched and still serves `#/classic`); only the prose was
// re-punctuated to the house rule of hyphens rather than em-dashes.

/** Server bounds, restated so a typo is a sentence rather than a 422. These
 *  MUST match `config.py` CloudmapConfig: poll_minutes ge=5 le=60, half_px
 *  ge=16 le=400. Below five cannot produce fresher data than the satellite
 *  publishes, which is why the server refuses rather than clamps. */
export const POLL_MIN = 5;
export const POLL_MAX = 60;
export const HALF_MIN = 16;
export const HALF_MAX = 400;

export type Platform = "auto" | "G18" | "G19";

export const PLATFORM_ORDER: readonly Platform[] = ["auto", "G18", "G19"];

export const PLATFORM_LABEL: Record<Platform, string> = {
  auto: "AUTO",
  G18: "G18 WEST",
  G19: "G19 EAST",
};

/** The sub-line under each option. GOES-16 was retired; East is 19 and West is
 *  18, and the crossover for this pair is about 106 W - not the 100 W a lot of
 *  documentation still repeats. */
export const PLATFORM_SUB: Record<Platform, string> = {
  auto: "from the site",
  G18: "west of 106 W",
  G19: "east of 106 W",
};

export const PLATFORM_NOTE: Record<Platform, string> = {
  auto: "Automatic picks the nearer satellite from the site longitude.",
  G18: "GOES-West, pinned. Correct west of about 106 W.",
  G19: "GOES-East, pinned. Correct east of about 106 W.",
};

export const PLATFORM_COVERAGE =
  "Both carry a CONUS sector only, so a site outside North America gets no reading from either - "
  + "the dome says so rather than drawing clear sky.";

export const CM_TITLE = "Cloud model";

export const CM_INTRO =
  "Where in the sky the cloud is, from the NOAA GOES cloud mask and cloud-top height, drawn as the "
  + "sky dome on the SKY screen of this hub. It is advisory only: nothing in the sequencer, the "
  + "safety gate or auto-resume reads it, so switching it on cannot stop a run.";

export const CM_ENABLED_NOTE =
  "Off means no satellite granules are fetched and the dome has nothing to draw.";

export const CM_DEFAULT_SITE_WARN =
  "Needs a real observing site. Every ray is traced from it, so with the default site the dome has "
  + "nothing to show.";

export const CM_EMPTY_TITLE = "NO CLOUD MODEL BLOCK YET";
export const CM_EMPTY_HINT =
  "The rig has not sent its cloud-model configuration. It appears as soon as the link brings the "
  + "config through; a server older than 0.3.10 does not carry the block at all.";

export const CM_SAVE_FAILED = "Could not save. The rig still has the settings shown.";

export const CM_CONFLICT =
  "The configuration changed somewhere else while this was open. It has been reloaded - re-apply "
  + "the edit.";

export const WINDOW_NOTE =
  "The window is the box of satellite cells fetched around the site; 100 reaches 200 km either "
  + "way, past the 151 km a 5-degree ray crosses.";

export const SWITCH_TO_AUTO = "SWITCH TO AUTOMATIC";

/** Roughly what one poll costs: two ABI products, a 201-cell window each.
 *  Scales with the SQUARE of the window, so the estimate moves when half_px
 *  does - the default 100 is the 4.4 MB the docs quote. */
export function megabytesPerHour(halfPx: number, pollMinutes: number): number {
  const cells = (2 * halfPx + 1) ** 2;
  const perCycleMb = 4.4 * (cells / (2 * 100 + 1) ** 2);
  return (perCycleMb * 60) / pollMinutes;
}

export function costLine(halfPx: number, pollMinutes: number): string {
  return `About ${megabytesPerHour(halfPx, pollMinutes).toFixed(1)} MB an hour on this setting. `
    + WINDOW_NOTE;
}

/** THE UPGRADE PATH, and the only one available. Pydantic writes defaults into
 *  the stored config, so an install from before "auto" existed carries an
 *  explicit "G18" it never chose - and that explicit value correctly wins over
 *  the new default, because AppConfig has no schema_version and a written-out
 *  default is byte-identical to a deliberate override. Rewriting it silently
 *  would overwrite somebody's real decision. So: say it, where the operator can
 *  act on it, and let them choose.
 *
 *  Null when there is nothing to say - no pin, no suggestion, or the two agree. */
export function mispinLine(platform: Platform, suggested: Platform | null | undefined): string | null {
  if (platform === "auto") return null;
  if (suggested == null || suggested === platform) return null;
  const pinned = platform === "G18" ? "GOES-West" : "GOES-East";
  const better = suggested === "G18" ? "GOES-West" : "GOES-East";
  return `This site is pinned to ${pinned}, but ${better} sees it at a lower zenith angle - a `
    + "sharper pixel and less parallax. If the pin was not deliberate, switch to Automatic.";
}

/** The read-only sentence for `LockNote`. Lower-case: `LockNote` prefixes
 *  "Read-only - ". */
export function cloudmapLockSentence(reason: string): string {
  return `changing the cloud model ${reason} (config.site_optics)`;
}
