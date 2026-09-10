// automationModel.ts - the plan editor's automation column, as data.
//
// WHY THE LABEL LIST IS A CONSTANT AND NOT A CONSEQUENCE OF THE JSX. Twenty
// settings decide what an unattended rig does at 3am; each is one line of JSX,
// and a rebuild that quietly drops one produces a screen that looks finished
// and a night that behaves differently. `AUTOMATION_LABELS` is the set of
// accessible names lifted VERBATIM from `views/SequenceView.tsx` (the `label=`
// props at :1505, :1512, :1518, :1525, :1532, :1543, :1552, :1562, :1576,
// :1581, :1592, :1603, :1611, :1616, :1632, :1666, :1679, :1697, :1716, :1735),
// and `planAutomationDom.test.tsx` compares it against what the section really
// renders. Dropping a control is then a red test, not a discovery in April.
//
// The strings are NOT re-punctuated. They are the legacy accessible names, so a
// user who learned the product by ear hears the same sentence, and the parity
// comparison is against the source of truth rather than against a paraphrase of
// it. (`°C` therefore stays; the house rule this wave enforces is em-dashes and
// emojis, and there are none here.)
//
// Everything below is pure: no React, no store, no fetch. The summaries are
// what each `Disclosure` head says while closed, and they state the group's
// current EFFECT - "unguided, no dither" - rather than repeating its name.
// Vocabulary matches `hubs/session/now/QuotaRows.tsx`, which already says "off",
// "no star floor" and "move on after N rejects in a row" for the same fields.

import type { SequencePlan } from "../../../../../types";

/** The plan fields this column writes, one per rendered control. Used for the
 *  `plan-guard-<key>` markers, so a test can name a control without matching on
 *  its sentence. */
export type AutomationKey =
  | "guide" | "dither_every" | "dither_pixels" | "recover_guiding"
  | "autofocus_every" | "refocus_on_temp_delta_c" | "apply_filter_offsets"
  | "meridian_flip" | "meridian_flip_warn_min" | "safety_check"
  | "cool_to" | "park_when_done" | "warm_cooler_when_done"
  | "count_mode" | "hfr_reject_factor" | "min_stars" | "max_guide_rms"
  | "max_eccentricity"
  | "max_consecutive_rejects" | "max_consecutive_rejects_night";

/** The accessible name each control carries, keyed by the field it writes.
 *  Verbatim from `SequenceView.tsx` - see the file header. */
export const AUTOMATION_LABEL: Record<AutomationKey, string> = {
  guide: "Guide during sequence",
  dither_every: "Dither every N frames",
  dither_pixels: "Dither size in pixels",
  recover_guiding: "Recover guiding if lost",
  autofocus_every: "Refocus every N frames",
  refocus_on_temp_delta_c: "Refocus on temperature change, in °C (0 = off)",
  apply_filter_offsets: "Apply filter focus offsets",
  meridian_flip: "Meridian flip (German mount)",
  meridian_flip_warn_min: "Meridian warning lead, in minutes",
  safety_check: "Safety monitor gate",
  cool_to: "Cool sensor to °C (blank = cooling off)",
  park_when_done: "Park mount when done",
  warm_cooler_when_done: "Warm camera when done",
  count_mode: "Count accepted frames instead of attempts",
  hfr_reject_factor: "Flag HFR spikes above this multiple of the median (0 = off)",
  min_stars: "Minimum stars per frame (0 = off)",
  max_guide_rms: "Maximum guide RMS in arcseconds (0 = off)",
  max_eccentricity: "Maximum star eccentricity, 0 to 1 (0 = off)",
  max_consecutive_rejects: "Skip a step after N consecutive rejects (0 = off)",
  max_consecutive_rejects_night: "End the night after N consecutive rejects (0 = off)",
};

/** The same twenty, as a flat list, for the parity assertion. */
export const AUTOMATION_LABELS: readonly string[] =
  Object.values(AUTOMATION_LABEL);

/** The short eyebrow above each control. The accessible name above is the full
 *  sentence; this is the word the eye lands on in a 420 px column. */
export const AUTOMATION_EYEBROW: Record<AutomationKey, string> = {
  guide: "GUIDE",
  dither_every: "DITHER EVERY",
  dither_pixels: "DITHER SIZE",
  recover_guiding: "RECOVER GUIDING",
  autofocus_every: "REFOCUS EVERY",
  refocus_on_temp_delta_c: "REFOCUS ON TEMP",
  apply_filter_offsets: "FILTER OFFSETS",
  meridian_flip: "MERIDIAN FLIP",
  meridian_flip_warn_min: "FLIP WARNING",
  safety_check: "SAFETY GATE",
  cool_to: "COOL TO",
  park_when_done: "PARK WHEN DONE",
  warm_cooler_when_done: "WARM WHEN DONE",
  count_mode: "COUNT MODE",
  hfr_reject_factor: "HFR SPIKES",
  min_stars: "MIN STARS",
  max_guide_rms: "MAX GUIDE RMS",
  max_eccentricity: "MAX ECCENTRICITY",
  max_consecutive_rejects: "SKIP AFTER",
  max_consecutive_rejects_night: "END NIGHT AFTER",
};

/** What the control means, one line, under the field. These are the legacy
 *  `InfoDot` "About ..." bodies (`src/help.ts` and the inline `content=` props),
 *  reworded to hyphens and to the length a hint can carry. A hint, not a popup:
 *  a tooltip that only opens on hover never fires on the tablet this editor is
 *  built for, which is why the legacy panel's own explanations were invisible
 *  to most of its users. */
export const AUTOMATION_HINT: Partial<Record<AutomationKey, string>> = {
  dither_every: "Nudges the mount a few pixels between frames so hot pixels and "
    + "noise land in different places and average out when stacking.",
  dither_pixels: "How far the nudge moves. A few pixels is typical.",
  refocus_on_temp_delta_c: "Refocus once the sensor temperature has drifted this "
    + "far since the last focus run.",
  apply_filter_offsets: "Filters focus at slightly different points. When set, "
    + "the focuser shifts on a filter change instead of running a full refocus.",
  meridian_flip: "German equatorial mounts reach the pier as a target crosses "
    + "the meridian. A flip swaps the scope to the other side and re-centres by "
    + "plate-solving. Off for fork and alt-az mounts, which never flip.",
  meridian_flip_warn_min: "How far ahead of the meridian the live flip-ETA chip "
    + "warns you during a run.",
  safety_check: "Honours the configured safety monitor during unattended runs: "
    + "pauses or parks when conditions go unsafe. Off means weather and cloud "
    + "stop nothing. The mount's own limits apply either way - they describe "
    + "your rig's geometry, not the sky.",
  cool_to: "Cools the sensor and holds it there before lights. The sequence "
    + "waits at the start, so there is no need to pre-cool before pressing RUN.",
  hfr_reject_factor: "Flags a frame whose HFR exceeds this multiple of the "
    + "running median - 1.5 means 50 percent softer than typical. Catches wind "
    + "gusts, passing cloud and focus drift.",
  count_mode: "Each step's count becomes a quota of ACCEPTED frames: rejects do "
    + "not use up the count and the step keeps shooting, across nights if "
    + "needed. Rejected frames stay on disk for regrading.",
  max_eccentricity: "Rejects a frame whose stars are too elongated - trailing, "
    + "tilt or coma - measured as the median star eccentricity.",
  max_consecutive_rejects: "Accepted-count mode only: after this many rejects in "
    + "a row on one step, move to the next step. The shortfall stays in the "
    + "session ledger for another night.",
  max_consecutive_rejects_night: "Accepted-count mode only: after this many "
    + "rejects in a row across targets - the counter resets on any accepted "
    + "frame - end the night early and leave the session resumable.",
};

/** What a zero means, per field, for `NumberField`'s `zeroMeans` line. Only the
 *  fields where the config really uses `0 = off`: `dither_pixels` and
 *  `meridian_flip_warn_min` do not, and printing "0 = off" on them would invent
 *  a setting the engine does not have. */
export const AUTOMATION_ZERO_MEANS: Partial<Record<AutomationKey, string>> = {
  dither_every: "no dithering",
  autofocus_every: "no frame-count refocus",
  refocus_on_temp_delta_c: "no temperature refocus",
  hfr_reject_factor: "off",
  min_stars: "no star floor",
  max_guide_rms: "no guide ceiling",
  max_eccentricity: "no elongation ceiling",
  max_consecutive_rejects: "never moves on by itself",
  max_consecutive_rejects_night: "never ends the night by itself",
};

export interface AutomationGroup {
  /** Marker suffix and `Disclosure` identity. */
  id: string;
  /** The eyebrow on the closed row. */
  title: string;
  keys: AutomationKey[];
}

/** Six groups, twenty controls. The split follows what an operator changes
 *  together, not the order the legacy column happened to grow in. */
export const AUTOMATION_GROUPS: readonly AutomationGroup[] = [
  { id: "guiding", title: "GUIDING AND DITHER", keys: ["guide", "recover_guiding", "dither_every", "dither_pixels"] },
  { id: "focus", title: "FOCUS", keys: ["autofocus_every", "refocus_on_temp_delta_c", "apply_filter_offsets"] },
  { id: "meridian", title: "MERIDIAN AND SAFETY", keys: ["meridian_flip", "meridian_flip_warn_min", "safety_check"] },
  { id: "endings", title: "COOLING AND END OF NIGHT", keys: ["cool_to", "park_when_done", "warm_cooler_when_done"] },
  { id: "quality", title: "FRAME QUALITY GATES", keys: ["count_mode", "hfr_reject_factor", "min_stars", "max_guide_rms", "max_eccentricity"] },
  { id: "guards", title: "REJECT GUARDS", keys: ["max_consecutive_rejects", "max_consecutive_rejects_night"] },
];

/** Every key, in render order. Kept as a derivation of the groups so a control
 *  added to a group cannot be missing from the parity list. */
export const AUTOMATION_KEYS: readonly AutomationKey[] =
  AUTOMATION_GROUPS.flatMap((g) => g.keys);

// ------------------------------------------------------------------ summaries

/** The effective value of one field: the plan's own, or the rig's standard when
 *  the plan holds `null`. Callers pass `planPolicy`'s answer in; this file only
 *  formats. */
export type Effective = Record<string, number | boolean>;

const off = (n: unknown): boolean => (typeof n === "number" ? n : 0) === 0;
const num = (v: unknown, fallback = 0): number => (typeof v === "number" ? v : fallback);
const yes = (v: unknown): boolean => v === true;

/** One line per group, stating what the group currently DOES. A closed
 *  disclosure that reads "FOCUS" tells you nothing you did not already know;
 *  "refocus every 20 frames, and on 1.5 °C of drift" is the reason not to open
 *  it. */
export function groupSummary(
  id: string, plan: SequencePlan | null, eff: Effective,
): string {
  if (!plan) return "no plan loaded";
  switch (id) {
    case "guiding": {
      const dither = off(plan.dither_every)
        ? "no dither"
        : `dither every ${plan.dither_every} frames at ${num(eff.dither_pixels, 3)} px`;
      const recover = yes(eff.recover_guiding) ? "re-acquires if lost" : "gives up if guiding is lost";
      return plan.guide ? `guiding, ${dither}, ${recover}` : `unguided, ${dither}`;
    }
    case "focus": {
      const byCount = off(plan.autofocus_every)
        ? "no refocus by frame count"
        : `refocus every ${plan.autofocus_every} frames`;
      const byTemp = off(eff.refocus_on_temp_delta_c)
        ? "no refocus on temperature"
        : `and on ${num(eff.refocus_on_temp_delta_c)} °C of drift`;
      const offsets = yes(eff.apply_filter_offsets)
        ? "filter offsets applied" : "no filter offsets";
      return `${byCount}, ${byTemp}, ${offsets}`;
    }
    case "meridian": {
      const flip = plan.meridian_flip
        ? `flips at the meridian, ${num(eff.meridian_flip_warn_min, 15)} min warning`
        : "never flips - fork or alt-az";
      const gate = (plan.safety_check ?? true)
        ? "safety monitor armed"
        : "safety monitor ignored - weather stops nothing";
      return `${flip}, ${gate}`;
    }
    case "endings": {
      const cool = plan.cool_to == null ? "no cooling" : `cools to ${plan.cool_to} °C`;
      const park = plan.park_when_done ? "parks" : "leaves the mount tracking";
      const warm = plan.warm_cooler_when_done ? "warms the camera" : "leaves the camera cold";
      return `${cool}, then ${park} and ${warm}`;
    }
    case "quality": {
      const mode = (plan.count_mode ?? "attempts") === "accepted"
        ? "counting accepted frames" : "counting attempts";
      const armed = [
        off(eff.hfr_reject_factor) ? null : "HFR",
        off(eff.min_stars) ? null : "stars",
        off(eff.max_guide_rms) ? null : "guide RMS",
        off(eff.max_eccentricity) ? null : "eccentricity",
      ].filter((s): s is string => s != null);
      return armed.length === 0
        ? `${mode}, no quality gate armed - every frame is a keeper`
        : `${mode}, ${armed.join(" + ")} armed`;
    }
    case "guards": {
      const step = off(eff.max_consecutive_rejects)
        ? "never moves on by itself"
        : `moves on after ${num(eff.max_consecutive_rejects)} rejects in a row`;
      const night = off(eff.max_consecutive_rejects_night)
        ? "never ends the night by itself"
        : `ends the night after ${num(eff.max_consecutive_rejects_night)} rejects in a row`;
      return `${step}, ${night}`;
    }
    default:
      return "";
  }
}

/** The note the whole column carries while a sequence is running.
 *
 *  THE ONE PANEL WHOSE ENGAGED STATE WAS A LIE, and still would be without
 *  this. The engine takes a COPY of the plan at Run (`sequence/engine.py`:
 *  `self.plan = plan`) and reads dither, refocus, filter offsets, the flip, the
 *  quality gates, cool-to and the end-of-run park/warm out of that copy for the
 *  rest of the night. So an operator who slides the safety monitor gate ON at
 *  2am and watches it stay ON walks away believing an unattended run will now
 *  park on an unsafe verdict. It will not.
 *
 *  A NOTE AND NOT A LOCK, deliberately: preparing tomorrow night's automation
 *  while tonight runs is a thing people do, and taking that away to fix a wrong
 *  claim would be a worse trade than telling the truth. Verbatim in substance
 *  from `SequenceView.tsx:1496`, with the em-dashes turned into hyphens. */
export function runCopyNote(runningPlanName: string): string {
  return `These settings apply to your NEXT run, not "${runningPlanName}" - the server `
    + "is executing the copy of the plan it took at Run, so changing anything here, "
    + "including the safety monitor gate, does not reach the run in progress. Stop it "
    + "and start it again to run under new settings.";
}
