// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T06: PoseTrack (the predictors, the axis mapping, rates and the sensor facts), LatencyEstimator and NorthAnchor
// (SPEC-v2 4.5, 4.12, the poseTrack, LatencyEstimator and NorthAnchor rows of 7.2).
//
// Mutants this file must catch, each by the test named:
// - nearest sample instead of slerp: `slerp between samples 16 ms apart` (0.24 degrees at 30 deg/s against 0.05);
// - `canPredict` requiring a relative sample: `canPredict from a still absolute-only stream`;
// - tilt correction also applied to yaw in absolute-gyro: `absolute-gyro: a 5-degree heading sinusoid`;
// - the latency intercept removed: `LatencyEstimator recovers an injected 80 ms` (the constant offset biases an
//   origin fit by about 24 ms, because the pairs' dOmega has a non-zero mean, as a scan of starts and stops does);
// - circular mean replaced by an arithmetic mean: `NorthAnchor: circular mean across the 359/1 wrap`;
// - the 2-degree sigma floor removed: `NorthAnchor: sigmaDeg is never below 2`.
// Two more, run with them: the latency regressor on |dOmega| (7.2's "regressor on absolute omega", as near as the
// estimator's inputs allow) fails the 80 ms test, and an averaged circular median fails the two-cluster case of the
// outliers test (no sample within 8 degrees of a median between the clusters, so the spread was NaN).
// The wave 1 fix round (SPEC-v2 12.4, rulings S6, S10 and S11) adds:
// - the quiet-gyro clause removed from the mode rule: `a still phone with a silent relative stream` (S6);
// - `rateAt` over two consecutive samples: `rateAt with no motion sample reads a chord` (S10);
// - the 85 % inlier share removed from stability: `NorthAnchor: a 60/40 compass split` (S11);
// and the reviewer's surviving mutants, each by the case named: stale intervals counted whatever the rate (O1,
// `intervals during motion only`), a turning handoff into absolute-only without the median of 3 (O2, `a moving phone
// with a dead relative stream`), predictAt before the oldest kept sample held instead of null (O8, `before the oldest
// kept sample`), and the tilt correction read from the gyro state instead of G at the sample's time (O9, `nodding`).
//
// The streams are generated from an exact trajectory: a camera pose (azimuth, altitude, roll) as a function of time,
// turned into W3C alpha, beta and gamma, and a body rate by central difference, which is what rotationRate is (the
// W3C order is the rate about device x, y and z, and the device frame is the camera frame at screen angle 0).
import assert from 'node:assert/strict';
import { DEG, lookBasis, type V3 } from '../../photosphereGeometry';
import {
  angleBetweenDeg, deviceOrientationFromQuat, elevationDeg, expSO3, headingDeg, logSO3, qinv, qmul, qrotate,
  quatFromBasis, rollDeg, worldYaw,
} from '../rotation';
import { CHORD_MAX_MS, CHORD_MIN_MS, LatencyEstimator, NorthAnchor, PoseTrack, fitAxisMapping } from '../poseTrack';
import type { MotionIn, OrientationIn, Quat } from '../types';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const near = (a: number, b: number, tol: number, what = '') => assert.ok(Math.abs(a - b) < tol, `${what} ${a} != ${b} (tol ${tol})`);
/** Difference of two angles in degrees, wrapped to [0, 180]. */
const angDiff = (a: number, b: number) => Math.abs((((a - b) % 360) + 540) % 360 - 180);
const wrap360 = (deg: number) => ((deg % 360) + 360) % 360;
/** Signed difference a - b in (-180, 180]. */
const sDiff = (a: number, b: number) => 180 - ((((180 - (a - b)) % 360) + 360) % 360);

/** mulberry32: a seeded generator, so every random case below is the same case every run. */
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6D2B79F5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const gauss = (r: () => number) => Math.sqrt(-2 * Math.log(1 - r())) * Math.cos(2 * Math.PI * r());

// ---- Trajectories and streams ----------------------------------------------

interface Pose { az: number; alt: number; roll: number }
type Traj = (t: number) => Pose;

const poseQ = (p: Pose): Quat => qmul(quatFromBasis(lookBasis(p.az, p.alt)), expSO3([0, 0, p.roll * DEG]));
/** The relative stream adds yaw0 to alpha, which is the same attitude turned about the vertical by -yaw0 in azimuth. */
const relTruth = (traj: Traj, t: number, yaw0: number): Quat => qmul(worldYaw(-yaw0), poseQ(traj(t)));

/** Camera-frame angular velocity at t, deg/s, by central difference; a still camera reads exact zeros. */
function bodyRateDeg(traj: Traj, t: number): V3 {
  const h = 0.5, w = logSO3(qmul(qinv(poseQ(traj(t - h))), poseQ(traj(t + h)))), k = 1000 / (2 * h) / DEG;
  return [w[0] * k, w[1] * k, w[2] * k].map(v => (Math.abs(v) < 1e-6 ? 0 : v)) as V3;
}

const steady = (az0: number, t0: number, rate: number, alt = 25): Traj => t => ({ az: az0 + rate * (t - t0) / 1000, alt, roll: 0 });
const still = (p: Pose): Traj => () => p;
/** Whole cycles in 5 s, so at t = 5000 the pose is az0 + 100, altitude 25, roll 0, with a pan rate of 20 deg/s.
 *  The speed varies (20 +- 4 deg/s), the pitch nods (+-4 degrees at 1 Hz) and the roll rocks: all three body axes
 *  are excited, which is what a hand-held pan with tremor does. */
function panA(az0: number): Traj {
  const pw = 1.25, palt = 1.0, proll = 1.25;
  return t => {
    const s = t / 1000;
    return {
      az: az0 + 20 * s + 4 * pw / (2 * Math.PI) * (1 - Math.cos(2 * Math.PI * s / pw)),
      alt: 25 + 4 * Math.sin(2 * Math.PI * s / palt),
      roll: 3 * Math.sin(2 * Math.PI * s / proll),
    };
  };
}
function piecewise(segs: readonly [number, Traj][]): Traj {
  return t => {
    let f = segs[0][1];
    for (const [t0, g] of segs) if (t >= t0) f = g;
    return f(t);
  };
}

type Ev = { kind: 'o'; e: OrientationIn } | { kind: 'm'; e: MotionIn };
interface Streams {
  /** `pumpAge`: the pump reads the sensor this many ms late and stamps its tick to the ms, as SIM's
   *  `_orientation_stream` does, so a reading is the attitude at `tick - age` under the timestamp `round(tick)`. */
  rel?: { hz: number; yaw0?: number; compass?: boolean; flagless?: boolean; pumpAge?: () => number };
  /** `offsetMs`: the absolute stream ticks this long after the motion stream's grid. */
  abs?: { hz: number; err?: (t: number, p: Pose) => number; altErr?: number; tiltNoise?: () => number; chromiumDup?: boolean; offsetMs?: number };
  motion?: { hz: number; map?: (w: V3) => [number, number, number] };
}
function ticks(t0: number, t1: number, hz: number): number[] {
  const out: number[] = [], period = 1000 / hz;
  for (let k = Math.ceil(t0 / period - 1e-9); k * period < t1 - 1e-9; k++) out.push(k * period);
  return out;
}
/** Events on [t0, t1) in delivery order: at one time, relative before absolute before its duplicate before motion. */
function events(traj: Traj, t0: number, t1: number, s: Streams): Ev[] {
  const evs: { t: number; order: number; ev: Ev }[] = [];
  if (s.rel) {
    const rel = s.rel;
    for (const tick of ticks(t0, t1, rel.hz)) {
      const t = rel.pumpAge ? Math.round(tick) : tick;
      const p = traj(rel.pumpAge ? tick - rel.pumpAge() : tick), d = deviceOrientationFromQuat(poseQ(p));
      evs.push({ t, order: 0, ev: { kind: 'o', e: {
        t, type: 'deviceorientation', alpha: wrap360(d.alpha + (rel.yaw0 ?? 0)), beta: d.beta, gamma: d.gamma,
        absolute: rel.flagless ? null : false, compassHeading: rel.compass ? wrap360(p.az) : null,
      } } });
    }
  }
  if (s.abs) {
    const abs = s.abs, off = abs.offsetMs ?? 0;
    for (const t of ticks(t0 - off, t1 - off, abs.hz).map(x => x + off)) {
      const p = traj(t), n = abs.tiltNoise ?? (() => 0);
      const d = deviceOrientationFromQuat(poseQ({ az: p.az, alt: p.alt + (abs.altErr ?? 0) + n(), roll: p.roll + n() }));
      // A compass error of e degrees in azimuth is -e in alpha (alpha runs counter-clockwise).
      const e: OrientationIn = {
        t, type: 'deviceorientationabsolute', alpha: wrap360(d.alpha - (abs.err ? abs.err(t, p) : 0)), beta: d.beta,
        gamma: d.gamma, absolute: true, compassHeading: null,
      };
      evs.push({ t, order: 1, ev: { kind: 'o', e } });
      if (abs.chromiumDup) evs.push({ t, order: 2, ev: { kind: 'o', e: { ...e, type: 'deviceorientation' } } });
    }
  }
  if (s.motion) {
    const motion = s.motion;
    for (const t of ticks(t0, t1, motion.hz)) {
      const w = bodyRateDeg(traj, t), r = motion.map ? motion.map(w) : w;
      evs.push({ t, order: 3, ev: { kind: 'm', e: { t, rate: { alpha: r[0], beta: r[1], gamma: r[2] } } } });
    }
  }
  evs.sort((a, b) => a.t - b.t || a.order - b.order);
  return evs.map(x => x.ev);
}
function feed(track: PoseTrack, evs: readonly Ev[]): void {
  for (const x of evs) { if (x.kind === 'o') track.onOrientation(x.e); else track.onMotion(x.e); }
}
/** Delivers `evs` in order up to each time asked for, so a case can ask as a scanner does, while the events arrive:
 *  PoseTrack keeps only the last 5 s of motion samples, so a question about an earlier time cannot be asked later. */
function player(track: PoseTrack, evs: readonly Ev[]): (t: number) => void {
  let i = 0;
  return t => { while (i < evs.length && evs[i].e.t <= t) feed(track, [evs[i++]]); };
}
const lastTick = (t1: number, hz: number) => { const ts = ticks(0, t1, hz); return ts[ts.length - 1]; };
const orient = (t: number, alpha: number | null, beta: number | null, gamma: number | null,
  type: OrientationIn['type'] = 'deviceorientation', absolute: boolean | null = false): OrientationIn =>
  ({ t, type, alpha, beta, gamma, absolute, compassHeading: null });
const motionEv = (t: number, a: number | null, b: number | null, g: number | null): MotionIn => ({ t, rate: { alpha: a, beta: b, gamma: g } });

// ---- PoseTrack: interpolation, hold, extrapolation and the stale limit -----

test('slerp between samples 16 ms apart at 30 deg/s is within 0.05 degrees of the truth', () => {
  const traj = steady(40, 0, 30, 20), yaw0 = 123, track = new PoseTrack();
  // 62.5 Hz: samples exactly 16 ms apart. Halfway between two of them the nearer sample is 30 x 0.008 = 0.24 off.
  feed(track, events(traj, 0, 1000, { rel: { hz: 62.5, yaw0 } }));
  let worst = 0;
  for (let k = 10; k < 60; k++) {
    for (const u of [0.25, 0.5, 0.75]) {
      const t = k * 16 + u * 16, p = track.predictAt(t);
      assert.ok(p, `prediction at ${t}`);
      assert.equal(p.extrapolatedMs, 0); assert.equal(p.held, false); assert.equal(p.mode, 'relative');
      worst = Math.max(worst, angleBetweenDeg(p.q, relTruth(traj, t, yaw0)));
    }
  }
  assert.ok(worst < 0.05, `worst slerp error ${worst} degrees`);
});

test('a quiet gyro holds the newest sample through change-driven silence with no time limit, and only while it vouches (S6)', () => {
  const traj = piecewise([[0, steady(0, 0, 20)], [1000, still({ az: 20, alt: 25, roll: 0 })]]), track = new PoseTrack();
  // The relative stream is change-driven: silent once the phone stops. The gyro keeps reporting, exact zeros, to 12 s.
  const upTo = player(track, [...events(traj, 0, 1000, { rel: { hz: 60 }, motion: { hz: 60 } }), ...events(traj, 1000, 12000, { motion: { hz: 60 } })]);
  const tn = lastTick(1000, 60);
  upTo(tn);
  const last = track.predictAt(tn)!;
  // v2's 2000 ms cap is gone: 10 s of silence is held.
  for (const g of [100, 2000, 2001, 10000]) {
    upTo(tn + g);
    const p = track.predictAt(tn + g);
    assert.ok(p, `held at +${g} ms`);
    assert.equal(p.held, true); near(p.extrapolatedMs, g, 1e-9, 'extrapolatedMs');
    assert.ok(angleBetweenDeg(p.q, last.q) < 1e-9, 'the held pose is the newest sample');
  }
  assert.equal(track.facts().staleRefusals, 0);
  // The gyro's last sample: 99 ms after it, it still vouches; 101 ms after, nothing does and the gap is far past the
  // stale limit.
  const tm = lastTick(12000, 60);
  upTo(tm);
  assert.ok(track.predictAt(tm + 99), 'a motion sample within 100 ms');
  assert.equal(track.predictAt(tm + 101), null);
  assert.equal(track.facts().staleRefusals, 1);
  assert.equal(track.mode, 'relative', 'silence is not a mode change');
});

test('body-frame extrapolation once the axis mapping is confirmed; a hold within the stale limit before it', () => {
  const yaw0 = 40, traj = piecewise([[0, panA(10)], [5000, steady(110, 5000, 20)]]);
  // Before the fit has its pairs: no per-axis rate, so a moving gap is held, not extrapolated.
  const early = new PoseTrack();
  feed(early, events(traj, 0, 1000, { rel: { hz: 60, yaw0 }, motion: { hz: 60 } }));
  const t0 = lastTick(1000, 60);
  assert.equal(early.omegaBodyAt(t0), null);
  const h = early.predictAt(t0 + 40)!;
  assert.equal(h.held, true); near(h.extrapolatedMs, 40, 1e-9);
  assert.ok(angleBetweenDeg(h.q, early.predictAt(t0)!.q) < 1e-9);

  const track = new PoseTrack();
  feed(track, events(traj, 0, 5500, { rel: { hz: 60, yaw0 }, motion: { hz: 60 } }));
  assert.ok(track.axis?.confirmed, 'the run-time fit confirmed the mapping during the pan');
  const tn = lastTick(5500, 60), p = track.predictAt(tn + 40)!;
  assert.equal(p.held, false); near(p.extrapolatedMs, 40, 1e-9);
  const err = angleBetweenDeg(p.q, relTruth(traj, tn + 40, yaw0));
  assert.ok(err < 0.02, `extrapolation error ${err} degrees (a hold would be 0.8)`);
  // Beyond the stale limit (50 ms on a 60 Hz stream): null, counted.
  assert.equal(track.predictAt(tn + 60), null);
  assert.equal(track.facts().staleRefusals, 1);
});

test('stale limit: 50 ms at 60 Hz, 75 ms at 20 Hz, 150 ms at 10 Hz and slower; null beyond it, counted', () => {
  // 1.5 x the median interval during motion, clamped to [50, 150] (4.5). 7.2's "150 ms on a 20 Hz stream" is the clamp
  // reached by a slower stream: by the formula 20 Hz gives 1.5 x 50 = 75.
  for (const [hz, limit] of [[60, 50], [20, 75], [10, 150], [5, 150]] as const) {
    const track = new PoseTrack();
    feed(track, events(steady(0, 0, 20), 0, 3000, { rel: { hz } }));
    const tn = lastTick(3000, hz);
    const inside = track.predictAt(tn + limit - 0.5);
    assert.ok(inside, `${hz} Hz: held just inside ${limit} ms`);
    assert.equal(inside.held, true, 'no gyro: a hold');
    assert.equal(track.predictAt(tn + limit + 0.5), null, `${hz} Hz: null beyond ${limit} ms`);
    const f = track.facts();
    assert.equal(f.staleRefusals, 1, `${hz} Hz: the refusal is counted`);
    assert.equal(f.staleLimitMs.n, 2); assert.equal(f.staleLimitMs.p50, limit); assert.equal(f.staleLimitMs.max, limit);
  }
});

test('stale limit: intervals during motion only, so a slow change-driven creep does not stretch it (O1)', () => {
  // 1 s at 20 deg/s at 60 Hz, then 2.2 s creeping at 0.5 deg/s, which a change-driven stream reports every 0.1 degree:
  // one sample every 200 ms. No gyro. The creep's intervals are under 2 deg/s, so the 2 s window before the newest
  // sample holds no interval during motion and the limit is the default 1.5 x 33, clamped to 50. Counting the creep's
  // 200 ms intervals would make it 150.
  const traj = piecewise([[0, steady(0, 0, 20)], [1000, steady(20, 1000, 0.5)]]), track = new PoseTrack();
  feed(track, [...events(traj, 0, 1000, { rel: { hz: 60 } }), ...events(traj, 1000, 3300, { rel: { hz: 5 } })]);
  const tn = lastTick(3300, 5);
  assert.equal(tn, 3200);
  const inside = track.predictAt(tn + 49);
  assert.ok(inside, 'held just inside 50 ms'); assert.equal(inside.held, true);
  assert.equal(track.predictAt(tn + 51), null, 'null beyond 50 ms');
  const f = track.facts();
  assert.equal(f.staleRefusals, 1); assert.equal(f.staleLimitMs.p50, 50); assert.equal(f.staleLimitMs.max, 50);
});

test('predictAt before the oldest kept sample is null, and not a stale refusal (O8)', () => {
  // 50 Hz, so that every sample time is a whole number of ms.
  const traj = steady(0, 0, 20), track = new PoseTrack(), upTo = player(track, events(traj, 1000, 9000, { rel: { hz: 50 } }));
  upTo(2000);
  // Before the first sample there is nothing to hold.
  assert.equal(track.predictAt(999), null);
  assert.ok(track.predictAt(1000), 'the first sample itself');
  // G keeps 5 s: with the newest sample at 8980 the oldest kept is at 3980.
  upTo(9000);
  assert.equal(track.predictAt(3900), null);
  const p = track.predictAt(4100);
  assert.ok(p, 'inside the kept history'); assert.equal(p.held, false);
  assert.ok(angleBetweenDeg(p.q, relTruth(traj, 4100, 0)) < 0.01);
  assert.equal(track.facts().staleRefusals, 0);
});

// ---- PoseTrack: classification, duplicates, blocked, canPredict ------------

test('classification: relative and absolute streams, a flagless deviceorientation is relative, Chromium absolute-only duplicates are dropped', () => {
  const traj = steady(30, 0, 20);
  // A phone with both streams: deviceorientation absolute:false and deviceorientationabsolute absolute:true.
  const both = new PoseTrack();
  feed(both, events(traj, 0, 1000, { rel: { hz: 60, yaw0: 123 }, abs: { hz: 60 } }));
  let f = both.facts();
  assert.deepEqual(f.events.map(e => e.type), ['deviceorientation', 'deviceorientationabsolute', 'devicemotion']);
  assert.deepEqual({ ...f.events[0], type: undefined }, { type: undefined, total: 60, absoluteTrue: 0, absoluteFalse: 60, absoluteMissing: 0, nullReadings: 0, duplicates: 0 });
  assert.deepEqual({ ...f.events[1], type: undefined }, { type: undefined, total: 60, absoluteTrue: 60, absoluteFalse: 0, absoluteMissing: 0, nullReadings: 0, duplicates: 0 });
  assert.equal(both.mode, 'relative');
  // Every absolute sample is a north sample against G: o = abs - pred = yaw0 for the relative frame.
  const n = both.north.estimate()!;
  assert.equal(n.samples, 60); near(angDiff(n.offsetDeg, 123), 0, 1e-6, 'offset');

  // Chromium's absolute-only shape: every absolute event arrives again as deviceorientation with absolute:true.
  const chromium = new PoseTrack();
  feed(chromium, events(traj, 0, 1000, { abs: { hz: 60, chromiumDup: true } }));
  f = chromium.facts();
  assert.deepEqual({ ...f.events[0], type: undefined }, { type: undefined, total: 60, absoluteTrue: 60, absoluteFalse: 0, absoluteMissing: 0, nullReadings: 0, duplicates: 60 });
  assert.equal(f.events[1].duplicates, 0);
  assert.equal(chromium.mode, 'absolute-only', 'absolute: true is never relative');
  assert.equal(chromium.north.estimate()!.samples, 60, 'a duplicate is not a second north sample');

  // No absolute flag at all is relative.
  const flagless = new PoseTrack();
  feed(flagless, events(traj, 0, 200, { rel: { hz: 60, flagless: true } }));
  assert.equal(flagless.mode, 'relative'); assert.equal(flagless.facts().events[0].absoluteMissing, 12);

  // A deviceorientation flagged absolute that does not repeat the previous absolute sample is an absolute sample.
  const own = new PoseTrack();
  own.onOrientation(orient(0, 10, 100, 1, 'deviceorientation', true));
  own.onOrientation(orient(16, 11, 100, 1, 'deviceorientation', true));
  assert.equal(own.mode, 'absolute-only'); assert.equal(own.facts().events[0].duplicates, 0);
  // The same values again from the SAME listener within 2 ms are a reading, not a duplicate; from the other one,
  // within 1e-9 and 2 ms, a duplicate; 3 ms later, a reading again.
  const dup = new PoseTrack();
  dup.onOrientation(orient(0, 10, 100, 1, 'deviceorientationabsolute', true));
  dup.onOrientation(orient(1, 10, 100, 1, 'deviceorientationabsolute', true));
  dup.onOrientation(orient(2, 10, 100, 1, 'deviceorientation', true));
  dup.onOrientation(orient(5, 10, 100, 1, 'deviceorientation', true));
  f = dup.facts();
  assert.equal(f.events[0].duplicates, 1); assert.equal(f.events[1].duplicates, 0);
  assert.equal(dup.north.estimate()!.samples, 3);
});

test('an all-null first event and nothing else for 1 s is blocked; a reading clears it', () => {
  const track = new PoseTrack();
  track.onOrientation(orient(100, null, null, null));
  assert.equal(track.blocked, false);
  assert.equal(track.predictAt(1099), null);
  assert.equal(track.blocked, false, 'not before 1 s');
  assert.equal(track.predictAt(1100), null);
  assert.equal(track.blocked, true);
  assert.equal(track.canPredict, false);
  let f = track.facts();
  assert.equal(f.blocked, true); assert.equal(f.events[0].nullReadings, 1); assert.equal(f.staleRefusals, 0, 'no stream is not stale');
  track.onOrientation(orient(1500, 10, 100, 1));
  assert.equal(track.blocked, false); assert.equal(track.canPredict, true);

  // A reading within the second: never blocked.
  const ok = new PoseTrack();
  ok.onOrientation(orient(100, null, null, null));
  ok.onMotion(motionEv(600, 0.3, 0, 0));
  ok.predictAt(3000);
  assert.equal(ok.blocked, false);
  // A first event with readings, then all-null ones: not the blocked shape.
  const later = new PoseTrack();
  later.onOrientation(orient(100, 10, 100, 1));
  later.onOrientation(orient(200, null, null, null));
  later.predictAt(3000);
  f = later.facts();
  assert.equal(f.blocked, false); assert.equal(f.events[0].nullReadings, 1);
});

test('canPredict from a still absolute-only stream (RS M4)', () => {
  const r = rng(11), pose: Pose = { az: 75, alt: 25, roll: 0 };
  const track = new PoseTrack();
  // A still phone with only Chromium's absolute-only shape: the compass wanders, nothing is relative, no gyro.
  feed(track, events(still(pose), 0, 500, { abs: { hz: 60, chromiumDup: true, err: () => 0.3 * gauss(r) } }));
  assert.equal(track.canPredict, true);
  assert.equal(track.mode, 'absolute-only');
  const p = track.predictAt(250);
  assert.ok(p, 'a prediction inside the stream');
  near(elevationDeg(p.q), 25, 1e-6, 'elevation');
  assert.equal(track.latch(500), 'absolute-only');
  // One still reading is enough.
  const one = new PoseTrack();
  one.onOrientation(orient(0, 10, 115, 0, 'deviceorientationabsolute', true));
  assert.equal(one.canPredict, true); assert.ok(one.predictAt(0));
});

// ---- PoseTrack: modes, the latch and the handoff ---------------------------

test('per-sample mode before the latch; the latch; handoffs with a heading jump under 0.01 degrees', () => {
  // Still phone, a relative stream whose yaw zero is 77 degrees from the compass's, the compass exact, no gyro.
  const pose: Pose = { az: 30, alt: 25, roll: 0 }, traj = still(pose), yaw0 = 77;
  const track = new PoseTrack();
  assert.equal(track.mode, 'none'); assert.equal(track.predictAt(0), null);
  const relAbs = { rel: { hz: 60, yaw0 }, abs: { hz: 60 } }, absOnly = { abs: { hz: 60 } };
  feed(track, events(traj, 0, 1000, relAbs));
  assert.equal(track.mode, 'relative');
  feed(track, events(traj, 1000, 2000, absOnly));
  assert.equal(track.mode, 'absolute-only', 'the relative stream silent for 500 ms');
  const lastRel = lastTick(1000, 60);
  const jump1 = angDiff(headingDeg(track.predictAt(lastRel)!.q), headingDeg(track.predictAt(1990)!.q));
  assert.ok(jump1 < 0.01, `heading jump ${jump1} at relative -> absolute-only (the yaw zeros differ by ${yaw0})`);
  feed(track, events(traj, 2000, 2500, relAbs));
  assert.equal(track.mode, 'relative');
  assert.ok(angDiff(headingDeg(track.predictAt(2490)!.q), headingDeg(track.predictAt(lastRel)!.q)) < 0.01);
  assert.deepEqual(track.facts().modeChanges, [], 'changes before the latch are not recorded');
  assert.equal(track.facts().modeAtBegin, null);
  assert.equal(track.latch(2500), 'relative');
  feed(track, events(traj, 2500, 3500, absOnly));
  const f = track.facts();
  assert.equal(f.modeAtBegin, 'relative');
  assert.equal(f.modeChanges.length, 1);
  const c = f.modeChanges[0];
  assert.equal(c.from, 'relative'); assert.equal(c.to, 'absolute-only');
  assert.ok(c.tMs > 2983 && c.tMs < 3020, `change at ${c.tMs}`);
  assert.ok(angDiff(headingDeg(track.predictAt(lastTick(2500, 60))!.q), headingDeg(track.predictAt(3490)!.q)) < 0.01);

  // Turning: absolute-only (no relative stream) hands off to absolute-gyro once the run-time fit confirms the mapping.
  const traj2 = panA(10), turn = new PoseTrack();
  const evs = events(traj2, 0, 5000, { abs: { hz: 60 }, motion: { hz: 60 } });
  feed(turn, evs.filter(x => x.e.t < 100));
  assert.equal(turn.latch(100), 'absolute-only');
  feed(turn, evs.filter(x => x.e.t >= 100));
  const changes = turn.facts().modeChanges;
  assert.equal(changes.length, 1);
  assert.equal(changes[0].from, 'absolute-only'); assert.equal(changes[0].to, 'absolute-gyro');
  assert.equal(turn.mode, 'absolute-gyro');
  const ts = changes[0].tMs, dt = 1000 / 60, h = (t: number) => headingDeg(turn.predictAt(t)!.q), az = (t: number) => traj2(t).az;
  // Before the change G's heading is the median of three, one sample behind: G(t_k) = az(t_k-1) + y.
  const y = sDiff(h(ts - dt), az(ts - 2 * dt));
  near(sDiff(h(ts - 2 * dt), az(ts - 3 * dt)), y, 1e-6, 'the median-of-3 lag');
  // The first absolute-gyro sample continues that line, and the gyro carries it on from there.
  const jump = Math.abs(sDiff(h(ts), az(ts - dt) + y));
  assert.ok(jump < 0.01, `heading jump ${jump} at absolute-only -> absolute-gyro`);
  for (let k = 1; k <= 5; k++) near(sDiff(h(ts + k * dt), h(ts)), az(ts + k * dt) - az(ts), 0.01, `gyro step ${k}`);
});

/** The gyro as Chromium reports a still phone (S21): bias 0.01 and noise 0.018 deg/s, rounded to 0.1 deg/s, so nearly
 *  every still sample is an exact zero triple and the rest read 0.1. */
const chromiumGyro = (r: () => number) => (w: V3): [number, number, number] =>
  w.map(v => Math.round((v + 0.01 + 0.018 * gauss(r)) * 10) / 10 + 0) as [number, number, number];

test('a still phone with a silent relative stream, a quiet gyro and a 3-degree compass stays relative for 10 s, before and after the latch (S6)', () => {
  // T06's finding: by the bare 500 ms window the phone fell to absolute-only and G's yaw followed the compass's
  // Ornstein-Uhlenbeck wander (3 degrees, tau 5 s: 2.85 degrees sd over 3 s) before the mapping could confirm. A
  // short pan first, so the relative stream has spoken and the gyro has read something other than zeros.
  const stop = 1000, traj = piecewise([[0, steady(0, 0, 20)], [stop, still({ az: 20, alt: 25, roll: 0 })]]);
  const ou = ouSeries(51, 22, 60, 5, 3), err = (t: number) => ou[Math.round(t * 60 / 1000)];
  const gyro = chromiumGyro(rng(52)), track = new PoseTrack();
  const upTo = player(track, [
    ...events(traj, 0, stop + 1, { rel: { hz: 60, yaw0: 77 }, abs: { hz: 60, err }, motion: { hz: 60, map: gyro } }),
    ...events(traj, stop + 1, 21000, { abs: { hz: 60, err }, motion: { hz: 60, map: gyro } }),
  ]);
  // The relative stream's last reading is the one at the stop (tick 60, a hair after 1000 ms).
  upTo(stop + 1);
  assert.equal(track.mode, 'relative');
  const ref = headingDeg(track.predictAt(stop + 1)!.q);
  const hold = (from: number, to: number) => {
    let worst = 0;
    for (let t = from; t <= to; t += 50) {
      upTo(t);
      assert.equal(track.mode, 'relative', `mode at ${t}`);
      const p = track.predictAt(t);
      assert.ok(p, `a prediction at ${t}`); assert.equal(p.held, true);
      worst = Math.max(worst, angDiff(headingDeg(p.q), ref));
    }
    return worst;
  };
  const before = hold(stop + 50, 11000);
  assert.ok(before < 0.01, `predicted yaw moved ${before} degrees before the latch`);
  assert.equal(track.latch(11000), 'relative');
  const after = hold(11050, 21000);
  assert.ok(after < 0.01, `predicted yaw moved ${after} degrees after the latch`);
  const f = track.facts();
  assert.equal(f.modeAtBegin, 'relative'); assert.deepEqual(f.modeChanges, []); assert.equal(f.staleRefusals, 0);
  assert.equal(f.axis?.confirmed ?? false, false, 'the mapping never confirmed');
  // The compass did wander: what G's yaw would have followed.
  const seen = ou.slice(0, 1261);
  assert.ok(Math.max(...seen) - Math.min(...seen) > 3, 'the compass wandered by more than 3 degrees');
});

test('a moving phone with a dead relative stream still hands off: to absolute-gyro, and to absolute-only with the median of 3 (S6, O2)', () => {
  // The gyro shows the motion, so it does not vouch for the silence: the relative stream loses its 500 ms window.
  // absolute-gyro: the mapping confirmed during the pan, then the relative stream stops while the phone turns on.
  const traj = piecewise([[0, panA(10)], [5000, steady(110, 5000, 20)]]), gyroTurn = new PoseTrack();
  feed(gyroTurn, events(traj, 0, 5000, { rel: { hz: 60 }, abs: { hz: 60 }, motion: { hz: 60 } }));
  assert.ok(gyroTurn.axis?.confirmed);
  assert.equal(gyroTurn.latch(5000), 'relative');
  feed(gyroTurn, events(traj, 5000, 6500, { abs: { hz: 60 }, motion: { hz: 60 } }));
  const lastRel = lastTick(5000, 60);
  let changes = gyroTurn.facts().modeChanges;
  assert.equal(changes.length, 1);
  assert.equal(changes[0].from, 'relative'); assert.equal(changes[0].to, 'absolute-gyro');
  assert.ok(changes[0].tMs > lastRel + 499 && changes[0].tMs < lastRel + 520, `change at ${changes[0].tMs}`);
  assert.ok(gyroTurn.predictAt(6480), 'predicting after the handoff');

  // absolute-only: a steady pan excites one body axis, so the mapping never confirms; the gyro is still a magnitude.
  const pan = steady(0, 0, 20), turn = new PoseTrack();
  feed(turn, events(pan, 0, 2000, { rel: { hz: 60 }, abs: { hz: 60 }, motion: { hz: 60 } }));
  assert.equal(turn.latch(2000), 'relative');
  feed(turn, events(pan, 2000, 3500, { abs: { hz: 60 }, motion: { hz: 60 } }));
  assert.equal(turn.axis?.confirmed ?? false, false);
  changes = turn.facts().modeChanges;
  assert.equal(changes.length, 1);
  assert.equal(changes[0].from, 'relative'); assert.equal(changes[0].to, 'absolute-only');
  const ts = changes[0].tMs, dt = 1000 / 60, h = (t: number) => headingDeg(turn.predictAt(t)!.q);
  // The handoff starts from the median of the last three headings, one sample behind the newest, as every
  // absolute-only sample after it is: G then steps with the phone. Started from the newest heading, the first step
  // would be 0 instead of 0.33 degrees.
  for (let k = 1; k <= 5; k++) near(sDiff(h(ts + k * dt), h(ts)), 20 * k * dt / 1000, 0.01, `absolute-only step ${k}`);
});

test('absolute-gyro: a 5-degree heading sinusoid moves the predictor\'s yaw over a 60-degree turn by less than 0.3 degrees', () => {
  // Phase A confirms the mapping with all three streams; then the relative stream stops. The phone stands still for
  // 1 s, which a quiet gyro vouches for, so the relative stream stays in charge (S6); then a 60-degree turn at 20 deg/s
  // with the compass off by 5 sin(az + 40), whose error moves by 5 degrees over the turn. The moving gyro hands the
  // predictor to absolute-gyro at the turn's start, and from there yaw must come from the gyro alone. The compass's
  // tilt carries T01's 0.05 degrees of white noise, as a real one does, so the correction runs on every sample: with
  // exact tilt it would have nothing to correct and never run.
  const err = (_t: number, p: Pose) => 5 * Math.sin((p.az + 40) * DEG);
  const r = rng(41), tiltNoise = () => 0.05 * gauss(r);
  const end: Pose = { az: 170, alt: 25, roll: 0 };
  const traj = piecewise([
    [0, panA(10)], [5000, still({ az: 110, alt: 25, roll: 0 })], [6000, steady(110, 6000, 20)], [9000, still(end)],
  ]);
  const track = new PoseTrack();
  feed(track, events(traj, 0, 5000, { rel: { hz: 60, yaw0: 33 }, abs: { hz: 60, err, tiltNoise }, motion: { hz: 60 } }));
  assert.ok(track.axis?.confirmed);
  track.latch(5000);
  feed(track, events(traj, 5000, 9300, { abs: { hz: 60, err, tiltNoise }, motion: { hz: 60 } }));
  assert.equal(track.mode, 'absolute-gyro');
  const changes = track.facts().modeChanges;
  assert.equal(changes.length, 1); assert.equal(changes[0].to, 'absolute-gyro');
  const ts = changes[0].tMs;
  assert.ok(ts > 6000 && ts < 6040, `handoff at ${ts}, when the gyro first shows the turn`);
  // The truth turns 170 - az(ts) degrees from the handoff to the end (60 less the turn's first 17-33 ms).
  const dYaw = sDiff(headingDeg(track.predictAt(9200)!.q), headingDeg(track.predictAt(ts)!.q)), truth = 170 - traj(ts).az;
  assert.ok(Math.abs(dYaw - truth) < 0.3, `predictor yaw moved ${dYaw} over a ${truth}-degree turn`);

  // Tilt IS corrected: the compass reports 2 degrees more pitch than the gyro has seen; G follows at 2 % a sample.
  const before = track.predictAt(9200)!;
  feed(track, events(traj, 9300, 11300, { abs: { hz: 60, err, altErr: 2, tiltNoise }, motion: { hz: 60 } }));
  const after = track.predictAt(11280)!;
  near(elevationDeg(before.q), 25, 0.05, 'elevation before');
  assert.ok(elevationDeg(after.q) > 26.6 && elevationDeg(after.q) < 27.05, `elevation pulled to ${elevationDeg(after.q)}`);
  assert.ok(angDiff(headingDeg(after.q), headingDeg(before.q)) < 0.01, 'a pitch correction leaves yaw alone');
});

test('absolute-gyro: nodding, the tilt correction reads G at the absolute sample\'s own time (O9)', () => {
  // panA nods the pitch at up to 25 deg/s and rocks the roll. Phase A confirms the mapping; then the relative stream
  // stops while the phone nods on, and absolute-gyro takes over. The absolute stream, with exact tilt, ticks 4 ms after
  // the motion grid, so each of its samples is applied once the gyro has integrated 12.7 ms past it. Read at the
  // sample's own time, G agrees with it and there is nothing to correct. Read from the gyro state 12.7 ms later, the
  // nod itself reads as a tilt error, and 2 % of it is "corrected" away on every sample. Measured over the last 4 s:
  // 0.004 degrees read at the sample's time, 0.077 read from the gyro state.
  const traj = panA(10), track = new PoseTrack();
  feed(track, events(traj, 0, 5000, { rel: { hz: 60 }, abs: { hz: 60, offsetMs: 4 }, motion: { hz: 60 } }));
  assert.ok(track.axis?.confirmed);
  feed(track, events(traj, 5000, 12000, { abs: { hz: 60, offsetMs: 4 }, motion: { hz: 60 } }));
  assert.equal(track.mode, 'absolute-gyro');
  const up = (q: Quat) => qrotate(qinv(q), [0, 0, 1]);
  let worst = 0;
  for (let t = 8000; t < 11900; t += 1000 / 30) {
    const g = up(track.predictAt(t)!.q), u = up(poseQ(traj(t)));
    worst = Math.max(worst, Math.acos(Math.min(1, g[0] * u[0] + g[1] * u[1] + g[2] * u[2])) / DEG);
  }
  assert.ok(worst < 0.02, `G's tilt is ${worst} degrees off the truth`);
});

test('absolute-gyro learns the gyro bias while the orientation stream is still', () => {
  // A still phone in absolute-gyro whose gyro reads 0.3 deg/s about y: under the 0.5 quiet line, and 1.05 degrees of
  // heading drift over 3.5 s if it were integrated as motion. Learned at weight 0.05 a sample once the orientation
  // stream has shown under 0.5 deg/s for 500 ms, the whole drift is under 0.3 degrees and stops. The relative stream
  // stops while the phone still turns, so the predictor is absolute-gyro before the phone stands still: a quiet gyro
  // would keep a silent relative stream in charge (S6).
  const pose: Pose = { az: 130, alt: 25, roll: 0 };
  const traj = piecewise([[0, panA(10)], [5000, steady(110, 5000, 20)], [6000, still(pose)]]);
  const r = rng(42), tiltNoise = () => 0.05 * gauss(r), track = new PoseTrack();
  feed(track, events(traj, 0, 5000, { rel: { hz: 60 }, abs: { hz: 60, tiltNoise }, motion: { hz: 60 } }));
  feed(track, events(traj, 5000, 6000, { abs: { hz: 60, tiltNoise }, motion: { hz: 60 } }));
  assert.equal(track.mode, 'absolute-gyro');
  feed(track, events(traj, 6000, 10000, { abs: { hz: 60, tiltNoise }, motion: { hz: 60, map: w => [w[0], w[1] + 0.3, w[2]] } }));
  assert.equal(track.mode, 'absolute-gyro');
  const h = (t: number) => headingDeg(track.predictAt(t)!.q);
  const total = Math.abs(sDiff(h(9980), h(6600))), lastSecond = Math.abs(sDiff(h(9980), h(8980)));
  assert.ok(total < 0.3, `heading drifted ${total} degrees`);
  assert.ok(lastSecond < 0.01, `still drifting ${lastSecond} degrees in the last second`);
});

// ---- PoseTrack: gyro samples, the run-time fit, rates, tilt and facts -------

test('zero triples are dropped before a non-zero sample and accepted after one; null, non-finite and backwards samples are dropped', () => {
  const track = new PoseTrack();
  for (let t = 0; t <= 100; t += 20) track.onMotion(motionEv(t, 0, 0, 0));
  let f = track.facts();
  assert.equal(f.gyroZeroTriples, 0); assert.equal(f.gyroSeenNonZero, false);
  assert.equal(track.rateAt(100), null, 'zeros from a gyro that has never read anything else are not readings');
  track.onMotion(motionEv(120, 0.1, 0, 0));
  for (let t = 140; t <= 300; t += 20) track.onMotion(motionEv(t, 0, -0, 0));
  f = track.facts();
  assert.equal(f.gyroSeenNonZero, true); assert.equal(f.gyroZeroTriples, 9);
  assert.equal(track.rateAt(300), 0);
  track.onMotion({ t: 320, rate: null });
  track.onMotion(motionEv(340, null, 0, 0));
  track.onMotion(motionEv(360, NaN, 0, 0));
  track.onMotion(motionEv(380, Infinity, 0, 0));
  track.onMotion(motionEv(250, 50, 0, 0));   // backwards
  f = track.facts();
  assert.equal(f.events[2].total, 21); assert.equal(f.events[2].nullReadings, 4);
  assert.equal(track.rateAt(250), 0, 'the backwards sample was dropped');
});

test('the run-time fit confirms the W3C mapping and the permuted #897 shape during a pan; omegaBodyAt follows', () => {
  const traj = piecewise([[0, panA(10)], [5000, steady(110, 5000, 20)]]);
  for (const [name, map, perm] of [
    ['W3C', undefined, [0, 1, 2]],
    ['#897 (z, x, y)', (w: V3): [number, number, number] => [w[2], w[0], w[1]], [1, 2, 0]],
  ] as const) {
    const track = new PoseTrack();
    feed(track, events(traj, 0, 2000, { rel: { hz: 60 }, motion: { hz: 60, map } }));
    assert.equal(track.omegaBodyAt(1990), null, `${name}: no per-axis rate before the mapping is confirmed`);
    feed(track, events(traj, 2000, 5500, { rel: { hz: 60 }, motion: { hz: 60, map } }));
    const a = track.axis!;
    assert.ok(a.confirmed, `${name}: confirmed (fit ${a.fit}, ${a.samples} pairs)`);
    assert.deepEqual(a.perm, perm); assert.deepEqual(a.sign, [1, 1, 1]); assert.equal(a.unit, 'deg');
    assert.ok(a.samples >= 30);
    const w = track.omegaBodyAt(5400)!, truth = bodyRateDeg(traj, 5400);
    for (let i = 0; i < 3; i++) near(w[i], truth[i] * DEG, 1e-6, `${name} omega[${i}] rad/s`);
    near(track.rateAt(5400)!, 20, 1e-6, `${name} rateAt`);
  }
});

test('rateAt with no motion sample reads a chord: a 60 Hz pump-aged stream at 20 deg/s, median error within 5 %, worst within 25 % (S10)', () => {
  // A pump-ticked reading is 0-16.7 ms old (uniform): two consecutive samples carry a rate error of about 40 % (a
  // median near 29 %, a worst near 100 %), a chord of just over 100 ms about 7 % (a median near 4.2 %, a worst
  // under 17 %). Graded on five seeded 20 s streams, each on its own, so the bound is not a choice of seed.
  assert.equal(CHORD_MIN_MS, 100); assert.equal(CHORD_MAX_MS, 300);
  for (const seed of [61, 62, 63, 64, 65]) {
    const r = rng(seed), track = new PoseTrack();
    const upTo = player(track, events(steady(0, 0, 20), 0, 20000, { rel: { hz: 60, pumpAge: () => r() * 1000 / 60 } }));
    const errs: number[] = [];
    // At 30 Hz frame times, each asking as its frame arrives.
    for (let t = 500; t < 19990; t += 1000 / 30) {
      upTo(t);
      const w = track.rateAt(t);
      assert.ok(w !== null, `a rate at ${t}`);
      errs.push(Math.abs(w / 20 - 1));
    }
    errs.sort((a, b) => a - b);
    const med = errs[errs.length >> 1], worst = errs[errs.length - 1];
    assert.ok(med <= 0.05, `seed ${seed}: median error ${(100 * med).toFixed(2)} %`);
    assert.ok(worst <= 0.25, `seed ${seed}: worst error ${(100 * worst).toFixed(2)} %`);
  }
});

test('rateAt reads a fresh motion sample, else a 100-300 ms chord of predictor samples', () => {
  const traj = steady(0, 0, 20), track = new PoseTrack();
  assert.equal(track.rateAt(0), null);
  feed(track, events(traj, 0, 1000, { rel: { hz: 60 } }));
  near(track.rateAt(990)!, 20, 1e-6, 'over a chord');
  // No sample more than 100 ms before the newest: no chord yet, and two consecutive samples are not one.
  const young = new PoseTrack();
  feed(young, events(traj, 0, 100, { rel: { hz: 60 } }));
  assert.equal(young.rateAt(90), null, 'six samples over 83 ms');
  // A 3 Hz stream: every chord spans more than 300 ms, a pause, so the rate is unknown; at 5 Hz the chord is 200 ms.
  for (const [hz, rate] of [[3, null], [5, 20]] as const) {
    const slow = new PoseTrack();
    feed(slow, events(traj, 0, 2000, { rel: { hz } }));
    const r = slow.rateAt(1999);
    if (rate === null) assert.equal(r, null, `${hz} Hz`); else near(r!, rate, 1e-6, `${hz} Hz`);
  }
  const withGyro = new PoseTrack();
  feed(withGyro, events(traj, 0, 1000, { rel: { hz: 60 }, motion: { hz: 60, map: w => [w[0] * 1.5, w[1] * 1.5, w[2] * 1.5] } }));
  near(withGyro.rateAt(990)!, 30, 1e-6, 'from the gyro, which reads 1.5x here');
  // The last motion sample is at 983.3 ms.
  near(withGyro.rateAt(1050)!, 30, 1e-6, 'a motion sample within 100 ms');
  near(withGyro.rateAt(1100)!, 20, 1e-6, 'none within 100 ms: back to the predictor');
});

test('elevationAt and rollAt come from gravity; a tilt-only reading can say where the camera points vertically', () => {
  const pose: Pose = { az: 200, alt: 31, roll: 4 }, track = new PoseTrack();
  assert.equal(track.elevationAt(0), null); assert.equal(track.rollAt(0), null);
  feed(track, events(still(pose), 0, 200, { rel: { hz: 60, yaw0: 50 } }));
  near(track.elevationAt(100)!, 31, 1e-6, 'elevation'); near(track.rollAt(100)!, rollDeg(poseQ(pose)), 1e-6, 'roll');
  assert.ok(Math.abs(rollDeg(poseQ(pose))) > 3, 'the roll is real');
  // A reading with no alpha: tilt but no heading, so no prediction, yet the sensor half of canBegin holds.
  const d = deviceOrientationFromQuat(poseQ(pose)), tiltOnly = new PoseTrack();
  tiltOnly.onOrientation(orient(0, null, d.beta, d.gamma));
  assert.equal(tiltOnly.canPredict, true); assert.equal(tiltOnly.predictAt(0), null);
  near(tiltOnly.elevationAt(0)!, 31, 1e-6, 'tilt-only elevation');
});

test('facts() fills every SensorFacts field; moving rates count only |omega| >= 10 deg/s; the rate-by-omega table', () => {
  const traj = piecewise([[0, steady(0, 0, 25)], [2000, steady(50, 2000, 3.5)]]), track = new PoseTrack();
  feed(track, [
    ...events(traj, 0, 2000, { rel: { hz: 60 }, abs: { hz: 60 }, motion: { hz: 60 } }),
    ...events(traj, 2000, 4000, { rel: { hz: 30 }, abs: { hz: 30 }, motion: { hz: 60 } }),
  ]);
  const f = track.facts();
  assert.deepEqual(Object.keys(f).sort(), ['axis', 'blocked', 'events', 'gyroSeenNonZero', 'gyroZeroTriples', 'latency',
    'modeAtBegin', 'modeChanges', 'movingHz', 'rateByOmega', 'staleLimitMs', 'staleRefusals']);
  near(f.movingHz.relative!, 60, 0.5, 'relative while moving'); near(f.movingHz.absolute!, 60, 0.5, 'absolute while moving');
  near(f.movingHz.motion!, 60, 0.5, 'motion while moving');
  assert.deepEqual(f.rateByOmega.map(b => [b.omegaFrom, b.omegaTo]), [[0, 2], [2, 5], [5, 10], [10, 20], [20, 40]]);
  near(f.rateByOmega[1].relativeHz!, 30, 0.5, '2-5 deg/s'); near(f.rateByOmega[1].absoluteHz!, 30, 0.5);
  near(f.rateByOmega[4].relativeHz!, 60, 0.5, '20-40 deg/s');
  // The streams' first samples have no rate yet (the gyro's sample at t = 0 arrives after them), so nothing lands in
  // 0-2; 5-10 and 10-20 were never visited.
  for (const i of [0, 2, 3]) { assert.equal(f.rateByOmega[i].relativeHz, null); assert.equal(f.rateByOmega[i].absoluteHz, null); }
  assert.deepEqual(f.latency, { priorMs: 50, tauMs: 50, sigmaMs: 40, pairs: 0, applied: false });
  assert.equal(f.blocked, false); assert.equal(f.modeAtBegin, null); assert.deepEqual(f.modeChanges, []);
  assert.equal(f.gyroSeenNonZero, true); assert.equal(f.staleRefusals, 0);
  assert.deepEqual(f.staleLimitMs, { n: 0, p50: null, p95: null, max: null });
  assert.equal(f.axis?.confirmed ?? false, false, 'a steady pan excites one axis: never confirmed');
  const custom = new PoseTrack({ latencyPriorMs: 70, latencySdMs: 30 }).facts().latency;
  assert.deepEqual(custom, { priorMs: 70, tauMs: 70, sigmaMs: 30, pairs: 0, applied: false });
});

test('an iOS compass heading gives a relative predictor sample and a north sample', () => {
  const track = new PoseTrack();
  feed(track, events(steady(10, 0, 20), 0, 1000, { rel: { hz: 60, yaw0: 50, compass: true } }));
  assert.equal(track.mode, 'relative');
  const f = track.facts();
  assert.equal(f.events[0].absoluteFalse, 60); assert.equal(f.events[1].total, 0);
  const n = track.north.estimate()!;
  assert.equal(n.samples, 60); near(angDiff(n.offsetDeg, 50), 0, 1e-6, 'compass minus the relative heading');
});

// ---- fitAxisMapping (pure) --------------------------------------------------

type Pair = { rate: readonly [number, number, number]; omegaBody: V3 };
function pairs(n: number, seed: number, rawOf: (b: V3) => [number, number, number], o: { pitch?: number; tremor?: number } = {}): Pair[] {
  const r = rng(seed), out: Pair[] = [];
  for (let k = 0; k < n; k++) {
    const w = 12 + 18 * r(), p = (o.pitch ?? 20 + 20 * r()) * DEG, tremor = o.tremor ?? 6;
    const b: V3 = [tremor * (r() - 0.5) + 0.3 * gauss(r), w * Math.cos(p) + 0.3 * gauss(r), w * Math.sin(p) + 0.3 * gauss(r)];
    // A 2 % scale error and 0.1 deg/s of gyro noise, both in deg/s before the device's own axes and unit.
    out.push({ rate: rawOf([b[0] * 1.02 + 0.1 * gauss(r), b[1] * 1.02 + 0.1 * gauss(r), b[2] * 1.02 + 0.1 * gauss(r)]), omegaBody: b });
  }
  return out;
}

test('fitAxisMapping confirms a W3C stream, identifies the permuted #897 shape, detects rad/s, and refuses weak evidence', () => {
  const w3c = fitAxisMapping(pairs(60, 1, b => [b[0], b[1], b[2]]));
  assert.ok(w3c.confirmed); assert.deepEqual(w3c.perm, [0, 1, 2]); assert.deepEqual(w3c.sign, [1, 1, 1]);
  assert.equal(w3c.unit, 'deg'); assert.ok(w3c.fit >= 0.8); assert.equal(w3c.samples, 60);
  // #897's shape: alpha about z, beta about x, gamma about y.
  const p897 = fitAxisMapping(pairs(60, 2, b => [b[2], b[0], b[1]]));
  assert.ok(p897.confirmed); assert.deepEqual(p897.perm, [1, 2, 0]); assert.deepEqual(p897.sign, [1, 1, 1]);
  // The same at a steady pitch with no tremor: y and z are one signal (omega cos p, omega sin p), so the swapped
  // permutation correlates as well and only the slopes tell them apart; x is unexcited and takes the proper sign.
  const flat = fitAxisMapping(pairs(60, 3, b => [b[2], b[0], b[1]], { pitch: 23, tremor: 0 }));
  assert.ok(flat.confirmed); assert.deepEqual(flat.perm, [1, 2, 0]); assert.deepEqual(flat.sign, [1, 1, 1]);
  const rad = fitAxisMapping(pairs(60, 4, b => [b[0] * DEG, b[1] * DEG, b[2] * DEG]));
  assert.ok(rad.confirmed); assert.equal(rad.unit, 'rad'); assert.deepEqual(rad.perm, [0, 1, 2]);
  const flipped = fitAxisMapping(pairs(60, 5, b => [b[0], -b[1], b[2]]));
  assert.ok(flipped.confirmed); assert.deepEqual(flipped.sign, [1, -1, 1]);

  assert.equal(fitAxisMapping(pairs(25, 6, b => [b[0], b[1], b[2]])).confirmed, false, 'fewer than 30 pairs');
  const r = rng(7);
  const noise = fitAxisMapping(pairs(60, 8, () => [10 * gauss(r), 10 * gauss(r), 10 * gauss(r)]));
  assert.equal(noise.confirmed, false); assert.ok(noise.fit < 0.8);
  assert.equal(fitAxisMapping(pairs(60, 9, b => [3 * b[0], 3 * b[1], 3 * b[2]])).confirmed, false, 'slopes outside both unit bands');
  assert.equal(fitAxisMapping(pairs(60, 10, b => [b[0], b[1], b[2]], { pitch: 0, tremor: 0 })).confirmed, false, 'one excited axis');
  assert.equal(fitAxisMapping([]).confirmed, false);
});

// ---- LatencyEstimator -------------------------------------------------------

/** Pairs from a scan of starts, stops and jitter: |dOmega| between 2.5 and 9.5 deg/s, seven in ten speeding up (a
 *  non-zero mean, as a scan's long start has), image yaw noise 0.03 degrees, and a constant offset in r. */
function latencyPairs(seed: number, n: number, tauTrue: number, tauUsed: (k: number) => number, offset: number): [number, number, number][] {
  const r = rng(seed), out: [number, number, number][] = [];
  for (let k = 0; k < n; k++) {
    const dOmega = (k % 10 < 7 ? 1 : -1) * (2.5 + 7 * r()), tu = tauUsed(k);
    out.push([offset + (tu - tauTrue) * dOmega / 1000 + 0.03 * gauss(r), dOmega, tu]);
  }
  return out;
}

test('LatencyEstimator recovers an injected 80 ms from 90 pairs within 5 ms, with a constant yaw offset (the intercept case)', () => {
  const est = new LatencyEstimator();
  for (const [r, d, tu] of latencyPairs(21, 90, 80, () => 50, 0.4)) est.addPair(r, d, tu);
  assert.equal(est.pairs, 90); assert.equal(est.applied, true);
  near(est.tauMs, 80, 5, 'tau'); assert.ok(est.sigmaMs < 15);
});

test('LatencyEstimator still recovers when tauUsed switches from the prior to an applied value mid-series', () => {
  const est = new LatencyEstimator();
  for (const [r, d, tu] of latencyPairs(22, 90, 80, k => (k < 45 ? 50 : 76), 0.2)) est.addPair(r, d, tu);
  near(est.tauMs, 80, 5, 'tau');
});

test('LatencyEstimator ignores pairs with |dOmega| <= 2, keeps the prior until 10 pairs with sigma under 15 ms, clamps', () => {
  const est = new LatencyEstimator(), ps = latencyPairs(23, 10, 80, () => 50, 0.1);
  for (const [r, d, tu] of ps.slice(0, 9)) est.addPair(r, d, tu);
  assert.equal(est.applied, false); assert.equal(est.tauMs, 50); assert.equal(est.sigmaMs, 40); assert.equal(est.pairs, 9);
  for (const d of [2, -2, 1.5, 0, 1.99]) est.addPair(5, d, 50);
  assert.equal(est.pairs, 9, '|dOmega| <= 2 is ignored');
  est.addPair(...ps[9]);
  assert.equal(est.applied, true); near(est.tauMs, 80, 5, 'tau from the ten qualifying pairs');
  const custom = new LatencyEstimator(30, 20);
  assert.equal(custom.tauMs, 30); assert.equal(custom.sigmaMs, 20);
  for (const [tauTrue, clamped] of [[400, 250], [-120, -50]] as const) {
    const e = new LatencyEstimator();
    for (const [r, d, tu] of latencyPairs(24, 40, tauTrue, () => 50, 0)) e.addPair(r, d, tu);
    assert.equal(e.tauMs, clamped);
  }
});

// ---- NorthAnchor ------------------------------------------------------------

function ouSeries(seed: number, seconds: number, hz: number, tauS: number, sigma: number): number[] {
  const r = rng(seed), keep = Math.exp(-1 / hz / tauS), innovation = sigma * Math.sqrt(1 - keep * keep), out: number[] = [];
  let x = sigma * gauss(r);
  for (let k = 0; k < seconds * hz; k++) { x = keep * x + innovation * gauss(r); out.push(x); }
  return out;
}

test('NorthAnchor: circular mean across the 359/1 wrap', () => {
  const alt = new NorthAnchor();
  for (let k = 0; k < 100; k++) alt.add(k * 100, k % 2 ? 359 : 1, 0);
  near(angDiff(alt.estimate()!.offsetDeg, 0), 0, 1e-9, 'alternating 359 and 1');
  const r = rng(31), a = new NorthAnchor();
  for (let k = 0; k < 300; k++) a.add(k * 100, 200 + gauss(r), 200);   // o = N(0, 1), across the wrap
  const e = a.estimate()!;
  assert.ok(angDiff(e.offsetDeg, 0) < 0.3, `offset ${e.offsetDeg}`); assert.ok(e.spreadDeg < 1.3);
});

test('NorthAnchor: 10 % outliers at 40 degrees are rejected', () => {
  const r = rng(32), a = new NorthAnchor();
  for (let k = 0; k < 300; k++) a.add(k * 100, k % 10 === 9 ? 50 : 10 + gauss(r), 0);
  const e = a.estimate()!;
  assert.ok(angDiff(e.offsetDeg, 10) < 0.3, `offset ${e.offsetDeg} (a mean with the outliers is 14)`);
  assert.ok(e.spreadDeg < 1.3, `spread ${e.spreadDeg}`); assert.equal(e.samples, 300);
  assert.equal(e.stable, true, 'inliers are 90 % of the samples (S11)');
  // Two equal clusters 20 degrees apart: the median is a sample, so one cluster is kept rather than neither.
  const split = new NorthAnchor();
  for (let k = 0; k < 100; k++) split.add(k * 100, k % 2 ? 20 : 0, 0);
  const s = split.estimate()!;
  assert.ok(Number.isFinite(s.offsetDeg) && Number.isFinite(s.sigmaDeg) && Number.isFinite(s.spreadDeg), JSON.stringify(s));
  assert.ok(angDiff(s.offsetDeg, 0) < 1e-6 || angDiff(s.offsetDeg, 20) < 1e-6, `offset ${s.offsetDeg}`);
});

test('NorthAnchor: a drifting offset or a wide residual marks unstable; a steady one is stable', () => {
  const r = rng(33), drift = new NorthAnchor(), steadyA = new NorthAnchor(), wide = new NorthAnchor();
  for (let k = 0; k < 600; k++) {
    const t = k * 100;
    drift.add(t, 10 + 6 * t / 60000 + 0.3 * gauss(r), 0);   // 6 deg/min
    steadyA.add(t, 10 + gauss(r), 0);
    wide.add(t, 10 + 4.5 * gauss(r), 0);
  }
  assert.equal(drift.estimate()!.stable, false);
  assert.equal(steadyA.estimate()!.stable, true);
  assert.equal(wide.estimate()!.stable, false);
});

test('NorthAnchor: a 60/40 compass split 30 degrees apart is unstable; inliers must be at least 85 % of the samples (S11)', () => {
  const r = rng(37), split = new NorthAnchor();
  for (let k = 0; k < 300; k++) split.add(k * 100, (k % 10 < 6 ? 10 : 40) + gauss(r), 0);
  const s = split.estimate()!;
  // The inliers alone are tight and steady: by the fit alone this read stable at sigma 2.
  assert.ok(angDiff(s.offsetDeg, 10) < 0.3, `offset ${s.offsetDeg}`); assert.ok(s.spreadDeg < 1.3, `spread ${s.spreadDeg}`);
  assert.equal(s.sigmaDeg, 2);
  assert.equal(s.stable, false, 'inliers are 60 % of the samples');
  // At the line: 85 inliers of 100 is stable, 84 is not. The outliers are spread through the series (37 is prime to
  // 100), 40 degrees off.
  for (const [inliers, stable] of [[85, true], [84, false]] as const) {
    const a = new NorthAnchor();
    for (let k = 0; k < 100; k++) a.add(k * 100, (k * 37) % 100 < 100 - inliers ? 50 : 10, 0);
    assert.equal(a.estimate()!.stable, stable, `${inliers} inliers of 100`);
  }
});

test('NorthAnchor: nEff is near N for white noise and near T / (2 tau) for a tau = 5 s Ornstein-Uhlenbeck series', () => {
  const r = rng(34), white = new NorthAnchor();
  for (let k = 0; k < 600; k++) white.add(k * 100, 10 + 2 * gauss(r), 0);   // 10 Hz, the resampling rate
  const w = white.estimate()!;
  assert.ok(w.nEff >= 0.8 * 600 && w.nEff <= 600, `white nEff ${w.nEff}`);
  // The estimator itself scatters by tens of percent per series at this length (a truncated sample autocorrelation),
  // so the claim is held on the median of twelve seeded series, and every series stays within a factor of 2.
  const T = 600, tau = 5, ratios: number[] = [];
  for (let seed = 1; seed <= 12; seed++) {
    const a = new NorthAnchor();
    ouSeries(100 + seed, T, 10, tau, 3).forEach((x, k) => a.add(k * 100, 40 + x, 0));
    ratios.push(a.estimate()!.nEff / (T / (2 * tau)));
  }
  ratios.sort((p, q) => p - q);
  const med = (ratios[5] + ratios[6]) / 2;
  assert.ok(med > 1 / 1.5 && med < 1.5, `median nEff / (T / 2 tau) ${med}`);
  assert.ok(ratios[0] > 0.5 && ratios[11] < 2, `ratios ${ratios.map(x => x.toFixed(2)).join(' ')}`);
  // Sampled at 60 Hz the answer is the same: N counts resampled points, not raw samples (6x more here).
  const fast = new NorthAnchor();
  ouSeries(200, T, 60, tau, 3).forEach((x, k) => fast.add(k * 1000 / 60, 40 + x, 0));
  const ratio60 = fast.estimate()!.nEff / (T / (2 * tau));
  assert.ok(ratio60 > 0.5 && ratio60 < 2, `60 Hz ratio ${ratio60}`);
});

test('NorthAnchor: sigmaDeg is never below 2', () => {
  const r = rng(35), many = new NorthAnchor();
  for (let k = 0; k < 600; k++) many.add(k * 100, 10 + gauss(r), 0);
  const e = many.estimate()!;
  assert.ok(e.spreadDeg / Math.sqrt(e.nEff) < 0.1, 'the averaged figure alone would claim 0.04');
  assert.equal(e.sigmaDeg, 2);
  // A short, noisy estimate keeps its larger sigma.
  const few = new NorthAnchor();
  for (const [t, o] of [[0, 7], [100, -7], [200, 7], [300, -7], [400, 0]]) few.add(t, 10 + o, 0);
  const f = few.estimate()!;
  near(f.sigmaDeg, f.spreadDeg / Math.sqrt(f.nEff), 1e-9); assert.ok(f.sigmaDeg > 2);
});

test('NorthAnchor: corrYawAt is subtracted; shift accumulates as a manual nudge; no samples is no estimate', () => {
  const r = rng(36), a = new NorthAnchor();
  assert.equal(a.estimate(), null);
  // A tracker correction that drifts 6 deg/min is in the raw offset and taken out by corrYawAt.
  const corr = (t: number) => 0.1 * t / 1000;
  for (let k = 0; k < 600; k++) { const t = k * 100; a.add(t, 10 + corr(t) + 0.5 * gauss(r), 0); }
  const raw = a.estimate()!, fixed = a.estimate(corr)!;
  assert.equal(raw.stable, false);
  assert.ok(angDiff(fixed.offsetDeg, 10) < 0.2, `offset ${fixed.offsetDeg}`); assert.equal(fixed.stable, true);
  assert.equal(fixed.source, 'scan');
  a.shift(5); a.shift(-2);
  const nudged = a.estimate(corr)!;
  assert.ok(angDiff(nudged.offsetDeg, 13) < 0.2); assert.equal(nudged.source, 'manual');
  const empty = new NorthAnchor();
  empty.shift(4);
  assert.equal(empty.estimate(), null);
});

console.log(`poseTrack.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
