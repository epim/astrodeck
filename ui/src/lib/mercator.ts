// lib/mercator.ts — pure Web-Mercator slippy-tile math + spherical geometry
// for the radar map's scope overlay (weather spec §11). No React, no DOM:
// npx-tsx testable (lib/site.ts precedent).

export const TILE_SIZE = 256;
export const MIN_Z = 3; // server zoom clamp (spec §6)
export const MAX_Z = 11;
export const EARTH_RADIUS_KM = 6371;
/** Web-Mercator latitude limit — beyond this latToTileY diverges. */
export const MERCATOR_MAX_LAT = 85.0511;
/** Pierce markers hide below this altitude (spec §11 clamp). */
export const MIN_PIERCE_ALT_DEG = 3;
/** ...and beyond this downrange distance (spec §11 clamp). */
export const MAX_PIERCE_KM = 150;
/** Cloud-deck heights (km): low=2, mid=5, high=9 (spec §11). */
export const CLOUD_DECKS_KM = { low: 2, mid: 5, high: 9 } as const;

const rad = (d: number): number => (d * Math.PI) / 180;
const deg = (r: number): number => (r * 180) / Math.PI;

export function clampLat(lat: number): number {
  return Math.max(-MERCATOR_MAX_LAT, Math.min(MERCATOR_MAX_LAT, lat));
}

export function clampZoom(z: number): number {
  return Math.max(MIN_Z, Math.min(MAX_Z, Math.round(z)));
}

/** Longitude -> fractional tile-x at zoom z (slippy convention). */
export function lonToTileX(lon: number, z: number): number {
  return ((lon + 180) / 360) * Math.pow(2, z);
}

/** Latitude -> fractional tile-y at zoom z (slippy convention). */
export function latToTileY(lat: number, z: number): number {
  const r = rad(clampLat(lat));
  return ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * Math.pow(2, z);
}

export function tileXToLon(x: number, z: number): number {
  return (x / Math.pow(2, z)) * 360 - 180;
}

export function tileYToLat(y: number, z: number): number {
  const n = Math.PI - (2 * Math.PI * y) / Math.pow(2, z);
  return deg(Math.atan(0.5 * (Math.exp(n) - Math.exp(-n))));
}

/** Spherical destination point: from (lat, lon) travel distKm along the great
 *  circle at compass bearingDeg (0 = north, 90 = east). */
export function destPoint(
  lat: number,
  lon: number,
  bearingDeg: number,
  distKm: number,
): { lat: number; lon: number } {
  const delta = distKm / EARTH_RADIUS_KM;
  const theta = rad(bearingDeg);
  const phi1 = rad(lat);
  const lam1 = rad(lon);
  const phi2 = Math.asin(
    Math.sin(phi1) * Math.cos(delta) +
      Math.cos(phi1) * Math.sin(delta) * Math.cos(theta),
  );
  const lam2 =
    lam1 +
    Math.atan2(
      Math.sin(theta) * Math.sin(delta) * Math.cos(phi1),
      Math.cos(delta) - Math.sin(phi1) * Math.sin(phi2),
    );
  return { lat: deg(phi2), lon: ((deg(lam2) + 540) % 360) - 180 };
}

/** Downrange distance (km) where a sight line at altDeg pierces a cloud deck
 *  deckKm above the site: deck_km / tan(alt) (spec §11). Returns null when the
 *  marker should be hidden: below 3° altitude or beyond 150 km. */
export function pierceDistanceKm(altDeg: number, deckKm: number): number | null {
  if (!Number.isFinite(altDeg) || altDeg < MIN_PIERCE_ALT_DEG || altDeg > 90) {
    return null;
  }
  const d = deckKm / Math.tan(rad(altDeg));
  if (!Number.isFinite(d) || d < 0) return null;
  return d > MAX_PIERCE_KM ? null : d;
}
