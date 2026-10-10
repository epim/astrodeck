// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T22: the cue precedence of SPEC-v2 2.12 (`cueFor`), the level rail of 2.4 and 4.4 (`pitchBand`), the speed limit of
// 4.3 (`rateMaxFromInterval`), and the copy they read (`copy.ts`).
//
// Mutant this file must catch (SPEC-v2 7.2): rows 9 and 10 (`too-fast`, tilt) swapped in `cueFor`. 'precedence: each
// row wins over every row below it' sets row 9's condition together with every later row's, the tilt among them, and
// expects `too-fast`; with the rows swapped it reads `tilt-up`.
//
// Each of the 18 rows is also checked on its own, from an input where nothing else fires, together with the edges the
// table states: a rate equal to rateMax is not too fast, the green band's ends are inside it, roll of exactly 15 is
// upright, 4 stale refusals in a second are not 5, a sky luma of 40 is not dark, and 330 covered degrees is almost
// round. A missing reading (null) fires no row.
import assert from 'node:assert/strict';
import { CUE_TEXT, ERROR_TEXT, REVIEW_TEXT, fill } from '../copy';
import { cueFor, pitchBand, rateMaxFromInterval, type CueInput } from '../scanner';
import type { CueKey } from '../types';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const near = (a: number, b: number, tol: number, what = '') => assert.ok(Math.abs(a - b) <= tol, `${what} ${a} != ${b} (tol ${tol})`);

/** Begun, upright and steady, 100 degrees covered: row 15, "Keep turning. 260 deg to go.". */
const BASE: CueInput = {
  error: null, begun: true, portrait: true, paused: false, cameraStalled: false, sensorStalled: false,
  staleInLastSecond: 0, gapDeg: null, rateDegS: 10, rateMaxDegS: 40, elevationDeg: 20, green: [12, 23.36], rollDeg: 0,
  mismatchRun: 0, skyLuma: 120, capReached: false, coveredDeg: 100, closed: false, tallSpans: 0,
};
const at = (patch: Partial<CueInput>): CueInput => ({ ...BASE, ...patch });

/** The 18 rows of 2.12 in order, each with the input change that makes its condition hold. Row 10 has two keys; the
 *  tilt-down half is checked on its own below. */
const ROWS: readonly { row: number; key: CueKey; when: Partial<CueInput> }[] = [
  { row: 1, key: 'error', when: { error: ERROR_TEXT.motionBlocked } },
  { row: 2, key: 'ready', when: { begun: false } },
  { row: 3, key: 'portrait', when: { portrait: false } },
  { row: 4, key: 'paused', when: { paused: true } },
  { row: 5, key: 'stalled-camera', when: { cameraStalled: true } },
  { row: 6, key: 'stalled-sensor', when: { sensorStalled: true } },
  { row: 7, key: 'stale', when: { staleInLastSecond: 5 } },
  { row: 8, key: 'gap', when: { gapDeg: 6.4 } },
  { row: 9, key: 'too-fast', when: { rateDegS: 41 } },
  { row: 10, key: 'tilt-up', when: { elevationDeg: 11.9 } },
  { row: 11, key: 'roll', when: { rollDeg: 15.1 } },
  { row: 12, key: 'mismatch', when: { mismatchRun: 3 } },
  { row: 13, key: 'dark', when: { skyLuma: 39 } },
  { row: 14, key: 'cap', when: { capReached: true } },
  { row: 15, key: 'turning', when: { coveredDeg: 100 } },
  { row: 16, key: 'almost', when: { coveredDeg: 330 } },
  { row: 17, key: 'done-tall', when: { closed: true, tallSpans: 2 } },
  { row: 18, key: 'done', when: { closed: true } },
];

// ---- Each row on its own ----------------------------------------------------

for (const { row, key, when } of ROWS) {
  test(`row ${row}: ${key} fires on its own condition`, () => {
    assert.equal(cueFor(at(when)).key, key);
  });
}

test('row 10: tilt-down above the green band', () => {
  assert.equal(cueFor(at({ elevationDeg: 23.5 })).key, 'tilt-down');
});

// ---- Precedence: first match wins ---------------------------------------------

test('precedence: each row wins over every row below it', () => {
  // Rows 1-14 are independent flags, so each is set together with all of 1-14 below it, and the ring is set closed with
  // Tall spans, so that rows 15-18 would also answer if the row under test let them.
  for (let i = 0; i < 14; i++) {
    const input: CueInput = { ...BASE, closed: true, tallSpans: 2 };
    for (let j = i; j < 14; j++) Object.assign(input, ROWS[j].when);
    assert.equal(cueFor(input).key, ROWS[i].key, `row ${ROWS[i].row} with every later row also true`);
  }
  // Rows 15-18 split on `closed`, covered degrees and Tall spans, and the two pairs cannot both hold.
  assert.equal(cueFor(at({ coveredDeg: 329.9, tallSpans: 3 })).key, 'turning');
  assert.equal(cueFor(at({ coveredDeg: 360, tallSpans: 3 })).key, 'almost');
  assert.equal(cueFor(at({ closed: true, coveredDeg: 200, tallSpans: 1 })).key, 'done-tall');
  assert.equal(cueFor(at({ closed: true, coveredDeg: 360, tallSpans: 0 })).key, 'done');
});

test('precedence: too-fast and tilt both hold, and too-fast is shown (rows 9 and 10)', () => {
  assert.equal(cueFor(at({ rateDegS: 55, elevationDeg: 3 })).key, 'too-fast');
  assert.equal(cueFor(at({ rateDegS: 55, elevationDeg: 40 })).key, 'too-fast');
  assert.equal(cueFor(at({ rateDegS: 39, elevationDeg: 3 })).key, 'tilt-up');
});

test('every cue key is reachable', () => {
  const seen = new Set(ROWS.map(r => cueFor(at(r.when)).key));
  seen.add(cueFor(at({ elevationDeg: 30 })).key);
  assert.deepEqual([...seen].sort(), (Object.keys(CUE_TEXT) as CueKey[]).sort());
});

// ---- Edges ----------------------------------------------------------------------

test('edges: a rate equal to rateMax is not too fast, and no rate is not too fast', () => {
  assert.equal(cueFor(at({ rateDegS: 40 })).key, 'turning');
  assert.equal(cueFor(at({ rateDegS: 40.01 })).key, 'too-fast');
  assert.equal(cueFor(at({ rateDegS: null })).key, 'turning');
  assert.equal(cueFor(at({ rateDegS: 16, rateMaxDegS: 15 })).key, 'too-fast', 'rateMax is the input, not 40');
});

test('edges: the green band ends are inside it, and no elevation is no tilt cue', () => {
  assert.equal(cueFor(at({ elevationDeg: 12 })).key, 'turning');
  assert.equal(cueFor(at({ elevationDeg: 23.36 })).key, 'turning');
  assert.equal(cueFor(at({ elevationDeg: null })).key, 'turning');
  assert.equal(cueFor(at({ elevationDeg: NaN })).key, 'turning');
  assert.equal(cueFor(at({ elevationDeg: 16, green: [17, 26] })).key, 'tilt-up', 'the band is the input');
});

test('edges: roll above 15 either way, stale at 5, mismatch at 3, dark under 40, gap from 2', () => {
  assert.equal(cueFor(at({ rollDeg: 15 })).key, 'turning');
  assert.equal(cueFor(at({ rollDeg: -15.1 })).key, 'roll');
  assert.equal(cueFor(at({ rollDeg: null })).key, 'turning');
  assert.equal(cueFor(at({ staleInLastSecond: 4 })).key, 'turning');
  assert.equal(cueFor(at({ mismatchRun: 2 })).key, 'turning');
  assert.equal(cueFor(at({ skyLuma: 40 })).key, 'turning');
  assert.equal(cueFor(at({ skyLuma: null })).key, 'turning');
  assert.equal(cueFor(at({ gapDeg: 1.99 })).key, 'turning');
  assert.equal(cueFor(at({ gapDeg: 2 })).key, 'gap');
});

// ---- Texts and kinds ---------------------------------------------------------------

test('texts: the error row shows the error itself; the others show their 2.12 text, filled', () => {
  const error = cueFor(at({ error: 'Camera access was blocked.' }));
  assert.deepEqual(error, { key: 'error', text: 'Camera access was blocked.', kind: 'block' });
  assert.equal(cueFor(at({ gapDeg: 6.4 })).text, 'Go back 6 degrees to fill the gap.');
  assert.equal(cueFor(at({ coveredDeg: 100 })).text, 'Keep turning. 260 deg to go.');
  assert.equal(cueFor(at({ coveredDeg: 329.6 })).text, 'Keep turning. 30 deg to go.');
  assert.equal(cueFor(at({ closed: true, tallSpans: 2 })).text,
    'Full circle. Something goes above the picture in 2 places; those are saved as blocked. Tap Review.');
  assert.equal(cueFor(at({ begun: false })).text,
    'Hold the phone upright at your chest. Tilt it until the rail is green, then tap Start and turn slowly on the spot.');
  for (const { key, when } of ROWS) {
    const cue = cueFor(at(when));
    assert.ok(!/\{\w+\}/.test(cue.text), `${key}: an unfilled placeholder in "${cue.text}"`);
    assert.ok(cue.text.length > 0, `${key} has a text`);
  }
});

test('kinds: errors and a sideways phone block, the steps the user must take warn, the normal flow informs', () => {
  const kind = (when: Partial<CueInput>) => cueFor(at(when)).kind;
  assert.equal(kind({ error: 'x' }), 'block');
  assert.equal(kind({ portrait: false }), 'block');
  for (const when of [{ cameraStalled: true }, { staleInLastSecond: 9 }, { gapDeg: 5 }, { rateDegS: 50 }, { rollDeg: 30 }])
    assert.equal(kind(when), 'warn', JSON.stringify(when));
  for (const when of [{ begun: false }, { paused: true }, { capReached: true }, {}, { closed: true }])
    assert.equal(kind(when), 'info', JSON.stringify(when));
});

// ---- copy.ts ------------------------------------------------------------------------

test('copy: the error cue has no text of its own, and every review and error text is there', () => {
  assert.equal(CUE_TEXT.error, '');
  assert.equal(ERROR_TEXT.motionBlocked,
    'Motion sensors are blocked for this site. In Brave or Chrome, open Site settings > Motion sensors and allow this site, then reopen the scan.');
  assert.equal(fill(REVIEW_TEXT.unseen, { from: 112, to: 139 }), 'Not photographed: 112-139 deg. Saved as blocked.');
  assert.equal(fill(REVIEW_TEXT.northScan, { s: 2 }),
    'North: compass averaged over the scan, about \u00b12 deg. Metal near a mount can bias it by a fixed amount.');
  assert.equal(fill(REVIEW_TEXT.tall, { from: 160, to: 200, d: 54 }), 'Taller than the photo: 160-200 deg (at least 54 deg). Saved as blocked.');
  for (const [key, text] of [...Object.entries(ERROR_TEXT), ...Object.entries(REVIEW_TEXT)]) assert.ok(text.length > 0, key);
});

test('fill: replaces the placeholders it has values for and leaves the rest showing', () => {
  assert.equal(fill('{n} of {d}', { n: 3, d: '4.5' }), '3 of 4.5');
  assert.equal(fill('{n} of {d}', { n: 3 }), '3 of {d}');
  assert.equal(fill('no braces', { n: 1 }), 'no braces');
  assert.equal(fill('{toString}', {}), '{toString}', 'a name the object inherits is not a value');
});

// ---- pitchBand (2.4, 4.4) -------------------------------------------------------------

test('pitchBand before the lock: target 20, green [12, 23.36], amber [5, 25.36], whatever the lens', () => {
  for (const long of [55.54, 67.42, 91.49]) {
    const b = pitchBand(false, long);
    assert.equal(b.target, 20);
    near(b.green[0], 12, 1e-9); near(b.green[1], 23.36, 0.005, 'green top');
    near(b.amber[0], 5, 1e-9); near(b.amber[1], 25.36, 0.005, 'amber top');
  }
});

test('pitchBand after the lock: p1 = clamp(h - 8, 12, 30) with the 4.4 lenses', () => {
  // S25: h 30.98, p1 22.98, green [14.98, 26.98], amber [7.98, 30.98] (2.4).
  const s25 = pitchBand(true, 67.42);
  near(s25.target, 22.98, 0.01); near(s25.green[0], 14.98, 0.01); near(s25.green[1], 26.98, 0.01);
  near(s25.amber[0], 7.98, 0.01); near(s25.amber[1], 30.98, 0.01);
  // The default lens (h 32.22), the narrow one (h 25.36, so green tops out at h - 2), and the wide one (p1 clamped to 30).
  near(pitchBand(true, 70).target, 24.22, 0.01);
  const narrow = pitchBand(true, 55.54);
  near(narrow.target, 17.36, 0.01); near(narrow.green[1], 21.36, 0.01, 'min(p1 + 4, h - 2) takes h - 2'); near(narrow.amber[1], 25.36, 0.01);
  const wide = pitchBand(true, 91.49);
  assert.equal(wide.target, 30);
  near(wide.green[1], 34, 1e-9, 'p1 + 4'); near(wide.amber[1], 42.73, 0.01);
});

// ---- rateMaxFromInterval (4.3) -------------------------------------------------------------

test('rateMaxFromInterval: 40 up to a 36 ms median, then 1000 / interval clamped to [10, 40]', () => {
  assert.equal(rateMaxFromInterval(null), 40);
  assert.equal(rateMaxFromInterval(33.3), 40);
  assert.equal(rateMaxFromInterval(36), 40);
  near(rateMaxFromInterval(66.7), 15, 0.01, '15 fps');
  near(rateMaxFromInterval(37), 1000 / 37, 1e-9);
  assert.equal(rateMaxFromInterval(250), 10);
  assert.equal(rateMaxFromInterval(NaN), 40);
});

console.log(`cueFor.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
