// catalogFormat.ts — display helpers for a CatalogEntry row (types.ts).
//
// WHY THIS EXISTS. Not every catalogued object has a published magnitude
// (NGC 604, IC 1604) and `alt`/`az` are only attached by the server for a
// caller holding view.site_derived (api/app.py's /api/catalog handler).
// CatalogEntry.mag is `number | null` and .alt is `number | undefined` for
// exactly that reason — a bare `r.mag.toFixed(1)` throws the instant a search
// returns one of those rows, which took down the whole Mount view for a
// three-character query ("604"). These three helpers are the one place that
// null/undefined case is handled, so no render site has to remember it.

/** The app's placeholder for a value nobody has published - a hyphen pair,
 *  never an em-dash (ARCHITECTURE.md copy rule #5), matching the same
 *  placeholder `next/lib/format.ts`'s `fmtDeg`/`fmtPct` already use. */
const NO_VALUE = "--";

/** "12.3", or the app's placeholder for a value nobody has published. */
export function fmtMag(mag: number | null | undefined): string {
  return mag == null ? NO_VALUE : mag.toFixed(1);
}

/** "55°", or the placeholder for a caller without view.site_derived (no
 *  alt/az on the row at all). */
export function fmtAlt(alt: number | null | undefined): string {
  return alt == null ? NO_VALUE : `${alt.toFixed(0)}°`;
}

/** Tone class for an altitude cell/badge: low glyph-red under 20°, good above
 *  40°, and the same muted class as any other unknown value when the row
 *  carries no alt at all. `normal` overrides the class for the 20-40° middle
 *  ground, which callers style differently (MountView's table leaves it
 *  unstyled; CatalogSearch's dropdown dims it). */
export function altTone(alt: number | null | undefined, normal = ""): string {
  if (alt == null) return "text-dim";
  if (alt < 20) return "text-warn";
  if (alt > 40) return "text-good";
  return normal;
}
