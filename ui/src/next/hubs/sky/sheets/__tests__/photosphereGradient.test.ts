// A steep sky gradient in part of the compass is not an obstruction (#102).
//
// The mosaic rule added for #71 and #74 compares each column's departure - the
// body over the sky just above it - against the median of the same quantity
// over the whole compass, so anything present in a MINORITY of bins stands out
// by construction. A wall is such a thing. So is a sky that darkens steeply in
// the sunward direction at low sun, and one column cannot tell them apart.
//
// The discriminator #102 names, and the one these cases pin, is vertical shape
// rather than extent in azimuth: past a wall's edge the rows hold their own
// level, while a gradient has nothing under it and simply goes on falling.
//
// The three gradients and the numbers they publish come from the issue. The
// ramp is geometric because "per cent per 12 rows" is a ratio, and a LINEAR
// fall of 40 per cent per 12 rows crosses zero four windows down; the departure
// the tracer measures is a ratio too, so this is the shape that holds the rate
// the issue names all the way to the bottom of the column.
//
// WHAT MAKES A CASE HERE VALID, and it cost a rewrite to learn: the azimuth
// pass can only RAISE a bin, so a shape the column rule already publishes is
// untouched by it and a case built on one cannot fail however `keepsRamping` is
// broken. A first draft graded a hard 40-per-cent wall, a 14-row floating band
// and a brightening surface that way; all three survived `return true` at the
// top of `keepsRamping` - the mutation that deletes the whole rule. Every case
// below therefore checks its shape BOTH ways, `mosaic` against `alone`, and the
// `alone` half is not decoration: it is what proves the mosaic is the thing
// being graded.
import assert from "node:assert/strict";
import { traceSkyCoverage, type SkyColumn } from "../photosphere";

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const ROWS = 101, BINS = 30, BASE = 122;
/** Bins 10 to 14 of thirty - five of them, which is what the issue measured. */
const LOCAL = [10, 11, 12, 13, 14];

const column = (lum: (row: number) => number): SkyColumn => ({
  lum: Array.from({ length: ROWS }, (_, row) => lum(row)),
  blue: Array.from({ length: ROWS }, () => 0),
});
const flat = column(() => BASE);

/** Darkening by `pct` per 12 rows from row 60 down, and STOPPING at `knee`:
 *  below it the rows hold the level they reached. `Infinity` never stops, which
 *  is the gradient. Row 60 is where the issue's ramp starts and the boundary
 *  the tracer fabricates is one row into it, which is altitude 30. */
const ramp = (pct: number, knee = Infinity) =>
  column(row => (row < 60 ? BASE
                          : BASE * Math.pow(1 - pct / 100, (Math.min(row, knee) - 60) / 12)));

/** The shape in five bins of thirty, everything else flat sky. */
const inFive = (c: SkyColumn) => traceSkyCoverage(
  Array.from({ length: BINS }, (_, b) => [LOCAL.includes(b) ? c : flat])).points;
/** The same shape in EVERY bin. Nothing stands out against the mosaic, so
 *  `applyAzimuthSupport` promotes nothing and what is left is the column rule
 *  alone - which is what the issue means by the pre-#71 tracer's answer. */
const alone = (c: SkyColumn) => traceSkyCoverage(
  Array.from({ length: BINS }, () => [c])).points[LOCAL[2]].alt;

test("A steep local sky gradient publishes no more than the column rule alone", () => {
  // The defect, at the three gradients the issue measured. Before the fix all
  // three published 30 - the row the ramp starts at - against a column rule
  // that published 0, 13 and 15.
  //
  // MUTATION: delete the `if (keepsRamping(c.column, run)) continue;` line in
  // `applyAzimuthSupport`. Observed: "a 25 per cent per 12 rows gradient in
  // five bins of thirty published 30 in bin 10 where the column rule alone
  // published 0".
  for (const pct of [25, 32, 40]) {
    const c = ramp(pct);
    const base = alone(c), points = inFive(c);
    for (const b of LOCAL) {
      assert.equal(points[b].alt, base,
        `a ${pct} per cent per 12 rows gradient in five bins of thirty published `
        + `${points[b].alt} in bin ${b} where the column rule alone published `
        + `${base}: the mosaic invented an obstruction out of a sky`);
    }
    for (const b of [0, 5, 20, 25]) {
      assert.equal(points[b].alt, 0,
        `flat bin ${b} was blocked, so the case above passes for a reason that `
        + 'has nothing to do with gradients');
    }
  }
});

test("The same ramp, stopped, is a wall and is still promoted", () => {
  // The guard against the fix, and the minimal pair: this column and the 25 per
  // cent gradient above are the same ramp from the same row at the same rate,
  // differing in one thing only - this one STOPS at row 76 and holds its level.
  // A surface carries none of its drop onward, so `carry` is zero and the ratio
  // never reaches the share.
  //
  // The soft edge is why the column rule cannot see it: 25 per cent per 12 rows
  // is inside the wide tolerance, so the local model follows the ramp down and
  // never notices the sky leaving. That is issue #74's half, and it is the only
  // kind of wall the azimuth pass is what publishes - which is what makes this
  // case able to fail at all.
  //
  // MUTATION: `return true` at the top of `keepsRamping`. Observed: bin 10
  // published 0 where the mosaic had published 30.
  const wall = ramp(25, 76);
  assert.equal(alone(wall), 0,
    'the column rule now publishes this wall on its own, so the mosaic is no '
    + 'longer what this case grades and it can no longer fail');
  const points = inFive(wall);
  for (const b of LOCAL) {
    assert.equal(points[b].alt, 30,
      `bin ${b} lost a wall whose edge is too soft for one column to see`);
  }
});

test("A body too short to measure is left to the departure rule", () => {
  // Unmeasurable is not ramping. A floating run without two full windows of its
  // own below `from` would otherwise be judged on rows that are not its body at
  // all, because past `end` is the sky underneath it.
  //
  // An 8-row band, which clears AZ_PERSIST_ROWS and is nowhere near two windows
  // of 12, flanked by 14-row bands that qualify on their own and so anchor it.
  // Without the flanks it is not promoted at all and the case cannot fail.
  //
  // MUTATION: `return true` instead of `false` on the
  // `run.from + 2 * SKY_WINDOW - 1 > run.end` guard. Observed: bin 12 published
  // 0 where the mosaic had published 30.
  const band = (rows: number) =>
    column(row => (row >= 61 && row < 61 + rows ? BASE * 0.4 : BASE));
  const mosaic = traceSkyCoverage(Array.from({ length: BINS }, (_, b) =>
    [b === 12 ? band(8) : b === 11 || b === 13 ? band(14) : flat]));
  const unflanked = traceSkyCoverage(Array.from({ length: BINS }, (_, b) =>
    [b === 12 ? band(8) : flat]));
  assert.equal(unflanked.points[12].alt, 0,
    'the 8-row band is published without its flanks, so the mosaic is not what '
    + 'this case grades');
  assert.equal(mosaic.points[12].alt, 30,
    'a floating obstruction whose body is too short to measure was refused');
});

// NOT PINNED, and said here rather than left to look like an oversight. The
// comment on `keepsRamping` says a body moving the OTHER way from the departure
// that found it is never refused, because the ratio keeps its sign. I could not
// build a case that grades it. Instrumenting the function and running every
// shape in this file plus a sweep of brightening surfaces - 45 and 55 per cent
// falls, knees at rows 70 and 74, rises from 40 to 220 per cent per 12 rows -
// logged not one candidate reaching the test with a negative carry: a body that
// turns back up has left the run by then, so `end` arrives before the two
// windows the test needs and it returns unmeasurable first.
//
// The signed form stays, because it can only refuse FEWER candidates than the
// magnitude would and the wrong half to refuse is the one that costs sky. But
// it is an argument, not a measurement, and `Math.abs` around that ratio passes
// every case here.

console.log(`photosphereGradient.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
