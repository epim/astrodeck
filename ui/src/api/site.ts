// api/site.ts — typed client for the site / mount-GPS / saved-locations surfaces
// (spec §5). Thin over the shared `api` fetch wrapper (api.ts): same ApiError
// throwing + per-path timeouts. Each function maps 1:1 to a verified route.
import { api } from "../api";
import type { AppConfig, SavedLocation, Site } from "../types";

/** Best-effort mount GPS read-back (GET /api/site/mount-gps). config.site_optics.
 *  Always resolves 200; `available:false` carries a human `detail`. */
export interface MountGps {
  available: boolean;
  source?: "mount" | "usb";
  detected?: boolean;
  latitude?: number;
  longitude?: number;
  elevation_m?: number;
  detail?: string;
}

/** Create/update body for a saved location (server LocationBody). */
export interface LocationInput {
  name: string;
  latitude: number;
  longitude: number;
  elevation_m: number;
  horizon_min_deg?: number | null;
  // S1 (additive): the drawn horizon polyline, [az, alt] pairs, az 0..360, alt
  // -10..90. Omit/null = leave the location's line as "not drawn"; [] = no
  // obstructions. The server sorts and dedupes by azimuth and 422s a bad point.
  horizon_points?: [number, number][] | null;
}

/** PUT /api/site {site, version, horizon_min_deg?} -> redacted config payload.
 *  config.site_optics (+ config.safety when horizon_min_deg is present). Pass
 *  `horizonMinDeg` ONLY when applying a saved location that carries one and the
 *  principal holds config.safety (§5); manual edits omit it (server preserves
 *  the stored value). 409 on a version conflict. */
export const saveSite = (
  site: Site,
  version: number | null,
  horizonMinDeg?: number,
): Promise<AppConfig> => {
  const body: { site: Site; version: number | null; horizon_min_deg?: number } = {
    site,
    version,
  };
  if (horizonMinDeg !== undefined) body.horizon_min_deg = horizonMinDeg;
  return api.put<AppConfig>("/api/site", body);
};

/** GET /api/site/mount-gps. config.site_optics. */
export const getMountGps = (detectedOnly = false): Promise<MountGps> =>
  api.get<MountGps>(`/api/site/mount-gps${detectedOnly ? "?detected_only=true" : ""}`);

/** GET /api/locations -> full list. config.site_optics. */
export const listLocations = (): Promise<SavedLocation[]> =>
  api.get<SavedLocation[]>("/api/locations");

/** POST /api/locations. 409 {code:"name_collision"|"library_full"}. */
export const saveLocation = (body: LocationInput): Promise<SavedLocation> =>
  api.post<SavedLocation>("/api/locations", body);

/** PUT /api/locations/{id}. 409 name_collision / 404 not_found. */
export const updateLocation = (
  id: string,
  body: LocationInput,
): Promise<SavedLocation> =>
  api.put<SavedLocation>(`/api/locations/${id}`, body);

/** DELETE /api/locations/{id}. 404 not_found. */
export const deleteLocation = (id: string): Promise<void> =>
  api.del<void>(`/api/locations/${id}`);

// ------------------------------------------------------------------ S1 horizon
// Imported here rather than widening the header import, so this block is a pure
// append.
import type { SiteInfo } from "../types";

/** GET /api/site -> the active site plus the horizon polyline the ENGINE is
 *  gating slews with (config.safety.horizon, not the library's copy). view.status.
 *  For a caller lacking view.site_precise the four precise keys are ABSENT;
 *  `horizon_min_deg` and `horizon_points` are retained. */
export const getSite = (): Promise<{ site: SiteInfo; version: number }> =>
  api.get<{ site: SiteInfo; version: number }>("/api/site");

/** POST /api/locations/{id}/apply -> make a saved location the active site AND
 *  copy its drawn horizon into config.safety.horizon. config.site_optics, plus
 *  config.safety when the location carries a horizon (403 otherwise, before any
 *  write). Prefer this over saveSite() for a saved location: saveSite writes the
 *  coordinates only, so the drawn line would stay in the library and gate
 *  nothing. 404 not_found. */
export const applyLocation = (id: string): Promise<AppConfig> =>
  api.post<AppConfig>(`/api/locations/${id}/apply`, {});
