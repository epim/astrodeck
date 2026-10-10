// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T08: the panorama scanner's band raster (SPEC-v2 4.11, D11, D12).
//
// Mutant this file must catch (SPEC-v2 7.2): class weight as a reset. `paint` lets a higher class replace what a lower one
// painted (and a lower class skip a pixel a higher one holds) instead of weighting by feather times class weight.
// `class is a weight, never a reset` fails it on the boundary gradient: the aligned/blurred seam of a correct build steps
// 2.8 times a plain two-aligned feather per column (121 against 43 of 255), and a reset steps 3.5 times or more. `an aligned
// slice over a sensor slice` and the cross-fade test fail it too, because 100 % is not 1 / 1.01.
//
// Other mutants run against this file (their failing tests are listed in the T08 report): the vertical limit at 1.0
// instead of 0.9, `clear()` resetting firstSeen, sigma as the last writer instead of the minimum, the coverage band
// widened, `shiftAzimuth` off by a column, the dirty rectangle clipped at the right edge, a nearest-neighbour sample, a
// pixel centre off by half, the trailing fill ignored, the zenith branch removed, and others.
//
// A pitch-0 camera makes the geometry exact: the horizontal tangent angle of a raster pixel is its azimuth offset from the
// camera heading at every altitude, so a feather or a seam can be placed to a column.
import assert from 'node:assert/strict';
import { DEG, lookBasis } from '../../photosphereGeometry';
import { BandPanorama, CLASS_WEIGHT, featherWeight, shiftRaster } from '../bandPanorama';
import { intrinsicsAt, priorFNorm, projectCamera, qinv, qrotate, quatFromBasis } from '../rotation';
import { PANO_H, PANO_W, PROFILE_BINS, PixClass, type DirtyRect, type KeyClass, type SliceSource } from '../types';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const near = (a: number, b: number, tol: number, what = '') => assert.ok(Math.abs(a - b) <= tol, `${what} ${a} != ${b} (tol ${tol})`);

// ---- Fixtures ---------------------------------------------------------------

type Fill = (col: number, row: number) => [number, number, number];
const FN = priorFNorm(180, 320);                 // the default lens: 43.00 x 70.00 degrees
const K0 = intrinsicsAt(0, 180, 320, FN);
const uniform = (v: number): Fill => () => [v, v, v];

/** A strip covering +-halfDeg of horizontal tangent angle about the centre, full height, filled by `fill`. */
function makeStrip(halfDeg: number, fill: Fill, k0 = K0) {
  const reach = k0.f * Math.tan(halfDeg * DEG);
  const stripX0 = Math.floor(k0.cx - reach), stripW = Math.ceil(k0.cx + reach) - stripX0, stripH = k0.h;
  const strip = new Uint8Array(stripW * stripH * 3);
  for (let row = 0; row < stripH; row++) for (let col = 0; col < stripW; col++) strip.set(fill(col, row), (row * stripW + col) * 3);
  return { strip, stripX0, stripW, stripH };
}

/** A slice looking at heading `az`, elevation `el`, with a uniform colour (a number) or a pattern. */
function slice(az: number, el: number, cls: KeyClass, fill: Fill | number, extra: Partial<SliceSource> = {}, halfDeg = 10): SliceSource {
  return {
    id: 0, ...makeStrip(halfDeg, typeof fill === 'number' ? uniform(fill) : fill),
    pose: quatFromBasis(lookBasis(az, el)), k0: K0, gainRGB: [1, 1, 1], cls, sigmaDeg: 1,
    halfWidthDeg: 3, trailingDeg: 0, trailingSide: 0, topFrac: 0.9, ...extra,
  };
}

const colAz = (x: number) => (x + 0.5) / 3;                       // azimuth of a column centre
const colOf = (az: number) => Math.floor(az * 3);                 // the column containing an azimuth
const rowOf = (alt: number) => Math.round((90 - alt) * (PANO_H - 1) / 100);
const altOfRow = (y: number) => 90 - y / (PANO_H - 1) * 100;
const at = (x: number, y: number) => y * PANO_W + x;
const red = (p: { rgba: Uint8ClampedArray }, x: number, y: number) => p.rgba[at(x, y) * 4];
const alpha = (p: { rgba: Uint8ClampedArray }, x: number, y: number) => p.rgba[at(x, y) * 4 + 3];
const fresh = () => { const p = new BandPanorama(); p.begin(0); return p; };
const observed = (p: BandPanorama) => { let n = 0; for (let i = 3; i < p.rgba.length; i += 4) if (p.rgba[i] !== 0) n++; return n; };
const inRect = (r: DirtyRect, x: number, y: number) => y >= r.y && y < r.y + r.h && ((x - r.x + PANO_W) % PANO_W) < r.w;
const sumsOf = (p: BandPanorama) => p['sumW'];

/** A deterministic busy pattern, so a shifted or resampled raster differs from its source almost everywhere. */
const TEXTURE: Fill = (col, row) => [(col * 7 + row * 3) & 255, (col * 13 + row) & 255, (row * 5 + col * 2) & 255];

// ---- Feather and class weight ------------------------------------------------

test('featherWeight: 1 within +-1 degree, linear to 0 at +-3, and neighbours 4 degrees apart sum to 1', () => {
  for (const [x, a, b] of [[1.0, 1.0, 0.0], [1.5, 0.75, 0.25], [2.0, 0.5, 0.5], [2.5, 0.25, 0.75], [3.0, 0.0, 1.0]]) {
    near(featherWeight(x), a, 1e-12, `w_A(${x})`);
    near(featherWeight(4 - x), b, 1e-12, `w_B(${x})`);
  }
  for (let x = 1; x <= 3; x += 0.01) near(featherWeight(x) + featherWeight(4 - x), 1, 1e-6, `sum at ${x}`);
  for (const a of [0, 0.4, 1, -1]) assert.equal(featherWeight(a), 1);
  for (const a of [3, -3, 3.5, -90, 1e9, NaN]) assert.equal(featherWeight(a), 0);
  assert.equal(featherWeight(-2), featherWeight(2));
  near(featherWeight(-2.4), 0.3, 1e-12);
  assert.deepEqual({ ...CLASS_WEIGHT }, { aligned: 1, blurred: 0.1, sensor: 0.01 });
});

test('slices 4 degrees apart cross-fade: the accumulated weight is 1 across the overlap and the colour follows (x - 1) / 2', () => {
  const p = fresh();
  p.paint(slice(100, 0, 'aligned', 0), 0);
  p.paint(slice(104, 0, 'aligned', 255), 0);
  let checked = 0;
  for (let x = colOf(100.5); x <= colOf(103.5); x++) {
    const off = colAz(x) - 100;                          // from slice A; from slice B it is off - 4
    if (off < 1 || off > 3) continue;
    for (const alt of [-9, 0, 15, 30]) {
      const y = rowOf(alt);
      near(sumsOf(p)[at(x, y)], 1, 1e-6, `sumW at x=${x} alt=${alt}`);
      near(red(p, x, y), 255 * (off - 1) / 2, 0.51, `colour at offset ${off}`);
      checked++;
    }
  }
  assert.ok(checked >= 24, `only ${checked} overlap pixels checked`);
});

test('an aligned slice over a sensor slice holds at least 98.9 % of the pixel, in either paint order', () => {
  for (const order of ['sensor-first', 'aligned-first'] as const) {
    const p = fresh();
    const paintAligned = () => p.paint(slice(100, 0, 'aligned', 255), 0);
    const paintSensor = () => p.paint(slice(100, 0, 'sensor', 0), 0);
    if (order === 'sensor-first') { paintSensor(); paintAligned(); } else { paintAligned(); paintSensor(); }
    const x = colOf(100), y = rowOf(10), v = red(p, x, y);
    assert.ok(v / 255 >= 0.989 - 0.5 / 255, `${order}: the aligned slice holds only ${(v / 255 * 100).toFixed(2)} %`);
    assert.ok(v >= 252 && v <= 253, `${order}: expected 255 / 1.01 = 252.5, got ${v}`);
    near(sumsOf(p)[at(x, y)], 1.01, 1e-6, `${order}: total weight`);
    assert.equal(p.cls[at(x, y)], PixClass.Aligned);
  }
  const p = fresh();
  p.paint(slice(100, 0, 'aligned', 0), 0); p.paint(slice(100, 0, 'sensor', 255), 0);
  assert.ok(red(p, colOf(100), rowOf(10)) <= 255 * 0.011 + 0.5, 'a sensor slice under an aligned one stays under 1.1 %');
});

test('class is a weight, never a reset: the aligned/blurred seam is a graded edge, whatever the paint order', () => {
  // Aligned A (colour 0) at 100 and blurred B (colour 255) at 104: B's weight is 0.1 x its feather, so the seam falls in the
  // last half degree of A's feather and takes about a column and a half. A reset flips the pixel at one column. The blurred
  // class is used because an aligned/sensor seam is nearly a hard edge in a correct build too (230 of 255, 5.4 times the
  // feather case): the sensor's 0.01 loses to the aligned feather until its last 0.02 degree, which is under a column, so that
  // pair cannot tell a weight from a reset by gradient. The value test above tells them apart.
  const seam = (clsA: KeyClass, clsB: KeyClass, order: 'ab' | 'ba') => {
    const p = fresh();
    const a = slice(100, 0, clsA, 0), b = slice(104, 0, clsB, 255);
    if (order === 'ab') { p.paint(a, 0); p.paint(b, 0); } else { p.paint(b, 0); p.paint(a, 0); }
    return p;
  };
  const y = rowOf(10);
  const model = (x: number) => {
    const offA = Math.abs(colAz(x) - 100), offB = Math.abs(colAz(x) - 104);
    const wa = featherWeight(offA) * CLASS_WEIGHT.aligned, wb = featherWeight(offB) * CLASS_WEIGHT.blurred;
    return 255 * wb / (wa + wb);
  };
  const row = (p: BandPanorama) => { const out: number[] = []; for (let x = colOf(97.5); x <= colOf(106.5); x++) out.push(red(p, x, y)); return out; };
  const maxStep = (r: number[]) => Math.max(...r.slice(1).map((v, i) => Math.abs(v - r[i])));
  const featherStep = maxStep(row(seam('aligned', 'aligned', 'ab')));      // two aligned slices: the plain 2-degree feather
  near(featherStep, 255 / 6, 1, 'the plain feather steps 255 / 6 per column');
  for (const order of ['ab', 'ba'] as const) {
    const p = seam('aligned', 'blurred', order);
    const step = maxStep(row(p));
    assert.ok(step < 3 * featherStep, `${order}: seam gradient ${step} is ${(step / featherStep).toFixed(2)}x the feather case, 3x or more is a hard edge`);
    assert.ok(step < 0.6 * 255, `${order}: the seam steps ${step} of 255 in one column`);
    for (let x = colOf(98.5); x <= colOf(105.5); x++) near(red(p, x, y), model(x), 1, `${order}: column ${x} follows the weights`);
  }
  // Blurred first, aligned second is the same seam seen from the other side: graded as well, and the classes can be swapped.
  const flipped = seam('blurred', 'aligned', 'ab');
  const flippedModel = (x: number) => {
    const wa = featherWeight(Math.abs(colAz(x) - 100)) * CLASS_WEIGHT.blurred, wb = featherWeight(Math.abs(colAz(x) - 104)) * CLASS_WEIGHT.aligned;
    return 255 * wb / (wa + wb);
  };
  for (let x = colOf(98.5); x <= colOf(105.5); x++) near(red(flipped, x, y), flippedModel(x), 1, `flipped: column ${x} follows the weights`);
  assert.ok(maxStep(row(flipped)) < 3 * featherStep, 'the flipped seam is graded too');
});

// ---- Alpha, trailing fill -----------------------------------------------------

test('alpha is only 0 or 255, after painting, trailing fill, shiftAzimuth and shiftRaster', () => {
  const p = fresh();
  p.paint(slice(10, 24, 'aligned', TEXTURE), 0);
  p.paint(slice(14, 24, 'blurred', TEXTURE, { trailingDeg: 6, trailingSide: -1 }), 0);
  p.paint(slice(200, 5, 'sensor', TEXTURE, { sigmaDeg: 3 }), 0);
  p.paint(slice(0, 80, 'aligned', TEXTURE), 0);
  const onlyBinary = (rgba: Uint8ClampedArray, what: string) => {
    let seen = 0;
    for (let i = 3; i < rgba.length; i += 4) { assert.ok(rgba[i] === 0 || rgba[i] === 255, `${what}: alpha ${rgba[i]}`); if (rgba[i]) seen++; }
    assert.ok(seen > 1000, `${what}: raster is nearly empty`);
  };
  onlyBinary(p.rgba, 'painted');
  onlyBinary(shiftRaster(p.rgba, 0.5), 'shiftRaster(0.5)');
  onlyBinary(shiftRaster(p.rgba, -37.3), 'shiftRaster(-37.3)');
  p.shiftAzimuth(1.1111);
  onlyBinary(p.rgba, 'shiftAzimuth(1.1111)');
});

test('a 10-degree trailing fill leaves no hole, on either side, and weighs 1 up to 1 + trailing', () => {
  // The previous slice A at 100 paints to 103. B, 14 degrees on (d - S = 10), paints its trailing side to meet it. The
  // strip is +-14 degrees here so the test is of the raster's weights; with the usual +-10 strip see the next test.
  const wide = 14;
  const rows = [-9, 0, 15, 30].map(rowOf);
  const ringOf = (side: -1 | 1, trailing: number) => {
    const p = fresh();
    const aAz = side === -1 ? 100 : 114, bAz = side === -1 ? 114 : 100;
    p.paint(slice(aAz, 0, 'aligned', 50, {}, wide), 0);
    p.paint(slice(bAz, 0, 'aligned', 200, { trailingDeg: trailing, trailingSide: side }, wide), 0);
    return p;
  };
  for (const side of [-1, 1] as const) {
    const p = ringOf(side, 10);
    for (let x = colOf(100.5); x <= colOf(113.5); x++) for (const y of rows) assert.equal(alpha(p, x, y), 255, `side ${side}: hole at ${colAz(x)} alt ${altOfRow(y)}`);
    const bare = ringOf(side, 0);
    assert.equal(alpha(bare, colOf(107), rowOf(0)), 0, 'without trailing the gap is there');
  }
  // B alone: weight 1 up to 1 + trailing on the trailing side, then linear to 0 at 3 + trailing, and the other side untouched.
  for (const side of [-1, 1] as const) {
    const p = fresh();
    p.paint(slice(114, 0, 'aligned', 200, { trailingDeg: 10, trailingSide: side }, wide), 0);
    for (let x = colOf(98); x <= colOf(130); x++) {
      const off = (colAz(x) - 114) * side;                 // positive on the trailing side
      const expected = off >= 0 ? (off <= 11 ? 1 : off < 13 ? (13 - off) / 2 : 0) : featherWeight(off);
      near(sumsOf(p)[at(x, rowOf(10))], expected, 1e-6, `side ${side} offset ${off}`);
    }
  }
  const p = fresh();
  p.paint(slice(114, 0, 'aligned', 200, { trailingDeg: 10, trailingSide: 0 }, wide), 0);
  assert.equal(alpha(p, colOf(105), rowOf(0)), 0, 'trailingSide 0 paints no extra width');
});

test('paint never invents pixels outside the strip: a +-10 degree strip stops a 10-degree trailing fill short of its 13-degree limit', () => {
  // SPEC-v2 4.3 rule 6 allows trailingDeg up to 10 (limit 13 degrees) while 4.11 keeps +-10 degrees of strip. The raster
  // paints what the strip holds and skips the rest, so d - S = 10 leaves the last degree grey. Recorded, not hidden.
  const p = fresh();
  p.paint(slice(100, 0, 'aligned', 50), 0);
  p.paint(slice(114, 0, 'aligned', 200, { trailingDeg: 10, trailingSide: -1 }), 0);
  assert.equal(alpha(p, colOf(103.5), rowOf(0)), 0, 'the 103..104 degree column is outside both feather and strip');
  assert.equal(alpha(p, colOf(104.3), rowOf(0)), 255, 'from the strip edge (B - 10) on, B paints');
  assert.equal(alpha(p, colOf(102.5), rowOf(0)), 255, 'A still paints to its own 3-degree limit');
});

// ---- cls, sigma, coverage, firstSeen --------------------------------------------

test('cls is the highest class painted with feather >= 0.5; pixels held only by a feather edge are observed but unclassed', () => {
  const p = fresh();
  p.paint(slice(100, 0, 'sensor', 10), 0);
  const y = rowOf(0);
  for (let x = colOf(97.5); x <= colOf(102.5); x++) {
    const off = Math.abs(colAz(x) - 100);
    if (off < 3) assert.equal(alpha(p, x, y), 255, `observed at offset ${off}`);
    assert.equal(p.cls[at(x, y)], off <= 2 ? PixClass.Sensor : PixClass.None, `cls at offset ${off}`);
  }
  p.paint(slice(101, 0, 'aligned', 10), 0);                                // wider class on top
  p.paint(slice(100, 0, 'blurred', 10), 0);                                // lower class afterwards never lowers it
  assert.equal(p.cls[at(colOf(101), y)], PixClass.Aligned);
  assert.equal(p.cls[at(colOf(99), y)], PixClass.Aligned);                 // 99 is 2 degrees from slice 101
  assert.equal(p.cls[at(colOf(98.2), y)], PixClass.Blurred);               // blurred holds it, aligned does not (2.8 away)
  assert.equal(p.cls[at(colOf(97.2), y)], PixClass.None);
});

test('sigma is the minimum over slices with feather >= 0.5, in 0.02-degree units, 255 for none or 5.1 and above', () => {
  const p = fresh();
  assert.ok(p.sigma.every(v => v === 255), 'unpainted pixels read 255');
  p.paint(slice(100, 0, 'aligned', 10, { sigmaDeg: 0.5 }), 0);
  const y = rowOf(0);
  assert.equal(p.sigma[at(colOf(100), y)], 25);
  p.paint(slice(103, 0, 'aligned', 10, { sigmaDeg: 0.1 }), 0);             // 3 degrees on: both hold [101, 102] at >= 0.5
  assert.equal(p.sigma[at(colOf(101.5), y)], 5, 'the lower sigma wins where both hold the pixel');
  assert.equal(p.sigma[at(colOf(101.2), y)], 5);
  assert.equal(p.sigma[at(colOf(104.5), y)], 5);
  // The feather >= 0.5 gate. Slice 103 paints from 100 to 106 and holds a pixel at 0.5 or more only within 2 degrees (101 to 105).
  // At 2.5 degrees out (100.5 and 105.5) its feather is 0.25: it paints the pixel but its sigma of 5 must not be written there.
  assert.equal(p.sigma[at(colOf(100.5), y)], 25, 'slice 103 paints this pixel at feather 0.25: its lower sigma does not count');
  assert.equal(alpha(p, colOf(105.5), y), 255, 'only slice 103 paints this pixel, at feather 0.25');
  assert.equal(p.sigma[at(colOf(105.5), y)], 255, 'a pixel held only under a feather of 0.5 has no sigma');
  // The threshold itself sits between 0.417 and 0.583: columns 302 and 315 are 2.17 degrees from 103, columns 303 and 314 are 1.83.
  assert.equal(p.sigma[at(302, y)], 25, 'column 302, feather 0.417 from 103, keeps the sigma of slice 100');
  assert.equal(p.sigma[at(303, y)], 5, 'column 303, feather 0.583 from 103, takes its sigma');
  assert.equal(p.sigma[at(314, y)], 5, 'column 314, feather 0.583 from 103, takes its sigma');
  assert.equal(p.sigma[at(315, y)], 255, 'column 315, feather 0.417 from 103, has no sigma');
  assert.equal(p.sigma[at(colOf(99), y)], 25, 'slice 103 does not reach this pixel (3.83 degrees away): slice 100 alone sets it');
  p.paint(slice(101, 0, 'sensor', 10, { sigmaDeg: 2 }), 0);               // a larger sigma never raises it
  assert.equal(p.sigma[at(colOf(101.5), y)], 5);
  const q = fresh();
  q.paint(slice(100, 0, 'aligned', 10, { sigmaDeg: 5.1 }), 0);
  assert.equal(q.sigma[at(colOf(100), y)], 255, '5.1 degrees is 255');
  q.paint(slice(100, 0, 'aligned', 10, { sigmaDeg: 99 }), 0);
  assert.equal(q.sigma[at(colOf(100), y)], 255, 'a huge sigma saturates');
  q.paint(slice(100, 0, 'aligned', 10, { sigmaDeg: 0 }), 0);
  assert.equal(q.sigma[at(colOf(100), y)], 0);
  const r = fresh();
  r.paint(slice(100, 0, 'aligned', 10, { sigmaDeg: Number.NaN }), 0);
  assert.equal(r.sigma[at(colOf(100), y)], 255, 'a NaN sigma is no sigma');
});

test('coverage: 720 cells of 0.5 degree hold the highest class painted between altitude -2 and +10', () => {
  assert.equal(new BandPanorama().coverage.length, PROFILE_BINS);
  const p = fresh();
  p.paint(slice(100, 0, 'aligned', 10), 0);
  // cls is Aligned for the columns whose centres lie within 2 degrees of 100, which is 294..305: cells 196..203 (98.0 to 102.0).
  const lit = (q: BandPanorama) => [...q.coverage].map((v, i) => v ? i : -1).filter(i => i >= 0);
  assert.deepEqual(lit(p), [196, 197, 198, 199, 200, 201, 202, 203]);
  assert.ok(p.coverage.every(v => v === 0 || v === PixClass.Aligned));
  p.paint(slice(104, 0, 'sensor', 10), 0);
  assert.equal(p.coverage[204], PixClass.Sensor, 'a sensor slice marks its cells');
  assert.equal(p.coverage[203], PixClass.Aligned, 'a cell keeps the highest class');
  p.paint(slice(104, 0, 'blurred', 10), 0);
  assert.equal(p.coverage[205], PixClass.Blurred);
  assert.equal(p.coverage[210], PixClass.Blurred);
  // The cell of a column is the one its centre falls in, so columns 3k, 3k+1 and 3k+2 go to cells 2k, 2k+1 and 2k+1. A slice at 100.4
  // holds columns 295 to 306 at feather >= 0.5, and 295 is 3 x 98 + 1: a mapping by column start would also light cell 196.
  const odd = fresh(); odd.paint(slice(100.4, 0, 'aligned', 10), 0);
  assert.deepEqual(lit(odd), [197, 198, 199, 200, 201, 202, 203, 204]);
  // The altitude band: a slice whose painted rows all lie above +10 or below -2 sets cls but not coverage.
  const inBand = (el: number) => { const q = fresh(); q.paint(slice(100, el, 'aligned', 10), 0); return lit(q).length; };
  // Near the cone's edge the 3-degree feather is narrower in azimuth (a pitched camera sees an azimuth step as a larger tangent), so 6 to 8 cells.
  assert.ok(inBand(34) >= 6, 'rows from 1.8 up are painted: some are inside +10');
  assert.equal(inBand(44), 0, 'rows from 11.8 up are all above +10');
  assert.ok(inBand(-32) >= 6, 'rows up to +0.2 are painted: some are inside -2');
  assert.equal(inBand(-40), 0, 'rows up to -7.8 are all below -2');
  const high = fresh(); high.paint(slice(100, 44, 'aligned', 10), 0);
  assert.ok(high.cls.some(v => v === PixClass.Aligned), 'cls is set even where coverage is not');
  // Every column maps to one cell and all 720 are reached (cells of one and two columns alternate).
  const ring = fresh();
  for (let az = 0; az < 360; az += 4) ring.paint(slice(az, 0, 'sensor', 10), 0);
  assert.ok(ring.coverage.every(v => v === PixClass.Sensor), 'a full ring of sensor slices covers every cell');
});

test('firstSeen is deciseconds since begin(), set once at the first w > 0, monotone, and survives clear()', () => {
  const p = new BandPanorama();
  p.begin(1000);
  p.paint(slice(100, 0, 'aligned', 10), 1000);
  const y = rowOf(0), a = at(colOf(100), y), edge = at(colOf(102.9), y);
  assert.equal(p.firstSeen[a], 1, 'never 0 once seen, even at the begin() instant');
  assert.equal(p.firstSeen[edge], 1, 'set at the first w > 0, a feather edge included');
  const first = p.firstSeen.slice();
  p.paint(slice(104, 0, 'aligned', 10), 1000 + 2340);
  assert.equal(p.firstSeen[a], 1, 'a later paint does not touch it');
  assert.equal(p.firstSeen[at(colOf(106), y)], 23, '2.34 s is 23 deciseconds');
  assert.equal(p.firstSeen[at(colOf(102.9), y)], 1, 'the shared edge keeps its first stamp');
  p.paint(slice(110, 0, 'aligned', 10), 1000 + 2340 + 5000);
  assert.equal(p.firstSeen[at(colOf(110), y)], 73);
  // Monotone: what a paint sets is never below anything set before it, and nothing already set changes.
  let ceiling = 0;
  const q = new BandPanorama(); q.begin(0);
  for (const [az, t] of [[0, 0], [40, 500], [20, 1200], [80, 1200], [60, 40000], [100, 40000]] as const) {
    const before = q.firstSeen.slice();
    q.paint(slice(az, 5, 'aligned', 10), t);
    for (let i = 0; i < before.length; i++) {
      if (before[i] !== 0) assert.equal(q.firstSeen[i], before[i], 'a stamp never changes');
      else if (q.firstSeen[i] !== 0) { assert.ok(q.firstSeen[i] >= ceiling, 'a new stamp is never below an earlier one'); }
    }
    for (const v of q.firstSeen) if (v > ceiling) ceiling = v;
  }
  assert.equal(ceiling, 400);
  // clear() wipes the pixels, keeps firstSeen, and a re-render does not re-stamp.
  p.clear();
  assert.equal(observed(p), 0);
  assert.ok(p.cls.every(v => v === 0) && p.sigma.every(v => v === 255) && p.coverage.every(v => v === 0));
  assert.equal(p.firstSeen[a], first[a]);
  assert.equal(p.firstSeen[at(colOf(110), y)], 73);
  p.paint(slice(100, 0, 'aligned', 10), 1000 + 90000);
  assert.equal(p.firstSeen[a], 1, 'the re-render leaves the original stamp');
  assert.equal(alpha(p, colOf(100), y), 255);
  // begin() is a new scan: the stamps go, and the clock restarts.
  p.begin(5000);
  assert.ok(p.firstSeen.every(v => v === 0));
  p.paint(slice(100, 0, 'aligned', 10), 5000 + 60);
  assert.equal(p.firstSeen[a], 1, 'a paint 60 ms in is stamped 1, never 0');
  p.paint(slice(110, 0, 'aligned', 10), 5000 + 150);
  assert.equal(p.firstSeen[at(colOf(110), y)], 2, '150 ms is 1.5 deciseconds, which rounds up to 2');
  // Saturation: 65535 deciseconds is the ceiling.
  const s = new BandPanorama(); s.begin(0);
  s.paint(slice(100, 0, 'aligned', 10), 1e9);
  assert.equal(s.firstSeen[a], 65535);
  // A raster painted without begin() uses its first paint as the origin.
  const t = new BandPanorama();
  t.paint(slice(100, 0, 'aligned', 10), 123456);
  assert.equal(t.firstSeen[a], 1);
});

// ---- Geometry: footprint, limits, sampling ------------------------------------------

test('footprint: one slice touches 3,902 +- 2 % raster pixels for the default lens at p1 (3,710 on the S25)', () => {
  const p1 = (shortDeg: number) => {
    const fNorm = 0.5 / Math.tan(shortDeg * DEG / 2), k0 = intrinsicsAt(0, 180, 320, fNorm);
    const longHalf = Math.atan(k0.cy / k0.f) / DEG;
    const h = Math.atan(0.9 * Math.tan(longHalf * DEG)) / DEG;
    return { k0, p1: Math.min(30, Math.max(12, h - 8)), h };
  };
  const cases: [string, number, number, number][] = [['default', 42.9957, 3902, 24.22], ['S25', 41.14, 3710, 22.98]];
  for (const [name, shortDeg, spec, pitch] of cases) {
    const { k0, p1: target, h } = p1(shortDeg);
    near(target, pitch, 0.02, `${name} p1`);
    const p = fresh();
    const rect = p.paint(slice(0, target, 'aligned', 128, { k0, ...makeStrip(10, uniform(128), k0) }), 0);
    const n = observed(p);
    assert.ok(Math.abs(n - spec) <= spec * 0.02, `${name}: footprint ${n} is not ${spec} +- 2 %`);
    let inside = 0;
    for (let y = 0; y < PANO_H; y++) for (let x = 0; x < PANO_W; x++) if (alpha(p, x, y) && inRect(rect, x, y)) inside++;
    assert.equal(inside, n, `${name}: the returned rectangle holds every painted pixel`);
    const ceiling = target + h;
    assert.ok(rect.y >= Math.floor((90 - ceiling) * 2.99) - 1 && rect.y <= Math.ceil((90 - ceiling) * 2.99) + 1, `${name}: top row ${rect.y} for a ceiling of ${ceiling}`);
  }
});

test('the vertical limit is 0.9 tan(long / 2): the top row of a painted column is where the cone says', () => {
  const p = fresh();
  p.paint(slice(100, 0, 'aligned', 128), 0);
  const tanV = 0.9 * K0.cy / K0.f;
  for (const x of [colOf(100), colOf(101.5), colOf(98.5)]) {
    const dAz = colAz(x) - 100;
    const limitAlt = Math.atan(tanV * Math.cos(dAz * DEG)) / DEG;           // tan(alt) <= tanV cos(dAz) at pitch 0
    let top = 0; while (!alpha(p, x, top)) top++;
    assert.equal(top, Math.ceil((90 - limitAlt) * (PANO_H - 1) / 100), `top row of column ${x} (limit ${limitAlt.toFixed(2)} degrees)`);
    let bottom = PANO_H - 1; while (!alpha(p, x, bottom)) bottom--;
    assert.equal(bottom, PANO_H - 1, 'the lower limit (-32) is below the raster, so the run reaches -10');
  }
  assert.ok(Math.atan(tanV) / DEG > 32.2 && Math.atan(tanV) / DEG < 32.3, 'h = atan(0.9 tan 35) = 32.22');
});

test('inverse mapping and bilinear sampling agree with an independent projection at pitch 24, to one colour level (0.33 to 1 strip pixel)', () => {
  // Strip pixel (col, row) holds R = 2 col, G = row, B = 3 col, all linear, so a bilinear sample at continuous (u, v) is exact
  // and the expected colour is a closed form of the projected position. The projection here is rotation.ts, not the paint's own.
  const az = 135, el = 24;
  const fill: Fill = (col, row) => [2 * col, Math.min(255, row), 3 * col];
  const s = slice(az, el, 'aligned', fill);
  const p = fresh();
  p.paint(s, 0);
  const worldToCam = qinv(s.pose);
  let checked = 0;
  for (let y = 0; y < PANO_H; y++) for (let x = 0; x < PANO_W; x++) {
    if (!alpha(p, x, y)) continue;
    const a = colAz(x) * DEG, e = altOfRow(y) * DEG;
    const ray = qrotate(worldToCam, [Math.sin(a) * Math.cos(e), Math.cos(a) * Math.cos(e), Math.sin(e)]);
    const proj = projectCamera(K0, ray);
    assert.ok(proj, 'a painted pixel projects');
    const [px, py] = proj!;
    const u = px - s.stripX0 - 0.5, v = py - 0.5;
    if (u < 0 || u > s.stripW - 1 || v < 0 || v > 254) continue;
    // Uint8ClampedArray rounds the exact mean; one level of slack covers the float32 sums.
    near(red(p, x, y), 2 * u, 1, `R at (${x}, ${y})`);
    near(p.rgba[at(x, y) * 4 + 1], v, 1, `G at (${x}, ${y})`);
    near(p.rgba[at(x, y) * 4 + 2], 3 * u, 1, `B at (${x}, ${y})`);
    checked++;
  }
  assert.ok(checked > 1500, `only ${checked} pixels compared`);
});

test('gain multiplies the colour and a strip narrower than the cone skips what it does not hold', () => {
  const p = fresh();
  p.paint(slice(100, 0, 'aligned', 100, { gainRGB: [1.5, 1, 0.5] }), 0);
  const x = colOf(100), y = rowOf(5);
  assert.deepEqual([p.rgba[at(x, y) * 4], p.rgba[at(x, y) * 4 + 1], p.rgba[at(x, y) * 4 + 2]], [150, 100, 50]);
  const clipped = fresh();
  clipped.paint(slice(100, 0, 'aligned', 255, { gainRGB: [2, 2, 2] }), 0);
  assert.equal(red(clipped, x, y), 255, 'the product clamps at 255');
  // A strip of +-1.5 degrees: the 3-degree feather has pixels beyond it, and they are skipped, not smeared.
  const narrow = fresh();
  narrow.paint(slice(100, 0, 'aligned', 90, {}, 1.5), 0);
  assert.equal(alpha(narrow, colOf(100), y), 255);
  assert.equal(alpha(narrow, colOf(101), y), 255);
  assert.equal(alpha(narrow, colOf(102), y), 0, '2 degrees is outside a +-1.5 strip');
  assert.equal(alpha(narrow, colOf(98), y), 0);
});

// ---- Dirty rectangles --------------------------------------------------------------------

test('paint returns the tight rectangle it touched, takeDirty hands the pending ones over once, and a rectangle wraps past column 1079', () => {
  const p = fresh();
  p.takeDirty();                                                          // begin() marked everything; start from clean
  const before = p.rgba.slice();
  const r1 = p.paint(slice(0, 0, 'aligned', 128), 0);                    // straddles the seam: columns 1070..1079 and 0..9
  assert.ok(r1.x + r1.w > PANO_W, `expected a wrapping rectangle, got ${JSON.stringify(r1)}`);
  assert.ok(r1.x >= 0 && r1.x < PANO_W && r1.w <= PANO_W);
  let changed = 0;
  for (let y = 0; y < PANO_H; y++) for (let x = 0; x < PANO_W; x++) if (p.rgba[at(x, y) * 4 + 3] !== before[at(x, y) * 4 + 3]) {
    changed++; assert.ok(inRect(r1, x, y), `pixel (${x}, ${y}) changed outside ${JSON.stringify(r1)}`);
  }
  assert.ok(changed > 2000, `${changed} pixels changed`);
  assert.equal(alpha(p, PANO_W - 1, rowOf(0)), 255); assert.equal(alpha(p, 0, rowOf(0)), 255);
  // Tight: every edge of the rectangle touches a painted pixel.
  const touches = (pred: (x: number, y: number) => boolean) => { for (let y = 0; y < PANO_H; y++) for (let x = 0; x < PANO_W; x++) if (alpha(p, x, y) && pred(x, y)) return true; return false; };
  assert.ok(touches((_x, y) => y === r1.y) && touches((_x, y) => y === r1.y + r1.h - 1), 'top and bottom rows are painted');
  assert.ok(touches((x) => x === r1.x) && touches((x) => x === (r1.x + r1.w - 1) % PANO_W), 'left and right columns are painted');
  const r2 = p.paint(slice(200, 0, 'aligned', 128), 0);
  assert.deepEqual(p.takeDirty(), [r1, r2]);
  assert.deepEqual(p.takeDirty(), [], 'taken once');
  p.paint(slice(100, -80, 'aligned', 128), 0);                           // looking at the floor: nothing in the raster
  assert.deepEqual(p.takeDirty(), []);
  // begin(), clear() and shiftAzimuth() change every pixel, so the whole raster is dirty after each.
  for (const act of [() => p.clear(), () => p.begin(5), () => p.shiftAzimuth(0.5)]) {
    p.takeDirty(); act();
    assert.deepEqual(p.takeDirty(), [{ x: 0, y: 0, w: PANO_W, h: PANO_H }]);
  }
  // The pending list is bounded: past 512 rectangles it collapses to the whole raster.
  p.takeDirty();
  for (let i = 0; i < 600; i++) p.paint(slice(i % 360, 0, 'sensor', 128), 0);
  const pending = p.takeDirty();
  assert.ok(pending.length <= 512, `${pending.length} pending rectangles`);
  assert.ok(pending.some(r => r.w === PANO_W && r.h === PANO_H), 'the collapse is the whole raster');
});

test('polar and unreachable slices: a slice holding the zenith paints every azimuth of row 0, one at the floor paints nothing', () => {
  for (const [az, el] of [[0, 90], [200, 88], [310, 85]] as const) {
    const p = fresh();
    const rect = p.paint(slice(az, el, 'aligned', 128), 0);
    for (let x = 0; x < PANO_W; x++) assert.equal(alpha(p, x, 0), 255, `elevation ${el}: the zenith row is unpainted at column ${x}`);
    assert.equal(rect.w, PANO_W);
    assert.equal(rect.y, 0);
    assert.ok(rect.x + rect.w <= 2 * PANO_W);
  }
  const floor = fresh();
  const none = floor.paint(slice(100, -80, 'aligned', 128), 0);
  assert.deepEqual(none, { x: 0, y: 0, w: 0, h: 0 });
  assert.equal(observed(floor), 0);
  const behind = fresh();                                                   // pitch -50: its top, at -17.8, is below the raster
  assert.equal(behind.paint(slice(100, -50, 'aligned', 128), 0).h, 0);
});

// ---- observedRun ----------------------------------------------------------------------------

test('observedRun is the painted run containing altitude 0, null when that pixel was never seen', () => {
  const p = fresh();
  assert.equal(p.observedRun(colOf(100)), null);
  p.paint(slice(100, 0, 'aligned', 128), 0);
  p.paint(slice(100, 70, 'aligned', 128), 0);                              // a second run well above, from altitude 37.8
  const x = colOf(100), run = p.observedRun(x)!;
  assert.ok(run, 'the run containing 0');
  near(run.bottomAlt, -10, 1e-9, 'the run reaches the bottom row (-10)');
  const limit = Math.atan(0.9 * K0.cy / K0.f * Math.cos((colAz(x) - 100) * DEG)) / DEG;
  assert.ok(run.topAlt <= limit && run.topAlt > limit - 0.34, `top ${run.topAlt} against the limit ${limit}`);
  assert.ok(run.topAlt < 33, 'the higher run is a different run');
  assert.ok(alpha(p, x, rowOf(60)) === 255, 'the high run is there');
  // A column away from the slice, and one whose only paint is above altitude 0.
  assert.equal(p.observedRun(colOf(200)), null);
  const high = fresh(); high.paint(slice(100, 45, 'aligned', 128), 0);
  assert.equal(high.observedRun(colOf(100)), null, 'painted from +12.8 up only');
  // x wraps like a column index does.
  const seam = fresh(); seam.paint(slice(0, 0, 'aligned', 128), 0);
  assert.deepEqual(seam.observedRun(-1), seam.observedRun(PANO_W - 1));
  assert.deepEqual(seam.observedRun(PANO_W), seam.observedRun(0));
  assert.ok(seam.observedRun(0));
  // The run is cut at an unobserved pixel: erase a row inside the run and the run above 0 is what remains.
  const cut = fresh(); cut.paint(slice(100, 0, 'aligned', 128), 0);
  cut.rgba[at(x, rowOf(10)) * 4 + 3] = 0;
  const part = cut.observedRun(x)!;
  near(part.topAlt, altOfRow(rowOf(10) + 1), 1e-9, 'the run stops under the erased row');
  // The run is anchored at the row nearest altitude 0, which is row 269 (+0.03 degrees; rows are 0.33 degrees apart). A slice whose
  // bottom edge is 0.2 degrees below 0 holds that row and a slice whose bottom edge is 0.2 degrees above it does not, so a column
  // whose paint ends within half a degree of 0 tells an anchor one row off, let alone three, from the right one.
  const hDeg = Math.atan(0.9 * K0.cy / K0.f) / DEG;                       // the vertical limit, 32.22 degrees
  const edgeRun = (edge: number) => { const q = fresh(); q.paint(slice(colAz(x), hDeg + edge, 'aligned', 128), 0); return q.observedRun(x); };
  const below = edgeRun(-0.2), lower = edgeRun(-0.5);
  assert.ok(below && lower, 'a run that ends below altitude 0 contains it');
  near(below!.bottomAlt, altOfRow(269), 1e-9, 'the paint ends at row 269 (+0.03)');
  near(lower!.bottomAlt, altOfRow(270), 1e-9, 'the paint ends at row 270 (-0.30)');
  assert.equal(edgeRun(0.2), null, 'a run that ends 0.2 degrees above altitude 0 does not contain it');
  assert.equal(edgeRun(0.5), null, 'nor one that ends 0.5 degrees above');
});

// ---- Shifts ------------------------------------------------------------------------------------

/** A textured, partly observed band: 50 slices 4 degrees apart at pitch 0 from azimuth 340 through the seam to 176. Rows above 32 degrees
 *  and azimuths 179 to 337 stay unseen, so shifting meets both edges of an observed region and the 0/1080 seam inside it. */
function texturedRing() {
  const p = fresh();
  for (let k = -5; k <= 44; k++) {
    const az = (k * 4 + 360) % 360;
    p.paint(slice(az, 0, k % 2 ? 'blurred' : 'aligned', TEXTURE, { sigmaDeg: 0.2 + (az % 12) / 10 }), 0);
  }
  return p;
}

test('shiftAzimuth(0.5) is an exact 1.5-column resample of rgba, cls and sigma, wrapping the seam', () => {
  const p = texturedRing();
  const rgba = p.rgba.slice(), cls = p.cls.slice(), sigma = p.sigma.slice();
  assert.ok(observed(p) > 40000);
  p.shiftAzimuth(0.5);                                                    // out[x] = (in[x - 1] + in[x - 2]) / 2
  const expectRgba = new Uint8ClampedArray(rgba.length);
  let blended = 0, dropped = 0;
  for (let y = 0; y < PANO_H; y++) for (let x = 0; x < PANO_W; x++) {
    const a = at((x - 1 + PANO_W) % PANO_W, y), b = at((x - 2 + PANO_W) % PANO_W, y), o = at(x, y);
    if (rgba[a * 4 + 3] && rgba[b * 4 + 3]) {
      for (let c = 0; c < 3; c++) expectRgba[o * 4 + c] = (rgba[a * 4 + c] + rgba[b * 4 + c]) / 2;
      expectRgba[o * 4 + 3] = 255; blended++;
    } else if (rgba[a * 4 + 3] || rgba[b * 4 + 3]) dropped++;
    assert.equal(p.cls[o], Math.min(cls[a], cls[b]), `cls at (${x}, ${y})`);
    assert.equal(p.sigma[o], Math.max(sigma[a], sigma[b]), `sigma at (${x}, ${y})`);
  }
  assert.deepEqual(p.rgba, expectRgba);
  assert.ok(blended > 40000, 'blended pixels were compared');
  assert.ok(dropped > 0, 'the rows where the observed band ends exercise the half-seen rule');
  assert.deepEqual(shiftRaster(rgba, 0.5), expectRgba, 'shiftRaster is the same resample');
  // Coverage is recomputed from the shifted cls.
  const brute = new Uint8Array(PROFILE_BINS);
  for (let y = 0; y < PANO_H; y++) { const alt = altOfRow(y); if (alt > 10 || alt < -2) continue; for (let x = 0; x < PANO_W; x++) { const c = Math.floor((x + 0.5) / 1.5); brute[c] = Math.max(brute[c], p.cls[at(x, y)]); } }
  assert.deepEqual(p.coverage, brute);
  // The accumulators moved with it: painting on the shifted raster blends into the shifted content, not the old one.
  near(sumsOf(p)[at(100, rowOf(0))], (sumsOf(texturedRing())[at(99, rowOf(0))] + sumsOf(texturedRing())[at(98, rowOf(0))]) / 2, 1e-5);
});

test('shiftAzimuth by whole columns is an exact copy, a negative shift runs the other way, and a full turn is the identity', () => {
  const p = texturedRing();
  const rgba = p.rgba.slice(), cls = p.cls.slice(), sigma = p.sigma.slice();
  p.shiftAzimuth(1);                                                      // 3 columns, toward larger azimuth
  for (let y = 0; y < PANO_H; y += 5) for (let x = 0; x < PANO_W; x++) {
    const src = at((x - 3 + PANO_W) % PANO_W, y), o = at(x, y);
    for (let c = 0; c < 4; c++) assert.equal(p.rgba[o * 4 + c], rgba[src * 4 + c]);
    assert.equal(p.cls[o], cls[src]); assert.equal(p.sigma[o], sigma[src]);
  }
  p.shiftAzimuth(-1);
  assert.deepEqual(p.rgba, rgba); assert.deepEqual(p.cls, cls); assert.deepEqual(p.sigma, sigma);
  p.shiftAzimuth(360); p.shiftAzimuth(0);
  assert.deepEqual(p.rgba, rgba);
  p.shiftAzimuth(-1 / 3);                                                 // one column back: 1/3 degree must not become a fraction
  for (let y = 0; y < PANO_H; y += 7) for (let x = 0; x < PANO_W; x++) {
    const src = at((x + 1) % PANO_W, y);
    for (let c = 0; c < 4; c++) assert.equal(p.rgba[at(x, y) * 4 + c], rgba[src * 4 + c]);
  }
  // A shift adds degrees to azimuth: a feature at azimuth 100 is at 130 after shiftAzimuth(30).
  const mark = fresh(); mark.paint(slice(100, 0, 'aligned', 200), 0);
  mark.shiftAzimuth(30);
  assert.equal(alpha(mark, colOf(130), rowOf(0)), 255); assert.equal(alpha(mark, colOf(100), rowOf(0)), 0);
  // firstSeen belongs to the scan frame and does not move.
  assert.ok(mark.firstSeen[at(colOf(100), rowOf(0))] > 0 && mark.firstSeen[at(colOf(130), rowOf(0))] === 0);
});

test('shiftRaster is pure: it returns a new raster, leaves its input alone, and checks the size', () => {
  const p = texturedRing();
  const input = p.rgba.slice();
  const out = shiftRaster(input, 12.34);
  assert.notEqual(out, input); assert.deepEqual(input, p.rgba);
  assert.equal(out.length, PANO_W * PANO_H * 4);
  assert.deepEqual(shiftRaster(input, 0), input);
  assert.notEqual(shiftRaster(input, 0), input);
  assert.deepEqual(shiftRaster(shiftRaster(input, 40), -40), input, 'whole-column shifts are exactly reversible');
  assert.throws(() => shiftRaster(new Uint8ClampedArray(12), 1), RangeError);
  assert.throws(() => shiftRaster(input, Number.NaN), RangeError);
  assert.throws(() => p.shiftAzimuth(Infinity), RangeError);
});

// ---- toPNG, memory, guards ---------------------------------------------------------------

test('toPNG draws the raster at PANO_W x PANO_H and returns the canvas data URL', () => {
  const p = texturedRing();
  let image: { data: Uint8ClampedArray; width: number; height: number } | null = null;
  let put: { x: number; y: number } | null = null, kind = '', type = '';
  const canvas = {
    width: 1, height: 1,
    getContext(k: string) {
      kind = k;
      return {
        createImageData(w: number, h: number) { image = { data: new Uint8ClampedArray(w * h * 4), width: w, height: h }; return image; },
        putImageData(img: unknown, x: number, y: number) { assert.equal(img, image); put = { x, y }; },
      };
    },
    toDataURL(t: string) { type = t; return 'data:image/png;base64,QUJD'; },
  } as unknown as HTMLCanvasElement;
  assert.equal(p.toPNG(canvas), 'data:image/png;base64,QUJD');
  assert.equal(kind, '2d'); assert.equal(type, 'image/png');
  assert.equal(canvas.width, PANO_W); assert.equal(canvas.height, PANO_H);
  assert.deepEqual(put, { x: 0, y: 0 });
  assert.deepEqual(image!.data, p.rgba);
  const noContext = { width: 0, height: 0, getContext: () => null, toDataURL: () => '' } as unknown as HTMLCanvasElement;
  assert.throws(() => p.toPNG(noContext), /Could not prepare the panorama/);
});

test('memory is the 4.14 raster: RGBA 1.30 MB, Float32 sums 5.18 MB, class 0.32, sigma 0.32, firstSeen 0.65 = 7.77 MB', () => {
  const p = new BandPanorama();
  assert.equal(p.rgba.byteLength, 1_296_000); assert.equal(p.cls.byteLength, 324_000);
  assert.equal(p.sigma.byteLength, 324_000); assert.equal(p.firstSeen.byteLength, 648_000);
  assert.ok(p['sumR'] instanceof Float32Array && p['sumG'] instanceof Float32Array && p['sumB'] instanceof Float32Array && p['sumW'] instanceof Float32Array);
  const sums = p['sumR'].byteLength + p['sumG'].byteLength + p['sumB'].byteLength + p['sumW'].byteLength;
  assert.equal(sums, 5_184_000);
  near((p.rgba.byteLength + sums + p.cls.byteLength + p.sigma.byteLength + p.firstSeen.byteLength) / 1e6, 7.77, 0.01);
  assert.ok(p.sigma.every(v => v === 255) && p.rgba.every(v => v === 0) && p.firstSeen.every(v => v === 0));
});

test('paint refuses a malformed slice before it changes anything', () => {
  const p = fresh();
  p.paint(slice(100, 0, 'aligned', 128), 0);
  p.takeDirty();                                                          // begin() and the good paint left rectangles pending; start from clean
  const rgba = p.rgba.slice(), w = p['sumW'].slice();
  const good = slice(100, 0, 'aligned', 200);
  assert.throws(() => p.paint({ ...good, strip: good.strip.subarray(0, 100) }, 0), RangeError);
  assert.throws(() => p.paint({ ...good, gainRGB: [1, Number.NaN, 1] }, 0), RangeError);
  assert.throws(() => p.paint({ ...good, gainRGB: [1, 1, Infinity] }, 0), RangeError);
  assert.throws(() => p.paint({ ...good, k0: { ...K0, f: 0 } }, 0), RangeError);
  assert.throws(() => p.paint({ ...good, pose: [Number.NaN, 0, 0, 0] }, 0), RangeError);
  assert.deepEqual(p.rgba, rgba); assert.deepEqual(p['sumW'], w);
  assert.deepEqual(p.takeDirty(), [], 'a refused paint marks nothing dirty');
});

console.log(`bandPanorama.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
