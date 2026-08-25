// domeProjection.ts — the sky hemisphere as a dome you look at from outside and
// slightly above, plus the occlusion colour ramp. Pure: no React, no canvas, no
// `window`, so the geometry is unit-tested without a browser.
//
// WHY A TILTED ORTHOGRAPHIC DOME rather than the usual zenith-down all-sky
// circle. Both show the same hemisphere. The circle is denser and every
// planetarium draws one, but it flattens the thing the operator is actually
// reasoning about: a cloud bank at 20 degrees altitude in the north-west is a
// wall standing between the scope and the sky, and on a zenith-down plot it is
// an arc near the rim that reads as "far away" rather than "in the way". The
// tilted dome keeps altitude vertical on screen, which is the axis that decides
// whether a target is behind weather.

/** A point on the unit sphere in the observer's local frame. */
export interface SkyVec { x: number; y: number; z: number }

/** Where a sky direction lands on the canvas, and whether it faces us. */
export interface DomePoint {
  x: number;
  y: number;
  /** Depth along the view axis. Positive = the near half of the dome. */
  depth: number;
  /** False for the far side, which the near side draws over. */
  facing: boolean;
}

/**
 * Camera elevation above the horizon plane, in degrees.
 *
 * 32 is a compromise found by drawing it: at 0 the dome collapses to a
 * semicircle and the horizon has no width, so azimuth is unreadable; past ~50
 * it turns back into the zenith-down circle this projection exists to avoid,
 * and low-altitude cloud — the kind that actually blocks a target — gets
 * squeezed into a thin rim.
 */
export const DOME_TILT_DEG = 32;

/** alt/az (degrees, az from North increasing East) to a unit vector.
 *  +y is North, +x is East, +z is up. */
export function skyVector(altDeg: number, azDeg: number): SkyVec {
  const alt = (altDeg * Math.PI) / 180;
  const az = (azDeg * Math.PI) / 180;
  const c = Math.cos(alt);
  return { x: c * Math.sin(az), y: c * Math.cos(az), z: Math.sin(alt) };
}

/**
 * Project a sky direction onto the canvas.
 *
 * Orthographic, not perspective: the dome is a coordinate display, and
 * perspective would make identical cloud cover look denser at the rim than
 * overhead purely because of where the camera is. `cx`,`cy` is the horizon
 * centre and `r` the horizon radius in px.
 *
 * The camera looks from due SOUTH and above, so North is at the BACK of the
 * dome — the same orientation as standing at the eyepiece looking north, which
 * is how the mount's own azimuth is quoted.
 */
export function projectDome(v: SkyVec, cx: number, cy: number, r: number,
                            tiltDeg: number = DOME_TILT_DEG): DomePoint {
  const t = (tiltDeg * Math.PI) / 180;
  const cosT = Math.cos(t);
  const sinT = Math.sin(t);
  // ONE camera basis, so screen position and depth cannot disagree -- deriving
  // them separately is how the first version put North at the BOTTOM while its
  // depth term still called North the far side.
  //
  // Camera sits due south and `tiltDeg` above the horizon, looking at the
  // observer. right = +x (East), up = (0, sinT, cosT), view = (0, cosT, -sinT).
  const upDot = v.y * sinT + v.z * cosT;        // screen "up" component
  const nearDot = -v.y * cosT + v.z * sinT;     // >0 faces the camera
  return {
    x: cx + v.x * r,
    y: cy - upDot * r,                          // canvas y grows downward
    depth: nearDot,
    // The visible surface of a hemisphere seen from outside is where the
    // outward normal (which IS the sky vector) points at the camera. The
    // zenith always qualifies; the northern horizon never does.
    facing: nearDot > 0,
  };
}

/** Convenience: alt/az straight to canvas. */
export function projectAltAz(altDeg: number, azDeg: number, cx: number,
                             cy: number, r: number,
                             tiltDeg: number = DOME_TILT_DEG): DomePoint {
  return projectDome(skyVector(altDeg, azDeg), cx, cy, r, tiltDeg);
}

/**
 * Occlusion probability to a colour.
 *
 * NOT a rainbow. The question is "can I image through this", which is
 * one-dimensional, so the ramp is one hue getting denser. Clear sky is left
 * nearly transparent so the dome's own grid and the pointing marker stay
 * readable through it — the operator looks at this to find the BAD patches.
 *
 * The stops are perceptual rather than linear: anything under a few percent is
 * observationally clear and should not draw the eye, and everything past ~60%
 * is "not imaging through that" and need not be distinguished further.
 */
export function occlusionFill(p: number | null | undefined): string {
  if (typeof p !== "number" || !Number.isFinite(p) || p < 0) {
    return "rgba(120,130,150,0.10)";          // no data: faint neutral, not clear
  }
  const q = Math.min(1, p);
  if (q < 0.02) return "rgba(90,190,255,0.05)";
  if (q < 0.10) return `rgba(120,180,230,${(0.10 + q * 1.2).toFixed(3)})`;
  if (q < 0.35) return `rgba(190,190,205,${(0.24 + q * 0.7).toFixed(3)})`;
  if (q < 0.60) return `rgba(225,215,205,${(0.40 + q * 0.5).toFixed(3)})`;
  return `rgba(245,235,225,${Math.min(0.88, 0.55 + q * 0.35).toFixed(3)})`;
}

/** The dome grid the /api/cloudmap/dome payload describes. */
export interface DomeGrid {
  rows: (number | null)[][];
  alt_start: number;
  alt_step: number;
  az_step: number;
}

/** One cell of the grid, as the four sky corners it spans. */
export interface DomeCell {
  altLo: number; altHi: number;
  azLo: number; azHi: number;
  p: number | null;
}

/**
 * Walk a dome payload into drawable cells.
 *
 * The payload is rows of azimuth samples, `rows[i]` at altitude
 * `alt_start + i * alt_step`. Those are SAMPLE CENTRES, not cell corners, so
 * each cell is drawn half a step either side — otherwise the whole dome is
 * offset by half a cell and the lowest row hangs below the horizon, which is
 * the kind of error that looks plausible and quietly misplaces a cloud bank by
 * a few degrees.
 */
export function domeCells(grid: DomeGrid): DomeCell[] {
  const out: DomeCell[] = [];
  const dAlt = grid.alt_step / 2;
  const dAz = grid.az_step / 2;
  grid.rows.forEach((row, i) => {
    const alt = grid.alt_start + i * grid.alt_step;
    row.forEach((p, j) => {
      const az = j * grid.az_step;
      out.push({
        altLo: Math.max(0, alt - dAlt),
        altHi: Math.min(90, alt + dAlt),
        azLo: az - dAz,
        azHi: az + dAz,
        p: typeof p === "number" ? p : null,
      });
    });
  });
  return out;
}

/** Worst occlusion anywhere on the dome, for the panel's one-line summary. */
export function domePeak(grid: DomeGrid): number | null {
  let peak: number | null = null;
  for (const row of grid.rows) {
    for (const p of row) {
      if (typeof p === "number" && Number.isFinite(p) && (peak === null || p > peak)) {
        peak = p;
      }
    }
  }
  return peak;
}

/**
 * How the operator should read a probability, in words.
 *
 * The numbers coming back are small even under real cloud (the mask is a
 * probability per 2 km cell, not a yes/no), so a bare "0.22" invites the wrong
 * conclusion in both directions. These thresholds match `occlusionFill`'s
 * stops so the legend and the picture cannot disagree.
 */
export function occlusionWord(p: number | null | undefined): string {
  if (typeof p !== "number" || !Number.isFinite(p)) return "no data";
  if (p < 0.02) return "clear";
  if (p < 0.10) return "thin";
  if (p < 0.35) return "patchy";
  if (p < 0.60) return "cloudy";
  return "socked in";
}
