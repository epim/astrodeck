// sitesModel.ts - pure helpers for the Sites sheet (T-SKY-4): great-circle
// distance from the phone's GPS fix, the coordinate/horizon summary lines a
// row prints (honouring the view.site_precise privacy rule), and which saved
// location - if any - IS the persisted active site.
//
// Pure and store-free on purpose: every rule here is worth unit-testing
// without mounting React, and `sites.tsx` is thin glue over these functions.

import type { SavedLocation, Site } from "../../../../types";

export interface GeoFix {
  lat: number;
  lon: number;
  /** metres, from `GeolocationPosition.coords.accuracy`. */
  accuracyM: number;
}

const EARTH_RADIUS_M = 6371000;

function toRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

/** Great-circle distance in metres (haversine). Accurate enough to ORDER a
 *  handful of sites a few kilometres apart; nothing here points a mount. */
export function distanceMeters(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

export interface LocationWithDistance extends SavedLocation {
  /** null when there is no phone fix yet - the caller keeps server order and
   *  hides the distance column (A.14: "with no fix, keep server order"). */
  distanceM: number | null;
}

/** Sort by distance from `fix`. With no fix, the input order survives
 *  unchanged (server order) - never re-sorted by name or id as a fallback,
 *  which would look like a real ranking it is not. */
export function sortByDistance(
  locations: SavedLocation[],
  fix: GeoFix | null,
): LocationWithDistance[] {
  if (!fix) return locations.map((l) => ({ ...l, distanceM: null }));
  const withDist = locations.map((l) => ({
    ...l,
    distanceM: distanceMeters(fix.lat, fix.lon, l.latitude, l.longitude),
  }));
  withDist.sort((a, b) => (a.distanceM as number) - (b.distanceM as number));
  return withDist;
}

/** "<1000 m" -> "N m", else "N.N km" (A.14 `fmtDist`). */
export function fmtDist(meters: number): string {
  if (meters < 1000) return `${Math.round(meters)} m`;
  return `${(meters / 1000).toFixed(1)} km`;
}

/** Green under 60 m (close enough that "distance" is really "you are here"),
 *  dim otherwise (A.14: "green under 60 m, #9aa6c2 otherwise"). */
export function distanceTone(meters: number | null): "good" | "dim" {
  return meters != null && meters < 60 ? "good" : "dim";
}

/** The nearest saved location's name to `fix`, or null with none/no fix -
 *  the phone-GPS card's "nearest <name>" clause. */
export function nearestName(locations: SavedLocation[], fix: GeoFix | null): string | null {
  if (!fix || locations.length === 0) return null;
  let best: SavedLocation | null = null;
  let bestD = Infinity;
  for (const l of locations) {
    const d = distanceMeters(fix.lat, fix.lon, l.latitude, l.longitude);
    if (d < bestD) { bestD = d; best = l; }
  }
  return best?.name ?? null;
}

/** magnitude + hemisphere letter, for the phone-GPS line ("47.6108 deg N"
 *  rather than a signed -122.33 nobody reads as "west" at a glance). */
export function fmtFixDeg(signed: number, axis: "lat" | "lon"): string {
  const mag = Math.abs(signed).toFixed(4);
  const hemi = axis === "lat" ? (signed < 0 ? "S" : "N") : (signed < 0 ? "W" : "E");
  return `${mag}° ${hemi}`;
}

/** The phone-GPS card's coordinate line once a fix has arrived (A.14):
 *  "<lat> deg N * <lon> deg W * +-<acc> m * nearest <name>" - or without a
 *  saved location to compare against, the line simply omits that clause. */
export function fmtFixLine(fix: GeoFix, nearest: string | null): string {
  const base = `${fmtFixDeg(fix.lat, "lat")} · ${fmtFixDeg(fix.lon, "lon")} · ±${Math.round(fix.accuracyM)} m`;
  return nearest ? `${base} · nearest ${nearest}` : base;
}

/** A saved location's coordinate line, or the derived-privacy line for a
 *  principal lacking `view.site_precise` (A.14: "the coords line is replaced
 *  by 'site set - precise location hidden for this role'", never a number). */
export function coordLine(
  loc: Pick<SavedLocation, "latitude" | "longitude" | "elevation_m">,
  canSeePrecise: boolean,
): string {
  if (!canSeePrecise) return "site set · precise location hidden for this role";
  return `${loc.latitude.toFixed(4)}° · ${loc.longitude.toFixed(4)}° · ${Math.round(loc.elevation_m)} m`;
}

/** True when `points` marks an open horizon (nothing drawn yet - null,
 *  undefined or empty are the same fact for display purposes). */
export function horizonIsOpen(points: [number, number][] | null | undefined): boolean {
  return !points || points.length === 0;
}

/** "horizon: N points - up to X deg" / "horizon: open - nothing marked yet"
 *  (A.14 row summary). The amber "open" tone is the caller's job (it knows
 *  the Tone type this module deliberately does not import). */
export function horizonSummaryLine(points: [number, number][] | null | undefined): string {
  if (horizonIsOpen(points)) return "horizon: open · nothing marked yet";
  const pts = points as [number, number][];
  const maxAlt = Math.round(Math.max(...pts.map((p) => p[1])));
  const noun = pts.length === 1 ? "point" : "points";
  return `horizon: ${pts.length} ${noun} · up to ${maxAlt}°`;
}

/** True when `loc` IS (to six decimal places, matching `lib/site.ts`'s
 *  `locationEquals`) the persisted active site - the radio ring's checked
 *  state. A principal without `view.site_precise` never sees a match: the
 *  stripped `site` carries no coordinates to compare, and claiming a match
 *  against nothing would be a fact the screen cannot back up. */
export function isActiveLocation(
  loc: Pick<SavedLocation, "name" | "latitude" | "longitude" | "elevation_m">,
  site: Pick<Site, "is_default" | "name" | "latitude" | "longitude" | "elevation_m"> | null | undefined,
): boolean {
  if (!site || site.is_default) return false;
  if (
    typeof site.latitude !== "number" ||
    typeof site.longitude !== "number" ||
    typeof site.elevation_m !== "number"
  ) {
    return false;
  }
  const close = (a: number, b: number) => Math.abs(a - b) < 1e-6;
  return (
    (site.name ?? "").trim() === loc.name.trim() &&
    close(site.latitude, loc.latitude) &&
    close(site.longitude, loc.longitude) &&
    close(site.elevation_m, loc.elevation_m)
  );
}

/** The toast after a successful apply (A.14, verbatim template - `mk` there
 *  is "the applied location carries a drawn horizon"). */
export function applyToastText(name: string, hasHorizonPoints: boolean): string {
  const where = hasHorizonPoints
    ? "its horizon is on the finder and the dome."
    : "open horizon: capture a photosphere or draw one with EDIT.";
  return `${name} selected - ${where}`;
}
