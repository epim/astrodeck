// advection.ts - wind advection of a sky point's cloud shadow, for the sky-view
// wind arrows and the +30 min "ghost tile" overlay.
//
// Lifts the formula verbatim from the design prototype's reference logic
// (scratchpad seams/proto/logic.js, `advect(alt, az, m)`), which is itself the
// implementation of the README's "Wind advection" formula (design_handoff
// README.md, "Formulas to lift"):
//
//   ground position of a sky point at cloud-base height h:
//     r = h / tan(alt)
//     (gx, gy) = r * (sin az, cos az)
//   shift by v*t along the wind's toward-vector, then back to sky coords:
//     alt = atan(h / r')
//     az  = atan2(gx', gy')
//
// h (baseKm) and v (windKmh) are prototype defaults (2.2 km, 12 km/h) - the
// real values come from the weather feed (README: "take both from the weather
// feed"), so callers pass them in rather than this module hard-coding them.

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;

export interface WindParams {
  /** Cloud-base height, km. */
  baseKm: number;
  /** Wind speed, km/h. */
  windKmh: number;
  /** Compass bearing the wind blows TOWARD, degrees (0 = N, 90 = E). */
  windTowardDeg: number;
}

export interface AltAz {
  alt: number;
  az: number;
}

/**
 * Advect a sky point (alt/az, degrees) `minutes` forward along the wind.
 * Guards alt <= 0 by flooring the input altitude at 2 deg (matching the
 * prototype's `Math.max(2, alt)`) so a point near the horizon never divides by
 * zero; the returned altitude is always > 0 because it is an atan() of a
 * strictly positive ratio.
 */
export function advect(altDeg: number, azDeg: number, minutes: number, wind: WindParams): AltAz {
  const a = Math.max(2, altDeg) * D2R;
  const r = wind.baseKm / Math.tan(a);
  const t = wind.windTowardDeg * D2R;
  const z = azDeg * D2R;
  const dist = (wind.windKmh * minutes) / 60;
  const gx = r * Math.sin(z) + dist * Math.sin(t);
  const gy = r * Math.cos(z) + dist * Math.cos(t);
  const r2 = Math.hypot(gx, gy);
  const alt = (Math.atan(wind.baseKm / Math.max(0.05, r2)) * R2D);
  const az = (((Math.atan2(gx, gy) * R2D) % 360) + 360) % 360;
  return { alt, az };
}
