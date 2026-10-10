// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T24: the editor model (SPEC-v2 2.9, 5.3-5.5): horizonDraft.ts, and the grid argument of horizonModel's
// insertPoint and movePoint.
//
// Mutant this file must catch (SPEC-v2 7.2): `editDraft` marking every bin Edited. `editDraft marks only the bins the
// edit changed` moves one vertex and requires exactly the bins under its two segments to become Edited and every other
// bin (Measured, Low, Unknown, Tall, Kept) to stay bit for bit what it was, so a draft that turns every bin Edited
// fails on the first bin outside the moved segments. The expected set is worked out from the vertices, not from
// horizonDraft.ts: a bin is under a segment when both of its edges lie between the segment's end vertices.
//
// The horizonModel cases at the end hold the old behaviour fixed. GOLDEN_INSERT and GOLDEN_MOVE were captured by
// running the UNEDITED insertPoint and movePoint (horizonModel.ts as of base 58584217) on seeded inputs, -0, NaN and
// infinite values included, so `no grid` is compared bit for bit (Object.is), not to a tolerance.
import assert from 'node:assert/strict';
import { horizonAltAt, insertPoint, movePoint, removePoint } from '../../../../lib/horizonModel';
import type { HorizonPoint } from '../../../../lib/horizonModel';
import { BinState, ColState, PANO_W, PROFILE_BINS } from '../pano/types';
import type { ColumnHorizon, HorizonDraft } from '../pano/types';
import { binProfile, mergeKept } from '../pano/horizon/profile';
import { conservativePolyline } from '../pano/horizon/simplify';
import { acceptSuggested, draftFromPoints, draftSpans, draftSummary, draftToPoints, editDraft, shiftDraft } from '../horizonDraft';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

/** mulberry32: a seeded generator, so every random case below is the same case every run. */
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6D2B79F5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const N = PROFILE_BINS;
const range = (from: number, to: number): number[] => Array.from({ length: to - from }, (_, k) => from + k);

/** A draft of 720 Measured bins at altitude 10, each with a trace of 10 and a photo top of 30. */
function blank(): HorizonDraft {
  return {
    alt: new Float32Array(N).fill(10), suggested: new Float32Array(N).fill(10), state: new Uint8Array(N).fill(BinState.Measured),
    reason: new Uint8Array(N).fill(ColState.Measured), top: new Float32Array(N).fill(30), lowLight: false,
  };
}

/** Set bins `from`..`to` (exclusive, indices taken modulo 720 so a run can wrap) to the given fields. */
function setBins(d: HorizonDraft, from: number, to: number,
  f: { alt?: number; suggested?: number; state?: number; reason?: number; top?: number }): void {
  for (let i = from; i < to; i++) {
    const b = ((i % N) + N) % N;
    if (f.alt !== undefined) d.alt[b] = f.alt;
    if (f.suggested !== undefined) d.suggested[b] = f.suggested;
    if (f.state !== undefined) d.state[b] = f.state;
    if (f.reason !== undefined) d.reason[b] = f.reason;
    if (f.top !== undefined) d.top[b] = f.top;
  }
}

/** The bins at which two drafts differ in any field, compared bit for bit (NaN equals NaN, 0 differs from -0). */
function diffBins(a: HorizonDraft, b: HorizonDraft): number[] {
  const out: number[] = [];
  for (let i = 0; i < N; i++) {
    if (!Object.is(a.alt[i], b.alt[i]) || a.state[i] !== b.state[i] || a.reason[i] !== b.reason[i]
      || !Object.is(a.suggested[i], b.suggested[i]) || !Object.is(a.top[i], b.top[i])) out.push(i);
  }
  return out;
}

const snapshot = (d: HorizonDraft) => JSON.stringify([Array.from(d.alt, v => String(v)), Array.from(d.suggested, v => String(v)),
  Array.from(d.state), Array.from(d.reason), Array.from(d.top, v => String(v)), d.lowLight]);

/** Run `fn` and require that it did not write `d`. */
function untouched<T>(d: HorizonDraft, fn: () => T): T {
  const before = snapshot(d);
  const out = fn();
  assert.equal(snapshot(d), before, 'the argument draft was written');
  return out;
}

/** A second reader of a wrapped polyline (written apart from horizonAltAt, as simplify.test.ts does). */
function lineAt(pts: readonly HorizonPoint[], az: number): number {
  const s = [...pts].sort((p, q) => p.az - q.az);
  const a = ((az % 360) + 360) % 360;
  const aa = a < s[0].az ? a + 360 : a;
  for (let k = 0; k < s.length; k++) {
    const p = s[k];
    const qAz = k + 1 < s.length ? s[k + 1].az : s[0].az + 360;
    const qAlt = k + 1 < s.length ? s[k + 1].alt : s[0].alt;
    if (aa >= p.az && aa <= qAz) return qAz === p.az ? p.alt : p.alt + (qAlt - p.alt) * ((aa - p.az) / (qAz - p.az));
  }
  throw new Error(`no segment holds ${az}`);
}

// ---- draftFromPoints and draftToPoints --------------------------------------------------------------------------

test('draftFromPoints: every bin Edited, valued at the higher of the line at its two edges (wrap included)', () => {
  const line: HorizonPoint[] = [{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }];
  const d = draftFromPoints(line);
  assert.equal(d.alt.length, N);
  for (let i = 0; i < N; i++) {
    const expect = Math.max(lineAt(line, i / 2), lineAt(line, (i + 1) / 2));
    assert.ok(Math.abs(d.alt[i] - expect) < 1e-4, `bin ${i}: ${d.alt[i]} vs ${expect}`);
    assert.equal(d.state[i], BinState.Edited);
    assert.equal(d.reason[i], ColState.Measured);
    assert.ok(Number.isNaN(d.suggested[i]) && Number.isNaN(d.top[i]), `bin ${i} has no trace and no photo`);
  }
  assert.equal(d.lowLight, false);
  // the same values worked out by hand: 10..20 rises 1/9 per degree, 20..5 falls 1/6, 5..15 rises 1/9, 15..10 falls 5/90
  assert.ok(Math.abs(d.alt[0] - (10 + 0.5 / 9)) < 1e-4, 'rising: the right edge');
  assert.ok(Math.abs(d.alt[179] - 20) < 1e-4 && Math.abs(d.alt[180] - 20) < 1e-4, 'the peak at 90 fills the bins on both sides');
  assert.ok(Math.abs(d.alt[359] - (20 - 89.5 / 6)) < 1e-4, 'falling: the left edge');
  assert.ok(Math.abs(d.alt[360] - (5 + 0.5 * 10 / 90)) < 1e-4, 'rising: the right edge');
  assert.ok(Math.abs(d.alt[719] - (15 - 89.5 * 5 / 90)) < 1e-4, 'the last bin reads the line across north');
});

test('draftFromPoints: an empty line reads altitude 0 in every bin, as horizonAltAt reads it', () => {
  const d = draftFromPoints([]);
  assert.ok(d.alt.every(v => v === 0));
  assert.ok(d.state.every(v => v === BinState.Edited));
});

test('draftFromPoints does not write or keep the points it is given', () => {
  const line: HorizonPoint[] = [{ az: 300, alt: 20 }, { az: 20, alt: 5 }];     // deliberately out of order
  const copy = JSON.stringify(line);
  const d = draftFromPoints(line);
  assert.equal(JSON.stringify(line), copy);
  assert.ok(Math.abs(d.alt[0] - Math.max(lineAt(line, 0), lineAt(line, 0.5))) < 1e-4, 'an unsorted line is read sorted');
});

test('draftToPoints is the conservative polyline of d.alt at 180 points', () => {
  const d = blank();
  setBins(d, 100, 140, { alt: 90 });
  setBins(d, 300, 320, { alt: 25 });
  const out = draftToPoints(d);
  const direct = conservativePolyline(d.alt, 180);
  assert.deepEqual(out.points, direct.points);
  assert.equal(out.tau, direct.tau);
  assert.equal(Object.keys(out).sort().join(), 'points,tau', 'no extra fields (shoulders stay inside simplify)');
  assert.ok(out.points.length >= 1 && out.points.length <= 180);
});

/** A 30-vertex line at every 12th degree, altitude a random walk of steps up to `step` degrees, on the 0.5 grid. */
function walkLine(seed: number, step: number): HorizonPoint[] {
  const r = rng(seed);
  const pts: HorizonPoint[] = [];
  let alt = 10;
  for (let k = 0; k < 30; k++) {
    alt = Math.min(40, Math.max(0, alt + (r() * 2 - 1) * step));
    pts.push({ az: k * 12, alt: Math.round(alt * 2) / 2 });
  }
  return pts;
}

test('draftFromPoints then draftToPoints keeps a 30-point line within 0.5 degree, and never below it', () => {
  let up = 0, down = 0;
  for (let seed = 1; seed <= 24; seed++) {
    const line = walkLine(seed, 1);
    assert.equal(line.length, 30);
    const back = draftToPoints(draftFromPoints(line));
    assert.ok(back.points.length <= 180);
    for (let az = 0; az < 360; az += 0.05) {
      const diff = horizonAltAt(back.points, az) - horizonAltAt(line, az);
      up = Math.max(up, diff);
      down = Math.min(down, diff);
    }
  }
  assert.ok(up <= 0.5, `the published line rises ${up.toFixed(4)} degrees above the line it came from`);
  assert.ok(down >= -1e-4, `the published line falls ${down} degrees below the line it came from`);
});

test('a hand-drawn line on whole degrees (the classic editor) round trips the same way', () => {
  const line: HorizonPoint[] = [{ az: 0, alt: 12 }, { az: 45, alt: 18 }, { az: 100, alt: 9 }, { az: 160, alt: 33 }, { az: 175, alt: 33 },
    { az: 230, alt: 4 }, { az: 301, alt: 21 }, { az: 340, alt: 7 }];
  const back = draftToPoints(draftFromPoints(line));
  for (let az = 0; az < 360; az += 0.05) {
    const diff = horizonAltAt(back.points, az) - horizonAltAt(line, az);
    assert.ok(diff >= -1e-4, `below the line at ${az.toFixed(2)}: ${diff}`);
  }
});

/** 720 bins of a random walk with blocked stretches, as a Measured draft. */
function randomDraft(seed: number): HorizonDraft {
  const r = rng(seed * 7919 + 5);
  const d = blank();
  let v = r() * 20;
  for (let i = 0; i < N; i++) { v = Math.min(45, Math.max(0, v + (r() * 2 - 1) * 2.5)); d.alt[i] = v; }
  for (let k = 0; k < 5; k++) setBins(d, Math.floor(r() * N), Math.floor(r() * N) + 1 + Math.floor(r() * 30), { alt: 90, state: BinState.Unknown });
  return d;
}

test('republishing a published line never lowers a bin: draftFromPoints(draftToPoints(d)) is at or above d', () => {
  for (let seed = 1; seed <= 40; seed++) {
    const d = randomDraft(seed);
    const again = draftFromPoints(draftToPoints(d).points);
    for (let i = 0; i < N; i++) assert.ok(again.alt[i] >= d.alt[i] - 1e-4, `seed ${seed} bin ${i}: ${again.alt[i]} < ${d.alt[i]}`);
  }
});

// ---- acceptSuggested --------------------------------------------------------------------------------------------

/** Low bins at 100..110 degrees (traced 14.5) and across north, 350..5 degrees (traced 8); one bin each of the others. */
function lowDraft(): HorizonDraft {
  const d = blank();
  setBins(d, 200, 220, { alt: 90, suggested: 14.5, state: BinState.Low, reason: ColState.Low });
  setBins(d, 700, 730, { alt: 90, suggested: 8, state: BinState.Low, reason: ColState.Low });
  setBins(d, 300, 301, { alt: 90, suggested: 12, state: BinState.Unknown, reason: ColState.UnknownUnseen });
  setBins(d, 310, 311, { alt: 90, suggested: 12, state: BinState.Tall, reason: ColState.Tall, top: 50 });
  return d;
}
const LOW_BINS = [...range(0, 10), ...range(200, 220), ...range(700, 720)];

test('acceptSuggested: every Low bin takes its traced altitude and becomes Edited; nothing else changes', () => {
  const d = lowDraft();
  const out = untouched(d, () => acceptSuggested(d));
  assert.deepEqual(diffBins(out, d), LOW_BINS);
  for (const i of LOW_BINS) {
    assert.equal(out.state[i], BinState.Edited, `bin ${i}`);
    assert.equal(out.alt[i], d.suggested[i], `bin ${i}`);
    assert.equal(out.reason[i], ColState.Low, 'the reason a bin was Low is kept');
    assert.equal(out.suggested[i], d.suggested[i]);
    assert.equal(out.top[i], d.top[i]);
  }
  assert.equal(out.state[300], BinState.Unknown, 'an Unknown bin has nothing to accept, whatever it suggests');
  assert.equal(out.alt[300], 90);
  assert.equal(out.state[310], BinState.Tall);
});

test('acceptSuggested: a range takes only the Low bins whose middle lies in it', () => {
  const d = lowDraft();
  assert.deepEqual(diffBins(acceptSuggested(d, 100, 110), d), range(200, 220));
  assert.deepEqual(diffBins(acceptSuggested(d, 102, 105), d), range(204, 210), 'bins 204..209 have middles 102.25..104.75');
  assert.deepEqual(diffBins(acceptSuggested(d, 100, 102.2), d), range(200, 204), 'the bin whose middle is 102.25 is not in');
  assert.deepEqual(diffBins(acceptSuggested(d, 100, 110.01), d), range(200, 220));
});

test('acceptSuggested: a range that crosses north wraps, and a range with only a start runs to 360', () => {
  const d = lowDraft();
  assert.deepEqual(diffBins(acceptSuggested(d, 350, 5), d), [...range(0, 10), ...range(700, 720)]);
  assert.deepEqual(diffBins(acceptSuggested(d, 355, 3), d), [...range(0, 6), ...range(710, 720)]);
  assert.deepEqual(diffBins(acceptSuggested(d, 100), d), [...range(200, 220), ...range(700, 720)], 'to defaults to 360');
  assert.deepEqual(diffBins(acceptSuggested(d, undefined, 100), d), range(0, 10), 'from defaults to 0');
});

test('acceptSuggested: nothing to accept returns the same draft; a bad range is refused', () => {
  const d = lowDraft();
  assert.equal(acceptSuggested(d, 200, 250), d);
  assert.equal(acceptSuggested(d, 100, 100), d, 'an arc of no width holds nothing');
  const open = blank();
  assert.equal(acceptSuggested(open), open, 'a draft with no Low bin');
  assert.throws(() => acceptSuggested(d, NaN, 10), RangeError);
  assert.throws(() => acceptSuggested(d, 0, Infinity), RangeError);
});

test('acceptSuggested leaves a Low bin with no traced altitude alone', () => {
  const d = lowDraft();
  d.suggested[205] = NaN;
  const out = acceptSuggested(d);
  assert.equal(out.state[205], BinState.Low);
  assert.equal(out.alt[205], 90);
  assert.equal(out.state[204], BinState.Edited);
});

// ---- editDraft --------------------------------------------------------------------------------------------------

/** Every vertex on the 0.5 degree grid, so bin edges meet vertices exactly. */
const LINE: HorizonPoint[] = [{ az: 0, alt: 10 }, { az: 40, alt: 14 }, { az: 85.5, alt: 12 }, { az: 120, alt: 30 },
  { az: 200, alt: 8 }, { az: 250, alt: 8 }, { az: 300, alt: 20 }, { az: 355, alt: 6 }];

/** A draft with a mixture of Measured, Low, Unknown, Tall and Kept bins and no Edited bin, every bin distinguishable. */
function mixedDraft(): HorizonDraft {
  const d = blank();
  for (let i = 0; i < N; i++) {
    const kind = (i * 7) % 5;
    d.alt[i] = 3 + (i % 29) * 0.5;
    d.suggested[i] = 4 + (i % 13);
    d.top[i] = 20 + (i % 17);
    if (kind === 0) { d.state[i] = BinState.Measured; d.reason[i] = ColState.Measured; }
    else if (kind === 1) { d.state[i] = BinState.Low; d.reason[i] = ColState.Low; d.alt[i] = 90; }
    else if (kind === 2) { d.state[i] = BinState.Unknown; d.reason[i] = ColState.UnknownDark; d.alt[i] = 90; d.suggested[i] = NaN; }
    else if (kind === 3) { d.state[i] = BinState.Tall; d.reason[i] = ColState.Tall; d.alt[i] = 90; }
    else { d.state[i] = BinState.Kept; d.reason[i] = ColState.UnknownUnseen; d.top[i] = NaN; }
  }
  return d;
}

test('editDraft marks only the bins the edit changed, with the new line at their edges, and leaves every other bin as it was', () => {
  const d = mixedDraft();
  const after = movePoint(LINE, 3, 120, 35, 0.5);                  // the vertex at 120 rises 5 degrees
  assert.deepEqual(after[3], { az: 120, alt: 35 });
  const out = untouched(d, () => editDraft(d, LINE, after));
  // the vertex's two segments run 85.5..120 and 120..200: the bins whose edges both lie in 85.5..200 are 171..399
  const under = range(171, 400);
  assert.deepEqual(diffBins(out, d), under);
  for (const i of under) {
    assert.equal(out.state[i], BinState.Edited, `bin ${i}`);
    const expect = Math.max(lineAt(after, i / 2), lineAt(after, (i + 1) / 2));
    assert.ok(Math.abs(out.alt[i] - expect) < 1e-4, `bin ${i}: ${out.alt[i]} vs ${expect}`);
    assert.equal(out.reason[i], d.reason[i], 'the reason a bin could not be published is kept');
    assert.ok(Object.is(out.suggested[i], d.suggested[i]) && Object.is(out.top[i], d.top[i]));
  }
  for (const i of [170, 400, 0, 719]) assert.notEqual(out.state[i], BinState.Edited, `bin ${i} is outside the edit`);
  assert.equal(out.lowLight, d.lowLight);
});

test('editDraft: an edit over blocked bins hands them to the user (Unknown, Tall, Low and Kept become Edited at the line)', () => {
  const d = mixedDraft();
  const out = editDraft(d, LINE, movePoint(LINE, 3, 120, 35, 0.5));
  const kinds = new Set(range(171, 400).map(i => d.state[i]));
  for (const s of [BinState.Measured, BinState.Low, BinState.Unknown, BinState.Tall, BinState.Kept]) {
    assert.ok(kinds.has(s), `the fixture has no state ${s} under the edit`);
  }
  for (const i of range(171, 400)) assert.ok(out.alt[i] < 90, `bin ${i} was ${d.alt[i]} and the user's line is below 90`);
});

test('editDraft across north: the last vertex and the first vertex edit the bins on both sides of 0', () => {
  const d = mixedDraft();
  // (355, 6) -> (355, 12): its segments run 300..355 and 355..360 (the first vertex at 0)
  assert.deepEqual(diffBins(editDraft(d, LINE, movePoint(LINE, 7, 355, 12, 0.5)), d), range(600, 720));
  // (0, 10) -> (0, 16): its segments run 355..360 and 0..40
  assert.deepEqual(diffBins(editDraft(d, LINE, movePoint(LINE, 0, 0, 16, 0.5)), d), [...range(0, 80), ...range(710, 720)]);
});

test('editDraft: an inserted vertex and a removed vertex edit the bins under their segments', () => {
  const d = mixedDraft();
  // the line at 150 is 21.75, so a vertex at (150, 20) bends the segment 120..200
  const inserted = insertPoint(LINE, 150, 20, 0.5);
  assert.equal(inserted.length, 9);
  assert.deepEqual(diffBins(editDraft(d, LINE, inserted), d), range(240, 400));
  // removing (120, 30) straightens 85.5..200
  assert.deepEqual(diffBins(editDraft(d, LINE, removePoint(LINE, 3)), d), range(171, 400));
});

test('editDraft: no change, or a change under 1e-6 degree, returns the same draft', () => {
  const d = mixedDraft();
  assert.equal(editDraft(d, LINE, LINE.map(p => ({ ...p }))), d);
  assert.equal(editDraft(d, LINE, LINE.map((p, k) => (k === 3 ? { az: p.az, alt: p.alt + 5e-7 } : p))), d);
  const out = editDraft(d, LINE, LINE.map((p, k) => (k === 3 ? { az: p.az, alt: p.alt + 1e-3 } : p)));
  assert.notEqual(out, d);
  assert.ok(diffBins(out, d).length > 0 && diffBins(out, d).length < N);
});

test('editDraft: a line that cannot be read blocks its bins instead of leaving them', () => {
  const d = mixedDraft();
  const broken = LINE.map((p, k) => (k === 3 ? { az: p.az, alt: NaN } : p));
  const out = editDraft(d, LINE, broken);
  for (const i of range(171, 400)) assert.equal(out.state[i], BinState.Edited, `bin ${i}`);
  assert.ok(Number.isNaN(out.alt[300]));
  assert.equal(horizonAltAt(draftToPoints(out).points, 150), 90, 'a non-finite bin publishes as blocked');
});

test('editDraft composes with draftFromPoints: editing a draft made from a line edits only under the edit', () => {
  const d = draftFromPoints(LINE);
  d.state.fill(BinState.Measured);                                  // so an Edited bin is a changed bin
  const after = movePoint(LINE, 4, 200, 20, 0.5);                   // (200, 8) -> (200, 20): segments 120..200 and 200..250
  const out = editDraft(d, LINE, after);
  assert.deepEqual(diffBins(out, d), range(240, 500));
  for (const i of range(240, 500)) assert.ok(Math.abs(out.alt[i] - Math.max(lineAt(after, i / 2), lineAt(after, (i + 1) / 2))) < 1e-4);
});

// ---- shiftDraft -------------------------------------------------------------------------------------------------

/** Every field varied, NaN in the traced and photo columns, all seven states. */
function variedDraft(seed: number): HorizonDraft {
  const r = rng(seed);
  const d = blank();
  d.lowLight = seed % 2 === 0;
  const states = [BinState.Measured, BinState.Low, BinState.Unknown, BinState.Tall, BinState.Edited, BinState.Kept, BinState.Measured];
  for (let i = 0; i < N; i++) {
    d.alt[i] = r() < 0.2 ? 90 : r() * 40 - 5;
    d.suggested[i] = r() < 0.3 ? NaN : r() * 40;
    d.top[i] = r() < 0.3 ? NaN : 20 + r() * 40;
    d.state[i] = states[Math.floor(r() * states.length)];
    d.reason[i] = Math.floor(r() * 6);
  }
  return d;
}

test('shiftDraft by a multiple of 0.5 degree moves every bin whole, wrapping at 360', () => {
  const d = variedDraft(3);
  for (const [deg, bins] of [[0.5, 1], [-0.5, -1], [5, 10], [-5, -10], [123.5, 247], [360.5, 1], [-359.5, 1], [180, 360]] as const) {
    const out = untouched(d, () => shiftDraft(d, deg));
    for (let i = 0; i < N; i++) {
      const s = (((i - bins) % N) + N) % N;
      assert.ok(Object.is(out.alt[i], d.alt[s]) && Object.is(out.suggested[i], d.suggested[s]) && Object.is(out.top[i], d.top[s]),
        `shift ${deg}, bin ${i}`);
      assert.ok(out.state[i] === d.state[s] && out.reason[i] === d.reason[s], `shift ${deg}, bin ${i}`);
    }
    assert.equal(out.lowLight, d.lowLight);
  }
});

test('shiftDraft by a whole turn returns the same draft; a sum that rounds to a whole bin is a whole bin', () => {
  const d = variedDraft(4);
  assert.equal(shiftDraft(d, 0), d);
  assert.equal(shiftDraft(d, 360), d);
  assert.equal(shiftDraft(d, -720), d);
  const sum = 0.7 - 0.2;                                            // 0.49999999999999994
  assert.notEqual(sum, 0.5);
  assert.deepEqual(diffBins(shiftDraft(d, sum), shiftDraft(d, 0.5)), []);
});

test('shiftDraft by any other amount is conservative: each target bin takes the highest bin it overlaps, with that bin\'s state', () => {
  for (let seed = 1; seed <= 6; seed++) {
    const d = variedDraft(seed + 10);
    for (const deg of [0.25, -0.25, 0.1, 1.3, -7.7, 123.456, 359.9, 0.4999]) {
      const out = untouched(d, () => shiftDraft(d, deg));
      const k = deg * 2;                                            // bins
      for (let j = 0; j < N; j++) {
        // brute force: the source bins whose interval [m, m + 1), at any turn, overlaps [j - k, j + 1 - k)
        const lo = j - k, hi = j + 1 - k;
        const over: number[] = [];
        for (let m = Math.floor(lo) - 1; m <= Math.ceil(hi) + 1; m++) {
          if (Math.min(m + 1, hi) - Math.max(m, lo) > 1e-9) over.push(((m % N) + N) % N);
        }
        assert.ok(over.length >= 1 && over.length <= 2, `seed ${seed} shift ${deg} bin ${j}: ${over.length} overlaps`);
        const best = Math.max(...over.map(s => d.alt[s]));
        assert.equal(out.alt[j], best, `seed ${seed} shift ${deg} bin ${j}`);
        assert.ok(over.some(s => d.alt[s] === best && d.state[s] === out.state[j] && d.reason[s] === out.reason[j]),
          `seed ${seed} shift ${deg} bin ${j}: state ${out.state[j]} is not that of a highest overlapped bin`);
        const sug = over.map(s => d.suggested[s]).filter(Number.isFinite);
        const top = over.map(s => d.top[s]).filter(Number.isFinite);
        assert.ok(Object.is(out.suggested[j], sug.length ? Math.max(...sug) : NaN), `suggested, bin ${j}`);
        assert.ok(Object.is(out.top[j], top.length ? Math.min(...top) : NaN), `top, bin ${j}`);
      }
    }
  }
});

test('shiftDraft never lowers a bin: shifting out and back is at or above the original', () => {
  for (let seed = 1; seed <= 6; seed++) {
    const d = variedDraft(seed + 30);
    for (const deg of [0.25, 1.3, 7.7, 100.1]) {
      const back = shiftDraft(shiftDraft(d, deg), -deg);
      for (let i = 0; i < N; i++) assert.ok(back.alt[i] >= d.alt[i], `seed ${seed} shift ${deg} bin ${i}: ${back.alt[i]} < ${d.alt[i]}`);
    }
  }
});

test('shiftDraft: a blocked bin beside an open one stays blocked, and the blocked state wins a tie at 90', () => {
  const d = blank();
  setBins(d, 100, 101, { alt: 90, state: BinState.Unknown, reason: ColState.UnknownUnseen, suggested: NaN, top: NaN });
  setBins(d, 101, 102, { alt: 90, state: BinState.Measured, reason: ColState.Measured });
  setBins(d, 102, 103, { alt: 90, state: BinState.Low, reason: ColState.Low });
  const out = shiftDraft(d, 0.25);                                   // target j overlaps source j - 1 and j
  assert.equal(out.alt[100], 90);                                    // sources 99 (open, 10) and 100 (unseen, 90)
  assert.equal(out.state[100], BinState.Unknown);
  assert.equal(out.reason[100], ColState.UnknownUnseen);
  assert.equal(out.state[101], BinState.Unknown, 'Unknown (100) and Measured at 90 (101) tie at 90: the blocked one is the state');
  assert.equal(out.state[102], BinState.Low, 'Measured at 90 (101) and Low (102) tie: the one that says why');
  assert.equal(out.alt[103], 90, 'sources 102 (90) and 103 (10)');
  assert.equal(out.top[100], 30, 'the unseen bin has no photo top; the open one beside it has');
});

test('shiftDraft refuses a shift that is not a number', () => {
  const d = blank();
  assert.throws(() => shiftDraft(d, NaN), RangeError);
  assert.throws(() => shiftDraft(d, Infinity), RangeError);
});

// ---- draftSpans and draftSummary --------------------------------------------------------------------------------

const isLow = (s: number) => s === BinState.Low;

test('draftSpans: a run that crosses north is one span with toDeg below fromDeg, in order of start', () => {
  const d = blank();
  setBins(d, 700, 740, { state: BinState.Low });                     // 350..370 degrees = 350..10
  setBins(d, 100, 105, { state: BinState.Low });
  assert.deepEqual(draftSpans(d, isLow), [{ fromDeg: 50, toDeg: 52.5, bins: 5 }, { fromDeg: 350, toDeg: 10, bins: 40 }]);
});

test('draftSpans: the ends of the ring and of a run', () => {
  const d = blank();
  setBins(d, 710, 720, { state: BinState.Low });
  assert.deepEqual(draftSpans(d, isLow), [{ fromDeg: 355, toDeg: 360, bins: 10 }], 'a run that ends at the last bin ends at 360');
  const e = blank();
  setBins(e, 0, 10, { state: BinState.Low });
  assert.deepEqual(draftSpans(e, isLow), [{ fromDeg: 0, toDeg: 5, bins: 10 }]);
  const one = blank();
  setBins(one, 201, 202, { state: BinState.Low });
  assert.deepEqual(draftSpans(one, isLow), [{ fromDeg: 100.5, toDeg: 101, bins: 1 }]);
  const first = blank();
  setBins(first, 0, 1, { state: BinState.Low });
  setBins(first, 719, 720, { state: BinState.Low });
  assert.deepEqual(draftSpans(first, isLow), [{ fromDeg: 359.5, toDeg: 0.5, bins: 2 }], 'bins 719 and 0 are one run');
});

test('draftSpans: all bins, no bins, all but one, and runs one bin apart', () => {
  const d = blank();
  assert.deepEqual(draftSpans(d, () => true), [{ fromDeg: 0, toDeg: 360, bins: 720 }]);
  assert.deepEqual(draftSpans(d, () => false), []);
  const gap = blank();
  setBins(gap, 5, 6, { state: BinState.Low });
  assert.deepEqual(draftSpans(gap, s => s !== BinState.Low), [{ fromDeg: 3, toDeg: 2.5, bins: 719 }]);
  const two = blank();
  setBins(two, 10, 20, { state: BinState.Low });
  setBins(two, 21, 30, { state: BinState.Low });
  assert.deepEqual(draftSpans(two, isLow), [{ fromDeg: 5, toDeg: 10, bins: 10 }, { fromDeg: 10.5, toDeg: 15, bins: 9 }]);
});

test('draftSpans: the test sees the state and the reason, and runs join across reasons that pass it', () => {
  const d = blank();
  setBins(d, 10, 14, { state: BinState.Unknown, reason: ColState.UnknownUnseen });
  setBins(d, 14, 18, { state: BinState.Unknown, reason: ColState.UnknownDark });
  setBins(d, 18, 20, { state: BinState.Tall, reason: ColState.Tall });
  assert.deepEqual(draftSpans(d, s => s === BinState.Unknown), [{ fromDeg: 5, toDeg: 9, bins: 8 }]);
  assert.deepEqual(draftSpans(d, (s, r) => s === BinState.Unknown && r === ColState.UnknownDark), [{ fromDeg: 7, toDeg: 9, bins: 4 }]);
  assert.deepEqual(draftSpans(d, (_s, r) => r === ColState.UnknownUnseen), [{ fromDeg: 5, toDeg: 7, bins: 4 }]);
  assert.deepEqual(draftSpans(d, (s, r) => s === ColState.UnknownDark && r === BinState.Unknown), [], 'state and reason are not swapped');
});

test('draftSummary: low, unseen, dark, tall and kept are the bins of that state and reason; Measured and Edited are in none', () => {
  const d = blank();
  setBins(d, 30, 36, { state: BinState.Low, reason: ColState.Low });                              // 15..18
  setBins(d, 224, 279, { state: BinState.Unknown, reason: ColState.UnknownUnseen });              // 112..139.5
  setBins(d, 602, 610, { state: BinState.Unknown, reason: ColState.UnknownDark });                // 301..305
  setBins(d, 640, 645, { state: BinState.Unknown, reason: ColState.UnknownContrast });            // 320..322.5
  setBins(d, 320, 400, { state: BinState.Tall, reason: ColState.Tall, top: 60 });                 // 160..200
  setBins(d, 350, 351, { top: 54 });
  setBins(d, 700, 720, { state: BinState.Kept, reason: ColState.UnknownUnseen, top: NaN });       // 350..360 and on across north
  setBins(d, 0, 6, { state: BinState.Kept, reason: ColState.UnknownUnseen, top: NaN });
  setBins(d, 500, 510, { state: BinState.Edited, reason: ColState.Low });
  const s = untouched(d, () => draftSummary(d));
  assert.deepEqual(s.low, [{ fromDeg: 15, toDeg: 18, bins: 6 }]);
  assert.deepEqual(s.unseen, [{ fromDeg: 112, toDeg: 139.5, bins: 55 }]);
  assert.deepEqual(s.dark, [{ fromDeg: 301, toDeg: 305, bins: 8 }, { fromDeg: 320, toDeg: 322.5, bins: 5 }],
    'low contrast has no list of its own and is told with the dark bins, so no blocked bin goes unexplained');
  assert.deepEqual(s.tall, [{ fromDeg: 160, toDeg: 200, bins: 80, atLeastDeg: 54 }]);
  assert.deepEqual(s.kept, [{ fromDeg: 350, toDeg: 3, bins: 26 }], 'the kept run crosses north');
  assert.equal(Object.keys(s).sort().join(), 'dark,kept,low,tall,unseen');
});

test('draftSummary: a Tall span is at least as tall as its lowest photo top; with no photo top at all it is 0', () => {
  const d = blank();
  setBins(d, 100, 110, { state: BinState.Tall, reason: ColState.Tall, top: 61 });
  d.top[104] = 48.5;
  d.top[105] = NaN;                                                  // a bin with no top does not count as 0
  setBins(d, 300, 305, { state: BinState.Tall, reason: ColState.Tall, top: NaN });
  setBins(d, 710, 725, { state: BinState.Tall, reason: ColState.Tall, top: 55 });     // 355 degrees across north to 2.5
  d.top[2] = 52;
  assert.deepEqual(draftSummary(d).tall, [
    { fromDeg: 50, toDeg: 55, bins: 10, atLeastDeg: 48.5 },
    { fromDeg: 150, toDeg: 152.5, bins: 5, atLeastDeg: 0 },
    { fromDeg: 355, toDeg: 2.5, bins: 15, atLeastDeg: 52 },
  ]);
});

/** A column set of 1080 Measured columns at altitude 8, photo top 30. */
function columns(): ColumnHorizon {
  return {
    alt: new Float32Array(PANO_W).fill(8), state: new Uint8Array(PANO_W).fill(ColState.Measured), top: new Float32Array(PANO_W).fill(30),
    bottom: new Float32Array(PANO_W).fill(-5), contrastSigma: new Float32Array(PANO_W).fill(10), sigmaDeg: new Float32Array(PANO_W).fill(0.1),
    lowLight: false,
  };
}

test('draftSummary on a real partial scan (S22): kept bins are Kept, a half-seen bin stays unseen at 90', () => {
  const c = columns();
  // bin 100 meets columns 150 and 151: one unseen, one measured
  c.alt[150] = NaN; c.state[150] = ColState.UnknownUnseen; c.top[150] = NaN;
  // bins 200..209 meet columns 300..314 only: all unseen
  for (let x = 300; x <= 314; x++) { c.alt[x] = NaN; c.state[x] = ColState.UnknownUnseen; c.top[x] = NaN; }
  // bins 300..304 meet columns 450..457; columns 450..456 are photographed but too dark (column 457 is Measured)
  for (let x = 450; x <= 456; x++) { c.alt[x] = NaN; c.state[x] = ColState.UnknownDark; }
  const previous: HorizonPoint[] = [{ az: 0, alt: 15 }, { az: 180, alt: 15 }];
  const d = mergeKept(binProfile(c), previous);
  const s = draftSummary(d);
  assert.deepEqual(s.kept, [{ fromDeg: 100, toDeg: 105, bins: 10 }]);
  assert.deepEqual(s.unseen, [{ fromDeg: 50, toDeg: 50.5, bins: 1 }]);
  assert.equal(d.alt[100], 90, 'the half-seen bin publishes 90');
  assert.deepEqual(s.dark, [{ fromDeg: 150, toDeg: 152.5, bins: 5 }]);
  assert.deepEqual(s.low, []);
  assert.deepEqual(s.tall, []);
  assert.equal(s.kept.reduce((n, k) => n + k.bins, 0), d.state.reduce((n, v) => n + (v === BinState.Kept ? 1 : 0), 0), 'kept counts state Kept');
});

test('draftSummary of a draft with nothing to say is five empty lists', () => {
  assert.deepEqual(draftSummary(blank()), { low: [], unseen: [], dark: [], tall: [], kept: [] });
  assert.deepEqual(draftSummary(draftFromPoints(LINE)), { low: [], unseen: [], dark: [], tall: [], kept: [] });
});

// ---- horizonModel: the golden cases (no grid) and the grid -------------------------------------------------------

type Pt = { az: number; alt: number };
type Golden = [Pt[], number, number, Pt[]];
type GoldenMove = [Pt[], number, number, number, Pt[] | null];
const samePts = (a: readonly Pt[], b: readonly Pt[]) => a.length === b.length && a.every((p, k) => Object.is(p.az, b[k].az) && Object.is(p.alt, b[k].alt));
const fmt = (a: readonly Pt[]) => JSON.stringify(a, (_k, v) => (typeof v === 'number' && !Number.isFinite(v) ? String(v) : Object.is(v, -0) ? '-0' : v));

// Golden cases, printed by the unedited insertPoint and movePoint (seeded inputs: quarter-degree values so halves and
// quarters are common, then hand-picked edge cases with -0, NaN and infinities). Rows are [line, az, alt, result] for
// insertPoint and [line, index, az, alt, result] for movePoint, where null means the array itself came back. Numbers are
// written as JavaScript prints them, so -0, NaN and Infinity are real values and are compared with Object.is.
const GOLDEN_INSERT: Golden[] = [
  [[{ az: 249.5, alt: 29 }, { az: 259, alt: -1.5 }], 244.5, 72.5, [{ az: 245, alt: 73 }, { az: 249.5, alt: 29 }, { az: 259, alt: -1.5 }]],
  [[{ az: 158, alt: 34.5 }, { az: 174.5, alt: 25.5 }, { az: 234, alt: 35 }, { az: 290.5, alt: 0.5 }], 5, -18.25, [{ az: 5, alt: -18 }, { az: 158, alt: 34.5 }, { az: 174.5, alt: 25.5 }, { az: 234, alt: 35 }, { az: 290.5, alt: 0.5 }]],
  [[{ az: 2, alt: 0.5 }, { az: 42.5, alt: 30.5 }, { az: 98.5, alt: 21.5 }, { az: 146, alt: 22.5 }, { az: 222, alt: 7.5 }, { az: 328.5, alt: 24 }], 24.25, 4, [{ az: 2, alt: 0.5 }, { az: 24, alt: 4 }, { az: 42.5, alt: 30.5 }, { az: 98.5, alt: 21.5 }, { az: 146, alt: 22.5 }, { az: 222, alt: 7.5 }, { az: 328.5, alt: 24 }]],
  [[{ az: 244.5, alt: 12 }, { az: 268, alt: 3.5 }, { az: 308.5, alt: 6 }], 31, 30.5, [{ az: 31, alt: 31 }, { az: 244.5, alt: 12 }, { az: 268, alt: 3.5 }, { az: 308.5, alt: 6 }]],
  [[{ az: 135.5, alt: 31.5 }, { az: 180.5, alt: 32.5 }, { az: 227, alt: 16.5 }, { az: 242.5, alt: 27 }, { az: 355, alt: 4 }], 163.5, 15, [{ az: 135.5, alt: 31.5 }, { az: 164, alt: 15 }, { az: 180.5, alt: 32.5 }, { az: 227, alt: 16.5 }, { az: 242.5, alt: 27 }, { az: 355, alt: 4 }]],
  [[{ az: 46, alt: 2 }, { az: 132, alt: 23 }, { az: 204.5, alt: 3 }, { az: 209.5, alt: 15 }, { az: 270.5, alt: -3.5 }, { az: 279, alt: 15 }], 227, 44.5, [{ az: 46, alt: 2 }, { az: 132, alt: 23 }, { az: 204.5, alt: 3 }, { az: 209.5, alt: 15 }, { az: 227, alt: 45 }, { az: 270.5, alt: -3.5 }, { az: 279, alt: 15 }]],
  [[{ az: 232, alt: 31.5 }], 115, -18.75, [{ az: 115, alt: -19 }, { az: 232, alt: 31.5 }]],
  [[{ az: 3, alt: 31.5 }, { az: 45, alt: 28.5 }, { az: 90, alt: 2.5 }, { az: 104.5, alt: 27 }], 393.75, 0.25, [{ az: 3, alt: 31.5 }, { az: 45, alt: 28.5 }, { az: 90, alt: 2.5 }, { az: 104.5, alt: 27 }, { az: 394, alt: 0 }]],
  [[{ az: 296, alt: -0.5 }], 108, 99.5, [{ az: 108, alt: 100 }, { az: 296, alt: -0.5 }]],
  [[{ az: 139.5, alt: 26.5 }, { az: 151, alt: 35 }, { az: 230, alt: 8 }], 176, -14.25, [{ az: 139.5, alt: 26.5 }, { az: 151, alt: 35 }, { az: 176, alt: -14 }, { az: 230, alt: 8 }]],
  [[{ az: 64.5, alt: 21 }], 364.25, 50.75, [{ az: 64.5, alt: 21 }, { az: 364, alt: 51 }]],
  [[{ az: 12.5, alt: 14 }, { az: 236, alt: 7.5 }], 259.75, 50, [{ az: 12.5, alt: 14 }, { az: 236, alt: 7.5 }, { az: 260, alt: 50 }]],
  [[{ az: 89, alt: 27.5 }, { az: 100, alt: -5 }, { az: 230.5, alt: 0.5 }, { az: 335, alt: -3.5 }, { az: 335.5, alt: 1 }], 264.5, 31.25, [{ az: 89, alt: 27.5 }, { az: 100, alt: -5 }, { az: 230.5, alt: 0.5 }, { az: 265, alt: 31 }, { az: 335, alt: -3.5 }, { az: 335.5, alt: 1 }]],
  [[{ az: 257.5, alt: 2 }, { az: 304.5, alt: 30 }], 64, -12.75, [{ az: 64, alt: -13 }, { az: 257.5, alt: 2 }, { az: 304.5, alt: 30 }]],
  [[{ az: 23, alt: -3.5 }, { az: 35, alt: 1 }, { az: 265, alt: -2 }, { az: 299.5, alt: 15 }], 300.75, 0.25, [{ az: 23, alt: -3.5 }, { az: 35, alt: 1 }, { az: 265, alt: -2 }, { az: 299.5, alt: 15 }, { az: 301, alt: 0 }]],
  [[{ az: 111, alt: 26.5 }, { az: 143, alt: 23.5 }, { az: 149.5, alt: 1.5 }, { az: 235.5, alt: 34.5 }, { az: 315, alt: 31 }, { az: 351.5, alt: -4.5 }], 78, 51.25, [{ az: 78, alt: 51 }, { az: 111, alt: 26.5 }, { az: 143, alt: 23.5 }, { az: 149.5, alt: 1.5 }, { az: 235.5, alt: 34.5 }, { az: 315, alt: 31 }, { az: 351.5, alt: -4.5 }]],
  [[{ az: 54.5, alt: 20.5 }, { az: 135.5, alt: 24.5 }, { az: 218.5, alt: 22 }, { az: 303.5, alt: 4.5 }, { az: 314.5, alt: 10 }], 181.25, 101.75, [{ az: 54.5, alt: 20.5 }, { az: 135.5, alt: 24.5 }, { az: 181, alt: 102 }, { az: 218.5, alt: 22 }, { az: 303.5, alt: 4.5 }, { az: 314.5, alt: 10 }]],
  [[{ az: 67.5, alt: 14 }, { az: 93, alt: 27 }, { az: 189.5, alt: -2.5 }, { az: 236.5, alt: 14 }, { az: 277, alt: 22 }], 363.25, 99.25, [{ az: 67.5, alt: 14 }, { az: 93, alt: 27 }, { az: 189.5, alt: -2.5 }, { az: 236.5, alt: 14 }, { az: 277, alt: 22 }, { az: 363, alt: 99 }]],
  [[{ az: 43, alt: 30.5 }, { az: 143, alt: 0.5 }], 18.5, 0.5, [{ az: 19, alt: 1 }, { az: 43, alt: 30.5 }, { az: 143, alt: 0.5 }]],
  [[{ az: 25, alt: 2.5 }, { az: 38.5, alt: 34.5 }, { az: 70, alt: 22.5 }, { az: 77.5, alt: 30 }, { az: 274, alt: 14 }, { az: 327, alt: 28.5 }], 45.5, 21.75, [{ az: 25, alt: 2.5 }, { az: 38.5, alt: 34.5 }, { az: 46, alt: 22 }, { az: 70, alt: 22.5 }, { az: 77.5, alt: 30 }, { az: 274, alt: 14 }, { az: 327, alt: 28.5 }]],
  [[{ az: 9.5, alt: 13.5 }, { az: 23, alt: 32.5 }, { az: 104, alt: 11.5 }, { az: 150.5, alt: 29 }, { az: 296.5, alt: 6.5 }, { az: 332, alt: 27.5 }], 120.75, 56.75, [{ az: 9.5, alt: 13.5 }, { az: 23, alt: 32.5 }, { az: 104, alt: 11.5 }, { az: 121, alt: 57 }, { az: 150.5, alt: 29 }, { az: 296.5, alt: 6.5 }, { az: 332, alt: 27.5 }]],
  [[{ az: 101.5, alt: 16 }, { az: 102.5, alt: -0 }, { az: 174, alt: 27.5 }, { az: 223.5, alt: 14 }, { az: 294, alt: 22 }], 154, 35, [{ az: 101.5, alt: 16 }, { az: 102.5, alt: -0 }, { az: 154, alt: 35 }, { az: 174, alt: 27.5 }, { az: 223.5, alt: 14 }, { az: 294, alt: 22 }]],
  [[{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }], 45.5, 12.5, [{ az: 0, alt: 10 }, { az: 46, alt: 13 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }]],
  [[{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }], 44.5, -2.5, [{ az: 0, alt: 10 }, { az: 45, alt: -2 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }]],
  [[{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }], 90, 20, [{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }]],
  [[{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }], -0.4, -0.4, [{ az: 0, alt: 10 }, { az: -0, alt: -0 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }]],
  [[{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }], 359.5, 90, [{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }, { az: 360, alt: 90 }]],
  [[{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }], NaN, 3, [{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }, { az: NaN, alt: 3 }]],
  [[{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }], 10, NaN, [{ az: 0, alt: 10 }, { az: 10, alt: NaN }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }]],
  [[{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }], Infinity, 1, [{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }, { az: Infinity, alt: 1 }]],
  [[{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }], -Infinity, 1, [{ az: -Infinity, alt: 1 }, { az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }]],
  [[], 200.5, 7.5, [{ az: 201, alt: 8 }]],
];
const GOLDEN_MOVE: GoldenMove[] = [
  [[{ az: 90, alt: 13 }, { az: 115, alt: 28 }, { az: 134, alt: 7.5 }, { az: 160, alt: 26 }], 2, 242.5, -85.75, [{ az: 90, alt: 13 }, { az: 115, alt: 28 }, { az: 159, alt: -8 }, { az: 160, alt: 26 }]],
  [[{ az: 287.5, alt: -4.5 }], 0, 403.25, 135.75, [{ az: 359, alt: 90 }]],
  [[{ az: 79.5, alt: 33 }, { az: 219.5, alt: 28 }, { az: 310.5, alt: 17.5 }], 1, 206.75, 82.75, [{ az: 79.5, alt: 33 }, { az: 207, alt: 83 }, { az: 310.5, alt: 17.5 }]],
  [[{ az: 247.5, alt: 22.5 }, { az: 295.5, alt: 5 }, { az: 336, alt: -4.5 }], 0, 208.5, 8, [{ az: 209, alt: 8 }, { az: 295.5, alt: 5 }, { az: 336, alt: -4.5 }]],
  [[{ az: 7.5, alt: 2 }, { az: 158, alt: 13.5 }, { az: 198, alt: 16.5 }], 0, 101, 24.5, [{ az: 101, alt: 25 }, { az: 158, alt: 13.5 }, { az: 198, alt: 16.5 }]],
  [[{ az: 19, alt: 17.5 }, { az: 137, alt: 9 }, { az: 151, alt: 6.5 }, { az: 167, alt: 10 }, { az: 201.5, alt: 1 }], 0, 281.5, 43, [{ az: 136, alt: 43 }, { az: 137, alt: 9 }, { az: 151, alt: 6.5 }, { az: 167, alt: 10 }, { az: 201.5, alt: 1 }]],
  [[{ az: 171.5, alt: 30.5 }, { az: 176, alt: 32.5 }], 0, 387.5, 47, [{ az: 175, alt: 47 }, { az: 176, alt: 32.5 }]],
  [[{ az: 123.5, alt: -1.5 }, { az: 331.5, alt: 6 }], 0, 354, 161.75, [{ az: 331, alt: 90 }, { az: 331.5, alt: 6 }]],
  [[{ az: 197, alt: 14 }, { az: 215.5, alt: 17 }, { az: 345.5, alt: 24 }], 1, 224.75, 45.5, [{ az: 197, alt: 14 }, { az: 225, alt: 46 }, { az: 345.5, alt: 24 }]],
  [[{ az: 23, alt: 19.5 }, { az: 106.5, alt: 2 }, { az: 121.5, alt: 2.5 }, { az: 143.5, alt: 32 }, { az: 233, alt: 6 }, { az: 234, alt: 23.5 }], 1, 198.75, 116, [{ az: 23, alt: 19.5 }, { az: 121, alt: 90 }, { az: 121.5, alt: 2.5 }, { az: 143.5, alt: 32 }, { az: 233, alt: 6 }, { az: 234, alt: 23.5 }]],
  [[{ az: 206.5, alt: 16 }], 0, 330, 102.5, [{ az: 330, alt: 90 }]],
  [[{ az: 149, alt: 28 }, { az: 259.5, alt: 28.5 }, { az: 272, alt: 25.5 }], 0, 28.5, 148.5, [{ az: 29, alt: 90 }, { az: 259.5, alt: 28.5 }, { az: 272, alt: 25.5 }]],
  [[{ az: 251.5, alt: 9.5 }, { az: 312, alt: 19.5 }], 0, 352.25, -98, [{ az: 311, alt: -8 }, { az: 312, alt: 19.5 }]],
  [[{ az: 97.5, alt: 9 }, { az: 211.5, alt: -3 }, { az: 280.5, alt: 21 }, { az: 284.5, alt: 31 }, { az: 328, alt: 6 }], 3, 32, 160.25, [{ az: 97.5, alt: 9 }, { az: 211.5, alt: -3 }, { az: 280.5, alt: 21 }, { az: 282, alt: 90 }, { az: 328, alt: 6 }]],
  [[{ az: 54.5, alt: 20 }, { az: 138, alt: 18.5 }, { az: 210, alt: 20.5 }], 2, 246.5, -9.5, [{ az: 54.5, alt: 20 }, { az: 138, alt: 18.5 }, { az: 247, alt: -8 }]],
  [[{ az: 187.5, alt: 27 }], 0, -36, 95.75, [{ az: 0, alt: 90 }]],
  [[{ az: 50, alt: 23 }, { az: 104, alt: 22.5 }, { az: 144.5, alt: 33 }, { az: 336, alt: 24.5 }, { az: 336.5, alt: 18.5 }], 4, 292.5, -86.75, [{ az: 50, alt: 23 }, { az: 104, alt: 22.5 }, { az: 144.5, alt: 33 }, { az: 336, alt: 24.5 }, { az: 337, alt: -8 }]],
  [[{ az: 133, alt: 15 }, { az: 186, alt: 12 }, { az: 194, alt: 15 }, { az: 269.5, alt: 0 }], 1, 417.25, 116.5, [{ az: 133, alt: 15 }, { az: 193, alt: 90 }, { az: 194, alt: 15 }, { az: 269.5, alt: 0 }]],
  [[{ az: 56.5, alt: 22.5 }, { az: 111, alt: 10.5 }, { az: 112.5, alt: 17 }, { az: 160, alt: -0.5 }, { az: 304, alt: 7.5 }], 4, 40, 192.75, [{ az: 56.5, alt: 22.5 }, { az: 111, alt: 10.5 }, { az: 112.5, alt: 17 }, { az: 160, alt: -0.5 }, { az: 161, alt: 90 }]],
  [[{ az: 118, alt: 15.5 }], 0, 170.5, 172.75, [{ az: 171, alt: 90 }]],
  [[{ az: 159.5, alt: -2 }, { az: 221.5, alt: 8.5 }], 0, 197.5, 167.5, [{ az: 198, alt: 90 }, { az: 221.5, alt: 8.5 }]],
  [[{ az: 136, alt: 6 }, { az: 240.5, alt: 10 }, { az: 289.5, alt: 31.5 }, { az: 306, alt: 10 }], 0, 186.75, 156.5, [{ az: 187, alt: 90 }, { az: 240.5, alt: 10 }, { az: 289.5, alt: 31.5 }, { az: 306, alt: 10 }]],
  [[{ az: 211, alt: 29 }, { az: 315.5, alt: -4 }], 1, 374.25, 143.5, [{ az: 211, alt: 29 }, { az: 359, alt: 90 }]],
  [[{ az: 60.5, alt: 31.5 }], 0, 238, 105.25, [{ az: 238, alt: 90 }]],
  [[{ az: 19, alt: 26.5 }, { az: 59, alt: 19 }, { az: 159, alt: 1.5 }, { az: 162, alt: 26 }, { az: 194.5, alt: 30.5 }, { az: 251, alt: 9.5 }], 3, 248.75, 183.25, [{ az: 19, alt: 26.5 }, { az: 59, alt: 19 }, { az: 159, alt: 1.5 }, { az: 194, alt: 90 }, { az: 194.5, alt: 30.5 }, { az: 251, alt: 9.5 }]],
  [[{ az: 148, alt: 4.5 }, { az: 345, alt: 33 }], 1, 340.25, 82.75, [{ az: 148, alt: 4.5 }, { az: 340, alt: 83 }]],
  [[{ az: 297.5, alt: 20.5 }], 0, 340.25, 15.25, [{ az: 340, alt: 15 }]],
  [[{ az: 4.5, alt: -4 }, { az: 240, alt: 34.5 }, { az: 319.5, alt: -4.5 }], 0, 166.5, 168, [{ az: 167, alt: 90 }, { az: 240, alt: 34.5 }, { az: 319.5, alt: -4.5 }]],
  [[{ az: 134, alt: 31 }, { az: 143, alt: 23.5 }, { az: 184.5, alt: -3 }, { az: 219, alt: -5 }, { az: 241, alt: 3.5 }], 1, 208.5, -19.25, [{ az: 134, alt: 31 }, { az: 184, alt: -8 }, { az: 184.5, alt: -3 }, { az: 219, alt: -5 }, { az: 241, alt: 3.5 }]],
  [[{ az: 37, alt: 7 }, { az: 97.5, alt: 18 }, { az: 200.5, alt: 7.5 }], 2, 211.75, 2.75, [{ az: 37, alt: 7 }, { az: 97.5, alt: 18 }, { az: 212, alt: 3 }]],
  [[{ az: 34, alt: 4 }, { az: 181, alt: -1 }, { az: 231.5, alt: -0 }, { az: 305, alt: 18.5 }, { az: 320.5, alt: 13.5 }, { az: 327, alt: 11 }], 1, 292, 143.5, [{ az: 34, alt: 4 }, { az: 231, alt: 90 }, { az: 231.5, alt: -0 }, { az: 305, alt: 18.5 }, { az: 320.5, alt: 13.5 }, { az: 327, alt: 11 }]],
  [[{ az: 109.5, alt: -2.5 }, { az: 128.5, alt: 28 }, { az: 133, alt: 32.5 }, { az: 185, alt: 15 }, { az: 198, alt: 22.5 }, { az: 216, alt: 2.5 }], 2, -5.75, -32, [{ az: 109.5, alt: -2.5 }, { az: 128.5, alt: 28 }, { az: 130, alt: -8 }, { az: 185, alt: 15 }, { az: 198, alt: 22.5 }, { az: 216, alt: 2.5 }]],
  [[{ az: 201.5, alt: 18 }], 0, 317, -29.75, [{ az: 317, alt: -8 }]],
  [[{ az: 314.5, alt: 21 }], 0, 5.75, -38.25, [{ az: 6, alt: -8 }]],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], 1, 999, 999, [{ az: 10, alt: 5 }, { az: 99, alt: 90 }, { az: 100, alt: 8 }]],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], 1, -999, -999, [{ az: 10, alt: 5 }, { az: 11, alt: -8 }, { az: 100, alt: 8 }]],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], 0, -50, 0, [{ az: 0, alt: 0 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }]],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], 2, 999, 0, [{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 359, alt: 0 }]],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], 1, 50.5, 12.5, [{ az: 10, alt: 5 }, { az: 51, alt: 13 }, { az: 100, alt: 8 }]],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], 1, 49.5, 12.5, [{ az: 10, alt: 5 }, { az: 50, alt: 13 }, { az: 100, alt: 8 }]],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], 1, 75, -8.5, [{ az: 10, alt: 5 }, { az: 75, alt: -8 }, { az: 100, alt: 8 }]],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], 1, 75, 90.5, [{ az: 10, alt: 5 }, { az: 75, alt: 90 }, { az: 100, alt: 8 }]],
  [[{ az: 10, alt: 5 }, { az: 11, alt: 10 }, { az: 12, alt: 8 }], 1, 11.5, 0, [{ az: 10, alt: 5 }, { az: 11, alt: 0 }, { az: 12, alt: 8 }]],
  [[{ az: 10, alt: 5 }, { az: 11, alt: 10 }, { az: 12, alt: 8 }], 1, 30, 0, [{ az: 10, alt: 5 }, { az: 11, alt: 0 }, { az: 12, alt: 8 }]],
  [[{ az: 20, alt: 5 }, { az: 20, alt: 10 }], 0, 20, 1, [{ az: 19, alt: 1 }, { az: 20, alt: 10 }]],
  [[{ az: 20, alt: 5 }, { az: 20, alt: 10 }], 1, 5, 1, [{ az: 20, alt: 5 }, { az: 21, alt: 1 }]],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], -1, 30, 5, null],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], 3, 30, 5, null],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], 1, NaN, 5, [{ az: 10, alt: 5 }, { az: NaN, alt: 5 }, { az: 100, alt: 8 }]],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], 1, 30, NaN, [{ az: 10, alt: 5 }, { az: 30, alt: NaN }, { az: 100, alt: 8 }]],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], 1, Infinity, Infinity, [{ az: 10, alt: 5 }, { az: 99, alt: 90 }, { az: 100, alt: 8 }]],
  [[{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }], 1, -0.4, -0.4, [{ az: 10, alt: 5 }, { az: 11, alt: -0 }, { az: 100, alt: 8 }]],
  [[{ az: 0, alt: 0 }], 0, 359.6, 0.5, [{ az: 359, alt: 1 }]],
  [[{ az: 0, alt: 0 }], 0, -0.4, 0.4, [{ az: 0, alt: 0 }]],
];

test('insertPoint without a grid is bit for bit what it was: the golden cases', () => {
  assert.ok(GOLDEN_INSERT.length >= 30);
  for (const [points, az, alt, expected] of GOLDEN_INSERT) {
    const input = points.map(p => ({ ...p }));
    const out = insertPoint(input, az, alt);
    assert.ok(samePts(out, expected), `insertPoint(${fmt(points)}, ${az}, ${alt}): ${fmt(out)} vs ${fmt(expected)}`);
    assert.ok(samePts(input, points), 'the argument array was written');
    assert.ok(samePts(insertPoint(input, az, alt, undefined), expected), 'an explicit undefined grid is no grid');
  }
});

test('movePoint without a grid is bit for bit what it was: the golden cases', () => {
  assert.ok(GOLDEN_MOVE.length >= 50);
  for (const [points, i, az, alt, expected] of GOLDEN_MOVE) {
    for (const explicitUndefined of [false, true]) {
      const input = points.map(p => ({ ...p }));
      const out = explicitUndefined ? movePoint(input, i, az, alt, undefined) : movePoint(input, i, az, alt);
      if (expected === null) assert.equal(out, input, `movePoint(${fmt(points)}, ${i}) with no such point returns the array it was given`);
      else assert.ok(samePts(out, expected), `movePoint(${fmt(points)}, ${i}, ${az}, ${alt}): ${fmt(out)} vs ${fmt(expected)}`);
      assert.ok(samePts(input, points), 'the argument array was written');
    }
  }
});

/** The bodies of insertPoint and movePoint as they were before T24, to widen the golden cases to random inputs. */
function legacyInsert(points: HorizonPoint[], az: number, alt: number): HorizonPoint[] {
  const next = [...points, { az: Math.round(az), alt: Math.round(alt) }];
  next.sort((p, q) => p.az - q.az);
  return next;
}
function legacyMove(points: HorizonPoint[], i: number, az: number, alt: number): HorizonPoint[] {
  if (i < 0 || i >= points.length) return points;
  const next = points.map((p) => ({ ...p }));
  const lo = i > 0 ? next[i - 1].az + 1 : 0;
  const hi = i < next.length - 1 ? next[i + 1].az - 1 : 359;
  next[i] = {
    az: Math.round(Math.max(lo, Math.min(hi, az))),
    alt: Math.round(Math.max(-8, Math.min(90, alt))),
  };
  return next;
}

/** A sorted line of `n` vertices on whole or half degrees. */
function lineOf(r: () => number, n: number, step = 0.5): HorizonPoint[] {
  const azs = new Set<number>();
  while (azs.size < n) azs.add(Math.round(r() * 359.5 / step) * step);
  return [...azs].sort((a, b) => a - b).map(az => ({ az, alt: Math.round((r() * 45 - 5) * 2) / 2 }));
}

/** A line whose vertices are on the half degree, at least 1 degree apart, the last at or below 358. */
function spacedLine(r: () => number): HorizonPoint[] {
  const pts: HorizonPoint[] = [];
  for (let az = Math.floor(r() * 20) * 0.5; az <= 358 && pts.length < 12; az += 1 + 0.5 * Math.floor(r() * 30)) {
    pts.push({ az, alt: Math.round((r() * 45 - 5) * 2) / 2 });
  }
  return pts;
}

test('insertPoint and movePoint without a grid agree with the pre-T24 bodies on 3000 random inputs', () => {
  const r = rng(24);
  for (let k = 0; k < 3000; k++) {
    const line = lineOf(r, Math.floor(r() * 8));
    const az = (r() * 440 - 40), alt = (r() * 160 - 50);
    assert.ok(samePts(insertPoint(line, az, alt), legacyInsert(line, az, alt)), `insert case ${k}`);
    if (line.length) {
      const i = Math.floor(r() * (line.length + 2)) - 1;
      assert.ok(samePts(movePoint(line, i, az, alt), legacyMove(line, i, az, alt)), `move case ${k}`);
    }
  }
});

const lastOf = (a: readonly HorizonPoint[]) => a[a.length - 1];
const TRI: HorizonPoint[] = [{ az: 10, alt: 5 }, { az: 50, alt: 10 }, { az: 100, alt: 8 }];
const SQUARE: HorizonPoint[] = [{ az: 0, alt: 10 }, { az: 90, alt: 20 }, { az: 180, alt: 5 }, { az: 270, alt: 15 }];

test('insertPoint with grid 0.5 rounds both coordinates to the half degree and keeps the line sorted', () => {
  const at = (az: number, alt: number, k: number) => {
    const out = insertPoint(SQUARE, az, alt, 0.5);
    assert.equal(out.length, 5);
    return out[k];
  };
  assert.deepEqual(at(45.3, 12.2, 1), { az: 45.5, alt: 12 });
  assert.deepEqual(at(45.24, 12.26, 1), { az: 45, alt: 12.5 });
  assert.deepEqual(at(45.25, 12.25, 1), { az: 45.5, alt: 12.5 }, 'halves round up, as Math.round');
  assert.deepEqual(at(135.75, 7.74, 2), { az: 136, alt: 7.5 });
  assert.deepEqual(at(200.1, 91, 3), { az: 200, alt: 91 }, 'altitude is the caller\'s to clamp, as without a grid');
  const sorted = insertPoint(SQUARE, 100.2, 1, 0.5);
  assert.deepEqual(sorted.map(p => p.az), [0, 90, 100, 180, 270]);
});

test('insertPoint with a grid holds az in [0, 360 - grid]: 360 is not a place, and a negative azimuth is 0', () => {
  assert.equal(lastOf(insertPoint(SQUARE, 359.8, 3, 0.5)).az, 359.5);
  assert.equal(lastOf(insertPoint(SQUARE, 400, 3, 0.5)).az, 359.5);
  assert.equal(lastOf(insertPoint(SQUARE, 359.74, 3, 0.5)).az, 359.5);
  const low = insertPoint(SQUARE, -3, 3, 0.5);
  assert.deepEqual(low.map(p => p.az), [0, 0, 90, 180, 270]);
  assert.deepEqual(low[1], { az: 0, alt: 3 }, 'stable: the new point follows the one already at 0');
  assert.equal(lastOf(insertPoint(SQUARE, 359.5, 3, 0.5)).az, 359.5);
  assert.equal(lastOf(insertPoint(SQUARE, 359.8, 3, 1)).az, 359, 'the ceiling is 360 minus the grid');
});

test('insertPoint with a grid never yields -0, and takes any positive grid', () => {
  const p = insertPoint([], -0.2, -0.2, 0.5)[0];
  assert.ok(Object.is(p.az, 0) && Object.is(p.alt, 0), `got ${fmt([p])}`);
  assert.deepEqual(insertPoint([], 45.9, 12.9, 2)[0], { az: 46, alt: 12 });
  assert.deepEqual(insertPoint([], 45.9, 12.9, 0.25)[0], { az: 46, alt: 13 });
  assert.deepEqual(insertPoint([], 7, 7, 5)[0], { az: 5, alt: 5 });
});

test('movePoint with grid 0.5 rounds to the half degree and stays 0.5 clear of its neighbours', () => {
  assert.deepEqual(movePoint(TRI, 1, 50.3, 12.2, 0.5)[1], { az: 50.5, alt: 12 });
  assert.deepEqual(movePoint(TRI, 1, 50.24, 12.26, 0.5)[1], { az: 50, alt: 12.5 });
  assert.deepEqual(movePoint(TRI, 1, 999, 999, 0.5)[1], { az: 99.5, alt: 90 }, 'the next vertex is at 100');
  assert.deepEqual(movePoint(TRI, 1, -999, -999, 0.5)[1], { az: 10.5, alt: -8 }, 'the previous vertex is at 10');
  assert.deepEqual(movePoint(TRI, 1, 10.2, 5, 0.5)[1], { az: 10.5, alt: 5 }, 'rounds to 10, then keeps clear of the neighbour');
  assert.deepEqual(movePoint(TRI, 1, 99.8, 5, 0.5)[1], { az: 99.5, alt: 5 });
  assert.deepEqual(movePoint(TRI, 1, 50, 90.4, 0.5)[1], { az: 50, alt: 90 }, 'altitude is clamped to -8..90 and then rounded');
  assert.deepEqual(movePoint(TRI, 1, 50, -8.3, 0.5)[1], { az: 50, alt: -8 });
  assert.deepEqual(movePoint(TRI, 1, 50, 89.8, 0.5)[1], { az: 50, alt: 90 });
});

test('movePoint with a grid: the ends run from 0 to 360 - grid, and the other points do not move', () => {
  assert.equal(movePoint(TRI, 0, -50, 0, 0.5)[0].az, 0);
  assert.equal(movePoint(TRI, 2, 999, 0, 0.5)[2].az, 359.5);
  assert.equal(movePoint(TRI, 2, 359.8, 0, 0.5)[2].az, 359.5);
  assert.equal(movePoint(TRI, 2, 359.8, 0, 2)[2].az, 358);
  const out = movePoint(TRI, 1, 70.5, 3.5, 0.5);
  assert.deepEqual(out[0], TRI[0]);
  assert.deepEqual(out[2], TRI[2]);
  assert.ok(Object.is(movePoint([{ az: 5, alt: 5 }], 0, -0.2, -0.2, 0.5)[0].az, 0), 'no -0');
  assert.ok(Object.is(movePoint([{ az: 5, alt: 5 }], 0, 5, -0.2, 0.5)[0].alt, 0), 'no -0');
});

test('movePoint with a grid keeps an off-grid neighbour uncrossed, and returns a new array without writing the old', () => {
  const odd: HorizonPoint[] = [{ az: 10.3, alt: 5 }, { az: 50, alt: 10 }, { az: 100.2, alt: 8 }];
  const copy = JSON.stringify(odd);
  const out = movePoint(odd, 1, 10.4, 5, 0.5);
  assert.ok(Math.abs(out[1].az - 10.8) < 1e-9, `got ${out[1].az}`);
  assert.ok(Math.abs(movePoint(odd, 1, 400, 5, 0.5)[1].az - 99.7) < 1e-9);
  assert.equal(JSON.stringify(odd), copy);
  assert.notEqual(out, odd);
  assert.notEqual(out[0], odd[0], 'the points are copies');
});

test('movePoint with a grid and an index with no point returns the array it was given', () => {
  assert.equal(movePoint(TRI, -1, 30, 5, 0.5), TRI);
  assert.equal(movePoint(TRI, 3, 30, 5, 0.5), TRI);
  assert.equal(movePoint([], 0, 30, 5, 0.5).length, 0);
});

test('movePoint with grid 1 is the old behaviour on whole-degree lines (the grid generalises the old rule)', () => {
  const sameValues = (a: readonly Pt[], b: readonly Pt[]) => a.length === b.length && a.every((p, k) => p.az === b[k].az && p.alt === b[k].alt);   // 0 equals -0: a grid never writes -0
  const r = rng(99);
  for (let k = 0; k < 1500; k++) {
    const line = lineOf(r, 1 + Math.floor(r() * 7), 1).filter(p => p.az <= 357);
    if (!line.length) continue;
    const i = Math.floor(r() * line.length);
    const az = r() * 440 - 40, alt = r() * 160 - 50;
    assert.ok(sameValues(movePoint(line, i, az, alt, 1), legacyMove(line, i, az, alt)), `case ${k}: ${fmt(line)} ${i} ${az} ${alt}`);
  }
});

test('movePoint with grid 0.5 keeps a line sorted, inside [0, 359.5], and each moved point 0.5 from its neighbours', () => {
  const r = rng(7);
  for (let k = 0; k < 800; k++) {
    const line = spacedLine(r);
    const i = Math.floor(r() * line.length);
    const out = movePoint(line, i, r() * 480 - 60, r() * 160 - 50, 0.5);
    for (let j = 0; j < out.length; j++) {
      assert.ok(out[j].az >= 0 && out[j].az <= 359.5, `case ${k}: az ${out[j].az}`);
      if (j > 0) assert.ok(out[j].az > out[j - 1].az, `case ${k}: ${fmt(out)} is not strictly increasing`);
    }
    assert.ok(out[i].az * 2 === Math.round(out[i].az * 2) && out[i].alt * 2 === Math.round(out[i].alt * 2), `case ${k}: off the grid`);
    assert.ok(out[i].alt >= -8 && out[i].alt <= 90);
    if (i > 0) assert.ok(out[i].az - out[i - 1].az >= 0.5);
    if (i < out.length - 1) assert.ok(out[i + 1].az - out[i].az >= 0.5);
  }
});

test('a grid that is not a positive finite number is refused, by both functions, whatever the index', () => {
  for (const g of [0, -0.5, NaN, Infinity]) {
    assert.throws(() => insertPoint(SQUARE, 10, 10, g), RangeError, `insertPoint grid ${g}`);
    assert.throws(() => movePoint(TRI, 1, 10, 10, g), RangeError, `movePoint grid ${g}`);
    assert.throws(() => movePoint(TRI, 9, 10, 10, g), RangeError, `movePoint grid ${g} with no such point`);
  }
});

console.log(`horizonDraft.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
