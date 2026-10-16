// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T28: the ring rig of the loop-closure tests (SPEC-v2 4.8, 4.9, 7.2's loop and closure row).
//
// Test helper, never imported by production code, and not a test: the name does not end in `.test.ts`, so `run-tests.mjs` does
// not run it. It runs the tracker as the scanner runs it (3.5): a synthetic pan (synth.ts, T17) gives the frames and the
// sensor streams, the real PoseTrack turns the streams into predictions and rates, the real FocalEstimator, LatencyEstimator
// and BandPanorama sit behind the tracker, and `onFrame`, `hold`, `commit` and `pump` are called in the order of the frame
// callback. Frames carry the simulator's sensor noise (luma sd 2 per channel, S37). It is the rig of trackerAlign.test.ts (T27)
// reduced to what a ring needs, with the closure's own observations added: the pose a keyframe was first painted at (before
// any correction), the focal's state at each `closeLoop` call, and the raster's state at the commit that closed the loop.
//
// Frames are read from a scene baked on a 0.2-degree grid with one bilinear sample per pixel, 2.4 ms a frame against 44 for
// synth.ts's `renderView` (differing by 0.1 grey level on average, 90 at a skyline edge): a ring reads some 300 frames.
//
// The rate the tracker is handed is PoseTrack's `rateAt`, the mapped gyro's magnitude only once its axis mapping has
// confirmed (S41). A steady pan at one pitch never confirms the fit (trackerAlign.test.ts explains), so the rig starts the
// pose track with a confirmed identity mapping, as a phone has after a pan that moves more than one body axis.
import { DEG, lookBasis } from '../../../photosphereGeometry';
import { BandPanorama } from '../../bandPanorama';
import { FocalEstimator, PRIOR_SD_PCT } from '../../focal';
import { PoseTrack } from '../../poseTrack';
import { basisFromQuat, headingDeg, qinv, qrotate, quatFromBasis } from '../../rotation';
import { Tracker } from '../../tracker';
import type { AnalysisFrame, AxisMapping, FocalLike, Intrinsics, Keyframe, Quat } from '../../types';
import { DEFAULT_PAN, makeSynthScene, synthPan, type SynthPan, type SynthPanOptions, type SynthScene } from './synth';

export const W = 180, H = 320;
/** synth DEFAULT_PAN's gyro scale error. */
export const SG = 0.02;
export const wrap180 = (d: number) => ((((d + 180) % 360) + 360) % 360) - 180;
/** The default scene of synth.ts. */
export const SCENE = makeSynthScene({ seed: 3 });

/** mulberry32: a seeded generator, so every random case is the same case every run. */
export function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6D2B79F5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
/** Four uniforms summed: sd 1 (Irwin-Hall), about a tenth of the cost of a Gaussian and enough for a noise floor. */
const noise1 = (r: () => number) => (r() + r() + r() + r() - 2) * Math.sqrt(3);

// ---- A faster renderView ---------------------------------------------------------------------------------------------

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
export function viewOf(scene: SynthScene, q: Quat, k: Intrinsics): Uint8ClampedArray {
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

// ---- Scenes ----------------------------------------------------------------------------------------------------------

/** synth.ts's scene with a striped fence in the sector within `halfWidthDeg` of azimuth 0, where the ring starts and ends:
 *  vertical stripes of `periodDeg` (duty 0.5, each edge a 0.2-degree ramp, the 13.4 stripe colours) at every altitude, so the
 *  fence fills the whole view there and the early keyframes see nothing else. A view that is all one repeated pattern matches
 *  itself one period off as well as it matches the truth (S36). */
export function fenceAtStart(periodDeg: number, halfWidthDeg: number): SynthScene {
  const base = makeSynthScene({ seed: 3 });
  const c1 = [150, 140, 120], c2 = [60, 52, 40], ramp = 0.2 / periodDeg;
  const clamp01 = (x: number) => Math.max(0, Math.min(1, x));
  return {
    horizonAlt: base.horizonAlt,
    sample(az, alt) {
      const a = ((az % 360) + 360) % 360;
      if (Math.min(a, 360 - a) > halfWidthDeg) return base.sample(az, alt);
      const u = (((az % periodDeg) + periodDeg) % periodDeg) / periodDeg;
      const t = u < 0.5 ? clamp01(Math.min(u, 0.5 - u) / ramp + 0.5) : 1 - clamp01(Math.min(u - 0.5, 1 - u) / ramp + 0.5);
      return [c2[0] + (c1[0] - c2[0]) * t, c2[1] + (c1[1] - c2[1]) * t, c2[2] + (c1[2] - c2[2]) * t];
    },
  };
}

// ---- Focal doubles ---------------------------------------------------------------------------------------------------

/** A FocalEstimator that records every `closeLoop` call: the state it was in, the arguments, what it returned or threw. */
export class SpyFocal extends FocalEstimator {
  readonly calls: { state: string; args: [number, number, number]; result?: { fNorm: number; gyroScale: number }; threw?: string }[] = [];
  override closeLoop(thetaImgDeg: number, thetaGyroDeg: number, trueDeg: number): { fNorm: number; gyroScale: number } {
    const call: SpyFocal['calls'][number] = { state: this.state, args: [thetaImgDeg, thetaGyroDeg, trueDeg] };
    this.calls.push(call);
    try { return (call.result = super.closeLoop(thetaImgDeg, thetaGyroDeg, trueDeg)); }
    catch (e) { call.threw = (e as Error).name; throw e; }
  }
}
/** A locked focal, as if the lock had fired on exact ratios: the prior itself is the locked value. A ring run on it has every
 *  step image-led from the start, in the degrees of the prior. */
export function lockedAt<T extends FocalEstimator>(f: T): T {
  for (let i = 0; i < 5; i++) f.addRatio(10, 10);
  return f;
}
/** A focal whose closure leaves the focal alone (and reports nothing): it isolates what the tracker does with the residual. */
export class NoRefocal extends SpyFocal {
  override closeLoop(thetaImgDeg: number, thetaGyroDeg: number, trueDeg: number): { fNorm: number; gyroScale: number } {
    this.calls.push({ state: this.state, args: [thetaImgDeg, thetaGyroDeg, trueDeg], result: { fNorm: this.fBest, gyroScale: 0 } });
    return { fNorm: this.fBest, gyroScale: 0 };
  }
}
/** A focal that never locks and throws if anything closes it. */
export class NeverLocks extends SpyFocal { override addRatio(): 'collecting' { return 'collecting'; } }

// ---- The scanner's frame path over a pan -----------------------------------------------------------------------------

/** Frame noise, sd per channel: the simulator's `noise_sigma` (S37). */
const FRAME_NOISE = 2;
/** Rate limit at 30 fps (rateMaxFromInterval of a 33 ms median interval). */
const RATE_MAX = 40;
/** The mapping a phone's pose track has confirmed a few seconds into a pan. */
const CONFIRMED_AXIS: AxisMapping = { perm: [0, 1, 2], sign: [1, 1, 1], unit: 'deg', fit: 1, confirmed: true, samples: 100 };

export interface RunOptions {
  pan?: Partial<SynthPanOptions>;
  scene?: SynthScene;
  /** The focal prior as a multiple of the true fNorm. */
  priorScale?: number;
  /** The focal the tracker gets, given the prior's fNorm (default: a FocalEstimator with the prior's 20 % sd). */
  makeFocal?: (prior: number) => FocalLike;
  /** A per-channel exposure on the n-th readback, applied before the sensor noise. */
  frameGain?: (readNo: number) => readonly [number, number, number];
  /** Frames the camera never delivers (a stall: the sensors keep running and the tracker sees the axis jump). */
  skip?: (frameIndex: number, truth: Quat) => boolean;
}
export interface Run {
  pan: SynthPan; tracker: Tracker; focal: FocalLike; pano: BandPanorama; fTrue: number;
  reads: number; lock: { kf: number; ratios: number } | null; clears: number;
  /** The pose each keyframe was first painted at: its pose when committed, before any later correction. */
  firstPose: Quat[];
  /** At the commit that closed the loop: the keyframe it was, the slices pending before the pump, the clears so far. */
  closedAt: { kf: number; pending: number; clears: number; frameIndex: number } | null;
  truth(kf: Keyframe): Quat;
}

export function run(o: RunOptions = {}): Run {
  const scene = o.scene ?? SCENE;
  const pan = synthPan({ ...DEFAULT_PAN, scene, fps: 30, ...o.pan });
  const fTrue = pan.k0.f / Math.min(pan.k0.w, pan.k0.h);   // fNorm = f / shortEdgePx (3.2)
  const prior = fTrue * (o.priorScale ?? 1);
  const focal: FocalLike = o.makeFocal ? o.makeFocal(prior) : new FocalEstimator({ fNorm: prior, source: 'default', sdPct: PRIOR_SD_PCT });
  const track = new PoseTrack();
  (track as unknown as { axisMap: AxisMapping | null }).axisMap = CONFIRMED_AXIS;
  const latency = track.latency;
  const pano = new BandPanorama();
  pano.begin(0);
  let clears = 0;
  const clear = pano.clear.bind(pano);
  pano.clear = () => { clears++; clear(); };
  const firstPose: Quat[] = [];
  const paint = pano.paint.bind(pano);
  pano.paint = (s, nowMs) => { if (firstPose[s.id] === undefined) firstPose[s.id] = s.pose; return paint(s, nowMs); };
  const tracker = new Tracker({ pano, focal, latency, frameW: W, frameH: H });

  type Item = { t: number; frame?: number; obs?: Record<string, unknown> };
  const items: Item[] = [];
  for (const obs of pan.observations) items.push({ t: obs.t_receive_ms as number, obs });
  pan.frames.forEach((f, frame) => items.push({ t: f.tPresentMs, frame }));
  items.sort((a, b) => a.t - b.t || (a.obs ? -1 : 1));   // a reading before a frame at the same millisecond (replay.ts)

  let begun = false, reads = 0, lock: Run['lock'] = null, closedAt: Run['closedAt'] = null;
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
    if (o.skip && o.skip(index, fr.truth)) continue;
    const t = fr.tCaptureMs ?? fr.tPresentMs;
    if (!begun && track.canPredict) { track.latch(t); begun = true; }
    tracker.setRateMax(RATE_MAX);
    const pred = track.predictAt(t - latency.tauMs);
    const at = track.rateAt(t), measured = at === null ? null : at;
    tracker.setActive(begun);
    const step = tracker.onFrame(t, pred, measured);
    if (step.read && pred) {
      const rgba = viewOf(scene, fr.truth, pan.k0);
      const gain = o.frameGain ? o.frameGain(reads) : [1, 1, 1];
      const noise = rng(1000 + index);
      for (let i = 0; i < rgba.length; i += 4) for (let c = 0; c < 3; c++) rgba[i + c] = rgba[i + c] * gain[c] + FRAME_NOISE * noise1(noise);
      reads++;
      const f: AnalysisFrame = { frameId: index + 1, t, w: W, h: H, rgba, readbackMs: 1 };
      tracker.hold(f, pred, measured ?? Infinity, qrotate(qinv(pred.q), [0, 0, 1]));
    }
    if (step.commit) {
      const before = focal.state, wasClosed = tracker.loop.closed;
      const kf = tracker.commit();
      if (before === 'prior' && focal.state !== 'prior' && lock === null) lock = { kf: kf.id, ratios: focal.ratios };
      if (!wasClosed && tracker.loop.closed && closedAt === null) closedAt = { kf: kf.id, pending: tracker.pendingSlices, clears, frameIndex: index };
    }
    tracker.pump(30);
  }
  return { pan, tracker, focal, pano, fTrue, reads, lock, clears, firstPose, closedAt, truth: kf => pan.frames[kf.frameId - 1].truth };
}

/** The chain's yaw error at a keyframe: its pose's heading relative to keyframe 0's, minus the truth's turn times `scale`. */
export function yawError(r: Run, pose: Quat, kf: Keyframe, scale = 1): number {
  const k0 = r.tracker.keyframes[0];
  const measured = wrap180(headingDeg(pose) - headingDeg(k0.pose));
  const truth = wrap180(headingDeg(r.truth(kf)) - headingDeg(r.truth(k0)));
  return wrap180(measured - truth * scale);
}
/** The largest |yaw error| over the keyframes. */
export function worstYawError(r: Run): number {
  return Math.max(...r.tracker.keyframes.map(k => Math.abs(yawError(r, k.pose, k))));
}
/** The pose a look-at basis gives, for the rotation tests. */
export const poseAt = (az: number, alt = 0): Quat => quatFromBasis(lookBasis(az, alt));
