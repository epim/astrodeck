// surveyView — pure math mapping the last-fetched survey frame onto the live
// Atlas view (Wave-1 spec §1.2). NO React, no DOM: importable by the npx-tsx
// assert tests (rotatorDial.ts precedent).
//
// The <img> shows a TAN cutout centered at `shown` (geometry from the server's
// X-Survey-* headers — snapped, so it differs from the request by <= fov/40).
// While the user pans/zooms, the live view center `view` drifts away from
// `shown`; this transform slides/scales the existing frame so the sky tracks
// the pointer with ZERO fetches until the settled fetch swaps a new frame in.
//
// Screen convention (matches SkyCanvas panTo): North up, East LEFT — a sky
// point at (xi, eta) degrees from center renders at
//   (centerPx - xi*pxPerDeg, centerPx - eta*pxPerDeg).
// To bring `view`'s center (offset (xi,eta) from `shown`'s center) to the
// canvas center, the frame translates by (+xi, +eta) * pxPerDeg-of-the-VIEW —
// the scale factor folds in because CSS applies scale() about center first.

import { project } from "./framing";

export interface SurveyGeom {
  raDeg: number;   // frame center RA, DEGREES (header is degrees; /15 for project)
  decDeg: number;
  fovDeg: number;  // frame angular width
}

export function surveyTransform(
  shown: SurveyGeom,
  view: SurveyGeom,
  renderedWidthPx: number,
): { dx: number; dy: number; scale: number } {
  // Exact TAN offsets of the view center relative to the shown frame's tangent
  // point — project() handles RA wrap and cos-dec inherently.
  const { xi, eta } = project(
    view.raDeg / 15, view.decDeg,
    shown.raDeg / 15, shown.decDeg,
  );
  const pxPerDeg = renderedWidthPx / view.fovDeg;
  return {
    dx: xi * pxPerDeg,
    dy: eta * pxPerDeg,
    scale: shown.fovDeg / view.fovDeg,
  };
}
