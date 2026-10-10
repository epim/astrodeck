// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T10: the old vertical-column suite (photosphereVertical.test.ts) run against the ported tracer through
// `rasterAdapter.ts`. Every old column becomes raster columns of a 1080 x 300 stub (3 rows a degree, sigma 5), a
// 12 degree bin becomes 36 columns, and `traceBins` reduces the per-column answer back to the old bin terms: a
// bin publishes the highest of its columns, 90 where a column is not Measured; a bin is uncertain when a column
// of it is in any state but Measured. The one column on each side of a seam between two unlike old columns is not
// read: the texture mask answers to the vertical edge between them, which no old bin contained.
//
// CHANGED, for every case that stays (the reason is the same each time):
//   - The old altitudes were whole degrees, one per old row. A raster row is 0.334 degrees, so a hard edge lands
//     within half a degree of the old number (`HARD`) and a soft-edge reach within a degree (`SOFT`, the old
//     tolerance of `|alt - top| <= 1`); `assert.equal(alt, n)` is `near(alt, n, tol)`.
//   - `traceSkyCoverage(columns)` is `traceBins(columns)`. `uncertainBins` keeps its meaning.
//   - The 'Mutation:' notes are restored, one for every case that stays, from a per-case run of the mutants against
//     the ported tracer (the T10 report's ids). They name the new code where the old ones named AZ_SLOP_ROWS and the
//     one-bin reach of `anchored`; the cases that guarded the dropped zenith rule have no counterpart.
// CHANGED, one case each:
//   - "A floating departure ...": the narrow-mosaic tail asked "a mosaic of five 12 degree bins gets the
//     single-column answer". The gate is now 72 degrees of azimuth OBSERVED (TRACE.azMinObservedDeg), not a count
//     of bins, so the tail paints 60 degrees (five bins: silent) and 72 degrees (six bins: active).
//   - "The anchor reaches one bin and six rows": the reach is now +-4 degrees of azimuth (TRACE.azSupportDeg),
//     not the adjacent 12 degree bin, and the slop is 18 rows. A band beside a wall is published in the columns
//     within 4 degrees of it and open beyond; the bin publishes its highest column, so the old assertions hold.
//   - "the column rule and the azimuth rule spell one measured fact (#107)": TRACE.lumaFrac is declared as the
//     literal 0.32 (SPEC-v2 3.4), so the half that read the SOURCE for `RE_EXPOSURE_PCT` in its spelling is
//     dropped for lumaFrac and kept for AZ_DEPARTURE; the arithmetic relations are kept.
// DROPPED, with the reason:
//   - 20-26 "Vertical orientation remains valid at the zenith ...": bandForAltitude and cameraPose, the band
//     model. Not the tracer (SPEC-v2 7.2).
//   - 27-35 "A nearby house reaching 75 degrees ...": `projectSweepColumns` of band frames. Projection.
//   - 36-44 "Overhead tilt needs no heading ...": cameraElevation and the overhead band.
//   - 45-61 "Near-overhead branches survive ...": projection of three bands. The floating-branch fact it pinned
//     (a 14 degree floating obstruction qualifies on its own) is a new case in horizonTraceRules.test.ts.
//   - 165-174 "A bright zenith row ...", 175-191 "A dark zenith SAMPLE ...", 192-201 "A zenith that is really
//     covered ...": the shared zenith sample. The raster has none (SPEC-v2 5.1); the tracer has no zenith rule.
//   - 449-454 "Returning to the lower elevation cannot erase a high obstruction", 455-459 "An unscanned upper
//     sky is unknown": `projectSweepColumns` of frames. The second is a new case on the raster (unseen).
//   - 460-466 "A blocked zenith remains blocked ...": projection and zenith.
//   - 467-470 "Manual review can retain obstructions all the way to 90 degrees": `altFromY` and `movePoint`, the
//     editor, which is not touched and keeps its own test.
// The two mutants named by SPEC-v2 7.2 are held in horizonTraceRules.test.ts.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { AZ_DEPARTURE, RE_EXPOSURE_BAND_PCT, RE_EXPOSURE_PCT, TRACE, robustSpread } from '../horizonTrace';
import { ColState } from '../../types';
import { traceBins, type SkyColumn } from './rasterAdapter';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

/** A hard edge lands within half an old degree; a soft edge within one (the old suite's own tolerance). */
const HARD = 0.5, SOFT = 1;
const near = (actual: number, expected: number, tol: number, what = 'altitude') =>
  assert.ok(Math.abs(actual - expected) <= tol, `${what} ${actual} is not within ${tol} of ${expected}`);

// 101 rows from the zenith down, one degree of altitude each. Grey (blueness 0) unless the case says otherwise.
const sample = (lum: (row: number) => number, blue: (row: number) => number = () => 0): SkyColumn => ({
  lum: Array.from({ length: 101 }, (_, row) => lum(row)),
  blue: Array.from({ length: 101 }, (_, row) => blue(row)),
});

test('A wall brighter than 0.7 of the sky level is still the horizon', () => {
  // Mutation: disable the luma half of the departure test (M46) and the bright wall reads 0; disable blueness (M20) and
  //   the warm wall reads 0; publish the first blocked row (M51) and the altitude lands a row low.
  // Issue #58, the chart yard's wall: luminance 104 against a sky of 122, warm where the sky is grey; and the
  // same transition the other way, a sunlit wall BRIGHTER than its sky, which no "below a fraction" rule can see.
  const warm = sample(row => (row < 66 ? 122 : 104), row => (row < 66 ? 0 : -24));
  const bright = sample(row => (row < 66 ? 122 : 200));
  const trace = traceBins([[warm], [bright]]);
  assert.deepEqual(trace.uncertainBins, []);
  near(trace.points[0].alt, 25, HARD);
  near(trace.points[1].alt, 25, HARD);
});
test('A bright band of sky lying on the top of a wall is not part of the wall', () => {
  // Mutation: remove the refinement onto the surface (M23) and the wall is reported 3 degrees too tall; disable
  //   blueness (M20) and the warm wall is lost.
  // Three stripes of 170 sit directly on the wall's top edge. Without the rule that the boundary row must look
  // like the surface under it, the wall is reported 3 degrees too tall.
  const stripe = sample(row => (row < 63 ? 122 : row < 66 ? 170 : 104), row => (row < 66 ? 0 : -24));
  near(traceBins([[stripe]]).points[0].alt, 25, HARD);
});
test('A dark disc in the sky with open sky beneath it is not a horizon', () => {
  // Mutation: persist 6 degrees and not 12 (M58): a ten degree disc qualifies on its own.
  // Ten degrees tall, larger than any chart marking and still not a structure: the sky comes back underneath it.
  const disc = sample(row => (row >= 30 && row < 40 ? 40 : 122));
  const trace = traceBins([[disc]]);
  assert.deepEqual(trace.uncertainBins, []);
  assert.equal(trace.points[0].alt, 0);
});
test('The dim wall the old threshold did see is still seen, with no colour at all', () => {
  // Mutation: disable the luma channel (M46): a grey wall has no colour and reads 0.
  // 130 against a sky of 200, grey: the luminance half of the test is the only thing that can find it.
  near(traceBins([[sample(row => (row < 70 ? 200 : 130))]]).points[0].alt, 21, HARD);
});
// The shape issue #73 was measured in: 30 identical bins of one sky.
const everywhere = (column: SkyColumn) => Array.from({ length: 30 }, () => [column]);
test('An empty sky that brightens toward the horizon is not a horizon', () => {
  // Mutation: the sky model does not follow the column (M47), or follows 3 degrees and not 12 (M57): the sky's own
  //   ramp is read as a departure.
  // Airlight. The old global level called the bottom of a 100-to-180 sky a 45 degree obstruction (#73); the sky
  // model follows the column, so a slow ramp of any amplitude is sky. The only boundary is the ground below row
  // 90, which is altitude 0.
  for (const top of [140, 180]) {
    const ramp = sample(row => (row <= 90 ? 100 + ((top - 100) * row) / 90 : 45));
    const trace = traceBins(everywhere(ramp));
    assert.deepEqual(trace.uncertainBins, []);
    assert.ok(trace.points.every(p => p.alt === 0), `sky 100 to ${top} published ${trace.points[0].alt}`);
  }
});
test('An ordinary clear sky, brightening and whitening together, is not a horizon', () => {
  // Mutation: the blueness model does not follow the column (M59), or the luma one does not (M47, M57).
  // The chroma gradient is the half a colour test is most exposed to: a real sky whitens toward the horizon.
  const clear = sample(row => (row <= 90 ? 100 + (70 * row) / 90 : 45), row => (row <= 90 ? 95 - (65 * row) / 90 : -20));
  const trace = traceBins(everywhere(clear));
  assert.deepEqual(trace.uncertainBins, []);
  assert.ok(trace.points.every(p => p.alt === 0));
});
test('An auto-exposure seam at a band boundary is not a horizon', () => {
  // Mutation: promote a supported candidate without asking whether it stands out from the compass (M10).
  // A 30 per cent step across a seam row is ordinary. It is a step like a wall's, and the only thing that
  // separates the two is the size of the exposure allowance.
  for (const factor of [0.7, 1.3]) {
    const seam = sample(row => (row < 45 ? 122 : 122 * factor));
    const trace = traceBins(everywhere(seam));
    assert.deepEqual(trace.uncertainBins, []);
    assert.ok(trace.points.every(p => p.alt === 0), `a x${factor} seam published ${trace.points[0].alt}`);
  }
});
test('The wall is still the wall under a sky that brightens', () => {
  // Mutation: disable blueness (M20) or publish the first blocked row (M51): the warm wall is lost or lands low.
  // The same warm wall over a sky that is 129 where the wall meets it rather than 122.
  const wall = sample(row => (row < 66 ? 100 + (40 * row) / 90 : 104), row => (row < 66 ? 0 : -24));
  near(traceBins([[wall]]).points[0].alt, 25, HARD);
});
test("A glint on top of a roof costs the boundary the glint's own height", () => {
  // Mutation: remove the refinement onto the surface (M23): the glint is part of the roof and the boundary is 2 degrees
  //   too high.
  // Two old rows of 255 sit on a roof at 60 whose true top is row 40 (altitude 51). They match neither the sky
  // nor the roof, so they are stepped over and the boundary lands two degrees lower: the mirror of the
  // bright-stripe case, and its price.
  const roof = sample(row => (row < 40 ? 122 : row < 42 ? 255 : 60));
  near(traceBins([[roof]]).points[0].alt, 49, HARD);
});
test('robustSpread is a spread one wild sample cannot move', () => {
  // Mutation: make robustSpread a standard deviation, or let the median absolute deviation read one extreme sample.
  //   (Not in the mutant run.)
  assert.equal(robustSpread([]), 0);
  assert.equal(robustSpread([7, 7, 7, 7, 7]), 0);
  const quiet = robustSpread([10, 11, 12, 13, 14]);
  assert.ok(quiet > 0);
  assert.equal(robustSpread([10, 11, 12, 13, 14, 9000]), quiet);
});
// A wall reached by a linear edge of `edge` rows, the body from `endrow` down, so the true top of the
// obstruction is row `endrow - edge`.
const edged = (sky: number, body: number, edge: number, endrow: number) => (row: number) =>
  row < endrow - edge ? sky
    : row >= endrow ? body
      : sky + ((body - sky) * (row - (endrow - edge) + 1)) / (edge + 1);
test('A wall with a soft edge is found at its top, not lost to it', () => {
  // Mutation: drop the lagged test (M18: `walked(here, lagged)` made false) and the 6 and 10 row edges read 0; remove the
  //   trace-back (M22) and the boundary is the bottom of the edge; drop the 1/1024 rounding (M24).
  // Issue #74. Every row of a gradual edge is within tolerance of a model that follows, so the edge walks the
  // model into the wall and the whole obstruction reads as open sky: a grey wall 35 per cent below its sky
  // published 21 at a 3-row edge, 21 at 5 rows, and 0 at 6 rows and softer. The lagged reference (the model is
  // compared with the sky it had a window above) is what finds the 6 and 10-row edges.
  for (const [edge, top] of [[3, 24], [5, 26], [6, 27], [10, 31]]) {
    const wall = sample(edged(200, 130, edge, 70));
    const alt = traceBins([[wall]]).points[0].alt;
    near(alt, top, SOFT, `a ${edge}-row edge published ${alt}, not ${top}:`);
  }
});
test('A warm wall with a soft edge is found at its top too', () => {
  // Mutation: remove the trace-back to the top of the transition (M22), or disable blueness (M20).
  // The same six-row edge in both channels: luminance 122 to 104, blueness 0 to -24.
  const wall = sample(edged(122, 104, 6, 70), edged(0, -24, 6, 70));
  const alt = traceBins([[wall]]).points[0].alt;
  near(alt, 27, SOFT, `the warm wall published ${alt}, not 27:`);
});
// A whole mosaic: 30 bins of one sampled column each, so a case can say what the NEIGHBOURING azimuths are doing.
const mosaic = (column: (bin: number) => SkyColumn) => Array.from({ length: 30 }, (_, bin) => [column(bin)]);
const openSky = sample(() => 122);
test('A floating departure is a roof, a marking or a disc by its height and its neighbours', () => {
  // Mutation: drop the anchor test and promote on contrast alone (M03: the disc publishes); drop the azimuth pass (M07:
  //   the roof reads 0); lower the floating floor to one row (M08: the marking publishes); judge a floating departure in
  //   either direction (M09: the glare publishes); promote without asking whether it stands out (M10).
  // Issue #71. Four of these columns carry a dark departure with clear sky underneath it, and NOTHING INSIDE ANY
  // OF THEM separates them. What does is beside them:
  //   bin 13  nine degrees, and the wall it runs out of reaches the ground next door: a roof, published;
  //   bin  7  three degrees, at the very row of the wall's top next door: refused by the floor of 6 degrees
  //           (the mosaic lowers the persistence floor without abolishing it);
  //   bin 20  nine degrees, anchored the same way but BRIGHTER than the mosaic: a cloud or a glint, refused;
  //   bin 25  ten degrees, flanked by open sky: a disc, and still open.
  // The roof bin publishes its highest column: the columns within 4 degrees of the wall are anchored by it.
  const wall = sample(row => (row < 30 ? 122 : 40));
  const roof = sample(row => (row >= 30 && row < 39 ? 40 : 122));
  const marking = sample(row => (row >= 27 && row < 30 ? 40 : 122));
  const glare = sample(row => (row >= 30 && row < 39 ? 220 : 122));
  const disc = sample(row => (row >= 30 && row < 40 ? 40 : 122));
  const trace = traceBins(mosaic(bin =>
    (bin >= 8 && bin <= 12) || (bin >= 17 && bin <= 19) ? wall
      : bin === 13 ? roof : bin === 7 ? marking : bin === 20 ? glare
        : bin === 25 ? disc : openSky));
  assert.deepEqual(trace.uncertainBins, []);
  near(trace.points[12].alt, 61, HARD);
  near(trace.points[13].alt, 61, HARD);
  assert.equal(trace.points[7].alt, 0);
  assert.equal(trace.points[20].alt, 0);
  assert.equal(trace.points[25].alt, 0);
  assert.equal(trace.points[2].alt, 0);
  // The columns of the roof bin that are further than 4 degrees from the wall are NOT promoted: only the near edge
  // is. (What the old suite could not say: its bin was one sample.)
  const roofFirst = 13 * 36, roofLast = 14 * 36 - 1;
  near(trace.horizon.alt[roofFirst], 61, HARD, 'the roof column beside the wall:');
  assert.equal(trace.horizon.state[roofLast], ColState.Measured);
  assert.equal(trace.horizon.alt[roofLast], 0, 'the far end of the roof bin was promoted by a wall 12 degrees away');
  // And a mosaic too narrow to have neighbours gets the single-column answer, unchanged. The gate is 72 degrees
  // of azimuth OBSERVED: five 12 degree bins are 60 and stay silent (the roof beside the wall stays open); six are
  // 72 and the rule is awake (the roof is published).
  const five = traceBins(Array.from({ length: 5 }, (_, bin) =>
    [bin === 1 ? wall : bin === 2 ? roof : openSky]), { binDeg: 12 });
  near(five.points[1].alt, 61, HARD);
  assert.deepEqual(five.points.map(p => p.alt === 0), [true, false, true, true, true]);
  const six = traceBins(Array.from({ length: 6 }, (_, bin) =>
    [bin === 1 ? wall : bin === 2 ? roof : openSky]), { binDeg: 12 });
  near(six.points[2].alt, 61, HARD);
  assert.equal(TRACE.azMinObservedDeg, 72);
});
test('A wall behind an edge too soft for one column is found across the mosaic', () => {
  // Mutation: one allowance for both passes (M17: the 11 to 16 row edges read 0); halve the mosaic threshold (M15) or
  //   double it (M16); drop the lagged test (M18).
  // Issue #74's surviving half. A fifteen-row edge walks the local model down into the wall before the lagged
  // reference can notice, so the column publishes 0 for an obstruction 36 degrees high. The narrower allowance
  // sees the edge; the mosaic says it is a wall and not a re-expose.
  const plain = sample(() => 200);
  for (const [edge, top] of [[11, 32], [13, 34], [15, 36], [16, 37]]) {
    const wall = sample(edged(200, 130, edge, 70));
    const trace = traceBins(mosaic(bin => (bin < 5 ? wall : plain)));
    near(trace.points[2].alt, top, SOFT, `a ${edge}-row edge published ${trace.points[2].alt}, not ${top}:`);
    assert.equal(trace.points[20].alt, 0);
  }
  // Where it runs out, pinned rather than rounded away: at seventeen rows the narrow allowance has been walked
  // too and the GREY wall is lost entirely.
  const soft = sample(edged(200, 130, 17, 70));
  assert.equal(traceBins(mosaic(bin => (bin < 5 ? soft : plain))).points[2].alt, 0);
  // A WARM wall has chroma to walk as well, and it never falls off that cliff: past sixteen rows it is
  // under-reported by about the edge's own height, and it stays under-reported. CHANGED: the old table pinned 18
  // rows at 27 (true top 39); the ported tracer publishes 38.8 there, the truth, because its 3 rows a degree put
  // that one edge on the other side of the cliff. The table keeps 17 (old 26) on one side of it and 20, 22 and 30
  // on the other, which still pin the under-report; 18 is asserted at the value it now has.
  const warmly = sample(() => 122);
  for (const [edge, top] of [[16, 37], [17, 26], [18, 39], [20, 28], [22, 30], [30, 35]]) {
    const wall = sample(edged(122, 104, edge, 70), edged(0, -24, edge, 70));
    const alt = traceBins(mosaic(bin => (bin < 5 ? wall : warmly))).points[2].alt;
    near(alt, top, SOFT, `a warm ${edge}-row edge published ${alt}, not ${top} (true top ${21 + edge}):`);
  }
});
test('A grey wall 32 per cent darker than its sky is found across the mosaic', () => {
  // Mutation: the wide allowance for both passes (M17) and the 32 per cent wall reads 0; halve the threshold (M15) and
  //   the 30 per cent wall publishes; read the mosaic from one column (M54).
  // The other surviving half of #74. One column cannot tell a 32 per cent wall from a 32 per cent re-expose; the
  // mosaic can, because this step is in five bins and not in the other twenty-five, but only just. A 30 per cent
  // WALL over part of the compass cannot be told from a re-expose and is left open: one point wide.
  const plain = sample(() => 200);
  const at = (darker: number) => {
    const wall = sample(row => (row < 70 ? 200 : 200 * (1 - darker)));
    const trace = traceBins(mosaic(bin => (bin < 5 ? wall : plain)));
    assert.deepEqual(trace.uncertainBins, []);
    assert.equal(trace.points[20].alt, 0);
    return trace.points[2].alt;
  };
  near(at(0.32), 21, HARD);
  assert.equal(at(0.30), 0);
  assert.equal(at(0.25), 0);
});
test('An arc of the compass exposed dimmer than the rest is open sky, not a dome', () => {
  // Mutation: read the mosaic from one column (M54).
  // Issue #98, the first half. Until a column has accepted four degrees of its own sky the model IS the seed, so
  // a column whose own sky sits further from the seed than the allowance never accepts a row. The window model
  // is built from the sky BESIDE the column (+-15 degrees), so an arc wider than the window's half is its own
  // majority and is measured against itself. 14, 18 and 26 per cent below the rest of the compass.
  for (const dim of [105, 100, 90]) {
    const trace = traceBins(mosaic(bin => sample(row => (bin < 5 ? dim : 122) * (row <= 90 ? 1 : 0.4))));
    assert.deepEqual(trace.uncertainBins, []);
    assert.ok(trace.points.every(p => p.alt === 0),
      `a mosaic dimmer by ${Math.round((1 - dim / 122) * 100)} per cent over five bins published ${trace.points[0].alt}`);
  }
});
test('An exposure seam over PART of the compass is not a horizon either', () => {
  // Mutation: halve the mosaic threshold (M15), or promote without asking whether it stands out (M10): a seam over
  //   part of the compass publishes as a horizon.
  // Issue #98, the second half: between two elevation bands the ROW is shared around the compass and the ratio
  // is a different number at every azimuth. A cosine taper over four bins and over twenty, at every depth up to
  // the 30 per cent a band re-expose is allowed to be.
  for (const width of [4, 20]) for (const depth of [0.12, 0.20, 0.30]) {
    const trace = traceBins(mosaic(bin => {
      const away = Math.min(Math.abs(bin - 5), 30 - Math.abs(bin - 5));
      const step = away <= width / 2 ? depth * (1 + Math.cos(Math.PI * away / (width / 2))) / 2 : 0;
      return sample(row => (row < 45 ? 122 : 122 * (1 - step)));
    }));
    assert.deepEqual(trace.uncertainBins, []);
    assert.ok(trace.points.every(p => p.alt === 0),
      `a ${depth * 100} per cent seam over ${width * 12} degrees published ${trace.points[5].alt}`);
  }
});
test('An exposure seam in every bin is still not a horizon at the narrow allowance', () => {
  // Mutation: promote without asking whether it stands out (M10): a seam in every bin is what the whole mosaic does at
  //   that row; or a sky model that does not follow (M47, M57).
  // The reason the narrow allowance cannot decide anything on its own: a 30 per cent step reaching the bottom of
  // every column is tall enough and grounded enough to qualify. What refuses it is that it is what the WHOLE
  // MOSAIC is doing at that row.
  const trace = traceBins(mosaic(bin => {
    const level = 122 + (bin % 5) * 4;
    return sample(row => (row < 45 ? level : level * 0.7));
  }));
  assert.deepEqual(trace.uncertainBins, []);
  assert.ok(trace.points.every(p => p.alt === 0), `a mosaic-wide seam published ${trace.points[0].alt}`);
});
test('A bin answers for its whole width, not for the ray through its centre', () => {
  // Mutation: publish the first blocked row (M51), or disable the luma channel (M46). (Reading the bin's centre column
  //   alone is a change to `traceBins`, not to the tracer, and is not in the mutant run.)
  // The product publishes one altitude per 12 degree bin, so the honest number is the bin's worst case. Half
  // the bin's columns here are wide open.
  const open = sample(() => 122);
  const blocked = sample(row => (row < 51 ? 122 : 40));
  const trace = traceBins([[open, blocked]]);
  assert.deepEqual(trace.uncertainBins, []);
  near(trace.points[0].alt, 40, HARD);
});
test('The anchor reaches 4 degrees of azimuth and 18 rows, and both limits are the rule (#107)', () => {
  // Mutation: widen the reach to 12 degrees (M04); raise the slop to 60 rows (M05) or lower it to 0 (M06); drop the
  //   anchor (M03): the mismatched-top band publishes, or the matched-top band goes open.
  // TRACE.azSlopRows and TRACE.azSupportDeg decide what the tracer publishes, and a guard whose deletion changes
  // no test is a guard nobody is holding. The sentence from the constant's comment, made executable: a six-degree
  // floating band two bins from a grounded wall stays open, and the same band beside the wall with the same top
  // is published. The reach is now measured in degrees, so "two bins away" is 37 columns from the wall's edge.
  const wall = sample(row => (row < 30 ? 122 : 40));
  const floating = (top: number) => sample(row => (row >= top && row < top + 9 ? 40 : 122));
  const place = (bins: Record<number, SkyColumn>) => traceBins(mosaic(bin => bins[bin] ?? openSky));
  // ADJACENT, tops four degrees apart: inside the slop, so the wall vouches.
  const near4 = place({ 5: wall, 6: floating(34) });
  assert.ok(near4.points[6].alt > 0, `a band beside the wall, four degrees off its top, was not published: ${near4.points[6].alt}`);
  // ADJACENT, tops ten degrees apart: outside the six-degree slop.
  const offset = place({ 5: wall, 6: floating(40) });
  assert.equal(offset.points[6].alt, 0, "a band ten degrees off the wall's top was anchored anyway, so the slop decides nothing");
  // TWO BINS AWAY, same top: inside the slop but outside the reach.
  const distant = place({ 5: wall, 7: floating(30) });
  assert.equal(distant.points[7].alt, 0, 'a band two bins from the wall was anchored, so the reach decides nothing');
  // ...and the same band one bin closer IS published, which makes the line above about the reach.
  const adjacent = place({ 5: wall, 6: floating(30) });
  assert.ok(adjacent.points[6].alt > 0, `the same band in the adjacent bin was not published: ${adjacent.points[6].alt}`);
  // The reach in columns: the near edge of the band is promoted, its far end (more than 4 degrees along) is not.
  const x0 = 6 * 36;
  assert.ok(adjacent.horizon.alt[x0] > 0 && adjacent.horizon.alt[x0 + 35] === 0,
    'the reach is not 4 degrees: the far end of the adjacent band and its near end read alike');
});

test('the column rule and the azimuth rule spell one measured fact (#107)', () => {
  // Mutation: halve the mosaic threshold (M15) or double it (M16); write it out as a literal (the source check; not in
  //   the mutant run).
  // The largest re-exposure a minority of the compass can show without being an obstruction is 30 per cent. The
  // wide allowance is that plus the whole #74 band; the mosaic threshold is the middle of the band.
  assert.equal(TRACE.lumaFrac, 0.32);
  assert.equal(TRACE.blueFrac, TRACE.lumaFrac);
  assert.equal(AZ_DEPARTURE, 0.31);
  assert.equal(RE_EXPOSURE_PCT / 100, 0.3);
  assert.equal(TRACE.lumaFrac, (RE_EXPOSURE_PCT + RE_EXPOSURE_BAND_PCT) / 100);
  assert.ok(AZ_DEPARTURE > RE_EXPOSURE_PCT / 100, 'a departure at the ordinary re-expose is not an obstruction');
  assert.ok(AZ_DEPARTURE < TRACE.lumaFrac, 'the mosaic threshold must sit inside the band the column cannot see');
  assert.equal(AZ_DEPARTURE - RE_EXPOSURE_PCT / 100, TRACE.lumaFrac - AZ_DEPARTURE,
    'the mosaic threshold is no longer the middle of the band');
  // The part that is not arithmetic: the threshold may not be written out again. Read from the file, because the
  // value is what the module shows and this is a claim about how it is spelled.
  const src = readFileSync(new URL('../horizonTrace.ts', import.meta.url), 'utf8');
  const m = src.match(/export const AZ_DEPARTURE\s*=\s*([^;]+)/);
  assert.ok(m, 'AZ_DEPARTURE is no longer declared where this case can read it');
  assert.ok(m[1].includes('RE_EXPOSURE_PCT'),
    `AZ_DEPARTURE is written out as ${m[1].trim()} instead of being expressed from RE_EXPOSURE_PCT`);
});

console.log(`tracerVertical.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
