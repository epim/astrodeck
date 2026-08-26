// api/cloudmap.ts — typed wrappers for the GOES cloud-occlusion routes.
//
// The model shipped complete in stage 6a and had no UI at all until now; it
// also had no way to be switched on until 0.3.10, so on most rigs these will
// answer `enabled: false` and that is the normal, expected state rather than
// an error.
import { api } from "../api";
import type { AppConfig } from "../types";

export interface CloudMotion {
  speed_kmh: number;
  toward_deg: number;
  /** The wind column agreed. False means the correlation found a shift no
   *  forecast level supports -- honest, and usually a still sky. */
  corroborated: boolean;
  peak: number;
  dt_s: number;
  /** Why the estimator believes what it believes, in a sentence. */
  reason: string;
}

export interface CloudmapStatus {
  enabled: boolean;
  platform: "G18" | "G19";
  observed_at: string | null;
  age_s: number | null;
  stale: boolean;
  last_error: string | null;
  motion: CloudMotion | null;
  /** What the geometry would pick for the configured site, independent of what
   *  is configured. Null when no site is set. It differs from `platform` only
   *  when the operator has pinned a bird -- see CloudmapPanel for why that is
   *  worth saying out loud rather than silently correcting. */
  suggested_platform?: "G18" | "G19" | null;
  credit: { source: string; url: string };
}

export interface CloudmapDome {
  enabled: boolean;
  observed_at: string | null;
  stale: boolean;
  /** rows[i] is the altitude band at `alt_start + i * alt_step`; each entry an
   *  azimuth sample at `j * az_step`. Null where the model has no reading. */
  rows: (number | null)[][];
  alt_start: number;
  alt_step: number;
  az_step: number;
  /** Ground footprint of one satellite pixel, km. A sanity check worth showing:
   *  it should match the platform's geometry for this site. */
  cell_km?: [number, number];
  reason?: string | null;
}

export interface CloudmapAt {
  enabled: boolean;
  alt_deg: number;
  az_deg: number;
  ahead_s: number;
  probability: number | null;
  /** "mask_only" | "forecast" | "no_data" -- what the number is based on.
   *  "no_data" is what you get before two granules exist, because motion needs
   *  a pair to correlate. */
  basis: string;
  crossing_km: number | null;
  pierce_lat_deg?: number | null;
  pierce_lon_deg?: number | null;
  downrange_km?: number | null;
  beam_m?: number | null;
}

export const getCloudmap = (): Promise<CloudmapStatus> =>
  api.get<CloudmapStatus>("/api/cloudmap");

/** Coarser steps cost the server real work per ray (it walks each one through
 *  the cloud volume), so callers pick the resolution they can actually draw. */
export const getCloudmapDome = (altStep = 6, azStep = 10): Promise<CloudmapDome> =>
  api.get<CloudmapDome>(`/api/cloudmap/dome?alt_step=${altStep}&az_step=${azStep}`);

export const getCloudmapAt = (alt: number, az: number, aheadS = 0): Promise<CloudmapAt> =>
  api.get<CloudmapAt>(
    `/api/cloudmap/at?alt=${alt.toFixed(3)}&az=${az.toFixed(3)}&ahead_s=${Math.round(aheadS)}`);

/** The persisted config block, as GET /api/config returns it. */
export interface CloudmapConfig {
  enabled: boolean;
  platform: "auto" | "G18" | "G19";
  poll_minutes: number;
  half_px: number;
}

/**
 * POST /api/config {cloudmap} -> persist the WHOLE block.
 *
 * Wholesale replace, like setSafetyConfig: the server's `set_cloudmap` swaps
 * the block rather than merging, so the caller must echo every field with its
 * edits applied. Requires config.site_optics.
 *
 * There is no /api/config/cloudmap. The block rides the generic config POST,
 * which is why it 422s at binding if a field is wrong rather than clamping.
 */
export const setCloudmapConfig = (cloudmap: CloudmapConfig): Promise<AppConfig> =>
  api.post<AppConfig>("/api/config", { cloudmap });
