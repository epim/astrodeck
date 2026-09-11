// aim.ts - sky <-> canvas pixels for ATLAS mode, in both directions.
//
// ONE PROJECTION, NOT A SECOND ONE. `skyToBox` is `lib/atlasFov.ts`'s
// `skyToView` with the viewBox scale already folded out, and `boxToSky` is its
// exact inverse over the same gnomonic (`lib/framing.ts`'s `project` /
// `deproject`). That is the same TAN the survey tiles, the planned FOV box, the
// catalogue markers and the live pointing footprint are all drawn with, so a
// target pill is glued to the pixels of the object it names and a tap lands on
// the sky that was under the finger - through every pan and zoom.
//
// WHY THE VIEWBOX CONSTANT IS NOT HERE. `SkyCanvas` draws into a 1000-unit
// square viewBox and scales it to the box's CSS px; both scalings cancel:
//
//   x_view = view/2 - xi * (view / fov)      (skyToView)
//   x_css  = x_view * box / view = box * (0.5 - xi / fov)
//
// so the arithmetic below never needs to know that the number is 1000. A copy
// of that constant here would be one more thing to keep in step with a file
// this area does not own.
//
// UNITS: `xi`/`eta` are DEGREES of standard coordinate (that is what
// `project`/`deproject` speak), `fovDeg` is the canvas's field width, `boxPx`
// is the square's CSS edge, and the returned x/y are CSS px from its top-left.

import { project, deproject } from "../../../../lib/framing";
import { angularSepDeg, TAN_HORIZON_DEG } from "../../../../lib/atlasFov";

export interface SkyPoint {
  ra_hours: number;
  dec_deg: number;
}

export interface BoxPoint {
  x: number;
  y: number;
}

/**
 * Where a sky position lands on the canvas, or null when it has no place on it.
 *
 * Null is the FAR HEMISPHERE, not "off the left edge": a gnomonic has no image
 * for a point more than 90 degrees from its tangent point, and past that it
 * folds the other half of the sky back MIRRORED onto this one. A marker drawn
 * there would be a confident label on the wrong patch of sky, which is worse
 * than no marker - so it is refused rather than clamped. Points that merely
 * fall outside the square come back with real coordinates; clipping them is the
 * caller's job, because a label whose anchor is just off-frame still matters.
 */
export function skyToBox(
  p: SkyPoint,
  centre: SkyPoint,
  fovDeg: number,
  boxPx: number,
): BoxPoint | null {
  if (!(fovDeg > 0) || !(boxPx > 0)) return null;
  const sep = angularSepDeg(p, centre);
  if (!(sep < TAN_HORIZON_DEG)) return null; // !( < ) also catches NaN
  const { xi, eta } = project(p.ra_hours, p.dec_deg, centre.ra_hours, centre.dec_deg);
  return { x: boxPx * (0.5 - xi / fovDeg), y: boxPx * (0.5 - eta / fovDeg) };
}

/**
 * The sky under a point on the canvas - the exact inverse of `skyToBox`.
 *
 * This is what "aim anywhere" is made of: a tap that hit no catalogued object
 * still hit a real RA and Dec, and that pair is the whole answer to "I want to
 * look at this patch because nobody has". No guard here for the far hemisphere,
 * because there is no such point: every pixel inside the square is inside the
 * projection by construction.
 */
export function boxToSky(
  at: BoxPoint,
  centre: SkyPoint,
  fovDeg: number,
  boxPx: number,
): SkyPoint {
  const xi = (0.5 - at.x / boxPx) * fovDeg;
  const eta = (0.5 - at.y / boxPx) * fovDeg;
  return deproject(xi, eta, centre.ra_hours, centre.dec_deg);
}
