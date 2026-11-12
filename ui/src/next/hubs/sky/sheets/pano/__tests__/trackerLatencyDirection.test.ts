// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// WP 11.2 (issue 1056): the latency estimate must not depend on which way the phone pans.
//
// A synthetic pan with a known camera-to-gyro latency (each frame is stamped LAG ms after the instant its pixels show) runs
// through the real PoseTrack, Tracker, FocalEstimator and LatencyEstimator as the scanner runs them, and the estimate the
// tracker ends with is graded against the truth. The pan has what the simulator's chartyard-pan1-reverse-41 route has: a speed
// that swings, which gives consecutive keyframes a change of heading rate to fit; a camera whose stabiliser sits on its crop
// margin with the sign of the turn (`eisOffsets`); and, in the second route, a turn that goes one way, stops, comes back past
// where it began and goes on. On the unchanged tracker the two pairs that span a stop carry the stabiliser's jump of twice the
// margin, which a fit on the change of heading rate reads as 100 ms or more of latency (issue 1056). Each route runs
// clockwise-first and anticlockwise-first, at a slow and a fast pan, at two true latencies.
//
// What a wrong latency costs: scanner.ts predicts every frame's pose at `t - tauMs`, so each frame's gyro placement, the keyframes
// the chain fuses and the live overlay are all out by the pan rate times the error: 68 ms at 20 deg/s is 1.4 degrees, six pixels
// of this lens. The 10 ms tolerance is 0.2 degrees at that rate, under one pixel.
//
// disk-reads: ui/src/next/hubs/sky/sheets/pano/
import assert from 'node:assert/strict';
import { DEG, lookBasis } from '../../photosphereGeometry';
import { BandPanorama } from '../bandPanorama';
import { FocalEstimator, PRIOR_SD_PCT } from '../focal';
import { PoseTrack } from '../poseTrack';
import { deviceOrientationFromQuat, logSO3, qinv, qmul, qrotate, quatFromBasis, worldYaw } from '../rotation';
import { Tracker } from '../tracker';
import type { AnalysisFrame, AxisMapping, Quat } from '../types';
import { H, SCENE, SG, W, rng, viewOf } from './synth/closureRig';

let passed = 0, failed = 0;
function test(name: string, fn: () => void) {
  try { fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; console.log(`FAIL ${name}\n  ${(e as Error).message.split(/\r?\n/)[0]}`); }
}

const CONFIRMED_AXIS: AxisMapping = { perm: [0, 1, 2], sign: [1, 1, 1], unit: 'deg', fit: 1, confirmed: true, samples: 100 };
const FPS = 30, PITCH_DEG = 22.98, SHORT_FOV_DEG = 41.14, RATE_MAX = 40;
const gauss = (r: () => number) => Math.sqrt(-2 * Math.log(1 - r())) * Math.cos(2 * Math.PI * r());

/** The signed speed, deg/s (positive turns clockwise), of a route of signed legs (degrees) at `peak` deg/s. The phone is already
 *  turning when the scan starts and still turning when it ends, so the first leg has no ramp in and the last none out (a
 *  stabiliser that is still settling would lag the first keyframes by the whole of its delay, which is not what is under
 *  test); each turn between legs stops and starts again over 0.5 s ramps as the simulator's pan route does. The speed swings by
 *  35 % every 1.6 s as a hand's does, so that consecutive keyframes differ in heading rate. */
function routeSpeed(legs: readonly number[], peak: number): (tS: number) => number {
  const RAMP_S = 0.5, smooth = (u: number) => { const c = Math.min(1, Math.max(0, u)); return c * c * (3 - 2 * c); };
  let t0 = 0;
  const spans = legs.map((len, i) => {
    const dur = Math.abs(len) / peak + (i > 0 ? RAMP_S / 2 : 0) + (i < legs.length - 1 ? RAMP_S / 2 : 0);
    const span = { sign: Math.sign(len), t0, dur, first: i === 0, last: i === legs.length - 1 };
    t0 += dur;
    return span;
  });
  return tS => (1 + 0.35 * Math.sin(2 * Math.PI * tS / 1.6)) * spans.reduce((v, s) =>
    v + s.sign * peak * (s.first ? 1 : smooth((tS - s.t0) / RAMP_S)) * (s.last ? 1 : smooth((s.t0 + s.dur - tS) / RAMP_S)), 0);
}
/** The seconds a route of `legs` takes at `peak`, rounded up. */
const routeSeconds = (legs: readonly number[], peak: number) => legs.reduce((s, len) => s + Math.abs(len) / peak + 0.5, 0);

/** The stabiliser of a phone camera, yaw only (the simulator's `eis_offsets`): the view shown is the azimuth through a critically
 *  damped low-pass with delay `EIS_DELAY_S`, and its offset from the truth stops at `EIS_MARGIN_DEG`, the crop's margin. At a
 *  cruising pan the offset sits on the margin with the sign of the turn, and it changes sign when the turn does. Returns the
 *  offset of the shown azimuth from the true one, degrees, at each millisecond. */
const EIS_DELAY_S = 0.2, EIS_MARGIN_DEG = 1.8;
function eisOffsets(az: Float64Array): Float64Array {
  const w = 2 / EIS_DELAY_S, out = new Float64Array(az.length), rate0 = (az[1] - az[0]) / 0.001;
  // Settled on the first rate: lagging by the margin (or by the steady lag, if that is smaller) and moving with the view.
  let x = az[0] - Math.sign(rate0) * Math.min(EIS_MARGIN_DEG, Math.abs(rate0) * EIS_DELAY_S), v = rate0;
  out[0] = x - az[0];
  for (let ms = 1; ms < az.length; ms++) {
    const h = 0.001, rate = (az[ms] - az[ms - 1]) / h;
    v += (w * w * (0.5 * (az[ms] + az[ms - 1]) - x) - 2 * w * v) * h;
    x += v * h;
    const offset = x - az[ms];
    if (Math.abs(offset) > EIS_MARGIN_DEG) {
      const side = Math.sign(offset);
      x = az[ms] + side * EIS_MARGIN_DEG;
      if ((v - rate) * side > 0) v = rate;
    }
    out[ms] = x - az[ms];
  }
  return out;
}

interface Pan { frames: { t: number; truth: Quat; eisDeg: number }[]; rows: Record<string, unknown>[]; k0: { w: number; h: number; f: number; cx: number; cy: number } }

/** The sensor streams and frames of one pan: azimuth is the integral of `speed` (signed: negative turns anticlockwise).
 *  Readings carry no pump age, so `lagMs` is exactly the offset to be recovered. */
function pan(speed: (tS: number) => number, lagMs: number, durationS: number): Pan {
  const k0 = { w: W, h: H, f: 0.5 / Math.tan(SHORT_FOV_DEG * DEG / 2) * W, cx: W / 2, cy: H / 2 };
  const total = Math.ceil(durationS * 1000), az = new Float64Array(total + 1);
  for (let ms = 1; ms <= total; ms++) az[ms] = az[ms - 1] + speed(ms / 1000) / 1000;
  const eis = eisOffsets(az);
  const azAt = (ms: number) => { const c = Math.min(total, Math.max(0, ms)), i = Math.floor(c); return az[i] + (az[Math.min(total, i + 1)] - az[i]) * (c - i); };
  const poseAt = (ms: number): Quat => quatFromBasis(lookBasis(((azAt(ms) % 360) + 360) % 360, PITCH_DEG));
  const r = rng(5), yaw0 = r() * 360, round = (x: number) => Math.round(x * 10) / 10;
  const rows: Record<string, unknown>[] = [];
  let last: { alpha: number; beta: number; gamma: number } | null = null;
  for (let tick = 0; tick <= total; tick += 1000 / 60) {
    const held = deviceOrientationFromQuat(qmul(worldYaw(yaw0 + SG * azAt(tick) + 2 / 60 * tick / 1000), poseAt(tick)));
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
  const frames: Pan['frames'] = [];
  for (let k = 0; Math.round(k * 1000 / FPS) <= total; k++) {
    const exposure = Math.round(k * 1000 / FPS);
    frames.push({ t: exposure + Math.round(lagMs), truth: poseAt(exposure), eisDeg: eis[exposure] });
  }
  return { frames, rows: rows.sort((a, b) => (a.t_receive_ms as number) - (b.t_receive_ms as number)), k0 };
}

/** The tracker's latency estimate at the end of one pan, and how many pairs it was fitted from. */
function estimate(speed: (tS: number) => number, lagMs: number, durationS: number): { tauMs: number; applied: boolean; pairs: number; sigmaMs: number } {
  const p = pan(speed, lagMs, durationS);
  const prior = p.k0.f / Math.min(p.k0.w, p.k0.h) * 1.1;
  const focal = new FocalEstimator({ fNorm: prior, source: 'default', sdPct: PRIOR_SD_PCT });
  const track = new PoseTrack();
  (track as unknown as { axisMap: AxisMapping | null }).axisMap = CONFIRMED_AXIS;
  const pano = new BandPanorama();
  pano.begin(0);
  const tracker = new Tracker({ pano, focal, latency: track.latency, frameW: W, frameH: H });
  type Item = { t: number; frame?: number; obs?: Record<string, unknown> };
  const items: Item[] = p.rows.map(obs => ({ t: obs.t_receive_ms as number, obs }));
  p.frames.forEach((f, frame) => items.push({ t: f.t + 10, frame }));
  items.sort((a, b) => a.t - b.t || (a.obs ? -1 : 1));
  let begun = false;
  for (const item of items) {
    if (item.obs) {
      const ob = item.obs;
      if (ob.kind === 'orientation') {
        track.onOrientation({ t: ob.t_event_ms as number, type: 'deviceorientation', alpha: ob.alpha as number, beta: ob.beta as number, gamma: ob.gamma as number, absolute: false, compassHeading: null });
      } else track.onMotion({ t: ob.t_event_ms as number, rate: ob.rate as { alpha: number; beta: number; gamma: number } });
      continue;
    }
    const index = item.frame as number, fr = p.frames[index], t = fr.t;
    if (!begun && track.canPredict) { track.latch(t); begun = true; }
    tracker.setRateMax(RATE_MAX);
    const pred = track.predictAt(t - track.latency.tauMs);
    const at = track.rateAt(t);
    tracker.setActive(begun);
    const step = tracker.onFrame(t, pred, at);
    if (step.read && pred) {
      const rgba = viewOf(SCENE, qmul(worldYaw(fr.eisDeg), fr.truth), p.k0), noise = rng(1000 + index);
      for (let i = 0; i < rgba.length; i += 4) for (let c = 0; c < 3; c++) rgba[i + c] += 2 * ((noise() + noise() + noise() + noise() - 2) * Math.sqrt(3));
      const f: AnalysisFrame = { frameId: index + 1, t, w: W, h: H, rgba, readbackMs: 1 };
      tracker.hold(f, pred, at ?? Infinity, qrotate(qinv(pred.q), [0, 0, 1]));
    }
    if (step.commit) tracker.commit();
    tracker.pump(30);
  }
  const l = track.latency;
  return { tauMs: l.tauMs, applied: l.applied, pairs: l.pairs, sigmaMs: l.sigmaMs };
}

// The tolerance: issue 1056's proof is "within 10 ms of 50", and the forward pan is graded to 10 ms in trackerAlign.test.ts.
const TOLERANCE_MS = 10;

// Legs in degrees, clockwise first. The simulator's route is 300, back 60, on 160; these are shorter, and scaled to the pan rate
// so that each pan has about as many keyframes. The route's turns are the cause under test.
const ROUTES = [
  { name: 'one way', legs: [120] },
  { name: 'there, back, on', legs: [120, -60, 100] },
];
for (const lag of [50, 80]) {
  for (const peak of [16, 24]) {
    for (const route of ROUTES) {
      for (const dir of [1, -1]) {
        const legs = route.legs.map(l => dir * l * peak / 24);
        test(`latency ${lag} ms, ${route.name}, ${dir > 0 ? 'clockwise' : 'anticlockwise'} first at ${peak} deg/s: the estimate is within ${TOLERANCE_MS} ms`, () => {
          const e = estimate(routeSpeed(legs, peak), lag, routeSeconds(legs, peak));
          console.log(`  tau ${e.tauMs.toFixed(1)} ms, sigma ${e.sigmaMs.toFixed(1)}, ${e.pairs} pairs, applied ${e.applied}`);
          assert.ok(e.applied, `the estimate was never applied (${e.pairs} pairs, sigma ${e.sigmaMs})`);
          assert.ok(Math.abs(e.tauMs - lag) <= TOLERANCE_MS, `tau ${e.tauMs} is not within ${TOLERANCE_MS} of ${lag}`);
        });
      }
    }
  }
}

console.log(`${passed} passed, ${failed} failed`);
if (failed) process.exitCode = 1;
