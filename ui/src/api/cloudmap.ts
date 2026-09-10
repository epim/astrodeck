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

/** How long one dome grid is reused.
 *
 *  THE ROUTE IS FETCHED TWICE PER CYCLE ON `#/sky`. The finder's cloud layer
 *  polls it (`next/hubs/sky/finder/model.ts`, CLOUD_REFRESH_MS = 60_000) and so
 *  does the dome card's own panel (`components/cloudmap/SkyDomePanel`, POLL_MS
 *  = 60_000), and the two are not phase-locked, so the second one arrives some
 *  arbitrary number of seconds after the first and asks for the same grid. Each
 *  request makes the server WALK 540 rays through the cloud volume, which is
 *  why `getCloudmapDome`'s own docstring tells callers to pick a resolution
 *  they can draw.
 *
 *  Just under that shared cadence, so a poller asking every 60 s still gets a
 *  fresh grid every 60 s and its unlucky twin gets the one already in hand.
 *  Well inside a granule either way: GOES delivers every 5-10 minutes, and the
 *  grid carries its own `observed_at`, so nothing downstream mistakes a reused
 *  answer for a newer observation. */
export const DOME_TTL_MS = 55_000;

const domeKey = (altStep: number, azStep: number): string => `${altStep}x${azStep}`;
/** Keyed by resolution: two callers asking for different steps are asking two
 *  different questions and must not share an answer. */
const domeCache = new Map<string, { at: number; grid: CloudmapDome }>();
/** The SAME promise while a request is in the air, so two callers a millisecond
 *  apart make one request rather than two and a cache that fills too late. */
const domeInFlight = new Map<string, Promise<CloudmapDome>>();

/** Coarser steps cost the server real work per ray (it walks each one through
 *  the cloud volume), so callers pick the resolution they can actually draw.
 *
 *  Answers from a `DOME_TTL_MS` cache, shared across every caller in the tab.
 *  A FAILURE IS NEVER CACHED: the entry is only written on success, so a dead
 *  feed is retried on the next poll rather than remembered for a minute.
 *
 *  The returned object is shared, not copied. Nothing in this app mutates a
 *  dome grid - it is drawn and read - and copying 540 cells per caller to guard
 *  against a mutation nobody makes would give the saved request straight back. */
export function getCloudmapDome(altStep = 6, azStep = 10): Promise<CloudmapDome> {
  const key = domeKey(altStep, azStep);
  const hit = domeCache.get(key);
  if (hit && Date.now() - hit.at < DOME_TTL_MS) return Promise.resolve(hit.grid);
  const flying = domeInFlight.get(key);
  if (flying) return flying;
  const p = api
    .get<CloudmapDome>(`/api/cloudmap/dome?alt_step=${altStep}&az_step=${azStep}`)
    .then((grid) => {
      domeCache.set(key, { at: Date.now(), grid });
      return grid;
    })
    .finally(() => { domeInFlight.delete(key); });
  domeInFlight.set(key, p);
  return p;
}

/** Drop everything cached. For tests, and for a caller that has just changed
 *  the cloudmap configuration and must not draw the old platform's grid. */
export function resetCloudmapDomeCache(): void {
  domeCache.clear();
  domeInFlight.clear();
}

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
