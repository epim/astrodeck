// zoom.ts - FRAME mode's field of view: the arithmetic, with no DOM in it.
//
// WHY THIS FILE EXISTS AT ALL. `SkyCanvas` writes `fovZoomDeg` from a wheel
// listener and the `+`/`-` KEYS (`SkyCanvas.tsx:616-628, 642-643`). A phone has
// neither, so before this module the survey field of view was fixed for the
// whole session on the only device the Sky hub is designed for (review #27).
// The legacy Atlas had four zoom controls - a stepper, "Fit object", "Match
// camera" and a recentre - and every one of them was a pure function of the
// framing session plus the optics, which is what is restored here.
//
// THE BOUNDS ARE THE ATLAS'S OWN (`SurveyControls.tsx:24-25`), not new ones:
// 0.1-10 degrees is the range `store.openFraming`'s seed is already clamped to
// (`seedFovZoomDeg`), so a stepper that allowed more would hand SkyCanvas a
// crop it refuses to draw and the screen would simply stop changing.

import type { CatalogEntry } from "../../../../types";

export const ZOOM_MIN = 0.1;
export const ZOOM_MAX = 10;

/** The multiplicative step the +/- pair takes. 1.25 rather than a fixed number
 *  of degrees: the useful range spans two orders of magnitude, and a 0.1 deg
 *  step is a third of the view at the bottom and invisible at the top. */
export const ZOOM_STEP = 1.25;

/** The padding "Fit object" and "Match camera" leave around what they frame,
 *  verbatim from `SurveyControls.fitObject` and the Atlas's FOV lock. */
export const FIT_PAD = 1.6;

export function clampZoom(v: number): number {
  if (!Number.isFinite(v)) return ZOOM_MIN;
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, v));
}

/** One press of `-` (`dir: -1`, a wider field) or `+` (`dir: 1`, tighter). */
export function zoomStep(current: number, dir: 1 | -1): number {
  const base = Number.isFinite(current) && current > 0 ? current : 1;
  return clampZoom(dir > 0 ? base / ZOOM_STEP : base * ZOOM_STEP);
}

/**
 * "Fit object": 1.6 x the catalogued extent, falling back to the camera's own
 * frame (or half a degree) for a point source - `SurveyControls.tsx:70-75`.
 *
 * A row with `size_arcmin: 0` is NOT a tiny object, it is an object nobody
 * published a size for, and zooming to 0 degrees on it would put the survey at
 * its tightest crop over something that may fill the frame.
 */
export function fitObjectZoom(
  target: Pick<CatalogEntry, "size_arcmin"> | null | undefined,
  frameFovDeg: number,
): number {
  const sizeDeg = (target?.size_arcmin ?? 0) / 60;
  if (sizeDeg > 0) return clampZoom(FIT_PAD * sizeDeg);
  return clampZoom(Math.max(frameFovDeg, 0.5));
}

/** "Match camera": the single-frame field of view with the same margin, so what
 *  is on screen is what the sensor will actually capture. Zero optics means
 *  there is no camera field to match and the caller must lock the control. */
export function matchCameraZoom(frameFovDeg: number): number {
  return clampZoom(frameFovDeg * FIT_PAD);
}

/** The bigger of the two frame axes - what both fit helpers measure against,
 *  because the survey crop is square-ish and the short axis would clip. */
export function frameFovDeg(fovXDeg: number, fovYDeg: number): number {
  return Math.max(fovXDeg > 0 ? fovXDeg : 0, fovYDeg > 0 ? fovYDeg : 0);
}

/**
 * A pinch, as a new field of view.
 *
 * Fingers moving APART (`d1 > d0`) mean "show me less sky, bigger" - the same
 * direction the wheel and the `+` key already take - so the field of view goes
 * DOWN as the distance goes up. Getting that backwards is invisible in a unit
 * test that only checks the number changed, which is why the direction is
 * asserted by name.
 *
 * A zero or non-finite start distance returns the field unchanged rather than
 * dividing by it: two pointers landing on the same pixel is an ordinary first
 * frame of a gesture, not an error.
 */
export function pinchZoom(current: number, startDist: number, nowDist: number): number {
  if (!(startDist > 0) || !(nowDist > 0)) return clampZoom(current);
  return clampZoom(current * (startDist / nowDist));
}

/** Distance between two pointer positions, for the pinch above. */
export function pointerDist(
  a: { x: number; y: number },
  b: { x: number; y: number },
): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/** The stepper's readout: "1.68°" at the tight end, "10.0°" at the wide one.
 *  Two decimals under a degree because the interesting range for a mosaic is
 *  0.3-3 degrees and "0.3°" and "0.34°" are different framings. */
export function zoomLabel(v: number): string {
  return v < 1 ? `${v.toFixed(2)}°` : `${v.toFixed(1)}°`;
}
