// Per-filter capture settings: what to fill in when the filter changes (#215).
//
// The wheel stores an exposure and a gain per slot (see types.ts
// `filterwheel.exposures` / `.gains`). Both halves are independently pinnable
// and `null` means NOT PINNED — which cannot be 0, because 0 is a real gain and
// a legitimate thing to want on a broadband slot.
//
// WHERE THIS IS ALLOWED TO BE USED, and where it is not:
//
//   YES — the moment a value is being CHOSEN. Picking Ha on the camera dial,
//         adding a plan step whose filter is Ha. The operator is looking at the
//         screen, and the number that appears is the number that will be used.
//
//   NO  — at capture time, against a plan that already exists. The sequence
//         engine never reads these. A plan is a reviewable artifact; rewriting
//         its exposures underneath the operator would make the plan on screen
//         stop describing the night, which is the same shape as the profile
//         provider override that quietly ran a simulated polar aligner for
//         twelve days before anyone noticed.
//
// The one authoritative reader anywhere is the offset-learning focus sweep, and
// that lives on the server (`hub.learn_filter_offsets`) precisely because there
// is no plan there to consult.

/** The subset of `status.filterwheel` this needs. Narrowed on purpose: it makes
 *  the function testable without a whole status object, and it documents that
 *  nothing here depends on the wheel's live position. */
export interface FilterSettingsSource {
  names?: string[];
  exposures?: (number | null)[];
  gains?: (number | null)[];
  opaque?: boolean[];
}

/** What the pinned settings say about `slot`, as a patch to merge into whatever
 *  the screen currently holds. Empty object = this slot pins nothing, so the
 *  screen keeps every value it already had.
 *
 *  A BLACKOUT SLOT NEVER PINS. It has no light path, so there is no "settings
 *  for that filter" to restore; the server clears them on save for the same
 *  reason, and this is the second half of that rule — a stale pin arriving from
 *  an older store must not be applied here just because it is in the array. */
export function filterSettingsPatch(
  fw: FilterSettingsSource | null | undefined,
  slot: number,
): { exposure_s?: number; gain?: number } {
  if (!fw || !Number.isInteger(slot) || slot < 0) return {};
  if (fw.opaque?.[slot]) return {};
  const patch: { exposure_s?: number; gain?: number } = {};
  const exp = fw.exposures?.[slot];
  const gain = fw.gains?.[slot];
  // `> 0` for exposure and `>= 0` for gain: a zero-second exposure is not a
  // frame, but gain 0 is a real setting. Treating them the same is how a
  // tri-state collapses back into a sentinel.
  if (typeof exp === "number" && Number.isFinite(exp) && exp > 0) {
    patch.exposure_s = exp;
  }
  if (typeof gain === "number" && Number.isFinite(gain) && gain >= 0) {
    patch.gain = Math.round(gain);
  }
  return patch;
}

/** The same patch, addressed by filter NAME rather than slot index — what the
 *  plan editor needs, because an `ExposureStep` stores the name.
 *
 *  An unmatched name yields an empty patch rather than a guess. A plan can name
 *  a filter this wheel does not have (imported plan, wheel swapped between
 *  nights), and the step editor already refuses that pick elsewhere; inventing
 *  settings for a filter that is not in the carousel would be worse than
 *  leaving the step's own numbers where the operator put them. */
export function filterSettingsPatchByName(
  fw: FilterSettingsSource | null | undefined,
  name: string | null | undefined,
): { exposure_s?: number; gain?: number } {
  if (!fw || !name) return {};
  const slot = (fw.names ?? []).indexOf(name);
  return slot < 0 ? {} : filterSettingsPatch(fw, slot);
}

/** Does `slot` pin anything at all? For a UI that wants to SAY it is about to
 *  change the settings, rather than silently changing them under the operator. */
export function filterHasPinnedSettings(
  fw: FilterSettingsSource | null | undefined,
  slot: number,
): boolean {
  return Object.keys(filterSettingsPatch(fw, slot)).length > 0;
}

/** One line naming what a slot pins ("30s · gain 100"), or "" when it pins
 *  nothing. Used in the note beside a filter pick, so the change is announced
 *  rather than discovered later in a FITS header. */
export function filterSettingsSummary(
  fw: FilterSettingsSource | null | undefined,
  slot: number,
): string {
  const p = filterSettingsPatch(fw, slot);
  const bits: string[] = [];
  if (p.exposure_s != null) bits.push(`${p.exposure_s}s`);
  if (p.gain != null) bits.push(`gain ${p.gain}`);
  return bits.join(" · ");
}
