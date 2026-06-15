// horizon.ts — pure horizon verdict (onboarding spec §1d).
// No HORIZON_MIN_DEG constant in TS: `horizonMin` comes from
// `site.horizon_min_deg` (server-owned, single source of truth).

export type HorizonVerdict = "ok" | "low" | "below" | "unknown";

/**
 * Verdict for a target altitude.
 * - `unknown` when the site is still the default OR altitude could not be fetched.
 * - `below` when alt < 0 (below the visible horizon now).
 * - `low` when 0 <= alt < horizonMin.
 * - `ok` when alt >= horizonMin.
 */
export function horizonVerdict(
  alt: number | null | undefined,
  horizonMin: number,
  siteIsDefault: boolean,
): HorizonVerdict {
  if (siteIsDefault || alt == null || Number.isNaN(alt)) return "unknown";
  if (alt < 0) return "below";
  if (alt < horizonMin) return "low";
  return "ok";
}
