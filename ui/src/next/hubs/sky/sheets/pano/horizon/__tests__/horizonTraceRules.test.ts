// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T10: the column tracer's rules on the raster (SPEC-v2 5.1, 5.2): the placement-sigma rule, the focal term, contrast,
// the texture channel, azimuth support that may raise and never lower, and the ring sky. The rest of the cases the
// raster adds are in horizonTrace.test.ts; the two were one file until it outgrew the runner's 60 s per-file limit on
// a loaded CI runner.
//
// Mutants this file must catch (SPEC-v2 7.2, tracer row):
//   - azimuth support allowed to lower a column (the `candidate <= alt` guard of `settle` removed):
//     'azimuth support raises a column to a floating band and the surface under it cannot lower it back';
//   - the sigma rule ignored (`sigma > TRACE.measuredMaxSigmaDeg` dropped from the Measured test):
//     'the placement sigma at the boundary pixel decides Measured: 255 is Low, 5 (0.1 degrees) is Measured'.
// The fix round (a reviewer found obstructions wider than the window coming out Measured) adds the ring sky, and with
// it these, each named in the 'Mutation:' line of the case that catches it:
//   - the ring sky never substituted; active below 72 degrees observed; without a majority requirement; from the
//     minimum instead of the median; its test blind to luma, or to blueness; its allowances widened or narrowed;
//   - lowLight from the minimum or the maximum of the window models instead of the median;
//   - the sigma of an open column read away from altitude 0;
//   - an unmeasurable contrast read as 0 instead of unknown.
// The fix round B (ruling S15, T10 re-review) adds one more here:
//   - the dark test on the model the column is walked against only (the substituted ring sky): 'UnknownDark reads the
//     column's own window sky as well as the ring sky: a dark sector in a lit ring is too dark, not Tall'.
// Every case below opens with a 'Mutation:' note naming the mutants it was seen to turn red, as the old suites did
// (the ids are those of the T10 report's mutant run, which lists the edit of each).
import assert from 'node:assert/strict';
import { TRACE, boundarySigmaDeg } from '../horizonTrace';
import { ColState, PANO_W } from '../../types';
import { rowAltitude } from './rasterAdapter';
import { SKY, WALL, col, extract, gauss, inSector, near, scene, unit, wallScene, type SceneFn } from './traceScenes';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

// ---- The placement-sigma rule (5.2) ------------------------------------------------------------------

test('the placement sigma at the boundary pixel decides Measured: 255 is Low, 5 (0.1 degrees) is Measured', () => {
  // Mutation: the sigma rule ignored (M02: drop `|| sigma > TRACE.measuredMaxSigmaDeg` from the Low test); the sigma
  //   read at the wrong pixel (M37); 255 read as known (M60); 1.0 degree itself rejected (M61); the sigma of an open
  //   column read 10 degrees above the horizon (X8b).
  const sceneFn = wallScene(100, 108, 30);
  const x = col(104);
  const known = extract(scene(sceneFn, { sigma: 5 }));
  assert.equal(known.state[x], ColState.Measured);
  near(known.sigmaDeg[x], 0.1, 1e-6);
  const unknown = extract(scene(sceneFn, { sigma: 255 }));
  assert.equal(unknown.state[x], ColState.Low);
  assert.equal(unknown.sigmaDeg[x], Infinity, '255 counts as infinite');
  near(unknown.alt[x], 30, 0.5, 'Low still carries the traced altitude, the suggestion:');
  // The edge of the rule is 1.0 degree: 50 (1.00) passes, 51 (1.02) does not.
  assert.equal(extract(scene(sceneFn, { sigma: 50 })).state[x], ColState.Measured);
  assert.equal(extract(scene(sceneFn, { sigma: 51 })).state[x], ColState.Low);
  // It is the sigma AT THE BOUNDARY that counts, not the best or the worst of the column: a sigma of 255 everywhere
  // except the boundary rows is Measured, and the reverse is Low. The boundary row is at altitude 30 here.
  const aroundBoundary = (y: number) => Math.abs(rowAltitude(y) - 30) < 0.9;
  assert.equal(extract(scene(sceneFn, { sigma: (_x, y) => (aroundBoundary(y) ? 5 : 255) })).state[x], ColState.Measured);
  assert.equal(extract(scene(sceneFn, { sigma: (_x, y) => (aroundBoundary(y) ? 255 : 5) })).state[x], ColState.Low);
  // An open column answers for the horizon: its boundary pixel is the one at altitude 0.
  const open = col(250);
  assert.equal(extract(scene(sceneFn, { sigma: 255 })).state[open], ColState.Low);
  assert.equal(extract(scene(sceneFn, { sigma: 5 })).state[open], ColState.Measured);
  // ...and it is the sigma AT altitude 0 that counts there too, not a row a few degrees above it: 255 only around
  // the horizon is Low, and 255 everywhere but around the horizon is Measured.
  const aroundHorizon = (y: number) => Math.abs(rowAltitude(y)) < 0.9;
  assert.equal(extract(scene(sceneFn, { sigma: (_x, y) => (aroundHorizon(y) ? 255 : 5) })).state[open], ColState.Low);
  assert.equal(extract(scene(sceneFn, { sigma: (_x, y) => (aroundHorizon(y) ? 5 : 255) })).state[open], ColState.Measured);
});

test('boundarySigmaDeg: the examples of 5.2, and the focal term grows off axis', () => {
  // Mutation: drop the focal term of boundarySigmaDeg (M38).
  // The spec's worked figures (5.2 and its appendix) are hand-rounded: the formula gives 0.286 and 2.825 where they
  // print 0.28 and 2.81. What they conclude (0.30, Low, 0.78) holds, and the figures are asserted to the formula.
  near(boundarySigmaDeg(0.1, 2, 15), 0.30, 0.005, 'locked at 2 per cent, u = 15, placement 0.1:');
  near(boundarySigmaDeg(0, 2, 15), 0.2861, 0.0001, 'its focal term (the spec rounds it to 0.28):');
  near(boundarySigmaDeg(0, 20, 15), 2.8246, 0.0001, 'prior at 20 per cent, u = 15 (the spec prints 2.81):');
  assert.ok(boundarySigmaDeg(0, 20, 15) > TRACE.measuredMaxSigmaDeg, 'the prior at u = 15 is Low');
  near(boundarySigmaDeg(0.5, 20, 3), 0.78, 0.005, 'prior at 20 per cent, u = 3, placement 0.5:');
  near(boundarySigmaDeg(0, 20, 3), 0.5986, 0.0001);
  assert.equal(boundarySigmaDeg(0.1, 0, 40), 0.1, 'no focal uncertainty adds nothing');
  assert.equal(boundarySigmaDeg(0.1, 20, 0), 0.1, 'on the axis a focal error moves nothing');
  assert.equal(boundarySigmaDeg(Infinity, 2, 15), Infinity);
  assert.ok(boundarySigmaDeg(0.1, 5, 30) > boundarySigmaDeg(0.1, 5, 10), 'the further off axis, the worse');
  assert.equal(boundarySigmaDeg(0.1, 20, -15), boundarySigmaDeg(0.1, 20, 15), 'below the axis is the same');
  assert.ok(Number.isFinite(boundarySigmaDeg(0.1, 20, 90)), 'a boundary at the pole does not blow up');
});

test('the focal term reaches the state: a 2 per cent focal at 15 degrees off axis is Measured, a 20 per cent prior is Low', () => {
  // Mutation: drop the focal term (M38) or ignore the sigma rule (M02): the 20 per cent prior reads Measured.
  const pano = scene(wallScene(100, 108, 30));
  const x = col(104);
  const locked = extract(pano, { focalSdPct: 2, axisAltDeg: 15 });   // u = |A - 15|, about 15
  assert.equal(locked.state[x], ColState.Measured);
  near(locked.sigmaDeg[x], boundarySigmaDeg(0.1, 2, locked.alt[x] - 15), 1e-4, 'the sigma of a column is the rule applied at its A:');
  near(locked.sigmaDeg[x], 0.30, 0.02);
  const prior = extract(pano, { focalSdPct: 20, axisAltDeg: 15 });
  assert.equal(prior.state[x], ColState.Low);
  near(prior.sigmaDeg[x], boundarySigmaDeg(0.1, 20, prior.alt[x] - 15), 1e-4);
  near(prior.sigmaDeg[x], 2.82, 0.05);
  // Close to the axis the same prior is good enough: u = 3, placement 0.1: 0.61.
  const onAxis = extract(pano, { focalSdPct: 20, axisAltDeg: 27 });
  assert.equal(onAxis.state[x], ColState.Measured);
  near(onAxis.sigmaDeg[x], boundarySigmaDeg(0.1, 20, onAxis.alt[x] - 27), 1e-4);
  assert.ok(onAxis.sigmaDeg[x] < TRACE.measuredMaxSigmaDeg);
});

// ---- Contrast ---------------------------------------------------------------------------------------

test('contrast is measured in the noise of the raster itself: 6 sigma is Measured, 3 to 6 is Low, under 3 is UnknownContrast', () => {
  // Mutation: never UnknownContrast (M35), Measured without 6 sigma (M36), or a noise estimate 20 per cent off (N01, N03).
  // The raster's noise is a median over the whole raster, so it is set by the noisy majority: 90 per cent of the
  // compass is sky with noise of 12, which the estimator reads as 10.4. The rest is clean sky of 85, where a wall
  // 29, 45 or 68 darker departs (the allowance is 32 per cent of 85 = 27) and is 2.8, 4.3 or 6.5 noise sigmas deep.
  const build = (depth: number) => scene((az, alt, x, y) => {
    const base = inSector(az, 335, 345) && alt < 30 ? 85 - depth : 85;
    return { lum: Math.max(0, Math.min(255, base + (az < 324 ? 12 * gauss(x, y, 33) : 0))) };
  });
  const x = col(340);
  const readings = [29, 45, 68].map(depth => {
    const h = extract(build(depth));
    return { depth, state: h.state[x], contrast: h.contrastSigma[x] };
  });
  assert.deepEqual(readings.map(r => r.state), [ColState.UnknownContrast, ColState.Low, ColState.Measured],
    `contrasts ${readings.map(r => r.contrast.toFixed(2))}`);
  const [a, b, c] = readings.map(r => r.contrast);
  assert.ok(a < TRACE.lowContrast && b >= TRACE.lowContrast && b < TRACE.measuredContrast && c >= TRACE.measuredContrast);
  near(c / a, 68 / 29, 0.15, 'contrast scales with the depth of the wall:');
  near(a * 10.4, 29, 2.5, 'the raster noise it was measured against:');
});

test('blueness alone finds a wall of exactly the sky brightness, and carries its contrast', () => {
  // Mutation: disable the blueness channel of the walk (M20), or leave blueness out of the contrast (M50).
  // Issue #58 on the raster: a warm wall of exactly the sky's luma. Luma sees nothing; blueness departs by 40 and
  // that difference, in blueness noise, is the boundary's contrast.
  const h = extract(scene((az, alt) => ({ lum: SKY, blue: inSector(az, 100, 108) && alt < 30 ? -40 : 0 })));
  const x = col(104);
  assert.equal(h.state[x], ColState.Measured);
  near(h.alt[x], 30, 0.5);
  assert.ok(h.contrastSigma[x] >= 30, `contrast ${h.contrastSigma[x]}: the blueness difference of 40 in sigma 1`);
});

test('the texture channel finds foliage that matches the sky in brightness and colour', () => {
  // Mutation: disable the texture channel (M19), do not smooth it over 5 rows (M43), leave texture out of the contrast
  //   (M49), or drop the persistence test (M21).
  // A canopy whose mean luma and blueness are the sky's, and whose pixels scatter by 35 either way: inside the
  // luma allowance (42), so no luminance rule sees it. Only its roughness does.
  const canopy = (az: number, alt: number, x: number, y: number) => ({
    lum: inSector(az, 100, 110) && alt < 30 ? SKY + 35 * (2 * unit(x, y, 9) - 1) : SKY,
  });
  const h = extract(scene(canopy));
  const x = col(105);
  assert.equal(h.state[x], ColState.Measured);
  near(h.alt[x], 30, 1.2, 'the canopy top (the smoothing reads up to 2 rows early):');
  assert.ok(h.alt[x] >= 29.5, 'the texture trace errs toward blocked');
  assert.equal(h.alt[col(250)], 0, 'the same rows beside it are open');
});

// ---- Azimuth support ---------------------------------------------------------------------------------

/** A wall W (az 100 to 112) from 60 degrees down, a floating dark band F beside it (az 112 to 124, 59 to 49
 *  degrees), then open sky to 5 degrees and ground below. Only F has ground, so the ground is not what the rest of
 *  the compass does at those rows. */
const bandScene: SceneFn = (az, alt) => {
  if (inSector(az, 100, 112)) return { lum: alt < 60 ? WALL : SKY };
  if (inSector(az, 112, 124)) return { lum: (alt <= 59 && alt > 49) || alt < 5 ? WALL : SKY };
  return { lum: SKY };
};

test('azimuth support raises a column to a floating band and the surface under it cannot lower it back', () => {
  // Mutation: azimuth support allowed to lower a column (M01: delete `if (candidate <= alt) continue;` in `settle`); the
  //   band at 59 is dragged down to its ground at 5. Also the anchor test dropped (M03), the slop at 0 rows (M06), or the
  //   whole pass off (M07).
  // The band is ten degrees tall with sky beneath it, so the column alone cannot vouch for it (12 degrees are
  // needed): alone, F publishes only its ground, at 5. The wall next door is a qualifying departure within 6 degrees
  // of the band's top, and the band is darker than the rest of the compass at those rows, so the columns of F
  // within 4 degrees of the wall are RAISED to the band. Their own ground (altitude 5, grounded, standing out
  // from the compass just as the band does) is a promotable candidate too, and it is lower: azimuth support may raise
  // a column and may never lower one.
  const h = extract(scene(bandScene));
  const nearWall = col(113), farEnd = col(123);
  near(h.alt[nearWall], 59.3, 0.7, 'a column of F beside the wall:');
  assert.ok(h.alt[nearWall] > 55, `the band was lowered to ${h.alt[nearWall]}`);
  assert.equal(h.state[nearWall], ColState.Measured);
  near(h.alt[farEnd], 5, 0.7, 'the far end of F, more than 4 degrees from the wall, is its ground alone:');
  near(h.alt[col(106)], 60, 0.7, 'the wall itself:');
  // Every promoted column is at the band; none of the column's other candidates has dragged it down.
  for (let x = col(112) + 1; x < col(112) + 10; x++) assert.ok(h.alt[x] > 55, `column ${x} published ${h.alt[x]}`);
});

test('a floating obstruction 12 degrees tall stands on its own; 10 degrees needs a neighbour', () => {
  // Mutation: drop the persistence test (M21) or persist 6 degrees (M58): the 10 degree branch publishes on its own.
  // 60 degrees of the compass painted: azimuth support is silent (it needs 72), so only what a column can vouch for
  // alone is published.
  const floating = (tall: number): SceneFn => (az, alt) =>
    (az >= 60 ? null : { lum: alt <= 60 && alt > 60 - tall ? WALL : SKY });
  const high = extract(scene(floating(14)));
  near(high.alt[col(30)], 60.3, 0.7, 'a 14 degree branch:');
  const low = extract(scene(floating(10)));
  assert.equal(low.alt[col(30)], 0, 'a 10 degree branch with sky under it is a marking or a disc');
});

test('azimuth support is silent below 72 degrees observed and awake from it', () => {
  // Mutation: remove the 72 degree gate (M14), or drop azimuth support (M07).
  // The roof beside a wall, painted over 57 and then 72 degrees of azimuth.
  const roofScene = (span: number): SceneFn => (az, alt) => {
    if (az >= span) return null;
    if (inSector(az, 30, 42)) return { lum: alt < 60 ? WALL : SKY };
    if (inSector(az, 42, 54)) return { lum: alt <= 59 && alt > 50 ? WALL : SKY };
    return { lum: SKY };
  };
  assert.equal(extract(scene(roofScene(57))).alt[col(43)], 0);
  near(extract(scene(roofScene(72))).alt[col(43)], 59.3, 0.7);
});

// ---- The ring sky (the T10 fix round) ---------------------------------------------------------------

/** The v1 ring: coverage from -8 degrees up to the S25 top of 53.96 (SPEC-v2 4.4), with two levels of pixel noise. */
const RING_TOP = 53.96;
const BLUE_SKY = { lum: SKY, blue: 0 };
/** An obstruction in [from, to) that rises from the ground past the top of the coverage, in the sky's own noise. */
const ringScene = (from: number, to: number, wall: { lum: number; blue: number }, top = RING_TOP): SceneFn =>
  (az, alt, x, y) => {
    if (alt > top || alt < -8) return null;
    const p = inSector(az, from, to) ? wall : BLUE_SKY;
    return { lum: p.lum + 2 * gauss(x, y, 5), blue: p.blue };
  };

test('an obstruction wider than the window is Tall: walls of 10 to 120 degrees past a 54 degree photo top', () => {
  // Mutation: never hold a column to the ring sky (R01); blind the ring test to blueness (R04) or to luma (R05); take the
  //   ring sky from the minimum of the models (R06); widen its allowance to 0.6 (R07) or its blueness floor to 50 (R08):
  //   the walls of 16 degrees and more read Measured at the ground under them.
  // SPEC-v2 4.4: truth above the photo top is Tall and publishes 90. The window model pools +-15 degrees, so a wall
  // wider than about 15 degrees is its own majority: its model is the wall, its top rows match that model exactly,
  // and without the ring sky it came out Measured at the ground under it (16 and 20 degrees, a 40 degree wall, the
  // chart yard's 140 degrees of truth above 53). The wall departs from the sky in luma in one scene and in
  // blueness alone in the other, so each channel of the ring test is held.
  const walls = [['darker', { lum: 70, blue: 0 }], ['warmer', { lum: SKY, blue: -40 }]] as const;
  for (const [what, wall] of walls) {
    for (const width of [10, 14, 16, 20, 40, 120]) {
      const h = extract(scene(ringScene(100, 100 + width, wall)));
      for (let x = col(100); x < col(100 + width); x++) {
        assert.equal(h.state[x], ColState.Tall, `a ${width} degree ${what} wall: column ${x} is state ${h.state[x]}`);
        // The suggestion is where the wall reaches the top of the photo, never the ground under a wall this tall.
        assert.ok(h.alt[x] >= RING_TOP - 1, `a ${width} degree ${what} wall: column ${x} suggests ${h.alt[x]}`);
      }
      // Two degrees clear of it the ring is open sky, whatever the window of a column beside the wall holds.
      for (let x = 0; x < PANO_W; x++) {
        if (x >= col(100) - 6 && x < col(100 + width) + 6) continue;
        assert.equal(h.state[x], ColState.Measured, `a ${width} degree ${what} wall: open column ${x} is state ${h.state[x]}`);
        assert.equal(h.alt[x], 0);
      }
    }
  }
});

test('a wide wall that stays inside the coverage is still a measurement, and one that shares its top rows is Low', () => {
  // Mutation: the ring sky never substituted (R01), blind to luma (R05), from the minimum (R06) or at 0.6 (R07); Measured
  //   without 6 degrees of sky (M31); the 2 degree Tall window dropped (M44).
  // The ring sky takes a column's own sky away only when the sky it found is not the ring's. A 40 degree wall with
  // 14 degrees of sky above it is Measured at its top. One whose top lies 3 degrees below the photo top has 3 degrees
  // of sky above it, which is Low (2-6), and one 1 degree below the top is Tall: the same states as a narrow wall.
  const wall = { lum: 70, blue: 0 };
  const wallTo = (top: number) => extract(scene((az, alt, x, y) => {
    if (alt > RING_TOP || alt < -8) return null;
    const p = inSector(az, 100, 140) && alt < top ? wall : BLUE_SKY;
    return { lum: p.lum + 2 * gauss(x, y, 5), blue: p.blue };
  }));
  const x = col(120);
  const low = wallTo(40);
  assert.equal(low.state[x], ColState.Measured);
  near(low.alt[x], 40, 0.5, 'a wide wall inside the coverage:');
  const three = wallTo(RING_TOP - 3);
  assert.equal(three.state[x], ColState.Low);
  near(three.alt[x], RING_TOP - 3, 0.7, 'its suggestion:');
  assert.equal(wallTo(RING_TOP - 1).state[x], ColState.Tall);
});

test('the ring sky needs a majority, and 72 degrees observed', () => {
  // Mutation: a ring sky with no majority requirement (R03) or a 10 per cent one (R09); active below 72 degrees observed
  //   (R02); from the minimum (R06); the window model pooled from the whole ring (W03).
  // No consensus: three exposures in thirds of the compass, each open sky, none within 32 per cent of the median.
  // No column is held to a sky the ring does not have, and every one is the open measurement it was before.
  const thirds = extract(scene((az, alt, x, y) => (alt > RING_TOP || alt < -8 ? null
    : { lum: (az < 120 ? 60 : az < 240 ? 100 : 160) + 2 * gauss(x, y, 5) })));
  const seamDistance = (x: number) => Math.min(...[0, col(120), col(240), PANO_W].map(s => Math.abs(x - s)));
  for (let x = 0; x < PANO_W; x++) {
    if (seamDistance(x) < 6) continue;   // a column at the step itself answers to the step, as it always did
    assert.equal(thirds.state[x], ColState.Measured, `column ${x} of the no-consensus ring is state ${thirds.state[x]}`);
    assert.equal(thirds.alt[x], 0);
  }
  // A majority and a stretch of another exposure 46 per cent below it: the stretch is not the ring's sky, and Tall.
  const minority = extract(scene((az, alt, x, y) => (alt > RING_TOP || alt < -8 ? null
    : { lum: (az < 252 ? SKY : 70) + 2 * gauss(x, y, 5) })));
  assert.equal(minority.state[col(300)], ColState.Tall);
  assert.equal(minority.state[col(100)], ColState.Measured);
  // Below 72 degrees observed a majority is a guess, so the rule is silent (as azimuth support is): a 20 degree wall
  // in 60 degrees of coverage is its own window and is Measured at the ground; in 72 it is Tall.
  const partial = (span: number) => extract(scene((az, alt, x, y) => {
    if (az >= span || alt > RING_TOP || alt < -8) return null;
    return { lum: (inSector(az, 20, 40) ? 70 : SKY) + 2 * gauss(x, y, 5) };
  }));
  assert.equal(partial(60).state[col(30)], ColState.Measured);
  assert.equal(partial(72).state[col(30)], ColState.Tall);
});

test('lowLight is the MEDIAN of the window sky models: a dark minority does not make low light, a dark majority does', () => {
  // Mutation: lowLight from the minimum of the window models (X3a) or from the maximum (X3b); never true (M40).
  // Two skies a little apart (17 and 22, inside each other's allowance, both under the dark floor of 40): the
  // minimum, the median and the maximum of the models differ, and the 20 of lowLightLuma lies between them.
  const ring = (darkShare: number) => extract(scene((az, alt) => (alt > RING_TOP || alt < -8 ? null
    : { lum: az < 360 * darkShare ? 17 : 22 })));
  assert.equal(ring(0.3).lowLight, false, 'under a third of the compass at 17');
  assert.equal(ring(0.7).lowLight, true, 'most of the compass at 17');
  assert.equal(ring(0.3).state[col(250)], ColState.UnknownDark);
});

test('UnknownDark reads the column\'s own window sky as well as the ring sky: a dark sector in a lit ring is too dark, not Tall', () => {
  // Mutation: the dark test on the model the column is walked against only (D01, the substituted model): the sector's
  //   own sky is replaced by the lit ring sky before the test and the sector reads Tall; the dark test dropped (M34).
  // S15: the ring sky stands in for a window model that departs from it, and the stand-in is lit, so a sector whose own
  // sky is 20 to 38 (under the floor of 40) came out Tall: an obstruction, when the camera simply saw too little.
  // The sector is 80 degrees wide and the columns asserted lie 16 degrees or more inside it, where the window of a
  // column holds nothing but the sector.
  const sector = (lum: number, ring: number) => extract(scene((az, alt, x, y) => (alt > RING_TOP || alt < -8 ? null
    : { lum: (inSector(az, 100, 180) ? lum : ring) + 2 * gauss(x, y, 5), blue: 0 })));
  for (const lum of [20, 30, 38]) {
    const h = sector(lum, SKY);
    for (let x = col(116); x < col(164); x++) {
      assert.equal(h.state[x], ColState.UnknownDark, `a dark sector of ${lum}: column ${x} is state ${h.state[x]}`);
      assert.ok(Number.isNaN(h.alt[x]), `a dark sector of ${lum}: column ${x} carries an altitude`);
    }
    // The lit sky around it is still read, and the sector did not darken the ring: low light is about the compass.
    for (const x of [col(40), col(250), col(330)]) assert.equal(h.state[x], ColState.Measured, `lit column ${x}`);
    assert.equal(h.lowLight, false);
  }
  // The edge of the rule is the same 40 as for a whole dark sky: a sector at 44 is another exposure, which is Tall.
  const edge = sector(44, SKY);
  for (let x = col(116); x < col(164); x++) assert.equal(edge.state[x], ColState.Tall, `a sector of 44: column ${x}`);
  // And the other way round: a lit sector in a dark ring is walked against the dark ring sky, which is too dark.
  const lit = sector(SKY, 30);
  for (let x = col(116); x < col(164); x++) assert.equal(lit.state[x], ColState.UnknownDark, `a lit sector in a dark ring: column ${x}`);
});

console.log(`horizonTraceRules.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
