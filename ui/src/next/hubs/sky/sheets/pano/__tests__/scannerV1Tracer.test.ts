// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T30: what the tracer learned from the realism rasters (SPEC-v2 5.1, 5.2, S15, T30 item 9). Every case here is a change the
// realism gate priced before it shipped (the T30 report has the numbers): the wall of 14 to 18 degrees that read Measured, the
// stripes of a sky that read as obstructions, a boundary walked up through a sky's own lumps, the seam pixel without a placement
// sigma, and the Finish-time guard for a partial scan. The scenes are the tracer suites' (horizon/__tests__/traceScenes.ts).
//
// Mutants this file must catch (each applied from a byte backup of horizonTrace.ts or scanner.ts and run under the normal command):
//   * the pooled-pixel share dropped from `analysis` (only the model median is held against the ring sky): 'a wall of any width
//     that reaches the top of the photo is Tall' finds the 14 to 18 degree walls Measured (17 of 48 columns at 16 degrees);
//   * `brightSky` always false (a bright mark across the top is always a top that does not match): 'bright marks that most of the
//     compass shows are the sky's texture' finds the boundary Tall under a sky of stripes;
//   * `brightSky` always true (bright floating runs always exempt): 'a bright band over one sector of a smooth sky is still Tall'
//     finds it Measured;
//   * `settleOnStep` not called: 'a boundary with a step in it is the step' finds it 6 degrees up the sky's own gradient;
//   * `placementSigmaAt` reading its own pixel only: 'a seam pixel ...' finds the boundary Low;
//   * `guardPartialScan` not called by Finish, or changing nothing: 'the partial-scan guard' finds the wall Measured at the ground.
import assert from 'node:assert/strict';
import { guardPartialScan } from '../scanner';
import { ColState, PANO_W } from '../types';
import { tracer } from '../horizon/horizonTrace';
import { rasterFromPixels } from '../horizon/__tests__/rasterAdapter';
import { GROUND, SKY, col, extract, gauss, inSector, near, scene, unit } from '../horizon/__tests__/traceScenes';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const tally = (h: { state: Uint8Array }, x0: number, x1: number) => {
  const c: Record<number, number> = {};
  for (let x = x0; x < x1; x++) c[h.state[x]] = (c[h.state[x]] ?? 0) + 1;
  return c;
};

test('a wall of any width that reaches the top of the photo is Tall: the pooled pixels, not only their median, are held against the ring sky (S15)', () => {
  // A wall 36 per cent darker than the sky (the walk's allowance is 32) under a luma noise of 5, from the top of the coverage
  // to the bottom. Past 8 degrees the pool of +-15 degrees is split between sky and wall, the median lands in either cluster or
  // between them, and without the share of departing pixels a wall of 14 to 18 degrees came out Measured at the ground.
  for (const width of [8, 12, 14, 16, 18, 24]) {
    const from = 100, to = from + width;
    const h = extract(scene((az, alt, x, y) => (alt > 54 || alt < -4 ? null
      : { lum: Math.round((inSector(az, from, to) ? SKY * 0.64 : alt < 0 ? GROUND : SKY) + 5 * gauss(x, y, 7)) })));
    const inside = tally(h, col(from), col(to));
    assert.deepEqual(inside, { [ColState.Tall]: col(to) - col(from) }, `a wall of ${width} degrees: ${JSON.stringify(inside)}`);
    assert.equal(h.state[col(from - 20)], ColState.Measured, 'the sky beside it is untouched');
    near(h.alt[col(from - 20)], 0, 100 / 299 + 1e-4, 'the ground at the horizon:');
  }
  // A wall that stays inside the coverage, with sky above it, is a measurement, whatever the pooled pixels do.
  const low = extract(scene((az, alt, x, y) => (alt > 54 || alt < -4 ? null
    : { lum: Math.round((inSector(az, 100, 116) && alt < 30 ? SKY * 0.64 : SKY) + 5 * gauss(x, y, 7)) })));
  assert.equal(low.state[col(108)], ColState.Measured);
  near(low.alt[col(108)], 30, 0.7, 'the wall top:');
});

/** A sky of `period` degrees of bright marks, each `width` wide and reaching down from the top of the photo for 4 degrees, with a
 *  skyline (a dark band under 20 degrees) the marks never touch. */
const markedSky = (period: number, width: number) => scene((az, alt, x, y) => {
  if (alt > 54 || alt < -4) return null;
  const mark = alt > 50 && az % period < width;
  return { lum: Math.round((alt < 20 ? 40 : mark ? 230 : SKY) + 2 * gauss(x, y, 3)) };
});

test('bright marks that most of the compass shows are the sky\'s texture: the boundary under one is still a measurement', () => {
  // A mark every 20 degrees, 3 wide: 15 per cent of the sampled columns have a bright run in their top rows, over the line of
  // 10 per cent, so a bright floating run does not make the top of the photo Tall or interrupt the sky above a boundary.
  const h = extract(markedSky(20, 3));
  const x = col(21);   // a column under a mark
  assert.equal(h.state[x], ColState.Measured, `state ${h.state[x]}`);
  near(h.alt[x], 20, 0.7, 'the skyline:');
  assert.equal(h.state[col(30)], ColState.Measured, 'and a column between marks');
});

test('a bright band over one sector of a smooth sky is still Tall: a mark that most of the compass does not show does not match the sky', () => {
  // The same mark in 3 degrees of a 360-degree ring (1 per cent of the sampled columns, under the line of 10).
  const h = extract(scene((az, alt, x, y) => {
    if (alt > 54 || alt < -4) return null;
    return { lum: Math.round((alt < 20 ? 40 : inSector(az, 100, 103) && alt > 50 ? 230 : SKY) + 2 * gauss(x, y, 3)) };
  }));
  assert.equal(h.state[col(101.5)], ColState.Tall);
  assert.equal(h.state[col(250)], ColState.Measured, 'the rest of the compass is untouched');
});

test('a boundary with a step in it is the step: the sky\'s own darkening beside a crown is not part of the edge, a ramp still is', () => {
  // The sky darkens from 178 to 163 over the 6 degrees above a crown whose edge is a step to 89 (the treeline of the realism
  // cases, where the walk climbed the lump to 6 degrees above the crown). The boundary is the step.
  const crown = scene((az, alt, x, y) => {
    if (alt > 54 || alt < -4) return null;
    const top = inSector(az, 100, 140) ? 30 : -20;
    const sky = alt > top + 6 ? 178 : 178 - 15 * (top + 6 - alt) / 6;
    return { lum: Math.round((alt < top ? 89 : sky) + 2 * gauss(x, y, 3)) };
  });
  const h = extract(crown);
  const x = col(120);
  assert.equal(h.state[x], ColState.Measured);
  near(h.alt[x], 30, 1.0, 'the crown top, not the top of the lump:');
  // A real ramp has no step to settle on and reads exactly as it did before the step rule: 89 at 20 degrees rising to 178 at 38
  // (the tracer's reading, 20.43, is the same with and without `settleOnStep`, as is that of the three other ramps tried).
  const ramp = extract(scene((az, alt, x, y) => (alt > 54 || alt < -4 ? null
    : { lum: Math.round((inSector(az, 100, 140) ? Math.max(89, Math.min(178, 89 + (alt - 20) * 5)) : 178) + 2 * gauss(x, y, 3)) })));
  assert.equal(ramp.state[col(120)], ColState.Measured);
  near(ramp.alt[col(120)], 20.43, 0.1, 'a ramp keeps its reading:');
});

test('a seam pixel carries no placement sigma, and a neighbour speaks for it; 255 on all of them is still unknown', () => {
  // Between two slices, where both have feather weight under 0.5, the raster stores 255 although the slices either side are well
  // placed. A boundary there is Measured on the neighbours' sigma; where no pixel within two thirds of a degree has one it is Low.
  const draw = (sigma: (x: number, y: number) => number) => rasterFromPixels((x, y, alt) => {
    if (alt > 54 || alt < -4) return null;
    return { lum: Math.round((inSector((x + 0.5) / 3, 100, 140) && alt < 30 ? 40 : SKY) + 2 * gauss(x, y, 3)) };
  }, { sigma });
  const x = col(120);
  const own = tracer.extract(draw((px) => (px === x ? 255 : 5)), { focalSdPct: 0, axisAltDeg: 20 });
  assert.equal(own.state[x], ColState.Measured, 'the pixel itself has none, its neighbours do');
  const none = tracer.extract(draw((px) => (Math.abs(px - x) <= 2 ? 255 : 5)), { focalSdPct: 0, axisAltDeg: 20 });
  assert.equal(none.state[x], ColState.Low, 'nothing within two columns: unknown');
});

test('the partial-scan guard: a uniform column of a scan under 180 degrees is Tall at Finish, a column with a boundary is not, a half ring is left alone', () => {
  const wall = (span: number) => extract(scene((az, alt, x, y) => (az < span && alt <= 54 && alt >= -4 ? { lum: Math.round(60 + 2 * gauss(x, y, 3)) } : null)));
  const observed = (h: { state: Uint8Array }) => { let n = 0; for (let x = 0; x < PANO_W; x++) if (h.state[x] !== ColState.UnknownUnseen) n++; return n; };

  // 100 degrees of a wall that fills the photo: its own sky, so the walk finds no boundary in any column of it, which the
  // tracer reads UnknownContrast with no altitude (S51; before it read Measured at 0, and the guard caught that).
  const h = wall(100);
  const before = tally(h, 0, col(100));
  assert.equal(before[ColState.UnknownContrast], col(100), `the wall is its own sky: ${JSON.stringify(before)}`);
  assert.equal(guardPartialScan(h, observed(h)), col(100));
  assert.deepEqual(tally(h, 0, col(100)), { [ColState.Tall]: col(100) });
  assert.ok(Number.isNaN(h.alt[col(50)]), 'and it suggests no altitude');

  // The same scan with a ground line under 10 degrees: a boundary is found, so the guard leaves it.
  const g = extract(scene((az, alt, x, y) => (az < 100 && alt <= 54 && alt >= -4 ? { lum: alt < 10 ? 50 : Math.round(130 + 2 * gauss(x, y, 3)) } : null)));
  assert.equal(guardPartialScan(g, observed(g)), 0);
  assert.equal(g.state[col(50)], ColState.Measured);

  // 200 degrees observed is past the half ring: the guard is silent.
  const half = wall(200);
  assert.equal(guardPartialScan(half, observed(half)), 0);
  assert.equal(half.state[col(100)], ColState.UnknownContrast, 'untouched by the guard: no boundary, as the tracer read it');
  // The edge: 179.9 and 180 degrees.
  assert.equal(guardPartialScan(wall(180), 539), col(180), '539 columns is under 180 degrees');
  assert.equal(guardPartialScan(wall(180), 540), 0, '540 columns is 180 degrees');
});

void unit;
console.log(`scannerV1Tracer.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
