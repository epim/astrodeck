// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Loop closure for the panorama scanner (SPEC-v2 4.9, D8): which early keyframes the newest one may be matched against, and how
// the residual that match leaves is spread back along the chain. The search, the acceptance and the correction themselves are
// the tracker's (tracker.ts, T28); these two functions are the pure parts.
import { axisSeparationDeg } from './rotation';
import type { Keyframe, V3 } from './types';

/** The closure is tried once the unwrapped yaw reaches this many degrees (4.9): a 360-degree ring a few degrees short of
 *  complete, with its gyro scale at 3 sigma. */
export const CLOSURE_MIN_DEG = 350;
/** Past this unwrapped yaw, 360 + hFoV / 2 on the default lens (381.5), a ring with no match closes by gyro (4.9 Fallback). */
export const CLOSURE_RETRY_MAX_DEG = 381.5;
/** The candidates are the first CLOSURE_FIRST keyframes, the ones the ring started on, and only those whose optical axis is
 *  within CLOSURE_AXIS_DEG of the newest keyframe's. At most CLOSURE_MAX_CANDIDATES are searched (4.9). Rule 8 puts a keyframe at
 *  the seam, near keyframe 0, and the candidates still come from the first 4 (ruling S27). */
export const CLOSURE_FIRST = 4, CLOSURE_AXIS_DEG = 15, CLOSURE_MAX_CANDIDATES = 2;

/** The ids to try a closure against: [] while |unwrappedDeg| is under 350, else at most 2 ids among the first 4 keyframes whose
 *  axis is within 15 degrees of `current`'s (ties and equal distances by id), nearest first. `current` itself is never a
 *  candidate. The axis is the pose's optical axis, so the corrections made so far count. */
export function closureCandidates(kfs: readonly Keyframe[], current: Keyframe, unwrappedDeg: number): number[] {
  if (!(Math.abs(unwrappedDeg) >= CLOSURE_MIN_DEG)) return [];
  const found: { id: number; sep: number }[] = [];
  for (let i = 0; i < kfs.length && i < CLOSURE_FIRST; i++) {
    const kf = kfs[i];
    if (kf.id === current.id) continue;
    const sep = axisSeparationDeg(kf.pose, current.pose);
    if (sep <= CLOSURE_AXIS_DEG) found.push({ id: kf.id, sep });
  }
  found.sort((a, b) => a.sep - b.sep || a.id - b.id);
  return found.slice(0, CLOSURE_MAX_CANDIDATES).map(f => f.id);
}

/** A view repeats along yaw when the mean of its pixel columns (a vertical stripe pattern, a fence, a row of windows) correlates
 *  with itself at a shift inside the closure window: past the main lobe of the autocorrelation, where it first falls below
 *  REPEAT_LOBE, it comes back to REPEAT_MIN or more at some lag up to REPEAT_MAX_LAG_PX pixels (15.3 degrees at L2 on the default
 *  lens, the window's reach). Such a view matches itself one period off as well as it matches the truth, and a closure can tell
 *  the two apart by nothing but its prior (S36, 4.6). Measured on synth.ts frames at L2 (45 columns): value-noise ground 0.20 to
 *  0.30 at every azimuth tried, a fence filling the view 0.92 to 0.98 at periods of 5 to 12 degrees; a period of 14 degrees
 *  (14.6 px, the end of the window) reads 0.32 and is not seen. A smooth view never falls below REPEAT_LOBE and does not repeat. */
export const REPEAT_MAX_LAG_PX = 16, REPEAT_LOBE = 0.3, REPEAT_MIN = 0.8;
export function repeatsAlongYaw(level: { w: number; h: number; px: Uint8Array }): boolean {
  const { w, h, px } = level;
  if (w < 2 * REPEAT_MAX_LAG_PX || h < 1) return false;
  const col = new Float64Array(w);
  let mean = 0;
  for (let x = 0; x < w; x++) {
    let s = 0;
    for (let y = 0; y < h; y++) s += px[y * w + x];
    col[x] = s / h;
    mean += col[x];
  }
  mean /= w;
  let r0 = 0;
  for (let x = 0; x < w; x++) { col[x] -= mean; r0 += col[x] * col[x]; }
  if (!(r0 > 1e-9)) return false;
  let pastLobe = false;
  for (let lag = 1; lag <= REPEAT_MAX_LAG_PX; lag++) {
    let s = 0;
    for (let x = 0; x + lag < w; x++) s += col[x] * col[x + lag];
    const r = s / r0 * w / (w - lag);   // the overlap shrinks with the lag: normalise it back
    if (!pastLobe) { if (r < REPEAT_LOBE) pastLobe = true; } else if (r >= REPEAT_MIN) return true;
  }
  return false;
}

/** Spread a closure's residual along the chain, linearly in cumulative angle: keyframe i takes `residual` x cumDeg[i] /
 *  cumDeg[last] and the same share of `logGain` (4.9 steps 2 and 3, 4.10 item 3). The first keyframe, at 0, takes nothing and
 *  the last takes all of it. `cumDeg` is non-decreasing from 0; a last entry that is not positive or not finite (a chain with
 *  no angle to spread over) gives zeros, never NaN. */
export function distributeClosure(cumDeg: readonly number[], residual: V3, logGain: readonly [number, number, number]):
  { rot: V3[]; logGain: [number, number, number][] } {
  const n = cumDeg.length, total = n > 0 ? cumDeg[n - 1] : 0, usable = Number.isFinite(total) && total > 0;
  const rot: V3[] = [], gain: [number, number, number][] = [];
  for (let i = 0; i < n; i++) {
    const s = usable ? cumDeg[i] / total : 0;
    rot.push([residual[0] * s, residual[1] * s, residual[2] * s]);
    gain.push([logGain[0] * s, logGain[1] * s, logGain[2] * s]);
  }
  return { rot, logGain: gain };
}
