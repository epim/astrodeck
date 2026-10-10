// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Focal estimator for the horizon panorama scanner (SPEC-v2 4.8, D7).
//
// Until the lock, tracking runs at the fixed prior f0. Every steady pair gives r = image yaw / gyro yaw, both read
// at f0, so r = f_true / (f0 (1 + s_g)) whatever came before it, and the running estimate is f0 x median(r). It is
// never f <- f x median: that applies the correction again on every ratio and the series runs away from the truth.
// The lock fires once there are LOCK_MIN_RATIOS ratios whose MAD / median is at most LOCK_MAX_MAD_PCT. It is
// f_lock = f_true / (1 + s_g), the focal in gyro degrees, and its sd is frozen. The closure then swaps that for the
// ring's own scale: f_closed = f_lock x Theta_img / (T_true - Theta_gyro), good to 0.139 % (0.5 degrees over 360).
//
// Which pairs are steady (aligned chain keyframes, |dOmega| < 2 deg/s, 0.5 s after the onset) belongs to the
// caller. This class takes the ratios it is given and refuses only what is not a ratio.
import type { FocalLike, FocalSource, FocalState } from './types';

export const LOCK_MIN_RATIOS = 5, LOCK_MAX_MAD_PCT = 3, PRIOR_SD_PCT = 20, MIN_GYRO_YAW_DEG = 6, CLOSED_SD_PCT = 0.139;

/** A normal distribution's sd is 1.4826 x its MAD. */
const MAD_TO_SD = 1.4826;

/** Median of an ascending array. */
function medianOf(sorted: readonly number[]): number {
  const n = sorted.length, m = n >> 1;
  return n % 2 ? sorted[m] : (sorted[m - 1] + sorted[m]) / 2;
}

/** Median absolute deviation of `values` about their `median`. */
function madOf(values: readonly number[], median: number): number {
  return medianOf(values.map(v => Math.abs(v - median)).sort((a, b) => a - b));
}

export class FocalEstimator implements FocalLike {
  private readonly f0: number;
  private readonly ratioList: number[] = [];   // accepted ratios, ascending
  private measure: number;
  private best: number;
  private phase: FocalState = 'prior';
  private sd: number;

  constructor(prior: { fNorm: number; source: FocalSource; sdPct: number }) {
    if (!(Number.isFinite(prior.fNorm) && prior.fNorm > 0)) throw new RangeError(`focal prior fNorm ${prior.fNorm}`);
    if (!(Number.isFinite(prior.sdPct) && prior.sdPct >= 0)) throw new RangeError(`focal prior sdPct ${prior.sdPct}`);
    this.f0 = this.measure = this.best = prior.fNorm;
    this.sd = prior.sdPct;
  }

  /** The fNorm that LK runs at: the prior until the lock, then the locked value. The closure does not change it. */
  get fMeasure(): number { return this.measure; }
  /** The prior, then f0 x median ratio, then the locked value, then the closed one. */
  get fBest(): number { return this.best; }
  get state(): FocalState { return this.phase; }
  /** The prior's sd, then the lock's (frozen), then CLOSED_SD_PCT. */
  get sdPct(): number { return this.sd; }
  /** Accepted ratios; frozen at the lock. */
  get ratios(): number { return this.ratioList.length; }

  /** Ignored unless |gyroYawDeg| >= MIN_GYRO_YAW_DEG, and unless the ratio is finite and positive (a pair whose image
   * turned against the gyro is not a ratio, and a negative one would drag the median through zero). */
  addRatio(imgYawDeg: number, gyroYawDeg: number): 'collecting' | 'locked-now' | 'locked' {
    if (this.phase !== 'prior') return 'locked';
    if (!(Math.abs(gyroYawDeg) >= MIN_GYRO_YAW_DEG)) return 'collecting';
    const r = imgYawDeg / gyroYawDeg;
    if (!(Number.isFinite(r) && r > 0)) return 'collecting';

    this.ratioList.push(r);
    this.ratioList.sort((a, b) => a - b);
    const n = this.ratioList.length, median = medianOf(this.ratioList);
    this.best = this.f0 * median;
    if (n < LOCK_MIN_RATIOS) return 'collecting';
    const mad = madOf(this.ratioList, median);
    if (mad * 100 > LOCK_MAX_MAD_PCT * median) return 'collecting';

    this.phase = 'locked';
    this.measure = this.best;
    this.sd = MAD_TO_SD * mad / median / Math.sqrt(n) * 100;
    return 'locked-now';
  }

  /** The closure of 4.8. thetaImgDeg is the composed image yaw over the image-led steps (W_yaw >= 0.5) and thetaGyroDeg
   * the gyro yaw over the gyro-led ones, both as the chain holds them, that is read at fMeasure; trueDeg is
   * T_true = 360 + yaw(closure match). The result is f_measure x thetaImg / (trueDeg - thetaGyro), computed from
   * fMeasure and not from fBest, so a second call with the same ring returns the same focal.
   *
   * gyroScale is s_g = T_gyro / T_true - 1 with T_gyro = thetaImg + thetaGyro, the chain's own total. After a lock the
   * image-led part is read at f_lock = f_true / (1 + s_g), so it is in gyro degrees like the rest. It carries the
   * lock's own error (sdPct at the lock), because f_lock is the only link between the two scales.
   *
   * Throws RangeError when the numbers cannot describe a ring (no image-led yaw, or the gyro-led part covering the
   * whole turn): the caller owns that precondition, and a closure that did not happen must not read as one. */
  closeLoop(thetaImgDeg: number, thetaGyroDeg: number, trueDeg: number): { fNorm: number; gyroScale: number } {
    const k = thetaImgDeg / (trueDeg - thetaGyroDeg);
    if (!(Number.isFinite(trueDeg) && trueDeg !== 0 && Number.isFinite(k) && k > 0)) {
      throw new RangeError(`closeLoop(${thetaImgDeg}, ${thetaGyroDeg}, ${trueDeg}) is not a ring`);
    }
    this.best = this.measure * k;
    this.phase = 'closed';
    this.sd = CLOSED_SD_PCT;
    return { fNorm: this.best, gyroScale: (thetaImgDeg + thetaGyroDeg) / trueDeg - 1 };
  }
}
