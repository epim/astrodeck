// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// horizonModel.ts - the per-site horizon polyline: altitude lookup, edit
// helpers (add/move/remove a point) and the photosphere auto-trace (README
// "12. Sites and Horizon" + "Formulas to lift" -> Horizon: "polyline of (az,
// alt) points per site, linear between points, wraps at 360; a target is
// obstructed when its alt <= horizon(az). Photosphere auto-trace: bin the
// daytime panorama into az columns, skyline = first row where luminance drops
// below sky.").
//
// `ui/src/lib/horizon.ts` (24 lines) already owns the single-altitude verdict
// (`horizonVerdict`: ok/low/below/unknown for one alt against `horizon_min_deg`)
// but has no polyline math at all - re-exported below rather than duplicated.
// Everything else here (interpolation, wrap, point editing, auto-trace) is new.
//
// `horizonAltAt`/point-editing clamps are lifted verbatim from the prototype's
// `hzAt()`/`hzDrag()` (scratchpad seams/proto/logic.js): wrap-around via a
// point appended before the first and after the last, and a moved point's
// azimuth clamped strictly between its neighbours (altitude -8..90).
//
// ASSUMPTION: `movePoint`/neighbour clamping expects `points` already sorted
// by `az` (as the prototype's stored horizon arrays are) - callers that add a
// point out of order should re-sort (see `insertPoint`, which does).
//
// `insertPoint`/`movePoint` take an optional `grid` (degrees). Left out, they
// behave exactly as they always have (whole degrees, neighbours +-1, ends at
// 0/359), which is what the classic editor and the old scanner rely on. Given,
// the panorama review editor (SPEC-v2 2.9) rounds both coordinates to the grid
// and keeps a moved point `grid` clear of its neighbours and inside
// [0, 360 - grid].

export { horizonVerdict } from "../../lib/horizon";
export type { HorizonVerdict } from "../../lib/horizon";

export interface HorizonPoint {
  az: number;
  alt: number;
}

/** Horizon altitude at `az`, linear between the two bracketing points, wrapped
 *  at 360. Empty `points` -> 0 (no obstruction known). */
export function horizonAltAt(points: HorizonPoint[], az: number): number {
  if (!points.length) return 0;
  const a = ((az % 360) + 360) % 360;
  const sorted = [...points].sort((p, q) => p.az - q.az);
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  const ext: HorizonPoint[] = [{ az: last.az - 360, alt: last.alt }, ...sorted, { az: first.az + 360, alt: first.alt }];
  for (let i = 0; i < ext.length - 1; i++) {
    const p = ext[i];
    const q = ext[i + 1];
    if (a >= p.az && a <= q.az) {
      const f = q.az === p.az ? 0 : (a - p.az) / (q.az - p.az);
      return p.alt + (q.alt - p.alt) * f;
    }
  }
  return 0;
}

/** True when `alt` at `az` is at or below the horizon line. */
export function isObstructed(points: HorizonPoint[], alt: number, az: number): boolean {
  return alt <= horizonAltAt(points, az);
}

/** `grid` must be a positive finite step; anything else is a caller bug, not an input to guess at. */
function checkGrid(grid: number): void {
  if (!(grid > 0 && Number.isFinite(grid))) throw new RangeError(`horizonModel: grid must be a positive finite number, got ${String(grid)}`);
}

/** `v` rounded to the nearest multiple of `grid` (halves up, as Math.round); the `+ 0` turns a -0 into 0. */
function onGrid(v: number, grid: number): number {
  return Math.round(v / grid) * grid + 0;
}

/** Add a point and keep the array az-sorted. The point's az/alt are rounded to whole degrees, or, with `grid`, to
 *  the grid, with az held inside [0, 360 - grid] (a point at 360 is the point at 0). */
export function insertPoint(points: HorizonPoint[], az: number, alt: number, grid?: number): HorizonPoint[] {
  if (grid !== undefined) checkGrid(grid);
  const at = grid === undefined
    ? { az: Math.round(az), alt: Math.round(alt) }
    : { az: Math.min(360 - grid, Math.max(0, onGrid(az, grid))), alt: onGrid(alt, grid) };
  const next = [...points, at];
  next.sort((p, q) => p.az - q.az);
  return next;
}

/** Move point `i`, clamping az strictly between its neighbours (or 0/359 at
 *  the ends) and alt to -8..90, including overhead obstructions, so a
 *  dragged point can never cross a neighbour or invert the polyline.
 *
 *  With `grid`, both coordinates are rounded to the grid first, and the az
 *  clamp keeps the point `grid` from each neighbour and inside
 *  [0, 360 - grid]; alt is still clamped to -8..90 and then rounded. Rounding
 *  comes before the clamp so an off-grid neighbour is never crossed. */
export function movePoint(points: HorizonPoint[], i: number, az: number, alt: number, grid?: number): HorizonPoint[] {
  if (grid !== undefined) checkGrid(grid);
  if (i < 0 || i >= points.length) return points;
  const next = points.map((p) => ({ ...p }));
  if (grid !== undefined) {
    const gLo = i > 0 ? next[i - 1].az + grid : 0;
    const gHi = i < next.length - 1 ? next[i + 1].az - grid : 360 - grid;
    next[i] = {
      az: Math.min(360 - grid, Math.max(0, Math.max(gLo, Math.min(gHi, onGrid(az, grid))))),
      alt: onGrid(Math.max(-8, Math.min(90, alt)), grid),
    };
    return next;
  }
  const lo = i > 0 ? next[i - 1].az + 1 : 0;
  const hi = i < next.length - 1 ? next[i + 1].az - 1 : 359;
  next[i] = {
    az: Math.round(Math.max(lo, Math.min(hi, az))),
    alt: Math.round(Math.max(-8, Math.min(90, alt))),
  };
  return next;
}

/** Remove point `i` (no confirm - README: "tap a point to delete (no confirm)"). */
export function removePoint(points: HorizonPoint[], i: number): HorizonPoint[] {
  if (i < 0 || i >= points.length) return points;
  return points.filter((_, idx) => idx !== i);
}

/** "13 points * up to 38 deg" summary line for the horizon editor header. */
export function summary(points: HorizonPoint[]): string {
  if (!points.length) return "0 points";
  const maxAlt = Math.round(Math.max(...points.map((p) => p.alt)));
  const noun = points.length === 1 ? "point" : "points";
  return `${points.length} ${noun} · up to ${maxAlt}°`;
}

/**
 * Photosphere auto-trace (README: "bin the daytime panorama into az columns,
 * skyline = first row where luminance drops below sky"). `columns[c][r]` is
 * the luminance of row `r` (top of frame first) in azimuth column `c`, the
 * columns spanning 0..360 evenly. `altTop`/`altBottom` map row 0 / the last
 * row to an altitude (ASSUMPTION: no source altitude range for the panorama
 * sweep; defaults to a typical handheld sweep, 90 deg down to -10 deg - flag
 * this in the report). A column with no luminance drop is treated as clear to
 * the bottom of the frame (horizon at `altBottom`).
 */
export function autoTraceSkyline(
  columns: number[][],
  skyLum: number,
  altTop = 90,
  altBottom = -10,
): HorizonPoint[] {
  if (!columns.length) return [];
  const n = columns.length;
  const points: HorizonPoint[] = [];
  for (let c = 0; c < n; c++) {
    const col = columns[c];
    if (!col || !col.length) continue;
    const rows = col.length;
    let rowIdx = rows - 1;
    for (let r = 0; r < rows; r++) {
      if (col[r] < skyLum) {
        rowIdx = r;
        break;
      }
    }
    const alt = rows > 1 ? altTop + (rowIdx / (rows - 1)) * (altBottom - altTop) : altTop;
    points.push({ az: Math.round((c / n) * 360), alt: Math.round(alt) });
  }
  return points;
}
