// A bin that disagrees with the pooled seed is not a blocked dome (#100).
//
// Every column is seeded from the pooled median of the top 26 rows of the WHOLE
// mosaic and keeps using it until it has accepted SKY_WINDOW_MIN rows of its
// own sky. A bin whose own sky sits further from that level than
// EXPOSURE_TOLERANCE therefore never accepts a row: the column reads as one
// departure from the top of the frame to the bottom, which reaches the bottom,
// which qualifies, and the bin publishes altitude 90 - out of empty sky, with
// `uncertainBins` empty. A fabricated measurement published as certain.
//
// The reference is wrong, not the tolerance, and the neighbours are what
// separate the two cases: an exposure disagreement spans the frames that were
// auto-exposed together, so it is an ARC of adjacent bins at one level, while a
// roof over a bin's top rows is local in azimuth.
//
// The table below is the issue's own, reproduced before anything was changed.
// The three rows that published 90 are the three this file pins at 0.
import assert from "node:assert/strict";
import { traceSkyCoverage, type SkyColumn } from "../photosphere";

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const BINS = 30, POOL = 122;

const column = (lum: (row: number) => number): SkyColumn => ({
  lum: Array.from({ length: 101 }, (_, row) => lum(row)),
  blue: Array.from({ length: 101 }, () => 0),
});
/** Empty sky at `level` down to row 90, ground below it. */
const sky = (level: number) => column(row => level * (row <= 90 ? 1 : 0.4));
/** `width` adjacent bins at `level`, the rest of the compass at the pool. */
const arc = (width: number, level: number) => traceSkyCoverage(
  Array.from({ length: BINS }, (_, b) => [sky(b < width ? level : POOL)]));

test("An arc of sky at a different exposure is sky, not a blocked dome", () => {
  // The defect, at both ends of the issue's table. -34 and +40 per cent
  // published 90 in all five bins with nothing marked uncertain; -26 through
  // +31 were already correct and must stay so, which is what makes this a
  // change of reference and not of tolerance.
  //
  // MUTATION: `return seed` unconditionally from `seedFor`. Observed: the -34
  // and +40 rows publish 90 again and this fails on the first of them.
  for (const [level, note] of [[80, "-34 per cent"], [90, "-26"], [105, "-14"],
                               [140, "+15"], [160, "+31"], [171, "+40"],
                               [200, "+64"]] as const) {
    const trace = arc(5, level);
    for (const b of [0, 1, 2, 3, 4]) {
      assert.equal(trace.points[b].alt, 0,
        `an arc of five bins of empty sky at ${note} published `
        + `${trace.points[b].alt} in bin ${b}`);
    }
    assert.deepEqual(trace.uncertainBins, [],
      `the arc at ${note} left bins unmeasured`);
    for (const b of [10, 20]) {
      assert.equal(trace.points[b].alt, 0,
        `bin ${b} is at the pooled level and must be unaffected`);
    }
  }
});

test("Two bins are an arc; one bin on its own is not measurable", () => {
  // The other half, and the reason this is not simply "seed every column from
  // itself". A lone bin that matches neither the pool nor its neighbours could
  // be an odd exposure or a roof over the zenith, and nothing here can tell
  // them apart - so it is UNCERTAIN, which is what 90-certain should always
  // have been. Two adjacent bins already vouch for each other.
  //
  // MUTATION: `return seed` instead of `return null` in `seedFor`. Observed:
  // the lone bin publishes 90 and is NOT marked uncertain, and this fails on
  // the uncertainBins assertion.
  for (const level of [80, 171]) {
    const alone = arc(1, level);
    assert.deepEqual(alone.uncertainBins, [0],
      `a lone bin at ${level} against a pool of ${POOL} was not marked `
      + `unmeasurable: it published ${alone.points[0].alt}`);
    const pair = arc(2, level);
    assert.deepEqual(pair.uncertainBins, [],
      `two adjacent bins at ${level} vouch for each other and must be measured`);
    assert.equal(pair.points[0].alt, 0);
    assert.equal(pair.points[1].alt, 0);
  }
});

test("A bright bin cannot vouch for itself by measuring the gap its own way", () => {
  // The asymmetry that the first version of this got wrong, kept because it is
  // a live trap. `|a - b| <= tolerance * b` gives a different answer depending
  // on which side is `b`: at 171 against a neighbour's 122 the gap is 49 and
  // `0.32 * 171` is 55, so the bright bin "agrees" with a neighbour that does
  // not agree with it. Measured on that arithmetic, a lone bin at +40 per cent
  // seeded itself and published 0 with nothing vouching for it.
  //
  // The dimmer of the two is the base, which makes the relation symmetric.
  //
  // MUTATION: base `shareLevel` on `Math.max` instead of `Math.min`. Observed:
  // the lone +40 per cent bin publishes 0 with uncertainBins empty, and this
  // fails.
  const bright = arc(1, 171);
  assert.deepEqual(bright.uncertainBins, [0],
    `a lone bin 40 per cent above the pool vouched for itself and published `
    + `${bright.points[0].alt}`);
});

test("A wall is still a wall", () => {
  // The guard against the fix. Nothing above may cost the tracer an obstruction
  // it finds today: these five bins are at the pooled level in their top rows,
  // so `seedFor` returns the pooled seed and the walk is byte-identical to
  // before.
  //
  // MUTATION: read the bin's own level from the WHOLE column instead of its top
  // 26 rows - the plausible wrong reading of "seed each column from its own
  // rows". Observed: "bin 0 lost a wall that starts at row 40", because the
  // seed lands between the sky and the wall and the wall becomes that column's
  // sky.
  //
  // NOT graded by "always seed from the bin's own level": measured, that leaves
  // this case at 51. These bins' top rows already ARE the pooled level, so
  // seeding from them changes nothing here. Written down because it was the
  // mutation this case originally claimed, and it does not grade it.
  const wall = traceSkyCoverage(Array.from({ length: BINS }, (_, b) =>
    [b < 5 ? column(row => (row >= 40 ? POOL * 0.4 : POOL))
           : sky(POOL)]));
  for (const b of [0, 1, 2, 3, 4]) {
    assert.equal(wall.points[b].alt, 51,
      `bin ${b} lost a wall that starts at row 40`);
  }
  for (const b of [10, 20]) assert.equal(wall.points[b].alt, 0, `bin ${b}`);
});

// NOT CHANGED, and recorded here because the issue asks about it. Its last
// paragraph says the zenith rule - `start === 0 && lum[0] < here.lum -
// here.lumTol` - is the same root cause and wants the same treatment. Half of
// that is now true for free: the rule reads `here`, which is the bin's own seed
// wherever a bin re-seeds, so on the arc above it no longer fires.
//
// What remains is a different question. A column whose top 26 rows match the
// pool but whose row 0 alone is 34 per cent below it still publishes 90:
// measured, one bin or three, `uncertainBins` empty. That is not a reference
// error - the reference is right there - it is the rule deciding a whole dome
// on one sample. Changing it moves a conservative rule in the direction of
// publishing open sky over an obstruction, which is the dangerous direction and
// not a call to make from a synthetic column. It is written on #100 instead.

console.log(`photosphereSeed.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
