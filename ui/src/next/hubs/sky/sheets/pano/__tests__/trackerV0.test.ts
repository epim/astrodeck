// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T19: the tracker v0, the keyframe rule of SPEC-v2 4.3 with placement by the predictor, and the pure functions of
// 4.7 against its worked numbers.
//
// Mutants this file must catch (SPEC-v2 7.2):
// - the window replaced by "commit the first frame at d >= S". `best-of-window commits the slowest candidate` asserts
//   that the frame committed from a window of rates 30, 20, 25, 35 is the rate-20 frame, read back second; under the
//   mutant the rate-30 frame commits at once.
// - rule 8 removed (ruling S27): `rule 8 closes the ring seam` reads gapDeg 5-9.5 after 375 degrees.
// - the kept strip held at +-10 degrees for a trailing fill (ruling S12): `a 10-degree trailing fill at d = 14` finds
//   alpha-0 columns and unpainted coverage cells between the two slices on the real raster.
// - noteReadFailed not clearing the pending read (ruling S28): `noteReadFailed logs read-failed` finds hold() accepted
//   after a failed readback.
//
// The rule tests run in sensor-only mode. The rule is the same in both modes, but T27 adds alignment and rule 5
// (revisits) to the other mode only, and a revisit would commit the slow frames these tests use to probe the
// window. Sensor-only mode keeps the v0 path exactly (T27), so these cases stay true after it. BandPanoramaLike,
// FocalLike and LatencyLike are stubs; the panorama stub marks a simplified footprint in `coverage` (below) so
// that `gapDeg` and `coveredDeg` can be read end to end, and the cases that need the real raster's alpha and
// coverage (S12, and the seam at the design point) paint through T08's BandPanorama. Poses are level (pitch 0)
// unless a case says otherwise, so an axis distance is an azimuth difference and the trailing-fill numbers are exact.
import assert from 'node:assert/strict';
import { lookBasis } from '../../photosphereGeometry';
import { BandPanorama } from '../bandPanorama';
import { headingDeg, intrinsicsAt, qinv, qmul, qrotate, quatFromBasis, worldYaw } from '../rotation';
import {
  FIRST_KF_MAX_WAIT_MS, MAX_KEYFRAMES, STEP_DEG, STRIP_HALF_DEG, TRAILING_MAX_DEG, Tracker, WINDOW_DEG,
  innovationGateDeg, placementSigmaDeg, predictorVarianceDeg2,
} from '../tracker';
import { PANO_H, PANO_W, PROFILE_BINS } from '../types';
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
 *  the edge of the strip (a slice paints only what its strip holds). Side -1 is the image's left, which for an
 *  upright camera is the lower azimuth. */
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
    const edge = (px: number) => Math.atan(px / s.k0.f) * 180 / Math.PI;
    const left = Math.min(2 + (s.trailingSide === -1 ? s.trailingDeg : 0), edge(s.k0.cx - s.stripX0));
    const right = Math.min(2 + (s.trailingSide === 1 ? s.trailingDeg : 0), edge(s.stripX0 + s.stripW - s.k0.cx));
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

interface Rig<P extends BandPanoramaLike = StubPano> {
  tracker: Tracker; pano: P; focal: Mutable<FocalLike>; latency: Mutable<LatencyLike>;
  rgba: Uint8ClampedArray; frameNo: number; reads: number;
  /** Per frame id read back: the azimuth and the prediction handed to hold(). */
  heldAz: Map<number, number>; heldPred: Map<number, Quat>;
}
/** A tracker on the stub, or on `pano` when one is given (the real raster). */
function rig<P extends BandPanoramaLike = StubPano>(o: Partial<TrackerOptions> = {}, rgba = new Uint8ClampedArray(W * H * 4), pano?: P): Rig<P> {
  const p = (pano ?? new StubPano()) as P, focal = stubFocal(), latency = stubLatency();
  const tracker = new Tracker({ pano: p, focal, latency, frameW: W, frameH: H, sensorOnly: true, ...o });
  return { tracker, pano: p, focal, latency, rgba, frameNo: 0, reads: 0, heldAz: new Map(), heldPred: new Map() };
}
/** One frame: onFrame, then hold() on a read and commit() on a commit, as 3.5 steps 3, 5 and 6 do. A null azimuth
 *  is a null prediction. */
function step(r: Rig<BandPanoramaLike>, t: number, az: number | null, rate: number | null, alt = 0): FrameStep {
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
/** One frame whose readback fails (3.5 step 5): onFrame asks to read, the readback is null, and the scanner calls
 *  noteReadFailed and neither hold() nor commit(). */
function failRead(r: Rig, t: number, az: number, rate: number): FrameStep {
  r.frameNo++;
  const s = r.tracker.onFrame(t, { q: poseAt(az), t: t - 50, mode: 'relative', extrapolatedMs: 0, held: false }, rate);
  assert.ok(s.read, 'a frame that asks to read');
  r.tracker.noteReadFailed(t);
  return s;
}
/** A steady turn at 30 frames/s from one azimuth to another; returns the time after the last frame. */
function sweep(r: Rig<BandPanoramaLike>, t: number, from: number, to: number, rate: number, alt = 0): number {
  const dir = Math.sign(to - from), perFrame = rate / 30;
  for (let az = from; dir * (to - az) >= 0; az += dir * perFrame, t += 1000 / 30) step(r, t, az, rate, alt);
  return t;
}
/** A first keyframe at (`az`, `alt`), from one steady frame. */
function started<P extends BandPanoramaLike = StubPano>(az = 0, o: Partial<TrackerOptions> = {}, alt = 0, pano?: P): Rig<P> {
  const r = rig(o, undefined, pano);
  r.tracker.setActive(true);
  assert.deepEqual(step(r, 0, az, 5, alt), { read: true, commit: true, why: 'first' });
  return r;
}
const newest = (r: Rig) => r.tracker.keyframes[r.tracker.keyframes.length - 1];
const lastSlice = (r: Rig) => r.pano.paints[r.pano.paints.length - 1].s;
/** Reaches the protected re-render hook that T27 and T28 call. */
class ProbeTracker extends Tracker {
  rerender(): void { this.scheduleRerender(); }
}

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

  // The window is (t - 1000, t]: a refusal 999 ms old is counted, one exactly 1000 ms old is not (ruling S29).
  const e = started(0);
  step(e, 100, null, 20);
  step(e, 1099, 1, 20);
  assert.equal(e.tracker.staleInLastSecond, 1, '999 ms old');
  step(e, 1100, 1.5, 20);
  assert.equal(e.tracker.staleInLastSecond, 0, 'exactly 1000 ms old');
});

test('an unknown rate is never steady and never replaces a known candidate (S29)', () => {
  // Rule 0: a null rate is not a steady frame, so only the 300 ms timeout takes it.
  const a = rig();
  a.tracker.setActive(true);
  assert.deepEqual(step(a, 0, 0, null), { read: false, commit: false, why: 'waiting-sharper' });
  assert.deepEqual(step(a, 299, 0.2, null), { read: false, commit: false, why: 'waiting-sharper' });
  assert.deepEqual(step(a, 300, 0.4, null), { read: true, commit: true, why: 'first' });

  // The window: the first frame with a null rate is held, not committed at once; a null rate never replaces a
  // candidate whose rate is known, however fast that one was.
  const b = started(0);
  assert.deepEqual(step(b, 100, 4.2, null), { read: true, commit: false, why: 'step' });
  const c = started(0);
  step(c, 100, 4.2, 30);
  const kept = c.frameNo;
  assert.deepEqual(step(c, 133, 4.6, null), { read: false, commit: false, why: 'waiting-sharper' });
  step(c, 166, 6.1, 30);
  assert.equal(c.tracker.keyframes[1].frameId, kept);
});

test('noteReadFailed logs read-failed, drops the step and keeps an earlier candidate (S28)', () => {
  // A failed first readback in the window: logged with the onFrame count as its frame id, the read is closed, nothing
  // is held, and the next frame in the window reads again.
  const a = started(0);
  assert.deepEqual(failRead(a, 100, 4.2, 20), { read: true, commit: false, why: 'step' });
  assert.equal(a.frameNo, 2);
  assert.deepEqual({ ...a.tracker.log[a.tracker.log.length - 1] }, { at: 100, frameId: 2, outcome: 'read-failed' });
  const pred: Prediction = { q: poseAt(4.2), t: 50, mode: 'relative', extrapolatedMs: 0, held: false };
  assert.throws(() => a.tracker.hold({ frameId: 2, t: 100, w: W, h: H, rgba: a.rgba, readbackMs: 1 }, pred, 20, upOf(pred.q)),
    /did not ask for a read/, 'no hold() after a failed readback');
  assert.throws(() => a.tracker.commit(), /no candidate is held/);
  assert.deepEqual(step(a, 133, 4.8, 20), { read: true, commit: false, why: 'step' }, 'the next frame reads again');
  step(a, 166, 6.1, 20);
  assert.equal(a.tracker.keyframes.length, 2);
  assert.equal(a.tracker.keyframes[1].frameId, 3);

  // A failed replacement (20 < 0.8 x 30) keeps the old candidate, and S + 2 commits it.
  const b = started(0);
  step(b, 100, 4.2, 30);
  const kept = b.frameNo;
  assert.deepEqual(failRead(b, 133, 4.6, 20), { read: true, commit: false, why: 'step' });
  assert.equal(b.tracker.log[b.tracker.log.length - 1].outcome, 'read-failed');
  assert.deepEqual(step(b, 166, 6.1, 30), { read: false, commit: true, why: 'step' });
  assert.equal(b.tracker.keyframes[1].frameId, kept);
  near(headingDeg(b.tracker.keyframes[1].pose), 4.2, 1e-9);

  // A failed read that would have committed at once (a steady replacement, a frame past the window) commits nothing.
  const c = started(0);
  step(c, 100, 4.2, 30);
  assert.deepEqual(failRead(c, 133, 4.6, 10), { read: true, commit: true, why: 'step' });
  assert.equal(c.tracker.keyframes.length, 1);
  step(c, 166, 6.1, 30);
  assert.equal(c.tracker.keyframes[1].frameId, 2, 'the old candidate');
  const d = started(0);
  d.tracker.setActive(false);
  step(d, 100, 5, 30);
  d.tracker.setActive(true);
  assert.deepEqual(failRead(d, 400, 9, 5), { read: true, commit: true, why: 'step' });
  assert.equal(d.tracker.keyframes.length, 1);
  assert.deepEqual(step(d, 433, 9.1, 5), { read: true, commit: true, why: 'step' }, 'the next frame reads again');
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
  assert.equal(far.tracker.gapDeg, 6, 'covered to 2 by the first slice, from 8 by the second (2 + 10 back, inside its strip)');
});

test('a 10-degree trailing fill at d = 14 meets the previous slice through the real BandPanorama (S12)', () => {
  // The fill paints the trailing side out to 3 + 10 = 13 degrees, weight 1 to 11 and 0.5 at 12, and the first slice's
  // feather is 0.5 at 2 and 0 at 3, 14 degrees away, so the two meet with no alpha or coverage hole only if the strip
  // holds that side out to 13 degrees. The plain strip ends 9.93 degrees left of the centre (columns 50-130 of 180),
  // which left an alpha-0 sliver from 3 to 4.07 degrees and unpainted coverage from 2 to 4.07. The case is level, so
  // these tangent angles are azimuths. 13 degrees is 228.5 tan 13 = 52.75 px from the centre (90),
  // so the strip runs from column 37 (turning clockwise, the previous slice on the left) or to column 143 (turning
  // counter-clockwise); the leading side keeps the plain edge. Every pixel carries its column, so the copy is checked.
  const rgba = new Uint8ClampedArray(W * H * 4);
  for (let p = 0; p < W * H; p++) { rgba[p * 4] = p % W; rgba[p * 4 + 3] = 255; }
  for (const [from, to, x0, x1] of [[0, 14, 37, 131], [100, 86, 50, 143]] as const) {
    const pano = new BandPanorama();
    pano.begin(0);
    const r = rig({}, rgba, pano);
    r.tracker.setActive(true);
    step(r, 0, from, 5);
    assert.deepEqual(step(r, 400, to, 5), { read: true, commit: true, why: 'step' }, 'past the window with nothing held');

    // Between the two slice centres: every column painted at every row from altitude 20 down to -10, and every
    // coverage cell painted. The lists name what is not.
    const lo = Math.min(from, to), hi = Math.max(from, to);
    const rowTop = Math.ceil((90 - 20) * (PANO_H - 1) / 100);
    const unseen: string[] = [], unpainted: string[] = [];
    for (let x = 0; x < PANO_W; x++) {
      const az = (x + 0.5) * 360 / PANO_W;
      if (az <= lo || az >= hi) continue;
      let rows = 0;
      for (let y = rowTop; y < PANO_H; y++) if (pano.rgba[(y * PANO_W + x) * 4 + 3] !== 255) rows++;
      if (rows > 0) unseen.push(`column ${x} (azimuth ${az.toFixed(2)}): ${rows} rows`);
    }
    for (let i = 0; i < PROFILE_BINS; i++) {
      const az = (i + 0.5) * 360 / PROFILE_BINS;
      if (az > lo && az < hi && pano.coverage[i] === 0) unpainted.push(`cell ${i} (azimuth ${az})`);
    }
    assert.deepEqual(unseen, [], `${from} to ${to}: alpha-0 columns between the slices`);
    assert.deepEqual(unpainted, [], `${from} to ${to}: unpainted coverage cells between the slices`);
    assert.equal(r.tracker.gapDeg, null);

    const kf = r.tracker.keyframes[1];
    assert.deepEqual([kf.stripX0, kf.stripX0 + kf.stripW], [x0, x1], `${from} to ${to}: the strip reaches 13 degrees on the trailing side only`);
    assert.equal(kf.strip.length, kf.stripW * H * 3);
    for (const c of [0, kf.stripW - 1]) assert.equal(kf.strip[(H - 1) * kf.stripW * 3 + c * 3], kf.stripX0 + c, `strip column ${c}`);
  }
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

  // 2.5 degrees cleared inside the swept range count, and exactly 2 (the threshold is inclusive, ruling S29); the same
  // outside it do not.
  r.pano.coverage.fill(0, 40, 45);
  assert.equal(r.tracker.gapDeg, 2.5);
  r.pano.coverage.fill(1, 44, 45);
  assert.equal(r.tracker.gapDeg, 2, 'exactly 2 degrees');
  r.pano.coverage.fill(1, 40, 44);
  r.pano.coverage.fill(0, 100, 105);
  assert.equal(r.tracker.gapDeg, null, '50 degrees is past the newest keyframe: not yet turned to');
  r.pano.coverage.fill(0, 40, 43);
  assert.equal(r.tracker.gapDeg, null, '1.5 degrees is under 2');

  // A 13-degree jump fills to the first slice: 2 + 9 degrees back reaches 2 (a 20-degree jump, above, leaves 6).
  const j = started(0);
  j.tracker.setActive(false);
  step(j, 100, 6, 30);
  j.tracker.setActive(true);
  step(j, 400, 13, 5);
  assert.equal(j.tracker.gapDeg, null);

  // The seam. Steady frames every 0.3 degrees commit at 4.2-degree spacing, so the last window keyframe is at 352.8
  // and the frames after it are within S of keyframe 0: the window never opens on the 7.2 degrees between them, and
  // 3.0 degrees (354.8 to 358) would stay unpainted. Rule 8 reads and commits the frame where the axis crosses their
  // midpoint, 356.4 (or the next frame, 356.7, when rounding leaves 356.4 a hair nearer 352.8), which paints the run.
  const seam = started(0);
  let t = 100;
  for (let i = 1; i * 0.3 <= 359; i++) step(seam, t += 33, i * 0.3, 10);
  near(headingDeg(seam.tracker.keyframes[84].pose), 352.8, 1e-9, 'the last window keyframe');
  const mid = headingDeg(newest(seam).pose);
  assert.ok(mid > 356.4 - 1e-9 && mid < 356.7 + 1e-9, `rule 8 at the midpoint: ${mid}`);
  assert.equal(seam.tracker.keyframes.length, 86);
  assert.equal(seam.tracker.gapDeg, null);
  // Unpainted cells past the newest keyframe are ahead of the user until the turn passes 360, and behind them after;
  // the cells are cleared by hand only while no frame runs, so rule 8 never sees them.
  const painted = seam.pano.coverage.slice();
  seam.pano.coverage.fill(0, 714, 720);
  assert.equal(seam.tracker.gapDeg, null, 'at 359 degrees, 357 to 360 is still ahead');
  seam.pano.coverage.set(painted);
  for (let i = 1; i <= 10; i++) step(seam, t += 33, 359 + i * 0.3, 10);
  assert.equal(seam.tracker.keyframes.length, 86, 'no keyframe past the seam');
  assert.equal(seam.tracker.gapDeg, null);
  seam.pano.coverage.fill(0, 714, 720);
  assert.equal(seam.tracker.gapDeg, 3, 'past 360 the same cells are a gap behind the turn');

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
  assert.equal(j.tracker.coveredDeg, 18, '8 cells around the first slice and 28 from 8 to 22 degrees');
});

// ---- Rule 8, the bracketed gap (ruling S27) -------------------------------------

test('rule 8 closes the ring seam: a steady ring at 15, 20 and 30 deg/s, pitch 0 and 23, leaves no gap after 375 degrees', () => {
  // Keyframes the same rings commit without rule 8 (the T19 tracker, this frame path), which left seams of 5 to 9.5
  // degrees: between keyframes w apart the window opens only for w >= 8 and commits only for w >= 12.
  const before: Record<number, Record<number, number>> = { 0: { 15: 83, 20: 82, 30: 79 }, 23: { 15: 78, 20: 76, 30: 71 } };
  const gaps: string[] = [], extras: string[] = [];
  for (const alt of [0, 23]) for (const rate of [15, 20, 30]) {
    const r = started(0, {}, alt);
    sweep(r, 33, rate / 30, 375, rate, alt);
    const what = `pitch ${alt} at ${rate} deg/s`, extra = r.tracker.keyframes.length - before[alt][rate];
    if (r.tracker.gapDeg !== null) gaps.push(`${what}: ${r.tracker.gapDeg}`);
    if (extra < 1 || extra > 2) extras.push(`${what}: ${extra}`);
  }
  assert.deepEqual(gaps, [], 'gapDeg after the full turn');
  assert.deepEqual(extras, [], 'keyframes more than without rule 8, outside 1 to 2');

  // The design point through the real raster, whose coverage cells read the painted classes between altitude -2 and
  // +10, where a slice at pitch 23 is about 2 degrees of azimuth either side.
  const pano = new BandPanorama();
  pano.begin(0);
  const real = started(0, {}, 23, pano);
  sweep(real, 33, 20 / 30, 375, 20, 23);
  assert.equal(real.tracker.gapDeg, null, 'the real raster: no gap after the full turn');
  const extra = real.tracker.keyframes.length - before[23][20];
  assert.ok(extra >= 1 && extra <= 2, `the real raster: ${extra} keyframes more than without rule 8`);
});

test('rule 8: a reversal over normal keyframes adds none', () => {
  // Neighbours S to S + one frame step apart leave no unpainted run of 2 degrees, so crossing their midpoints back
  // and forth reads nothing and commits nothing. In five of the six cases a candidate is held at the far end (all but
  // pitch 0 at 20 deg/s), which a rule 8 fired without its gap test would commit.
  for (const alt of [0, 23]) for (const rate of [15, 20, 30]) {
    const r = started(0, {}, alt);
    let t = sweep(r, 33, rate / 30, 90, rate, alt);
    const n = r.tracker.keyframes.length, reads = r.reads;
    t = sweep(r, t, 88, 2, rate, alt);
    t = sweep(r, t, 2, 88, rate, alt);
    sweep(r, t, 88, 2, rate, alt);
    assert.equal(r.tracker.keyframes.length, n, `pitch ${alt} at ${rate} deg/s: no keyframe`);
    assert.equal(r.reads, reads, `pitch ${alt} at ${rate} deg/s: no readback`);
  }
});

test('rule 8 fires on a bracketed run of exactly 2 degrees, the gap threshold, and not on 1.5', () => {
  // Keyframes at 0 and 4.3 leave cell 4 (2.0-2.5) unpainted; clearing cells 2-5 by hand makes the run between them
  // exactly 2 degrees, and 2-4 makes it 1.5. Turning back across their midpoint (2.15) is the crossing.
  for (const [clearTo, commits] of [[6, true], [5, false]] as const) {
    const r = started(0);
    assert.deepEqual(step(r, 100, 4.3, 12), { read: true, commit: true, why: 'step' });
    r.pano.coverage.fill(0, 2, clearTo);
    assert.equal(step(r, 133, 3.0, 12).why, 'covered', 'nearest is the new keyframe: not a crossing');
    assert.deepEqual(step(r, 166, 2.0, 12), commits ? { read: true, commit: true, why: 'step' } : { read: false, commit: false, why: 'covered' },
      `a run of ${(clearTo - 2) / 2} degrees`);
    assert.equal(r.tracker.keyframes.length, commits ? 3 : 2);
  }
});

test('rule 8 waits while a re-render is pending, because the raster is then cleared on purpose', () => {
  const pano = new StubPano(), focal = stubFocal(), latency = stubLatency();
  const r: Rig = { tracker: new ProbeTracker({ pano, focal, latency, frameW: W, frameH: H, sensorOnly: true }), pano, focal, latency,
    rgba: new Uint8ClampedArray(W * H * 4), frameNo: 0, reads: 0, heldAz: new Map(), heldPred: new Map() };
  r.tracker.setActive(true);
  step(r, 0, 0, 5);
  let t = sweep(r, 33, 0.6, 40, 20);
  const n = r.tracker.keyframes.length, reads = r.reads;
  (r.tracker as ProbeTracker).rerender();
  t = sweep(r, t, 38, 2, 20);
  assert.equal(r.tracker.keyframes.length, n, 'every midpoint crossed over a cleared raster, nothing committed');
  assert.equal(r.reads, reads);
  assert.equal(r.tracker.pump(100), 0);
  sweep(r, t, 2, 38, 20);
  assert.equal(r.tracker.keyframes.length, n, 'nor once it is repainted');
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
