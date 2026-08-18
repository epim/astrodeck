// skyRegion.ts — "what is catalogued in the patch of sky I am looking at",
// fetched once per REGION rather than once per view.
//
// WHY THE QUERY IS SERVER-SIDE AND THE CACHE IS CLIENT-SIDE.
//
// The catalogue is 588 KB of TSV for 13,370 deep-sky objects, plus 241 named
// stars, plus a live astropy ephemeris for the Sun, Moon and planets. Shipping
// the first would more than double a bundle that is served off a Pi over field
// WiFi (vite.config.ts carries a measured note about trading 5 kB chunks for
// round trips); the third cannot be shipped at all, because it is computed, not
// stored. And an unattended multi-night run has no browser in it — an
// identification computed in a tab is absent for exactly the sessions this rig
// exists to run.
//
// "Offline-first" is not an argument for the client here, because the server IS
// on the rig. The round trip is a LAN hop to a box in the garden, not a request
// to the internet, and it works with the house's uplink down.
//
// What makes it feel instant is that the Atlas does not need a query per frame.
// It needs a REGION, and the sky does not move: fetch a circle 1.6× the
// viewport, then re-project it locally with `skyToView` on every frame — which
// is what lib/skyMarkers.ts does, at a cost of one gnomonic per row. A fetch
// fires only when the view leaves the cached circle, when the zoom changes by
// more than 1.4×, or when the cached solar-system positions age out. During a
// hard continuous pan that is roughly one fetch every couple of seconds; at
// rest it is zero, forever.
//
// Measured server-side on this tree: 0.8 ms for a 2° query over the whole
// catalogue, 3.4 ms for a 30° one including the (cached) ephemeris.

import { useEffect, useRef, useState } from "react";
import { u } from "./base";
import { angularSepDeg } from "./atlasFov";

export type SkyKind = "dso" | "star" | "solar_system";

/** One row of GET /api/catalog/region. Everything the canvas draws and
 *  everything the info card shows is in here — there is no per-object detail
 *  route, so a tap costs no round trip and works with the network down. */
export interface SkyRow {
  id: string;
  /** Short text drawn beside the marker: a common name when one exists, else
   *  the designation. 173 of 13,370 catalog rows have a real common name. */
  label: string;
  kind: SkyKind;
  /** Readable type — "Galaxy", "Planetary Nebula", "Star", "Planet". */
  type: string;
  ra_hours: number;
  dec_deg: number;
  /** null = NOBODY PUBLISHED ONE (1,823 rows). Not 99, not 0, not "faint". */
  mag: number | null;
  size_arcmin: number;
  constellation: string | null;
  /** One sentence composed server-side from the fields the object actually
   *  has. Never empty, never a placeholder. */
  describe: string;
  /** A second designation where one exists — "NGC 224" for M31 (109 rows), the
   *  Bayer letter for a star. The single most useful line for anyone holding a
   *  printed chart. */
  alias: string | null;
  sep_deg?: number;
  /** Solar-system rows only: the instant this position was computed for. */
  ephemeris_unix?: number;
  topocentric?: boolean;
  /** Why the position was computed from the centre of the Earth, when it was.
   *
   *  Two reasons, and they need two sentences. "site_unset" is a setting the
   *  user can fix. "not_permitted" (#203) is their ROLE: the planets are
   *  computed site-free for anyone without `view.site_derived`, because a
   *  topocentric row is a location oracle. Telling that user "no site is set"
   *  would send them to change a setting that is already correct. */
  geocentric_reason?: "site_unset" | "not_permitted" | null;
}

export interface RegionResponse {
  center: { ra_hours: number; dec_deg: number };
  radius_deg: number;
  fov_deg: number;
  rows: SkyRow[];
  truncated: boolean;
  catalog_degraded: boolean;
  notes: string[];
}

export interface CachedRegion {
  ra_hours: number;
  dec_deg: number;
  radius_deg: number;
  fov_deg: number;
  rows: SkyRow[];
  truncated: boolean;
  degraded: boolean;
  notes: string[];
  /** ms, Date.now() at the moment the response landed. */
  at: number;
  /** Whether any row in it moves (i.e. a solar-system body). */
  ephemeral: boolean;
}

/** Radius asked for, as a multiple of the view width: 0.8 × fov is 1.6 × the
 *  viewport edge-to-edge, so the user can pan most of a screen in any direction
 *  before the cache misses. */
export const RADIUS_FACTOR = 0.8;
/** How far the centre may drift inside the cached circle before refetching. */
export const DRIFT_FRAC = 0.25;
/** Zoom change that invalidates a region, in either direction. */
export const ZOOM_FACTOR = 1.4;
/**
 * How long a region holding solar-system rows may be reused, ms.
 *
 * The Moon moves ~0.55°/hour, so a two-minute-old marker is at most ~1.1'
 * stale — under the Moon's own apparent radius. The server caches the
 * ephemeris for the same 120 s and stamps every such row with the instant it
 * was computed for, so this is the client half of one stated staleness rather
 * than a second, hidden one.
 */
export const EPHEMERAL_TTL_MS = 120_000;

/**
 * Does the view need a new region, or does the cached one still answer it?
 *
 * Pure, and exported, because this is the whole performance argument: if it
 * ever returns true too often the Atlas fetches on every pointer move, and
 * nothing about the picture would look wrong while it did.
 */
export function regionNeedsFetch(
  cached: CachedRegion | null,
  center: { ra_hours: number; dec_deg: number },
  fovDeg: number,
  nowMs: number,
): boolean {
  if (!cached) return true;
  const zoom = fovDeg / cached.fov_deg;
  if (zoom > ZOOM_FACTOR || zoom < 1 / ZOOM_FACTOR) return true;
  const drift = angularSepDeg(center, {
    ra_hours: cached.ra_hours, dec_deg: cached.dec_deg,
  });
  if (drift > DRIFT_FRAC * cached.radius_deg) return true;
  // A region whose only stale rows are fixed stars and galaxies never expires:
  // those coordinates were true in 2000 and will be true in 2050.
  if (cached.ephemeral && nowMs - cached.at > EPHEMERAL_TTL_MS) return true;
  return false;
}

export function regionUrl(
  center: { ra_hours: number; dec_deg: number }, fovDeg: number, limit: number,
): string {
  const radius = fovDeg * RADIUS_FACTOR;
  const q = new URLSearchParams({
    ra_hours: wrapRa(center.ra_hours).toFixed(6),
    dec_deg: clampDec(center.dec_deg).toFixed(6),
    radius_deg: radius.toFixed(6),
    fov_deg: fovDeg.toFixed(6),
    limit: String(limit),
  });
  return u(`/api/catalog/region?${q.toString()}`);
}

/** The route validates 0 <= ra < 24 and -90 <= dec <= 90 and 422s outside it.
 *  A pan past the pole or across 24h is an ordinary gesture, not an error, so
 *  it is normalised here rather than turned into a failed request. */
function wrapRa(ra: number): number {
  const r = ra % 24;
  return r < 0 ? r + 24 : r;
}
function clampDec(dec: number): number {
  return Math.min(90, Math.max(-90, dec));
}

export interface SkyRegionState {
  rows: SkyRow[];
  /** True only while a fetch is in flight AND nothing is cached to draw. */
  loading: boolean;
  /** A sentence, or null. Never a bare "error". */
  error: string | null;
  /** The server answered from the 64 curated objects because the bulk
   *  deep-sky file did not load. Stated, because an almost-empty sky and a
   *  broken catalogue look identical. */
  degraded: boolean;
  truncated: boolean;
  notes: string[];
}

const EMPTY: SkyRegionState = {
  rows: [], loading: false, error: null, degraded: false,
  truncated: false, notes: [],
};

/** Trailing debounce before a settled view is fetched — the same cadence the
 *  survey loader in SkyCanvas uses, so a drag produces one request, not sixty. */
export const DEBOUNCE_MS = 220;

/**
 * The rows for the current view. Never blocks a render, never throws.
 *
 * A pan with nothing cached draws no labels and shows no spinner: an
 * unlabelled sky is the correct intermediate state, not an error. A failed
 * fetch keeps the last good rows on screen (the same keep-last-good the survey
 * image already uses) and reports the failure as a sentence for the panel
 * beside the canvas to show.
 */
export function useSkyRegion(
  center: { ra_hours: number; dec_deg: number },
  fovDeg: number,
  enabled: boolean,
  limit = 80,
): SkyRegionState {
  const [state, setState] = useState<SkyRegionState>(EMPTY);
  const cacheRef = useRef<CachedRegion | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const genRef = useRef(0);

  useEffect(() => {
    if (!enabled || !(fovDeg > 0)) return;
    if (!regionNeedsFetch(cacheRef.current, center, fovDeg, Date.now())) return;
    const timer = setTimeout(() => {
      const gen = ++genRef.current;
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      const radius = fovDeg * RADIUS_FACTOR;
      // Only the FIRST fetch is a "loading" state. A refetch during a pan
      // still has the previous region's rows on screen, and calling that
      // loading would make the card blink between "here is M31" and "looking…"
      // every couple of seconds while the user drags.
      if (cacheRef.current === null) setState((s) => ({ ...s, loading: true }));
      void (async () => {
        try {
          const res = await fetch(regionUrl(center, fovDeg, limit), { signal: ac.signal });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = (await res.json()) as RegionResponse;
          if (gen !== genRef.current) return;
          // A response with no `rows` array is not an empty sky — it is a
          // server that answered something else (the SPA catch-all serves
          // index.html for an unregistered route, which parses as neither).
          if (!Array.isArray(data?.rows)) {
            throw new Error("the sky-region endpoint answered without a row list");
          }
          cacheRef.current = {
            ra_hours: wrapRa(center.ra_hours),
            dec_deg: clampDec(center.dec_deg),
            radius_deg: radius,
            fov_deg: fovDeg,
            rows: data.rows,
            truncated: !!data.truncated,
            degraded: !!data.catalog_degraded,
            notes: Array.isArray(data.notes) ? data.notes : [],
            at: Date.now(),
            ephemeral: data.rows.some((r) => r.kind === "solar_system"),
          };
          setState({
            rows: data.rows,
            loading: false,
            error: null,
            degraded: !!data.catalog_degraded,
            truncated: !!data.truncated,
            notes: Array.isArray(data.notes) ? data.notes : [],
          });
        } catch (e) {
          if (gen !== genRef.current) return;           // superseded, not failed
          if ((e as Error)?.name === "AbortError") return;
          setState((s) => ({
            ...s,
            loading: false,
            // Keep whatever rows are already on screen. A named sky that has
            // stopped updating beats a sky that empties itself because one
            // request failed.
            error:
              `Couldn't load what's in this part of the sky (${(e as Error).message}). ` +
              `The map still works; object names will come back when the server does.`,
          }));
        }
      })();
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // center is a fresh object every render in the caller, so depend on its
    // fields — otherwise this effect re-runs (and re-debounces) forever.
  }, [enabled, center.ra_hours, center.dec_deg, fovDeg, limit]);

  useEffect(() => () => {
    genRef.current++;
    abortRef.current?.abort();
  }, []);

  return enabled ? state : EMPTY;
}
