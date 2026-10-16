// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The conservative polyline (SPEC-v2 5.4, D16): the line Save publishes is a
// polyline of at most 180 vertices that is never below a bin, so simplifying
// can only block more sky, never open any.
//
// Vertices sit on bin edges (0.5 degree for the 720-bin profile), except the boundary ramps of S47 (below). The line is
// linear between vertices, as `horizonAltAt` reads it, wrapped at 360. Two
// guarantees, by construction:
//   G1  inside every bin the line is at or above the bin's value, checked at
//       both bin ends (linear between them, so the ends are enough);
//   G2  at a bin centre the line is at most tau above the bin, except on a
//       shoulder bin: a single bin between two vertices at a step edge, where
//       G2 is infeasible because both ends have to clear the taller neighbour.
//       Shoulders always err toward blocked.
//
// S47: where a Measured bin meets one that is not, the line ramps over 0.01 degree inside the lower bin, not over
// the whole Measured bin, so a conservative line does not hide the measurement beside a blocked neighbour.
//
// The algorithm is the one SPEC-v2 5.4 prototyped: 100 / 49 / 35 vertices for the chart yard fixture (the
// Float32 draft) at tau 0.25 / 0.5 / 1.
// Differences from it, none of which changes a chart-yard count:
//   * vertex values are capped at 90, because the server refuses a control
//     point above HORIZON_ALT_MAX_DEG (server/astrodeck/locations.py:33), and a
//     slope that is feasible for G1 and G2 can climb past it at a large tau;
//   * the closing shoulder (the last bin, which rises to the starting maximum)
//     is counted in `shoulders`, so a caller can bound G2 violations by it;
//   * a profile that needs one vertex is given a second, collinear one half a
//     turn away, so a flat horizon is still a line (as ideal.step_polyline).
import { BinState } from '../types';
import type { HorizonPoint } from '../types';

/** Tolerances to try, in order, until the polyline fits (5.4 step 4). */
export const TAU_LADDER: readonly number[] = [0.25, 0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32, 45, 90];

/** The server's bounds on a control point (HORIZON_ALT_MIN_DEG, HORIZON_ALT_MAX_DEG). */
const ALT_MIN = -10;
const ALT_MAX = 90;
/** A lifted vertex can land a few ulp past 90 (or under -10) by rounding. It is clamped; only more than this is past the cap. */
const ROUNDING = 1e-12;

interface Rung { edges: number[]; values: number[]; shoulders: number }

/** The ramp across a Measured/non-Measured boundary spans this many degrees (S47), on the lower bin's side. */
export const BOUNDARY_RAMP_DEG = 0.01;

/**
 * One run of the line, fitted greedily from the vertex at position `p` (value `vp`) to the vertex at `z` (value `vz`),
 * both fixed. Positions are in bins and may be fractional (a boundary ramp ends 0.02 bin from an edge); the run's
 * interior vertices sit on whole edges. From the vertex at (a, va), intersect the feasible slope interval [lo, hi] bin
 * by bin, and end the segment at the last edge where a vertex that is also at least the next bin's value is feasible.
 * Interior vertices are pushed to `out`; the two ends are the caller's. Returns the shoulders it needed.
 */
function fitRun(q: Float64Array, p: number, vp: number, z: number, vz: number, tau: number, out: { edges: number[]; values: number[] }): number {
  const last = Math.ceil(z);
  let a = p, va = vp, shoulders = 0;
  for (;;) {
    let lo = -Infinity, hi = Infinity;
    let bestB = -1, bestV = 0, closed = false;
    for (let b = Math.floor(a) + 1; b <= last; b++) {
      const i = b - 1;                     // the bin just added: [i, b)
      const reach = Math.min(b, z);        // how far the run covers it
      lo = Math.max(lo, (q[i] - va) / (reach - a));                  // G1 at its right end
      if (i > a) lo = Math.max(lo, (q[i] - va) / (i - a));           // G1 at its left end
      hi = Math.min(hi, (q[i] + tau - va) / (i + 0.5 - a));          // G2 at its centre
      if (lo > hi) break;
      if (b === last) {                    // the run ends at z, whose value is fixed
        const s = (vz - va) / (z - a);
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
      const f = Math.floor(a);
      if (f + 1 >= last) break;            // the last bin: the end vertex clears it
      bestB = f + 1;
      bestV = Math.max(q[f], q[f + 1]);
    }
    a = bestB; va = bestV;
    out.edges.push(a); out.values.push(va);
  }
  return shoulders;
}

/** A vertex the line is forced through: `pair` ties the two vertices of one boundary ramp, -1 for the anchor. */
interface Forced { pos: number; val: number; pair: number }

/**
 * One tolerance. `q` is the profile rotated to start at its highest bin; edge e is the left edge of bin e and
 * edge n is edge 0 again. `bounds` are the edges, ascending, that are Measured/non-Measured boundaries and keep their
 * ramp (S47): at each, the vertex pair puts the higher bin's value on the edge and the lower bin's value 0.01 degree
 * into the lower bin, so the line is never below either bin at the edge (G1) and the ramp costs the lower bin only
 * 0.01 degree. Between two ramps the interior is fitted by `fitRun`; with no ramps this is the whole circle from the
 * highest bin back to itself.
 */
function rung(q: Float64Array, tau: number, bounds: readonly number[]): Rung {
  const n = q.length;
  const d = BOUNDARY_RAMP_DEG / (360 / n);
  const forced: Forced[] = [];
  bounds.forEach((e, k) => {
    const aL = q[(e + n - 1) % n], aR = q[e];
    if (aR > aL) { forced.push({ pos: e === 0 ? n - d : e - d, val: aL, pair: k }, { pos: e, val: aR, pair: k }); }
    else forced.push({ pos: e, val: aL, pair: k }, { pos: e + d, val: aR, pair: k });
  });
  forced.sort((x, y) => x.pos - y.pos);
  if (forced.length === 0 || forced[0].pos !== 0) forced.unshift({ pos: 0, val: q[0], pair: -1 });
  const edges: number[] = [], values: number[] = [];
  let shoulders = 0;
  for (let j = 0; j < forced.length; j++) {
    const from = forced[j], to = j + 1 < forced.length ? forced[j + 1] : { pos: n, val: forced[0].val, pair: forced[0].pair };
    edges.push(from.pos); values.push(from.val);
    if (from.pair >= 0 && from.pair === to.pair) continue;      // the two ends of one ramp: a straight line
    shoulders += fitRun(q, from.pos, from.val, to.pos, to.val, tau, { edges, values });
  }
  return { edges, values, shoulders };
}

/**
 * The polyline for a 720-bin profile (or any bin count): `alt[i]` is the value of bin i, covering
 * [i * 360 / n, (i + 1) * 360 / n). Tries `TAU_LADDER` until the vertices number at most `maxPoints`. Never empty;
 * azimuths are distinct, ascending and in [0, 360); altitudes lie in the server's -10..90. A non-finite bin counts as
 * blocked (90). If even the last tolerance does not fit, the line is flat at the profile maximum, whose tolerance
 * is reported as the largest drop it hides.
 *
 * `state`, when given, is the BinState of every bin (S47). At every edge where Measured meets a bin that is not
 * Measured the line gets a boundary ramp (see `rung`): vertices off the bin grid by 0.01 degree, so the line follows a
 * Measured bin to within 0.01 degree of a blocked or unresolved neighbour instead of ramping across the whole bin.
 * The cap still holds: the interior runs are simplified harder first (the tau ladder); only when the last rung still
 * does not fit are ramps dropped, smallest altitude step first, each dropped ramp reverting to the old whole-bin one.
 */
export function conservativePolyline(alt: Float32Array, maxPoints = 180, state?: ArrayLike<number>): { points: HorizonPoint[]; tau: number; shoulders: number } {
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

  // The boundary edges, in the rotated frame, ordered so the ones to drop first come first.
  const boundaries: number[] = [];
  if (state && state.length === n) {
    for (let e = 0; e < n; e++) {
      if ((state[(e + s0) % n] === BinState.Measured) !== (state[(e + s0 + n - 1) % n] === BinState.Measured)) boundaries.push(e);
    }
  }
  const step = 360 / n;
  const stepAt = (e: number) => Math.abs(q[e] - q[(e + n - 1) % n]);
  const byStep = [...boundaries].sort((x, y) => stepAt(x) - stepAt(y) || x - y);

  const build = (edges: number[], values: number[]): HorizonPoint[] => {
    const pts = edges.map((e, k) => ({ az: Math.round((((e + s0) % n) * step) * 1e9) / 1e9, alt: values[k] }));
    // A flat horizon needs one vertex; a second, on the same line, keeps it a line to draw.
    if (pts.length === 1 && maxPoints >= 2) pts.push({ az: (pts[0].az + 180) % 360, alt: pts[0].alt });
    return pts.sort((p, r) => p.az - r.az);
  };

  // The first rung of the ladder that fits with the `dropped` smallest-step ramps given up, or null.
  const tryDrop = (dropped: number): { points: HorizonPoint[]; tau: number; shoulders: number } | null => {
    const gone = new Set(byStep.slice(0, dropped));
    const kept = boundaries.filter(e => !gone.has(e));
    for (const tau of TAU_LADDER) {
      const r = rung(q, tau, kept);
      const count = r.edges.length === 1 && maxPoints >= 2 ? 2 : r.edges.length;
      if (count <= maxPoints) return { points: build(r.edges, r.values), tau, shoulders: r.shoulders };
    }
    return null;
  };

  // Fewest dropped ramps that fits (monotone: dropping a ramp never adds a vertex at the same tolerance).
  let lo = 0, hi = boundaries.length;
  let found = tryDrop(0);
  if (!found) {
    found = tryDrop(hi);
    if (found) {
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        const r = tryDrop(mid);
        if (r) { hi = mid; found = r; } else lo = mid;
      }
    }
  }
  if (found) return found;
  let lowest = q[0];
  for (let i = 1; i < n; i++) if (q[i] < lowest) lowest = q[i];
  return { points: build([0], [q[0]]), tau: q[0] - lowest, shoulders: 0 };
}
