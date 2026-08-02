// mosaicNightSummary.ts — pure reduction of a mosaic's per-panel peak altitudes into
// the few facts the Atlas "Tonight" panel states, INCLUDING the reason for every
// panel that has none.
//
// The defect this exists to close: the mosaic route answered for some panels and
// said nothing at all about the others. The server names the cause on the wire
// now (`transit_alt_error`, catalog/framing.py), but a reason that never reaches
// a screen is the same silence one layer down. Everything here is built so a
// panel without an altitude produces WORDS, never an omission — see
// `missingAltitudeReason`, which invents nothing but refuses to return "".
//
// It lives next to the component rather than in lib/ because it is Atlas
// presentation logic (lib/visibility.ts is the night CHART's formatter) and
// because it is pure: no React, no fetch, so the component's one interesting
// decision is testable on its own (mosaicNightSummary.test.ts).

import { fmtAlt } from "../../lib/visibility";

/** A panel as the server actually sends it.
 *
 *  `transit_alt_error` is on the wire but NOT yet in `types.ts` MosaicPanel, so
 *  this widening is where it is read — the same shape FocusView uses to read
 *  `fit` off the raw focus event until the type catches up. */
export interface PanelNight {
  row: number;
  col: number;
  transit_alt?: number;
  transit_alt_error?: string;
}

export interface MosaicNightSummary {
  total: number;
  /** Panels carrying a usable altitude. */
  answered: number;
  /** Peak-altitude spread across the answered panels; null when none are. */
  peak: { min: number; max: number } | null;
  /** Answered panels whose PEAK never reaches the horizon limit — i.e. panels
   *  that do not clear it at any point tonight, not panels that are low now. */
  belowLimit: number;
  /** Panels with no altitude at all. `total - answered`. */
  missing: number;
  /** Distinct server-stated causes, first-seen order. Empty iff missing === 0. */
  reasons: string[];
}

/** A panel is answered only by a real, finite number. `undefined` is the "we
 *  could not" case and `null`/NaN would be a non-measurement dressed as one. */
function altOf(p: PanelNight): number | null {
  const v = p.transit_alt;
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function summarisePanelNight(
  panels: PanelNight[],
  altLimitDeg: number,
): MosaicNightSummary {
  let answered = 0;
  let belowLimit = 0;
  let min = Infinity;
  let max = -Infinity;
  const reasons: string[] = [];

  for (const p of panels) {
    const alt = altOf(p);
    if (alt === null) {
      // A panel that lost its altitude AND its reason is the original bug
      // reappearing one layer up, so it gets a stated cause here rather than
      // being quietly folded into the count. We do not know WHY — and saying
      // exactly that is the honest reading of an absent key.
      const why = p.transit_alt_error?.trim() || "no reason was given";
      if (!reasons.includes(why)) reasons.push(why);
      continue;
    }
    answered++;
    if (alt < min) min = alt;
    if (alt > max) max = alt;
    if (alt < altLimitDeg) belowLimit++;
  }

  return {
    total: panels.length,
    answered,
    peak: answered > 0 ? { min, max } : null,
    belowLimit,
    missing: panels.length - answered,
    reasons,
  };
}

/** "9 panels peak 58°–66°" / "all 9 panels peak 64°" — the spread the centre
 *  curve above cannot show, because it is one point and this is up to a hundred.
 *  Null when nothing was answered (the caller states the reason instead). */
export function peakSpreadText(s: MosaicNightSummary): string | null {
  if (!s.peak) return null;
  const lo = fmtAlt(s.peak.min);
  const hi = fmtAlt(s.peak.max);
  // Rounded to whole degrees by fmtAlt, so a sub-degree spread reads as one
  // value — printing "64°–64°" would imply a measured range that isn't there.
  if (lo === hi) {
    return s.answered === s.total
      ? `All ${s.total} panels peak ${lo} tonight`
      : `${s.answered} of ${s.total} panels peak ${lo} tonight`;
  }
  return s.answered === s.total
    ? `${s.total} panels peak ${lo}–${hi} tonight`
    : `${s.answered} of ${s.total} panels peak ${lo}–${hi} tonight`;
}

/** "3 of 9 panels never clear 30° — they image low or not at all." Null when
 *  every answered panel clears the limit, so the line only appears when it is
 *  telling the user something. */
export function belowLimitText(
  s: MosaicNightSummary,
  altLimitDeg: number,
): string | null {
  if (s.belowLimit <= 0) return null;
  const who = s.belowLimit === s.answered && s.missing === 0
    ? `No panel of this mosaic clears ${Math.round(altLimitDeg)}° tonight`
    : `${s.belowLimit} of ${s.total} panels never clear ${Math.round(altLimitDeg)}° tonight`;
  return `${who} — their whole night is below your horizon limit.`;
}

/** The sentence that replaces a blank cell: who is missing an altitude, and the
 *  server's own cause. Null when nothing is missing.
 *
 *  Multiple distinct causes are all real (one panel can hit an OSError while
 *  another gets an unusable value back), so they are joined rather than reduced
 *  to the first — with a count when there are more than two, because a wall of
 *  stack-shaped strings in a tooltip is its own kind of unreadable. */
export function missingAltitudeReason(s: MosaicNightSummary): string | null {
  if (s.missing <= 0) return null;
  const who = s.missing === s.total
    ? `No panel of this mosaic has a peak altitude`
    : `${s.missing} of ${s.total} panels have no peak altitude`;
  const head = s.reasons.slice(0, 2).join("; ");
  const rest = s.reasons.length > 2 ? `; and ${s.reasons.length - 2} more` : "";
  return `${who} — ${head}${rest}`;
}

/** The short label on the read-only chip. The COUNT goes here (it is the part a
 *  glance needs); the cause rides in the chip's reason, which is reachable by
 *  tap, keyboard and screen reader. */
export function missingAltitudeLabel(s: MosaicNightSummary): string {
  return s.missing === s.total
    ? `No peak altitude for any panel`
    : `${s.missing} of ${s.total} panels: no peak altitude`;
}
