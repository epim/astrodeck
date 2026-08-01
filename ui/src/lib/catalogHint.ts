// catalogHint.ts — FALLBACK zero-state copy for the Atlas catalog search.
//
// The server now answers this itself: /api/catalog?explain=1 returns notes
// composed by the code that knows what it carries and whether the ephemeris
// responded, and CatalogSearch renders those in preference to anything here.
// This module is what an OLDER server leaves behind, so it must say only what
// is true of every version.
//
// It previously said "Planets aren't supported yet" and "Messier/NGC/IC
// deep-sky objects only". Both became false the night 241 named stars and a
// live Sun/Moon/planet ephemeris shipped, and the UI went on saying them —
// which is how a user ends up told that Polaris does not exist.
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
  "That body is not in this catalog — the Sun, Moon and naked-eye planets are, "
  + "so check the spelling, or use manual coordinates.";
export const GENERAL_SCOPE_HINT =
  "Search covers deep-sky objects (M31, NGC 7000), named stars (Polaris, Caph, "
  + "beta Cas) and solar-system bodies.";

/** The one-line hint shown under a zero-result search (spec: a solar-system
 *  guess gets the planet-specific explanation; anything else gets the
 *  general catalog-scope reminder). */
export function catalogScopeHint(query: string): string {
  return looksLikeSolarSystemQuery(query) ? PLANET_SCOPE_HINT : GENERAL_SCOPE_HINT;
}
