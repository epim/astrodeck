// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T14: the conservative polyline (SPEC-v2 5.4).
//
// Mutant this file must catch (SPEC-v2 7.2): a vertex not lifted to the next bin's left edge. Without the lift a
// segment ends at a vertex that is below the bin that starts there, so the line dips under that bin at its left end.
// G1 at every bin end, over 200 seeded random profiles and the chart yard, sees it on every step up.
//
// G1 and G2 are checked here from the returned points alone, with `horizonAltAt` (the reader the engine uses) and, in
// one test, against a second interpolation written below, so the guarantees are stated against the line a caller gets.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { horizonAltAt } from '../../../../../../lib/horizonModel';
import type { HorizonPoint } from '../../types';
import { TAU_LADDER, conservativePolyline } from '../simplify';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

/** mulberry32: a seeded generator, so every random profile below is the same profile every run. */
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

const N = 720;
const EPS = 1e-9;

// ---- Random profiles: eight shapes of horizon, each a Float32Array of 720 bins -----------------------------

function randomProfile(seed: number): Float32Array {
  const r = rng(seed * 7919 + 13);
  const p = new Float64Array(N);
  switch (seed % 8) {
    case 0:                                 // independent bins
      for (let i = 0; i < N; i++) p[i] = r() * 30;
      break;
    case 1: {                               // a random walk
      let v = r() * 20;
      for (let i = 0; i < N; i++) { v = Math.min(40, Math.max(0, v + gauss(r) * 1.5)); p[i] = v; }
      break;
    }
    case 2:                                 // plateaus with steps
      for (let i = 0; i < N;) {
        const len = 1 + Math.floor(r() * 60), h = r() * 45;
        for (let k = 0; k < len && i < N; k++, i++) p[i] = h;
      }
      break;
    case 3:                                 // open ground with poles, one to three bins wide
      for (let k = 0; k < 25; k++) {
        const at = Math.floor(r() * N), w = 1 + Math.floor(r() * 3), h = 5 + r() * 55;
        for (let j = 0; j < w; j++) p[(at + j) % N] = Math.max(p[(at + j) % N], h);
      }
      break;
    case 4:                                 // a low baseline with blocked sectors at exactly 90
      for (let i = 0; i < N; i++) p[i] = r() * 20;
      for (let k = 0; k < 6; k++) {
        const at = Math.floor(r() * N), len = 1 + Math.floor(r() * 80);
        for (let j = 0; j < len; j++) p[(at + j) % N] = 90;
      }
      break;
    case 5: {                               // a jagged tree line: bumps plus correlated noise
      for (let k = 0; k < 40; k++) {
        const c = r() * 360, w = 3 + r() * 17, h = 5 + r() * 23;
        for (let i = 0; i < N; i++) {
          const d = Math.min(Math.abs(i / 2 - c), 360 - Math.abs(i / 2 - c));
          if (d < w) p[i] = Math.max(p[i], h * Math.sqrt(1 - (d / w) ** 2));
        }
      }
      let j = 0;
      for (let i = 0; i < N; i++) { j = 0.85 * j + gauss(r) * 0.9; if (p[i] > 0) p[i] = Math.min(90, Math.max(0, p[i] + j)); }
      break;
    }
    case 6:                                 // flat ground and one bin
      p[Math.floor(r() * N)] = 1 + r() * 60;
      break;
    default:                                // a ridge that looks down: negative altitudes
      for (let i = 0; i < N; i++) p[i] = -8 + r() * 8;
      break;
  }
  return Float32Array.from(p);
}

// ---- The guarantees, stated against the returned points --------------------------------------------------------

/** The largest amount by which the line is below a bin at either of that bin's two ends (negative when never below). */
function g1Shortfall(alt: Float32Array, points: HorizonPoint[]): number {
  const step = 360 / alt.length;
  let worst = -Infinity;
  for (let i = 0; i < alt.length; i++) {
    worst = Math.max(worst, alt[i] - horizonAltAt(points, i * step), alt[i] - horizonAltAt(points, (i + 1) * step));
  }
  return worst;
}

/** Bins whose centre the line passes more than tau above the bin. G2 allows these only on shoulders. */
function g2Violations(alt: Float32Array, points: HorizonPoint[], tau: number): number {
  const step = 360 / alt.length;
  let n = 0;
  for (let i = 0; i < alt.length; i++) if (horizonAltAt(points, (i + 0.5) * step) > alt[i] + tau + EPS) n++;
  return n;
}

function checkShape(points: HorizonPoint[], what: string) {
  assert.ok(points.length >= 1, `${what}: never empty`);
  for (let k = 0; k < points.length; k++) {
    const p = points[k];
    assert.ok(Number.isFinite(p.az) && Number.isFinite(p.alt), `${what}: vertex ${k} is finite`);
    assert.ok(p.az >= 0 && p.az < 360, `${what}: az ${p.az} in [0, 360)`);
    assert.ok(p.alt >= -10 && p.alt <= 90, `${what}: alt ${p.alt} within the server's -10..90`);
    assert.equal(p.az * 2, Math.round(p.az * 2), `${what}: az ${p.az} sits on a bin edge`);
    if (k > 0) assert.ok(p.az > points[k - 1].az, `${what}: azimuths distinct and ascending at ${k}`);
  }
}

/** A second reader of a closed polyline: find the bracketing pair by scanning, wrapping across 360. */
function lineAt(points: HorizonPoint[], az: number): number {
  const a = ((az % 360) + 360) % 360;
  const pts = [...points].sort((p, q) => p.az - q.az);
  const first = pts[0], last = pts[pts.length - 1];
  if (a < first.az) return last.alt + (first.alt - last.alt) * (a + 360 - last.az) / (first.az + 360 - last.az);
  for (let k = 0; k + 1 < pts.length; k++) {
    if (a >= pts[k].az && a <= pts[k + 1].az) return pts[k].alt + (pts[k + 1].alt - pts[k].alt) * (a - pts[k].az) / (pts[k + 1].az - pts[k].az);
  }
  return pts.length === 1 ? last.alt : last.alt + (first.alt - last.alt) * (a - last.az) / (first.az + 360 - last.az);
}

// ---- The chart yard fixture and its truth -------------------------------------------------------------------------

const FIXTURE = JSON.parse(readFileSync(new URL('./fixtures/chartyard-reference-720.json', import.meta.url), 'utf8')) as { source: string; bins: number; alt: number[] };
const TRUTH_URL = new URL('../../../../../../../../../tools/photosphere_sim/fixtures/chartyard-shortpan-60/truth/reference-horizon.json', import.meta.url);
const TRUTH = (JSON.parse(readFileSync(TRUTH_URL, 'utf8')) as { bins: number; alt_max: number[] }).alt_max.map(a => Math.min(90, Math.max(0, a)));
const CHART = Float32Array.from(FIXTURE.alt);
const nearestRank = (sorted: number[], q: number) => sorted[Math.max(0, Math.ceil(q * sorted.length) - 1)];

/** |line - truth| at the centre of each 0.1-degree truth bin, and the worst amount by which the line is below the truth. */
function againstTruth(points: HorizonPoint[]): { p95: number; under: number } {
  const errs: number[] = [];
  let under = 0;
  for (let j = 0; j < TRUTH.length; j++) {
    const line = lineAt(points, (j + 0.5) * 0.1);
    errs.push(Math.abs(line - TRUTH[j]));
    under = Math.max(under, TRUTH[j] - line);
  }
  errs.sort((a, b) => a - b);
  return { p95: nearestRank(errs, 0.95), under };
}

// ---- Tests ------------------------------------------------------------------------------------------------------

test('TAU_LADDER is the ladder of SPEC-v2 5.4', () => {
  assert.deepEqual([...TAU_LADDER], [0.25, 0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32, 45, 90]);
});

test('200 seeded random profiles: G1 at every bin end, at most 180 points, never empty, distinct sorted azimuths under 360', () => {
  const shapes = new Set<number>();
  let steps = 0;
  for (let seed = 0; seed < 200; seed++) {
    const alt = randomProfile(seed);
    shapes.add(seed % 8);
    for (let i = 0; i < N; i++) if (alt[i] !== alt[(i + 1) % N]) steps++;
    const { points, tau, shoulders } = conservativePolyline(alt, 180);
    const what = `profile ${seed} (shape ${seed % 8})`;
    checkShape(points, what);
    assert.ok(points.length <= 180, `${what}: ${points.length} points`);
    assert.ok(TAU_LADDER.includes(tau), `${what}: tau ${tau} is on the ladder`);
    const shortfall = g1Shortfall(alt, points);
    assert.ok(shortfall <= EPS, `${what}: the line is ${shortfall} below a bin end`);
    assert.ok(g2Violations(alt, points, tau) <= shoulders, `${what}: G2 broken on more bins than there are shoulders (${shoulders})`);
  }
  assert.equal(shapes.size, 8, 'all eight shapes were generated');
  assert.ok(steps > 20000, `the profiles have steps to lift over (${steps})`);
});

test('the chosen tau is the first rung that fits: a smaller cap climbs the ladder, and the line is still never below a bin', () => {
  let climbed = 0;
  for (let seed = 0; seed < 40; seed++) {
    const alt = randomProfile(seed);
    let prevTau = 0;
    for (const cap of [180, 120, 80, 50, 30, 20, 12, 8, 5, 3, 2]) {
      const { points, tau } = conservativePolyline(alt, cap);
      const what = `profile ${seed}, cap ${cap}`;
      checkShape(points, what);
      assert.ok(points.length <= cap, `${what}: ${points.length} points`);
      assert.ok(g1Shortfall(alt, points) <= EPS, `${what}: G1`);
      assert.ok(tau >= prevTau, `${what}: tau ${tau} never falls as the cap tightens (was ${prevTau})`);
      if (tau > prevTau) climbed++;
      prevTau = tau;
    }
  }
  assert.ok(climbed > 100, `the ladder was climbed ${climbed} times`);
});

test('horizonAltAt agrees with an independent interpolation of the returned points, and passes through every vertex', () => {
  for (let seed = 0; seed < 24; seed++) {
    const { points } = conservativePolyline(randomProfile(seed), 180);
    for (const p of points) assert.ok(Math.abs(horizonAltAt(points, p.az) - p.alt) < EPS, `vertex at ${p.az}`);
    for (let k = 0; k <= 4 * N; k++) {
      const az = k * 0.125;                  // bin edges, quarter points and centres
      const a = horizonAltAt(points, az), b = lineAt(points, az);
      assert.ok(Math.abs(a - b) < 1e-9, `profile ${seed} at ${az}: ${a} vs ${b}`);
    }
  }
  const { points } = conservativePolyline(CHART, 60);
  for (let k = 0; k <= 4 * N; k++) assert.ok(Math.abs(horizonAltAt(points, k * 0.125) - lineAt(points, k * 0.125)) < 1e-9);
});

test('a step up is lifted: the vertex at the step edge is at the value of the taller bin, and the line clears both bins', () => {
  // Bins 0..99 at 0, 100..199 at 10, 200..719 at 0. The vertex at the left edge of bin 100 (az 50) must be at least 10.
  const alt = new Float32Array(N);
  alt.fill(10, 100, 200);
  for (const cap of [180, 40, 8, 4]) {
    const r = conservativePolyline(alt, cap);
    assert.ok(horizonAltAt(r.points, 50) >= 10 - EPS, `cap ${cap}: line at 50 degrees is ${horizonAltAt(r.points, 50)}`);
    assert.ok(horizonAltAt(r.points, 100) >= 10 - EPS);
    assert.ok(g1Shortfall(alt, r.points) <= EPS);
  }
  const r = conservativePolyline(alt, 180);
  const at50 = r.points.find(p => p.az === 50);
  assert.ok(at50 && at50.alt >= 10 - EPS, 'a vertex stands on the step edge, at or above the taller bin');
});

test('a ramp 0, 80, 90 never produces a vertex above 90, even at a tolerance that allows the climb', () => {
  const alt = new Float32Array(N);
  for (const at of [40, 200, 380, 560]) { alt[at + 1] = 80; alt[at + 2] = 90; }
  for (const cap of [3, 4, 5, 6, 8, 12]) {
    const r = conservativePolyline(alt, cap);
    checkShape(r.points, `ramp, cap ${cap}`);
    assert.ok(g1Shortfall(alt, r.points) <= EPS, `ramp, cap ${cap}: G1`);
  }
});

test('flat and degenerate profiles are lines, not empties', () => {
  const flat = conservativePolyline(new Float32Array(N), 180);
  assert.deepEqual(flat.points, [{ az: 0, alt: 0 }, { az: 180, alt: 0 }]);
  assert.equal(flat.tau, 0.25);
  assert.equal(flat.shoulders, 0);

  const blocked = conservativePolyline(new Float32Array(N).fill(90), 180);
  assert.deepEqual(blocked.points.map(p => p.alt), [90, 90]);

  const level = conservativePolyline(new Float32Array(N).fill(12.5), 180);
  assert.deepEqual(level.points, [{ az: 0, alt: 12.5 }, { az: 180, alt: 12.5 }]);

  // The line starts at the highest bin's left edge: the first maximum on a tie.
  const two = new Float32Array(N); two[100] = 30; two[400] = 30;
  const t = conservativePolyline(two, 180);
  assert.ok(t.points.some(p => p.az === 50 && p.alt === 30));
  assert.ok(g1Shortfall(two, t.points) <= EPS);

  // One bin only.
  const one = conservativePolyline(Float32Array.of(7), 180);
  assert.deepEqual(one.points, [{ az: 0, alt: 7 }, { az: 180, alt: 7 }]);
  // A cap of 1 gives the single vertex, never nothing.
  assert.equal(conservativePolyline(new Float32Array(N), 1).points.length, 1);
  // No bins at all: the safe answer, blocked.
  const none = conservativePolyline(new Float32Array(0), 180);
  assert.deepEqual(none.points.map(p => p.alt), [90, 90]);
});

test('a single pole stays a pole: the line clears it at both of its bin ends and falls away on both sides', () => {
  const alt = new Float32Array(N);
  alt[300] = 45;
  const r = conservativePolyline(alt, 180);
  assert.ok(horizonAltAt(r.points, 150) >= 45 - EPS && horizonAltAt(r.points, 150.5) >= 45 - EPS);
  assert.ok(horizonAltAt(r.points, 148) < 45 && horizonAltAt(r.points, 153) < 45);
  assert.ok(g1Shortfall(alt, r.points) <= EPS);
});

test('a bin that is not a number counts as blocked', () => {
  const alt = new Float32Array(N);
  alt[100] = NaN;
  alt[500] = 200;                            // above the server's 90: clamped, not published
  const r = conservativePolyline(alt, 180);
  checkShape(r.points, 'nan');
  assert.ok(horizonAltAt(r.points, 50) >= 90 - EPS && horizonAltAt(r.points, 50.5) >= 90 - EPS);
  assert.ok(horizonAltAt(r.points, 250) >= 90 - EPS);
  assert.ok(r.points.every(p => p.alt <= 90));
});

test('if even the last tolerance cannot fit, the line is flat at the maximum, with the tolerance it needs', () => {
  const alt = new Float32Array(N).fill(-10);
  alt[200] = 90;                             // a drop of 100 degrees is more than the ladder's 90
  const r = conservativePolyline(alt, 2);
  assert.equal(r.points.length, 2);
  assert.deepEqual(r.points.map(p => p.alt), [90, 90]);
  assert.equal(r.tau, 100);
  assert.equal(r.shoulders, 0);
  assert.ok(g1Shortfall(alt, r.points) <= EPS);
});

test('conservativePolyline does not write its input and is deterministic', () => {
  const alt = randomProfile(5);
  const copy = alt.slice();
  const a = conservativePolyline(alt, 100), b = conservativePolyline(alt, 100);
  assert.deepEqual(Array.from(alt), Array.from(copy));
  assert.deepEqual(a, b);
});

test('chart yard fixture: the 720 maxima of the committed 0.1-degree truth, clamped to 0..90', () => {
  assert.equal(FIXTURE.bins, 720);
  assert.equal(FIXTURE.alt.length, 720);
  assert.ok(/reference-horizon\.json/.test(FIXTURE.source) && /maximum/.test(FIXTURE.source), 'the source note names the file and the rule');
  assert.equal(TRUTH.length, 3600);
  for (let i = 0; i < 720; i++) {
    let m = -Infinity;
    for (let j = 5 * i; j < 5 * i + 5; j++) m = Math.max(m, TRUTH[j]);
    assert.equal(FIXTURE.alt[i], m, `bin ${i}`);
  }
  assert.ok(Math.max(...FIXTURE.alt) > 70 && Math.min(...FIXTURE.alt) === 0);
});

// The chart yard counts. Derivation: SPEC-v2 5.4's algorithm run on this fixture's 720 maxima as the draft holds them,
// in a Float32Array. The Python prototype of 5.4 (scratchpad/pano/sensorfirst_simplify.py, `simplify`) on the same
// values after a float32 round trip gives 100 / 49 / 35 vertices at tau 0.25 / 0.5 / 1 with 45 / 12 / 9 shoulders, and
// this port gives the same. On the unrounded doubles the prototype gives 102 / 48 / 35, which is SPEC-v2's table
// (computed on the cache's slightly different c_ref): float32 merges values that differ only in their last double
// digits, so a few steps disappear and a few tolerances land differently. Both are far under 60.
const CHARTYARD_POINTS = { 0.25: 100, 0.5: 49, 1: 35 } as const;
const CHARTYARD_SHOULDERS = { 0.25: 45, 0.5: 12, 1: 9 } as const;

test('chart yard: tau 0.5 gives the recorded count (49), at most 60, p95 under 1.0 against the 0.1-degree truth, never below it', () => {
  const r = conservativePolyline(CHART, 60);
  assert.equal(r.tau, 0.5, 'a cap of 60 passes over tau 0.25 (100 points) and settles on 0.5');
  assert.equal(r.points.length, CHARTYARD_POINTS[0.5]);
  assert.ok(r.points.length <= 60);
  assert.equal(r.shoulders, CHARTYARD_SHOULDERS[0.5]);
  checkShape(r.points, 'chart yard');
  assert.ok(g1Shortfall(CHART, r.points) <= EPS, 'G1');
  assert.ok(g2Violations(CHART, r.points, r.tau) <= r.shoulders, 'G2 holds outside the shoulders');
  const t = againstTruth(r.points);
  console.log(`  chart yard tau 0.5: ${r.points.length} points, ${r.shoulders} shoulders, p95 |line - truth| ${t.p95.toFixed(3)}`);
  assert.ok(t.p95 < 1.0, `p95 ${t.p95}`);
  assert.ok(t.p95 > 0.3, `p95 ${t.p95} is a real measurement, not a flat line`);
  assert.ok(t.under < 1e-5, `the line is below the truth by ${t.under}`);
});

test('chart yard at the default cap of 180 stops at tau 0.25 and at the next rungs as the cap tightens', () => {
  const rungs: [number, 0.25 | 0.5 | 1][] = [[180, 0.25], [99, 0.5], [60, 0.5], [48, 1], [35, 1]];
  for (const [cap, tau] of rungs) {
    const r = conservativePolyline(CHART, cap);
    assert.equal(r.tau, tau, `cap ${cap}`);
    assert.equal(r.points.length, CHARTYARD_POINTS[tau], `cap ${cap}`);
    assert.equal(r.shoulders, CHARTYARD_SHOULDERS[tau], `cap ${cap}`);
    assert.ok(g1Shortfall(CHART, r.points) <= EPS, `cap ${cap}: G1`);
    assert.ok(againstTruth(r.points).under < 1e-5, `cap ${cap}: below the truth`);
  }
  const tight = conservativePolyline(CHART, 34);
  assert.ok(tight.tau > 1 && tight.points.length <= 34);
});

console.log(`simplify.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
