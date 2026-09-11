// wind.ts - which way the cloud is going, drawn as drift on the screen
// (hub-sky plan B.9).
//
// THE CONVENTION TRAP. `weather.now.wind_dir_deg` is METEOROLOGICAL: the
// direction the wind comes FROM. `advect()` wants a bearing the wind blows
// TOWARD. Getting that backwards points every arrow the wrong way and looks
// entirely plausible on screen - there is no visual tell. The conversion happens
// once, here, and the server's own field comment ("10 m wind direction, degrees,
// METEOROLOGICAL convention: where the wind comes FROM. Turn it around before
// drawing an arrow that points downwind." - types.ts WeatherSurface) is what
// pins it.
//
// MEASURED BEATS FORECAST. `GET /api/cloudmap` carries `motion: {speed_kmh,
// toward_deg}` derived by correlating two satellite granules - that is the cloud
// field's OWN drift, already in toward-convention, and it is what the ghost tiles
// are computed from. When it exists it wins, and the pill says `measured` so the
// number on screen is attributable.

import { advect, type WindParams } from "../../../lib/advection";
import type { CloudMotion } from "../../../../api/cloudmap";
import type { WeatherNow } from "../../../../types";
import type { Projector } from "./projection";
import type { Marker } from "./targets";
import type { CloudTile } from "../../../lib/cloudTiles";
import { fmtClock } from "../../../lib/format";

/** The prototype's cloud base, used only when neither the feed nor the
 *  temperature/dew-point spread can say (proto/logic.js:79). */
export const FALLBACK_BASE_KM = 2.2;

const D2R = Math.PI / 180;

const POINTS = [
  "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
];

/** 16-point compass name for a bearing. */
export function compassPoint(deg: number): string {
  const d = ((deg % 360) + 360) % 360;
  return POINTS[Math.round(d / 22.5) % 16];
}

export interface WindModel {
  wind: WindParams;
  /** True when the numbers came from the cloud field's own correlated motion. */
  measured: boolean;
  kmh: number;
  towardDeg: number;
  /** Where the cloud-base height came from, for the layers note. */
  baseSource: "feed" | "spread" | "fallback";
}

/**
 * Cloud base in km. The feed's own value first; then the lifting-condensation
 * approximation (125 m per degree of temperature/dew-point spread), which is the
 * same estimate the server falls back to; then the prototype's constant. A
 * negative or zero spread means saturated air at the surface - fog, not a base -
 * so it is floored rather than allowed to put the deck underground.
 */
export function cloudBaseKm(now: WeatherNow | null | undefined): {
  km: number;
  source: "feed" | "spread" | "fallback";
} {
  const m = now?.cloud_base_m;
  if (typeof m === "number" && Number.isFinite(m) && m > 0) {
    return { km: Math.max(0.1, m / 1000), source: "feed" };
  }
  const t = now?.temp_c;
  const td = now?.dewpoint_c;
  if (typeof t === "number" && typeof td === "number" && Number.isFinite(t) && Number.isFinite(td)) {
    const km = (125 * (t - td)) / 1000;
    if (km > 0.05) return { km, source: "spread" };
  }
  return { km: FALLBACK_BASE_KM, source: "fallback" };
}

/**
 * The wind the arrows are drawn from, or `null` when nothing in the feed says.
 * Null hides the arrows AND the pill - a wind field drawn from a default is a
 * picture of nothing.
 */
export function windFrom(
  now: WeatherNow | null | undefined,
  motion: CloudMotion | null | undefined,
): WindModel | null {
  const base = cloudBaseKm(now);
  if (motion && Number.isFinite(motion.speed_kmh) && Number.isFinite(motion.toward_deg)) {
    return {
      wind: { baseKm: base.km, windKmh: motion.speed_kmh, windTowardDeg: motion.toward_deg },
      measured: true,
      kmh: motion.speed_kmh,
      towardDeg: ((motion.toward_deg % 360) + 360) % 360,
      baseSource: base.source,
    };
  }
  const kmh = now?.wind_kmh;
  const from = now?.wind_dir_deg;
  if (typeof kmh !== "number" || !Number.isFinite(kmh)) return null;
  if (typeof from !== "number" || !Number.isFinite(from)) return null;
  // FROM -> TOWARD. See the module header.
  const towardDeg = ((from + 180) % 360 + 360) % 360;
  return {
    wind: { baseKm: base.km, windKmh: kmh, windTowardDeg: towardDeg },
    measured: false,
    kmh,
    towardDeg,
    baseSource: base.source,
  };
}

export interface WindArrow {
  key: string;
  x: number;
  y: number;
  /** Screen rotation, degrees, applied to the design's `M-9 0H9M4 -4l5 4-5 4`. */
  rot: number;
  /** 0.35 near the horizon, 0.6 higher - a low arrow is a longer air path and a
   *  less certain drift. */
  op: number;
}

/**
 * The arrow grid (proto/logic.js:546). Every arrow is a REAL four-minute
 * advection of the sky point under it, projected and measured - not a copy of one
 * bearing stamped across the box. Near the horizon the same wind produces far
 * more screen motion, and the grid shows that.
 *
 * Skips: the two round buttons top-right, the reticle in the middle, and any cell
 * sitting on a marker's label.
 */
export function windArrows(
  p: Projector,
  wind: WindParams,
  markers: Marker[],
  frameOn = false,
): WindArrow[] {
  if (frameOn) return [];
  const out: WindArrow[] = [];
  const { W, H } = p;
  for (let gy = 78; gy < H - 20; gy += 52) {
    for (let gx = 30 + ((gy / 52) % 2) * 26; gx < W - 20; gx += 52) {
      if (gx > W - 70 && gy < 145) continue; // clear of the funnel + layers buttons
      if (Math.abs(gx - W / 2) < 60 && Math.abs(gy - H / 2) < 60) continue; // clear of the reticle
      if (markers.some((t) => Math.abs(t.x - gx) < 34 && gy - t.y > -14 && gy - t.y < 34)) continue;
      const { az, alt } = p.unproj(gx, gy);
      if (alt < 3 || alt > 88) continue;
      const e = advect(alt, az, 4, wind);
      const p1 = p.proj(e.az, e.alt);
      const rot = Math.atan2(p1.y - gy, p1.x - gx) / D2R;
      out.push({ key: `${gx}:${gy}`, x: gx, y: gy, rot, op: alt < 12 ? 0.35 : 0.6 });
    }
  }
  return out;
}

/** How long until a cloudy tile drifts over the zenith, or `null` when none does
 *  inside the search horizon. This is the one number in the wind pill that says
 *  something the arrows do not: when the sky overhead stops being usable. */
export function minutesToDeckAtZenith(
  tiles: CloudTile[],
  wind: WindParams,
  thresholdPct = 40,
  horizonMinutes = 180,
): number | null {
  const cloudy = tiles.filter((t) => t.pct >= thresholdPct);
  if (cloudy.length === 0) return null;
  for (let m = 5; m <= horizonMinutes; m += 5) {
    for (const t of cloudy) {
      const moved = advect(t.alt0 + 3, t.az0 + 3, m, wind);
      if (moved.alt >= 80) return m;
    }
  }
  return null;
}

/**
 * The pill above the arrows. Verbatim shape from the README
 * (`wind 12 km/h → NE · deck at zenith ~23:49`), with the measured variant the
 * plan asks for. The trailing clause is dropped rather than faked when no tile
 * is heading for the zenith.
 */
export function windLine(
  model: WindModel,
  tiles: CloudTile[],
  nowMs: number,
): string {
  const head = model.measured
    ? `drift ${Math.round(model.kmh)} km/h → ${compassPoint(model.towardDeg)} · measured`
    : `wind ${Math.round(model.kmh)} km/h → ${compassPoint(model.towardDeg)}`;
  const mins = minutesToDeckAtZenith(tiles, model.wind);
  if (mins == null) return head;
  const at = nowMs + mins * 60_000;
  return `${head} · deck at zenith ~${fmtClock(at, at)}`;
}
