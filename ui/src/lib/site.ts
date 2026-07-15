// lib/site.ts — pure geo helpers for the Settings → Site panel. No React, no
// DOM: npx-tsx testable (caps.ts/safety.ts precedent). Longitude is stored
// SIGNED East-positive, latitude signed +N (config.py:11-16 convention); the UI
// collects magnitude + hemisphere and converts here at the boundary.
import type { SavedLocation } from "../types";

export type Hemisphere = "N" | "S" | "E" | "W";

// The panel's converted (signed) draft — the comparison basis for saved
// locations and the payload basis for PUT /api/site.
export interface SiteDraft {
  name: string;
  latitude: number;   // signed +N
  longitude: number;  // signed +E
  elevation_m: number;
}

/** magnitude + hemisphere -> signed value. S/W negate; N/E keep. */
export function toSigned(magnitude: number, hemisphere: Hemisphere): number {
  return hemisphere === "S" || hemisphere === "W" ? -magnitude : magnitude;
}

/** signed value -> {magnitude, hemisphere}. axis picks the N/S vs E/W pair;
 *  0 maps to the positive hemisphere (N / E). */
export function fromSigned(
  signed: number,
  axis: "lat" | "lon",
): { magnitude: number; hemisphere: Hemisphere } {
  const magnitude = Math.abs(signed);
  if (axis === "lat") return { magnitude, hemisphere: signed < 0 ? "S" : "N" };
  return { magnitude, hemisphere: signed < 0 ? "W" : "E" };
}

/** Validate a latitude MAGNITUDE (0..90). Returns an error string or null. */
export function validateLat(mag: number): string | null {
  if (!Number.isFinite(mag)) return "Latitude must be a number";
  if (mag < 0 || mag > 90) return "Latitude must be between 0 and 90°";
  return null;
}

/** Validate a longitude MAGNITUDE (0..180). */
export function validateLon(mag: number): string | null {
  if (!Number.isFinite(mag)) return "Longitude must be a number";
  if (mag < 0 || mag > 180) return "Longitude must be between 0 and 180°";
  return null;
}

/** Validate an elevation in metres (−430..9000). */
export function validateElevation(v: number): string | null {
  if (!Number.isFinite(v)) return "Elevation must be a number";
  if (v < -430 || v > 9000) return "Elevation must be between −430 and 9000 m";
  return null;
}

/** Format a signed coordinate to 6 decimal places (mirrors the server store). */
export function formatCoord(v: number): string {
  return v.toFixed(6);
}

/** True when a converted (signed) draft equals a saved location — the dirty-
 *  state basis for the saved-locations row. Coordinates compare at 6-dp; name
 *  is trimmed. */
export function locationEquals(draft: SiteDraft, loc: SavedLocation): boolean {
  return (
    draft.name.trim() === loc.name.trim() &&
    formatCoord(draft.latitude) === formatCoord(loc.latitude) &&
    formatCoord(draft.longitude) === formatCoord(loc.longitude) &&
    formatCoord(draft.elevation_m) === formatCoord(loc.elevation_m)
  );
}
