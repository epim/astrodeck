// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T17: a synthetic scene and a synthetic pan, for tests (SPEC-v2 3.4 synth block, 7.2, 13.1).
//
// Test helper, never imported by production code, and not a test: the name does not end in `.test.ts`, so
// `run-tests.mjs` does not run it. It is the in-process twin of the simulator's `pans` route and realism streams
// (SIM/sim/sensors.py, T01), small enough to build a ring in a unit test in milliseconds and to render only the
// frames the test looks at.
//
// A scene is a function of direction alone: value-noise ground under a skyline, a smooth sky above it, and optional
// blank sectors. A camera at a point sees the same scene whatever its position, which is the pure-rotation model the
// scanner assumes, so a rotation-only registration of two views has an exact answer to be graded against.
//
// A pan is a ring at one pitch: holds at both ends, a smoothstep speed ramp at the start and end of each leg
// (13.3), and the sensor streams a phone gives while it turns (13.1): a relative and an absolute orientation
// stream on a 60 Hz change-driven pump, `devicemotion` at 60 Hz in the W3C slots (alpha = device x, beta = y,
// gamma = z), and frames whose `captureTime` carries a lag or is absent. Every random source is seeded, so the same
// options give the same bytes.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { encodePng } from '../../../__sim__/png';
import { DEG, lookBasis } from '../../../photosphereGeometry';
import {
  basisFromQuat, deviceOrientationFromQuat, logSO3, qinv, qmul, quatFromBasis, worldYaw,
} from '../../rotation';
import type { Intrinsics, Quat } from '../../types';

// ---- Scene -----------------------------------------------------------------

export interface SynthScene {
  sample(azDeg: number, altDeg: number): [r: number, g: number, b: number];
  /** The altitude of the skyline at an azimuth: the truth a traced horizon is graded against. */
  horizonAlt(azDeg: number): number;
}

const mod = (x: number, m: number) => ((x % m) + m) % m;
const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x));
const fade = (t: number) => t * t * t * (t * (t * 6 - 15) + 10);   // quintic: continuous in value, slope and curvature
const smoothstep = (t: number) => { const u = clamp(t, 0, 1); return u * u * (3 - 2 * u); };

/** A lattice value in [0, 1) from a 32-bit integer hash of (seed, i, j). */
function lattice(seed: number, i: number, j: number): number {
  let h = Math.imul(seed | 0, 0x9e3779b1) ^ Math.imul(i | 0, 0x27d4eb2d) ^ Math.imul(j | 0, 0x165667b1);
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/** Periodic value noise in 1-D: `cells` lattice cells around the circle, x in cell units. In [0, 1). */
function noise1(seed: number, x: number, cells: number): number {
  const i = Math.floor(x), t = fade(x - i);
  const a = lattice(seed, mod(i, cells), 0), b = lattice(seed, mod(i + 1, cells), 0);
  return a + (b - a) * t;
}

/** Value noise in 2-D, periodic in x with `cells` lattice cells; y runs free. In [0, 1). */
function noise2(seed: number, x: number, y: number, cells: number): number {
  const i = Math.floor(x), j = Math.floor(y), tx = fade(x - i), ty = fade(y - j);
  const i0 = mod(i, cells), i1 = mod(i + 1, cells);
  const top = lattice(seed, i0, j) + (lattice(seed, i1, j) - lattice(seed, i0, j)) * tx;
  const bottom = lattice(seed, i0, j + 1) + (lattice(seed, i1, j + 1) - lattice(seed, i0, j + 1)) * tx;
  return top + (bottom - top) * ty;
}

/** The ground texture: four octaves of value noise over (azimuth, altitude). The lattice spacing is
 *  1 / cycles degrees, with a whole number of cells around the circle (`round(360 cycles)`) so the texture is
 *  periodic in azimuth; the same cells-per-degree scales altitude, so a feature is as tall as it is wide.
 *  Octave sizes 12.5, 5, 2 and 0.83 degrees: 55, 22, 9 and 3.6 pixels at the 4.4 pixels per degree of the default
 *  41-degree lens at 180 pixels, and half and a quarter of that at the 90 x 160 and 45 x 80 pyramid levels, so every
 *  level the aligner works at has a scale of texture to hold on to. */
const GROUND_OCTAVES = [
  { cells: Math.round(360 * 0.08), weight: 0.4 },
  { cells: Math.round(360 * 0.2), weight: 0.3 },
  { cells: Math.round(360 * 0.5), weight: 0.2 },
  { cells: Math.round(360 * 1.2), weight: 0.1 },
] as const;
/** The weighted noise lies in [0, 1) and has a standard deviation of 0.12 (measured over 20,000 points), so a gain
 *  of 2 gives a luma factor 1 + 2 (n - 0.5) with a standard deviation of 0.25 that cannot go below 0: roughly 21
 *  grey levels on the mean ground. */
const GROUND_GAIN = 2;
const GROUND_MEAN: readonly [number, number, number] = [96, 84, 62];
const DEFAULT_SKY: readonly [number, number, number] = [150, 185, 225];
/** The sky darkens toward the zenith by this fraction at 70 degrees: smooth, no texture, no yaw information. */
const SKY_FALLOFF = 0.25;
/** A blank sector fades into the scene over this many degrees on each side, so its border is a slope, not an edge. */
const BLANK_FADE_DEG = 4;

/** Default skyline: a 5 degree mean with bumps of 3.2 degrees at 26 degrees, 1.1 at 4 degrees and 0.35 at 1 degree.
 *  It stays between 0.35 and 9.65 degrees (1.2 to 8.3 for seed 3) and is jagged enough to be a treeline at 4.4 pixels
 *  per degree. */
function defaultSkyline(seed: number): (azDeg: number) => number {
  return azDeg => 5
    + 3.2 * (2 * noise1(seed + 1, azDeg / 360 * 14, 14) - 1)
    + 1.1 * (2 * noise1(seed + 2, azDeg / 360 * 90, 90) - 1)
    + 0.35 * (2 * noise1(seed + 3, azDeg / 360 * 360, 360) - 1);
}

/** Degrees from `az` to the clockwise interval [from, to], 0 inside it. The interval may wrap through north. */
function distanceToSector(az: number, from: number, to: number): number {
  const a = mod(az, 360), f = mod(from, 360), t = mod(to, 360);
  const inside = f <= t ? a >= f && a <= t : a >= f || a <= t;
  if (inside) return 0;
  const gap = (x: number, y: number) => { const d = Math.abs(mod(x - y, 360)); return Math.min(d, 360 - d); };
  return Math.min(gap(a, f), gap(a, t));
}

export function makeSynthScene(o: {
  seed: number; skyline?: (azDeg: number) => number; blankSectors?: readonly (readonly [from: number, to: number])[];
  sky?: readonly [number, number, number]; textureContrast?: number /* 1 */;
}): SynthScene {
  const seed = o.seed | 0;
  const skyline = o.skyline ?? defaultSkyline(seed);
  const sky = o.sky ?? DEFAULT_SKY;
  const contrast = o.textureContrast ?? 1;
  const sectors = o.blankSectors ?? [];

  const skyAt = (alt: number): [number, number, number] => {
    const k = 1 - SKY_FALLOFF * clamp(alt / 70, 0, 1);
    return [sky[0] * k, sky[1] * k, sky[2] * k];
  };
  const groundAt = (az: number, alt: number): [number, number, number] => {
    let n = 0;
    for (let i = 0; i < GROUND_OCTAVES.length; i++) {
      const { cells, weight } = GROUND_OCTAVES[i];
      const perDeg = cells / 360;
      n += weight * noise2(seed + 11 * (i + 1), az * perDeg, alt * perDeg, cells);
    }
    // A slow tint, so the three channels do not move together and per-channel gains have something to measure.
    const tint = 2 * noise2(seed + 97, az * 0.05, alt * 0.05, 18) - 1;
    const f = 1 + contrast * GROUND_GAIN * (n - 0.5), d = contrast * 16 * tint;
    return [GROUND_MEAN[0] * f + d, GROUND_MEAN[1] * f, GROUND_MEAN[2] * f - d];
  };

  return {
    horizonAlt: azDeg => skyline(azDeg),
    sample(azDeg, altDeg) {
      const az = mod(azDeg, 360);
      let rgb = altDeg < skyline(az) ? groundAt(az, altDeg) : skyAt(altDeg);
      // A blank sector is one flat colour, ground and sky alike, with no skyline edge and no texture: nothing for a
      // registration to hold on to. Its `horizonAlt` is still the skyline, which exists whether or not it shows.
      let blank = 0;
      for (const [from, to] of sectors) blank = Math.max(blank, 1 - smoothstep(distanceToSector(az, from, to) / BLANK_FADE_DEG));
      if (blank > 0) rgb = [rgb[0] + (sky[0] - rgb[0]) * blank, rgb[1] + (sky[1] - rgb[1]) * blank, rgb[2] + (sky[2] - rgb[2]) * blank];
      return [clamp(rgb[0], 0, 255), clamp(rgb[1], 0, 255), clamp(rgb[2], 0, 255)];
    },
  };
}

/** Sub-pixel sample offsets: 2 x 2 at the quarter points of a pixel [x, x + 1). */
const SUBSAMPLES: readonly (readonly [number, number])[] = [[0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]];

/** RGBA, `k.w` x `k.h`, row-major, alpha 255. `q` is camera to world (x right, y up, looking along -z) and a pixel i
 *  spans [i, i + 1), so the principal point is the centre. A sample point at (u, v) looks along
 *  `forward + (u - cx) / f right - (v - cy) / f up`; the average of the four sub-samples is the pixel. */
export function renderView(scene: SynthScene, q: Quat, k: Intrinsics): Uint8ClampedArray {
  const { right, up, forward } = basisFromQuat(q);
  const out = new Uint8ClampedArray(k.w * k.h * 4);
  for (let y = 0; y < k.h; y++) {
    for (let x = 0; x < k.w; x++) {
      let r = 0, g = 0, b = 0;
      for (const [sx, sy] of SUBSAMPLES) {
        const vx = (x + sx - k.cx) / k.f, vy = -(y + sy - k.cy) / k.f;
        const dx = forward[0] + vx * right[0] + vy * up[0];
        const dy = forward[1] + vx * right[1] + vy * up[1];
        const dz = forward[2] + vx * right[2] + vy * up[2];
        const rgb = scene.sample(Math.atan2(dx, dy) / DEG, Math.atan2(dz, Math.hypot(dx, dy)) / DEG);
        r += rgb[0]; g += rgb[1]; b += rgb[2];
      }
      const i = (y * k.w + x) * 4;
      out[i] = r / 4; out[i + 1] = g / 4; out[i + 2] = b / 4; out[i + 3] = 255;
    }
  }
  return out;
}

// ---- Pan -------------------------------------------------------------------

export interface SynthPanOptions {
  scene: SynthScene; shortFovDeg: number; w: number; h: number; fps: number; speedDegS: number; pitchDeg: number;
  turnDeg: number; startHoldS: number; endHoldS: number; gyroScaleErr: number; driftDegMin: number; seed: number;
  streams: 'both' | 'absolute-only'; gyro: boolean; captureLagMs: number | null; reverseAtDeg?: number;
}
export interface SynthPan {
  frames: { frameId: string; tCaptureMs: number | null; tPresentMs: number; truth: Quat; render(): Uint8ClampedArray }[];   // rendered lazily
  observations: Record<string, unknown>[];   // the 13.1 schema without frame records, sorted by delivery time
  actions: { t_ms: number; action: 'begin' | 'finish' }[];
  k0: Intrinsics;
}

/** 41.14 lens, 180 x 320, 15 fps, 20 deg/s, pitch 22.98, turn 385, holds 1 s, scale 0.02, drift 2 deg/min, seed 1,
 *  both streams, gyro, lag 50. The pitch puts a 70 degree long axis symmetric about the horizon band; 385 is a full
 *  ring plus 25 degrees of overlap (7.5). */
export const DEFAULT_PAN: Omit<SynthPanOptions, 'scene'> = {
  shortFovDeg: 41.14, w: 180, h: 320, fps: 15, speedDegS: 20, pitchDeg: 22.98, turnDeg: 385,
  startHoldS: 1, endHoldS: 1, gyroScaleErr: 0.02, driftDegMin: 2, seed: 1,
  streams: 'both', gyro: true, captureLagMs: 50,
};

/** The speed ramp at each end of a leg, seconds (13.3 `ramp_s`). */
const RAMP_S = 0.5;
/** With `reverseAtDeg` the pan turns that far, goes back this far, then on to `turnDeg`. The reverse route of 7.5 is
 *  300 degrees, back 60, then on 160, which is `turnDeg` 400 and `reverseAtDeg` 300: 300 - 60 + 160 = 400. */
const REVERSE_BACK_DEG = 60;
/** The scan starts at north: azimuth 0 at t = 0. */
const START_AZ_DEG = 0;

/** The browser event pump and the sensor faults, at the 13.2 defaults. */
const PUMP_HZ = 60;
const PUMP_LATENCY_MS = 5;           // t_receive_ms - t_event_ms, all three streams
const QUANT_DEG = 0.1;               // orientation angles are rounded to 0.1 degree (Chromium)
const THRESHOLD_DEG = 0.1;           // a change-driven stream is silent below this change in any angle
const TILT_NOISE_DEG = 0.05;         // white noise on beta and gamma of every reading
const ABS_NOISE_SIGMA_DEG = 3.0;     // the compass: Ornstein-Uhlenbeck, stationary sd and correlation time
const ABS_NOISE_TAU_S = 5.0;
const ABS_SINUSOID_AMP_DEG = 2.0;    // plus a heading sinusoid: amp x sin(heading + phase)
const ABS_SINUSOID_PHASE_DEG = 40.0;
const MOTION_BIAS_DEG_S = 0.01;      // S2: 0.05 sits on the 0.1 rounding boundary and spoils the exact-zero holds
const MOTION_NOISE_DEG_S = 0.03;
const MOTION_ROUND_DEG_S = 0.1;      // Chromium rounds rotationRate to 0.1 deg/s
/** The page is handed a frame this long after its exposure: sim `frame_records` `present_latency_ms`. */
const PRESENT_LATENCY_MS = 60;

/** mulberry32: a seeded generator in [0, 1). */
function rng(seed: number): () => number {
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
/** Each source draws from its own child of the case seed, so changing one stream never moves another's noise. The
 *  numbering follows the simulator's SeedSequence order: 0 relative, 1 absolute, 2 motion. */
const child = (seed: number, index: number) => rng(Math.imul(seed | 0, 0x9e3779b1) ^ Math.imul(index + 1, 0x85ebca6b));

/** `x` rounded to a multiple of `step`, as a short decimal and never -0. */
const roundTo = (x: number, step: number) => Number((Math.round(x / step) * step).toFixed(6)) + 0;

interface Leg { from: number; len: number; peak: number; t0: number; dur: number }
interface Timeline { durationMs: number; azAt(tMs: number): number; poseAt(tMs: number): Quat }

/** Travel inside one leg, degrees from its start, `s` seconds after it began. The leg's speed rises as a smoothstep
 *  over `RAMP_S`, holds at `peak`, and falls the same way, so the speed is continuous and the legs run back to back
 *  with a momentary stop between them. The integral of a smoothstep ramp of `R` s to speed `v` is `v R (u^3 - u^4 / 2)`
 *  at u = s / R, which is `v R / 2` at the top of the ramp. */
function legTravel(leg: Leg, s: number): number {
  const len = Math.abs(leg.len), R = RAMP_S, v = leg.peak;
  const ramp = (u: number) => v * R * (u * u * u - u * u * u * u / 2);
  let d: number;
  if (s <= 0) d = 0;
  else if (s >= leg.dur) d = len;
  else if (s < R) d = ramp(s / R);
  else if (s <= leg.dur - R) d = v * R / 2 + v * (s - R);
  else d = len - ramp((leg.dur - s) / R);
  return Math.sign(leg.len) * d;
}

function makeTimeline(o: SynthPanOptions): Timeline {
  const lens = o.reverseAtDeg === undefined ? [o.turnDeg]
    : [o.reverseAtDeg, -REVERSE_BACK_DEG, o.turnDeg - o.reverseAtDeg + REVERSE_BACK_DEG];
  const legs: Leg[] = [];
  let t = o.startHoldS, from = 0;
  for (const len of lens) {
    if (len === 0) continue;
    // A leg too short to reach the speed ramps up and down in `RAMP_S` each at a lower peak: |len| = peak R.
    const peak = Math.min(o.speedDegS, Math.abs(len) / RAMP_S);
    const dur = Math.abs(len) / peak + RAMP_S;
    legs.push({ from, len, peak, t0: t, dur });
    t += dur; from += len;
  }
  const durationMs = (t + o.endHoldS) * 1000;
  const travelAt = (tMs: number): number => {
    const s = tMs / 1000;
    let travel = 0;
    for (const leg of legs) {
      if (s < leg.t0) break;
      travel = leg.from + legTravel(leg, s - leg.t0);
    }
    return travel;
  };
  const azAt = (tMs: number) => START_AZ_DEG + travelAt(clamp(tMs, 0, durationMs));
  return {
    durationMs, azAt,
    poseAt: tMs => quatFromBasis(lookBasis(mod(azAt(tMs), 360), o.pitchDeg)),
  };
}

type Row = Record<string, unknown>;

/** One orientation stream as a browser's event pump delivers it (SIM `_orientation_stream`). The pump fires every
 *  1000 / PUMP_HZ ms and reads the sensor `age` ms late, `age` uniform over one period, so the reading is the
 *  attitude at `tick - age`. It is delivered when any angle has moved THRESHOLD_DEG from the last DELIVERED reading,
 *  and not otherwise, so a phone holding still goes silent. The change test reads the attitude before the white tilt
 *  noise and before rounding.
 *
 *  Relative: the truth turned about the vertical by `yaw0 + scale travel + drift t`, where `yaw0` is a seeded zero
 *  and `travel` is the signed azimuth travelled since the start. The sensor integrates a gyro that reads
 *  (1 + scale) of the true rate, so the relative heading turns (1 + scale) as fast as the phone does. Absolute: the
 *  truth turned by minus the compass error, a heading sinusoid plus an Ornstein-Uhlenbeck process (a compass reads
 *  the true azimuth minus its error, S3). */
function orientationStream(tl: Timeline, o: SynthPanOptions, absolute: boolean, seed: () => number): Row[] {
  const periodMs = 1000 / PUMP_HZ;
  const nTicks = Math.round(tl.durationMs / periodMs) + 1;
  // Everything random is drawn up front in a fixed order, so the number of ticks a fault is applied on changes
  // nothing else in the stream.
  const yaw0 = seed() * 360;
  const ages = Array.from({ length: nTicks }, () => seed() * periodMs);
  const tiltNoise = Array.from({ length: nTicks }, () => [gauss(seed), gauss(seed)] as const);
  const headingNoise = Array.from({ length: nTicks }, () => gauss(seed));
  const keep = Math.exp(-(periodMs / 1000) / ABS_NOISE_TAU_S);
  const innovation = ABS_NOISE_SIGMA_DEG * Math.sqrt(1 - keep * keep);
  const event = absolute ? 'deviceorientationabsolute' : 'deviceorientation';

  const rows: Row[] = [];
  let last: { alpha: number; beta: number; gamma: number } | null = null;
  let ou = 0;
  for (let k = 0; k < nTicks; k++) {
    const tickMs = k * periodMs;
    const readMs = clamp(tickMs - ages[k], 0, tl.durationMs);
    const az = tl.azAt(readMs);
    let offset: number;   // degrees added to every azimuth
    if (absolute) {
      ou = k === 0 ? ABS_NOISE_SIGMA_DEG * headingNoise[k] : keep * ou + innovation * headingNoise[k];
      offset = -(ABS_SINUSOID_AMP_DEG * Math.sin((-az + ABS_SINUSOID_PHASE_DEG) * DEG) + ou);
    } else {
      offset = yaw0 + o.gyroScaleErr * (az - START_AZ_DEG) + o.driftDegMin / 60 * readMs / 1000;
    }
    const held = deviceOrientationFromQuat(qmul(worldYaw(offset), tl.poseAt(readMs)));
    if (last) {
      const dAlpha = Math.abs(((held.alpha - last.alpha + 540) % 360) - 180);
      if (!(dAlpha >= THRESHOLD_DEG - 1e-9 || Math.abs(held.beta - last.beta) >= THRESHOLD_DEG - 1e-9
        || Math.abs(held.gamma - last.gamma) >= THRESHOLD_DEG - 1e-9)) continue;
    }
    last = held;
    const beta = mod(held.beta + TILT_NOISE_DEG * tiltNoise[k][0] + 180, 360) - 180;
    const gamma = clamp(held.gamma + TILT_NOISE_DEG * tiltNoise[k][1], -90, 90);
    const tEvent = Math.round(tickMs);
    const alpha = roundTo(held.alpha, QUANT_DEG);   // [0, 360], and 360 is 0
    rows.push({
      kind: 'orientation', event, t_event_ms: tEvent, t_receive_ms: tEvent + PUMP_LATENCY_MS,
      alpha: alpha >= 360 ? 0 : alpha, beta: roundTo(beta, QUANT_DEG), gamma: roundTo(gamma, QUANT_DEG),
      absolute,
    });
  }
  return rows;
}

/** `devicemotion` on a fixed grid, never thresholded. The rate is the rotation between the poses half a period
 *  either side of the sample, expressed in the device frame (`logSO3(qinv(q0) q1)`), so alpha, beta and gamma are the
 *  rates about device x, y and z: a portrait phone turning clockwise at omega and pitch p reads
 *  beta = -omega cos p and gamma = +omega sin p, with alpha about 0. The gyro reads (1 + scale) of the truth, plus a
 *  bias and white noise, and is rounded to 0.1 deg/s AFTER the noise. */
function motionStream(tl: Timeline, o: SynthPanOptions, seed: () => number): Row[] {
  const periodMs = 1000 / PUMP_HZ;
  const nSamples = Math.round(tl.durationMs / periodMs);
  const rows: Row[] = [];
  for (let k = 0; k <= nSamples; k++) {
    const tMs = k * periodMs;
    const noise = [gauss(seed), gauss(seed), gauss(seed)];
    const t0 = Math.max(0, tMs - periodMs / 2), t1 = Math.min(tl.durationMs, tMs + periodMs / 2);
    if (t1 <= t0) continue;
    const v = logSO3(qmul(qinv(tl.poseAt(t0)), tl.poseAt(t1)));
    const rate = [0, 1, 2].map(i =>
      roundTo(v[i] / DEG / ((t1 - t0) / 1000) * (1 + o.gyroScaleErr) + MOTION_BIAS_DEG_S + MOTION_NOISE_DEG_S * noise[i],
        MOTION_ROUND_DEG_S));
    const tEvent = Math.round(tMs);
    rows.push({ kind: 'motion', t_event_ms: tEvent, t_receive_ms: tEvent + PUMP_LATENCY_MS, rate: { alpha: rate[0], beta: rate[1], gamma: rate[2] } });
  }
  return rows;
}

export function synthPan(o: SynthPanOptions): SynthPan {
  if (!(o.fps > 0 && o.speedDegS > 0 && o.w > 0 && o.h > 0 && o.shortFovDeg > 0 && o.shortFovDeg < 180))
    throw new RangeError('synthPan: fps, speedDegS, w, h and shortFovDeg must be positive, and shortFovDeg under 180');
  if (o.reverseAtDeg !== undefined && !(o.reverseAtDeg > 0 && o.turnDeg - o.reverseAtDeg + REVERSE_BACK_DEG >= 0))
    throw new RangeError('synthPan: reverseAtDeg must be positive, and turnDeg at least reverseAtDeg minus the 60 degrees back');
  const tl = makeTimeline(o);
  // fNorm = f / shortEdgePx = 0.5 / tan(shortFov / 2); the principal point is the centre (3.2).
  const k0: Intrinsics = {
    w: o.w, h: o.h, f: 0.5 / Math.tan(o.shortFovDeg * DEG / 2) * Math.min(o.w, o.h), cx: o.w / 2, cy: o.h / 2,
  };

  // Frame k is exposed at round(k 1000 / fps) ms, up to the last that does not pass the end of the route. The page
  // is told `captureTime = exposure + lag` (Chromium's delivery semantics), or nothing; the frame is presented at
  // least PRESENT_LATENCY_MS after the exposure and 10 ms after a captureTime that is present.
  const periodMs = 1000 / o.fps;
  const nFrames = Math.floor(tl.durationMs / periodMs + 1e-9) + 1;
  const frames: SynthPan['frames'] = [];
  for (let k = 0; k < nFrames; k++) {
    const exposure = Math.round(k * periodMs);
    const tCaptureMs = o.captureLagMs === null ? null : exposure + Math.round(o.captureLagMs);
    const tPresentMs = tCaptureMs === null ? exposure + PRESENT_LATENCY_MS : Math.max(exposure + PRESENT_LATENCY_MS, tCaptureMs + 10);
    const truth = tl.poseAt(exposure);
    frames.push({ frameId: `f${String(k).padStart(6, '0')}`, tCaptureMs, tPresentMs, truth, render: () => renderView(o.scene, truth, k0) });
  }

  // Generation order is relative, absolute, motion; the stable sort by delivery time keeps it for equal times.
  const readings: Row[] = [];
  if (o.streams === 'both') readings.push(...orientationStream(tl, o, false, child(o.seed, 0)));
  for (const row of orientationStream(tl, o, true, child(o.seed, 1))) {
    readings.push(row);
    // Chromium without a relative sensor: the same reading again as a `deviceorientation` that says it is absolute.
    if (o.streams === 'absolute-only') readings.push({ ...row, event: 'deviceorientation' });
  }
  if (o.gyro) readings.push(...motionStream(tl, o, child(o.seed, 2)));
  const observations = readings
    .map((row, index) => ({ row, index }))
    .sort((a, b) => (a.row.t_receive_ms as number) - (b.row.t_receive_ms as number) || a.index - b.index)
    .map(entry => entry.row);

  const lastPresent = frames[frames.length - 1].tPresentMs;
  return {
    frames, observations, k0,
    actions: [{ t_ms: 0, action: 'begin' }, { t_ms: lastPresent + 1000, action: 'finish' }],
  };
}

/** Write `input/frames/<id>.png`, `input/observations.jsonl` and `input/actions.jsonl` under `caseDir`, in the shape
 *  `replay.ts` reads (13.1). Frames come before readings in the file and the whole is stable-sorted by delivery time
 *  (a frame's `t_present_ms`, a reading's `t_receive_ms`), as the simulator writes it; the replay then puts a
 *  reading before a frame at the same millisecond whatever the file says. */
export function writeReplayInput(caseDir: string, pan: SynthPan): void {
  const input = join(caseDir, 'input'), framesDir = join(input, 'frames');
  mkdirSync(framesDir, { recursive: true });
  const frameRows: Row[] = [];
  for (const frame of pan.frames) {
    writeFileSync(join(framesDir, `${frame.frameId}.png`), encodePng(frame.render(), pan.k0.w, pan.k0.h));
    frameRows.push({
      kind: 'frame', frame_id: frame.frameId, t_capture_ms: frame.tCaptureMs, t_present_ms: frame.tPresentMs,
      width: pan.k0.w, height: pan.k0.h, file: `frames/${frame.frameId}.png`,
    });
  }
  const deliveredAt = (row: Row) => (row.kind === 'frame' ? row.t_present_ms : row.t_receive_ms) as number;
  const rows = [...frameRows, ...pan.observations]
    .map((row, index) => ({ row, index }))
    .sort((a, b) => deliveredAt(a.row) - deliveredAt(b.row) || a.index - b.index)
    .map(entry => entry.row);
  const jsonl = (list: unknown[]) => list.map(row => JSON.stringify(row)).join('\n') + (list.length ? '\n' : '');
  writeFileSync(join(input, 'observations.jsonl'), jsonl(rows), 'utf8');
  writeFileSync(join(input, 'actions.jsonl'), jsonl(pan.actions), 'utf8');
}
