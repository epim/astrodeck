// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T10: the old gradient suite (photosphereGradient.test.ts, issue #102) run against the ported tracer through
// `rasterAdapter.ts`. A steep sky gradient in part of the compass is not an obstruction.
//
// The mosaic rule compares each column's departure (the body over the sky just above it) against the median of the
// same quantity over the whole compass, so anything present in a MINORITY of the compass stands out by
// construction. A wall is such a thing. So is a sky that darkens steeply in the sunward direction at low sun. The
// discriminator #102 names, and these cases pin, is vertical shape: past a wall's edge the rows hold their own level,
// while a gradient has nothing under it and goes on falling.
//
// WHAT MAKES A CASE HERE VALID (it cost the old file a rewrite): azimuth support only RAISES a column, so a shape the
// column rule already publishes is untouched by it and a case built on one cannot fail however the ramp test is
// broken. Every case checks its shape BOTH ways, `inFive` against `alone`, and the `alone` half is what proves the
// mosaic is the thing being graded.
//
// CHANGED, every case:
//   - `traceSkyCoverage(columns).points` is `traceBins(columns).points`; the old bins 10 to 14 of thirty are 180
//     raster columns; altitudes that were compared for equality BETWEEN two traces (`inFive` against `alone`) are
//     still compared for equality, and the two published numbers (30 for a stopped ramp and for the short band)
//     are within half an old degree.
//   - The windows are 36 rows (12 degrees) where the old ones were 12, and the ramp is geometric per 12 DEGREES, so
//     the shapes are the same shapes in degrees.
//   - The MUTATION notes named `keepsRamping` in `applyAzimuthSupport`; it is the method of that name in
//     horizonTrace.ts. They are rewritten from a per-case run of the mutants (the T10 report's ids).
// DROPPED: none.
import assert from 'node:assert/strict';
import { traceBins, type SkyColumn } from './rasterAdapter';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }
const HARD = 0.5;
const near = (actual: number, expected: number, tol: number, what = 'altitude') =>
  assert.ok(Math.abs(actual - expected) <= tol, `${what} ${actual} is not within ${tol} of ${expected}`);

const ROWS = 101, BINS = 30, BASE = 122;
/** Bins 10 to 14 of thirty: five of them, which is what the issue measured. */
const LOCAL = [10, 11, 12, 13, 14];

const column = (lum: (row: number) => number): SkyColumn => ({
  lum: Array.from({ length: ROWS }, (_, row) => lum(row)),
  blue: Array.from({ length: ROWS }, () => 0),
});
const flat = column(() => BASE);

/** Darkening by `pct` per 12 rows from row 60 down, and STOPPING at `knee`: below it the rows hold the level they
 *  reached. `Infinity` never stops, which is the gradient. Row 60 is where the issue's ramp starts. */
const ramp = (pct: number, knee = Infinity) =>
  column(row => (row < 60 ? BASE : BASE * Math.pow(1 - pct / 100, (Math.min(row, knee) - 60) / 12)));

/** The shape in five bins of thirty, everything else flat sky. */
const inFive = (c: SkyColumn) => traceBins(Array.from({ length: BINS }, (_, b) => [LOCAL.includes(b) ? c : flat])).points;
/** The same shape in EVERY bin. Nothing stands out against the mosaic, so azimuth support promotes nothing and what
 *  is left is the column rule alone. */
const alone = (c: SkyColumn) => traceBins(Array.from({ length: BINS }, () => [c])).points[LOCAL[2]].alt;

test('A steep local sky gradient publishes no more than the column rule alone', () => {
  // Mutation: delete `|| this.keepsRamping(a.col, run)` in `settle` (M11): a gradient local to five bins publishes
  //   30 degrees.
  // The three gradients the issue measured. Before the fix all three published 30, the row the ramp starts at,
  // against a column rule that published 0, 13 and 15.
  // Mutant: delete the `keepsRamping` refusal in `settle`. The published bins then read 30, not the column rule's.
  for (const pct of [25, 32, 40]) {
    const c = ramp(pct);
    const base = alone(c), points = inFive(c);
    for (const b of LOCAL) {
      assert.equal(points[b].alt, base,
        `a ${pct} per cent per 12 rows gradient in five bins of thirty published ${points[b].alt} in bin ${b} `
        + `where the column rule alone published ${base}: the mosaic invented an obstruction out of a sky`);
    }
    for (const b of [0, 5, 20, 25]) {
      assert.equal(points[b].alt, 0,
        `flat bin ${b} was blocked, so the case above passes for a reason that has nothing to do with gradients`);
    }
  }
});

test('The same ramp, stopped, is a wall and is still promoted', () => {
  // Mutation: `keepsRamping` returns true (M12), so that a surface is refused as a gradient; one allowance for both
  //   passes (M17); remove the trace-back (M22); follow the sky 3 degrees and not 12 (M57).
  // The minimal pair: this column and the 25 per cent gradient above are the same ramp from the same row at the same
  // rate, differing in one thing only. This one STOPS at row 76 and holds its level. A surface carries none of its
  // drop onward, so the ratio never reaches the share. The soft edge is why the column rule cannot see it: 25 per cent
  // per 12 rows is inside the wide tolerance, so the local model follows the ramp down. That is #74's half, and the
  // only kind of wall the azimuth pass is what publishes, which is what makes this case able to fail at all.
  // Mutant: `return true` at the top of `keepsRamping`. Bin 10 then publishes 0 where the mosaic had published 30.
  const wall = ramp(25, 76);
  assert.equal(alone(wall), 0,
    'the column rule now publishes this wall on its own, so the mosaic is no longer what this case grades');
  const points = inFive(wall);
  for (const b of LOCAL) near(points[b].alt, 30, HARD, `bin ${b} lost a wall whose edge is too soft for one column to see:`);
});

test('A body too short to measure is left to the departure rule', () => {
  // Mutation: a body too short to measure counted as ramping (M13: `return true` for the short body in keepsRamping);
  //   drop the anchor test (M03); drop the persistence test (M21).
  // Unmeasurable is not ramping. A floating run without two full windows of its own below `from` would otherwise be
  // judged on rows that are not its body at all, because past `end` is the sky underneath it. An 8-degree band,
  // which clears the 6-degree floor and is nowhere near two windows of 12, flanked by 14-degree bands that qualify on
  // their own and so anchor it. Without the flanks it is not promoted at all and the case cannot fail.
  // Mutant: `return true` instead of `false` on the two-window guard of `keepsRamping`.
  const band = (rows: number) => column(row => (row >= 61 && row < 61 + rows ? BASE * 0.4 : BASE));
  const mosaic = traceBins(Array.from({ length: BINS }, (_, b) =>
    [b === 12 ? band(8) : b === 11 || b === 13 ? band(14) : flat]));
  const unflanked = traceBins(Array.from({ length: BINS }, (_, b) => [b === 12 ? band(8) : flat]));
  assert.equal(unflanked.points[12].alt, 0,
    'the 8-degree band is published without its flanks, so the mosaic is not what this case grades');
  near(mosaic.points[12].alt, 30, HARD, 'a floating obstruction whose body is too short to measure was refused:');
});

// NOT PINNED, as in the old file: the comment on `keepsRamping` says a body moving the OTHER way from the departure that
// found it is never refused, because the ratio keeps its sign. No case could be built that grades it: a body that turns
// back up has left the run by the time two windows are needed, so it returns unmeasurable first.

console.log(`tracerGradient.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
