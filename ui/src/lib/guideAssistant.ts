// guideAssistant.ts — PURE logic + copy for the Guiding Assistant panel
// (design 2026-07-24 §4.4). The panel in GuideView.tsx is a thin render shell;
// all measurement→copy, the apply-payload builder, and the selective-apply
// merge live here so they are testable under plain `tsx` (no DOM).
//
// The apply builder maps recommendations onto the SAME GuideSettings shape the
// GuideView tuning drawer edits, then runs it through the EXISTING
// `validateGuideSettings` + `toSnake` so clamps + camel→snake stay single-
// sourced (guideSettings.ts) — buildApplyBody never re-implements a bound or a
// key rename.

import {
  GUIDE_ALGORITHM_DEFAULTS,
  validateGuideSettings,
  isValidRaAlgorithm,
  isValidDecAlgorithm,
  isValidDecGuideMode,
  toSnake,
  type GuideAlgorithmKind,
  type GuideSettings,
  type DecGuideMode,
} from "./guideSettings";

// ------------------------------------------------------------------ wire types
// Mirror the server `report_dict` shape (server/astrodeck/guide/assistant.py).
export interface AssistantRecommendation {
  key: string;
  field: string;
  current: unknown;
  recommended: unknown;
  unit: string;
  rationale: string;
  confidence: string; // "high" | "medium" | "low"
  advanced: boolean;
}

export interface AssistantBacklash {
  bl_px: number;
  bl_ms: number;
  sigma_ms: number;
  north_rate: number;
  result_code: string;
  y_rate_source: string;
  halted: boolean;
  // True only when the server actually DERIVED a backlash number. A default /
  // cut-short / edge-halted run reports result_code TOO_FEW_NORTH with bl_ms 0,
  // which must never be summarised as "negligible Dec backlash".
  measured: boolean;
}

export interface AssistantMeasurements {
  n: number;
  rms_ra_px: number;
  rms_dec_px: number;
  rms_total_px: number;
  rms_ra_arcsec: number | null;
  rms_dec_arcsec: number | null;
  rms_total_arcsec: number | null;
  drift_per_min_px: number;
  drift_per_min_arcsec: number | null;
  pe_amplitude_px: number;
  pe_period_s: number | null;
  jitter_px: number;
  image_scale_arcsec: number;
  image_scale_known: boolean;
  backlash: AssistantBacklash;
}

export interface AssistantPolar {
  verdict: string;
  tone: "good" | "warn" | "bad";
  drift_per_min_arcsec: number | null;
  drift_per_min_arcmin?: number;
  drift_per_min_px?: number;
}

export interface AssistantCurrent {
  ra_algorithm: string;
  dec_algorithm: string;
  dec_guide_mode: string;
  blc_pulse_ms: number;
  ra_params: Record<string, number>;
  dec_params: Record<string, number>;
}

export interface AssistantReport {
  measurements: AssistantMeasurements;
  recommendations: AssistantRecommendation[];
  current: AssistantCurrent;
  polar: AssistantPolar;
  samples: { t: number; ra: number; dec: number }[];
}

// The PUT /api/guide/settings body (matches GuideView's drawer save()).
export interface GuideSettingsPutBody {
  ra_algorithm: string;
  dec_algorithm: string;
  ra_params: Record<string, number>;
  dec_params: Record<string, number>;
  dec_guide_mode: DecGuideMode;
  blc_pulse_ms: number;
}

// ------------------------------------------------------------------- summarize
export interface AssistantSummary {
  headline: string;
  polarVerdict: string;
  tone: "good" | "warn" | "bad";
}

function _driftPhrase(m: AssistantMeasurements): string {
  if (m.drift_per_min_arcsec != null) {
    const a = Math.abs(m.drift_per_min_arcsec);
    if (a >= 60) return `drifts about ${(a / 60).toFixed(1)}′/min`;
    return `drifts about ${a.toFixed(0)}″/min`;
  }
  return `drifts about ${Math.abs(m.drift_per_min_px).toFixed(1)} px/min`;
}

/** The server's BL_* result code as a sentence. The advanced panel used to print
 *  the enum verbatim ("TOO_FEW_NORTH"), which tells a user nothing they can act
 *  on. Unknown codes fall through to a neutral sentence rather than leaking the
 *  identifier. */
export function backlashResultSentence(code: string): string {
  switch (code) {
    case "VALID":
      return "clean measurement";
    case "TOO_FEW_NORTH":
      return "few usable north steps — treat this as a rough figure";
    case "TOO_FEW_SOUTH":
      return "not enough south steps to measure — no figure derived";
    case "BL_NOT_CLEARED":
      return "the slack never took up, so nothing could be measured";
    case "SANITY":
      return "the numbers didn't add up, so the result was rejected";
    default:
      return "measurement result unavailable";
  }
}

// ------------------------------------------------- honest-disabled reasons
// The result card's Apply / Apply-selected / Start-over buttons are never
// natively `disabled` (house rule §11.8 — that strips the control out of the
// accessibility tree along with the reason, and leaves a dead grey rectangle
// under a fingertip). They dim, carry `aria-disabled`, and explain on press.
// The precedence between the blocking conditions is the only real logic, so it
// lives here, pure and pinned by a test, rather than in the render shell. The
// permission phrase is passed IN (it comes from lib/caps, which is React-bound
// and must not be imported into this module).

/** Shown while an apply PUT is in flight. Honest about the wait. */
export const APPLYING_REASON = "Applying the settings — this takes a moment.";

/** Shown when the advanced table has nothing ticked, so "Apply selected" has
 *  nothing to do. Before this the button was natively disabled with NO stated
 *  cause at all. */
export const EMPTY_SELECTION_REASON =
  "Nothing is ticked, so there is nothing to apply. Tick at least one row "
  + "above, or use Apply recommended settings to take the whole set.";

/** The reason an assistant apply-style button is inert right now, or `null`
 *  when it is live. First blocking condition wins: no permission beats an
 *  in-flight apply beats an empty selection. Omit `selectedCount` for the
 *  buttons that have no selection (novice Apply, Start over). */
export function applyActionReason(opts: {
  noPermissionReason?: string | null;
  busy: boolean;
  selectedCount?: number;
}): string | null {
  if (opts.noPermissionReason) return opts.noPermissionReason;
  if (opts.busy) return APPLYING_REASON;
  if (opts.selectedCount === 0) return EMPTY_SELECTION_REASON;
  return null;
}

function _backlashPhrase(b: AssistantBacklash): string {
  // Same gate as the server's recommend(): believe bl_ms ONLY when the run
  // derived it and was not cut short by the edge guard.
  const measured =
    b.measured === true && !b.halted
    && (b.result_code === "VALID" || b.result_code === "TOO_FEW_NORTH");
  if (measured && b.bl_ms > 0) {
    return `has about ${b.bl_ms} ms of Dec backlash`;
  }
  if (measured) return "has negligible Dec backlash";
  if (b.halted) {
    return "couldn't measure Dec backlash (the run stopped early to keep the "
      + "star on the sensor), so your current setting is left alone";
  }
  return "couldn't measure Dec backlash (the run was cut short), so your "
    + "current setting is left alone";
}

/** The novice summary card copy (design §4.1). Plain language only — never a raw
 *  number the user must interpret without a verdict. */
export function summarize(report: AssistantReport): AssistantSummary {
  const m = report.measurements;
  const headline =
    `Your mount ${_driftPhrase(m)} ` +
    `(polar alignment: ${report.polar.verdict}) and ${_backlashPhrase(m.backlash)}. ` +
    `I've prepared recommended settings.`;
  return { headline, polarVerdict: report.polar.verdict, tone: report.polar.tone };
}

// ---------------------------------------------------------- format for advanced
export interface RecommendationRow {
  key: string;
  field: string;
  label: string;
  current: unknown;
  recommended: unknown;
  unit: string;
  rationale: string;
  confidence: string;
  advanced: boolean;
  // Keys of OTHER rows that write the same field — ticking this row must untick
  // them (see `toggleRecommendationKey`). Empty for the ordinary one-row fields.
  conflicts: string[];
}

const FIELD_LABELS: Record<string, string> = {
  min_move: "Min-move (both axes)",
  aggression: "RA aggression",
  hysteresis: "RA hysteresis",
  ra_algorithm: "RA algorithm",
  dec_algorithm: "Dec algorithm",
  blc_pulse_ms: "Dec backlash pulse",
};

// Two recommendations can target the SAME field (the conservative default and
// an advanced opt-in — e.g. ra_algorithm→hysteresis vs ra_algorithm_ppec→ppec).
// Labelling them off `field` printed two identical "RA algorithm" rows, and
// ticking both silently applied whichever came last. Label off the KEY so each
// row says which choice it is.
const KEY_LABELS: Record<string, string> = {
  ra_algorithm: "RA algorithm → Hysteresis",
  ra_algorithm_ppec: "RA algorithm → Predictive PEC",
  dec_algorithm: "Dec algorithm → Resist Switch",
  dec_algorithm_lowpass2: "Dec algorithm → Lowpass2",
};

/** Advanced before→after rows (design §4.2). One row per recommendation, with a
 *  human label; the panel renders a per-row checkbox for selective apply. Rows
 *  that write the same field carry each other in `conflicts` so the panel can
 *  enforce the either/or the server intends. */
export function formatRecommendations(report: AssistantReport): RecommendationRow[] {
  return report.recommendations.map((r) => ({
    key: r.key,
    field: r.field,
    label: KEY_LABELS[r.key] ?? FIELD_LABELS[r.field] ?? r.field,
    current: r.current ?? "default",
    recommended: r.recommended,
    unit: r.unit,
    rationale: r.rationale,
    confidence: r.confidence,
    advanced: r.advanced,
    conflicts: report.recommendations
      .filter((o) => o.field === r.field && o.key !== r.key)
      .map((o) => o.key),
  }));
}

/** Toggle one recommendation key in the selective-apply set, enforcing the
 *  same-field either/or: ticking "RA algorithm → Predictive PEC" unticks
 *  "RA algorithm → Hysteresis" instead of letting apply-order decide. Pure. */
export function toggleRecommendationKey(
  report: AssistantReport,
  selected: ReadonlySet<string>,
  key: string,
): Set<string> {
  const next = new Set(selected);
  if (next.has(key)) {
    next.delete(key);
    return next;
  }
  const field = report.recommendations.find((r) => r.key === key)?.field;
  if (field) {
    for (const r of report.recommendations) {
      if (r.field === field && r.key !== key) next.delete(r.key);
    }
  }
  next.add(key);
  return next;
}

// --------------------------------------------------------------- apply builder
function _baseSettings(cur: AssistantCurrent): GuideSettings {
  const ra = isValidRaAlgorithm(cur.ra_algorithm)
    ? (cur.ra_algorithm as GuideAlgorithmKind)
    : "hysteresis";
  const dec = isValidDecAlgorithm(cur.dec_algorithm)
    ? (cur.dec_algorithm as GuideAlgorithmKind)
    : "resist_switch";
  const decMode = isValidDecGuideMode(cur.dec_guide_mode)
    ? (cur.dec_guide_mode as DecGuideMode)
    : "auto";
  return {
    ra: { algorithm: ra, params: { ...GUIDE_ALGORITHM_DEFAULTS[ra] } },
    dec: { algorithm: dec, params: { ...GUIDE_ALGORITHM_DEFAULTS[dec] } },
    decGuideMode: decMode,
    blcPulseMs: cur.blc_pulse_ms ?? 0,
  };
}

function _applyRec(s: GuideSettings, r: AssistantRecommendation): void {
  const num = typeof r.recommended === "number" ? r.recommended : Number(r.recommended);
  switch (r.field) {
    case "ra_algorithm":
      if (isValidRaAlgorithm(String(r.recommended))) {
        s.ra.algorithm = r.recommended as GuideAlgorithmKind;
        s.ra.params = { ...GUIDE_ALGORITHM_DEFAULTS[s.ra.algorithm] };
      }
      break;
    case "dec_algorithm":
      if (isValidDecAlgorithm(String(r.recommended))) {
        s.dec.algorithm = r.recommended as GuideAlgorithmKind;
        s.dec.params = { ...GUIDE_ALGORITHM_DEFAULTS[s.dec.algorithm] };
      }
      break;
    case "min_move":
      s.ra.params.minMove = num;
      s.dec.params.minMove = num;
      break;
    case "aggression":
      s.ra.params.aggression = num;
      break;
    case "hysteresis":
      s.ra.params.hysteresis = num;
      break;
    case "blc_pulse_ms":
      s.blcPulseMs = num;
      break;
  }
}

/** Build the validated, clamped PUT body from the accepted recommendations
 *  (design §4.4). `selectedFields` omitted ⇒ apply ALL non-advanced recs (the
 *  novice one-tap); provided ⇒ apply exactly those recommendation KEYS (advanced
 *  opt-in / selective apply). Algorithm changes are applied before param tweaks
 *  so a param rec is never clobbered by an algorithm's default reset. Runs
 *  through `validateGuideSettings` + `toSnake` so clamps + key rename are single-
 *  sourced — an out-of-range recommendation is clamped here exactly as a hand
 *  edit would be. */
export function buildApplyBody(
  report: AssistantReport,
  selectedKeys?: string[],
): GuideSettingsPutBody {
  const chosen = selectedKeys
    ? report.recommendations.filter((r) => selectedKeys.includes(r.key))
    : report.recommendations.filter((r) => !r.advanced);

  const s = _baseSettings(report.current);
  // algorithms first (they reset their axis params), then params, then blc.
  const order = (r: AssistantRecommendation) =>
    r.field === "ra_algorithm" || r.field === "dec_algorithm" ? 0 : 1;
  for (const r of [...chosen].sort((a, b) => order(a) - order(b))) _applyRec(s, r);

  const v = validateGuideSettings(s);
  return {
    ra_algorithm: v.ra.algorithm,
    dec_algorithm: v.dec.algorithm,
    ra_params: toSnake(v.ra.params),
    dec_params: toSnake(v.dec.params),
    dec_guide_mode: v.decGuideMode,
    blc_pulse_ms: v.blcPulseMs,
  };
}

/** Whether the applied selection CHANGES a guide algorithm vs the current config
 *  (design D4): an algorithm change wants a fresh calibration, so the panel
 *  clears calibration (novice: auto + toast; advanced: prompt). A param/backlash-
 *  only apply leaves calibration intact. */
export function applyChangesAlgorithm(
  report: AssistantReport,
  selectedKeys?: string[],
): boolean {
  const body = buildApplyBody(report, selectedKeys);
  return (
    body.ra_algorithm !== report.current.ra_algorithm ||
    body.dec_algorithm !== report.current.dec_algorithm
  );
}
