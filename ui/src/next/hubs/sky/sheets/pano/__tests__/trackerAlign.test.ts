// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T27: the tracker's stage 2, alignment, fusion and the focal lock (SPEC-v2 4.5-4.8, 4.10, 7.2's tracker stage 2 row).
//
// The tracker runs here as the scanner runs it (3.5): a synthetic pan (T17) gives the frames and the sensor streams, the
// real PoseTrack turns the streams into predictions and rates, the real FocalEstimator, LatencyEstimator and BandPanorama
// sit behind the tracker, and `onFrame`, `hold`, `commit` and `pump` are called in the order of the frame callback. Frames
// carry the simulator's sensor noise (luma sd 2 per channel, S37).
//
// Mutants this file must catch (SPEC-v2 7.2 and the T27 brief), each against the cases named; every one was applied to
// tracker.ts from a byte backup and turned the cases below red (the first failing line of each is in the T27 report):
//   * the focal term dropped from Sigma_img (`iy = cy` in `fuse`, no `(sdF dPsi)^2`): 'pre-lock, the chain follows the gyro
//     and not the 10 % wrong focal' finds keyframe 2 0.61 degrees off (3.6 at the tenth), and 'W_yaw is 0.11 before the lock
//     and 0.99 after' finds the weight at 1.00 before it;
//   * the post-lock gate width applied before the lock (`locked: true` in `fuse`'s gate): 'a wide lens locks' finds a ring
//     whose matches are refused for want of the widened gate, so the focal never locks, and 'the gate is wide before the
//     lock and 1.93 after' finds the 3-degree disagreement refused before the lock;
//   * addPair fed the absolute omega instead of dOmega (`Math.abs(b.omega)` in `feedLatency`): 'an injected 80 ms of
//     latency is recovered within 10 ms' finds tau at 250 (the clamp), and 'a steady ring feeds the latency estimator only
//     its accelerating pairs' finds 55 pairs fed over 77 keyframes.
// Further mutants of this file, each against a case of its own: the S34 widening removed ('a trailing fill meets the previous
// slice ... at pitch 0 and 23'); a rate over two consecutive frames ('the signed heading rate at a keyframe ...'); rule 5
// never firing ('a revisit upgrades amber'); the S32 re-anchor removed; the tilt pull removed; the ratio's sign flipped
// (every lock case: 19 of 31 red, the S19 guard); the gains chained the wrong way round; the edges not re-solved at the lock;
// correctionYawAt always 0; no re-solve at f_best before the lock; a chain pair aligned against the nearest keyframe in the
// corrected frame ('a chain commit aligns against the previous keyframe'); the per-step gain clamp removed ('a large exposure
// jump is clamped per step'); the steady span's strict `<` made `<=` ('the steady-pair test is strict'); the S32 drop leaving the
// dropped keyframe's capture record behind ('a first keyframe taken from an absolute-only sample').
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { replayCase } from '../../__sim__/replay';
import { DEG, lookBasis } from '../../photosphereGeometry';
import { BandPanorama } from '../bandPanorama';
import { FocalEstimator, PRIOR_SD_PCT } from '../focal';
import { PoseTrack } from '../poseTrack';
import { angleBetweenDeg, basisFromQuat, deviceOrientationFromQuat, elevationDeg, headingDeg, logSO3, priorFNorm, qinv, qmul, qrotate, quatFromBasis, worldYaw } from '../rotation';
import { Tracker, innovationGateDeg, predictorVarianceDeg2 } from '../tracker';
import { PANO_H, PANO_W, PixClass } from '../types';
import type { AnalysisFrame, BandPanoramaLike, FocalLike, Intrinsics, Keyframe, LatencyLike, Prediction, PredictorMode, Quat } from '../types';
import { DEFAULT_PAN, makeSynthScene, renderView, synthPan, writeReplayInput, type SynthPan, type SynthPanOptions, type SynthScene } from './synth/synth';

// A failing case is reported and the file carries on, so a mutant shows every case it turns red, not only the first
// (the pattern of align.test.ts); the exported `failed` and the exit code carry the failure.
let passed = 0, failed = 0;
function test(name: string, fn: () => void) {
  try { fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; console.log(`FAIL ${name}\n  ${(e as Error).message.split(/\r?\n/)[0]}`); }
}

const W = 180, H = 320;
/** synth DEFAULT_PAN's gyro scale error. */
const SG = 0.02;
const wrap180 = (d: number) => ((((d + 180) % 360) + 360) % 360) - 180;
const near = (a: number, b: number, tol: number, what: string) => assert.ok(Math.abs(a - b) <= tol, `${what}: ${a} is not within ${tol} of ${b}`);

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
/** Four uniforms summed: sd 1 (Irwin-Hall), about a tenth of the cost of `gauss` and enough for a noise floor. */
const noise1 = (r: () => number) => (r() + r() + r() + r() - 2) * Math.sqrt(3);

// ---- A pan whose speed varies --------------------------------------------------------------------------------------

/** A one-way pan at one pitch whose speed follows `speed(tS)` (negative turns the other way), with the sensor streams a
 *  phone gives (13.1): a relative orientation stream on a 60 Hz change-driven pump (quantised to 0.1 degree, a gyro scale
 *  error and drift in its heading) and `devicemotion` at 60 Hz. synthPan has only constant-speed legs, so its keyframes see
 *  a change of heading rate only at the ends of a leg; the latency estimator needs pairs with |dOmega| > 2 all along (4.5).
 *  Readings carry no pump age here, so the sensor-to-frame offset a test injects through `lagMs` is exactly the one the
 *  estimator should return. */
function modulatedPan(o: {
  scene: SynthScene; shortFovDeg: number; pitchDeg: number; durationS: number; lagMs: number; speed(tS: number): number;
}): SynthPan {
  const fps = 30, drift = 2;
  const k0 = { w: W, h: H, f: 0.5 / Math.tan(o.shortFovDeg * DEG / 2) * W, cx: W / 2, cy: H / 2 };
  // The azimuth by integrating the speed in 1 ms steps; the pan holds still for 0.5 s, then ramps up over 0.5 s.
  const total = Math.round(o.durationS * 1000), az = new Float64Array(total + 1);
  for (let ms = 1; ms <= total; ms++) {
    const tS = ms / 1000, ramp = Math.min(1, Math.max(0, (tS - 0.5) / 0.5));
    az[ms] = az[ms - 1] + o.speed(tS) * ramp * ramp * (3 - 2 * ramp) / 1000;
  }
  const azAt = (ms: number) => { const c = Math.min(total, Math.max(0, ms)), i = Math.floor(c); return az[i] + (az[Math.min(total, i + 1)] - az[i]) * (c - i); };
  const poseAt = (ms: number): Quat => quatFromBasis(lookBasis(((azAt(ms) % 360) + 360) % 360, o.pitchDeg));
  const r = rng(5), yaw0 = r() * 360;
  const rows: Record<string, unknown>[] = [];
  let last: { alpha: number; beta: number; gamma: number } | null = null;
  const round = (x: number) => Math.round(x * 10) / 10;
  for (let tick = 0; tick <= total; tick += 1000 / 60) {
    const offset = yaw0 + SG * azAt(tick) + drift / 60 * tick / 1000;
    const held = deviceOrientationFromQuat(qmul(worldYaw(offset), poseAt(tick)));
    if (last) {
      const dA = Math.abs(((held.alpha - last.alpha + 540) % 360) - 180);
      if (!(dA >= 0.1 - 1e-9 || Math.abs(held.beta - last.beta) >= 0.1 - 1e-9 || Math.abs(held.gamma - last.gamma) >= 0.1 - 1e-9)) continue;
    }
    last = held;
    const t = Math.round(tick);
    rows.push({ kind: 'orientation', event: 'deviceorientation', t_event_ms: t, t_receive_ms: t + 5, alpha: round(held.alpha) % 360, beta: round(held.beta), gamma: round(held.gamma), absolute: false });
  }
  for (let tick = 0; tick <= total; tick += 1000 / 60) {
    const t0 = Math.max(0, tick - 8.3), t1 = Math.min(total, tick + 8.3);
    const v = logSO3(qmul(qinv(poseAt(t0)), poseAt(t1)));
    const rate = [0, 1, 2].map(i => round(v[i] / DEG / ((t1 - t0) / 1000) * (1 + SG) + 0.01 + 0.03 * gauss(r)));
    const t = Math.round(tick);
    rows.push({ kind: 'motion', t_event_ms: t, t_receive_ms: t + 5, rate: { alpha: rate[0], beta: rate[1], gamma: rate[2] } });
  }
  const frames: SynthPan['frames'] = [];
  for (let k = 0; Math.round(k * 1000 / fps) <= total; k++) {
    const exposure = Math.round(k * 1000 / fps), truth = poseAt(exposure), capture = exposure + Math.round(o.lagMs);
    frames.push({ frameId: `f${String(k).padStart(6, '0')}`, tCaptureMs: capture, tPresentMs: capture + 10, truth, render: () => renderView(o.scene, truth, k0) });
  }
  return { frames, observations: rows.sort((a, b) => (a.t_receive_ms as number) - (b.t_receive_ms as number)), actions: [], k0 };
}

// ---- A faster renderView ---------------------------------------------------------------------------------------------

/** A scene baked on a 0.2-degree grid (azimuth 0 to 360, altitude -30 to 90), with one bilinear sample per pixel: a frame costs
 *  2.4 ms against 44 for synth.ts's `renderView` (2 x 2 supersampled, direct), and differs from it by 0.1 grey level on
 *  average (90 at a skyline edge, which the baked scene softens by 0.2 degree). The ring and its variants read some 340 frames, which the
 *  exact renderer would spend 15 s on. The replay proof below still uses the exact one. */
const BAKE_STEP = 0.2, BAKE_AZ = 1800, BAKE_ALT0 = -30, BAKE_ALT = 601;
const baked = new Map<SynthScene, Float32Array>();
function bakeOf(scene: SynthScene): Float32Array {
  let data = baked.get(scene);
  if (!data) {
    data = new Float32Array(BAKE_AZ * BAKE_ALT * 3);
    for (let j = 0; j < BAKE_ALT; j++) for (let i = 0; i < BAKE_AZ; i++) {
      const c = scene.sample(i * BAKE_STEP, BAKE_ALT0 + j * BAKE_STEP), o = (j * BAKE_AZ + i) * 3;
      data[o] = c[0]; data[o + 1] = c[1]; data[o + 2] = c[2];
    }
    baked.set(scene, data);
  }
  return data;
}
function viewOf(scene: SynthScene, q: Quat, k: Intrinsics): Uint8ClampedArray {
  const data = bakeOf(scene), { right, up, forward } = basisFromQuat(q), out = new Uint8ClampedArray(k.w * k.h * 4);
  const inv = 1 / BAKE_STEP / DEG;
  for (let y = 0; y < k.h; y++) {
    const vy = -(y + 0.5 - k.cy) / k.f;
    for (let x = 0; x < k.w; x++) {
      const vx = (x + 0.5 - k.cx) / k.f;
      const dx = forward[0] + vx * right[0] + vy * up[0], dy = forward[1] + vx * right[1] + vy * up[1], dz = forward[2] + vx * right[2] + vy * up[2];
      let fa = Math.atan2(dx, dy) * inv;
      if (fa < 0) fa += BAKE_AZ;
      let fb = Math.atan2(dz, Math.hypot(dx, dy)) * inv - BAKE_ALT0 / BAKE_STEP;
      fb = fb < 0 ? 0 : fb > BAKE_ALT - 1.001 ? BAKE_ALT - 1.001 : fb;
      const i0 = fa | 0, j0 = fb | 0, ua = fa - i0, ub = fb - j0, i1 = i0 + 1 === BAKE_AZ ? 0 : i0 + 1;
      const a = (j0 * BAKE_AZ + i0) * 3, b = (j0 * BAKE_AZ + i1) * 3, c = ((j0 + 1) * BAKE_AZ + i0) * 3, d = ((j0 + 1) * BAKE_AZ + i1) * 3;
      const o = (y * k.w + x) * 4, w00 = (1 - ua) * (1 - ub), w10 = ua * (1 - ub), w01 = (1 - ua) * ub, w11 = ua * ub;
      out[o] = data[a] * w00 + data[b] * w10 + data[c] * w01 + data[d] * w11;
      out[o + 1] = data[a + 1] * w00 + data[b + 1] * w10 + data[c + 1] * w01 + data[d + 1] * w11;
      out[o + 2] = data[a + 2] * w00 + data[b + 2] * w10 + data[c + 2] * w01 + data[d + 2] * w11;
      out[o + 3] = 255;
    }
  }
  return out;
}

// ---- The scanner's frame path over a pan -----------------------------------------------------------------------------

/** The default scene of synth.ts. */
const SCENE = makeSynthScene({ seed: 3 });
/** Frame noise, sd per channel: the simulator's `noise_sigma` (S37). */
const FRAME_NOISE = 2;
/** Rate limit at 30 fps (rateMaxFromInterval of a 33 ms median interval). */
const RATE_MAX = 40;

interface RunOptions {
  pan?: Partial<SynthPanOptions>;
  /** A prepared pan, instead of synthPan's (see modulatedPan). */
  panOverride?: (scene: SynthScene) => SynthPan;
  scene?: SynthScene;
  /** The focal prior as a multiple of the true fNorm: 1.1 is the "10 % focal prior error" of the 7.2 row. */
  priorScale?: number;
  /** A replacement for the FocalEstimator (e.g. one that never locks). */
  makeFocal?: (prior: number) => FocalLike;
  makeLatency?: (real: LatencyLike) => LatencyLike;
  /** rateMax at a frame (index into the pan). */
  rateMaxAt?: (frameIndex: number) => number;
  /** Extra yaw the image shows beyond the truth, degrees, for the n-th readback (0-based); the sensors do not know. */
  imageYaw?: (readNo: number) => number;
  /** A per-channel exposure on the n-th readback, applied before the sensor noise. */
  frameGain?: (readNo: number) => readonly [number, number, number];
  /** The rate onFrame is given: the mapped gyro's as PoseTrack gives it, or none (the tracker then has only its own chord;
   *  hold() still gets the rate, which only classes the keyframe). */
  rate?: 'gyro' | 'none';
  /** A factor on the rate handed in: pi / 180 is a gyro in rad/s read as deg/s (S33), which reads quiet while turning. */
  rateScale?: number;
  sensorOnly?: boolean;
}
interface Snap { id: number; frameIndex: number; pose: Quat; cls: string; mismatchRun: number; ratiosBefore: number }
interface Run {
  pan: SynthPan; tracker: Tracker; focal: FocalLike; latency: LatencyLike; track: PoseTrack; pano: BandPanorama; fTrue: number;
  snaps: Snap[]; reads: number; lock: { kf: number; ratios: number } | null; clears: number; pendingAtLock: number;
  truth(kf: Keyframe): Quat;
}

function run(o: RunOptions = {}): Run {
  const scene = o.scene ?? SCENE;
  const pan = o.panOverride ? o.panOverride(scene) : synthPan({ ...DEFAULT_PAN, scene, fps: 30, ...o.pan });
  const fTrue = pan.k0.f / Math.min(pan.k0.w, pan.k0.h);   // fNorm = f / shortEdgePx (3.2)
  const prior = fTrue * (o.priorScale ?? 1);
  const focal: FocalLike = o.makeFocal ? o.makeFocal(prior) : new FocalEstimator({ fNorm: prior, source: 'default', sdPct: PRIOR_SD_PCT });
  const track = new PoseTrack();
  const latency = o.makeLatency ? o.makeLatency(track.latency) : track.latency;
  const pano = new BandPanorama();
  pano.begin(0);
  let clears = 0, pendingAtLock = -1;
  const clear = pano.clear.bind(pano);
  pano.clear = () => { clears++; clear(); };
  const tracker = new Tracker({ pano, focal, latency, frameW: W, frameH: H, sensorOnly: o.sensorOnly });

  type Item = { t: number; frame?: number; obs?: Record<string, unknown> };
  const items: Item[] = [];
  for (const obs of pan.observations) items.push({ t: obs.t_receive_ms as number, obs });
  pan.frames.forEach((f, frame) => items.push({ t: f.tPresentMs, frame }));
  items.sort((a, b) => a.t - b.t || (a.obs ? -1 : 1));   // a reading before a frame at the same millisecond (replay.ts)

  let begun = false, reads = 0, lock: Run['lock'] = null;
  const snaps: Snap[] = [];
  for (const item of items) {
    if (item.obs) {
      const ob = item.obs;
      if (ob.kind === 'orientation') {
        track.onOrientation({
          t: ob.t_event_ms as number, type: ob.event as 'deviceorientation' | 'deviceorientationabsolute', alpha: ob.alpha as number,
          beta: ob.beta as number, gamma: ob.gamma as number, absolute: ob.absolute as boolean, compassHeading: null,
        });
      } else track.onMotion({ t: ob.t_event_ms as number, rate: ob.rate as { alpha: number; beta: number; gamma: number } });
      continue;
    }
    const index = item.frame as number, fr = pan.frames[index];
    const t = fr.tCaptureMs ?? fr.tPresentMs;
    if (!begun && track.canPredict) { track.latch(t); begun = true; }
    tracker.setRateMax(o.rateMaxAt ? o.rateMaxAt(index) : RATE_MAX);
    const pred = track.predictAt(t - latency.tauMs);
    const at = track.rateAt(t), measured = at === null ? null : at * (o.rateScale ?? 1);
    const rate = o.rate === 'none' ? null : measured;
    tracker.setActive(begun);
    const step = tracker.onFrame(t, pred, rate);
    if (step.read && pred) {
      const extra = o.imageYaw ? o.imageYaw(reads) : 0;
      const rgba = viewOf(scene, extra === 0 ? fr.truth : qmul(worldYaw(extra), fr.truth), pan.k0);
      const gain = o.frameGain ? o.frameGain(reads) : [1, 1, 1];
      const noise = rng(1000 + index);
      for (let i = 0; i < rgba.length; i += 4) for (let c = 0; c < 3; c++) rgba[i + c] = rgba[i + c] * gain[c] + FRAME_NOISE * noise1(noise);
      reads++;
      const f: AnalysisFrame = { frameId: index + 1, t, w: W, h: H, rgba, readbackMs: 1 };
      tracker.hold(f, pred, measured ?? Infinity, qrotate(qinv(pred.q), [0, 0, 1]));
    }
    if (step.commit) {
      const before = focal.state, ratiosBefore = focal.ratios;
      const kf = tracker.commit();
      snaps.push({ id: kf.id, frameIndex: kf.frameId - 1, pose: kf.pose, cls: kf.cls, mismatchRun: tracker.mismatchRun, ratiosBefore });
      if (before === 'prior' && focal.state !== 'prior' && lock === null) { lock = { kf: kf.id, ratios: focal.ratios }; pendingAtLock = tracker.pendingSlices; }
    }
    tracker.pump(30);
  }
  return { pan, tracker, focal, latency, track, pano, fTrue, snaps, reads, lock, clears, pendingAtLock, truth: kf => pan.frames[kf.frameId - 1].truth };
}

/** The chain's yaw error at a keyframe: its pose's heading relative to keyframe 0's, minus the truth's turn times `scale`
 *  (1 is the truth itself; 1 + s_g is the gyro-degree truth an unclosed chain can reach). */
function yawError(r: Run, pose: Quat, kf: Keyframe, scale = 1): number {
  const k0 = r.tracker.keyframes[0];
  const measured = wrap180(headingDeg(pose) - headingDeg(k0.pose));
  const truth = wrap180(headingDeg(r.truth(kf)) - headingDeg(r.truth(k0)));
  return wrap180(measured - truth * scale);
}

/** The capture record of the commit that made keyframe `id`. */
const recordOf = (r: Run, id: number) => r.tracker.log.find(x => x.kf === id && (x.outcome === 'accepted' || x.outcome === 'revisit'));
/** The tracker's per-keyframe table of signed heading rates, which is private: the tests below read it to pin the rate source. */
const omegasOf = (r: Run) => (r.tracker as unknown as { links: { omega: number | null }[] }).links.map(l => l.omega);

// ---- 1. A ring: keyframes, the lock, the focal ----------------------------------------------------------------------

// The 7.2 row: 2 deg/min drift and 2 % gyro scale (synth's defaults), a focal prior 10 % too long, and an exposure that
// drifts 0.2 to 0.4 % a keyframe, per channel, as an auto-exposure does.
const EXPOSURE = (n: number): [number, number, number] => [1 + 0.004 * n, 1 + 0.003 * n, 1 + 0.002 * n];
const ring = run({ priorScale: 1.1, frameGain: EXPOSURE });
const ringKfs = ring.tracker.keyframes;
const ringLock = ring.lock as { kf: number; ratios: number };

test('a ring with 2 deg/min drift, 2 % gyro scale and a 10 % focal prior error: about 80 keyframes, 80 % aligned, the lock', () => {
  // 385 degrees at ring pitch 23 and 4.7 degrees of azimuth a keyframe (the window's first frame is 4 to 4.6 degrees of
  // axis from the last): about 77. The 7.2 row's 83 is the ideal 4.345-degree step of 82.9 keyframes a ring.
  assert.ok(ringKfs.length >= 70 && ringKfs.length <= 92, `${ringKfs.length} keyframes`);
  const aligned = ringKfs.filter(k => k.cls === 'aligned').length;
  assert.ok(aligned / ringKfs.length >= 0.8, `${aligned} of ${ringKfs.length} aligned`);
  assert.ok(ring.lock, 'the focal locked');
  assert.ok(ringLock.ratios >= 5 && ringLock.ratios <= 12, `${ringLock.ratios} ratios at the lock`);
  assert.equal(ring.focal.state, 'locked');
  // The 7.2 row's 2 %, on this ring (+1.45 %). It is not general for a 10 %-long prior: see the backwards ring.
  near(ring.focal.fBest / (ring.fTrue / (1 + SG)) - 1, 0, 0.02, 'f within 2 % of f_true / (1 + s_g)');
  assert.ok(ring.focal.sdPct >= 0.55, `sdPct ${ring.focal.sdPct} is floored at 0.55`);
});

test('the lock re-solves, re-fuses and repaints through pump: one clear, every keyframe so far queued, drained by the end', () => {
  // The lock commit schedules a repaint of the keyframes so far (at most about 13, 4.8); the scanner's pump drains it at 30
  // slices a frame, and the raster is cleared once.
  assert.equal(ring.pendingAtLock, ringLock.kf + 1, 'every keyframe so far is pending at the lock');
  assert.ok(ring.pendingAtLock <= 13, `${ring.pendingAtLock} slices to repaint`);
  assert.equal(ring.clears, 1, 'the raster is cleared once, for the repaint');
  assert.equal(ring.tracker.pendingSlices, 0, 'drained');
  assert.ok(ring.tracker.coveredDeg >= 300, `${ring.tracker.coveredDeg} degrees painted after the repaint`);
  assert.equal(ring.tracker.log.filter(x => x.detail === 'focal-lock').length, 1);
  assert.equal(recordOf(ring, ringLock.kf)?.detail, 'focal-lock');
  // The steady pre-lock edges were re-solved from their correspondences at f_lock (Horn): they read the same share of the gyro's
  // rotation as an edge LK measured at f_lock. Left at the prior f0, 10 % long, they would read 0.93 of that share.
  const angle = (q: Quat) => 2 * Math.acos(Math.min(1, Math.abs(q[0]))) / DEG;
  const share = (e: { a: number; b: number; qBA: Quat }) => angle(e.qBA) / angleBetweenDeg(ringKfs[e.a].pred, ringKfs[e.b].pred);
  const post = ring.tracker.edges.filter(e => e.b > ringLock.kf + 2).map(share).sort((x, y) => x - y);
  const reference = post[post.length >> 1];
  // Each edge's share carries the 0.1-degree quantisation and the pump age of its two predictions (3 to 4 %), so the mean of the
  // steady ones is compared.
  const preShares = ring.tracker.edges.filter(x => x.b >= 3 && x.b <= ringLock.kf).map(share);
  near(preShares.reduce((x, y) => x + y, 0) / preShares.length, reference, 0.02, 'the steady pre-lock edges, re-solved');
});

test('W_yaw is 0.11 before the lock and 0.99 after (S38: 0.113 at the default prior; the sd floor gives 0.97 and up)', () => {
  const wYaw = (from: number, to: number) => ringKfs.slice(from, to).map(k => recordOf(ring, k.id)?.wYaw as number);
  for (const w of wYaw(3, ringLock.kf)) near(w, 0.113, 0.02, 'a steady pre-lock pair');
  for (const w of wYaw(ringLock.kf + 2, ringKfs.length)) assert.ok(w > 0.95, `post-lock W_yaw ${w}`);
  // The worked case of 4.7: Sigma_g 0.0965, covRad2 0.0025, the focal term (0.2 x 4.345)^2.
  near(0.0965 / (0.0965 + 0.0025 + (0.2 * 4.345) ** 2), 0.113, 0.0005, 'the worked case');
});

test('every ring step is a chain edge to the previous keyframe with its correspondences, and sigmas follow 4.7', () => {
  const edges = ring.tracker.edges;
  assert.equal(edges.length, ringKfs.length - 1, 'one edge a step, none for the gauge');
  edges.forEach((e, i) => {
    assert.deepEqual([e.kind, e.a, e.b], ['chain', i, i + 1]);
    assert.ok(e.corr.length >= 8 && e.corr.length <= 16, `edge ${i}: ${e.corr.length} correspondences`);
    assert.ok(e.zncc >= 0.7 && e.n > 1000 && e.covRad2[4] > 0 && Number.isFinite(e.covRad2[4]), `edge ${i}`);
  });
  // An aligned keyframe is 0.05 sqrt(s) for s chain steps to the gauge; the gauge is the 0.5 floor.
  for (const k of ringKfs) {
    if (k.cls === 'aligned') near(k.sigmaDeg, 0.05 * Math.sqrt(Math.max(1, k.id)), 1e-12, `keyframe ${k.id}`);
  }
  assert.equal(ringKfs[0].sigmaDeg, 0.5);
});

test('after the lock the chain is image-led: far better than the gyro, bounded tilt of C, innovations under a degree', () => {
  // The lock's focal is good to about 2 % (here +1.45 %), so the chain's scale is the truth's to a fraction of a percent
  // where the gyro's is 2 % out: over 385 degrees the gyro alone is 7.7 degrees off, the chain a few. The closure (T28)
  // removes the rest.
  const chainWorst = Math.max(...ringKfs.map(k => Math.abs(yawError(ring, k.pose, k, 1))));
  const gyroWorst = Math.max(...ringKfs.map(k => Math.abs(yawError(ring, k.pred, k, 1))));
  assert.ok(gyroWorst > 7, `the gyro alone is ${gyroWorst} degrees off by the end`);
  assert.ok(chainWorst < 3.0 && chainWorst < gyroWorst / 2, `the chain is ${chainWorst} degrees off, the gyro ${gyroWorst}`);
  for (const k of ringKfs) assert.ok(Math.abs(elevationDeg(k.pose) - elevationDeg(k.pred)) < 0.6, `keyframe ${k.id}: tilt of C ${elevationDeg(k.pose) - elevationDeg(k.pred)}`);
  const inn = ring.tracker.log.filter(x => x.outcome === 'accepted' && x.kf !== undefined && x.kf > ringLock.kf && x.innovationDeg !== undefined).map(x => x.innovationDeg as number);
  assert.ok(inn.length > 50);
  assert.ok(Math.max(...inn) < 1.0, `post-lock innovations up to ${Math.max(...inn)}`);
  // The signed heading rate of every keyframe after the start is the gyro's 20.4 deg/s with the sign of the turn (S10).
  for (const w of omegasOf(ring).slice(3)) near(w as number, 20.4, 0.6, 'heading rate');
});

test('a steady ring feeds the latency estimator only its accelerating pairs (dOmega, not omega: the mutant feeds all)', () => {
  // At 20 deg/s every pair has |omega| = 20, which a regressor on omega would take for a pair; the steady ones have
  // |dOmega| < 2 and the estimator drops them. The start ramp gives a handful.
  assert.ok(ring.latency.pairs >= 1, 'addPair is fed');
  assert.ok(ring.latency.pairs <= 12, `${ring.latency.pairs} pairs fed over ${ringKfs.length} keyframes`);
  assert.equal(ring.latency.applied, false, 'a handful of pairs apply nothing');
});

test('chained gains follow a drifting exposure and normalise to a median of 1 at Finish (4.10)', () => {
  // Exposure e_n multiplies the n-th readback, so a perfect chain has gain_n x e_n constant per channel (e_0 = 1).
  let worst = 0;
  for (let n = 0; n < ringKfs.length; n++) {
    const e = EXPOSURE(n), g = ringKfs[n].gainRGB;
    for (let c = 0; c < 3; c++) worst = Math.max(worst, Math.abs(g[c] * e[c] - 1));
  }
  assert.ok(worst < 0.03, `gain x exposure off by ${worst}`);
  assert.deepEqual(ringKfs[0].gainRGB, [1, 1, 1], 'the gauge keeps gain 1');
  // The Finish normalisation: the median keyframe gain is 1 per channel, and the relative gains are kept.
  const before = ringKfs.map(k => [...k.gainRGB]);
  ring.tracker.renderAll(0);
  for (let c = 0; c < 3; c++) {
    const g = ringKfs.map(k => k.gainRGB[c]).sort((a, b) => a - b), m = g.length;
    near(m % 2 ? g[(m - 1) / 2] : (g[m / 2 - 1] + g[m / 2]) / 2, 1, 1e-9, `median gain of channel ${c}`);
    near(ringKfs[10].gainRGB[c] / ringKfs[20].gainRGB[c], before[10][c] / before[20][c], 1e-9, 'relative gains kept');
  }
});

// The ring's exposure drifts 0.2 to 0.4 % a keyframe, so no step comes near the limits of 0.67 and 1.5 and only a jump can grade
// them. Here one channel of one readback is darkened to 0.4 (an auto-exposure catching a light, a hand over the lens): the ratio
// of overlap means is 2.5 into that frame and 0.4 out of it, past the clamp both ways. Readback n is keyframe n in a steady ring.
const JUMPS: Record<number, [number, number, number]> = { 12: [1, 0.4, 1], 20: [1, 1, 0.4] };
const jump = run({ priorScale: 1.1, pan: { turnDeg: 150 }, frameGain: n => JUMPS[n] ?? [1, 1, 1] });

test('a large exposure jump is clamped per step to 0.67..1.5, and the chain comes back to level after it (4.10)', () => {
  const kfs = jump.tracker.keyframes, g = (n: number, c: number) => kfs[n].gainRGB[c];
  assert.ok(kfs.length > 24, `${kfs.length} keyframes`);
  assert.ok(kfs.slice(11, 23).every(k => k.cls === 'aligned'), 'the dark frames still align: the gain is chained from their edges');
  const step = (n: number, c: number) => g(n, c) / g(n - 1, c);
  // 2.5 is clamped to exactly 1.5 on the way in, 0.4 to exactly 0.67 on the way out; unclamped they would be 2.5 and 0.4.
  near(step(12, 1), 1.5, 1e-9, 'into the dark frame, channel 1');
  near(step(13, 1), 0.67, 1e-9, 'out of it');
  near(step(20, 2), 1.5, 1e-9, 'into the dark frame, channel 2');
  near(step(21, 2), 0.67, 1e-9, 'out of it');
  // The other channels have no jump: their steps follow the exposure, which is steady here.
  for (const [n, c] of [[12, 0], [12, 2], [13, 0], [13, 2], [20, 0], [20, 1], [21, 0], [21, 1]]) near(step(n, c), 1, 0.03, `keyframe ${n} channel ${c}`);
  // 1.5 x 0.67 is 1.005: the clamps are nearly each other's inverse, so the chain is back at its level two keyframes on.
  near(g(13, 1) / g(11, 1), 1.005, 0.02, 'channel 1 at keyframe 13 against 11');
  near(g(21, 2) / g(19, 2), 1.005, 0.02, 'channel 2 at keyframe 21 against 19');
  for (let n = 1; n < kfs.length; n++) for (let c = 0; c < 3; c++) {
    assert.ok(step(n, c) >= 0.67 - 1e-9 && step(n, c) <= 1.5 + 1e-9, `keyframe ${n} channel ${c}: step ${step(n, c)}`);
  }
});

test('correctionYawAt is the yaw of C at each keyframe, linear between them, held outside', () => {
  const k = ringKfs;
  const yawOf = (i: number) => wrap180(headingDeg(k[i].pose) - headingDeg(k[i].pred));
  for (const i of [0, 1, 7, 8, 9, 30, 60, k.length - 1]) near(ring.tracker.correctionYawAt(k[i].t), yawOf(i), 1e-9, `at keyframe ${i}`);
  near(ring.tracker.correctionYawAt((k[40].t + k[41].t) / 2), (yawOf(40) + yawOf(41)) / 2, 1e-9, 'halfway');
  near(ring.tracker.correctionYawAt(k[0].t - 5000), 0, 1e-12, 'before the first keyframe C is the gauge');
  near(ring.tracker.correctionYawAt(k[k.length - 1].t + 5000), yawOf(k.length - 1), 1e-9, 'held after the newest');
  assert.ok(Math.abs(yawOf(k.length - 1)) > 1.0, `C has turned by ${yawOf(k.length - 1)} degrees: the test is not on the identity`);
});

// ---- 2. Before the lock ----------------------------------------------------------------------------------------------

/** A FocalEstimator that never locks: the whole run stays in the pre-lock regime. */
class NeverLocks extends FocalEstimator { addRatio(): 'collecting' { return 'collecting'; } }
const pre = run({ priorScale: 1.1, pan: { turnDeg: 70 }, makeFocal: p => new NeverLocks({ fNorm: p, source: 'default', sdPct: PRIOR_SD_PCT }) });

test('pre-lock, the chain follows the gyro and not the 10 % wrong focal: the first ten keyframes within 0.5 degrees of truth', () => {
  // With the prior 10 % long the image reads each 4.7-degree step 0.4 degrees short; W_yaw 0.11 (the focal term of Sigma_img)
  // lets the gyro lead, whose 2 % scale error is 0.1 a step the other way. Without the focal term W_yaw is 0.97 and the
  // chain follows the image: 0.4 degrees short a step, 3.6 degrees at the tenth keyframe.
  const kfs = pre.tracker.keyframes;
  assert.ok(kfs.length >= 12, `${kfs.length} keyframes`);
  assert.equal(pre.focal.state, 'prior');
  for (let i = 0; i < 10; i++) {
    const e = yawError(pre, pre.snaps[i].pose, kfs[i]);
    assert.ok(Math.abs(e) < 0.5, `keyframe ${i}: ${e.toFixed(2)} degrees off`);
  }
  for (let i = 1; i < 10; i++) assert.equal(kfs[i].cls, 'aligned', `keyframe ${i}`);
});

// A lens 80 degrees across its short axis against the 43-degree prior (the widest plausible lens is 60): each match disagrees
// with the gyro by 2.0 to 2.2 degrees before the lock, over the narrow gate of 1.96 and well inside the wide one of 4.1.
const wide = run({ pan: { shortFovDeg: 80, turnDeg: 90 }, priorScale: priorFNorm(W, H) / (0.5 / Math.tan(40 * DEG)) });

test('a wide lens locks: the gate before the lock is the wide one, so matches that disagree by 2 degrees are fused', () => {
  const kfs = wide.tracker.keyframes;
  assert.ok(kfs.length >= 15);
  assert.ok(wide.lock, 'the focal locked');
  assert.ok(kfs.slice(1).filter(k => k.cls === 'aligned').length >= kfs.length - 3, 'nearly every keyframe is aligned');
  const lockAt = (wide.lock as { kf: number }).kf;
  const inn = wide.tracker.log.filter(x => x.outcome === 'accepted' && x.kf !== undefined && x.kf > 0 && x.kf < lockAt).map(x => x.innovationDeg as number);
  assert.ok(inn.filter(v => v > 1.96).length >= 3, `innovations ${inn.map(v => v.toFixed(2))} are over the post-lock gate`);
  assert.ok(Math.max(...inn) < 4.1, 'and inside the wide gate');
  // From the third ratio the innovation is computed from the correspondences re-solved at f_best (4.7, 4.8), in the gyro's
  // degrees, and drops from about 2 degrees to about 1.
  const before = wide.snaps.filter(s => s.id >= 2 && s.ratiosBefore < 3).map(s => recordOf(wide, s.id)?.innovationDeg as number);
  const after = wide.snaps.filter(s => s.ratiosBefore >= 3 && s.id < lockAt).map(s => recordOf(wide, s.id)?.innovationDeg as number);
  assert.ok(before.length >= 3 && after.length >= 1, `${before.length} before, ${after.length} after`);
  assert.ok(Math.min(...before) > 1.8 && Math.max(...after) < 1.4, `innovations ${before.map(v => v.toFixed(2))} then ${after.map(v => v.toFixed(2))}`);
});

// The same lock turning the other way: a sign error in a ratio shows only as a lock that never comes (S19).
const backwards = run({ priorScale: 1.1, pan: { turnDeg: -80 } });

test('turning the other way locks too: the ratio is positive whichever way the image and the gyro turn (S19)', () => {
  assert.ok(backwards.lock && backwards.lock.ratios >= 5 && backwards.lock.ratios <= 12, `lock ${JSON.stringify(backwards.lock)}`);
  // Within 5 %: over twelve runs (three seeds, both directions, two pan lengths) the lock of a 10 %-long prior lands 1.6 to 3.8 % high, because LK at a wrong
  // focal reads a yaw 0.932 of the truth where 0.909 is expected (isolated in align: the same 4.7-degree step at f0 = 1.1 f).
  near(backwards.focal.fBest / (backwards.fTrue / (1 + SG)) - 1, 0, 0.05, 'f within 5 %');
  assert.ok(backwards.tracker.keyframes.slice(1).every(k => k.cls === 'aligned'));
  for (const w of omegasOf(backwards).slice(3)) near(w as number, -20.4, 2, 'heading rate, signed');
});

// ---- 3. The gate -----------------------------------------------------------------------------------------------------

test('innovationGateDeg: 1 + 3 sigma_g after the lock (1.93 steady with tau applied, 1.96 with the prior), wide before it', () => {
  const sg = (dOmega: number, tauSd: number) => Math.sqrt(predictorVarianceDeg2('relative', 4, 1 / 4.6, dOmega, tauSd));
  assert.equal(innovationGateDeg({ locked: true, ratios: 0, dPsiGyroDeg: 4, sigmaGDeg: sg(1.99, 2.1) }).toFixed(2), '1.93');
  assert.equal(innovationGateDeg({ locked: true, ratios: 0, dPsiGyroDeg: 4, sigmaGDeg: sg(1.99, 40) }).toFixed(2), '1.96');
  near(innovationGateDeg({ locked: false, ratios: 0, dPsiGyroDeg: 4, sigmaGDeg: sg(1.99, 40) }), 1.96 + 2, 0.01, 'two degrees wider at 4');
  assert.equal(innovationGateDeg({ locked: false, ratios: 3, dPsiGyroDeg: 4, sigmaGDeg: sg(1.99, 40) }).toFixed(2), '1.96');
});

// An image that disagrees with the gyro, for chosen readbacks (the n-th readback is keyframe n in a steady ring), by yaw
// degrees the sensors do not know of; a disagreement shows as an innovation of about 0.85 to 1.1 times that:
//   2: +3, before the lock; 12: +2.8 and 16: +1.4, after it; 20, 21, 22: +3, -3, +3 (a run of mismatches).
const SHOWN: Record<number, number> = { 2: 3, 12: 2.8, 16: 1.4, 20: 3, 21: -3, 22: 3 };
const gate = run({ priorScale: 1.1, pan: { turnDeg: 150 }, imageYaw: n => SHOWN[n] ?? 0 });
const gateAt = (id: number) => recordOf(gate, id);

test('the gate is wide before the lock and 1.93 after: 3 degrees fused before it, 2.8 refused and 1.4 fused after', () => {
  assert.ok(gate.lock && gate.lock.kf > 2 && gate.lock.kf < 12, `the focal locked at keyframe ${gate.lock?.kf}`);
  const early = gateAt(2);
  assert.ok(early?.detail === 'aligned' || early?.detail === 'focal-lock', `3 degrees before the lock: ${early?.detail} at ${early?.innovationDeg}`);
  assert.ok((early?.innovationDeg as number) > 1.96 && (early?.innovationDeg as number) < 4.1, `${early?.innovationDeg} is between the two gates`);
  assert.equal(gateAt(12)?.detail, 'mismatch', `2.8 degrees after the lock: ${gateAt(12)?.detail}`);
  assert.equal(gate.tracker.keyframes[12].cls, 'sensor');
  assert.ok((gateAt(12)?.innovationDeg as number) > 1.96, `${gateAt(12)?.innovationDeg}`);
  assert.equal(gateAt(16)?.detail, 'aligned', `1.4 degrees after the lock: ${gateAt(16)?.detail}`);
  assert.ok((gateAt(16)?.innovationDeg as number) < 1.93, `${gateAt(16)?.innovationDeg}`);
});

test('a refused match is placed by the gyro and leaves no edge; the keyframe after it measures the other half (a run of two)', () => {
  const k = gate.tracker.keyframes;
  // Placed by the gyro: the yaw of C stays where keyframe 11 left it (to the 0.1 of tilt the pull removes), whatever the image
  // showed. Shown 2.8 degrees ahead, a fused keyframe would have moved it by 0.2 to 2.5.
  const cYaw = (i: number) => wrap180(headingDeg(k[i].pose) - headingDeg(k[i].pred));
  near(cYaw(12), cYaw(11), 0.1, 'the refused keyframe keeps the yaw of C');
  assert.equal(gateAt(13)?.detail, 'mismatch', 'the next keyframe measures the other half of the disagreement');
  assert.equal(gateAt(14)?.detail, 'aligned', 'and the one after is clean again');
  assert.equal(gate.tracker.edges.filter(e => e.b === 12 || e.b === 13).length, 0, 'a refused match leaves no edge');
});

test('mismatchRun counts consecutive textured mismatches and any other commit resets it', () => {
  // Readbacks 20, 21, 22 are shown +3, -3, +3, so pairs 20..23 all disagree by 3 to 6 degrees.
  const logged = gate.tracker.log.filter(x => x.outcome === 'accepted' && x.kf !== undefined && x.kf >= 19 && x.kf <= 25);
  assert.deepEqual(logged.slice(1, 5).map(x => x.detail), ['mismatch', 'mismatch', 'mismatch', 'mismatch'], logged.map(x => `${x.kf}${x.detail}`).join(' '));
  assert.deepEqual(gate.snaps.slice(19, 25).map(s => s.mismatchRun), [0, 1, 2, 3, 4, 0], 'the counter after keyframes 19 to 24');
  assert.equal(Math.max(...gate.snaps.map(s => s.mismatchRun)), 4);
  assert.deepEqual(gate.snaps.slice(11, 15).map(s => s.mismatchRun), [0, 1, 2, 0], 'a run of two, then clean');
  assert.equal(gate.tracker.mismatchRun, 0);
});

// ---- 4. A blank sector -----------------------------------------------------------------------------------------------

// 60 degrees of one flat colour, wider than the 41-degree frame: a frame whose centre is 20 degrees inside sees nothing to hold.
const blank = run({ scene: makeSynthScene({ seed: 3, blankSectors: [[60, 120]] }), priorScale: 1.1, pan: { turnDeg: 150 } });

test('a blank sector is placed by the gyro within the drift bound and marked sensor', () => {
  const kfs = blank.tracker.keyframes;
  const az = (k: Keyframe) => headingDeg(blank.truth(k));
  const inside = kfs.filter(k => az(k) > 82 && az(k) < 98);
  assert.ok(inside.length >= 3, `${inside.length} keyframes deep in the sector`);
  for (const k of inside) {
    assert.equal(k.cls, 'sensor', `keyframe ${k.id} at ${az(k).toFixed(0)}`);
    assert.equal(recordOf(blank, k.id)?.detail, 'no-texture', `keyframe ${k.id}`);
  }
  assert.equal(blank.tracker.edges.some(e => inside.some(k => e.b === k.id)), false, 'no edge into the blank sector');
  // Placed by the gyro: across the sector the chain error changes by the gyro's own error, 2 % of the turn plus the drift,
  // plus the half-degree the gravity floor leaves.
  const first = kfs.find(k => k.id > 0 && az(k) > 60 && az(k) < 80 && k.cls === 'aligned') as Keyframe;
  const last = kfs.find(k => k.id > first.id && az(k) > 112 && k.cls === 'aligned') as Keyframe;
  const span = az(last) - az(first), dt = (last.t - first.t) / 1000;
  const err = yawError(blank, last.pose, last) - yawError(blank, first.pose, first);
  const bound = SG * span + 2 / 60 * dt + 0.5;
  assert.ok(Math.abs(err) <= bound, `chain error across the sector ${err}, bound ${bound}`);
  assert.equal(last.cls, 'aligned', 'textured frames align again after it');
  // Their placement sigma runs from the nearest aligned keyframe before them (4.7): above the 0.5 floor, below a few degrees.
  for (const k of inside) assert.ok(k.sigmaDeg > 0.5 && k.sigmaDeg < 3, `keyframe ${k.id}: sigma ${k.sigmaDeg}`);
  assert.ok(inside[inside.length - 1].sigmaDeg > inside[0].sigmaDeg, 'and grows with the distance from the anchor');
  assert.equal(blank.focal.state, 'locked');
});

// ---- 5. Revisits (rule 5) --------------------------------------------------------------------------------------------

// Out 60 degrees too fast (rateMax 10 against 20 deg/s: every keyframe is blurred), back over it slowly enough (rateMax 100).
const reversal = { turnDeg: 100, reverseAtDeg: 60 };
const turnaround = (() => {
  const probe = synthPan({ ...DEFAULT_PAN, scene: makeSynthScene({ seed: 3 }), fps: 30, ...reversal });
  let peak = 0, top = -1;
  for (let i = 0; i < probe.frames.length; i++) {
    const az = headingDeg(probe.frames[i].truth);
    if (az > top) { top = az; peak = i; } else if (az < top - 1) break;
  }
  return peak;
})();
const revisit = run({ pan: reversal, priorScale: 1.1, rateMaxAt: i => (i < turnaround ? 10 : 100) });

test('a revisit upgrades amber to aligned: the blurred out-pass is repainted by aligned keyframes on the way back', () => {
  const kfs = revisit.tracker.keyframes;
  const blurred = kfs.filter(k => k.cls === 'blurred');
  assert.ok(blurred.length >= 10, `${blurred.length} blurred keyframes on the way out`);
  const revisits = revisit.tracker.log.filter(x => x.outcome === 'revisit');
  assert.ok(revisits.length >= 8, `${revisits.length} revisits`);
  assert.ok(revisits.every(x => x.detail === 'aligned' || x.detail === 'focal-lock'), revisits.map(x => x.detail).join(' '));
  // After the out-pass the cells from 5 to 55 degrees are blurred; at the end most are aligned.
  const cells = { none: 0, sensor: 0, blurred: 0, aligned: 0 };
  for (let i = 10; i < 110; i++) {
    const c = revisit.pano.coverage[i];
    if (c === PixClass.Aligned) cells.aligned++; else if (c === PixClass.Blurred) cells.blurred++; else if (c === PixClass.Sensor) cells.sensor++; else cells.none++;
  }
  assert.ok(cells.aligned >= 60, `${JSON.stringify(cells)}`);
  assert.ok(cells.aligned > 3 * cells.blurred, `${JSON.stringify(cells)}`);
  // A revisit is chained to the nearest keyframe, not to the previous one, and its edge says so.
  const edges = revisit.tracker.edges.filter(e => e.kind === 'revisit');
  assert.ok(edges.length >= 8, `${edges.length} revisit edges`);
  assert.ok(edges.some(e => e.a < e.b - 1), 'one aligns against a keyframe other than the previous');
  assert.ok(revisit.tracker.keyframes.length < 60, 'turning back does not commit without bound');
});

// ---- 6. The control and the contract ---------------------------------------------------------------------------------

test('with sensorOnly: true nothing is aligned, nothing is fused, and the focal stays the prior (the v0 path)', () => {
  const so = run({ pan: { turnDeg: 70 }, sensorOnly: true });
  const kfs = so.tracker.keyframes;
  assert.ok(kfs.length >= 12);
  for (const k of kfs) {
    assert.ok(k.cls === 'sensor' || k.cls === 'blurred', `keyframe ${k.id} is ${k.cls}`);
    assert.deepEqual(k.pose, k.pred, 'placed by the prediction');
    assert.equal(k.pyr, null);
    assert.deepEqual(k.gainRGB, [1, 1, 1]);
  }
  assert.equal(so.tracker.edges.length, 0);
  assert.equal(so.tracker.mismatchRun, 0);
  assert.equal(so.focal.state, 'prior');
  assert.equal(so.focal.ratios, 0);
  assert.equal(so.latency.pairs, 0);
  assert.equal(so.tracker.correctionYawAt(kfs[5].t), 0);
  assert.ok(so.tracker.log.every(x => x.psr === undefined && x.innovationDeg === undefined && x.wYaw === undefined));
});

// ---- 7. Latency and the rates behind it ------------------------------------------------------------------------------

// The speed swings 12 to 28 deg/s every 1.6 s for 5 s, so consecutive keyframes (0.2 s apart) differ by up to 7 deg/s, then holds
// 20 deg/s for the last 3 s. A sensor-to-frame offset of 80 ms, not the 50 ms prior, is injected through the frames' capture time.
const speed = (tS: number) => 20 + 8 * Math.sin(2 * Math.PI * tS / 1.6) * Math.min(1, Math.max(0, (5 - tS) / 0.8));
const LAG = 80;
const modulated = (dir: 1 | -1) => (scene: SynthScene) => modulatedPan({
  scene, shortFovDeg: 41.14, pitchDeg: 22.98, durationS: 8, lagMs: LAG, speed: t => dir * speed(t),
});
const calls: { r: number; dOmega: number; tauUsed: number }[] = [];
const spy = (real: LatencyLike): LatencyLike => ({
  addPair(r, dOmega, tauUsed) { calls.push({ r, dOmega, tauUsed }); real.addPair(r, dOmega, tauUsed); },
  get tauMs() { return real.tauMs; }, get sigmaMs() { return real.sigmaMs; }, get pairs() { return real.pairs; }, get applied() { return real.applied; },
});
const gyroRun = run({ panOverride: modulated(1), priorScale: 1.1, makeLatency: spy });
// The same pan the other way round with no gyro rate handed in: the tracker has only the predictor's headings (a chord).
const chordRun = run({ panOverride: modulated(-1), priorScale: 1.1, rate: 'none' });

test('an injected 80 ms of latency is recovered within 10 ms from the aligned pairs, with the prior 30 ms wrong', () => {
  assert.ok(gyroRun.latency.pairs >= 10, `${gyroRun.latency.pairs} pairs`);
  assert.equal(gyroRun.latency.applied, true, 'applied: 10 pairs and a sigma under 15 ms');
  near(gyroRun.latency.tauMs, LAG, 10, 'tau');
  assert.ok(gyroRun.latency.sigmaMs < 15, `sigma ${gyroRun.latency.sigmaMs}`);
  assert.ok(gyroRun.tracker.keyframes.length >= 25);
});

test('the same from the predictor\'s heading chord alone, turning the other way', () => {
  assert.ok(chordRun.latency.pairs >= 10, `${chordRun.latency.pairs} pairs`);
  near(chordRun.latency.tauMs, LAG, 10, 'tau from chord rates');
});

test('addPair is fed a signed change of heading rate, with the tau the pair was predicted at', () => {
  assert.ok(calls.length >= 15, `${calls.length} calls`);
  assert.ok(calls.every(c => Number.isFinite(c.r) && Number.isFinite(c.dOmega) && Number.isFinite(c.tauUsed)));
  // dOmega is a SIGNED difference of two heading rates of 12 to 28 deg/s: both signs occur and it is far smaller than a rate.
  assert.ok(calls.some(c => c.dOmega > 2) && calls.some(c => c.dOmega < -2), 'both signs');
  assert.ok(Math.max(...calls.map(c => Math.abs(c.dOmega))) < 16, 'a difference of two rates, never a rate');
  // Until the estimate applies every frame was predicted at the 50 ms prior; after it, at the estimate.
  for (const c of calls.slice(0, 5)) near(c.tauUsed, 50, 1e-6, 'tauUsed at the prior');
  assert.ok(calls.some(c => Math.abs(c.tauUsed - 50) > 1), 'and later at other values, once tau is applied');
});

test('the signed heading rate at a keyframe is the gyro\'s magnitude with the chord\'s sign, or the chord\'s own rate with no gyro (S10)', () => {
  // On the steady tail (20 deg/s, read 20.4 by the gyro with its 2 % scale error) the rate is good to 0.5 deg/s from the gyro and 1.0 from
  // the chord. The heading stream is quantised to 0.1 degree, so a rate over one 33 ms frame would be good to 3 deg/s: the chord
  // (more than 100 ms) is what keeps it within 1.
  for (const [r, dir, tol] of [[gyroRun, 1, 0.5], [chordRun, -1, 1.0]] as const) {
    const w = omegasOf(r);
    let n = 0;
    r.tracker.keyframes.forEach((k, i) => {
      if (k.t - LAG < 6200) return;
      n++;
      assert.ok(w[i] !== null, `keyframe ${i}: a rate is known`);
      near(w[i] as number, dir * 20.4, tol, `keyframe ${i}, direction ${dir}`);
    });
    assert.ok(n >= 5, `${n} keyframes on the steady tail`);
  }
});

test('a gyro read in the wrong unit (rad/s as deg/s: quiet while turning) is not believed over the predictor\'s own turn (S33)', () => {
  const rad = run({
    panOverride: scene => modulatedPan({ scene, shortFovDeg: 41.14, pitchDeg: 22.98, durationS: 3.5, lagMs: LAG, speed: () => 20 }),
    priorScale: 1.1, rateScale: Math.PI / 180,
  });
  const w = omegasOf(rad).slice(3);
  assert.ok(w.length >= 4, `${w.length} keyframes`);
  for (const v of w) near(v as number, 20.4, 1.0, 'heading rate from the chord');
});

test('the steady-pair test is strict: heading rates 2.00 deg/s apart over the span are not steady, 1.99 apart are (4.8)', () => {
  // The rule is |dOmega| < 2 over every keyframe of the span, and the cases above hold their rates to within 1 of each other, so
  // none of them touches the boundary. The span is set directly: a short ring makes the aligned chain, then the rates of the
  // last four keyframes are put exactly 2 and 1.99 apart (the private table, as omegasOf reads it) and restored.
  const r = run({ priorScale: 1.1, pan: { turnDeg: 60 } });
  const t = r.tracker as unknown as { links: { omega: number | null }[]; steadySpan(a: number, k: number): boolean };
  const k = r.tracker.keyframes.length - 1, a = k - 3, saved = t.links.map(l => l.omega);
  assert.ok(k >= 8 && r.tracker.keyframes.slice(a, k + 1).every(x => x.cls === 'aligned'), `${k + 1} keyframes, the span aligned`);
  try {
    const spread = (hi: number) => { for (let i = a; i <= k; i++) t.links[i].omega = 20; t.links[k].omega = hi; return t.steadySpan(a, k); };
    assert.equal(spread(20), true, 'level rates are a steady span');
    assert.equal(spread(21.99), true, '1.99 apart is steady');
    assert.equal(spread(22), false, '2.00 apart is not below 2');
    assert.equal(spread(25), false, 'nor is 5 apart');
  } finally {
    saved.forEach((w, i) => { t.links[i].omega = w; });
  }
});

// ---- 8. Stripes: a one-period jump (ruling S4) ---------------------------------------------------------------------

/** The default ground and skyline, with a fence of vertical stripes `periodDeg` of azimuth wide in front of the sky: dark and
 *  light bars with soft edges, the repeated structure on which PSR, ZNCC and the inlier test accept a match one period away. */
function stripesScene(periodDeg: number): SynthScene {
  const base = makeSynthScene({ seed: 3 });
  return {
    horizonAlt: az => base.horizonAlt(az),
    sample(az, alt) {
      if (alt > base.horizonAlt(az) + 1.5 && alt < 40) {
        const u = ((az % periodDeg) + periodDeg) % periodDeg / periodDeg, l = 125 + 55 * Math.sin(2 * Math.PI * u);
        return [l, l * 0.96, l * 0.88];
      }
      return base.sample(az, alt);
    },
  };
}
const PERIOD = 6;
const stripes = run({ scene: stripesScene(PERIOD), priorScale: 1.1, pan: { turnDeg: 110 }, imageYaw: n => (n === 14 ? PERIOD : 0) });

test('a one-period jump on stripes is refused by the innovation gate, though PSR, ZNCC and the inlier test accept it', () => {
  const x = recordOf(stripes, 14);
  assert.ok(stripes.lock, 'the ring through a fence locks');
  assert.ok(x);
  assert.equal(x?.detail, 'mismatch', `${x?.detail} with innovation ${x?.innovationDeg}`);
  assert.ok((x?.innovationDeg as number) >= 4, `the jump is a whole period: innovation ${x?.innovationDeg}`);
  assert.ok((x?.psr as number) >= 7 && (x?.zncc as number) >= 0.7, `PSR ${x?.psr} and ZNCC ${x?.zncc} accepted the match`);
  assert.equal(stripes.tracker.keyframes[14].cls, 'sensor');
  assert.equal(stripes.tracker.edges.some(e => e.b === 14), false, 'no edge: the gyro placed it');
  const cYaw = (i: number) => wrap180(headingDeg(stripes.tracker.keyframes[i].pose) - headingDeg(stripes.tracker.keyframes[i].pred));
  near(cYaw(14), cYaw(13), 0.1, 'the refused keyframe keeps the yaw of C, not a period (6 degrees) away');
});

// ---- 9. The keyframe rules with alignment on (S12, S27, S28, S34) ---------------------------------------------------

const poseAt = (az: number, alt = 0): Quat => quatFromBasis(lookBasis(az, alt));

interface Rig { tracker: Tracker; pano: BandPanoramaLike; frameNo: number; reads: number; rgba: Uint8ClampedArray }
function rig(sensorOnly: boolean, rgba = new Uint8ClampedArray(W * H * 4)): Rig {
  const pano = new BandPanorama();
  pano.begin(0);
  const focal = new FocalEstimator({ fNorm: priorFNorm(W, H), source: 'default', sdPct: PRIOR_SD_PCT });
  const tracker = new Tracker({ pano, focal, latency: new PoseTrack().latency, frameW: W, frameH: H, sensorOnly });
  tracker.setActive(true);
  return { tracker, pano, frameNo: 0, reads: 0, rgba };
}
/** One frame as the scanner makes it: onFrame, then hold() on a read and commit() on a commit. A null azimuth is a null prediction. */
function step(r: Rig, t: number, az: number | null, rate: number | null, alt = 0, mode: PredictorMode = 'relative') {
  r.frameNo++;
  const pred: Prediction | null = az === null ? null : { q: poseAt(az, alt), t: t - 50, mode, extrapolatedMs: 0, held: false };
  const s = r.tracker.onFrame(t, pred, rate);
  if (s.read) {
    assert.ok(pred, 'a read needs a prediction');
    r.reads++;
    r.tracker.hold({ frameId: r.frameNo, t, w: W, h: H, rgba: r.rgba, readbackMs: 1 }, pred, rate ?? 0, qrotate(qinv(pred.q), [0, 0, 1]));
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

test('rule 8 closes the ring seam with alignment on as it does in sensor-only mode: 15, 20 and 30 deg/s at pitch 0 and 23', () => {
  // Blank frames: nothing to align, so the gyro places every keyframe and the rules alone decide, in both modes.
  const gaps: string[] = [], counts: string[] = [];
  for (const alt of [0, 23]) for (const rate of [15, 20, 30]) {
    const a = rig(false), s = rig(true);
    for (const r of [a, s]) { step(r, 0, 0, 5, alt); sweep(r, 33, rate / 30, 375, rate, alt); }
    const what = `pitch ${alt} at ${rate} deg/s`;
    if (a.tracker.gapDeg !== null) gaps.push(`${what}: ${a.tracker.gapDeg}`);
    if (a.tracker.keyframes.length !== s.tracker.keyframes.length) counts.push(`${what}: ${a.tracker.keyframes.length} against ${s.tracker.keyframes.length}`);
    assert.ok(a.tracker.keyframes.length > 70, what);
  }
  assert.deepEqual(gaps, [], 'gapDeg after the full turn');
  assert.deepEqual(counts, [], 'the same keyframes as the control');
});

test('a trailing fill meets the previous slice with no alpha-0 column at pitch 0 and 23 (S12, S34), through the real raster', () => {
  // Frame columns carry their own index. The two keyframes are d degrees apart on the sphere, d = S + 2 .. S + 9, with nothing
  // held, as after a stall; rows from altitude 20 down to -5 (the slice's own vertical limit ends a little below). At pitch 23
  // a degree of the fill's tangent angle spans only cos(23) degrees of azimuth, so the fill is widened by 1 / cos (S34).
  const rgba = new Uint8ClampedArray(W * H * 4);
  for (let p = 0; p < W * H; p++) { rgba[p * 4] = p % W; rgba[p * 4 + 3] = 255; }
  const top = Math.ceil((90 - 20) * (PANO_H - 1) / 100), bottom = Math.floor((90 + 5) * (PANO_H - 1) / 100);
  const bad: string[] = [];
  for (const pitch of [0, 23]) for (const d of [6, 8, 10, 12, 13]) for (const dir of [1, -1]) {
    const sin2 = Math.sin(pitch * DEG) ** 2, cos2 = Math.cos(pitch * DEG) ** 2;
    const dAz = Math.acos((Math.cos(d * DEG) - sin2) / cos2) / DEG, from = 100, to = from + dir * dAz;
    const r = rig(false, rgba);
    step(r, 0, from, 5, pitch);
    assert.deepEqual([step(r, 400, to, 5, pitch).read, r.tracker.keyframes.length], [true, 2], `pitch ${pitch}, d ${d}`);
    const lo = Math.min(from, to), hi = Math.max(from, to);
    let holes = 0;
    for (let x = 0; x < PANO_W; x++) {
      const az = (x + 0.5) * 360 / PANO_W;
      if (az <= lo || az >= hi) continue;
      for (let y = top; y <= bottom; y++) if (r.pano.rgba[(y * PANO_W + x) * 4 + 3] !== 255) { holes++; break; }
    }
    if (holes > 0 || r.tracker.gapDeg !== null) bad.push(`pitch ${pitch} d ${d} ${dir > 0 ? 'cw' : 'ccw'}: ${holes} columns, gap ${r.tracker.gapDeg}`);
  }
  assert.deepEqual(bad, []);
});

test('a chain commit aligns against the previous keyframe, not the nearest in the corrected frame (the ring seam)', () => {
  // Keyframe 1 is corrected 10 degrees back (as fusion does when the gyro has run ahead), so the corrected frame sees keyframe 0
  // as the nearest to the frame that commits next while keyframe 1 is the step before, 9 degrees of gyro away.
  const r = rig(false);
  step(r, 0, 0, 5);
  sweep(r, 33, 0.7, 6.5, 20);
  const k1 = r.tracker.keyframes[1];
  assert.ok(k1, 'keyframe 1');
  k1.pose = qmul(worldYaw(-10), k1.pred);
  sweep(r, 500, 7.2, 20, 20);
  const links = (r.tracker as unknown as { links: { ref: number }[] }).links;
  assert.ok(links.length >= 3, `${links.length} keyframes`);
  assert.equal(links[2].ref, 1, 'aligned against keyframe 1, the previous step');
});

test('noteReadFailed logs read-failed, drops the step and keeps an earlier candidate, with alignment on (S28)', () => {
  const r = rig(false);
  step(r, 0, 0, 5);
  assert.deepEqual(step(r, 100, 4.2, 30), { read: true, commit: false, why: 'step' });
  const held = r.frameNo;
  r.frameNo++;
  const pred: Prediction = { q: poseAt(4.7), t: 83, mode: 'relative', extrapolatedMs: 0, held: false };
  assert.equal(r.tracker.onFrame(133, pred, 20).read, true, 'a frame in the window that is 20 % slower asks to read');
  r.tracker.noteReadFailed(133);
  assert.deepEqual({ ...r.tracker.log[r.tracker.log.length - 1] }, { at: 133, frameId: r.frameNo, outcome: 'read-failed' });
  assert.throws(() => r.tracker.hold({ frameId: r.frameNo, t: 133, w: W, h: H, rgba: r.rgba, readbackMs: 1 }, pred, 20, [0, 0, 1]), 'nothing to hold after a failed read');
  step(r, 166, 6.3, 30);
  assert.equal(r.tracker.keyframes.length, 2);
  assert.equal(r.tracker.keyframes[1].frameId, held, 'the earlier candidate committed');
});

// ---- 10. The first keyframe from a relative sample (S32) -----------------------------------------------------------

test('a first keyframe taken from an absolute-only sample is retaken from a relative one that follows within 1 s of Begin (S32)', () => {
  const r = rig(false);
  assert.deepEqual(step(r, 0, 10, 5, 0, 'absolute-only'), { read: true, commit: true, why: 'first' });
  assert.equal(r.tracker.keyframes[0].frameId, 1);
  // 400 ms later the relative stream is back, 0.4 degrees away (the compass wandered): the first keyframe is dropped and taken again.
  assert.deepEqual(step(r, 400, 10.4, 5, 0, 'relative'), { read: true, commit: true, why: 'first' });
  assert.equal(r.tracker.keyframes.length, 1);
  assert.equal(r.tracker.keyframes[0].frameId, 2, 'retaken from the relative frame');
  // One 'accepted' record a keyframe: the dropped keyframe's record went with it, and the retaken keyframe 0 is the only 'first'.
  const accepted = (x: Rig) => x.tracker.log.filter(l => l.outcome === 'accepted').map(l => [l.kf, l.frameId, l.detail]);
  assert.deepEqual(accepted(r), [[0, 2, 'first']]);
  near(headingDeg(r.tracker.keyframes[0].pose), 10.4, 1e-9, 'placed at the relative prediction');
  const painted = Array.from(r.pano.coverage).map((c, i) => (c !== 0 ? i : -1)).filter(i => i >= 0);
  assert.ok(painted.length > 0 && painted.every(i => Math.abs((i + 0.5) / 2 - 10.4) < 3), 'the raster holds only the retaken slice');

  // Later than 1 s, or from a first keyframe that was already relative, nothing is retaken; nor in the control.
  const late = rig(false);
  step(late, 0, 10, 5, 0, 'absolute-only');
  step(late, 1200, 10.4, 5, 0, 'relative');
  assert.equal(late.tracker.keyframes[0].frameId, 1);
  assert.deepEqual(accepted(late), [[0, 1, 'first']], 'and its record stays');
  const rel = rig(false);
  step(rel, 0, 10, 5, 0, 'relative');
  step(rel, 400, 10.4, 5, 0, 'relative');
  assert.equal(rel.tracker.keyframes[0].frameId, 1);
  const so = rig(true);
  step(so, 0, 10, 5, 0, 'absolute-only');
  step(so, 400, 10.4, 5, 0, 'relative');
  assert.equal(so.tracker.keyframes[0].frameId, 1);
});

// ---- 11. The replay through --scanner pano ---------------------------------------------------------------------------

async function replayProof(): Promise<void> {
  // A textured synthetic pan, 45 x 80 frames the harness's camera scales to the analysis frame (as T22's replay does), through the
  // real scanner: 100 degrees at 10 deg/s and 15 fps, inside the 15 deg/s limit.
  const pan = synthPan({ ...DEFAULT_PAN, scene: makeSynthScene({ seed: 3 }), w: 45, h: 80, fps: 15, speedDegS: 10, turnDeg: 100, startHoldS: 0.5, endHoldS: 0.5 });
  const dir = mkdtempSync(join(tmpdir(), 'pano-t27-replay-'));
  try {
    writeReplayInput(dir, pan);
    const classes = async (sensorOnly: boolean) => {
      writeFileSync(join(dir, 'input', 'scanner.json'), JSON.stringify({ sensor_only: sensorOnly }), 'utf8');
      await replayCase(dir, { scanner: 'pano' });
      const d = JSON.parse(readFileSync(join(dir, 'result', 'diagnostics.json'), 'utf8')) as { sensor_only: boolean; keyframes: { cls: string }[]; focal: { state: string } };
      return { sensorOnly: d.sensor_only, total: d.keyframes.length, aligned: d.keyframes.filter(k => k.cls === 'aligned').length, focal: d.focal.state };
    };
    const aligned = await classes(false), control = await classes(true);
    test('replay --scanner pano of a textured pan: at least 80 % of the keyframes are aligned and the focal locks', () => {
      assert.equal(aligned.sensorOnly, false);
      assert.ok(aligned.total >= 15, `${aligned.total} keyframes`);
      assert.ok(aligned.aligned / aligned.total >= 0.8, `${aligned.aligned} of ${aligned.total} aligned`);
      assert.equal(aligned.focal, 'locked');
    });
    test('replay with sensor_only: true in scanner.json: 0 aligned keyframes and the focal stays the prior', () => {
      assert.equal(control.sensorOnly, true);
      assert.equal(control.total, aligned.total, 'the same keyframes');
      assert.equal(control.aligned, 0);
      assert.equal(control.focal, 'prior');
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
await replayProof();

console.log(`trackerAlign.test: ${passed}/${passed + failed} passed`);
if (failed > 0) process.exitCode = 1;
export const result = { passed, failed, total: passed + failed };
