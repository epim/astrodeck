// api/cloudmap.ts — typed wrappers for the GOES cloud-occlusion routes.
//
// The model shipped complete in stage 6a and had no UI at all until now; it
// also had no way to be switched on until 0.3.10, so on most rigs these will
// answer `enabled: false` and that is the normal, expected state rather than
// an error.
import { api } from "../api";

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
