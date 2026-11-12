// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// WP 11.1 (#1051): the scale of an aligned step. T28 saw the chain's steps read 0.2 to 0.4 % short of the truth and recorded it
// (S45) as a Lucas-Kanade bias "at the true focal length". This file measures that claim, and the measurement refutes it:
//   * at the TRUE focal the aligner reads a step to within 0.03 degrees at every magnitude, direction, pitch and roll tried (the
//     worst, 0.024, is the weakly observed roll component of a step with 3 degrees of roll in it; without roll, 0.008), and the
//     mean ratio over steps of 2 degrees and more is 0.9999, with steps reading long as often as short. The error is absolute
//     (sub-pixel interpolation and sensor noise) and of both signs, not proportional, so there is no step-scale bias to correct;
//   * at a focal that is 2 % short, which is what the closure rig's `level` ring runs LK at (locked at f_true / (1 + s_g)), the
//     reading is 1.014 to 1.019 against the 1.02 a perfect aligner is expected to give. That shortfall of 0.2 to 0.6 % is the
//     geometry of a rotation fitted with the wrong pinhole: a yaw moves a pixel by f (1 + X^2) per radian, so with f off by a
//     factor F the best rotation over the frame is F (1 + X^2) / (1 + F^2 X^2) of the step, under F and growing with F and
//     with the pitch (the frame's X spans more of the step). `geometricRatio` computes it from exact pixel correspondences
//     with Horn's rotation fit and no image at all; LK agrees with it to 0.25 %.
// So the bound the consumers carry is: LK adds at most 0.03 degrees to a step, and a step read at a focal off by F is the
// geometric reference, below F by 0.15 % at the level and 0.45 % at the ring pitch for F = 1.02 (1.1 % at F = 1.05). A residual
// compared with 360 s_g + drift (trackerClosure.test.ts) has to allow that shortfall; it is not the aligner's.
//
// Mutants this file must catch (align.ts, each applied from a byte backup): the LK update damped to half (`step` halved in
// alignPair's descent) and the descent skipped at L1 (ITERS_L1 = 0) both leave the seed's residual in the step: 'at the true
// focal ...' and 'a step the table never saw ...' read the step short and fail.
//
// Frames are the 9:16 analysis frame at the 70-degree prior, carrying the simulator's frame noise (sd 2, synth.ts's default).
// Every draw is seeded: one case is the same case every run.
//
// disk-reads: ui/src/next/hubs/sky/sheets/__sim__/png.ts
// disk-reads: ui/src/next/hubs/sky/sheets/pano/__tests__/synth/synth.ts
import assert from 'node:assert/strict';
import { lookBasis } from '../../photosphereGeometry';
import { alignPair } from '../align';
import { buildPyramid, prepareTemplate } from '../pyramid';
import {
  expSO3, intrinsicsAt, logSO3, priorFNorm, qinv, qmul, quatFromBasis, rotationFromRays, rotationHomography, unprojectPixel,
} from '../rotation';
import type { Quat, V3 } from '../types';
import { makeSynthScene, renderView, type SynthScene } from './synth/synth';

// A failing case is reported and the file carries on, so a mutant shows every case it turns red (the pattern of align.test.ts).
let passed = 0, failed = 0;
function test(name: string, fn: () => void) {
  try { fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; console.log(`FAIL ${name}\n  ${(e as Error).message.split(/\r?\n/)[0]}`); }
}

const W = 180, H = 320, DEG = Math.PI / 180, FNORM = priorFNorm(W, H);
const look = (az: number, alt: number): Quat => quatFromBasis(lookBasis(az, alt));
const SCENES: Record<string, SynthScene> = { a: makeSynthScene({ seed: 2 }), b: makeSynthScene({ seed: 3 }), holdout: makeSynthScene({ seed: 7 }) };

/** The bound on what LK adds to a step at the true focal, in degrees; the worst of the table below reads 0.024. */
const ABS_BOUND_DEG = 0.03;

interface Shift { scene: string; az0: number; pitch: number; dAz: number; dAlt: number; roll: number }
interface Reading { step: number; err: number; ratio: number }

/** Align a frame `shift` away from the template, with LK running at a focal `fRatio` times shorter than the one the frames were
 *  rendered at (1 is the true focal), from a prediction 5 % short of the true rotation (the gyro's error is of this order).
 *  `step` and `err` are degrees, `ratio` is the measured step along the true rotation's axis over the true step. */
function read(s: Shift, fRatio = 1): Reading {
  const r0 = intrinsicsAt(0, W, H, FNORM);
  const k0 = intrinsicsAt(0, W, H, FNORM / fRatio), k1 = intrinsicsAt(1, W, H, FNORM / fRatio), k2 = intrinsicsAt(2, W, H, FNORM / fRatio);
  const qa = look(s.az0, s.pitch), qb = qmul(look(s.az0 + s.dAz, s.pitch + s.dAlt), expSO3([0, 0, s.roll * DEG]));
  const truth = qmul(qinv(qb), qa), sc = SCENES[s.scene];
  const tpl = prepareTemplate(buildPyramid(renderView(sc, qa, r0, { seed: 100 + s.az0 }), W, H).pyr, k1, k2);
  const img = buildPyramid(renderView(sc, qb, r0, { seed: 200 + s.az0 }), W, H).pyr;
  const tv = logSO3(truth), pred = expSO3([tv[0] * 0.95, tv[1] * 0.95, tv[2] * 0.95]);
  const res = alignPair(tpl, img, pred, { l0: k0, l1: k1, l2: k2 }, { window: 'keyframe' });
  assert.ok(res.ok, `alignPair refused ${JSON.stringify(s)} at focal ratio ${fRatio}: ${res.reason}`);
  const mv = logSO3(res.qBA), n = Math.hypot(tv[0], tv[1], tv[2]), along = (tv[0] * mv[0] + tv[1] * mv[1] + tv[2] * mv[2]) / n;
  return { step: n / DEG, err: (along - n) / DEG, ratio: along / n };
}

/** What a rotation fitted to the exact pixel motion of `s` reads when the pinhole is `fRatio` times too short: every pixel of the
 *  L1 frame carried through the true rotation, both ends unprojected through the wrong focal, Horn's fit. No image, no LK. */
function geometricRatio(s: Shift, fRatio: number): number {
  const t = intrinsicsAt(1, W, H, FNORM), wrong = intrinsicsAt(1, W, H, FNORM / fRatio);
  const qa = look(s.az0, s.pitch), qb = qmul(look(s.az0 + s.dAz, s.pitch + s.dAlt), expSO3([0, 0, s.roll * DEG]));
  const truth = qmul(qinv(qb), qa), hm = rotationHomography(t, t, truth), a: V3[] = [], b: V3[] = [];
  for (let y = 0; y < t.h; y++) for (let x = 0; x < t.w; x++) {
    const u = x + 0.5, v = y + 0.5, d = hm[6] * u + hm[7] * v + hm[8];
    const ub = (hm[0] * u + hm[1] * v + hm[2]) / d, vb = (hm[3] * u + hm[4] * v + hm[5]) / d;
    if (ub < 0 || ub > t.w || vb < 0 || vb > t.h) continue;
    a.push(unprojectPixel(wrong, u, v)); b.push(unprojectPixel(wrong, ub, vb));
  }
  const tv = logSO3(truth), mv = logSO3(rotationFromRays(a, b)), n2 = tv[0] ** 2 + tv[1] ** 2 + tv[2] ** 2;
  return (tv[0] * mv[0] + tv[1] * mv[1] + tv[2] * mv[2]) / n2;
}

const shift = (scene: string, pitch: number, dAz: number, dAlt = 0, roll = 0, az0 = 40): Shift => ({ scene, az0, pitch, dAz, dAlt, roll });
const label = (s: Shift) => `scene ${s.scene} pitch ${s.pitch} d(${s.dAz}, ${s.dAlt}) roll ${s.roll}`;
const mean = (xs: number[]) => xs.reduce((p, c) => p + c, 0) / xs.length;

// ---- The table: shifts of every magnitude and direction, with and without roll, at two pitches and on two scenes ---------------

const TABLE: Shift[] = [];
for (const pitch of [0, 23]) {
  for (const [dAz, dAlt] of [[0.3, 0], [-0.3, 0], [1, 0], [2, 0], [4, 0], [-4, 0], [6, 0], [0, 1], [0, -3], [2, 2], [-3, 1.5]]) TABLE.push(shift('a', pitch, dAz, dAlt));
  for (const [dAz, dAlt] of [[0.3, 0], [1, 0], [4, 0], [-4, 0], [0, -3], [2, 2]]) TABLE.push(shift('b', pitch, dAz, dAlt));
}
for (const pitch of [0, 23]) for (const roll of [2, -3]) for (const dAz of [1, 4]) TABLE.push(shift('a', pitch, dAz, 0, roll));

const TABLE_READINGS = TABLE.map(s => ({ s, r: read(s) }));
const stepsOver2 = TABLE_READINGS.filter(x => x.s.dAz * x.s.dAz + x.s.dAlt * x.s.dAlt >= 4);

test('at the true focal LK adds at most 0.03 degrees to a step, and the mean ratio of steps of 2 degrees and more is 1.000 +- 0.002', () => {
  for (const { s, r } of TABLE_READINGS) console.log(`  ${label(s)}: true ${r.step.toFixed(3)} deg, error ${r.err.toFixed(4)} deg, ratio ${r.ratio.toFixed(5)}`);
  const worst = Math.max(...TABLE_READINGS.map(x => Math.abs(x.r.err))), m = mean(stepsOver2.map(x => x.r.ratio));
  console.log(`  worst error ${worst.toFixed(4)} deg over ${TABLE.length} shifts; mean ratio ${m.toFixed(5)} over ${stepsOver2.length} steps of 2 degrees and more`);
  for (const { s, r } of TABLE_READINGS) assert.ok(Math.abs(r.err) <= ABS_BOUND_DEG, `${label(s)}: error ${r.err.toFixed(4)} degrees`);
  assert.ok(Math.abs(m - 1) <= 0.002, `mean ratio ${m.toFixed(5)}: the aligner reads steps ${m < 1 ? 'short' : 'long'}`);
});

test('the premise of #1051 fails: steps of 2 degrees and more read long as often as short, so none is shifted by a scale', () => {
  const long = stepsOver2.filter(x => x.r.ratio > 1.0005).length, short = stepsOver2.filter(x => x.r.ratio < 0.9995).length;
  assert.ok(long >= 8 && short >= 8, `${long} read long and ${short} read short of ${stepsOver2.length}`);
  const meanErr = mean(stepsOver2.map(x => x.r.err));
  assert.ok(Math.abs(meanErr) < 0.003, `mean error ${meanErr.toFixed(4)} degrees: a scale of 0.3 % on 4 degrees would be 0.012`);
});

// ---- A step the table was not tuned on: another scene, other azimuths, magnitudes and a pitch the table never used --------------

test('a step the table never saw reads within 0.03 degrees too: another scene, azimuth, pitch and magnitudes', () => {
  const held = [shift('holdout', 11, 3.3, 0, 0, 200), shift('holdout', 11, -5.1, 0, 0, 200), shift('holdout', 11, 2.7, 1.1, 0, 310),
    shift('holdout', 14, 5.9, 0, 0, 120), shift('holdout', -15, -2.2, -1.7, 1.5, 75), shift('holdout', 14, 4.8, 0, -2.5, 120)];
  const rs = held.map(s => ({ s, r: read(s) }));
  for (const { s, r } of rs) {
    console.log(`  held out ${label(s)} az ${s.az0}: true ${r.step.toFixed(3)} deg, error ${r.err.toFixed(4)} deg, ratio ${r.ratio.toFixed(5)}`);
    assert.ok(Math.abs(r.err) <= ABS_BOUND_DEG, `${label(s)}: error ${r.err.toFixed(4)} degrees`);
  }
  assert.ok(Math.abs(mean(rs.map(x => x.r.ratio)) - 1) <= 0.002, `mean ratio ${mean(rs.map(x => x.r.ratio)).toFixed(5)}`);
});

// ---- The shortfall is the geometry of a wrong focal, not the aligner ----------------------------------------------------------------

test('at a focal 2 % short LK reads a step as pinhole geometry says, 0.15 % under 1.02 at the level and 0.45 % at the ring pitch', () => {
  const cases = [shift('a', 0, 4), shift('a', 0, -4), shift('a', 0, 6), shift('b', 23, 4), shift('b', 23, -4), shift('b', 23, 6), shift('a', 23, 2, 2)];
  for (const s of cases) {
    const lk = read(s, 1.02).ratio, geo = geometricRatio(s, 1.02);
    console.log(`  ${label(s)}: LK ${lk.toFixed(5)}, geometry ${geo.toFixed(5)}, LK over 1.02 ${(lk / 1.02).toFixed(5)}`);
    assert.ok(Math.abs(lk - geo) <= 0.0025, `${label(s)}: LK ${lk.toFixed(5)} against the geometric reference ${geo.toFixed(5)}`);
    const [lo, hi] = s.pitch === 0 ? [0.9975, 0.9992] : [0.9935, 0.997];
    assert.ok(geo / 1.02 > lo && geo / 1.02 < hi, `${label(s)}: the reference is ${(geo / 1.02).toFixed(5)} of 1.02, not in ${lo} to ${hi}`);
  }
  assert.ok(geometricRatio(shift('a', 23, 4), 1.02) < geometricRatio(shift('a', 0, 4), 1.02), 'the ring pitch reads further short than the level');
});

test('the geometric shortfall grows with the focal error: 5 % short reads 1.1 % under 1.05 at the ring pitch, and LK follows it', () => {
  const s = shift('b', 23, 4), lk = read(s, 1.05).ratio, geo = geometricRatio(s, 1.05);
  console.log(`  ${label(s)} at 5 %: LK ${lk.toFixed(5)}, geometry ${geo.toFixed(5)}, geometry over 1.05 ${(geo / 1.05).toFixed(5)}`);
  assert.ok(Math.abs(lk - geo) <= 0.004, `LK ${lk.toFixed(5)} against ${geo.toFixed(5)}`);
  assert.ok(geo / 1.05 < 0.992 && geo / 1.05 > 0.985, `the reference is ${(geo / 1.05).toFixed(5)} of 1.05`);
});

test('at the true focal the geometric reference is the step itself: there is no shortfall to account for', () => {
  for (const s of [shift('a', 0, 4), shift('a', 23, 4), shift('a', 23, 6, 1)]) assert.ok(Math.abs(geometricRatio(s, 1) - 1) < 1e-6, `${label(s)}: ${geometricRatio(s, 1)}`);
});

console.log(`alignStepScale.test: ${passed}/${passed + failed} passed`);
if (failed > 0) process.exitCode = 1;
export const result = { passed, failed, total: passed + failed };
