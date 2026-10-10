// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { decimalYear, declinationDeg } from '../wmm';
import { WMM2025 } from '../wmm2025';

// The declination that turns the compass's magnetic azimuths into true ones.
// The values below are the model authority's own: NOAA NCEI publishes test
// tables with the WMM2025 coefficients so that an implementation can prove it
// reproduces the model. Both tables are used, at their own locations only, so
// no site of ours appears in this file (the locations are the model's
// synthetic points, spread over the globe, over height 0 to 100 km and over
// 2025.0 to 2029.5, which exercises the secular variation).
//
//   * TABLE_WEB: 12 rows, WMM2025_TEST_VALUES.txt, from
//     https://www.ncei.noaa.gov/sites/default/files/2025-02/WMM2025_TEST_VALUES.txt
//     (md5 f3d2e3aee4e3c19017b898bb7ac5829a), column 11, Declination. Its
//     longitudes run 0 to 360.
//   * TABLE_ZIP: 100 rows, WMM2025_TestValues.txt, from WMM2025COF.zip
//     (md5 19150107978e49f6e3fb539451660b41), column 5, declination.
//
// Every number was copied from those files by script. Rows are
//   [decimal year, height above WGS-84 (km), geodetic latitude, geodetic
//    longitude, declination (degrees east)].
//
// What each test pins, and the mutant it exists to catch. Flipping the sign of
// each of the 283 non-zero values of the table in turn (g, h, gDot, hDot),
// measured against the 112 rows above:
//   * both tables within 0.1 degrees, the requirement: catches 151 of the 283.
//     Every main-field g and h up to degree 6 is among them, so the sign of
//     one Schmidt coefficient flipped in wmm2025.ts fails a test value. A
//     high-order term moves a declination by less than 0.1 degree and slips
//     through, which is why the next two tests exist;
//   * both tables within 0.01 degrees, the tables' own rounding step (the
//     worst agreement is 0.005): catches 259 of the 283, and also a slip in the
//     secular variation or in the geodetic to geocentric step;
//   * a digest of the whole table: catches all 283, and any other single edit.
//     No tolerance on a declination can do that, because a term of degree 10
//     to 12 can change by its whole value and move nothing the tables can see.
type Row = readonly [number, number, number, number, number];

const TABLE_WEB: readonly Row[] = [
  [2025.0, 0.0, 80.0, 0.0, 1.28],
  [2025.0, 0.0, 0.0, 120.0, -0.16],
  [2025.0, 0.0, -80.0, 240.0, 68.78],
  [2025.0, 100.0, 80.0, 0.0, 0.85],
  [2025.0, 100.0, 0.0, 120.0, -0.15],
  [2025.0, 100.0, -80.0, 240.0, 68.21],
  [2027.5, 0.0, 80.0, 0.0, 2.59],
  [2027.5, 0.0, 0.0, 120.0, -0.24],
  [2027.5, 0.0, -80.0, 240.0, 68.49],
  [2027.5, 100.0, 80.0, 0.0, 2.16],
  [2027.5, 100.0, 0.0, 120.0, -0.23],
  [2027.5, 100.0, -80.0, 240.0, 67.93],
];

const TABLE_ZIP: readonly Row[] = [
  [2025.0, 28, 89, -121, -99.77],
  [2025.0, 48, 80, -96, -29.91],
  [2025.0, 54, 82, 87, 54.89],
  [2025.0, 65, 43, 93, 0.5],
  [2025.0, 51, -33, 109, -5.49],
  [2025.0, 39, -59, -8, -15.75],
  [2025.0, 3, -50, -103, 27.96],
  [2025.0, 94, -29, -110, 15.74],
  [2025.0, 66, 14, 143, -0.19],
  [2025.0, 18, 0, 21, 1.29],
  [2025.5, 6, -36, -137, 20.28],
  [2025.5, 63, 26, 81, 0.51],
  [2025.5, 69, 38, -144, 12.93],
  [2025.5, 50, -70, -133, 57.21],
  [2025.5, 8, -52, -75, 14.91],
  [2025.5, 8, -66, 17, -33.14],
  [2025.5, 22, -37, 140, 9.28],
  [2025.5, 40, -12, -129, 10.76],
  [2025.5, 44, 33, -118, 11.1],
  [2025.5, 50, -81, -67, 28.13],
  [2026.0, 74, -57, 3, -22.51],
  [2026.0, 46, -24, -122, 14.01],
  [2026.0, 69, 23, 63, 1.17],
  [2026.0, 33, -3, -147, 9.71],
  [2026.0, 47, -72, -22, -6.32],
  [2026.0, 62, -14, 99, -1.43],
  [2026.0, 83, 86, -46, -30.61],
  [2026.0, 82, -64, 87, -81.74],
  [2026.0, 34, -19, 43, -14.98],
  [2026.0, 56, -81, 40, -59.77],
  [2026.5, 14, 0, 80, -3.1],
  [2026.5, 12, -82, -68, 29.79],
  [2026.5, 44, -46, -42, -11.36],
  [2026.5, 43, 17, 52, 1.19],
  [2026.5, 64, 10, 78, -1.53],
  [2026.5, 12, 33, -145, 11.96],
  [2026.5, 12, -79, 115, -137.58],
  [2026.5, 14, -33, -114, 18.12],
  [2026.5, 19, 29, 66, 2.24],
  [2026.5, 86, -11, 167, 10.24],
  [2027.0, 37, -66, -5, -17.22],
  [2027.0, 67, 72, -115, 13.73],
  [2027.0, 44, 22, 174, 6.46],
  [2027.0, 54, 54, 178, 0.63],
  [2027.0, 57, -43, 50, -48.27],
  [2027.0, 44, -43, -111, 24.31],
  [2027.0, 12, -63, 178, 57.87],
  [2027.0, 38, 27, -169, 8.48],
  [2027.0, 61, 59, -77, -16.48],
  [2027.0, 67, -47, -32, -13.52],
  [2027.5, 8, 62, 53, 19.39],
  [2027.5, 77, -68, -7, -16.19],
  [2027.5, 98, -5, 159, 7.79],
  [2027.5, 34, -29, -107, 15.64],
  [2027.5, 60, 27, 65, 1.85],
  [2027.5, 73, -72, 95, -102.64],
  [2027.5, 96, -46, -85, 17.93],
  [2027.5, 0, -13, -59, -17.49],
  [2027.5, 16, 66, -178, 0.37],
  [2027.5, 72, -87, 38, -65.44],
  [2028.0, 49, 20, 167, 5.1],
  [2028.0, 71, 5, -13, -6.47],
  [2028.0, 95, 14, 65, -0.51],
  [2028.0, 86, -85, -79, 41.09],
  [2028.0, 30, -36, -64, -4.65],
  [2028.0, 75, 79, 125, -18.59],
  [2028.0, 21, 6, -32, -14.34],
  [2028.0, 1, -76, -75, 29.87],
  [2028.0, 45, -46, -41, -11.68],
  [2028.0, 11, -22, -21, -23.24],
  [2028.5, 28, 54, -120, 15.43],
  [2028.5, 68, -58, 156, 41.57],
  [2028.5, 39, -65, -88, 29.45],
  [2028.5, 27, -23, 81, -13.27],
  [2028.5, 11, 34, 0, 1.57],
  [2028.5, 72, -62, 65, -67.87],
  [2028.5, 55, 86, 70, 67.64],
  [2028.5, 59, 32, 163, 0.15],
  [2028.5, 65, 48, 148, -9.55],
  [2028.5, 95, 30, 28, 4.56],
  [2029.0, 95, -60, -59, 8.58],
  [2029.0, 95, -70, 42, -55.06],
  [2029.0, 50, 87, -154, -73.48],
  [2029.0, 58, 32, 19, 4.11],
  [2029.0, 57, 34, -13, -1.89],
  [2029.0, 38, -76, 49, -64.28],
  [2029.0, 49, -50, -179, 32.11],
  [2029.0, 90, -55, -171, 38.65],
  [2029.0, 41, 42, -19, -4.13],
  [2029.0, 19, 46, -22, -5.65],
  [2029.5, 31, 13, -132, 9.04],
  [2029.5, 93, -2, 158, 7.09],
  [2029.5, 51, -76, 40, -56.34],
  [2029.5, 64, 22, -132, 10.23],
  [2029.5, 26, -65, 55, -63.48],
  [2029.5, 66, -21, 32, -14.63],
  [2029.5, 18, 9, -172, 9.24],
  [2029.5, 63, 88, 26, 36.52],
  [2029.5, 33, 17, 5, 0.89],
  [2029.5, 77, -18, 138, 4.45],
];

// SHA-256 of the canonical text of the official WMM2025.COF (md5
// a1b6402dd23658d15affb0f31babccc7): one line per row,
// "n m g h gDot hDot", each of the four numbers to one decimal, a negative
// zero as 0.0, every line ended by a newline.
const TABLE_DIGEST = '63a03fb2c148696c8a61c1a8c84efc31d0d511fc528f814efc312e766f2ac68e';

let passed = 0, failed = 0;
function test(name: string, fn: () => void) {
  try { fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; console.log(`FAIL ${name}\n     ${(e as Error).message.split('\n')[0]}`); }
}

/** Worst absolute disagreement over a table, and the row it is at. */
function worst(table: readonly Row[]): { err: number; row: Row } {
  let err = -1;
  let at = table[0];
  for (const row of table) {
    const [year, height, lat, lon, decl] = row;
    const e = Math.abs(declinationDeg(lat, lon, height, year) - decl);
    if (Number.isNaN(e)) return { err: NaN, row };
    if (e > err) { err = e; at = row; }
  }
  return { err, row: at };
}

function within(table: readonly Row[], tolerance: number) {
  const { err, row } = worst(table);
  assert.ok(err <= tolerance,
    `worst disagreement ${err.toFixed(4)} degrees at [${row.join(', ')}] against a bound of ${tolerance}`);
}

test('the web test table (12 rows) agrees within 0.1 degrees', () => {
  assert.equal(TABLE_WEB.length, 12);
  within(TABLE_WEB, 0.1);
});

test('the coefficient-archive test table (100 rows) agrees within 0.1 degrees', () => {
  assert.equal(TABLE_ZIP.length, 100);
  within(TABLE_ZIP, 0.1);
});

test('both tables agree within the 0.01 degree rounding of the tables themselves', () => {
  within(TABLE_WEB, 0.01);
  within(TABLE_ZIP, 0.01);
});

test('the tables reach the secular variation, both hemispheres, and east and west declinations', () => {
  const years = new Set(TABLE_ZIP.map(r => r[0]));
  assert.ok(years.size >= 10 && years.has(2025.0) && years.has(2029.5), 'the years run 2025.0 to 2029.5');
  assert.ok(TABLE_ZIP.some(r => r[2] > 60) && TABLE_ZIP.some(r => r[2] < -60), 'both polar caps');
  assert.ok(TABLE_ZIP.some(r => r[4] > 20) && TABLE_ZIP.some(r => r[4] < -20), 'east and west declinations');
  assert.ok(TABLE_ZIP.some(r => r[1] >= 90), 'heights up to 90 km');
});

test('RangeError outside decimal years 2025.0 to 2030.0, in both directions', () => {
  assert.throws(() => declinationDeg(40, -100, 0, 2024.9), RangeError);
  assert.throws(() => declinationDeg(40, -100, 0, 2030.1), RangeError);
  assert.throws(() => declinationDeg(40, -100, 0, 1999.5), RangeError);
  assert.throws(() => declinationDeg(40, -100, 0, NaN), RangeError);
  assert.throws(() => declinationDeg(40, -100, 0, decimalYear(new Date(NaN))), RangeError);
});

test('the ends of the validity range are valid', () => {
  assert.ok(Number.isFinite(declinationDeg(40, -100, 0, 2025.0)));
  assert.ok(Number.isFinite(declinationDeg(40, -100, 0, 2030.0)));
});

test('a bad position is refused without quoting it', () => {
  assert.throws(() => declinationDeg(91.23456, 10.54321, 0, 2027.5),
    (e: unknown) => e instanceof RangeError && !/91\.23456|10\.54321/.test(e.message));
  assert.throws(() => declinationDeg(-90.5, 0, 0, 2027.5), RangeError);
  assert.throws(() => declinationDeg(NaN, 0, 0, 2027.5), RangeError);
  assert.throws(() => declinationDeg(40, Infinity, 0, 2027.5), RangeError);
  assert.throws(() => declinationDeg(40, 0, NaN, 2027.5), RangeError);
});

test('a longitude is the same longitude one turn round', () => {
  const a = declinationDeg(40, -100.25, 0.3, 2027.5);
  assert.ok(Math.abs(a - declinationDeg(40, 259.75, 0.3, 2027.5)) < 1e-9);
  assert.ok(Math.abs(a - declinationDeg(40, -460.25, 0.3, 2027.5)) < 1e-9);
});

test('both poles give a finite declination close to a point just off them', () => {
  for (const lat of [90, -90]) {
    for (const lon of [0, 77, -150]) {
      const at = declinationDeg(lat, lon, 0, 2027.5);
      const off = declinationDeg(lat > 0 ? 89.9999 : -89.9999, lon, 0, 2027.5);
      assert.ok(Number.isFinite(at), 'finite');
      assert.ok(Math.abs(at - off) < 0.01, `within 0.01 of the point 11 m off the pole, ${at.toFixed(4)} against ${off.toFixed(4)}`);
    }
  }
});

test('decimalYear is the UTC year plus the elapsed fraction', () => {
  assert.equal(decimalYear(new Date(Date.UTC(2027, 0, 1))), 2027);
  assert.equal(decimalYear(new Date(Date.UTC(2027, 6, 2, 12))), 2027.5);
  // 2028 has 366 days; noon on 1 July is 182.5 of them in.
  assert.ok(Math.abs(decimalYear(new Date(Date.UTC(2028, 6, 1, 12))) - (2028 + 182.5 / 366)) < 1e-12);
  assert.ok(decimalYear(new Date(Date.UTC(2026, 11, 31, 23, 59, 59))) < 2027);
  assert.ok(decimalYear(new Date(Date.UTC(2026, 11, 31, 23, 59, 59))) > 2026.9999);
  assert.ok(Number.isNaN(decimalYear(new Date(NaN))));
});

test('decimalYear does not depend on the local time zone', () => {
  // The same instant written with offsets either side of UTC.
  const a = decimalYear(new Date('2027-07-02T12:00:00Z'));
  const b = decimalYear(new Date('2027-07-02T14:00:00+02:00'));
  const c = decimalYear(new Date('2027-07-02T05:00:00-07:00'));
  assert.equal(a, 2027.5);
  assert.equal(b, a);
  assert.equal(c, a);

  // And with the process clock moved 14 hours east of UTC and 7 to 8 hours
  // west of it. A year start taken on the local wall would shift the answer by
  // that many hours of the year, about 0.0016. A platform that ignores TZ
  // leaves the offset at zero, and then there is nothing more to prove.
  const before = process.env.TZ;
  try {
    for (const tz of ['Pacific/Kiritimati', 'America/Los_Angeles']) {
      process.env.TZ = tz;
      const noon = new Date(Date.UTC(2027, 6, 2, 12));
      if (noon.getTimezoneOffset() === 0) continue;
      assert.equal(decimalYear(noon), 2027.5, tz);
      const edge = new Date(Date.UTC(2026, 11, 31, 23, 59, 59));
      assert.ok(decimalYear(edge) < 2027 && decimalYear(edge) > 2026.9999, tz);
    }
  } finally {
    if (before === undefined) delete process.env.TZ; else process.env.TZ = before;
  }
});

test('the coefficient table is the official file, row for row', () => {
  assert.equal(WMM2025.model, 'WMM-2025');
  assert.equal(WMM2025.released, '11/13/2024');
  assert.equal(WMM2025.epoch, 2025.0);
  assert.equal(WMM2025.rows.length, 90);
  let k = 0;
  for (let n = 1; n <= 12; n++) {
    for (let m = 0; m <= n; m++) {
      const row = WMM2025.rows[k++];
      assert.deepEqual([row[0], row[1]], [n, m], `row ${k} is degree ${n} order ${m}`);
      if (m === 0) assert.ok(row[3] === 0 && row[5] === 0, `degree ${n} order 0 has no h`);
    }
  }
  const fixed = (v: number): string => { const s = v.toFixed(1); return s === '-0.0' ? '0.0' : s; };
  const text = WMM2025.rows
    .map(r => `${r[0]} ${r[1]} ${fixed(r[2])} ${fixed(r[3])} ${fixed(r[4])} ${fixed(r[5])}\n`)
    .join('');
  assert.equal(createHash('sha256').update(text).digest('hex'), TABLE_DIGEST);
});

test('source scan: wmm.ts has no console and exports only declinationDeg and decimalYear', () => {
  // The scan would pass on an empty file, so prove it sees what it looks for.
  const consoleUse = /\bconsole\s*\./;
  assert.ok(consoleUse.test('console.log(1)') && consoleUse.test('console .warn(x)') && !consoleUse.test('consoles are'));

  const src = readFileSync(new URL('../wmm.ts', import.meta.url), 'utf8') as string;
  assert.ok(src.length > 1000 && src.includes('atan2'), 'read the real source');
  assert.ok(!consoleUse.test(src), 'wmm.ts uses console');
  assert.ok(!/\bdebugger\b/.test(src), 'wmm.ts has a debugger statement');

  const lines = src.match(/^[ \t]*export\b[^\r\n]*/gm) ?? [];
  const names = lines.map(l => /^[ \t]*export\s+(?:function|const)\s+([A-Za-z_$][\w$]*)/.exec(l)?.[1] ?? l.trim());
  assert.deepEqual([...names].sort(), ['decimalYear', 'declinationDeg']);

  // It reads its table from the sibling and from nothing else.
  const imports = src.match(/^[ \t]*import\b[^\r\n]*/gm) ?? [];
  assert.deepEqual(imports.map(l => l.trim()), ["import { WMM2025 } from './wmm2025';"]);
});

test('source scan: wmm2025.ts holds the table and nothing else', () => {
  const src = readFileSync(new URL('../wmm2025.ts', import.meta.url), 'utf8') as string;
  assert.ok(src.length > 4000 && src.includes('WMM-2025'), 'read the real source');
  assert.ok(!/\bconsole\s*\./.test(src), 'wmm2025.ts uses console');
  assert.ok(!/^[ \t]*import\b/m.test(src), 'wmm2025.ts imports something');
  const lines = src.match(/^[ \t]*export\b[^\r\n]*/gm) ?? [];
  assert.equal(lines.length, 1);
  assert.ok(/^export const WMM2025\b/.test(lines[0]));
  assert.ok(!/\bfunction\b|=>/.test(src.replace(/^[ \t]*\/\/[^\r\n]*/gm, '')), 'wmm2025.ts holds code');
});

console.log(`wmm.test: ${passed}/${passed + failed} passed`);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
