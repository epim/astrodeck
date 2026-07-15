// api/site.ts — typed client for the site / mount-GPS / saved-locations surfaces
// (spec §5). Thin over the shared `api` fetch wrapper (api.ts): same ApiError
// throwing + per-path timeouts. Each function maps 1:1 to a verified route.
import { api } from "../api";
import type { AppConfig, SavedLocation, Site } from "../types";

/** Best-effort mount GPS read-back (GET /api/site/mount-gps). config.site_optics.
 *  Always resolves 200; `available:false` carries a human `detail`. */
export interface MountGps {
  available: boolean;
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
export const getMountGps = (): Promise<MountGps> =>
  api.get<MountGps>("/api/site/mount-gps");

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
