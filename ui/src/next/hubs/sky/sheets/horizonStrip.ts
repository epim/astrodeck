// horizonStrip.ts - pure geometry for the horizon editor's unrolled 360-deg
// strip (plan A.15): `viewBox="0 0 340 150"`, azimuth across the width,
// altitude down the height, a dashed floor at 25 deg. Point EDITING
// (insert/move/remove/summary) already lives in `next/lib/horizonModel.ts`
// and is reused as-is (T-SKY-4 must not re-derive it) - this module is only
// the screen<->sky mapping horizon.tsx needs to drive that shared math from
// pointer events.

import type { HorizonPoint } from "../../../lib/horizonModel";

export const STRIP_W = 340;
export const STRIP_H = 150;
/** y of the ground baseline (alt 0). */
export const GROUND_Y = 140;
/** y of the top of the strip (alt 90). */
export const SKY_Y = 10;
/** README "12. Sites and Horizon": the floor where seeing gets poor. */
export const FLOOR_ALT_DEG = 25;
/** proto's own pointer hit radius, in the strip's own unit grid (the proto
 *  canvas is drawn 1:1 with its viewBox, so a 16px screen radius IS 16
 *  viewBox units there - kept identical here). */
export const HIT_RADIUS = 16;

/** Azimuth (degrees, any range) -> x. Deliberately NOT wrapped: the fill
 *  path extends a point to az-360/az+360 on purpose so the polyline enters
 *  and leaves the visible strip at the correct slope, and wrapping here
 *  would fold those back on top of the real data. */
export function HX(az: number): number {
  return (az / 360) * STRIP_W;
}

/** Altitude -> y. GROUND_Y at alt 0, SKY_Y at alt 90 (`HY(90) = SKY_Y`). */
export function HY(alt: number): number {
  return GROUND_Y - (alt * (GROUND_Y - SKY_Y)) / 90;
}

/** x (viewBox units) -> azimuth, wrapped into [0, 360). The one direction
 *  that DOES wrap: a pointer tap always names one true azimuth. */
export function azFromX(x: number): number {
  const az = (x / STRIP_W) * 360;
  return ((az % 360) + 360) % 360;
}

/** y (viewBox units) -> altitude, clamped -8..88 - `horizonModel.movePoint`'s
 *  own range, matched here so a tap and a drag can never disagree about what
 *  altitude a given height on the strip means. */
export function altFromY(y: number): number {
  const alt = ((GROUND_Y - y) * 90) / (GROUND_Y - SKY_Y);
  return Math.max(-8, Math.min(88, alt));
}

/** The index of the point nearest (x, y) within HIT_RADIUS, or -1 when none
 *  is close enough - the "tap a point" vs "tap the strip" fork every pointer
 *  handler starts from. */
export function hitTestPoint(points: HorizonPoint[], x: number, y: number): number {
  let best = -1;
  let bestD = HIT_RADIUS;
  for (let i = 0; i < points.length; i++) {
    const dx = HX(points[i].az) - x;
    const dy = HY(points[i].alt) - y;
    const d = Math.hypot(dx, dy);
    if (d <= bestD) {
      bestD = d;
      best = i;
    }
  }
  return best;
}

export interface ClientRectLike {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Map a client (screen) pointer position into the strip's own 340x150 unit
 *  grid, given the SVG element's rendered rect. The one DOM-touching step in
 *  this module, kept as a function of a plain rect object so a test can
 *  fabricate one without a real layout engine - jsdom's
 *  `getBoundingClientRect` is a permanent all-zero stub. */
export function toViewBox(clientX: number, clientY: number, rect: ClientRectLike): { x: number; y: number } {
  const w = rect.width || STRIP_W;
  const h = rect.height || STRIP_H;
  return {
    x: ((clientX - rect.left) / w) * STRIP_W,
    y: ((clientY - rect.top) / h) * STRIP_H,
  };
}

/** The filled horizon polygon's `d` attribute: the polyline across the
 *  visible strip, wrapped one point past each edge (as `horizonAltAt` does)
 *  so the line enters/exits at the true interpolated slope rather than
 *  snapping flat at az 0/360, closed down to the ground baseline. Empty
 *  `points` -> "" (nothing to fill; the open-horizon case draws no path). */
export function buildFillPathD(points: HorizonPoint[]): string {
  if (points.length === 0) return "";
  const sorted = [...points].sort((a, b) => a.az - b.az);
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  const ext: HorizonPoint[] = [
    { az: last.az - 360, alt: last.alt },
    ...sorted,
    { az: first.az + 360, alt: first.alt },
  ];
  const top = ext.map((p) => `${HX(p.az).toFixed(1)},${HY(p.alt).toFixed(1)}`).join(" L ");
  const lastX = HX(ext[ext.length - 1].az).toFixed(1);
  const firstX = HX(ext[0].az).toFixed(1);
  return `M ${top} L ${lastX},${GROUND_Y} L ${firstX},${GROUND_Y} Z`;
}

/** The stroke-only polyline (no fill/close) for the same points - used for
 *  the crisp horizon LINE on top of the softer fill. */
export function buildStrokePathD(points: HorizonPoint[]): string {
  if (points.length === 0) return "";
  const sorted = [...points].sort((a, b) => a.az - b.az);
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  const ext: HorizonPoint[] = [
    { az: last.az - 360, alt: last.alt },
    ...sorted,
    { az: first.az + 360, alt: first.alt },
  ];
  return `M ${ext.map((p) => `${HX(p.az).toFixed(1)},${HY(p.alt).toFixed(1)}`).join(" L ")}`;
}
