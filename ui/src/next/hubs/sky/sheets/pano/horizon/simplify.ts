// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The conservative polyline (SPEC-v2 5.4, D16): the line Save publishes is a
// polyline of at most 180 vertices that is never below a bin, so simplifying
// can only block more sky, never open any.
//
// Vertices sit on bin edges (0.5 degree for the 720-bin profile). The line is
// linear between vertices, as `horizonAltAt` reads it, wrapped at 360. Two
// guarantees, by construction:
//   G1  inside every bin the line is at or above the bin's value, checked at
//       both bin ends (linear between them, so the ends are enough);
//   G2  at a bin centre the line is at most tau above the bin, except on a
//       shoulder bin: a single bin between two vertices at a step edge, where
//       G2 is infeasible because both ends have to clear the taller neighbour.
//       Shoulders always err toward blocked.
//
// The algorithm is the one SPEC-v2 5.4 prototyped (a Python script, sensorfirst_simplify.py,
// kept in the build's scratch notes): 102 / 48 / 35 vertices for the chart yard at tau 0.25 / 0.5 / 1.
// Differences from it, none of which changes a chart-yard count:
//   * vertex values are capped at 90, because the server refuses a control
//     point above HORIZON_ALT_MAX_DEG (server/astrodeck/locations.py:33), and a
//     slope that is feasible for G1 and G2 can climb past it at a large tau;
//   * the closing shoulder (the last bin, which rises to the starting maximum)
//     is counted in `shoulders`, so a caller can bound G2 violations by it;
//   * a profile that needs one vertex is given a second, collinear one half a
//     turn away, so a flat horizon is still a line (as ideal.step_polyline).
import type { HorizonPoint } from '../types';

/** Tolerances to try, in order, until the polyline fits (5.4 step 4). */
export const TAU_LADDER: readonly number[] = [0.25, 0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32, 45, 90];

/** The server's bounds on a control point (HORIZON_ALT_MIN_DEG, HORIZON_ALT_MAX_DEG). */
const ALT_MIN = -10;
const ALT_MAX = 90;
/** A lifted vertex can land a few ulp past 90 (or under -10) by rounding. It is clamped; only more than this is past the cap. */
const ROUNDING = 1e-12;

interface Rung { edges: number[]; values: number[]; shoulders: number }

/**
 * One tolerance. `q` is the profile rotated to start at its highest bin; edge e is the left edge of bin e and
 * edge n is edge 0 again. Greedy: from the vertex at edge a, intersect the feasible slope interval [lo, hi] bin by
 * bin, and end the segment at the last edge where a vertex that is also at least the next bin's value is feasible.
 */
function rung(q: Float64Array, tau: number): Rung {
  const n = q.length;
  const startV = q[0];                     // the maximum, so it clears the last bin too
  const edges = [0], values = [startV];
  let a = 0, va = startV, shoulders = 0;
  for (;;) {
    let lo = -Infinity, hi = Infinity;
    let bestB = -1, bestV = 0, closed = false;
    for (let b = a + 1; b <= n; b++) {
      const i = b - 1;                     // the bin just added: [i, b)
      lo = Math.max(lo, (q[i] - va) / (b - a));                      // G1 at its right end
      if (i > a) lo = Math.max(lo, (q[i] - va) / (i - a));           // G1 at its left end
      hi = Math.min(hi, (q[i] + tau - va) / (i + 0.5 - a));          // G2 at its centre
      if (lo > hi) break;
      if (b === n) {                       // the vertex at edge n is the start, fixed at startV
        const s = (startV - va) / (n - a);
        closed = s >= lo && s <= hi;
        break;
      }
      // A vertex at edge b is also the left end of bin b: lift it to that bin's value.
      const slope = Math.max(lo, (q[b] - va) / (b - a));
      const v = va + slope * (b - a);
      if (slope <= hi && v <= ALT_MAX + ROUNDING) { bestB = b; bestV = Math.max(ALT_MIN, Math.min(ALT_MAX, v)); }
    }
    if (closed) break;
    if (bestB < 0) {
      // No feasible vertex: a shoulder. The line covers this one bin at both ends and the next bin's left edge.
      shoulders++;
      if (a + 1 === n) break;              // the last bin rises to the start, which is the maximum
      bestB = a + 1;
      bestV = Math.max(q[a], q[a + 1]);
    }
    a = bestB; va = bestV;
    edges.push(a); values.push(va);
  }
  return { edges, values, shoulders };
}

/**
 * The polyline for a 720-bin profile (or any bin count): `alt[i]` is the value of bin i, covering
 * [i * 360 / n, (i + 1) * 360 / n). Tries `TAU_LADDER` until the vertices number at most `maxPoints`. Never empty;
 * azimuths are distinct, ascending and in [0, 360); altitudes lie in the server's -10..90. A non-finite bin counts as
 * blocked (90). If even the last tolerance does not fit, the line is flat at the profile maximum, whose tolerance
 * is reported as the largest drop it hides.
 */
export function conservativePolyline(alt: Float32Array, maxPoints = 180): { points: HorizonPoint[]; tau: number; shoulders: number } {
  const n = alt.length;
  if (n === 0) {
    return { points: [{ az: 0, alt: ALT_MAX }, { az: 180, alt: ALT_MAX }], tau: TAU_LADDER[0], shoulders: 0 };
  }
  const profile = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const v = alt[i];
    profile[i] = Number.isFinite(v) ? Math.min(ALT_MAX, Math.max(ALT_MIN, v)) : ALT_MAX;
  }
  let s0 = 0;                              // rotate so the profile starts at its highest bin (the first, on a tie)
  for (let i = 1; i < n; i++) if (profile[i] > profile[s0]) s0 = i;
  const q = new Float64Array(n);
  for (let i = 0; i < n; i++) q[i] = profile[(i + s0) % n];

  const step = 360 / n;
  const build = (edges: number[], values: number[]): HorizonPoint[] => {
    const pts = edges.map((e, k) => ({ az: ((e + s0) % n) * step, alt: values[k] }));
    // A flat horizon needs one vertex; a second, on the same line, keeps it a line to draw.
    if (pts.length === 1 && maxPoints >= 2) pts.push({ az: (pts[0].az + 180) % 360, alt: pts[0].alt });
    return pts.sort((p, r) => p.az - r.az);
  };

  for (const tau of TAU_LADDER) {
    const r = rung(q, tau);
    const count = r.edges.length === 1 && maxPoints >= 2 ? 2 : r.edges.length;
    if (count <= maxPoints) return { points: build(r.edges, r.values), tau, shoulders: r.shoulders };
  }
  let lowest = q[0];
  for (let i = 1; i < n; i++) if (q[i] < lowest) lowest = q[i];
  return { points: build([0], [q[0]]), tau: q[0] - lowest, shoulders: 0 };
}
