// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T10: the column tracer ported to degrees (SPEC-v2 5.1, 5.2) and the noise estimator beside it. The three old
// suites run through the raster adapter in tracerVertical, tracerSeed and tracerGradient; this file holds the cases
// the raster adds: the column states, open above coverage, the placement-sigma rule, the focal term, the texture
// channel, azimuth support that may raise and never lower, and `noiseSigma`.
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
//   - the sky above A counted from the A row (17 rows of height read as 18);
//   - an unmeasurable contrast read as 0 instead of unknown.
// The fix round B (ruling S15, T10 re-review) adds two more:
//   - the dark test on the model the column is walked against only (the substituted ring sky): 'UnknownDark reads the
//     column's own window sky as well as the ring sky: a dark sector in a lit ring is too dark, not Tall';
//   - the sigma test back to `sigma > TRACE.measuredMaxSigmaDeg`, which a NaN sigma passes: 'every comparison fails
//     closed: an unknown axis altitude is a NaN sigma, which is Low and never Measured'.
// Every case below opens with a 'Mutation:' note naming the mutants it was seen to turn red, as the old suites did
// (the ids are those of the T10 report's mutant run, which lists the edit of each).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { noiseSigma } from '../noise';
import { TRACE, boundarySigmaDeg, tracer } from '../horizonTrace';
import { ColState, PANO_W, type ColumnHorizon } from '../../types';
import { rasterFromPixels, rowAltitude, type PixelFn, type RasterOptions, type StubPanorama } from './rasterAdapter';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }
const near = (actual: number, expected: number, tol: number, what = 'value') =>
  assert.ok(Math.abs(actual - expected) <= tol, `${what} ${actual} is not within ${tol} of ${expected}`);

/** An integer hash of three numbers to [0, 1): the same pixel is the same noise on every run, in any order. */
function unit(a: number, b: number, salt: number): number {
  let h = Math.imul(a + 0x9E3779B1, 0x85EBCA6B) ^ Math.imul(b + 0x7F4A7C15, 0xC2B2AE35) ^ Math.imul(salt + 0x165667B1, 0x27D4EB2F);
  h = Math.imul(h ^ (h >>> 15), 0x2C1B3C6D); h = Math.imul(h ^ (h >>> 12), 0x297A2D39); h ^= h >>> 15;
  return (h >>> 0) / 4294967296;
}
const gauss = (a: number, b: number, salt: number) =>
  Math.sqrt(-2 * Math.log(1 - unit(a, b, salt))) * Math.cos(2 * Math.PI * unit(a, b, salt + 1));

/** What a scene draws at an azimuth (degrees) and altitude (degrees). */
type SceneFn = (az: number, alt: number, x: number, y: number) => { lum: number; blue?: number } | null;
const scene = (fn: SceneFn, opts?: RasterOptions): StubPanorama =>
  rasterFromPixels(((x, y, alt) => fn((x + 0.5) / 3, alt, x, y)) as PixelFn, opts);
const extract = (pano: StubPanorama, o: { focalSdPct?: number; axisAltDeg?: number; columns?: [number, number] } = {}): ColumnHorizon =>
  tracer.extract(pano, { focalSdPct: o.focalSdPct ?? 0, axisAltDeg: o.axisAltDeg ?? 20, columns: o.columns });
/** The raster column of an azimuth. */
const col = (az: number) => Math.floor(az * 3);
const inSector = (az: number, from: number, to: number) => az >= from && az < to;

const SKY = 130, WALL = 40;

// ---- Constants ----------------------------------------------------------------------------------

test('TRACE is the table of SPEC-v2 3.4 and cannot be edited', () => {
  // Mutation: change any value of TRACE, drop a key, or stop freezing it. (A literal edit of the table; not in the
  //   mutant run, which edits behaviour.)
  assert.deepEqual({ ...TRACE }, {
    rowsPerDeg: 3, skyWindowDeg: 6, skyPoolDeg: 15, followDeg: 12, persistDeg: 12,
    lumaFrac: 0.32, blueMin: 8, blueFrac: 0.32, spreads: 3, localTol: 0.12,
    azSupportDeg: 4, azSlopRows: 18, azMinObservedDeg: 72,
    darkLuma: 40, lowLightLuma: 20, measuredContrast: 6, lowContrast: 3,
    skyAboveMeasuredDeg: 6, skyAboveLowDeg: 2, tallWithinDeg: 2, coverBottomDeg: -2, measuredMaxSigmaDeg: 1.0,
  });
  assert.ok(Object.isFrozen(TRACE));
  assert.equal(tracer.name, 'tracer');
});

test('the tracer carries no site data and imports nothing from the files T32 deletes', () => {
  // Mutation: import photosphere.ts or photosphereStability.ts into the tracer or noise module, or leave a console call
  //   in either. (Not in the mutant run.)
  const trace = readFileSync(new URL('../horizonTrace.ts', import.meta.url), 'utf8');
  const noise = readFileSync(new URL('../noise.ts', import.meta.url), 'utf8');
  for (const src of [trace, noise]) {
    assert.ok(!/from\s+'[^']*\/photosphere'/.test(src), 'imports photosphere.ts');
    assert.ok(!/from\s+'[^']*photosphereStability'/.test(src), 'imports photosphereStability.ts');
    assert.ok(!/__tests__|__sim__/.test(src.replace(/^[ \t]*\/\/[^\r\n]*/gm, '')), 'imports test or simulator code');
    assert.ok(!/console\./.test(src), 'writes to the console');
  }
});

// ---- Noise ---------------------------------------------------------------------------------------

test('noiseSigma recovers the sigma of white noise, and is blind to what the mask annihilates', () => {
  // Mutation: read the 75th percentile for the median (N01), a gain of 5 for 6 (N03), or a kernel centre of 3 for 4
  //   (N04): the recovered sigma is 20 per cent off, or a flat plane reads as noise.
  const w = 160, h = 120;
  for (const sigma of [1, 3, 8]) {
    const plane = new Float32Array(w * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) plane[y * w + x] = 100 + sigma * gauss(x, y, 7);
    near(noiseSigma(plane, w, h) / sigma, 1, 0.06, `white noise sigma ${sigma}:`);
  }
  // 8-bit input, as the old estimator read: the rounding adds 1/12 to the variance.
  const bytes = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) bytes[y * w + x] = Math.round(100 + 4 * gauss(x, y, 11));
  near(noiseSigma(bytes, w, h) / Math.sqrt(16 + 1 / 12), 1, 0.07, '8-bit noise sigma 4:');
  // Anything that depends on x alone or on y alone has no response: a flat plane, a ramp each way, and a
  // horizon (a step that is constant along x) however sharp.
  const flat = new Float32Array(w * h).fill(90);
  const rampY = new Float32Array(w * h), rampX = new Float32Array(w * h), horizon = new Float32Array(w * h), post = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    rampY[y * w + x] = y * 1.5; rampX[y * w + x] = x * 0.7;
    horizon[y * w + x] = y < 60 ? 200 : 20;
    post[y * w + x] = x < 80 ? 200 : 20;
  }
  for (const [name, plane] of [['flat', flat], ['vertical ramp', rampY], ['horizontal ramp', rampX], ['horizon step', horizon], ['vertical step', post]] as const) {
    assert.equal(noiseSigma(plane, w, h), 0, `${name} reads as noise`);
  }
  // What it does answer to besides noise: a checkerboard is all corners.
  const board = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) board[y * w + x] = (x + y) % 2 ? 200 : 20;
  assert.ok(noiseSigma(board, w, h) > 50);
});

test('noiseSigma is a median: terrain in the plane does not make it noisy', () => {
  // Mutation: the 75th percentile for the median (N01), or the kernel centre of 3 (N04): the foliage drags the sigma up.
  const w = 160, h = 120;
  const plane = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const foliage = y > 96;   // the bottom 20 per cent is rough
    plane[y * w + x] = 100 + (foliage ? 45 : 2) * gauss(x, y, 3);
  }
  // The median of a mixture sits at the sky's 62nd percentile of |z|, 1.3 times its sigma; the mean of the same
  // responses would read 5 times it.
  const est = noiseSigma(plane, w, h);
  assert.ok(est > 2 && est < 3.2, `sky noise 2 under 20 per cent foliage of 45 read ${est}`);
});

test('noiseSigma reads only windows whose nine samples are all masked in', () => {
  // Mutation: read the mask at the centre pixel only (N02): the clean strip reads the noise of its neighbours.
  const w = 120, h = 90;
  const plane = new Float32Array(w * h), left = new Uint8Array(w * h), right = new Uint8Array(w * h), strip = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = y * w + x;
    plane[i] = 100 + (x < 60 ? 0 : 6) * gauss(x, y, 5);   // clean left half, noisy right half
    left[i] = x < 60 ? 1 : 0; right[i] = x >= 60 ? 1 : 0;
  }
  assert.equal(noiseSigma(plane, w, h, left), 0);
  near(noiseSigma(plane, w, h, right) / 6, 1, 0.1, 'the noisy half alone:');
  // Three clean columns in a noisy plane: only the windows centred on the middle one lie wholly inside the mask, and
  // they are clean. A mask read at the centre alone would take the two edge columns too, whose windows reach into the
  // noise.
  const mixed = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    mixed[y * w + x] = 100 + (x >= 40 && x <= 42 ? 0 : 6) * gauss(x, y, 8);
    strip[y * w + x] = x >= 40 && x <= 42 ? 1 : 0;
  }
  assert.equal(noiseSigma(mixed, w, h, strip), 0, 'the clean strip, read through a mask of its own width');
  // A strip two columns wide holds no whole window at all.
  const thin = strip.map((v, i) => (v && i % w !== 42 ? 1 : 0));
  assert.equal(noiseSigma(mixed, w, h, thin), 0, 'no whole window');
  const full = new Uint8Array(w * h).fill(1);
  assert.equal(noiseSigma(plane, w, h, full), noiseSigma(plane, w, h));
  assert.equal(noiseSigma(new Float32Array(2 * 2), 2, 2), 0);
  assert.equal(noiseSigma(new Float32Array(10), 8, 8), 0, 'a short buffer');
  assert.equal(noiseSigma(plane, w, h, new Uint8Array(4)), 0, 'a short mask');
});

// ---- The column states (5.2) --------------------------------------------------------------------------

/** A ring of open sky with a wall in azimuth [from, to) from `top` degrees down. */
const wallScene = (from: number, to: number, top: number, extra?: { coverTop?: number; coverBottom?: number; sky?: number }): SceneFn =>
  (az, alt) => {
    if (alt > (extra?.coverTop ?? 90) || alt < (extra?.coverBottom ?? -10)) return null;
    return { lum: inSector(az, from, to) && alt < top ? WALL : (extra?.sky ?? SKY) };
  };

test('Measured: a wall against open sky, published at its top, with the evidence behind it', () => {
  // Mutation: publish the first blocked row instead of the row above it (M51), disable the luma channel (M46), or
  //   drop the noise floor (M42): the wall is lost or lands below its top.
  const h = extract(scene(wallScene(100, 108, 30)));
  const x = col(104), open = col(250);
  assert.equal(h.state[x], ColState.Measured);
  near(h.alt[x], 30, 0.5, 'the wall top:');
  // "The top of the first qualifying run plus one row toward blocked": the row above the first blocked one, so the
  // published altitude is never below the wall and is at most one row (0.334 degrees) above it.
  assert.ok(h.alt[x] >= 30 && h.alt[x] < 30 + 100 / 299 + 1e-4, `A ${h.alt[x]} is not the row above the wall top at 30`);
  assert.ok(h.contrastSigma[x] >= TRACE.measuredContrast, `contrast ${h.contrastSigma[x]}`);
  near(h.sigmaDeg[x], 0.1, 1e-6, 'placement 5 x 0.02 with no focal error:');
  near(h.top[x], 90, 0.05); near(h.bottom[x], -10, 0.05);
  assert.equal(h.state[open], ColState.Measured);
  assert.equal(h.alt[open], 0);
  assert.ok(Number.isNaN(h.contrastSigma[open]), 'an open column has no boundary to take a contrast of');
  assert.equal(h.lowLight, false);
});

test('open above coverage: 6 degrees of sky above the boundary is Measured wherever the coverage ends', () => {
  // Mutation: drop the 6 degrees of sky Measured needs (M31), never assign Tall (M28), or drop the 2 degree Tall window
  //   (M44): the 4 degree and the 1 degree walls read Measured.
  // The coverage of this scene ends 8 degrees above the wall. An overhanging branch above the photographed band
  // is missed; the review draws the photo top so the user sees where the evidence ends (5.2).
  const h = extract(scene(wallScene(100, 108, 25, { coverTop: 33 })));
  const x = col(104);
  assert.equal(h.state[x], ColState.Measured);
  near(h.alt[x], 25, 0.5);
  near(h.top[x], 33, 0.4, 'the photo top:');
  // And the same wall with only 4 degrees above it is Low, and with 1 degree is Tall.
  assert.equal(extract(scene(wallScene(100, 108, 25, { coverTop: 29 }))).state[x], ColState.Low);
  assert.equal(extract(scene(wallScene(100, 108, 25, { coverTop: 26 }))).state[x], ColState.Tall);
  // A coverage that ends well above the wall changes nothing for the columns without one.
  assert.equal(h.state[col(250)], ColState.Measured);
});

test('Low: a boundary with only 2 to 6 degrees of sky above it publishes 90 and suggests the trace', () => {
  // Mutation: drop the 6 degrees of sky Measured needs (M31): the wall with 4.5 degrees of sky above it reads Measured.
  const h = extract(scene(wallScene(100, 108, 25, { coverTop: 29.5 })));
  const x = col(104);
  assert.equal(h.state[x], ColState.Low);
  near(h.alt[x], 25, 0.5, 'the suggestion, the traced boundary:');
});

test('Tall: a wall within 2 degrees of the photo top, or a top that does not match the sky', () => {
  // Mutation: never assign Tall (M28), stop requiring the top rows to match the sky (M29), drop the 2 degree window
  //   (M44), or read a contrast that cannot be measured as 0 instead of unknown (NAN).
  const x = col(104);
  const near_top = extract(scene(wallScene(100, 108, 25, { coverTop: 26.2 })));
  assert.equal(near_top.state[x], ColState.Tall);
  // The wall reaches the very top of the photo. There is no sky above its boundary to take a contrast against, so the
  // contrast is unknown (NaN), which is not "none" (0).
  const reaches = extract(scene(wallScene(100, 108, 90)));
  assert.equal(reaches.state[x], ColState.Tall);
  const overhead = extract(scene(wallScene(100, 108, 90, { coverTop: 80 })));   // every painted row of the column is wall
  assert.equal(overhead.state[x], ColState.Tall);
  assert.ok(Number.isNaN(overhead.contrastSigma[x]), `contrast ${overhead.contrastSigma[x]} of a boundary at the photo top`);
  assert.ok(!Number.isNaN(near_top.contrastSigma[x]), 'a boundary with sky above it has a contrast');
  // A boundary far below, and a bright band across the top 1 degree of this sector only: the top rows do not match
  // the sky, so what lies above the evidence is unknown.
  const band = extract(scene((az, alt) => {
    const wall = inSector(az, 100, 108);
    if (wall && alt > 86.5) return { lum: 230 };
    return { lum: wall && alt < 25 ? WALL : SKY };
  }));
  assert.equal(band.state[x], ColState.Tall);
  assert.equal(band.state[col(250)], ColState.Measured, 'the rest of the compass is untouched');
});

test('Tall also covers a wall only the neighbours saw, and Low a boundary with a wire just above it', () => {
  // Mutation: a boundary within 2 degrees of the top is Tall only when the top rows mismatch (M30), or less than 2
  //   degrees of sky above a boundary is Tall (M32, the wire); also the azimuth pass off (M07) and the mosaic threshold
  //   doubled (M16).
  // A wall 31.5 per cent darker than its sky is under the column rule's 32 per cent allowance: the wide pass sees
  // nothing, and the 12 per cent pass plus the mosaic find it. Its run marks no row of the wide pass, so only the
  // distance to the photo top can call it Tall when it starts 1.2 degrees below it.
  const soft = (top: number) => scene((az, alt) => (alt > 60 ? null : { lum: inSector(az, 100, 108) && alt < top ? 89 : SKY }));
  const x = col(104);
  const seen = extract(soft(58.8));
  assert.equal(seen.state[x], ColState.Tall);
  near(seen.alt[x], 58.9, 0.5, 'the neighbours found it:');
  assert.equal(extract(soft(40)).state[x], ColState.Measured, 'the same wall further down is a measurement');
  // A one degree wire 0.8 degrees above a wall: 0.8 degrees of sky between them is a boundary found and not
  // measured. The top of the photo is far away, so it is Low and carries its suggestion; it is not Tall.
  const wire = extract(scene((az, alt) => {
    const here = inSector(az, 100, 108);
    return { lum: here && (alt < 30 || (alt > 30.8 && alt <= 31.8)) ? WALL : SKY };
  }));
  assert.equal(wire.state[x], ColState.Low);
  near(wire.alt[x], 30, 0.5, 'its suggestion:');
});

test('UnknownUnseen: no painted run at altitude 0, or a run that stops short of -2 degrees', () => {
  // Mutation: drop the coverage-gap rule (M33): the column whose run stops at -1 degree is measured.
  // Nothing painted at altitude 0.
  const none = extract(scene((_, alt) => (alt > 5 ? { lum: SKY } : null)));
  for (const x of [0, 400, 1079]) {
    assert.equal(none.state[x], ColState.UnknownUnseen);
    assert.ok(Number.isNaN(none.alt[x]) && Number.isNaN(none.top[x]) && Number.isNaN(none.bottom[x]));
  }
  // A run that holds altitude 0 but ends at -1: a coverage gap between -2 and the boundary.
  const short = extract(scene((_, alt) => (alt >= -1.1 ? { lum: SKY } : null)));
  assert.equal(short.state[500], ColState.UnknownUnseen);
  assert.ok(Number.isNaN(short.alt[500]));
  near(short.bottom[500], -1.1, 0.4);
  // At -2.5 the coverage reaches the floor of the rule and the column is measured.
  const reaches = extract(scene((_, alt) => (alt >= -2.5 ? { lum: SKY } : null)));
  assert.equal(reaches.state[500], ColState.Measured);
});

test('UnknownDark: a window sky below 40 is never read, lit wall or not, and lowLight says how dark', () => {
  // Mutation: drop the dark rule (M34): the lit wall at night is measured; or lowLight never true (M40).
  const night = (sky: number, wall = false) => extract(scene((az, alt) =>
    (wall && inSector(az, 100, 108) && alt < 30 ? { lum: 190 } : { lum: sky })));
  const dusk = night(25);
  assert.equal(dusk.state[col(250)], ColState.UnknownDark);
  assert.ok(Number.isNaN(dusk.alt[col(250)]));
  assert.equal(dusk.lowLight, false, 'a sky of 25 is dark but not low light');
  const dark = night(12);
  assert.equal(dark.state[col(250)], ColState.UnknownDark);
  assert.equal(dark.lowLight, true);
  // The edge of the rule: 39 is dark, 40 is read.
  assert.equal(night(39).state[col(250)], ColState.UnknownDark);
  assert.equal(night(40).state[col(250)], ColState.Measured);
  // A wall lit against a dark sky must not be published as open, nor as a measurement: every column of it is
  // dark, none Measured, and nothing is an altitude.
  const lit = night(12, true);
  for (let x = col(100); x < col(108); x++) {
    assert.equal(lit.state[x], ColState.UnknownDark, `lit wall column ${x}`);
    assert.ok(Number.isNaN(lit.alt[x]));
  }
  assert.ok(!Array.from(lit.state).includes(ColState.Measured));
  // At twilight the same wall is read: a sky of 45 is measurable.
  const twilight = extract(scene((az, alt) => (inSector(az, 100, 108) && alt < 30 ? { lum: 190 } : { lum: 45 })));
  assert.equal(twilight.state[col(104)], ColState.Measured);
  near(twilight.alt[col(104)], 30, 0.5);
});

test('the window model keeps to smooth pixels: a rough canopy in the top rows is not the sky', () => {
  // Mutation: pool every pixel and not the smooth ones (M25): the canopy is the model and the ring reads dark.
  // Sixty per cent of the top 6 degrees of every column is a dark rough canopy (mean 10), forty per cent smooth sky.
  // The median of everything pooled is the canopy and would call the ring dark; the model reads the smooth pixels.
  const h = extract(scene((_, alt, x, y) => {
    if (alt > 90 - 3.6) return { lum: Math.max(0, 10 + 8 * gauss(x, y, 21)) };
    return { lum: SKY };
  }));
  assert.notEqual(h.state[500], ColState.UnknownDark);
  assert.equal(h.state[500], ColState.Tall, 'the rough top rows do not match the sky beside them');
});

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

test('every comparison fails closed: an unknown axis altitude is a NaN sigma, which is Low and never Measured', () => {
  // Mutation: the sigma test back to `sigma > TRACE.measuredMaxSigmaDeg` (S01): NaN > 1 is false, and with axisAltDeg
  //   NaN and no keyframes all 1080 columns of a clean raster read Measured.
  const clean = scene(() => ({ lum: SKY }));
  const lost = extract(clean, { axisAltDeg: NaN });
  for (let x = 0; x < PANO_W; x++) {
    assert.ok(Number.isNaN(lost.sigmaDeg[x]), `column ${x}: the sigma of a boundary off an unknown axis is ${lost.sigmaDeg[x]}`);
    assert.equal(lost.state[x], ColState.Low, `column ${x} is state ${lost.state[x]} with no axis altitude`);
    assert.equal(lost.alt[x], 0, 'the trace is still there: it is the suggestion of a Low column');
  }
  // The same raster with the axis known is the open measurement it should be: the NaN is what made the difference.
  assert.equal(extract(clean, { axisAltDeg: 20 }).state[col(250)], ColState.Measured);
  // A boundary with a contrast is held the same way: the wall is Low off an unknown axis and Measured off a known one.
  const wall = scene(wallScene(100, 108, 30));
  assert.equal(extract(wall, { axisAltDeg: 20 }).state[col(104)], ColState.Measured);
  const walled = extract(wall, { axisAltDeg: NaN });
  assert.equal(walled.state[col(104)], ColState.Low);
  near(walled.alt[col(104)], 30, 0.5, 'the wall top, kept as the suggestion:');
  assert.ok(!Array.from(walled.state).includes(ColState.Measured), 'a Measured column off an unknown axis');
});

test('Measured needs 6 degrees of sky above A itself: 17 rows of height are 5.7 degrees, 18 are 6.0', () => {
  // Mutation: count the sky above A from the A row, so that 17 rows of height read as 18 (A6); drop the 6 degrees (M31).
  // The rows counted begin with the row of A, so 18 rows of sky are 17 rows of height above A. The wall top is at 25
  // (A is the row above its first blocked row, 25.1), and the photo top is 31.0 (the topmost sky row is 30.8, 5.7
  // above A) or 31.3 (31.1, 6.0 above A).
  const x = col(104);
  assert.equal(extract(scene(wallScene(100, 108, 25, { coverTop: 31.0 }))).state[x], ColState.Low);
  assert.equal(extract(scene(wallScene(100, 108, 25, { coverTop: 31.3 }))).state[x], ColState.Measured);
});

// ---- The extraction interface -------------------------------------------------------------------------

test('columns restricts the extraction, wraps, and leaves the rest unseen', () => {
  // Mutation: a `columns` range that does not wrap (M41).
  const pano = scene(wallScene(100, 108, 30));
  const full = extract(pano);
  const part = extract(pano, { columns: [col(98), col(110)] });
  for (let x = col(98); x < col(110); x++) {
    assert.equal(part.state[x], full.state[x], `state ${x}`);
    assert.equal(part.alt[x], full.alt[x], `alt ${x}`);
  }
  assert.equal(part.state[col(250)], ColState.UnknownUnseen);
  assert.ok(Number.isNaN(part.alt[col(250)]));
  assert.equal(part.state[col(110)], ColState.UnknownUnseen, 'the end of the range is exclusive');
  // Wrapping past 1080 and with the end below the start.
  const wrapped = tracer.extract(scene(wallScene(358, 360, 30)), { focalSdPct: 0, axisAltDeg: 20, columns: [1070, 1090] });
  for (const x of [1070, 1079, 0, 9]) assert.equal(wrapped.state[x], ColState.Measured, `column ${x}`);
  assert.equal(wrapped.state[10], ColState.UnknownUnseen);
  const below = tracer.extract(pano, { focalSdPct: 0, axisAltDeg: 20, columns: [1075, 5] });
  assert.equal(below.state[1079], full.state[1079]);
  assert.equal(below.state[4], full.state[4]);
  assert.equal(below.state[5], ColState.UnknownUnseen);
  assert.equal(extract(pano, { columns: [300, 300] }).state[300], ColState.UnknownUnseen, 'an empty range reads nothing');
  assert.equal(PANO_W, 1080);
});

test('extraction is pure: the raster is not changed and a second run agrees with the first', () => {
  // Mutation: write into the raster, or keep state between calls. (Not in the mutant run.)
  const pano = scene(wallScene(100, 108, 30));
  const before = Buffer.from(pano.rgba).toString('base64') + Buffer.from(pano.sigma).toString('base64');
  const a = extract(pano), b = extract(pano);
  assert.equal(Buffer.from(pano.rgba).toString('base64') + Buffer.from(pano.sigma).toString('base64'), before);
  assert.deepEqual(Array.from(a.state), Array.from(b.state));
  assert.deepEqual(Array.from(a.alt), Array.from(b.alt));
  assert.equal(a.alt.length, PANO_W);
  assert.equal(a.state.length, PANO_W);
});

console.log(`horizonTrace.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
