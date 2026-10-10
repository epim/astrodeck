// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T10: the old seed suite (photosphereSeed.test.ts, issue #100) run against the ported tracer through
// `rasterAdapter.ts`. A bin that disagrees with the pooled seed is not a blocked dome.
//
// WHAT THE OLD SUITE GUARDED, AND WHAT GUARDS IT NOW. The old tracer seeded every column from ONE pooled median of
// the whole mosaic and had to repair that with `seedFor`: an arc of bins at another exposure re-seeded from its own
// top rows when a neighbour shared its level, and a lone bin was unmeasurable. SPEC-v2 5.1 replaces the pooled seed
// with a window model (the top 6 degrees of coverage, +-15 degrees of azimuth, smooth pixels), so the rule is
// no longer a function: it is the shape of the window. A column is measured against the sky beside it, which is
// its own majority when its sky is at least 15.3 degrees wide (46 of the window's 91 columns) and a minority when
// it is narrower. Twelve degrees is narrower; twenty-four is not. That is the old "two bins are an arc; one bin on
// its own is not measurable", for a reason the spec states in degrees.
//
// RE-RULED IN THE FIX ROUND. The window alone cannot tell an arc at another exposure from an obstruction that fills
// the top of the photo: both are uniform, wider than the window, and their own majority (the reviewer's finding; a
// 20 degree wall past the photo top came out Measured). The tracer now holds a column's window model to the sky the
// whole compass agrees on (the ring sky, horizonTrace.ts header), with the allowance of the column walk (32 per
// cent in luma). So the arcs of #100 split at that allowance:
//   - within it (-26 to +31 per cent here) an arc of any width is sky, as before;
//   - beyond it (-34, +40, +64) an arc is NOT sky any more, whatever its width. It is Tall: published 90 and marked
//     uncertain, which is the half of the old defect that was silence ("published 90 in all five bins with nothing
//     marked uncertain"). The project's own measured fact (#107) is that 30 per cent is the largest re-exposure a
//     minority of the compass shows without being an obstruction, and nothing in a uniform arc separates a larger
//     one from a wall. The blocked direction is the one the spec always chose.
//
// CHANGED, every case:
//   - `traceSkyCoverage(columns)` is `traceBins(columns)`; altitudes are within half an old degree (`HARD`); the
//     quantities the old suite asserted exactly (0, and uncertainBins) are still asserted exactly.
//   - A lone odd bin is no longer "uncertain because `seedFor` returned null"; it is a column whose top rows do not
//     match the sky beside it, which 5.2 calls Tall, published 90. The adapter's bin is uncertain when any column
//     of it is not Measured, which is the same claim ("90, and not a measurement").
//   - 'An arc of sky at a different exposure is sky': the three levels beyond the allowance move to the new case
//     'An exposure beyond the allowance is not sky, however wide', see above.
//   - 'Two bins are an arc; one bin on its own is not measurable': beyond the allowance a pair is no more measurable
//     than a lone bin (both Tall); within it a lone bin is already sky. The pair that vouches for itself is gone.
//   - The MUTATION notes named `seedFor`, which no longer exists. They are rewritten from a per-case run of the mutants
//     against the ported tracer (the T10 report's ids). The mutant that stood in for "return seed unconditionally",
//     the window model pooled from the whole ring (W03), is caught in horizonTrace.test.ts, not here: the ring sky
//     makes a pooled model and a window model agree on every arc this file builds.
// DROPPED: none. The closing note of the old file (the dark-row-0 zenith rule) is dropped with the zenith.
import assert from 'node:assert/strict';
import { ColState } from '../../types';
import { traceBins, type SkyColumn } from './rasterAdapter';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }
const HARD = 0.5;
const near = (actual: number, expected: number, tol: number, what = 'altitude') =>
  assert.ok(Math.abs(actual - expected) <= tol, `${what} ${actual} is not within ${tol} of ${expected}`);

const BINS = 30, POOL = 122;

const column = (lum: (row: number) => number): SkyColumn => ({
  lum: Array.from({ length: 101 }, (_, row) => lum(row)),
  blue: Array.from({ length: 101 }, () => 0),
});
/** Empty sky at `level` down to row 90, ground below it. */
const sky = (level: number) => column(row => level * (row <= 90 ? 1 : 0.4));
/** `width` adjacent bins at `level`, the rest of the compass at the pool. */
const arc = (width: number, level: number) => traceBins(
  Array.from({ length: BINS }, (_, b) => [sky(b < width ? level : POOL)]));

test('An arc of sky at a different exposure within the allowance is sky, not a blocked dome', () => {
  // Mutation: narrow the wide pass to the 12 per cent allowance (W01): the arcs at -14 and +15 per cent read blocked.
  //   (Narrowing the ring test to 0.1 (R10) changes nothing here: the walk's own 32 per cent decides, and the case
  //   guards that it stays so.)
  // The defect, at the ends of the issue's table that the 32 per cent allowance covers. -26 through +31 were correct
  // before the window model and must stay so, for an arc of five bins and for a lone bin.
  for (const [level, note] of [[90, '-26 per cent'], [105, '-14'], [140, '+15'], [160, '+31']] as const) {
    for (const width of [1, 2, 5]) {
      const trace = arc(width, level);
      for (let b = 0; b < width; b++) {
        assert.equal(trace.points[b].alt, 0,
          `an arc of ${width} bins of empty sky at ${note} published ${trace.points[b].alt} in bin ${b}`);
      }
      assert.deepEqual(trace.uncertainBins, [], `the arc of ${width} at ${note} left bins unmeasured`);
      for (const b of [10, 20]) {
        assert.equal(trace.points[b].alt, 0, `bin ${b} is at the pooled level and must be unaffected`);
      }
    }
  }
});

test('An exposure beyond the allowance is not sky, however wide: Tall, published 90 and marked', () => {
  // Mutation: never hold a column to the ring sky (R01); blind the ring test to luma (R05); take the ring from the
  //   minimum of the models (R06); widen its allowance to 0.6 (R07): the arcs at -34, +40 and +64 per cent read open.
  // -34, +40 and +64 per cent, the other end of the issue's table. A uniform arc that far from the rest of the compass
  // cannot be told from an obstruction that fills the top of the photo (the ring sky, horizonTrace.ts), and the
  // tracer takes the blocked direction: 90 in every bin of the arc and each one marked, where the defect of #100
  // was the same 90 with nothing marked. Alone, in a pair and across five bins.
  for (const level of [80, 171, 200]) {
    for (const width of [1, 2, 5]) {
      const trace = arc(width, level);
      const bins = Array.from({ length: width }, (_, b) => b);
      assert.deepEqual(trace.uncertainBins, bins, `an arc of ${width} bins at ${level} against a pool of ${POOL} was not marked`);
      for (const b of bins) assert.equal(trace.points[b].alt, 90, `bin ${b} of the arc at ${level} published ${trace.points[b].alt}`);
      for (const b of [10, 20]) assert.equal(trace.points[b].alt, 0, `bin ${b} is at the pooled level and must be unaffected`);
      for (const b of bins) {
        const x = (b * 36) + 18;   // the middle of a bin; the seam columns of the arc are not read
        assert.equal(trace.horizon.state[x], ColState.Tall, `the middle of bin ${b} at ${level}`);
      }
    }
  }
});

test('A bright bin cannot vouch for itself by measuring the gap its own way', () => {
  // Mutation: drop the noise floor (M42), disable the luma channel (M46), or read the window from the top row alone
  //   (M27). The asymmetry the old fix got wrong has no counterpart in the window model, so this pins the outcome.
  // The asymmetry the first version of the old fix got wrong: `|a - b| <= tolerance * b` answers differently
  // depending on which side is `b`, so a bin at 171 agreed with a neighbour at 122 that did not agree with it. The
  // window model has no pairwise agreement to get wrong; this pins the outcome. A lone bin 40 per cent above the
  // pool is not a measurement.
  const bright = arc(1, 171);
  assert.deepEqual(bright.uncertainBins, [0],
    `a lone bin 40 per cent above the pool vouched for itself and published ${bright.points[0].alt}`);
});

test('A wall is still a wall', () => {
  // Mutation: publish the first blocked row (M51), disable the luma channel (M46), or drop the noise floor (M42).
  // The guard against the fix. These five bins are at the pooled level in their top rows, so the window model is
  // the sky and the walk finds the wall at row 40 (altitude 51).
  const wall = traceBins(Array.from({ length: BINS }, (_, b) =>
    [b < 5 ? column(row => (row >= 40 ? POOL * 0.4 : POOL)) : sky(POOL)]));
  for (const b of [0, 1, 2, 3, 4]) near(wall.points[b].alt, 51, HARD, `bin ${b} lost a wall that starts at row 40:`);
  for (const b of [10, 20]) assert.equal(wall.points[b].alt, 0, `bin ${b}`);
});

console.log(`tracerSeed.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
