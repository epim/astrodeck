// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// domeScope.ts - the telescope glyph at the middle of the dome, pointing where
// the mount points (issue #67).
//
// ONE TAPERED SHAPE, AND NOTHING ELSE.
//
// It has had a tripod, a mount head, a dew shield and a focuser stub. Each was
// added to make the thing more recognisable and each made it busier: at the
// 280-400 px this dome occupies on a phone, the model came out as a handful of
// grey rectangles in a tangle, and on the low-south panel you could not tell
// what you were looking at.
//
// So the parts are gone and the silhouette does the work. A shape that is
// narrow at one end and wide at the other reads as a telescope AND as a
// direction in the same stroke - the wide end is the objective, the objective
// is where it is looking. Nothing else here has to carry that.
//
// The bearing is the projected line from the dome's centre to the reticle: the
// same line the reader's eye follows to the cross, taken from the dome's own
// projection rather than from a second copy of its camera.
//
// The length is CONSTANT. An honestly-3D tube foreshortens, and this projection
// is orthographic with the camera due south, so anything pointed low in the
// south pointed at the camera and collapsed to a stub. That is correct for a
// real object and useless for an indicator, so the glyph keeps the quantity a
// reader uses - the bearing - and gives up the one they cannot, its apparent
// length.

import { projectAltAz } from "./domeProjection";

/** Glyph length, as a fraction of the dome radius. */
export const TUBE_SCREEN = 0.30;
/** Half-width at the eyepiece end and at the objective, in units of the length.
 *  The RATIO is what carries the direction, and it has to be unmistakable at
 *  40 px: at 1.8 the shape reads as a slightly wonky bar and you have to look
 *  twice to see which end is the objective. At about 3.2 the taper is the first
 *  thing you see, and the shape still reads as a tube rather than as a dart -
 *  a dart would be a second reticle, and there is already one. */
const R_BACK = 0.048;
const R_FRONT = 0.155;
/** How far behind the dome's centre the narrow end starts, so the glyph is
 *  balanced on the centre rather than growing out of it. */
const BACK = -0.30;

export interface ScopeColors {
  body: string;
  line: string;
}

export const SCOPE_COLORS: ScopeColors = {
  body: "rgba(226,232,242,0.97)",
  line: "rgba(8,10,16,0.9)",
};

type P2 = { x: number; y: number };

/**
 * The glyph's outline, as screen points.
 *
 * `at` is where it is hung - the dome's centre - `(ux, uy)` is a unit screen
 * direction toward the reticle, and `len` is the length in px.
 *
 * Pure, so a test can check the wide end really is the end that points at the
 * target without going near a canvas.
 */
export function tubeOutline(at: P2, ux: number, uy: number, len: number): P2[] {
  const vx = -uy, vy = ux;                       // across the glyph
  const p = (a: number, b: number): P2 =>
    ({ x: at.x + (ux * a + vx * b) * len, y: at.y + (uy * a + vy * b) * len });
  return [
    p(BACK, R_BACK), p(1, R_FRONT), p(1, -R_FRONT), p(BACK, -R_BACK),
  ];
}

/**
 * Draw the glyph at the dome's centre, aimed at `altDeg`/`azDeg`.
 *
 * `cx`,`cy`,`r`,`tiltDeg`,`yawDeg` are the panel's own, unchanged: the bearing
 * comes from the dome's projection, so the glyph turns with the dome because
 * the reticle does.
 */
export function drawDomeScope(
  ctx: CanvasRenderingContext2D,
  altDeg: number, azDeg: number,
  cx: number, cy: number, r: number,
  tiltDeg: number, yawDeg: number,
  colors: ScopeColors = SCOPE_COLORS,
): void {
  const aim = projectAltAz(altDeg, azDeg, cx, cy, r, tiltDeg, yawDeg);
  let dx = aim.x - cx, dy = aim.y - cy;
  const m = Math.hypot(dx, dy);
  // THE ONE DIRECTION WITH NO BEARING ON SCREEN, and it is not the zenith.
  // `upDot` is `y*sin(t) + z*cos(t)`, so the sky point that lands on the dome's
  // CENTRE is the one with `upDot == 0` - due south at altitude == the camera
  // tilt. The zenith projects a full `cos(t)` above centre and has a perfectly
  // good bearing. The projection hits that point exactly, so this is a real
  // 0/0 and not a near-miss.
  if (m < 1e-6) { dx = 0; dy = -1; } else { dx /= m; dy /= m; }

  const pts = tubeOutline({ x: cx, y: cy }, dx, dy, r * TUBE_SCREEN);
  ctx.save();
  ctx.lineJoin = "round";
  ctx.beginPath();
  pts.forEach((q, i) => (i === 0 ? ctx.moveTo(q.x, q.y) : ctx.lineTo(q.x, q.y)));
  ctx.closePath();
  ctx.fillStyle = colors.body;
  ctx.fill();
  ctx.lineWidth = 1.2;
  ctx.strokeStyle = colors.line;
  ctx.stroke();
  ctx.restore();
}
