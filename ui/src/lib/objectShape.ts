// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// objectShape.ts - the ellipse of a catalogue object (#181): its shape, how far
// it reaches along two axes, and what FIT OBJECT shows of it. Pure. The Target
// modal's SUGGEST GRID (`framingModel.suggestGridFor`) and FIT OBJECT read it,
// and so does the Atlas canvas that draws the ellipse.

import type { CatalogEntry } from "../types";

export interface ObjectShape {
  major_arcmin: number;
  minor_arcmin: number;
  /** Degrees east of north of the major axis, or null when it is not known. */
  pa_deg: number | null;
}

/** The ellipse a row describes, or null when it describes a circle (no minor
 *  axis, or one that is not shorter than the major). The major axis is the
 *  row's own `size_arcmin`, the size printed beside it. */
export function objectShape(t: CatalogEntry): ObjectShape | null {
  const major = t.size_arcmin;
  const minor = t.minor_arcmin;
  if (!(major > 0) || typeof minor !== "number" || !(minor > 0) || !(minor < major)) return null;
  const pa = typeof t.pa_deg === "number" && Number.isFinite(t.pa_deg) ? t.pa_deg : null;
  return { major_arcmin: major, minor_arcmin: minor, pa_deg: pa };
}

/** How far an ellipse reaches along two perpendicular axes, `turnDeg` being the
 *  angle from the first axis to the major axis: an ellipse with axes a and b
 *  (full lengths) projects onto a line at angle d from its major axis as
 *  sqrt((a cos d)^2 + (b sin d)^2). `along` is the first axis, `across` the
 *  second. */
export function ellipseExtents(shape: ObjectShape, turnDeg: number): { along: number; across: number } {
  const d = (turnDeg * Math.PI) / 180;
  const a = shape.major_arcmin;
  const b = shape.minor_arcmin;
  return {
    along: Math.hypot(a * Math.cos(d), b * Math.sin(d)),
    across: Math.hypot(a * Math.sin(d), b * Math.cos(d)),
  };
}

/** What FIT OBJECT must show, in arcminutes: the larger of the ellipse's
 *  north-south and east-west reach (the sky is drawn north up), or the long
 *  axis when the row has no ellipse with an angle. */
export function fitSizeArcmin(target: CatalogEntry): number {
  const shape = objectShape(target);
  if (!shape || shape.pa_deg === null) return target.size_arcmin ?? 0;
  const reach = ellipseExtents(shape, shape.pa_deg);
  return Math.max(reach.along, reach.across);
}
