// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The panorama scanner's pose predictor (SPEC-v2 4.5): the browser's orientation and motion events in, G(t) out,
// where G is the camera -> scan frame rotation at any time a frame asks for. Also the sensor facts the report carries,
// LatencyEstimator (the sensor-to-frame offset tau, 4.5) and NorthAnchor (the compass offset of the scan frame, 4.12).
//
// Plain inputs only. The scanner's listeners timestamp each event on the performance.now() timeline (3.2) and
// forward it; nothing here attaches a listener, starts a timer or reads a clock, so a replay drives exactly this
// code. Time moves only when a call carries one: `blocked` is decided against the latest time any call has carried.
//
// Three predictors, chosen per orientation sample (4.5):
// - `relative`: the relative stream's own rotation, slerped between its samples;
// - `absolute-gyro`: the gyro integrated in the body frame, the absolute stream correcting tilt only (never yaw, so
//   the magnetometer cannot bias focal ratios);
// - `absolute-only`: the absolute stream, its heading the median of the last three.
// A change of mode hands off continuously: the new stream's rotation is turned about the vertical so that the
// heading of G does not move at the instant of the change, and tilt comes from the new stream.
import { DEG, dot } from '../photosphereGeometry';
import {
  angleBetweenDeg, elevationDeg, expSO3, headingDeg, logSO3, qinv, qmul, qnormalize, qrotate,
  quatFromDeviceOrientation, rollDeg, slerp, worldYaw,
} from './rotation';
import type {
  AxisMapping, LatencyLike, MotionIn, NorthEstimate, OrientationIn, PredictorMode, Prediction, Quat, SensorFacts,
  Stats, V3,
} from './types';

export const STALE_LIMIT_MIN_MS = 50, STALE_LIMIT_MAX_MS = 150, HOLD_RATE_DEG_S = 0.5, RELATIVE_WINDOW_MS = 500, CHORD_MIN_MS = 100, CHORD_MAX_MS = 300;
// HOLD_MAX_MS is gone (S6): a hold vouched for by a quiet gyro has no time limit.

// ---- Constants (4.5, 4.12) --------------------------------------------------

/** A motion sample this close in time speaks for that time ("within 100 ms", "under 100 ms old"). */
const MOTION_FRESH_MS = 100;
/** The stale limit's interval when the last 2 s hold none during motion; 1.5 x 33 clamps to 50. */
const DEFAULT_INTERVAL_MS = 33;
const STALE_WINDOW_MS = 2000;
/** Only intervals during motion set the stale limit: a still phone's change-driven silence is not a slow stream. */
const STALE_MOTION_DEG_S = 2;
/** A duplicate is the other listener's copy of the previous absolute sample (Chromium's absolute-only shape). */
const DUPLICATE_ANGLE_EPS = 1e-9, DUPLICATE_MS = 2;
const BLOCKED_AFTER_MS = 1000;
/** absolute-gyro: the share of the tilt error between G and an absolute sample corrected per absolute sample. */
const TILT_GAIN = 0.02;
/** absolute-gyro: a longer step between motion samples integrates only this much (a stalled motion stream). */
const GYRO_DT_MAX_MS = 50;
/** absolute-gyro: absolute samples kept waiting for the gyro; more than a few means the motion stream has stopped. */
const PENDING_TILT_MAX = 8;
/** Gyro bias: an exponential mean of mapped rates while the orientation stream shows under 0.5 deg/s for 500 ms. */
const BIAS_WEIGHT = 0.05, STILL_RATE_DEG_S = 0.5, STILL_FOR_MS = 500;
/** Stillness is read over at least this baseline. With 0.05 degrees of white noise on each tilt axis (T01's default),
 *  the tilt between two samples is Rayleigh with sigma 0.071 degrees. Sample to sample (17 ms) that reads as 4 deg/s;
 *  0.5 deg/s over 250 ms is 0.125 degrees, which the noise passes on 21 % of samples, so a still phone would never
 *  stay still for 500 ms. Over 500 ms the line is 0.25 degrees and the noise passes it on 0.2 %. */
const STILL_BASELINE_MS = 500;
/** Axis-mapping pairs: a body rate from two orientation samples CHORD_MIN_MS or more apart, paired with the mean of
 *  the motion samples between them. Sample to sample (17 ms), the 0-16.7 ms pump age of a reading is a 40 % rate
 *  error; over 100 ms it is 7 %. A chord longer than CHORD_MAX_MS spans a pause and is not used (S10). */
const AXIS_OMEGA_MIN_DEG_S = 10;
const AXIS_MIN_PAIRS = 30, AXIS_FIT_MIN = 0.8, AXIS_EXCITED_SD_DEG_S = 1, AXIS_REFIT_EVERY = 10, AXIS_MAX_PAIRS = 2000;
const UNIT_BANDS = { deg: [0.7, 1.4], rad: [40, 75] } as const;
/** The six permutations, the W3C identity first so that it wins a tie. */
const PERMS: readonly (readonly [number, number, number])[] = [[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]];
/** Moving event rates count only intervals at or above this rate (RD finding 12). */
const MOVING_DEG_S = 10;
const RATE_BINS: readonly (readonly [number, number])[] = [[0, 2], [2, 5], [5, 10], [10, 20], [20, 40]];
/** How long G and the motion history are kept; predictAt before the oldest kept sample is null. */
const KEEP_MS = 5000;
/** How long the raw orientation streams are kept: chords and stillness look back at most 300 ms. */
const STREAM_KEEP_MS = 2000;
/** An absolute sample waits this long for G to reach its time before it is anchored against a hold. */
const NORTH_WAIT_MS = 250;
const MAX_STALE_RECORDS = 20000;

const LATENCY_MIN_DOMEGA_DEG_S = 2, LATENCY_APPLY_PAIRS = 10, LATENCY_APPLY_SIGMA_MS = 15;
const LATENCY_MIN_MS = -50, LATENCY_MAX_MS = 250;

const NORTH_INLIER_DEG = 8, NORTH_RESAMPLE_MS = 100, NORTH_SIGMA_FLOOR_DEG = 2;
const NORTH_UNSTABLE_SLOPE_DEG_MIN = 3, NORTH_UNSTABLE_RESIDUAL_DEG = 3;
/** Stable needs at least this share of the samples among the inliers: a 60/40 compass split 30 degrees apart has a
 *  tight inlier set and read stable at sigma 2 (S11). */
const NORTH_STABLE_INLIER_SHARE = 0.85;

// ---- Small helpers ----------------------------------------------------------

const Z: V3 = [0, 0, 1];
const finite = (x: number | null | undefined): x is number => typeof x === 'number' && Number.isFinite(x);
const wrap360 = (deg: number) => ((deg % 360) + 360) % 360;
/** Degrees into [-180, 180). */
const wrap180 = (deg: number) => { const w = wrap360(deg); return w >= 180 ? w - 360 : w; };
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const scale3 = (v: V3, k: number): V3 => [v[0] * k, v[1] * k, v[2] * k];
const norm3 = (v: V3) => Math.hypot(v[0], v[1], v[2]);
const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x));

function median(xs: readonly number[]): number {
  const s = [...xs].sort((a, b) => a - b), n = s.length;
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
}

/** Nearest-rank summary (types.ts Stats). */
function statsOf(xs: readonly number[]): Stats {
  if (!xs.length) return { n: 0, p50: null, p95: null, max: null };
  const s = [...xs].sort((a, b) => a - b), rank = (p: number) => s[Math.max(0, Math.ceil(p * s.length) - 1)];
  return { n: s.length, p50: rank(0.5), p95: rank(0.95), max: s[s.length - 1] };
}

/** Angle between the world-up directions two rotations see: the tilt part of their difference, blind to yaw. */
function tiltAngleDeg(a: Quat, b: Quat): number {
  const ua = qrotate(qinv(a), Z), ub = qrotate(qinv(b), Z);
  return Math.atan2(norm3(cross(ua, ub)), dot(ua, ub)) / DEG;
}

interface Sample { t: number; q: Quat }
interface MotionSample { t: number; raw: V3 }

/** A time-ordered buffer trimmed from the front without shifting on every call. */
class Series<T extends { t: number }> {
  private items: T[] = [];
  private head = 0;
  get length(): number { return this.items.length - this.head; }
  at(i: number): T { return this.items[this.head + i]; }
  get last(): T | undefined { return this.length ? this.items[this.items.length - 1] : undefined; }
  push(x: T): void { this.items.push(x); }
  replaceLast(x: T): void { this.items[this.items.length - 1] = x; }
  /** Drops items older than t but always keeps the newest. */
  trimBefore(t: number): void {
    while (this.head < this.items.length - 1 && this.items[this.head].t < t) this.head++;
    if (this.head > 1024) { this.items = this.items.slice(this.head); this.head = 0; }
  }
  /** Index of the newest item at or before t, or -1. */
  floor(t: number): number {
    let lo = 0, hi = this.length - 1, found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (this.at(mid).t <= t) { found = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return found;
  }
  /** The item nearest t, if one is within `within`. */
  nearest(t: number, within: number): T | null {
    const i = this.floor(t);
    let best: T | null = null, bestD = Infinity;
    for (const j of [i, i + 1]) {
      if (j < 0 || j >= this.length) continue;
      const d = Math.abs(this.at(j).t - t);
      if (d < bestD) { best = this.at(j); bestD = d; }
    }
    return bestD <= within ? best : null;
  }
}

/** deg/s between the newest sample and the newest one at least `minMs` older (or the oldest kept); null with no
 *  earlier sample to measure against. */
function chordRateDeg(series: Series<Sample>, minMs: number, tiltOnly: boolean): number | null {
  const cur = series.last;
  if (!cur || series.length < 2) return null;
  const ref = series.at(Math.max(0, series.floor(cur.t - minMs)));
  if (!(cur.t > ref.t)) return null;
  return (tiltOnly ? tiltAngleDeg(ref.q, cur.q) : angleBetweenDeg(ref.q, cur.q)) / ((cur.t - ref.t) / 1000);
}

type EventType = 'deviceorientation' | 'deviceorientationabsolute' | 'devicemotion';
interface EventCount { total: number; absoluteTrue: number; absoluteFalse: number; absoluteMissing: number; nullReadings: number; duplicates: number }
const newCount = (): EventCount => ({ total: 0, absoluteTrue: 0, absoluteFalse: 0, absoluteMissing: 0, nullReadings: 0, duplicates: 0 });
interface RateAcc { n: number; ms: number }
const hz = (a: RateAcc): number | null => (a.n > 0 && a.ms > 0 ? a.n / a.ms * 1000 : null);

// ---- PoseTrack --------------------------------------------------------------

export class PoseTrack {
  readonly north = new NorthAnchor();
  readonly latency: LatencyEstimator;
  private readonly latencyPriorMs: number;

  /** G, the predictor's output: one sample per predictor sample of the active mode. */
  private g = new Series<Sample>();
  private rel = new Series<Sample>();
  private abs = new Series<Sample>();
  private motion = new Series<MotionSample>();
  /** The newest orientation sample with finite beta and gamma, for elevation and roll before G exists. */
  private tilt: Sample | null = null;
  private headings: number[] = [];   // the last three absolute headings, for absolute-only's median
  private lastAbsEvent: { t: number; type: OrientationIn['type']; alpha: number | null; beta: number; gamma: number } | null = null;

  private active: PredictorMode = 'none';
  private latched = false;
  private modeAtBegin: PredictorMode | null = null;
  private modeChanges: SensorFacts['modeChanges'] = [];
  /** The yaw, degrees, composed onto the active stream's raw rotation since the last handoff. */
  private yawDeg = 0;
  private gyroQ: Quat | null = null;
  private gyroT = 0;
  /** absolute-gyro: absolute samples waiting for the gyro to integrate past their time. */
  private pendingTilt: Sample[] = [];

  private counts: Record<EventType, EventCount> = {
    deviceorientation: newCount(), deviceorientationabsolute: newCount(), devicemotion: newCount(),
  };
  private first: { t: number; allNull: boolean } | null = null;
  private usable = false;
  private sawTilt = false;
  private clock = -Infinity;

  private sawRelative = false;
  private chordStart: Sample | null = null;
  private chords: { t0: number; t1: number; omega: V3 }[] = [];
  private pairs: { rate: readonly [number, number, number]; omegaBody: V3 }[] = [];
  private nextFitAt = AXIS_REFIT_EVERY;
  private axisMap: AxisMapping | null = null;
  private biasDeg: V3 = [0, 0, 0];
  private lastMoveT: number | null = null;

  private staleIntervals = new Series<{ t: number; dt: number }>();
  private staleRefusals = 0;
  private staleLimits: number[] = [];
  private gyroZeroTriples = 0;
  private gyroSeenNonZero = false;
  private rateStats = {
    relative: { lastT: null as number | null, lastW: null as number | null, moving: { n: 0, ms: 0 }, bins: RATE_BINS.map(() => ({ n: 0, ms: 0 })) },
    absolute: { lastT: null as number | null, lastW: null as number | null, moving: { n: 0, ms: 0 }, bins: RATE_BINS.map(() => ({ n: 0, ms: 0 })) },
    motion: { lastT: null as number | null, lastW: null as number | null, moving: { n: 0, ms: 0 }, bins: RATE_BINS.map(() => ({ n: 0, ms: 0 })) },
  };
  private pendingNorth: { t: number; heading: number }[] = [];

  constructor(o?: { latencyPriorMs?: number; latencySdMs?: number }) {
    this.latencyPriorMs = o?.latencyPriorMs ?? 50;
    this.latency = new LatencyEstimator(this.latencyPriorMs, o?.latencySdMs ?? 40);
  }

  get canPredict(): boolean { return this.sawTilt; }
  get mode(): PredictorMode { return this.active; }
  get axis(): AxisMapping | null { return this.axisMap; }
  get blocked(): boolean {
    return this.first !== null && this.first.allNull && !this.usable && this.clock - this.first.t >= BLOCKED_AFTER_MS;
  }

  latch(t: number): PredictorMode {
    this.advance(t);
    if (!this.latched) { this.latched = true; this.modeAtBegin = this.active; }
    return this.active;
  }

  // ---- Inputs ----

  onOrientation(e: OrientationIn): void {
    this.advance(e.t);
    const count = this.counts[e.type];
    count.total++;
    if (e.absolute === true) count.absoluteTrue++; else if (e.absolute === false) count.absoluteFalse++; else count.absoluteMissing++;
    const allNull = e.alpha === null && e.beta === null && e.gamma === null;
    if (this.first === null) this.first = { t: e.t, allNull };
    if (allNull) { count.nullReadings++; return; }
    if (!finite(e.beta) || !finite(e.gamma)) return;
    // 4.5: an absolute sample is flagged absolute, comes from the absolute listener or carries a compass heading; but
    // an iOS deviceorientation with a compass heading is a relative predictor sample plus a north sample.
    const relative = e.type === 'deviceorientation' && e.absolute !== true;
    if (!relative && this.isDuplicate(e)) { count.duplicates++; return; }
    if (!relative) this.lastAbsEvent = { t: e.t, type: e.type, alpha: e.alpha, beta: e.beta, gamma: e.gamma };
    this.usable = true;
    this.sawTilt = true;
    const alpha = finite(e.alpha) ? e.alpha : null;
    // Elevation and roll do not depend on alpha, so a tilt-only reading still says where the camera points vertically.
    this.tilt = { t: e.t, q: quatFromDeviceOrientation(alpha ?? 0, e.beta, e.gamma) };
    if (alpha === null) return;   // no heading at all: not a predictor sample

    const q = this.tilt.q;
    const series = relative ? this.rel : this.abs;
    const prev = series.last;
    if (prev && e.t < prev.t) return;   // a backwards timestamp
    const s: Sample = { t: e.t, q };
    series.push(s);
    if (relative && !this.sawRelative) { this.sawRelative = true; this.chordStart = null; }
    this.noteStill(series, !relative, e.t);
    this.noteRate(relative ? 'relative' : 'absolute', e.t, this.omegaFor(series, e.t));
    series.trimBefore(e.t - STREAM_KEEP_MS);
    // The axis fit reads the relative stream when there is one, the absolute stream otherwise.
    if (relative === this.sawRelative) this.addChordPoint(s);
    if (!relative) { this.headings.push(headingDeg(q)); if (this.headings.length > 3) this.headings.shift(); }

    // The old predictor takes the sample first, so a handoff starts from the old G at this very instant.
    this.feedActive(relative, s);
    const next = this.chooseMode(e.t);
    if (next !== this.active) this.handoff(e.t, next);

    const compass = finite(e.compassHeading) ? wrap360(e.compassHeading) : null;
    const north = compass ?? (relative ? null : headingDeg(q));
    if (north !== null) this.pendingNorth.push({ t: e.t, heading: north });
    this.resolveNorth();
  }

  onMotion(e: MotionIn): void {
    this.advance(e.t);
    const count = this.counts.devicemotion;
    count.total++;
    const r = e.rate;
    if (!r || !finite(r.alpha) || !finite(r.beta) || !finite(r.gamma)) { count.nullReadings++; return; }
    const prev = this.motion.last;
    if (prev && e.t < prev.t) return;   // a backwards timestamp (#106)
    const raw: V3 = [r.alpha, r.beta, r.gamma];
    // Chromium rounds to 0.1 deg/s, so a still phone reads exact zeros most of the time. A zero triple is a reading
    // only once the gyro has shown it can read anything else (4.5, RD finding 11).
    if (raw[0] === 0 && raw[1] === 0 && raw[2] === 0) {
      if (!this.gyroSeenNonZero) return;
      this.gyroZeroTriples++;
    } else this.gyroSeenNonZero = true;
    this.usable = true;
    this.motion.push({ t: e.t, raw });
    this.motion.trimBefore(e.t - KEEP_MS);
    this.noteRate('motion', e.t, this.magnitudeDeg(raw) ?? this.chordRateAt(e.t));

    const axis = this.axisMap;
    if (axis?.confirmed && this.lastMoveT !== null && e.t - this.lastMoveT >= STILL_FOR_MS) {
      const w = this.mappedDeg(raw, axis);
      if (norm3(w) < HOLD_RATE_DEG_S) {
        const b = this.biasDeg;
        this.biasDeg = [b[0] + BIAS_WEIGHT * (w[0] - b[0]), b[1] + BIAS_WEIGHT * (w[1] - b[1]), b[2] + BIAS_WEIGHT * (w[2] - b[2])];
      }
    }

    if (this.active === 'absolute-gyro' && this.gyroQ && axis?.confirmed) {
      const wNow = this.bodyRad(raw, axis);
      // Trapezoid over the step when the previous sample is close enough to stand for its start.
      const w = prev && e.t - prev.t <= GYRO_DT_MAX_MS ? scale3(this.add(wNow, this.bodyRad(prev.raw, axis)), 0.5) : wNow;
      const dt = clamp(e.t - this.gyroT, 0, GYRO_DT_MAX_MS) / 1000;
      if (dt > 0) this.gyroQ = qnormalize(qmul(this.gyroQ, expSO3(scale3(w, dt))));
      this.gyroT = Math.max(this.gyroT, e.t);
      this.pushG(e.t, this.gyroQ);
      if (this.pendingTilt.length) { this.settleTilt(); this.pushG(e.t, this.gyroQ); }
    }

    this.settleChords(e.t);
    this.resolveNorth();
  }

  // ---- Prediction ----

  predictAt(t: number): Prediction | null {
    if (!finite(t)) return null;
    this.advance(t);
    const r = this.evaluate(t, true);
    if (r === 'stale') { this.staleRefusals++; return null; }
    if (r === null) return null;
    return { q: r.q, t, mode: this.active, extrapolatedMs: r.extrapolatedMs, held: r.held };
  }

  rateAt(t: number): number | null {
    this.advance(t);
    const m = this.motion.nearest(t, MOTION_FRESH_MS);
    // A motion sample speaks only once the mapping has confirmed its unit; otherwise, and without a sample, the chord (S41).
    return (m ? this.magnitudeDeg(m.raw) : null) ?? this.chordRateAt(t);
  }

  omegaBodyAt(t: number): V3 | null {
    this.advance(t);
    return this.omegaBody(t);
  }

  elevationAt(t: number): number | null {
    this.advance(t);
    const q = this.looseQ(t);
    return q ? elevationDeg(q) : null;
  }

  rollAt(t: number): number | null {
    this.advance(t);
    const q = this.looseQ(t);
    return q ? rollDeg(q) : null;
  }

  facts(): SensorFacts {
    const types: EventType[] = ['deviceorientation', 'deviceorientationabsolute', 'devicemotion'];
    const rs = this.rateStats;
    return {
      modeAtBegin: this.modeAtBegin,
      modeChanges: this.modeChanges.map(c => ({ ...c })),
      events: types.map(type => ({ type, ...this.counts[type] })),
      movingHz: { relative: hz(rs.relative.moving), absolute: hz(rs.absolute.moving), motion: hz(rs.motion.moving) },
      rateByOmega: RATE_BINS.map(([from, to], i) => ({
        omegaFrom: from, omegaTo: to, relativeHz: hz(rs.relative.bins[i]), absoluteHz: hz(rs.absolute.bins[i]),
      })),
      blocked: this.blocked,
      axis: this.axisMap ? { ...this.axisMap } : null,
      gyroZeroTriples: this.gyroZeroTriples,
      gyroSeenNonZero: this.gyroSeenNonZero,
      staleRefusals: this.staleRefusals,
      staleLimitMs: statsOf(this.staleLimits),
      latency: {
        priorMs: this.latencyPriorMs, tauMs: this.latency.tauMs, sigmaMs: this.latency.sigmaMs,
        pairs: this.latency.pairs, applied: this.latency.applied,
      },
    };
  }

  // ---- Internals: modes and G ----

  private advance(t: number): void { if (finite(t) && t > this.clock) this.clock = t; }

  private isDuplicate(e: OrientationIn): boolean {
    const p = this.lastAbsEvent;
    if (!p || p.type === e.type || Math.abs(e.t - p.t) > DUPLICATE_MS) return false;
    const same = (a: number | null, b: number | null) => (a === null || b === null ? a === b : Math.abs(a - b) <= DUPLICATE_ANGLE_EPS);
    return same(e.alpha, p.alpha) && same(e.beta, p.beta) && same(e.gamma, p.gamma);
  }

  /** The per-sample rule of 4.5. It runs on orientation samples, and the sample that runs it always qualifies its own
   *  stream, so once any sample has arrived the answer is never 'none': silence is predictAt's business. A silent
   *  relative stream stays in charge while a quiet gyro says the phone is still (S6): otherwise G's yaw would follow
   *  the compass's wander before the mapping can confirm. */
  private chooseMode(t: number): PredictorMode {
    const rel = this.rel.last, abs = this.abs.last, m = this.motion.last;
    if (rel && t - rel.t <= RELATIVE_WINDOW_MS) return 'relative';
    if (rel && this.active === 'relative' && this.quietGyro(t)) return 'relative';
    if (abs && this.axisMap?.confirmed && m && Math.abs(t - m.t) < MOTION_FRESH_MS) return 'absolute-gyro';
    if (abs && t - abs.t <= RELATIVE_WINDOW_MS) return 'absolute-only';
    return 'none';
  }

  private feedActive(relative: boolean, s: Sample): void {
    if (this.active === 'relative' && relative) this.pushG(s.t, qmul(worldYaw(this.yawDeg), s.q));
    else if (this.active === 'absolute-only' && !relative) this.pushG(s.t, qmul(worldYaw(this.yawDeg), this.medianHeading(s.q)));
    else if (this.active === 'absolute-gyro' && !relative) { this.pendingTilt.push(s); this.settleTilt(); }
  }

  /** Applies the tilt corrections of absolute samples the gyro has integrated past, so that each reads G at its own
   *  time between two G samples rather than extrapolated one motion step ahead. */
  private settleTilt(): void {
    const last = this.g.last;
    while (last && this.pendingTilt.length && this.pendingTilt[0].t <= last.t) {
      const s = this.pendingTilt.shift()!;
      this.correctTilt(s.q, s.t);
    }
    if (this.pendingTilt.length > PENDING_TILT_MAX) this.pendingTilt.splice(0, this.pendingTilt.length - PENDING_TILT_MAX);
  }

  /** The newest absolute rotation turned to the median of the last three headings (#47), unwrapped about the newest. */
  private medianHeading(q: Quat): Quat {
    const newest = this.headings[this.headings.length - 1];
    const m = median(this.headings.map(h => newest + wrap180(h - newest)));
    return qmul(worldYaw(m - newest), q);
  }

  /** Rotate G about the body axis up_G x up_A by TILT_GAIN of the angle between the two ups. The axis is horizontal
   *  in the world, so this moves tilt and never yaw. up_G is read from G at the absolute sample's own time, not from
   *  the gyro state: at a 25 deg/s nod one motion step is 0.4 degrees of real tilt change, 2 % of which per sample
   *  would otherwise be "corrected" away (0.009 degrees a step, measured). */
  private correctTilt(a: Quat, t: number): void {
    const g = this.gyroQ;
    if (!g) return;
    const at = this.evaluate(t, false), ref = at && at !== 'stale' ? at.q : g;
    const target = qmul(worldYaw(this.yawDeg), a);   // the absolute sample in G's yaw zero; it sees the same up as a
    const upG = qrotate(qinv(ref), Z), upA = qrotate(qinv(target), Z);
    const axis = cross(upG, upA), s = norm3(axis);
    if (s < 1e-12) return;
    const angle = Math.atan2(s, dot(upG, upA));
    // G' = G . R makes up_G' = R^-1 up_G, so R turns by minus the correction to bring up_G toward up_A.
    this.gyroQ = qnormalize(qmul(g, expSO3(scale3(axis, -TILT_GAIN * angle / s))));
  }

  private handoff(t: number, next: PredictorMode): void {
    const from = this.active;
    const old = from === 'none' ? null : this.evaluate(t, false);
    const rel = this.rel.last, abs = this.abs.last;
    let raw: Quat | null = null;
    if (next === 'relative' && rel) raw = rel.q;
    else if (next === 'absolute-only' && abs) raw = this.medianHeading(abs.q);
    else if (next === 'absolute-gyro' && abs) raw = abs.q;   // "start from the newest absolute sample"
    if (next !== 'none' && raw === null) return;
    if (raw !== null) {
      // The heading difference between the old G and the new raw rotation at this instant.
      this.yawDeg = old && old !== 'stale' ? wrap180(headingDeg(old.q) - headingDeg(raw)) : 0;
      const q = qmul(worldYaw(this.yawDeg), raw);
      if (next === 'absolute-gyro') { this.gyroQ = q; this.gyroT = t; }
      this.pushG(t, q);
    }
    if (this.latched) this.modeChanges.push({ tMs: t, from, to: next });
    this.active = next;
    this.pendingTilt = [];
  }

  private pushG(t: number, q: Quat): void {
    const last = this.g.last;
    if (last && t < last.t) return;
    if (last && t === last.t) { this.g.replaceLast({ t, q }); return; }
    if (last) {
      const dt = t - last.t;
      const m = this.motion.nearest(t, MOTION_FRESH_MS);
      // Until the unit is confirmed the rate is the interval's own angle over dt, not a chord (S41): this only asks whether
      // the interval is motion (the 2 deg/s line), and a chord could not end at the sample being pushed.
      const w = (m ? this.magnitudeDeg(m.raw) : null) ?? angleBetweenDeg(last.q, q) / (dt / 1000);
      if (w >= STALE_MOTION_DEG_S) this.staleIntervals.push({ t, dt });
    }
    this.g.push({ t, q });
    this.g.trimBefore(t - KEEP_MS);
    this.staleIntervals.trimBefore(t - STALE_WINDOW_MS);
  }

  /** 1.5 x the median interval between G's samples over the 2 s before its newest, counting only intervals during
   *  motion, clamped to [50, 150] ms (4.5, RS M6). */
  private staleLimit(): number {
    const last = this.g.last;
    const dts: number[] = [];
    if (last) {
      for (let i = 0; i < this.staleIntervals.length; i++) {
        const s = this.staleIntervals.at(i);
        if (s.t > last.t - STALE_WINDOW_MS) dts.push(s.dt);
      }
    }
    return clamp(1.5 * (dts.length ? median(dts) : DEFAULT_INTERVAL_MS), STALE_LIMIT_MIN_MS, STALE_LIMIT_MAX_MS);
  }

  /** G at t. Strict is predictAt's rule (4.5); loose holds or extrapolates without limit, for a handoff, a north
   *  sample or the elevation. 'stale' is a strict refusal. */
  private evaluate(t: number, strict: boolean): { q: Quat; extrapolatedMs: number; held: boolean } | 'stale' | null {
    const n = this.g.length;
    if (n === 0 || (strict && this.active === 'none')) return null;
    const first = this.g.at(0), last = this.g.at(n - 1);
    if (t < first.t) return strict ? null : { q: first.q, extrapolatedMs: 0, held: true };
    if (t <= last.t) {
      const i = this.g.floor(t), a = this.g.at(i);
      if (a.t === t || i === n - 1) return { q: a.q, extrapolatedMs: 0, held: false };
      const b = this.g.at(i + 1);
      return { q: slerp(a.q, b.q, (t - a.t) / (b.t - a.t)), extrapolatedMs: 0, held: false };
    }
    const gap = t - last.t;
    // A quiet gyro vouches for change-driven silence, for as long as it stays quiet (S6).
    if (this.quietGyro(t)) return { q: last.q, extrapolatedMs: gap, held: true };
    const limit = strict ? this.staleLimit() : STALE_LIMIT_MAX_MS;
    if (strict && this.staleLimits.length < MAX_STALE_RECORDS) this.staleLimits.push(limit);
    const w = this.omegaBody((last.t + t) / 2) ?? this.omegaBody(t);
    if (gap <= limit) {
      if (w) return { q: qnormalize(qmul(last.q, expSO3(scale3(w, gap / 1000)))), extrapolatedMs: gap, held: false };
      return { q: last.q, extrapolatedMs: gap, held: true };
    }
    return strict ? 'stale' : { q: last.q, extrapolatedMs: gap, held: true };
  }

  private looseQ(t: number): Quat | null {
    const r = this.evaluate(t, false);
    return r && r !== 'stale' ? r.q : this.tilt?.q ?? null;
  }

  private resolveNorth(): void {
    if (!this.pendingNorth.length) return;
    const last = this.g.last;
    const keep: { t: number; heading: number }[] = [];
    for (const p of this.pendingNorth) {
      const due = this.clock - p.t >= NORTH_WAIT_MS;
      if (last && (p.t <= last.t || due)) {
        const r = this.evaluate(p.t, false);
        if (r && r !== 'stale') this.north.add(p.t, p.heading, headingDeg(r.q));
      } else if (!due) keep.push(p);
    }
    this.pendingNorth = keep;
  }

  // ---- Internals: rates, bias and the axis mapping ----

  /** |rate| of a motion sample in deg/s, or null until the axis mapping is confirmed (S41). The unit is the mapping's,
   *  and an unconfirmed fit is a guess: a transient one read `rad` and turned a 12 deg/s pan into 703 deg/s, and a gyro
   *  that reports rad/s reads as quiet while moving (S33). So a motion sample is a rate only once the mapping vouches for
   *  its unit, and until then every caller takes the orientation chord (S10) instead. */
  private magnitudeDeg(raw: V3): number | null {
    const a = this.axisMap;
    return a?.confirmed ? norm3(raw) * (a.unit === 'rad' ? 1 / DEG : 1) : null;
  }
  /** |omega| over a chord of 100-300 ms of G samples ending at the newest one at or before t, never over two
   *  consecutive samples (S10). The chord starts at the newest sample MORE than CHORD_MIN_MS older: on a regular 60 Hz
   *  stream the sample exactly 100 ms back makes a 6-interval chord, whose median pump-age error is 4.9 %; one interval
   *  more is 4.2 %. A chord longer than CHORD_MAX_MS spans a pause, and the rate is unknown. */
  private chordRateAt(t: number): number | null {
    const i = this.g.floor(t);
    if (i < 1) return null;
    const b = this.g.at(i);
    let j = this.g.floor(b.t - CHORD_MIN_MS);
    if (j >= 0 && this.g.at(j).t === b.t - CHORD_MIN_MS) j--;
    if (j < 0) return null;
    const a = this.g.at(j);
    return b.t - a.t <= CHORD_MAX_MS ? angleBetweenDeg(a.q, b.q) / ((b.t - a.t) / 1000) : null;
  }
  private mappedDeg(raw: V3, a: AxisMapping): V3 {
    const k = a.unit === 'rad' ? 1 / DEG : 1;
    return [a.sign[0] * raw[a.perm[0]] * k, a.sign[1] * raw[a.perm[1]] * k, a.sign[2] * raw[a.perm[2]] * k];
  }
  /** Body rate less the bias, rad/s. */
  private bodyRad(raw: V3, a: AxisMapping): V3 {
    const w = this.mappedDeg(raw, a), b = this.biasDeg;
    return [(w[0] - b[0]) * DEG, (w[1] - b[1]) * DEG, (w[2] - b[2]) * DEG];
  }
  private add(a: V3, b: V3): V3 { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }

  private omegaBody(t: number): V3 | null {
    const a = this.axisMap;
    if (!a?.confirmed) return null;
    const m = this.motion.nearest(t, MOTION_FRESH_MS);
    return m ? this.bodyRad(m.raw, a) : null;
  }

  /** A quiet gyro: a motion sample within 100 ms of t, and a rate under 0.5 deg/s. The rate is the sample's once the
   *  mapping is confirmed and the orientation chord's before (S33, S41): the sample then only shows that the gyro is
   *  delivering, and a chord that is unknown does not vouch. This narrows S6 until the mapping confirms: a silent
   *  relative stream is held only while a chord of G reads quiet. */
  private quietGyro(t: number): boolean {
    const m = this.motion.nearest(t, MOTION_FRESH_MS);
    if (m === null) return false;
    const w = this.magnitudeDeg(m.raw) ?? this.chordRateAt(t);
    return w !== null && w < HOLD_RATE_DEG_S;
  }

  /** The rate of a stream at its newest sample: the gyro's magnitude when fresh and its unit confirmed (S41), else the
   *  stream's own chord. */
  private omegaFor(series: Series<Sample>, t: number): number | null {
    const m = this.motion.nearest(t, MOTION_FRESH_MS);
    return (m ? this.magnitudeDeg(m.raw) : null) ?? chordRateDeg(series, CHORD_MIN_MS, false);
  }

  /** An interval's rate is the smaller of its two ends, so the gap that ends a pause is not counted as moving. An end
   *  whose rate is unknown (a stream's first sample, before any gyro) leaves its interval out of the table. */
  private noteRate(kind: 'relative' | 'absolute' | 'motion', t: number, w: number | null): void {
    const st = this.rateStats[kind];
    if (st.lastT !== null && t > st.lastT && w !== null && st.lastW !== null) {
      const dt = t - st.lastT, wi = Math.min(st.lastW, w);
      if (wi >= MOVING_DEG_S) { st.moving.n++; st.moving.ms += dt; }
      const bin = RATE_BINS.findIndex(([from, to]) => wi >= from && wi < to);
      if (bin >= 0) { st.bins[bin].n++; st.bins[bin].ms += dt; }
    }
    st.lastT = t;
    st.lastW = w;
  }

  /** The orientation stream "shows" motion when its rate over STILL_BASELINE_MS reaches 0.5 deg/s. The absolute
   *  stream is read for tilt only: its heading is a compass, and compass noise is not motion. */
  private noteStill(series: Series<Sample>, tiltOnly: boolean, t: number): void {
    if (this.lastMoveT === null) this.lastMoveT = t;   // no evidence of stillness before the first sample
    if ((chordRateDeg(series, STILL_BASELINE_MS, tiltOnly) ?? 0) >= STILL_RATE_DEG_S) this.lastMoveT = t;
  }

  private addChordPoint(s: Sample): void {
    const start = this.chordStart;
    if (!start || s.t - start.t > CHORD_MAX_MS) { this.chordStart = s; return; }
    if (s.t - start.t < CHORD_MIN_MS) return;
    this.chordStart = s;
    const omega = scale3(logSO3(qmul(qinv(start.q), s.q)), 1000 / (s.t - start.t) / DEG);   // body frame, deg/s
    if (norm3(omega) >= AXIS_OMEGA_MIN_DEG_S) this.chords.push({ t0: start.t, t1: s.t, omega });
  }

  /** Pairs each chord with the mean motion sample between its ends once the motion stream has passed its end, and
   *  refits every AXIS_REFIT_EVERY pairs until the mapping is confirmed. A confirmed mapping is kept. */
  private settleChords(motionT: number): void {
    while (this.chords.length && this.chords[0].t1 <= motionT) {
      const c = this.chords.shift()!;
      const sum: V3 = [0, 0, 0];
      let n = 0;
      for (let i = Math.max(0, this.motion.floor(c.t0)); i < this.motion.length; i++) {
        const m = this.motion.at(i);
        if (m.t > c.t1) break;
        if (m.t < c.t0) continue;
        sum[0] += m.raw[0]; sum[1] += m.raw[1]; sum[2] += m.raw[2]; n++;
      }
      if (!n) continue;
      this.pairs.push({ rate: [sum[0] / n, sum[1] / n, sum[2] / n], omegaBody: c.omega });
      if (this.pairs.length > AXIS_MAX_PAIRS) this.pairs.shift();
    }
    if (!this.axisMap?.confirmed && this.pairs.length >= this.nextFitAt) {
      this.axisMap = fitAxisMapping(this.pairs);
      this.nextFitAt = this.pairs.length + AXIS_REFIT_EVERY;
    }
  }
}

// ---- The axis mapping (4.5, RD finding 1) -----------------------------------

/** Fits how rotationRate's three components map onto the camera axes, from pairs of a raw rate triple and the body
 *  rate the orientation stream shows over the same time (omegaBody in deg/s). All six permutations are tried. For each
 *  body axis with a rate sd of at least 1 deg/s, the correlation and the regression slope of the body rate on the raw
 *  component; the sign is the slope's sign, `fit` is the smallest |corr| over the excited axes, and the unit is 'deg'
 *  when the median |slope| is in [0.7, 1.4] and 'rad' when it is in [40, 75].
 *
 *  Which permutation is best: the highest fit among those whose slopes all fall inside one unit band, else the highest
 *  fit. A pan at a steady pitch turns the y and z rates into one signal (omega cos p, omega sin p), so a permutation
 *  that swaps them correlates just as well and only the slopes (cos p / sin p, not 1) tell it apart.
 *
 *  Confirmed needs a fit of at least 0.8, 30 pairs, the slopes in the unit's band, and two excited axes: with one, the
 *  other two axes' order is a guess. With exactly one axis unexcited, its sign makes the mapping a proper rotation. */
export function fitAxisMapping(pairs: readonly { rate: readonly [number, number, number]; omegaBody: V3 }[]): AxisMapping {
  const n = pairs.length;
  const none: AxisMapping = { perm: [0, 1, 2], sign: [1, 1, 1], unit: 'deg', fit: 0, confirmed: false, samples: n };
  if (n < 3) return none;
  const mb = [0, 0, 0], mr = [0, 0, 0];
  for (const p of pairs) for (let i = 0; i < 3; i++) { mb[i] += p.omegaBody[i] / n; mr[i] += p.rate[i] / n; }
  const vb = [0, 0, 0], vr = [0, 0, 0], c = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (const p of pairs) {
    for (let i = 0; i < 3; i++) {
      const db = p.omegaBody[i] - mb[i];
      vb[i] += db * db / n; vr[i] += (p.rate[i] - mr[i]) ** 2 / n;
      for (let j = 0; j < 3; j++) c[i][j] += db * (p.rate[j] - mr[j]) / n;
    }
  }
  const excited = [0, 1, 2].filter(i => Math.sqrt(vb[i]) >= AXIS_EXCITED_SD_DEG_S);
  if (!excited.length) return none;
  const corr = (i: number, j: number) => (vr[j] > 0 ? c[i][j] / Math.sqrt(vb[i] * vr[j]) : 0);
  const slope = (i: number, j: number) => (vr[j] > 0 ? c[i][j] / vr[j] : 0);
  const unitOf = (slopes: number[]): 'deg' | 'rad' | null => {
    const m = median(slopes.map(Math.abs));
    return m >= UNIT_BANDS.deg[0] && m <= UNIT_BANDS.deg[1] ? 'deg' : m >= UNIT_BANDS.rad[0] && m <= UNIT_BANDS.rad[1] ? 'rad' : null;
  };
  const inBand = (slopes: number[], unit: 'deg' | 'rad' | null) =>
    unit !== null && slopes.every(s => Math.abs(s) >= UNIT_BANDS[unit][0] && Math.abs(s) <= UNIT_BANDS[unit][1]);

  let best: { perm: readonly [number, number, number]; fit: number; banded: boolean; slopes: number[]; unit: 'deg' | 'rad' | null } | null = null;
  for (const perm of PERMS) {
    const fit = Math.min(...excited.map(i => Math.abs(corr(i, perm[i]))));
    const slopes = excited.map(i => slope(i, perm[i]));
    const unit = unitOf(slopes), banded = inBand(slopes, unit);
    if (!best || (banded && !best.banded) || (banded === best.banded && fit > best.fit)) best = { perm, fit, banded, slopes, unit };
  }
  const b = best!;
  const sign: [number, number, number] = [1, 1, 1];
  excited.forEach((i, k) => { sign[i] = b.slopes[k] < 0 ? -1 : 1; });
  if (excited.length === 2) {
    // A proper rotation: the product of the signs equals the parity of the permutation.
    const free = [0, 1, 2].find(i => !excited.includes(i))!;
    const p = b.perm, parity = (p[0] < p[1] ? 1 : -1) * (p[0] < p[2] ? 1 : -1) * (p[1] < p[2] ? 1 : -1);
    sign[free] = parity * sign[excited[0]] * sign[excited[1]];
  }
  const confirmed = b.fit >= AXIS_FIT_MIN && n >= AXIS_MIN_PAIRS && b.banded && excited.length >= 2;
  return { perm: b.perm, sign, unit: b.unit ?? 'deg', fit: b.fit, confirmed, samples: n };
}

// ---- LatencyEstimator (4.5, RD finding 9) -----------------------------------

/** The sensor-to-frame offset tau. Each aligned chain pair gives r, the image yaw minus the predictor yaw over the
 *  pair (degrees), dOmega, the heading rate at b minus that at a (deg/s), and the tau used for those frames (ms). The
 *  model r = a + (tauUsed - tau) dOmega / 1000 is linear in (a, tau): with y = r - tauUsed dOmega / 1000 and
 *  x = -dOmega / 1000 it is y = a + tau x, an ordinary least-squares line WITH an intercept. The intercept absorbs the
 *  constant yaw offset a pre-lock focal error adds; through the origin that offset would bias tau. */
export class LatencyEstimator implements LatencyLike {
  private readonly priorMs: number;
  private readonly priorSdMs: number;
  private n = 0;
  private sx = 0; private sy = 0; private sxx = 0; private sxy = 0; private syy = 0;
  private est: { tau: number; sigma: number } | null = null;
  private isApplied = false;

  constructor(priorMs = 50, priorSdMs = 40) {
    this.priorMs = priorMs;
    this.priorSdMs = priorSdMs;
  }

  addPair(residualYawDeg: number, dOmegaDegS: number, tauUsedMs: number): void {
    if (!finite(residualYawDeg) || !finite(dOmegaDegS) || !finite(tauUsedMs)) return;
    if (!(Math.abs(dOmegaDegS) > LATENCY_MIN_DOMEGA_DEG_S)) return;
    const x = -dOmegaDegS / 1000, y = residualYawDeg - tauUsedMs * dOmegaDegS / 1000;
    this.n++; this.sx += x; this.sy += y; this.sxx += x * x; this.sxy += x * y; this.syy += y * y;
    this.solve();
  }

  get tauMs(): number { return this.isApplied && this.est ? clamp(this.est.tau, LATENCY_MIN_MS, LATENCY_MAX_MS) : this.priorMs; }
  get sigmaMs(): number { return this.isApplied && this.est ? this.est.sigma : this.priorSdMs; }
  get pairs(): number { return this.n; }
  get applied(): boolean { return this.isApplied; }

  private solve(): void {
    const n = this.n;
    if (n < 3) return;
    // Moments about the means: the line's slope with its intercept free.
    const sxxC = this.sxx - this.sx * this.sx / n;
    const sxyC = this.sxy - this.sx * this.sy / n;
    const syyC = this.syy - this.sy * this.sy / n;
    if (!(sxxC > 0)) return;
    const tau = sxyC / sxxC;
    const rss = Math.max(0, syyC - sxyC * sxyC / sxxC);
    const sigma = Math.sqrt(rss / (n - 2) / sxxC);
    this.est = { tau, sigma };
    if (n >= LATENCY_APPLY_PAIRS && sigma < LATENCY_APPLY_SIGMA_MS) this.isApplied = true;
  }
}

// ---- NorthAnchor (4.12, RD finding 8) ---------------------------------------

function circularMeanDeg(values: readonly number[]): number {
  let s = 0, c = 0;
  for (const v of values) { s += Math.sin(v * DEG); c += Math.cos(v * DEG); }
  return wrap360(Math.atan2(s, c) / DEG);
}

function circularSdDeg(values: readonly number[]): number {
  let s = 0, c = 0;
  for (const v of values) { s += Math.sin(v * DEG); c += Math.cos(v * DEG); }
  const r = Math.hypot(s, c) / values.length;
  return r >= 1 ? 0 : Math.sqrt(-2 * Math.log(r)) / DEG;
}

/** N_eff of a series: resampled to 10 Hz by linear interpolation, N / (1 + 2 sum rho_k) with rho the autocorrelation
 *  of the resampled series, summed until the first rho_k <= 0, clamped to [1, N] and to the number of samples. N is
 *  the resampled count, so a white series sampled at 10 Hz gives about N and an Ornstein-Uhlenbeck series of
 *  correlation time tau over T seconds gives about T / (2 tau). */
function effectiveCount(ts: readonly number[], ds: readonly number[]): number {
  const n = ts.length;
  if (n < 2) return 1;
  const t0 = ts[0], m = Math.floor((ts[n - 1] - t0) / NORTH_RESAMPLE_MS) + 1;
  if (m < 3) return clamp(Math.min(m, n), 1, n);
  const x = new Float64Array(m);
  let j = 0;
  for (let k = 0; k < m; k++) {
    const t = t0 + k * NORTH_RESAMPLE_MS;
    while (j < n - 2 && ts[j + 1] <= t) j++;
    const span = ts[j + 1] - ts[j];
    const u = span > 0 ? clamp((t - ts[j]) / span, 0, 1) : 0;
    x[k] = ds[j] + u * (ds[j + 1] - ds[j]);
  }
  let mean = 0;
  for (let k = 0; k < m; k++) mean += x[k] / m;
  let c0 = 0;
  for (let k = 0; k < m; k++) { x[k] -= mean; c0 += x[k] * x[k]; }
  if (!(c0 > 0)) return clamp(Math.min(m, n), 1, n);
  let sum = 0;
  for (let lag = 1; lag < m; lag++) {
    let c = 0;
    for (let k = 0; k + lag < m; k++) c += x[k] * x[k + lag];
    const rho = c / c0;
    if (rho <= 0) break;
    sum += rho;
  }
  return clamp(m / (1 + 2 * sum), 1, Math.min(m, n));
}

/** The compass offset of the scan frame. Each absolute sample's heading is stored against G's heading at the same
 *  time; the estimate is the circular median of o = abs - pred - corrYawAt(t), then the circular mean within 8 degrees
 *  of it, with the circular sd of those samples as the spread. Its sigma is honest about correlated compass noise:
 *  spread / sqrt(N_eff), and never under 2 degrees until a landmark says otherwise (4.12). Stable needs inliers that
 *  are at least 85 % of the samples as well as a steady fit. Magnetic: declination is the caller's, and never enters
 *  here. */
export class NorthAnchor {
  private ts: number[] = [];
  private os: number[] = [];
  private manualDeg = 0;
  private nudged = false;

  add(t: number, absHeadingDeg: number, predHeadingDeg: number): void {
    if (!finite(t) || !finite(absHeadingDeg) || !finite(predHeadingDeg)) return;
    this.ts.push(t);
    this.os.push(wrap360(absHeadingDeg - predHeadingDeg));
  }

  shift(deg: number): void {
    if (!finite(deg)) return;
    this.manualDeg += deg;
    this.nudged = true;
  }

  estimate(corrYawAt?: (t: number) => number): NorthEstimate | null {
    const n = this.ts.length;
    if (!n) return null;
    const order = this.ts.map((_, i) => i).sort((a, b) => this.ts[a] - this.ts[b]);
    const t = order.map(i => this.ts[i]);
    const o = order.map(i => wrap360(this.os[i] - (corrYawAt ? corrYawAt(this.ts[i]) : 0)));
    // The circular median: the linear median of the deviations about the circular mean, which is the same thing for
    // any distribution narrower than a half circle and costs a sort instead of N^2. The lower median, so that it is a
    // sample: the average of two middle samples in two clusters 20 degrees apart would have no sample within 8.
    const centre = circularMeanDeg(o);
    const devs = o.map(v => wrap180(v - centre)).sort((a, b) => a - b);
    const med = wrap360(centre + devs[(n - 1) >> 1]);
    const inT: number[] = [], inO: number[] = [];
    for (let i = 0; i < n; i++) if (Math.abs(wrap180(o[i] - med)) <= NORTH_INLIER_DEG) { inT.push(t[i]); inO.push(o[i]); }
    const mean = circularMeanDeg(inO);
    const spread = circularSdDeg(inO);
    const dev = inO.map(v => wrap180(v - mean));
    const nEff = effectiveCount(inT, dev);
    return {
      offsetDeg: wrap360(mean + this.manualDeg),
      sigmaDeg: Math.max(spread / Math.sqrt(nEff), NORTH_SIGMA_FLOOR_DEG),
      spreadDeg: spread,
      samples: n,
      nEff,
      stable: inT.length >= NORTH_STABLE_INLIER_SHARE * n && stableOffset(inT, dev),
      source: this.nudged ? 'manual' : 'scan',
    };
  }
}

/** A linear fit of o(t): a slope above 3 deg/min or a residual spread above 3 degrees marks north unstable. Fewer than
 *  three samples, or all at one time, is no evidence of stability. */
function stableOffset(ts: readonly number[], ds: readonly number[]): boolean {
  const n = ts.length;
  if (n < 3) return false;
  let mt = 0, md = 0;
  for (let i = 0; i < n; i++) { mt += ts[i] / n; md += ds[i] / n; }
  let stt = 0, std = 0, sdd = 0;
  for (let i = 0; i < n; i++) { const a = ts[i] - mt, b = ds[i] - md; stt += a * a; std += a * b; sdd += b * b; }
  if (!(stt > 0)) return false;
  const slopePerMin = std / stt * 60000;
  const residual = Math.sqrt(Math.max(0, sdd - std * std / stt) / (n - 2));
  return Math.abs(slopePerMin) <= NORTH_UNSTABLE_SLOPE_DEG_MIN && residual <= NORTH_UNSTABLE_RESIDUAL_DEG;
}
