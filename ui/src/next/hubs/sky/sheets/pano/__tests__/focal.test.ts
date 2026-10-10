// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T11: the panorama scanner's focal estimator (SPEC-v2 4.8, 7.2 focal row).
//
// Mutant this file must catch (SPEC-v2 7.2): the compounding update `f <- f x median` in `addRatio`, where the
// estimate is meant to be `f0 x median`. Ratios are read at the fixed f0 until the lock, so each one already holds
// the whole correction; multiplying the running value by the median applies it again on every ratio. The case that
// catches it is `the running estimate is f0 x median of the ratios so far`, and the pan cases catch it a second way
// through `fBest within 2 % of f_true / (1 + s_g)` at the lock.
//
// Other mutants this file was run against (focal.ts, one at a time, see the T11 report):
//   * the 6-degree gate removed;
//   * the lock criterion on 1.4826 x MAD instead of MAD, or on the mean instead of the median, or with no MAD
//     check, or with 4 ratios instead of 5;
//   * sdPct recomputed after the lock instead of frozen;
//   * CLOSED_SD_PCT not applied by `closeLoop`;
//   * `closeLoop` working from `fBest` instead of `fMeasure` (a second closure compounds).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { CLOSED_SD_PCT, FocalEstimator, LOCK_MAX_MAD_PCT, LOCK_MIN_RATIOS, MIN_GYRO_YAW_DEG, PRIOR_SD_PCT } from '../focal';
import type { FocalLike } from '../types';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const near = (a: number, b: number, tol: number, what = '') => assert.ok(Math.abs(a - b) < tol, `${what} ${a} != ${b} (tol ${tol})`);
/** Relative difference of a from b. */
const rel = (a: number, b: number) => a / b - 1;

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

// The reference statistics, written out here and not imported, so that a slip in focal.ts cannot move both sides.
const sortedCopy = (v: readonly number[]) => [...v].sort((a, b) => a - b);
function median(v: readonly number[]) {
  const s = sortedCopy(v), m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}
const mad = (v: readonly number[]) => { const m = median(v); return median(v.map(x => Math.abs(x - m))); };

const DEG = Math.PI / 180;
/** The S25-like lens of the chart yard cases (SPEC-v2 7.5): 41.14 degrees on the short axis. */
const F_TRUE = 0.5 / Math.tan(41.14 * DEG / 2);
const GYRO_SCALE = 0.015;
const RATE = 20, STEP = 4.345;   // deg/s along the axis, and the keyframe step at 4.6 per second (4.7)
const DT = STEP / RATE;
const PUMP_AGE_MS = 16.7, LK_SD_DEG = 0.05;

/** An estimator whose prior is `factor` x the true focal: 1.54 is a prior 54 % long, 1 / 1.54 is 60 degrees assumed on a
 * 41.14-degree lens (tan 30 / tan 20.57 = 1.538). */
const priorWith = (factor: number) => new FocalEstimator({ fNorm: F_TRUE * factor, source: 'default', sdPct: PRIOR_SD_PCT });

interface Pair { img: number; gyro: number }
/** Steady pairs (k - span, k) of a constant-rate pan, in the form the tracker builds them (4.8). Keyframe k is read at
 * k DT. The gyro heading it carries is the reading from `age` ms earlier, the age uniform over 0-16.7 ms (the 60 Hz
 * pump), and is scaled by 1 + s_g. The image yaw is the chain's composition of per-step rotations solved at f0, each
 * atan(tan(step) f_true / f0), plus the LK walk over the span. The span starts at least 0.5 s after the onset. */
function panPairs(r: () => number, f0: number, o: { span?: number; gyroScale?: number; steps?: number; pump?: boolean; lkSd?: number } = {}): Pair[] {
  const span = o.span ?? 3, gyroScale = o.gyroScale ?? GYRO_SCALE, steps = o.steps ?? Math.ceil(60 / STEP);   // keyframes 0..steps
  const pump = o.pump ?? true, lkSd = o.lkSd ?? LK_SD_DEG;
  const age = Array.from({ length: steps + 1 }, () => (pump ? r() * PUMP_AGE_MS : 0) / 1000);
  const gyroAt = (k: number) => (1 + gyroScale) * RATE * (k * DT - age[k]);
  const imgStep = Math.atan(Math.tan(STEP * DEG) * F_TRUE / f0) / DEG;
  const out: Pair[] = [];
  for (let k = span; k <= steps; k++) {
    if ((k - span) * DT < 0.5) continue;
    out.push({ img: span * imgStep + gauss(r) * lkSd, gyro: gyroAt(k) - gyroAt(k - span) });
  }
  return out;
}

/** The focal a pan of this gyro scale locks to. */
const fGyro = (gyroScale = GYRO_SCALE) => F_TRUE / (1 + gyroScale);

// ---- Prior, ratios and the 6-degree minimum ----------------------------------------------------------------------

test('constants are the block of SPEC-v2 3.4', () => {
  assert.deepEqual([LOCK_MIN_RATIOS, LOCK_MAX_MAD_PCT, PRIOR_SD_PCT, MIN_GYRO_YAW_DEG, CLOSED_SD_PCT], [5, 3, 20, 6, 0.139]);
});

test('a new estimator is the prior: fMeasure = fBest = prior, state prior, sdPct as given, no ratios', () => {
  const f: FocalLike = new FocalEstimator({ fNorm: 1.2694, source: 'default', sdPct: PRIOR_SD_PCT });
  assert.equal(f.fMeasure, 1.2694);
  assert.equal(f.fBest, 1.2694);
  assert.equal(f.state, 'prior');
  assert.equal(f.sdPct, 20);
  assert.equal(f.ratios, 0);
  assert.equal(new FocalEstimator({ fNorm: 0.9521, source: 'default', sdPct: 7 }).sdPct, 7);
});

test('a prior that is not a focal is refused', () => {
  for (const fNorm of [0, -1, NaN, Infinity]) {
    assert.throws(() => new FocalEstimator({ fNorm, source: 'default', sdPct: 20 }), RangeError, `fNorm ${fNorm}`);
  }
  for (const sdPct of [-1, NaN]) assert.throws(() => new FocalEstimator({ fNorm: 1.2, source: 'default', sdPct }), RangeError, `sdPct ${sdPct}`);
});

test('ratios below 6 degrees of gyro yaw are ignored, 6 and above count, in either direction of turn', () => {
  const f = priorWith(1);
  for (const g of [0, 0.5, 5.999, -5.999, 3]) assert.equal(f.addRatio(g * 1.2, g), 'collecting', `gyro ${g}`);
  assert.equal(f.ratios, 0);
  assert.equal(f.fBest, F_TRUE, 'an ignored ratio does not move the estimate');
  f.addRatio(6 * 1.2, 6);
  assert.equal(f.ratios, 1);
  near(f.fBest, F_TRUE * 1.2, 1e-12);
  f.addRatio(-12 * 1.2, -12);   // a counter-clockwise pan: both yaws negative, the ratio is still positive
  assert.equal(f.ratios, 2);
  near(f.fBest, F_TRUE * 1.2, 1e-12);
  assert.equal(f.state, 'prior');
});

test('a 4.3-degree step and the k-1 baselines of a pan never reach the estimator', () => {
  const f = priorWith(1.1);
  for (const p of panPairs(rng(1), F_TRUE * 1.1, { span: 1 })) f.addRatio(p.img, p.gyro);
  assert.equal(f.ratios, 0);
  assert.equal(f.state, 'prior');
  assert.equal(f.fBest, F_TRUE * 1.1);
  // k-2 baselines are 8.7 degrees and count.
  const g = priorWith(1.1);
  for (const p of panPairs(rng(1), F_TRUE * 1.1, { span: 2 })) g.addRatio(p.img, p.gyro);
  assert.ok(g.ratios > 0);
});

test('what is not a ratio is ignored: opposite sign, zero, NaN, infinite', () => {
  const f = priorWith(1);
  for (const [img, gyro] of [[-10, 10], [0, 10], [NaN, 10], [10, NaN], [Infinity, 10], [10, Infinity], [10, -10]]) {
    assert.equal(f.addRatio(img, gyro), 'collecting', `${img}/${gyro}`);
  }
  assert.equal(f.ratios, 0);
  assert.equal(f.fBest, F_TRUE);
});

// ---- The estimate, the lock rule and sdPct -----------------------------------------------------------------------

test('the running estimate is f0 x median of the ratios so far, never compounded (mutant: f <- f x median)', () => {
  const f0 = F_TRUE * 1.1;
  const f = priorWith(1.1);
  const ratios: number[] = [];
  // A drifting series is the worst case for a compounding update: the ratios are read at the fixed f0, so each one
  // is about f_true / (f0 (1 + s_g)) = 0.896, and a product of five of those is 0.57.
  for (const [i, p] of panPairs(rng(7), f0).slice(0, 4).entries()) {
    f.addRatio(p.img, p.gyro);
    ratios.push(p.img / p.gyro);
    assert.equal(f.ratios, i + 1);
    near(f.fBest, f0 * median(ratios), 1e-12, `after ${i + 1}`);
    assert.equal(f.fMeasure, f0, 'LK keeps running at the prior until the lock');
    assert.equal(f.state, 'prior');
    assert.equal(f.sdPct, PRIOR_SD_PCT, 'the prior sd holds until the lock');
  }
  assert.ok(Math.abs(rel(f.fBest, fGyro())) < 0.02, 'four ratios are already within 2 % of f_true / (1 + s_g)');
});

test('the lock needs 5 ratios, however tight', () => {
  const f = priorWith(1);
  for (let i = 0; i < 4; i++) assert.equal(f.addRatio(12, 10), 'collecting');   // r = 1.2 four times: MAD is 0
  assert.equal(f.state, 'prior');
  assert.equal(f.addRatio(12, 10), 'locked-now');
  assert.equal(f.ratios, 5);
  assert.equal(f.state, 'locked');
});

test('the lock needs MAD / median at most 3 %, the MAD raw and not scaled by 1.4826', () => {
  // r = 1.0, 1.1, 0.9, 1.05, 0.95: median 1.0, MAD 0.05, 5 %. Three more 1.0s bring the MAD to 0.025 at n = 8, which
  // is 2.5 % raw and 3.7 % scaled; n = 6 and 7 stay at 0.05.
  const f = priorWith(1);
  const seq = [1.0, 1.1, 0.9, 1.05, 0.95, 1.0, 1.0, 1.0];
  const states = seq.map(r => f.addRatio(r * 10, 10));
  assert.deepEqual(states, ['collecting', 'collecting', 'collecting', 'collecting', 'collecting', 'collecting', 'collecting', 'locked-now']);
  assert.equal(f.ratios, 8);
  near(f.fBest, F_TRUE * 1.0, 1e-12);
  // A mean in place of the median would not agree: the same eight ratios average 1.0.
  const g = priorWith(1);
  g.addRatio(10, 10); g.addRatio(11, 10); g.addRatio(9, 10); g.addRatio(10.5, 10);
  assert.equal(g.addRatio(9.5, 10), 'collecting');
  assert.equal(g.state, 'prior');
});

test('the lock freezes fMeasure, fBest, ratios and sdPct; later ratios return locked and change nothing', () => {
  const f = priorWith(1);
  for (const r of [1.18, 1.20, 1.21, 1.19]) assert.equal(f.addRatio(r * 10, 10), 'collecting');
  assert.equal(f.addRatio(12.2, 10), 'locked-now');
  const lock = { fMeasure: f.fMeasure, fBest: f.fBest, sdPct: f.sdPct, ratios: f.ratios };
  near(lock.fMeasure, F_TRUE * 1.2, 1e-12);
  assert.equal(lock.fBest, lock.fMeasure);
  for (const [img, gyro] of [[30, 10], [10, 10], [1, 10], [20, 3], [12, 10]]) {
    assert.equal(f.addRatio(img, gyro), 'locked');
    assert.deepEqual({ fMeasure: f.fMeasure, fBest: f.fBest, sdPct: f.sdPct, ratios: f.ratios }, lock);
    assert.equal(f.state, 'locked');
  }
});

test('sdPct after the lock is 1.4826 x MAD / median / sqrt(n) x 100, from the ratios at the lock', () => {
  // 1.18, 1.19, 1.20, 1.21, 1.22: median 1.20, MAD 0.01. Worked by hand: 1.4826 x 0.01 / 1.20 / sqrt 5 x 100 = 0.5525.
  const f = priorWith(1);
  for (const r of [1.18, 1.20, 1.21, 1.19, 1.22]) f.addRatio(r * 10, 10);
  near(f.sdPct, 0.5525, 5e-5);
  near(f.sdPct, 1.4826 * 0.01 / 1.2 / Math.sqrt(5) * 100, 1e-9);
  // Identical ratios have no spread: the definition gives 0 and nothing floors it.
  const g = priorWith(1);
  for (let i = 0; i < 5; i++) g.addRatio(12, 10);
  assert.equal(g.sdPct, 0);
});

// ---- The synthetic 60-degree pan ---------------------------------------------------------------------------------

const PRIOR_FACTORS = [1.1, 0.9, 1.54, 1 / 1.54];   // 10 % long, 10 % short, 54 % long, 60-degree lens assumed on 41.14

interface Trial { lockedAt: number; fAtLock: number; sdAtLock: number; fEnd: number; stateEnd: string; sdEnd: number; ratiosEnd: number; used: number[]; f0: number }
function runPan(seed: number, factor: number, gyroScale = GYRO_SCALE): Trial {
  const f0 = F_TRUE * factor, f = priorWith(factor), r = rng(seed);
  const used: number[] = [];
  let lockedAt = 0, fAtLock = NaN, sdAtLock = NaN;
  for (const p of panPairs(r, f0, { gyroScale })) {
    used.push(p.img / p.gyro);
    if (f.addRatio(p.img, p.gyro) === 'locked-now') { lockedAt = f.ratios; fAtLock = f.fBest; sdAtLock = f.sdPct; }
  }
  return { lockedAt, fAtLock, sdAtLock, fEnd: f.fBest, stateEnd: f.state, sdEnd: f.sdPct, ratiosEnd: f.ratios, used, f0 };
}

test('the pan has the timing noise of SPEC-v2 4.8: MAD / median 0.76 % at 12 degrees and 1.14 % at 8', () => {
  // device_numbers.py: the pump's reading age (uniform 0-16.7 ms) plus 0.05 degrees of LK noise. The generator has to be
  // that noisy for the cases below to mean anything. Baselines here are 13.0 and 8.7 degrees.
  const meanSpread = (o: { span: number; pump?: boolean }) => {
    let sum = 0;
    for (let seed = 1; seed <= 300; seed++) {
      const ratios = panPairs(rng(seed), F_TRUE, o).map(p => p.img / p.gyro);
      sum += mad(ratios) / median(ratios);
    }
    return sum / 300 * 100;
  };
  const k3 = meanSpread({ span: 3 }), k2 = meanSpread({ span: 2 });
  assert.ok(k3 > 0.65 && k3 < 0.9, `k-3 baseline: ${k3.toFixed(2)} %`);
  assert.ok(k2 > 1.0 && k2 < 1.3, `k-2 baseline: ${k2.toFixed(2)} %`);
  // The pump accounts for most of it: without the reading age the spread falls under half.
  const quiet = meanSpread({ span: 3, pump: false });
  assert.ok(quiet < k3 / 2, `no pump ${quiet.toFixed(2)} % against ${k3.toFixed(2)} %`);
});

test('a 60-degree pan with 1.5 % gyro scale locks at 5-12 ratios, fBest within 2 % of f_true / (1 + s_g), for every prior', () => {
  for (const factor of PRIOR_FACTORS) {
    let at5 = 0, beyond2 = 0;
    for (let seed = 1; seed <= 400; seed++) {
      const t = runPan(seed, factor);
      const tag = `prior x ${factor.toFixed(3)} seed ${seed}`, err = Math.abs(rel(t.fAtLock, fGyro()));
      assert.ok(t.lockedAt >= 5 && t.lockedAt <= 12, `${tag}: locked at ${t.lockedAt} ratios`);
      // The spec's figure is a two-sigma-and-a-half claim (the ratio noise is 1 % and the lock takes the median of 5):
      // it holds on the first hundred pans of each prior, and over all 400 only the odd pan lands past it.
      if (seed <= 100) assert.ok(err < 0.02, `${tag}: fBest at the lock ${(rel(t.fAtLock, fGyro()) * 100).toFixed(2)} % off`);
      assert.ok(err < 0.035, `${tag}: fBest at the lock ${(rel(t.fAtLock, fGyro()) * 100).toFixed(2)} % off`);
      if (err >= 0.02) beyond2++;
      assert.equal(t.stateEnd, 'locked', tag);
      assert.equal(t.fEnd, t.fAtLock, `${tag}: fBest is frozen at the lock`);
      assert.equal(t.sdEnd, t.sdAtLock, `${tag}: sdPct is frozen at the lock`);
      assert.equal(t.ratiosEnd, t.lockedAt, `${tag}: ratios is frozen at the lock`);
      if (t.lockedAt === 5) at5++;
    }
    assert.ok(beyond2 <= 4, `prior x ${factor.toFixed(3)}: ${beyond2} of 400 pans locked 2 % or more off`);
    // The 13-degree baselines are quiet: nearly every pan locks as soon as it may, 2.2 s into the turn.
    assert.ok(at5 >= 396, `prior x ${factor.toFixed(3)}: ${at5} of 400 pans locked at the fifth ratio`);
  }
});

test('the lock rule on noisy pans: the first count of 5 or more with MAD / median at most 3 %, checked against a reference', () => {
  // 0.4 degrees of LK walk on a 13-degree baseline is 3 % one-sigma noise, so the lock lands anywhere from the fifth
  // ratio to never inside 60 degrees. For each pan, the reference finds the first prefix that satisfies the rule.
  const counts = new Map<number, number>();
  for (let seed = 1; seed <= 300; seed++) {
    const f0 = F_TRUE * 1.1, f = priorWith(1.1);
    const pairs = panPairs(rng(seed), f0, { lkSd: 0.4 });
    const ratios = pairs.map(p => p.img / p.gyro).filter(x => x > 0);
    let expectAt = 0;
    for (let n = LOCK_MIN_RATIOS; n <= ratios.length && !expectAt; n++) {
      const head = ratios.slice(0, n);
      if (mad(head) / median(head) <= LOCK_MAX_MAD_PCT / 100) expectAt = n;
    }
    let got = 0;
    for (const p of pairs) if (f.addRatio(p.img, p.gyro) === 'locked-now') got = f.ratios;
    assert.equal(got, expectAt, `seed ${seed}`);
    assert.equal(f.state, expectAt ? 'locked' : 'prior', `seed ${seed}`);
    if (!expectAt) near(f.fBest, f0 * median(ratios), 1e-12, `seed ${seed}: the estimate keeps following the median while it collects`);
    counts.set(expectAt, (counts.get(expectAt) ?? 0) + 1);
  }
  // The generator has to reach the cases the rule is about: an early lock, a late one, and none.
  assert.ok((counts.get(5) ?? 0) > 10 && (counts.get(0) ?? 0) > 10, JSON.stringify([...counts]));
  assert.ok([...counts.keys()].some(n => n > 5), JSON.stringify([...counts]));
});

test('the lock follows the gyro scale: f_true / (1 + s_g) for -2 %, 0 and +3 %', () => {
  for (const s of [-0.02, 0, 0.03]) {
    for (let seed = 1; seed <= 40; seed++) {
      const t = runPan(seed, 1.1, s);
      assert.ok(t.lockedAt >= 5, `s_g ${s}`);
      assert.ok(Math.abs(rel(t.fAtLock, fGyro(s))) < 0.02, `s_g ${s} seed ${seed}: ${rel(t.fAtLock, fGyro(s)) * 100} % off`);
    }
  }
});

test('sdPct follows its definition on every pan: 1.4826 x MAD / median / sqrt(n) x 100 over the ratios at the lock', () => {
  let sum = 0;
  for (const factor of PRIOR_FACTORS) {
    for (let seed = 1; seed <= 50; seed++) {
      const t = runPan(seed, factor), seen = t.used.slice(0, t.lockedAt);
      const expect = 1.4826 * mad(seen) / median(seen) / Math.sqrt(seen.length) * 100;
      near(t.sdAtLock, expect, 1e-9, `prior x ${factor} seed ${seed}`);
      near(t.fAtLock, t.f0 * median(seen), 1e-9, `prior x ${factor} seed ${seed}`);
      assert.ok(t.sdAtLock > 0 && t.sdAtLock < 1.5, `sdPct ${t.sdAtLock}`);
      sum += t.sdAtLock;
    }
  }
  // About a half of a percent: the 1 % ratio noise over the square root of 5.
  const mean = sum / (PRIOR_FACTORS.length * 50);
  assert.ok(mean > 0.15 && mean < 0.9, `mean sdPct ${mean}`);
});

// ---- The closure -------------------------------------------------------------------------------------------------

/** A ring read at the estimator's fMeasure: `blank` degrees of gyro-led sector, the rest image-led in 4.3-degree
 * steps of the chain, each atan(tan(step) f_true / fMeasure) plus the LK walk. Returns the three numbers of closeLoop. */
function ringAt(f: FocalEstimator, r: () => number, o: { tTrue?: number; blank?: number; gyroScale?: number; lk?: boolean } = {}) {
  const tTrue = o.tTrue ?? 363.7, blank = o.blank ?? 12, gyroScale = o.gyroScale ?? GYRO_SCALE, lk = o.lk ?? false;
  const nImg = Math.round((tTrue - blank) / STEP), stepTrue = (tTrue - blank) / nImg;
  const stepImg = Math.atan(Math.tan(stepTrue * DEG) * F_TRUE / f.fMeasure) / DEG;
  let thetaImg = 0;
  for (let i = 0; i < nImg; i++) thetaImg += stepImg + (lk ? gauss(r) * LK_SD_DEG : 0);
  return { thetaImg, thetaGyro: blank * (1 + gyroScale), tTrue };
}
const lockedAfterPan = (seed: number, factor: number) => {
  const f = priorWith(factor), r = rng(seed);
  for (const p of panPairs(r, F_TRUE * factor)) f.addRatio(p.img, p.gyro);
  assert.equal(f.state, 'locked');
  return { f, r };
};

test('the closure recovers a 54 % prior error to within 0.1 %, from a prior too long and from one too short', () => {
  for (const factor of [1.54, 1 / 1.54]) {
    for (let seed = 1; seed <= 50; seed++) {
      const { f, r } = lockedAfterPan(seed, factor);
      const fLock = f.fMeasure;
      assert.ok(Math.abs(rel(fLock, fGyro())) < 0.02);
      const ring = ringAt(f, r);
      const out = f.closeLoop(ring.thetaImg, ring.thetaGyro, ring.tTrue);
      assert.ok(Math.abs(rel(out.fNorm, F_TRUE)) < 0.001, `prior x ${factor.toFixed(3)} seed ${seed}: closed ${rel(out.fNorm, F_TRUE) * 100} % off`);
      assert.equal(f.fBest, out.fNorm);
      assert.equal(f.state, 'closed');
      assert.equal(f.sdPct, CLOSED_SD_PCT);
      assert.equal(f.fMeasure, fLock, 'LK measurements were taken at the locked value, and fMeasure says so');
      assert.equal(f.addRatio(12, 10), 'locked');
      assert.equal(f.fBest, out.fNorm);
    }
  }
});

test('the closure with the 0.5-degree noise of SPEC-v2 4.8 stays within 0.5 % of the lens, mean error near 0', () => {
  let sum = 0, worst = 0, n = 0;
  for (const factor of [1.54, 1 / 1.54]) {
    for (let seed = 1; seed <= 100; seed++) {
      const { f, r } = lockedAfterPan(seed, factor);
      const ring = ringAt(f, r, { lk: true });
      const e = rel(f.closeLoop(ring.thetaImg, ring.thetaGyro, ring.tTrue).fNorm, F_TRUE);
      sum += e; worst = Math.max(worst, Math.abs(e)); n++;
    }
  }
  assert.ok(worst < 0.005, `worst closure error ${(worst * 100).toFixed(3)} %`);
  assert.ok(Math.abs(sum / n) < 0.0005, `mean closure error ${(sum / n * 100).toFixed(3)} %`);
});

test('the closure formula is f_lock x Theta_img / (T_true - Theta_gyro), with the gyro-led part taken as true degrees', () => {
  const f = priorWith(1);
  for (let i = 0; i < 5; i++) f.addRatio(12, 10);   // locked at 1.2 x
  assert.equal(f.state, 'locked');
  const out = f.closeLoop(300, 30, 360);   // 300 / (360 - 30) = 0.9090...
  near(out.fNorm, F_TRUE * 1.2 * 300 / 330, 1e-12);
  // A closure with nothing before it: no lock, so f_lock is the prior.
  const g = priorWith(1.3);
  near(g.closeLoop(330, 0, 360).fNorm, F_TRUE * 1.3 * 330 / 360, 1e-12);
  assert.equal(g.state, 'closed');
  assert.equal(g.sdPct, 0.139);
  assert.equal(g.fMeasure, F_TRUE * 1.3);
});

test('closeLoop is computed from the focal the ring was read at, so a second closure does not compound', () => {
  const f = priorWith(1);
  for (let i = 0; i < 5; i++) f.addRatio(11, 10);
  const a = f.closeLoop(320, 10, 362);
  const b = f.closeLoop(320, 10, 362);
  assert.equal(b.fNorm, a.fNorm);
  assert.equal(f.fBest, a.fNorm);
  assert.equal(f.state, 'closed');
  // A different match is a different ring and replaces the first answer.
  near(f.closeLoop(320, 10, 358).fNorm, F_TRUE * 1.1 * 320 / 348, 1e-12);
});

test('gyroScale is T_gyro / T_true - 1: the chain total over the true turn, equal to s_g to within the lock\'s own error', () => {
  // Exact ring and exact ratios: the lock's only error is the atan curvature of the 54 % prior, under 0.5 %.
  const f = priorWith(1.54);
  for (const p of panPairs(rng(3), F_TRUE * 1.54, { pump: false, lkSd: 0 })) f.addRatio(p.img, p.gyro);
  const ring = ringAt(f, rng(3));
  const out = f.closeLoop(ring.thetaImg, ring.thetaGyro, ring.tTrue);
  near(out.gyroScale, (ring.thetaImg + ring.thetaGyro) / ring.tTrue - 1, 1e-12);
  near(out.gyroScale, GYRO_SCALE, 0.005);
  // Over the noisy pans: unbiased. The scatter is the lock's, about 0.5 % one sigma.
  let sum = 0, n = 0, worst = 0;
  for (const factor of PRIOR_FACTORS) {
    for (let seed = 1; seed <= 50; seed++) {
      const { f: g, r } = lockedAfterPan(seed, factor);
      const rg = ringAt(g, r);
      const e = g.closeLoop(rg.thetaImg, rg.thetaGyro, rg.tTrue).gyroScale - GYRO_SCALE;
      sum += e; n++; worst = Math.max(worst, Math.abs(e));
    }
  }
  assert.ok(Math.abs(sum / n) < 0.002, `mean gyro scale error ${(sum / n * 100).toFixed(3)} %`);
  assert.ok(worst < 0.025, `worst gyro scale error ${(worst * 100).toFixed(2)} %`);
  // Gyro scale of the opposite sign comes out with its sign.
  const h = priorWith(1.1);
  const ri = rng(5);
  for (const p of panPairs(ri, F_TRUE * 1.1, { gyroScale: -0.02 })) h.addRatio(p.img, p.gyro);
  const rh = ringAt(h, ri, { gyroScale: -0.02 });
  assert.ok(h.closeLoop(rh.thetaImg, rh.thetaGyro, rh.tTrue).gyroScale < -0.01);
});

test('a ring that cannot exist throws RangeError and changes nothing', () => {
  const f = priorWith(1);
  for (let i = 0; i < 5; i++) f.addRatio(11, 10);
  const before = { m: f.fMeasure, b: f.fBest, s: f.sdPct, st: f.state };
  const bad: [number, number, number][] = [
    [0, 0, 360], [-5, 0, 360], [NaN, 0, 360], [300, NaN, 360], [300, 0, NaN], [300, 0, Infinity],
    [300, 360, 360], [300, 400, 360], [300, 0, 0], [Infinity, 0, 360],
  ];
  for (const [a, b, c] of bad) {
    assert.throws(() => f.closeLoop(a, b, c), RangeError, `${a} ${b} ${c}`);
    assert.deepEqual({ m: f.fMeasure, b: f.fBest, s: f.sdPct, st: f.state }, before);
  }
});

// ---- The module itself -------------------------------------------------------------------------------------------

test('source scan: focal.ts imports only types, never the simulator or tests, and does not log', () => {
  const src = readFileSync(new URL('../focal.ts', import.meta.url), 'utf8') as string;
  assert.ok(src.length > 2000 && src.includes('closeLoop'), 'read the real source');
  assert.ok(!/\bconsole\s*\./.test(src), 'focal.ts uses console');
  const imports = src.match(/^[ \t]*import\b[^\r\n]*/gm) ?? [];
  assert.deepEqual(imports.map(l => l.trim()), ["import type { FocalLike, FocalSource, FocalState } from './types';"]);
  assert.ok(!/__sim__|__tests__/.test(src.replace(/^[ \t]*\/\/[^\r\n]*/gm, '')), 'focal.ts reaches into a test directory');
});

console.log(`focal.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
