// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T19: the tracker v0, the keyframe rule of SPEC-v2 4.3 with placement by the predictor, and the pure functions of
// 4.7 against its worked numbers.
//
// Mutant this file must catch (SPEC-v2 7.2): the window replaced by "commit the first frame at d >= S".
// `best-of-window commits the slowest candidate` asserts that the frame committed from a window of rates
// 30, 20, 25, 35 is the rate-20 frame, read back second; under the mutant the rate-30 frame commits at once.
//
// The rule tests run in sensor-only mode. The rule is the same in both modes, but T27 adds alignment and rule 5
// (revisits) to the other mode only, and a revisit would commit the slow frames these tests use to probe the
// window. Sensor-only mode keeps the v0 path exactly (T27), so these cases stay true after it. BandPanoramaLike,
// FocalLike and LatencyLike are stubs; the panorama stub marks a simplified footprint in `coverage` (below) so
// that `gapDeg` and `coveredDeg` can be read end to end. Poses are level (pitch 0) unless a case says otherwise,
// so an axis distance is an azimuth difference and the trailing-fill numbers are exact.
import assert from 'node:assert/strict';
import { lookBasis } from '../../photosphereGeometry';
import { headingDeg, intrinsicsAt, qinv, qmul, qrotate, quatFromBasis, worldYaw } from '../rotation';
import {
  FIRST_KF_MAX_WAIT_MS, MAX_KEYFRAMES, STEP_DEG, STRIP_HALF_DEG, TRAILING_MAX_DEG, Tracker, WINDOW_DEG,
  innovationGateDeg, placementSigmaDeg, predictorVarianceDeg2,
} from '../tracker';
import type {
  BandPanoramaLike, DirtyRect, FocalLike, FrameStep, KeyClass, LatencyLike, Prediction, Quat, SliceSource, TrackerOptions, V3,
} from '../types';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const near = (a: number, b: number, tol: number, what = '') => assert.ok(Math.abs(a - b) < tol, `${what} ${a} != ${b} (tol ${tol})`);
const nearQuat = (a: Quat, b: Quat, tol: number, what = '') => {
  // q and -q are one rotation.
  const s = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3] < 0 ? -1 : 1;
  for (let i = 0; i < 4; i++) near(a[i], s * b[i], tol, `${what}[${i}]`);
};
const angDiff = (a: number, b: number) => Math.abs((((a - b) % 360) + 540) % 360 - 180);

// ---- Stubs ------------------------------------------------------------------

const CLASS_VALUE: Record<KeyClass, number> = { sensor: 1, blurred: 2, aligned: 3 };

/** Records every paint and clear. Its coverage is a simplified footprint: the cells whose centres lie within 2
 *  degrees of the slice's heading (feather weight >= 0.5), widened on the trailing side by trailingDeg, never past
 *  the strip's 10 degrees. Side -1 is the image's left, which for an upright camera is the lower azimuth. */
class StubPano implements BandPanoramaLike {
  readonly rgba = new Uint8ClampedArray(4);
  readonly cls = new Uint8Array(1);
  readonly sigma = new Uint8Array(1);
  readonly firstSeen = new Uint16Array(1);
  readonly coverage = new Uint8Array(720);
  readonly paints: { s: SliceSource; nowMs: number }[] = [];
  clears = 0;
  begin(): void {}
  paint(s: SliceSource, nowMs: number): DirtyRect {
    this.paints.push({ s, nowMs });
    const h = headingDeg(s.pose);
    const left = Math.min(2 + (s.trailingSide === -1 ? s.trailingDeg : 0), STRIP_HALF_DEG);
    const right = Math.min(2 + (s.trailingSide === 1 ? s.trailingDeg : 0), STRIP_HALF_DEG);
    for (let i = 0; i < 720; i++) {
      const off = ((((i + 0.5) / 2 - h) % 360) + 540) % 360 - 180;
      if (off >= -left && off <= right) this.coverage[i] = Math.max(this.coverage[i], CLASS_VALUE[s.cls]);
    }
    return { x: 0, y: 0, w: 0, h: 0 };
  }
  clear(): void { this.clears++; this.coverage.fill(0); }
  takeDirty(): DirtyRect[] { return []; }
  observedRun(): { topAlt: number; bottomAlt: number } | null { return null; }
}

type Mutable<T> = { -readonly [K in keyof T]: T[K] };
/** The default lens: the 16:9 prior, a 43.00-degree short axis. */
const F_DEFAULT = 0.5 / Math.tan(21.5 * Math.PI / 180);
const stubFocal = (fBest = F_DEFAULT): Mutable<FocalLike> => ({
  fMeasure: fBest, fBest, state: 'prior', sdPct: 20, ratios: 0,
  addRatio: () => 'collecting', closeLoop: () => ({ fNorm: fBest, gyroScale: 0 }),
});
const stubLatency = (sigmaMs = 40): Mutable<LatencyLike> => ({ tauMs: 50, sigmaMs, pairs: 0, applied: false, addPair: () => {} });

// ---- The scanner's frame path (3.5), cut down to what the tracker sees ------

const W = 180, H = 320;
const poseAt = (az: number, alt = 0): Quat => quatFromBasis(lookBasis(az, alt));
const upOf = (q: Quat): V3 => qrotate(qinv(q), [0, 0, 1]);

interface Rig {
  tracker: Tracker; pano: StubPano; focal: Mutable<FocalLike>; latency: Mutable<LatencyLike>;
  rgba: Uint8ClampedArray; frameNo: number; reads: number;
  /** Per frame id read back: the azimuth and the prediction handed to hold(). */
  heldAz: Map<number, number>; heldPred: Map<number, Quat>;
}
function rig(o: Partial<TrackerOptions> = {}, rgba = new Uint8ClampedArray(W * H * 4)): Rig {
  const pano = new StubPano(), focal = stubFocal(), latency = stubLatency();
  const tracker = new Tracker({ pano, focal, latency, frameW: W, frameH: H, sensorOnly: true, ...o });
  return { tracker, pano, focal, latency, rgba, frameNo: 0, reads: 0, heldAz: new Map(), heldPred: new Map() };
}
/** One frame: onFrame, then hold() on a read and commit() on a commit, as 3.5 steps 3, 5 and 6 do. A null azimuth
 *  is a null prediction. */
function step(r: Rig, t: number, az: number | null, rate: number | null, alt = 0): FrameStep {
  r.frameNo++;
  const pred: Prediction | null = az === null ? null : { q: poseAt(az, alt), t: t - 50, mode: 'relative', extrapolatedMs: 0, held: false };
  const s = r.tracker.onFrame(t, pred, rate);
  if (s.read) {
    assert.ok(pred && az !== null, 'a read needs a prediction');
    r.reads++;
    r.heldAz.set(r.frameNo, az);
    r.heldPred.set(r.frameNo, pred.q);
    r.tracker.hold({ frameId: r.frameNo, t, w: W, h: H, rgba: r.rgba, readbackMs: 1 }, pred, rate ?? 0, upOf(pred.q));
  }
  if (s.commit) r.tracker.commit();
  return s;
}
/** A steady turn at 30 frames/s from one azimuth to another; returns the time after the last frame. */
function sweep(r: Rig, t: number, from: number, to: number, rate: number, alt = 0): number {
  const dir = Math.sign(to - from), perFrame = rate / 30;
  for (let az = from; dir * (to - az) >= 0; az += dir * perFrame, t += 1000 / 30) step(r, t, az, rate, alt);
  return t;
}
/** A first keyframe at `az`, from one steady frame. */
function started(az = 0, o: Partial<TrackerOptions> = {}): Rig {
  const r = rig(o);
  r.tracker.setActive(true);
  assert.deepEqual(step(r, 0, az, 5), { read: true, commit: true, why: 'first' });
  return r;
}
const newest = (r: Rig) => r.tracker.keyframes[r.tracker.keyframes.length - 1];
const lastSlice = (r: Rig) => r.pano.paints[r.pano.paints.length - 1].s;

// ---- The pure functions of 4.7 ------------------------------------------------

// A 4-degree step at 4.6 keyframes/s. 4.7 writes the steady cases with dOmega = 2 and the accelerating one with
// dOmega = 2 as well, while its rule is "sigma_eis = 0.3 when |dOmega| < 2"; 1.99 and 2.01 sit either side of that
// boundary and give the same four numbers to the printed precision.
const DT = 1 / 4.6;
const SG_STEADY_APPLIED = Math.sqrt(predictorVarianceDeg2('relative', 4, DT, 1.99, 2.1));
const SG_STEADY_PRIOR = Math.sqrt(predictorVarianceDeg2('relative', 4, DT, 1.99, 40));
const SG_ACCEL = Math.sqrt(predictorVarianceDeg2('absolute-gyro', 4, DT, 2.01, 40));
const SG_ABS_ONLY = Math.sqrt(predictorVarianceDeg2('absolute-only', 4, DT, 1.99, 2.1));

test('predictorVarianceDeg2 gives the worked sigmas of 4.7: 0.311, 0.321, 1.006, 1.047', () => {
  assert.equal(SG_STEADY_APPLIED.toFixed(3), '0.311');
  assert.equal(SG_STEADY_PRIOR.toFixed(3), '0.321');
  assert.equal(SG_ACCEL.toFixed(3), '1.006');
  assert.equal(SG_ABS_ONLY.toFixed(3), '1.047');
  // The terms one at a time: 0.0064 + 0.00005 + (sigma_tau dOmega)^2 + sigma_eis^2.
  near(predictorVarianceDeg2('relative', 4, 0, 0, 0), 0.0064 + 0.09, 1e-12, 'scale + steady EIS');
  near(predictorVarianceDeg2('relative', 0, 60, 0, 0), 4 + 0.09, 1e-12, 'drift 2 deg/min over a minute');
  near(predictorVarianceDeg2('relative', 0, 0, 10, 40), 0.16 + 1, 1e-12, 'latency 40 ms x 10 deg/s, accelerating EIS');
  near(predictorVarianceDeg2('relative', 0, 0, -1.99, 0), 0.09, 1e-12, 'a negative rate change is steady by magnitude');
  near(predictorVarianceDeg2('absolute-only', 0, 0, 0, 0), 1.09, 1e-12, 'the absolute-only term');
});

test('innovationGateDeg gives the worked gates of 4.7: 1.93, 1.96, 4.02, 4.14, widened before 3 ratios', () => {
  const gate = (sigmaGDeg: number) => innovationGateDeg({ locked: true, ratios: 0, dPsiGyroDeg: 4, sigmaGDeg });
  assert.equal(gate(SG_STEADY_APPLIED).toFixed(2), '1.93');
  assert.equal(gate(SG_STEADY_PRIOR).toFixed(2), '1.96');
  assert.equal(gate(SG_ACCEL).toFixed(2), '4.02');
  assert.equal(gate(SG_ABS_ONLY).toFixed(2), '4.14');
  near(innovationGateDeg({ locked: false, ratios: 3, dPsiGyroDeg: 4, sigmaGDeg: SG_STEADY_APPLIED }), gate(SG_STEADY_APPLIED), 1e-12, 'three ratios');
  near(innovationGateDeg({ locked: false, ratios: 2, dPsiGyroDeg: -4, sigmaGDeg: SG_STEADY_APPLIED }), gate(SG_STEADY_APPLIED) + 2, 1e-12,
    'fewer than 3 ratios widens by 0.5 |dPsi_gyro|');
});

test('placementSigmaDeg gives the worked sigmas of 4.7: 3.6 sensor-only and 0.5 after an image closure', () => {
  // 180 degrees from the gauge, half of an 18 s ring, the gauge itself as the anchor.
  const half = { aligned: false, stepsToGauge: 41, dPsiToAnchorDeg: 180, dtToAnchorS: 9, omegaDegS: 20, sigmaTauMs: 2.1, anchorSigmaDeg: 0 };
  assert.equal(placementSigmaDeg({ ...half, closed: false }).toFixed(1), '3.6');
  assert.equal(placementSigmaDeg({ ...half, closed: true }), 0.5, 'sqrt(0.25^2 + 0.3^2 + 0.3^2) = 0.49, then the floor');
  near(placementSigmaDeg({ ...half, dPsiToAnchorDeg: 0, dtToAnchorS: 0, omegaDegS: 50, sigmaTauMs: 40, closed: false }),
    Math.sqrt(0.09 + 4), 1e-12, 'sigma_tau omega');
  near(placementSigmaDeg({ ...half, anchorSigmaDeg: 2, closed: false }), Math.hypot(3.6, 0.3, 0.3, 0.042, 2), 1e-12, 'the anchor term');
  assert.equal(placementSigmaDeg({ ...half, aligned: true, stepsToGauge: 16, closed: false }), 0.2, 'aligned: 0.05 sqrt(s)');
  assert.equal(placementSigmaDeg({ ...half, aligned: true, stepsToGauge: 0, closed: false }), 0.05, 'aligned: s at least 1');
});

// ---- The keyframe rule of 4.3 -------------------------------------------------

test('the constants of the tracker block', () => {
  assert.deepEqual([STEP_DEG, WINDOW_DEG, FIRST_KF_MAX_WAIT_MS, MAX_KEYFRAMES, STRIP_HALF_DEG, TRAILING_MAX_DEG], [4, 2, 300, 140, 10, 10]);
});

test('best-of-window commits the slowest candidate', () => {
  // A turn at 30 deg/s that slows to 20 and speeds up again inside the window [S, S + 2); every rate is above
  // 0.3 x rateMax = 12, so only S + 2 commits. Frames 2-5 are inside S, 6-9 inside the window, 10 past it.
  const r = started(0);
  const frames: [number, number][] = [[1, 30], [2, 30], [3, 30], [3.9, 30], [4.2, 30], [4.6, 20], [5.0, 25], [5.4, 35], [6.1, 30]];
  const steps = frames.map(([az, rate], i) => step(r, 100 + 33 * i, az, rate));
  const first = 6, slowest = 7;
  const kf = r.tracker.keyframes[1];
  assert.ok(kf, 'a second keyframe');
  assert.equal(kf.frameId, slowest, 'the slowest frame of the window, not the first frame at d >= S');
  assert.equal(r.tracker.keyframes.length, 2);
  assert.equal(kf.rate, 20);
  near(headingDeg(kf.pose), 4.6, 1e-9, 'placed where that frame looked');
  assert.deepEqual(steps.map(s => s.why), ['covered', 'covered', 'covered', 'covered', 'step', 'step', 'waiting-sharper', 'waiting-sharper', 'step']);
  assert.deepEqual(steps.map(s => s.read), [false, false, false, false, true, true, false, false, false],
    'the first frame in the window is read, then the one below 0.8 x its rate; two readbacks for the keyframe');
  assert.deepEqual(steps.map(s => s.commit), [false, false, false, false, false, false, false, false, true], 'S + 2 commits the held one');
  assert.notEqual(kf.frameId, first);

  // A later frame must be clearly slower: 26 is not below 0.8 x 30, so the first frame stays.
  const h = started(0);
  step(h, 100, 4.2, 30);
  const kept = h.frameNo;
  assert.equal(step(h, 133, 4.7, 26).read, false);
  step(h, 166, 6.2, 30);
  assert.equal(h.tracker.keyframes[1].frameId, kept);
});

test('the first-keyframe rule: a steady frame, else the first frame 300 ms after the first active one', () => {
  const a = rig();
  a.tracker.setActive(true);
  assert.deepEqual(step(a, 0, 0, 30), { read: false, commit: false, why: 'waiting-sharper' }, '30 > 0.3 x 40');
  assert.deepEqual(step(a, 33, 0.5, 12), { read: true, commit: true, why: 'first' }, '12 = 0.3 x 40 is steady');
  assert.equal(a.tracker.keyframes.length, 1);
  assert.equal(a.tracker.keyframes[0].frameId, 2);
  assert.deepEqual({ ...a.tracker.log[a.tracker.log.length - 1] },
    { at: 33, frameId: 2, outcome: 'accepted', detail: 'first', kf: 0, rateDegS: 12, extrapolatedMs: 0 });

  // The 300 ms run from the first ACTIVE frame: frames before Begin start nothing, and a null prediction never commits.
  const b = rig();
  for (let t = 0; t < 1000; t += 33) assert.equal(step(b, t, 0, 30).why, 'inactive');
  b.tracker.setActive(true);
  assert.equal(step(b, 1000, 0, 30).why, 'waiting-sharper');
  assert.equal(step(b, 1150, null, 30).why, 'stale-pose');
  assert.equal(step(b, 1299, 1, 30).why, 'waiting-sharper');
  assert.deepEqual(step(b, 1300, 2, 30), { read: true, commit: true, why: 'first' }, '300 ms after the first active frame');
  assert.equal(b.tracker.keyframes[0].cls, 'sensor');

  // The steady test follows setRateMax: at 15 deg/s, 0.3 x rateMax is 4.5.
  const c = rig();
  c.tracker.setActive(true);
  c.tracker.setRateMax(15);
  assert.equal(step(c, 0, 0, 12).why, 'waiting-sharper');
  assert.equal(step(c, 33, 0, 4.5).why, 'first');
});

test('commit at 0.3 x rateMax or at S + 2, and only a commit from no window gets trailing fill', () => {
  const a = started(0);
  assert.deepEqual(step(a, 100, 4.3, 12), { read: true, commit: true, why: 'step' }, 'a steady frame in the window commits at once');
  assert.equal(a.tracker.keyframes.length, 2);
  assert.equal(lastSlice(a).trailingDeg, 0);
  assert.equal(lastSlice(a).trailingSide, 0);

  const b = started(0);
  assert.deepEqual(step(b, 100, 4.1, 20), { read: true, commit: false, why: 'step' });
  assert.equal(step(b, 133, 5.95, 20).commit, false, 'still inside the window');
  assert.deepEqual(step(b, 166, 6.05, 20), { read: false, commit: true, why: 'step' }, 'd reached S + 2');
  near(headingDeg(b.tracker.keyframes[1].pose), 4.1, 1e-9);
  assert.equal(lastSlice(b).trailingDeg, 0, 'a window commit (d < S + 2) meets its neighbour without fill');
  assert.equal(b.tracker.log[b.tracker.log.length - 1].stepDeg?.toFixed(6), '4.100000');

  // A held candidate commits as soon as it is steady, which a raised rateMax can make it.
  const c = started(0);
  step(c, 100, 4.4, 20);
  const heldId = c.frameNo;
  c.tracker.setRateMax(70);
  assert.deepEqual(step(c, 133, 4.8, 25), { read: false, commit: true, why: 'step' }, '20 <= 0.3 x 70');
  assert.equal(c.tracker.keyframes[1].frameId, heldId);
});

test('blurred above rateMax, sensor at or below it', () => {
  const r = started(0);
  r.tracker.setRateMax(15);
  step(r, 100, 4.2, 20);
  step(r, 133, 6.3, 20);
  assert.equal(r.tracker.keyframes[1].cls, 'blurred', '20 > 15');
  assert.equal(lastSlice(r).cls, 'blurred');
  assert.equal(r.tracker.log[r.tracker.log.length - 1].detail, 'blurred');
  step(r, 166, 8.4, 15);
  step(r, 200, 10.5, 15);
  assert.equal(r.tracker.keyframes[2].cls, 'sensor', '15 is not above 15');
  assert.equal(r.tracker.keyframes[0].cls, 'sensor');

  const f = rig();
  f.tracker.setActive(true);
  f.tracker.setRateMax(15);
  step(f, 0, 0, 30);
  step(f, 300, 0, 30);
  assert.equal(f.tracker.keyframes[0].cls, 'blurred', 'the first keyframe too, when it commits by the timeout');
});

test('a null prediction reads nothing and is counted for one second', () => {
  const r = started(0);
  assert.equal(r.tracker.livePose(null), null);
  step(r, 100, 4.3, 20);
  const reads = r.reads, logged = r.tracker.log.length;
  for (let i = 0; i < 5; i++) {
    assert.deepEqual(step(r, 133 + 33 * i, null, 20), { read: false, commit: false, why: 'stale-pose' });
  }
  assert.equal(r.reads, reads);
  assert.equal(r.tracker.staleInLastSecond, 5);
  assert.deepEqual(r.tracker.log.slice(logged).map(x => x.outcome), ['stale-pose', 'stale-pose', 'stale-pose', 'stale-pose', 'stale-pose']);
  assert.equal(r.tracker.log[logged].frameId, 3, 'a record of an unread frame carries the onFrame count');
  step(r, 1250, 4.4, 20);
  assert.equal(r.tracker.staleInLastSecond, 1, 'at 1250 ms the refusals at 133-232 ms have aged out, 265 ms has not');
  step(r, 1300, 4.5, 20);
  assert.equal(r.tracker.staleInLastSecond, 0);
  assert.equal(r.tracker.keyframes.length, 1, 'the stale run committed nothing');
});

test('inactive frames do nothing', () => {
  const r = started(0);
  r.tracker.setActive(false);
  const reads = r.reads, logged = r.tracker.log.length, paints = r.pano.paints.length;
  for (let az = 1; az <= 30; az += 1) {
    assert.deepEqual(step(r, 100 + az * 33, az, az % 2 ? 5 : 30), { read: false, commit: false, why: 'inactive' });
  }
  assert.equal(r.reads, reads, 'no readback');
  assert.equal(r.tracker.keyframes.length, 1, 'no commit');
  assert.equal(r.pano.paints.length, paints, 'no paint');
  assert.deepEqual(r.tracker.log.slice(logged).map(x => x.outcome), ['inactive'], 'one record for the stretch');
  r.tracker.setActive(true);
  assert.equal(step(r, 2000, 31, 5).commit, true, 'active again, the frame past the window commits');
});

test('trailing fill for a 9-degree jump meets the previous slice on its side', () => {
  const jump = (from: number, to: number) => {
    const r = started(from);
    r.tracker.setActive(false);
    for (let i = 1; i < 9; i++) step(r, 33 * i, from + (to - from) * i / 9, 30);
    r.tracker.setActive(true);
    assert.deepEqual(step(r, 400, to, 5), { read: true, commit: true, why: 'step' }, 'past the window with nothing held');
    return r;
  };
  const cw = jump(0, 9);
  near(lastSlice(cw).trailingDeg, 9 - STEP_DEG, 1e-9, 'trailingDeg = d - S');
  assert.equal(lastSlice(cw).trailingSide, -1, 'turning clockwise, the previous slice is on the left');
  assert.equal(cw.tracker.gapDeg, null, 'the fill leaves no gap');
  assert.equal(cw.tracker.log[cw.tracker.log.length - 1].detail, 'sensor');
  near(cw.tracker.log[cw.tracker.log.length - 1].stepDeg ?? NaN, 9, 1e-9);

  const ccw = jump(100, 91);
  near(lastSlice(ccw).trailingDeg, 5, 1e-9);
  assert.equal(lastSlice(ccw).trailingSide, 1, 'turning counter-clockwise, it is on the right');

  // Beyond S + 10 the fill stops at 10 degrees, the gap stays grey and the record says so.
  const far = started(0);
  far.tracker.setActive(false);
  step(far, 100, 10, 30);
  far.tracker.setActive(true);
  step(far, 400, 20, 5);
  near(lastSlice(far).trailingDeg, TRAILING_MAX_DEG, 1e-12);
  assert.equal(far.tracker.log[far.tracker.log.length - 1].detail, 'gap');
  assert.equal(far.tracker.gapDeg, 8, 'covered to 2 by the first slice, from 10 by the second');
});

test('the cap: 140 keyframes by default, then nothing more is committed', () => {
  const small = started(0, { maxKeyframes: 5 });
  sweep(small, 33, 0.6, 60, 20);
  assert.equal(small.tracker.keyframes.length, 5);
  assert.equal(step(small, 9000, 70, 5).why, 'cap');
  assert.equal(step(small, 9033, 80, 5).why, 'cap');
  assert.equal(small.tracker.log.filter(x => x.outcome === 'cap').length, 1, 'one record when the cap is reached');

  // The default: rings at three pitches offer about 230 keyframes.
  const r = started(0);
  let t = sweep(r, 33, 0.6, 359, 20);
  t = sweep(r, t, 0, 359, 20, 12);
  t = sweep(r, t, 0, 359, 20, 24);
  assert.equal(r.tracker.keyframes.length, MAX_KEYFRAMES);
  assert.equal(step(r, t, 10, 5, 36).why, 'cap');
});

test('gapDeg: the largest unpainted run of 2 degrees or more inside the swept range', () => {
  const r = started(0);
  sweep(r, 33, 0.6, 40, 20);
  assert.equal(r.tracker.gapDeg, null, 'a steady turn leaves none');
  assert.ok(r.tracker.coveredDeg > 35 && r.tracker.coveredDeg < 45, 'while most of the ring is unpainted beyond the ends');

  // 2.5 degrees cleared inside the swept range count; the same outside it do not.
  r.pano.coverage.fill(0, 40, 45);
  assert.equal(r.tracker.gapDeg, 2.5);
  r.pano.coverage.fill(1, 40, 45);
  r.pano.coverage.fill(0, 100, 105);
  assert.equal(r.tracker.gapDeg, null, '50 degrees is past the newest keyframe: not yet turned to');
  r.pano.coverage.fill(0, 40, 43);
  assert.equal(r.tracker.gapDeg, null, '1.5 degrees is under 2');

  // A 13-degree jump fills to within 1 degree of the first slice (a 20-degree jump, above, leaves 8).
  const j = started(0);
  j.tracker.setActive(false);
  step(j, 100, 6, 30);
  j.tracker.setActive(true);
  step(j, 400, 13, 5);
  assert.equal(j.tracker.gapDeg, null);

  // The seam. Steady frames every 0.3 degrees commit at 4.2-degree spacing, so the last keyframe is at 352.8 and
  // the frames after it are within S of keyframe 0: 3.0 degrees (354.8 to 358) stay unpainted. That is ahead of
  // the user until the turn passes 360, and behind them after.
  const seam = started(0);
  let t = 100;
  for (let i = 1; i * 0.3 <= 359; i++) step(seam, t += 33, i * 0.3, 10);
  near(headingDeg(newest(seam).pose), 352.8, 1e-9, 'the last keyframe');
  assert.equal(seam.tracker.gapDeg, null, 'at 359 degrees the seam is still ahead');
  for (let i = 1; i <= 10; i++) step(seam, t += 33, 359 + i * 0.3, 10);
  assert.equal(seam.tracker.keyframes.length, 85, 'no keyframe past the seam');
  assert.equal(seam.tracker.gapDeg, 3, 'past 360 the seam is a gap behind the turn');

  // After the full turn the whole ring is inside, and a run across azimuth 0 is one run. The coverage is set by
  // hand so that only the cleared cells are unpainted.
  const ring = started(0);
  sweep(ring, 33, 0.6, 375, 20);
  assert.ok(ring.tracker.loop.unwrappedDeg > 340, `unwrapped ${ring.tracker.loop.unwrappedDeg}: the newest keyframe, not wrapped to below 0`);
  ring.pano.coverage.fill(1);
  assert.equal(ring.tracker.gapDeg, null);
  ring.pano.coverage.fill(0, 718, 720);
  ring.pano.coverage.fill(0, 0, 3);
  assert.equal(ring.tracker.gapDeg, 2.5, 'cells 718-719 and 0-2');
  ring.pano.coverage.fill(0, 300, 310);
  assert.equal(ring.tracker.gapDeg, 5, 'the largest run wins');
});

test('coveredDeg counts the painted coverage cells / 2', () => {
  const r = rig();
  assert.equal(r.tracker.coveredDeg, 0);
  r.pano.coverage.fill(2, 10, 110);
  assert.equal(r.tracker.coveredDeg, 50);
  const j = started(0);
  j.tracker.setActive(false);
  step(j, 100, 10, 30);
  j.tracker.setActive(true);
  step(j, 400, 20, 5);
  assert.equal(j.tracker.coveredDeg, 16, '8 cells around the first slice and 24 from 10 to 22 degrees');
});

test('livePose = C . pred, with C = P_k . G_k^-1 of the newest keyframe', () => {
  const r = rig();
  const pred = (az: number, alt = 10): Prediction => ({ q: poseAt(az, alt), t: 0, mode: 'relative', extrapolatedMs: 0, held: false });
  nearQuat(r.tracker.livePose(pred(30)) as Quat, pred(30).q, 1e-15, 'no keyframe: C is the identity');
  r.tracker.setActive(true);
  step(r, 0, 0, 5);
  sweep(r, 33, 0.6, 20, 20);
  nearQuat(r.tracker.livePose(pred(57)) as Quat, pred(57).q, 1e-12, 'v0 places by the predictor, so C is the identity');
  // A correction on the newest keyframe, as fusion will make one: the live pose carries it, composed on the left.
  const kf = newest(r);
  const c = qmul(worldYaw(1.5), qmul(poseAt(0, 0.5), qinv(poseAt(0, 0))));
  kf.pose = qmul(c, kf.pred);
  const q = pred(57).q;
  nearQuat(r.tracker.livePose(pred(57)) as Quat, qmul(c, q), 1e-12, 'C . pred');
  assert.ok(angDiff(headingDeg(r.tracker.livePose(pred(57)) as Quat), headingDeg(qmul(q, c))) > 0.1, 'not pred . C');
  assert.equal(r.tracker.livePose(null), null);
});

test('in sensor-only mode placement equals the prediction', () => {
  const r = started(0);
  sweep(r, 33, 0.6, 120, 25, 23);
  const kfs = r.tracker.keyframes;
  assert.ok(kfs.length > 20, `${kfs.length} keyframes`);
  const painted = new Map(r.pano.paints.map(p => [p.s.id, p.s]));
  for (const kf of kfs) {
    assert.deepEqual(kf.pose, kf.pred, `keyframe ${kf.id}: pose is the prediction`);
    assert.deepEqual(kf.pred, r.heldPred.get(kf.frameId), `keyframe ${kf.id}: the prediction handed to hold()`);
    assert.deepEqual(painted.get(kf.id)?.pose, kf.pose, `keyframe ${kf.id}: painted at its pose`);
    assert.ok(kf.cls === 'sensor' || kf.cls === 'blurred');
    assert.deepEqual(kf.gainRGB, [1, 1, 1]);
    assert.equal(kf.pyr, null);
    near(qrotate(kf.pose, kf.up)[2], 1, 1e-12, `keyframe ${kf.id}: up is world up seen in the camera`);
  }
  assert.equal(r.tracker.edges.length, 0);
  assert.equal(r.tracker.mismatchRun, 0);
  assert.equal(r.tracker.correctionYawAt(5000), 0);
});

test('kept strips are the central +-10 degrees at L0, as RGB; skyLuma is the median of the top 10 % of rows', () => {
  // Every pixel carries its own column and row, so the strip's provenance is exact; the top 32 rows hold 2000
  // pixels of grey 10, 3000 of grey 120 and 760 of grey 250 (median 120) above a black frame (median 0).
  const rgba = new Uint8ClampedArray(W * H * 4);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const p = (y * W + x) * 4;
    rgba[p] = x; rgba[p + 1] = y & 255; rgba[p + 2] = (x * 7 + y) & 255; rgba[p + 3] = 77;
  }
  const r = rig({}, rgba);
  r.tracker.setActive(true);
  step(r, 0, 0, 5);
  const kf = r.tracker.keyframes[0];
  assert.equal(kf.stripW, 81, 'ceil(2 x 228.5 tan 10) on the default lens');
  assert.equal(kf.stripX0, 50);
  assert.equal(kf.stripH, H);
  assert.equal(kf.strip.length, 81 * H * 3, '77.8 KB');
  const probes: [number, number][] = [[0, 0], [0, 80], [159, 40], [319, 0], [319, 80]];
  for (const [y, c] of probes) {
    // Annotated: the assertions above narrow kf.stripW and kf.stripX0, which tsc cannot resolve inside the loop.
    const at: number = (y * kf.stripW + c) * 3, x: number = kf.stripX0 + c;
    assert.deepEqual([kf.strip[at], kf.strip[at + 1], kf.strip[at + 2]], [x, y & 255, (x * 7 + y) & 255], `strip (${c}, ${y})`);
  }
  const slice = lastSlice(r);
  assert.equal(slice.strip, kf.strip);
  assert.deepEqual([slice.stripX0, slice.stripW, slice.stripH], [50, 81, H]);
  assert.deepEqual(slice.k0, intrinsicsAt(0, W, H, F_DEFAULT), 'k0 from focal.fBest');
  assert.deepEqual([slice.halfWidthDeg, slice.topFrac, slice.gainRGB, slice.id, slice.sigmaDeg],
    [3, 0.9, [1, 1, 1], 0, kf.sigmaDeg]);

  // The strip width and k0 follow fBest at the time; the 4.11 widths for the S25, narrow and wide lenses.
  for (const [shortDeg, width] of [[41.14, 85], [33, 108], [60, 55]] as const) {
    r.focal.fBest = 0.5 / Math.tan(shortDeg / 2 * Math.PI / 180);
    const n = r.tracker.keyframes.length;
    step(r, 1000 * n, 10 * n, 5);
    const k = r.tracker.keyframes[n];
    assert.equal(k.stripW, width, `short axis ${shortDeg}`);
    near(k.stripX0 + k.stripW / 2, W / 2, 0.51, 'centred');
    assert.deepEqual(lastSlice(r).k0, intrinsicsAt(0, W, H, r.focal.fBest));
  }

  const sky = new Uint8ClampedArray(W * H * 4);
  for (let i = 0; i < 32 * W; i++) {
    const g = i < 2000 ? 10 : i < 5000 ? 120 : 250;
    sky[i * 4] = g; sky[i * 4 + 1] = g; sky[i * 4 + 2] = g; sky[i * 4 + 3] = 255;
  }
  const skyRig = rig({}, sky);
  skyRig.tracker.setActive(true);
  step(skyRig, 0, 0, 5);
  assert.equal(skyRig.tracker.keyframes[0].skyLuma, 120);
});

test('sigmaDeg comes from placementSigmaDeg with keyframe 0 as the only anchor', () => {
  const r = started(0);
  sweep(r, 33, 0.6, 200, 20);
  const kfs = r.tracker.keyframes;
  assert.equal(kfs[0].sigmaDeg, 0.5, 'the gauge: sqrt(0.3^2 + (0.04 x 5)^2) = 0.36, then the floor');
  for (const kf of kfs.slice(1)) {
    const az = r.heldAz.get(kf.frameId) as number;
    const want = placementSigmaDeg({ aligned: false, stepsToGauge: kf.id, dPsiToAnchorDeg: az, dtToAnchorS: kf.t / 1000,
      omegaDegS: 20, sigmaTauMs: 40, anchorSigmaDeg: 0, closed: false });
    near(kf.sigmaDeg, want, 1e-9, `keyframe ${kf.id} at ${az.toFixed(2)}`);
    assert.equal(r.pano.paints.find(p => p.s.id === kf.id)?.s.sigmaDeg, kf.sigmaDeg);
  }
  const far = kfs[kfs.length - 1];
  assert.ok(far.sigmaDeg > 3.6 && far.sigmaDeg < 4.5, `about 200 degrees out: ${far.sigmaDeg}`);
});

class ProbeTracker extends Tracker {
  rerender(): void { this.scheduleRerender(); }
}

test('renderAll and pump clear and repaint, renderAll with worldYaw composed; sliceMsP50', () => {
  const pano = new StubPano(), focal = stubFocal(), latency = stubLatency();
  const r: Rig = { tracker: new ProbeTracker({ pano, focal, latency, frameW: W, frameH: H, sensorOnly: true }), pano, focal, latency,
    rgba: new Uint8ClampedArray(W * H * 4), frameNo: 0, reads: 0, heldAz: new Map(), heldPred: new Map() };
  const probe = r.tracker as ProbeTracker;
  r.tracker.setActive(true);
  step(r, 0, 0, 5);
  sweep(r, 33, 0.6, 60, 20);
  const n = r.tracker.keyframes.length;
  assert.equal(pano.paints.length, n, 'one paint per commit');
  assert.equal(r.tracker.pump(30), 0, 'nothing pending');
  assert.equal(pano.paints.length, n);

  probe.rerender();
  assert.equal(pano.clears, 1);
  assert.equal(r.tracker.pendingSlices, n);
  assert.equal(r.tracker.gapDeg, null, 'no gap cue from a raster cleared on purpose');
  assert.equal(r.tracker.pump(2), n - 2);
  assert.deepEqual(pano.paints.slice(n).map(p => p.s.id), [0, 1]);
  step(r, 5000, 75, 5);
  assert.equal(pano.paints[pano.paints.length - 1].s.id, n, 'a commit during the re-render paints at once');
  assert.equal(r.tracker.pump(100), 0);
  assert.equal(pano.paints.length, 2 * n + 1, 'the new keyframe was not queued');

  probe.rerender();
  const before = pano.paints.length;
  r.tracker.renderAll(12.5);
  assert.equal(pano.clears, 3);
  assert.equal(r.tracker.pendingSlices, 0, 'renderAll drops the queue');
  const repaint = pano.paints.slice(before);
  assert.deepEqual(repaint.map(p => p.s.id), r.tracker.keyframes.map(k => k.id));
  for (const p of repaint) {
    const kf = r.tracker.keyframes[p.s.id];
    nearQuat(p.s.pose, qmul(worldYaw(12.5), kf.pose), 1e-15, `slice ${kf.id}`);
    near(angDiff(headingDeg(p.s.pose), headingDeg(kf.pose) + 12.5), 0, 1e-9, `slice ${kf.id} heading`);
    assert.deepEqual(kf.pose, kf.pred, 'the keyframe pose stays in the scan frame');
  }

  // sliceMsP50 times each paint with performance.now(): 2.5 ms per paint on a clock that steps 2.5 ms per read,
  // once those paints fill its window of the newest 32, and 0 under a frozen clock, as in the replay.
  const perf = performance as unknown as { now: () => number };
  let clock = 0;
  try {
    perf.now = () => (clock += 2.5);
    while (clock < 2.5 * 2 * 32) r.tracker.renderAll(0);
    assert.equal(r.tracker.sliceMsP50, 2.5);
    perf.now = () => 1234;
    const z = rig();
    z.tracker.setActive(true);
    step(z, 0, 0, 5);
    sweep(z, 33, 0.6, 30, 20);
    assert.equal(z.tracker.sliceMsP50, 0);
  } finally {
    delete (perf as { now?: () => number }).now;
  }
  assert.equal(typeof performance.now(), 'number');
});

console.log(`trackerV0.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
