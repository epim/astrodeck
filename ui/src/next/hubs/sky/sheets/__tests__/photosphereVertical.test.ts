import assert from "node:assert/strict";
import { bandForAltitude, cameraElevation, cameraPose, projectSweepColumns, robustSpread, traceSkyCoverage, OVERHEAD_BAND, type SkyColumn, type SweepFrame } from "../photosphere";
import { altFromY, SKY_Y } from "../horizonStrip";
import { isObstructed, movePoint } from "../../../../lib/horizonModel";

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }
const frame = (bin: number, band: number, altitude: number, obstacle: (alt: number) => boolean): SweepFrame => ({
  bin, band, altitude, verticalFov: 45,
  column: Array.from({ length: 181 }, (_, i) => obstacle(altitude + (0.5 - i / 180) * 45) ? 25 : 240),
});
const scan = (obstacle: (alt: number) => boolean) => [
  ...[0, 35, 70].flatMap((alt, band) => [0, 1].map(bin => frame(bin, band, alt, bin === 0 ? obstacle : () => false))),
  frame(0, OVERHEAD_BAND, 90, () => false),
];

test("Vertical orientation remains valid at the zenith and distinguishes three rings", () => {
  assert.equal(bandForAltitude(0), 0); assert.equal(bandForAltitude(35), 1);
  assert.equal(bandForAltitude(70), 2); assert.equal(bandForAltitude(90), OVERHEAD_BAND);
  assert.equal(bandForAltitude(-30), null);
  const top = cameraPose({ alpha: 150, beta: 180, gamma: 0, absolute: true });
  assert.ok(top); assert.ok(top.alt > 89.99); assert.ok(Number.isFinite(top.az));
});
test("A nearby house reaching 75 degrees is retained above the low sweep", () => {
  const columns = projectSweepColumns(scan(alt => alt <= 75), 2);
  assert.ok(columns.every(col => col.slice(0, 91).every(Number.isFinite)));
  const trace = traceSkyCoverage(columns);
  assert.equal(trace.uncertainBins.length, 0);
  assert.ok(trace.points[0].alt >= 75 && trace.points[0].alt <= 77);
  assert.equal(trace.points[1].alt, 0);
  assert.ok(isObstructed(trace.points, 70, trace.points[0].az));
});
test("Overhead tilt needs no heading and accepts either side of the beta wrap", () => {
  for (const beta of [180, -180, 176, -176]) {
    assert.equal(bandForAltitude(cameraElevation({ beta, gamma: 0 })!), OVERHEAD_BAND);
  }
  assert.notEqual(bandForAltitude(cameraElevation({ beta: 170, gamma: 0 })!), OVERHEAD_BAND);
  assert.equal(cameraElevation({ beta: null, gamma: 0 }), null);
  assert.equal(cameraElevation({ beta: 180, gamma: NaN }), null);
  assert.equal(cameraElevation({ beta: 0, gamma: 0 }), -90);
});
test("Near-overhead branches survive even when sky is visible beneath them", () => {
  // Re-pinned from an 8 degree band to 15. A boundary is now a departure from
  // the sky that PERSISTS (issue #58), and nothing shorter than the
  // persistence floor with clear sky under it can be told apart from the
  // things a sky carries - the chart yard's dark disc near the zenith, which
  // the old rule published as an 87 degree horizon, is 3 degrees tall. Fifteen
  // is the chart yard's roof: a floating obstruction the simulator grades, and
  // the case this test is about. The cost is stated where the floor is
  // defined: a floating obstruction under 12 degrees tall with clear sky
  // beneath it now reads as open. Ground-connected ones are unaffected at any
  // height, which is the case above this one.
  // Mutation: keep only runs that reach the bottom of the column, and this
  // floating branch reads as open sky.
  const trace = traceSkyCoverage(projectSweepColumns(scan(alt => alt >= 74 && alt <= 88), 2));
  assert.ok(trace.points[0].alt >= 88);
  assert.equal(trace.points[1].alt, 0);
});
// 101 rows from the zenith down, one degree of altitude each, as the panorama
// hands them to the tracer. Grey (blueness 0) unless the case says otherwise.
const sample = (lum: (row: number) => number, blue: (row: number) => number = () => 0): SkyColumn => ({
  lum: Array.from({ length: 101 }, (_, row) => lum(row)),
  blue: Array.from({ length: 101 }, (_, row) => blue(row)),
});
test("A wall brighter than 0.7 of the sky level is still the horizon", () => {
  // Issue #58, the chart yard's wall: luminance 104 against a sky level of
  // 122, so the old rule's threshold of 85 never saw it and published 12
  // degrees of a 25 degree wall as open sky. It is warm where the sky is grey.
  const warm = sample(row => (row < 66 ? 122 : 104), row => (row < 66 ? 0 : -24));
  // The same transition the other way: a sunlit wall BRIGHTER than the sky it
  // stands against, which no "below a fraction of the sky level" rule can see
  // at all.
  const bright = sample(row => (row < 66 ? 122 : 200));
  // Mutation: the old rule - count only a luminance BELOW the sky level, with
  // no colour - and both of these read as open sky.
  const trace = traceSkyCoverage([[warm], [bright]]);
  assert.deepEqual(trace.uncertainBins, []);
  assert.equal(trace.points[0].alt, 25);
  assert.equal(trace.points[1].alt, 25);
});
test("A bright band of sky lying on the top of a wall is not part of the wall", () => {
  // The chart yard's sky carries bright stripes, and three of them sit
  // directly on the wall's top edge in the bin issue #58 is about. They depart
  // from the sky as far as the wall does, in the other direction; without the
  // rule that the boundary row must look like the surface under it, the wall
  // is reported 3 degrees too tall.
  // Mutation: place the boundary at the run's first row and it reports 28.
  const stripe = sample(row => (row < 63 ? 122 : row < 66 ? 170 : 104), row => (row < 66 ? 0 : -24));
  assert.equal(traceSkyCoverage([[stripe]]).points[0].alt, 25);
});
test("A dark disc in the sky with open sky beneath it is not a horizon", () => {
  // Ten rows, so it is larger than any marking the chart yard paints and still
  // not a structure: the sky comes back underneath it, all the way down.
  // Mutation: take the first transition with no persistence test and it
  // reports 61, which is this defect's other half.
  const disc = sample(row => (row >= 30 && row < 40 ? 40 : 122));
  const trace = traceSkyCoverage([[disc]]);
  assert.deepEqual(trace.uncertainBins, []);
  assert.equal(trace.points[0].alt, 0);
});
test("The dim wall the old threshold did see is still seen, with no colour at all", () => {
  // 130 against a sky level of 200 - below the old rule's 0.7, and grey, so
  // the luminance half of the test is the only thing that can find it.
  // Mutation: drop the luminance half and keep chroma only, and it reports 0.
  const wall = sample(row => (row < 70 ? 200 : 130));
  assert.equal(traceSkyCoverage([[wall]]).points[0].alt, 21);
});
// The shape issue #73 was measured in: 30 identical bins of one sky, so the
// pooled seed and the column say the same thing and nothing else is in frame.
const everywhere = (column: SkyColumn) => Array.from({ length: 30 }, () => [column]);
test("An empty sky that brightens toward the horizon is not a horizon", () => {
  // Airlight. The old global level called the bottom of a 100-to-180 sky a 45
  // degree obstruction and marked no bin uncertain (issue #73); the sky model
  // now follows the column, so a slow ramp of any amplitude is sky. The only
  // boundary in these columns is the ground below row 90, which is alt 0.
  // Mutation: measure every row against one pooled median and the 100-to-180
  // sky publishes 39 degrees again. The gentler ramp survives that mutant
  // (the pooled median plus the exposure allowance happens to cover it), which
  // is why both amplitudes are here and not just one. A gradient survives
  // the lagged test with no tolerance at all, because the confirmation step
  // still asks whether the rows below have LEFT that older sky and on a ramp
  // they have not; what that mutation breaks is the soft-edge case below.
  for (const top of [140, 180]) {
    const ramp = sample(row => (row <= 90 ? 100 + ((top - 100) * row) / 90 : 45));
    const trace = traceSkyCoverage(everywhere(ramp));
    assert.deepEqual(trace.uncertainBins, []);
    assert.ok(trace.points.every(p => p.alt === 0), `sky 100 to ${top} published ${trace.points[0].alt}`);
  }
});
test("An ordinary clear sky, brightening and whitening together, is not a horizon", () => {
  // The chroma gradient is the half a colour test is most exposed to: a real
  // sky whitens toward the horizon, which is where the boundary is. Measured
  // at 51 degrees before this fix.
  // Mutation: one pooled blueness median for the whole column and it is 40.
  const clear = sample(row => (row <= 90 ? 100 + (70 * row) / 90 : 45),
    row => (row <= 90 ? 95 - (65 * row) / 90 : -20));
  const trace = traceSkyCoverage(everywhere(clear));
  assert.deepEqual(trace.uncertainBins, []);
  assert.ok(trace.points.every(p => p.alt === 0));
});
test("An auto-exposure seam at a band boundary is not a horizon", () => {
  // The sweep captures three elevation bands and the phone re-exposes between
  // them, so a 30 per cent step across a seam row is ordinary. It is a step
  // like a wall's, and the only thing that separates the two is the size of
  // the exposure allowance, which is why the dim wall at 35 per cent below is
  // a case of its own.
  // Mutation: narrow the allowance to round 0's 0.25 and both seams publish 46.
  for (const factor of [.7, 1.3]) {
    const seam = sample(row => (row < 45 ? 122 : 122 * factor));
    const trace = traceSkyCoverage(everywhere(seam));
    assert.deepEqual(trace.uncertainBins, []);
    assert.ok(trace.points.every(p => p.alt === 0), `a x${factor} seam published ${trace.points[0].alt}`);
  }
});
test("The wall is still the wall under a sky that brightens", () => {
  // The same warm wall, over a sky that is 129 where the wall meets it rather
  // than 122. A model that follows the sky must not follow it past the wall.
  // Mutation: any of the three above.
  const wall = sample(row => (row < 66 ? 100 + (40 * row) / 90 : 104), row => (row < 66 ? 0 : -24));
  assert.equal(traceSkyCoverage([[wall]]).points[0].alt, 25);
});
test("A bright zenith row does not block the sky under it", () => {
  // `projectSweepColumns` writes the overhead sample into row 0 of EVERY bin,
  // so one blown-out overhead frame used to publish 90 degrees in all 30 of
  // them, flagged certain (issue #73). The zenith block is dark-only again.
  // Mutation: make the zenith test sign-agnostic and every bin reads 90.
  const glare = sample(row => (row === 0 ? 255 : 122));
  const trace = traceSkyCoverage(everywhere(glare));
  assert.deepEqual(trace.uncertainBins, []);
  assert.ok(trace.points.every(p => p.alt === 0));
});
test("A glint on top of a roof costs the boundary the glint's own height", () => {
  // The bound on the refinement, in the direction that is not safe. Two rows
  // of 255 sit on a roof at 60 whose true top is row 40 (alt 51); they match
  // neither the sky nor the roof, so they are stepped over and the boundary
  // lands two rows lower. This is the mirror of the bright-stripe case, and it
  // is the price of that one: no column can tell a stripe of sky above a wall
  // from a glint on top of a roof.
  const roof = sample(row => (row < 40 ? 122 : row < 42 ? 255 : 60));
  assert.equal(traceSkyCoverage([[roof]]).points[0].alt, 49);
});
test("robustSpread is a spread one wild sample cannot move", () => {
  assert.equal(robustSpread([]), 0);
  assert.equal(robustSpread([7, 7, 7, 7, 7]), 0);
  const quiet = robustSpread([10, 11, 12, 13, 14]);
  assert.ok(quiet > 0);
  assert.equal(robustSpread([10, 11, 12, 13, 14, 9000]), quiet);
});
// A wall reached by a linear edge of `edge` rows, the body from `endrow` down,
// so the true top of the obstruction is row `endrow - edge`.
const edged = (sky: number, body: number, edge: number, endrow: number) => (row: number) =>
  row < endrow - edge ? sky
    : row >= endrow ? body
    : sky + ((body - sky) * (row - (endrow - edge) + 1)) / (edge + 1);
test("A wall with a soft edge is found at its top, not lost to it", () => {
  // Issue #74. Every row of a gradual edge is within tolerance of a model that
  // follows, so the edge walks the model into the wall and the whole
  // obstruction reads as open sky: a grey wall 35 per cent below its sky - the
  // amplitude the exposure allowance is set to KEEP - published 21 at a 3-row
  // edge (3 degrees low), 21 at 5 rows, and 0 at 6 rows and softer. Six rows
  // is six degrees: a distant tree line, a ridge in haze, a blurred handheld
  // frame, or the frame-fold path's own 24 row averages over a 45 degree
  // field. The reach is about ten rows; beyond that the model has walked
  // before the lagged reference notices, and the residual is recorded at
  // PERSIST_ROWS and in issue #74.
  // Mutation: drop the lagged test (never compare the model with the sky it
  // had a window above) and the 6 and 10-row edges read 0 again.
  for (const [edge, top] of [[3, 24], [5, 26], [6, 27], [10, 31]]) {
    const wall = sample(edged(200, 130, edge, 70));
    const alt = traceSkyCoverage([[wall]]).points[0].alt;
    assert.ok(Math.abs(alt - top) <= 1, `a ${edge}-row edge published ${alt}, not ${top}`);
  }
});
test("A warm wall with a soft edge is found at its top too", () => {
  // The same six-row edge in both channels: luminance 122 to 104, blueness 0
  // to -24. Chroma survives a soft edge further than luminance does, but not
  // indefinitely - at 20 rows this wall was reported as open sky as well.
  // Mutation: drop the lagged test and this reads 25, the bottom of the edge.
  const wall = sample(edged(122, 104, 6, 70), edged(0, -24, 6, 70));
  const alt = traceSkyCoverage([[wall]]).points[0].alt;
  assert.ok(Math.abs(alt - 27) <= 1, `the warm wall published ${alt}, not 27`);
});
test("A bin answers for its whole width, not for the ray through its centre", () => {
  // The product publishes one altitude per 12 degree bin, so the honest number
  // is the bin's worst case. The centre column here is wide open.
  // Mutation: read the bin's centre column alone and it reports 0.
  const open = sample(() => 122);
  const blocked = sample(row => (row < 51 ? 122 : 40));
  const trace = traceSkyCoverage([[open, blocked]]);
  assert.deepEqual(trace.uncertainBins, []);
  assert.equal(trace.points[0].alt, 40);
});
test("Returning to the lower elevation cannot erase a high obstruction", () => {
  const frames = scan(alt => alt <= 82);
  frames.push(frame(0, 0, 0, () => false));
  const trace = traceSkyCoverage(projectSweepColumns(frames, 2));
  assert.ok(trace.points[0].alt >= 82);
});
test("An unscanned upper sky is unknown and cannot be accepted as open sky", () => {
  const trace = traceSkyCoverage(projectSweepColumns([frame(0, 0, 0, () => false)], 2));
  assert.deepEqual(trace.uncertainBins, [0, 1]);
  assert.ok(trace.points.every(p => p.alt === 90));
});
test("A blocked zenith remains blocked even if subsequent high views look clear", () => {
  const frames = scan(() => false);
  frames.splice(frames.length - 1, 1);
  frames.unshift(frame(0, OVERHEAD_BAND, 90, () => true));
  const trace = traceSkyCoverage(projectSweepColumns(frames, 2));
  assert.ok(trace.points.every(p => p.alt === 90));
});
test("Manual review can retain obstructions all the way to 90 degrees", () => {
  assert.equal(altFromY(SKY_Y), 90);
  assert.equal(movePoint([{ az: 180, alt: 90 }], 0, 180, 90)[0].alt, 90);
});
console.log(`photosphereVertical.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
