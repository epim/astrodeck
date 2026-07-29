// stepDefaults.ts — the plan editor's step-authoring RULES, as pure functions.
//
// Extracted from SequenceView so the three defaults the four-persona UX review
// filed as defects can be reasoned about and tested without a DOM:
//
//   #29  `+ STEP` never inherits. After Ha/300s/×20 the new row came back as
//        Light / no filter / 120s / g100 / 1× / ×10, so a three-filter narrowband
//        target cost ~15 field edits typed on a tablet in the dark.
//   #40  A Flat step defaulted to 120s (fully saturated against a panel) with
//        TARGET ADU 0 and no unit — the value that decides whether auto-exposure
//        runs at all.
//   #1   Every new step started on "no filter" on a rig with a wheel connected.
//        (The capture-time half of #1 is server-side; this is the editor half —
//        a new step now starts on the filter the wheel is actually parked on.)
//
// and #30's question "is any quality gate armed?", which is what makes
// `count_mode: "attempts"` quietly shrink a requested frame count.
import type { ExposureStep, SequencePlan } from "../../types";

/** A step with no history to inherit from — the very first step of a target. */
export const BASE_STEP: Omit<ExposureStep, "id"> = {
  filter: null, exposure_s: 120, gain: 100, offset: 30,
  binning: 1, count: 10, frame_type: "Light",
};

/** Longest exposure that is plausibly a flat. Anything above this against a
 *  panel is saturated, so switching a step to Flat clamps to FLAT_EXPOSURE_S. */
export const FLAT_MAX_EXPOSURE_S = 30;
export const FLAT_EXPOSURE_S = 3;
/** ~38% of a 16-bit well — the conventional flat target, and the value that
 *  makes auto-exposure actually engage (0 means "don't solve, use exposure_s"). */
export const FLAT_ADU_TARGET = 25000;

/**
 * The filter a brand-new step should start on.
 *
 * With a wheel connected this is the slot the wheel is physically parked on, so
 * the plan editor agrees with the hardware instead of writing `null` ("no
 * filter") onto a rig that unambiguously has one. With no wheel it stays null.
 *
 * A blackout slot is never the answer. The wheel is parked on one for the whole
 * of a dark run, so without this a step added straight afterwards would inherit
 * "shoot lights through the light block".
 */
export function defaultFilter(
  names: string[] | undefined, position: number | undefined,
  opaque: boolean[] = [],
): string | null {
  if (!names || names.length === 0) return null;
  const i = position ?? 0;
  if (names[i] && !opaque[i]) return names[i];
  const first = names.findIndex((n, j) => !!n && !opaque[j]);
  return first >= 0 ? names[first] : null;
}

/**
 * The step `+ step` should append to `steps`.
 *
 * Inherits EVERYTHING from the last step — exposure, gain, offset, binning,
 * count, frame type, ADU target — because the overwhelmingly common next step
 * is "the same again through a different filter". Only `id` is dropped (the
 * caller mints a fresh one). With no previous step it falls back to BASE_STEP
 * with the wheel's current filter resolved in.
 */
export function nextStep(
  steps: ExposureStep[],
  filterNames?: string[],
  position?: number,
  opaque: boolean[] = [],
): Omit<ExposureStep, "id"> {
  const last = steps.length > 0 ? steps[steps.length - 1] : null;
  if (!last) {
    return { ...BASE_STEP, filter: defaultFilter(filterNames, position, opaque) };
  }
  const { id: _id, ...rest } = last;
  void _id;
  return { ...rest };
}

/**
 * Field corrections to apply when a step's frame_type changes.
 *
 * Returns ONLY the fields that would otherwise be nonsensical for the new type,
 * so everything the user deliberately set (gain, binning, count) is preserved:
 *
 *   Flat  — clamp a light-length exposure down to FLAT_EXPOSURE_S and arm
 *           auto-exposure at FLAT_ADU_TARGET when no target ADU is set yet.
 *   Bias  — the shortest exposure the camera can take, by definition.
 *   Dark  — unchanged: a dark MUST match its lights' exposure, so inheriting is
 *           correct, and the filter is irrelevant (shutter closed).
 *   Light — unchanged.
 */
export function frameTypeDefaults(
  step: ExposureStep, nextType: string,
): Partial<ExposureStep> {
  const patch: Partial<ExposureStep> = { frame_type: nextType };
  if (nextType === "Flat") {
    if (step.exposure_s > FLAT_MAX_EXPOSURE_S) patch.exposure_s = FLAT_EXPOSURE_S;
    if (!step.adu_target) patch.adu_target = FLAT_ADU_TARGET;
  } else if (nextType === "Bias") {
    patch.exposure_s = 0;
  }
  return patch;
}

/**
 * Which per-frame quality gates are armed on this plan.
 *
 * This is the trigger for #30: with `count_mode: "attempts"` a rejected frame
 * still consumes the step's count, so the moment ANY of these is non-zero,
 * "20 × 300s" silently becomes "at most 20 × 300s". With every gate off the two
 * modes are behaviourally identical, which is why the warning is gate-driven
 * rather than unconditional.
 */
export function armedQualityGates(plan: SequencePlan): string[] {
  const armed: string[] = [];
  if ((plan.min_stars ?? 0) > 0) armed.push("min stars");
  if ((plan.max_guide_rms ?? 0) > 0) armed.push("max guide RMS");
  if ((plan.max_eccentricity ?? 0) > 0) armed.push("max eccentricity");
  if ((plan.hfr_reject_factor ?? 0) > 0) armed.push("HFR spike");
  return armed;
}

/** True when the plan will quietly deliver fewer frames than the user asked for. */
export function countShrinksSilently(plan: SequencePlan): boolean {
  return (plan.count_mode ?? "attempts") === "attempts"
    && armedQualityGates(plan).length > 0;
}
