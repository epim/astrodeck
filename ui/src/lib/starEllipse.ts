// starEllipse.ts — pure geometry for elongation-oriented star markers.
//
// The overlay encodes HFR as color + stroke-style; elongation (ecc) is encoded
// as SHAPE — an ellipse whose major axis keeps the circle's radius and whose
// minor axis shrinks by the aspect ratio b/a = sqrt(1 - ecc^2), rotated by the
// second-moment position angle theta (radians → degrees for SVG rotate()).
// Returns null when no ecc is present (Pass-1 / untrusted star) so the caller
// falls back to the plain <circle>.

export interface EllipseGeom {
  rx: number;
  ry: number;
  rotDeg: number;
}

export function ellipseGeom(
  rCircle: number,
  ecc: number | undefined,
  theta: number | undefined,
): EllipseGeom | null {
  if (ecc == null) return null;
  const e = Math.min(1, Math.max(0, ecc));
  const aspect = Math.sqrt(1 - e * e); // minor/major = b/a
  return { rx: rCircle, ry: rCircle * aspect, rotDeg: ((theta ?? 0) * 180) / Math.PI };
}
