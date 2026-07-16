// catalogHint.ts — pure zero-state copy for the Atlas catalog search
// (R2-ATL-01 minimal, ATLAS-01, r1 ATL-02). The server catalog
// (server/astrodeck/catalog/objects.py) is a curated Messier/NGC/IC deep-sky
// list ONLY — no Sun/Moon/planet ephemerides (that's a feature, explicitly
// deferred: R2-ATL-01 full). When a "no matches" query looks like it's
// naming one of those bodies, say so instead of a bare empty state.
const SOLAR_SYSTEM_BODIES = [
  "sun", "moon", "mercury", "venus", "earth", "mars", "jupiter",
  "saturn", "uranus", "neptune", "pluto",
];

/** True when `query` looks like a Sun/Moon/planet search — a prefix match
 *  against the body's full name, either direction, case-insensitive. A
 *  needle under 3 chars is too ambiguous to guess from ("me" -> Mercury? the
 *  M-catalog?), so it never matches. */
export function looksLikeSolarSystemQuery(query: string): boolean {
  const q = query.trim().toLowerCase();
  if (q.length < 3) return false;
  return SOLAR_SYSTEM_BODIES.some((b) => b.startsWith(q) || q.startsWith(b));
}

export const PLANET_SCOPE_HINT =
  "Planets aren't supported yet — use manual coordinates or free-roam.";
export const GENERAL_SCOPE_HINT =
  "Catalog covers Messier/NGC/IC deep-sky objects only — try a name or ID (e.g. M31).";

/** The one-line hint shown under a zero-result search (spec: a solar-system
 *  guess gets the planet-specific explanation; anything else gets the
 *  general catalog-scope reminder). */
export function catalogScopeHint(query: string): string {
  return looksLikeSolarSystemQuery(query) ? PLANET_SCOPE_HINT : GENERAL_SCOPE_HINT;
}
