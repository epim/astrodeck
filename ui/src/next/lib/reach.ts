// reach.ts — "reach score" ranking for the Sky hub's Suggested targets / reach
// strip (README "1. Sky (home)" + "Formulas to lift" -> Reach score:
// "altitude now, minutes above the floor to dawn, cloud % at the target, moon
// separation; hidden kinds excluded").
//
// The prototype's own ranking (scratchpad seams/proto/logic.js) is a RAW,
// unbounded score (`alt*0.5 + (100-cloud)*0.4 + min(4,leftH)*5 - obst*60`) with
// no moon term. This module normalizes each factor to 0..1 and weights them so
// the result is a stable 0..1 score suitable for a progress-bar-style
// "altitude bar" (README "3. Suggested targets"); moon separation is folded in
// per the README's ranking inputs even though the prototype doesn't model it.
// ASSUMPTION (no source formula for the exact weights or the moon curve):
// altitude 0.35, clear-sky 0.35, time-above-floor 0.20 (capped at 4h, matching
// the prototype's `min(4, leftH)` cap), moon separation 0.10 (full credit at
// >=90 deg separation). Flagged in the T0.3 report.

export interface ReachInput {
  /** Altitude right now, degrees. */
  altNow: number;
  /** Minutes the target stays above the horizon floor, up to dawn. */
  minutesAboveFloorToDawn: number;
  /** Cloud cover at the target, 0..100. */
  cloudPct: number;
  /** Angular separation from the Moon, degrees. */
  moonSepDeg: number;
  /** This target's kind is hidden by the lens filter (README "Funnel"/lens). */
  hidden?: boolean;
}

function clamp01(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return Math.max(0, Math.min(1, v));
}

/** 0..1, or -1 for a hidden kind (excluded from ranking, per the README). */
export function reachScore(inp: ReachInput): number {
  if (inp.hidden) return -1;
  const altNorm = clamp01(inp.altNow / 90);
  const clearNorm = clamp01((100 - inp.cloudPct) / 100);
  const timeNorm = clamp01(inp.minutesAboveFloorToDawn / 240); // cap ~4h
  const moonNorm = clamp01(inp.moonSepDeg / 90);
  return altNorm * 0.35 + clearNorm * 0.35 + timeNorm * 0.2 + moonNorm * 0.1;
}

/** Sorts by score descending, attaches `.score`, drops hidden kinds entirely
 *  (README: "hidden kinds excluded" from the ranked list). */
export function rankTargets<T extends ReachInput>(list: T[]): (T & { score: number })[] {
  return list
    .map((t) => ({ ...t, score: reachScore(t) }))
    .filter((t) => t.score >= 0)
    .sort((a, b) => b.score - a.score);
}

/** "2h 40m" / "45m" — the reach-strip / lock-card window label. */
export function windowLabel(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes <= 0) return "0m";
  const m = Math.round(minutes);
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return h > 0 ? `${h}h ${mm}m` : `${mm}m`;
}
