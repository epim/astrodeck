// equatorial.ts - the horizon <-> equatorial pair the finder needs.
//
// `ui/src/lib/altaz.ts` already owns the FORWARD direction (RA/Dec -> alt/az)
// and is a line-for-line port of the server's `catalog/coords.py`. It is used
// as-is; nothing here re-derives it. What it does not have is the INVERSE
// (the reticle is aimed at a patch of sky and IMAGE THIS PATCH has to say which
// coordinates that is) or the hour-angle stepper the to-dawn track walks. Those
// two are the prototype's `eq()` and `hz()` (proto/logic.js:131-132), transcribed
// here.
//
// NOT FOR POINTING - the same boundary altaz.ts states. Good to about a second
// of sidereal time: fine for "which way is that", nowhere near good enough to
// slew on. Every mount command in this hub travels as a target's catalogued
// RA/Dec, never as a screen alt/az.
//
// RA WRAPPING goes through `lib/framing.ts`'s `wrapRaHours` and not a local
// `% 24`: `Target.ra_hours` is `Field(ge=0, lt=24)` server-side, and a value that
// rounds up to exactly 24.0 422s the whole plan. That fold-back is the entire
// reason the shared helper exists.

import { lstHours } from "../../../../lib/altaz";
import { wrapRaHours } from "../../../../lib/framing";

export { wrapRaHours };

export const D2R = Math.PI / 180;
export const R2D = 180 / Math.PI;

/** Degrees of hour angle per hour of clock time - the sidereal rate the track
 *  steps at (proto/logic.js:485). 15.041, not 15: the sky gains ~4 minutes a
 *  day on the clock, which is 10 arcminutes over a seven-hour night. */
export const SIDEREAL_DEG_PER_HOUR = 15.041;

const clamp1 = (v: number): number => Math.max(-1, Math.min(1, v));

export interface HourAngle {
  /** Declination, RADIANS. */
  decRad: number;
  /** Hour angle, RADIANS. Negative east of the meridian. */
  haRad: number;
}

/**
 * alt/az (degrees) -> declination + hour angle (radians), at latitude `latDeg`.
 * Verbatim from the prototype's `eq()`.
 */
export function eq(altDeg: number, azDeg: number, latDeg: number): HourAngle {
  const f = latDeg * D2R;
  const a = altDeg * D2R;
  const A = azDeg * D2R;
  const decRad = Math.asin(clamp1(Math.sin(a) * Math.sin(f) + Math.cos(a) * Math.cos(f) * Math.cos(A)));
  let haRad = Math.acos(
    clamp1((Math.sin(a) - Math.sin(f) * Math.sin(decRad)) / (Math.cos(f) * Math.cos(decRad))),
  );
  // acos() cannot tell east from west; the azimuth's own hemisphere can.
  if (Math.sin(A) > 0) haRad = -haRad;
  return { decRad, haRad };
}

export interface AltAzDeg {
  alt: number;
  az: number;
}

/**
 * declination + hour angle (radians) -> alt/az (degrees). The forward twin of
 * `eq()`, verbatim from the prototype's `hz()`. This is what advances a target
 * along its arc: step `haRad` by `t * SIDEREAL_DEG_PER_HOUR * D2R` and read the
 * altitude back out.
 */
export function hz(decRad: number, haRad: number, latDeg: number): AltAzDeg {
  const f = latDeg * D2R;
  const alt = Math.asin(clamp1(Math.sin(f) * Math.sin(decRad) + Math.cos(f) * Math.cos(decRad) * Math.cos(haRad)));
  let az = Math.acos(
    clamp1((Math.sin(decRad) - Math.sin(f) * Math.sin(alt)) / (Math.cos(f) * Math.cos(alt))),
  );
  if (Math.sin(haRad) > 0) az = 2 * Math.PI - az;
  return { alt: alt * R2D, az: az * R2D };
}

export interface RaDec {
  ra_hours: number;
  dec_deg: number;
}

/** Hour angle (radians) -> RA hours at a site and instant, wrapped into [0,24). */
export function raFromHourAngle(haRad: number, lonDeg: number, unixTime: number): number {
  return wrapRaHours(lstHours(lonDeg, unixTime) - (haRad * R2D) / 15);
}

/**
 * Where the reticle is pointing, in catalogue coordinates. The inverse of
 * `altAzOf`, and the only path by which a patch of empty sky acquires an RA and
 * a Dec (IMAGE THIS PATCH, and the coordinates sheet's USE FINDER button).
 */
export function raDecFromAltAz(
  altDeg: number,
  azDeg: number,
  latDeg: number,
  lonDeg: number,
  unixTime: number,
): RaDec {
  const { decRad, haRad } = eq(altDeg, azDeg, latDeg);
  return {
    ra_hours: raFromHourAngle(haRad, lonDeg, unixTime),
    dec_deg: decRad * R2D,
  };
}

/** "22h 57m 54s" - the sexagesimal form the flow payload and the report carry. */
export function raHmsStr(hours: number): string {
  const h = wrapRaHours(hours);
  const totalSec = Math.round(h * 3600);
  const hh = Math.floor(totalSec / 3600) % 24;
  const mm = Math.floor((totalSec % 3600) / 60);
  const ss = totalSec % 60;
  return `${String(hh).padStart(2, "0")}h ${String(mm).padStart(2, "0")}m ${String(ss).padStart(2, "0")}s`;
}

/** "+62° 37′ 06″" - a real prime and double-prime, matching the coordinates
 *  sheet's placeholder and the server's own parser. */
export function decDmsStr(deg: number): string {
  const sign = deg < 0 ? "-" : "+";
  const totalSec = Math.round(Math.abs(deg) * 3600);
  const dd = Math.floor(totalSec / 3600);
  const mm = Math.floor((totalSec % 3600) / 60);
  const ss = totalSec % 60;
  return `${sign}${String(dd).padStart(2, "0")}° ${String(mm).padStart(2, "0")}′ ${String(ss).padStart(2, "0")}″`;
}
