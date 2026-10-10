// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T14: the 720-bin profile (SPEC-v2 5.3) and the partial-scan merge (5.5).
//
// Mutant this file must catch (SPEC-v2 7.2): the maximum over a bin's columns replaced by the centre column, so
// that a 0.6-degree pole vanishes. `every single-column spike` puts a spike on each of the 1080 columns in turn and
// requires every bin that column touches to carry it in full, which no single column per bin can do (the two columns
// of a bin are both needed, and the shared column feeds two bins). `a 0.6-degree pole` is the case the spec names.
//
// The expected bin of a column is computed from the geometry (column x spans [x / 3, (x + 1) / 3) degrees, bin i spans
// [i / 2, (i + 1) / 2)), as the integer inequality 2 x < 3 (i + 1) and 2 (x + 1) > 3 i, not from profile.ts's formula.
import assert from 'node:assert/strict';
import { BinState, ColState, PANO_W, PROFILE_BINS, type ColumnHorizon, type HorizonDraft, type HorizonPoint } from '../../types';
import { WORST_ORDER, binOfAz, binProfile, mergeKept } from '../profile';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

/** A column set with every column Measured at altitude 0, photo top 20, unless told otherwise. */
function columnSet(init: { alt?: number; state?: number; top?: number; lowLight?: boolean } = {}, n = PANO_W): ColumnHorizon {
  return {
    alt: new Float32Array(n).fill(init.alt ?? 0),
    state: new Uint8Array(n).fill(init.state ?? ColState.Measured),
    top: new Float32Array(n).fill(init.top ?? 20),
    bottom: new Float32Array(n).fill(-5),
    contrastSigma: new Float32Array(n).fill(10),
    sigmaDeg: new Float32Array(n).fill(0.1),
    lowLight: init.lowLight ?? false,
  };
}

/** Does raster column x touch bin i? Column x is [x / 3, (x + 1) / 3) degrees, bin i is [i / 2, (i + 1) / 2). */
const touches = (x: number, i: number) => 2 * x < 3 * (i + 1) && 2 * (x + 1) > 3 * i;

/** The bins a raster column touches, by scanning all 720 (independent of the production index arithmetic). */
function binsOfColumn(x: number): number[] {
  const out: number[] = [];
  for (let i = 0; i < PROFILE_BINS; i++) if (touches(x, i)) out.push(i);
  return out;
}

const snapshot = (d: HorizonDraft) => JSON.stringify([Array.from(d.alt), Array.from(d.suggested, v => (Number.isNaN(v) ? 'nan' : v)),
  Array.from(d.state), Array.from(d.reason), Array.from(d.top, v => (Number.isNaN(v) ? 'nan' : v)), d.lowLight]);

// ---- binOfAz and WORST_ORDER ------------------------------------------------

test('binOfAz is floor(wrap(az) * 2) and wraps both ways', () => {
  assert.equal(binOfAz(0), 0);
  assert.equal(binOfAz(0.49), 0);
  assert.equal(binOfAz(0.5), 1);
  assert.equal(binOfAz(100.25), 200);
  assert.equal(binOfAz(100.5), 201);
  assert.equal(binOfAz(359.99), 719);
  assert.equal(binOfAz(360), 0);
  assert.equal(binOfAz(360.5), 1);
  assert.equal(binOfAz(-0.1), 719);
  assert.equal(binOfAz(-0.5), 719);
  assert.equal(binOfAz(-0.6), 718);
  assert.equal(binOfAz(720.75), 1);
  assert.equal(binOfAz(-3e-14), 719, 'a rounding error below 0 is the last bin, not bin 720');
  assert.equal(binOfAz(-1e-14), 0, 'a value that rounds to 360 lands on 0');
  for (let i = 0; i < PROFILE_BINS; i++) {
    assert.equal(binOfAz(i / 2), i, `left edge of bin ${i}`);
    assert.equal(binOfAz(i / 2 + 0.25), i, `centre of bin ${i}`);
  }
});

test('WORST_ORDER is the ColState order of SPEC-v2 5.3, worst first', () => {
  assert.deepEqual([...WORST_ORDER], [ColState.UnknownUnseen, ColState.UnknownDark, ColState.UnknownContrast, ColState.Tall, ColState.Low, ColState.Measured]);
  assert.deepEqual([...WORST_ORDER], [2, 3, 4, 5, 1, 0]);
  assert.equal(new Set(WORST_ORDER).size, 6);
});

// ---- binProfile: the maximum --------------------------------------------------

test('a 0.6-degree pole fills its bin at full height, at every phase of the raster', () => {
  // 0.6 degree is 1.8 columns: 1 or 2 columns have their centre inside it, depending on the phase.
  let sawOne = false, sawTwo = false;
  for (let k = 0; k < 40; k++) {
    const az0 = 100 + k / 120;             // one third of a degree (a column) in 40 steps
    const c = columnSet();
    const marked: number[] = [];
    for (let x = 0; x < PANO_W; x++) {
      const centre = (x + 0.5) / 3;
      if (centre >= az0 && centre < az0 + 0.6) { c.alt[x] = 40; marked.push(x); }
    }
    assert.ok(marked.length === 1 || marked.length === 2, `phase ${k}: ${marked.length} columns marked`);
    if (marked.length === 1) sawOne = true; else sawTwo = true;
    const d = binProfile(c);
    const full = new Set<number>();
    for (const x of marked) for (const i of binsOfColumn(x)) full.add(i);
    for (let i = 0; i < PROFILE_BINS; i++) {
      assert.equal(d.alt[i], full.has(i) ? 40 : 0, `phase ${k}, bin ${i}`);
      assert.equal(d.state[i], BinState.Measured);
    }
    // The bin that holds the pole's centre is among the full ones: the pole is not lost between two bins.
    assert.ok(full.has(binOfAz(az0 + 0.3)), `phase ${k}: the bin of the pole's centre is full`);
  }
  assert.ok(sawOne && sawTwo, 'the sweep covers both phases');
});

test('the worked example: a pole at 100.2 to 100.8 degrees is column 301 and fills bins 200 and 201 at 40 degrees', () => {
  const c = columnSet();
  c.alt[301] = 40;                           // centre (301 + 0.5) / 3 = 100.5 degrees
  const d = binProfile(c);
  assert.equal(d.alt[200], 40);
  assert.equal(d.alt[201], 40);
  assert.equal(d.alt[199], 0);
  assert.equal(d.alt[202], 0);
  assert.equal(d.suggested[200], 40);
});

test('every single-column spike fills exactly the bins its column touches, in altitude and in state', () => {
  // Kills a single column per bin of any choice (centre, left, right): each bin needs both of its columns.
  for (let x = 0; x < PANO_W; x++) {
    const touched = new Set(binsOfColumn(x));
    assert.ok(touched.size === 1 || touched.size === 2, `column ${x} touches ${touched.size} bins`);

    const spike = columnSet();
    spike.alt[x] = 40;
    const d = binProfile(spike);
    for (let i = 0; i < PROFILE_BINS; i++) assert.equal(d.alt[i], touched.has(i) ? 40 : 0, `spike column ${x}, bin ${i}`);

    const low = columnSet();
    low.alt[x] = 40; low.state[x] = ColState.Low;
    const e = binProfile(low);
    for (let i = 0; i < PROFILE_BINS; i++) {
      assert.equal(e.state[i], touched.has(i) ? BinState.Low : BinState.Measured, `low column ${x}, bin ${i}`);
      assert.equal(e.suggested[i], touched.has(i) ? 40 : 0);
    }
  }
});

test('a bin takes the higher of its two columns', () => {
  const c = columnSet();
  c.alt[3] = 7; c.alt[4] = 9.5;              // bin 2 is columns 3 and 4
  c.alt[5] = 2;                              // bin 3 is columns 4 and 5
  const d = binProfile(c);
  assert.equal(d.alt[2], 9.5);
  assert.equal(d.alt[3], 9.5);
  assert.equal(d.alt[4], 0);                 // columns 6 and 7; column 5 belongs to bin 3 only
  assert.equal(d.alt[1], 0);                 // columns 1 and 2; column 3 belongs to bin 2 only
});

// ---- binProfile: states ---------------------------------------------------------

test('a run reaching the top is Tall: 90 published, reason Tall, the traced altitude kept as the suggestion', () => {
  const c = columnSet({ alt: 55, state: ColState.Tall, top: 12 });
  const d = binProfile(c);
  for (let i = 0; i < PROFILE_BINS; i++) {
    assert.equal(d.state[i], BinState.Tall);
    assert.equal(d.reason[i], ColState.Tall);
    assert.equal(d.alt[i], 90);
    assert.equal(d.suggested[i], 55);
    assert.equal(d.top[i], 12);
  }
  // A Tall column with no altitude: still Tall, no suggestion.
  const e = binProfile(columnSet({ alt: NaN, state: ColState.Tall, top: 12 }));
  assert.equal(e.state[10], BinState.Tall);
  assert.ok(Number.isNaN(e.suggested[10]));
  assert.equal(e.alt[10], 90);
});

test('unobserved is Unknown with reason UnknownUnseen, 90 published, no suggestion and no photo top', () => {
  const d = binProfile(columnSet({ alt: NaN, state: ColState.UnknownUnseen, top: NaN }));
  for (let i = 0; i < PROFILE_BINS; i++) {
    assert.equal(d.state[i], BinState.Unknown);
    assert.equal(d.reason[i], ColState.UnknownUnseen);
    assert.equal(d.alt[i], 90);
    assert.ok(Number.isNaN(d.suggested[i]));
    assert.ok(Number.isNaN(d.top[i]));
  }
});

test('dark and contrast bins are Unknown at 90 with their own reason', () => {
  for (const [col, name] of [[ColState.UnknownDark, 'dark'], [ColState.UnknownContrast, 'contrast']] as const) {
    const d = binProfile(columnSet({ alt: NaN, state: col, top: 9 }));
    for (const i of [0, 1, 359, 360, 719]) {
      assert.equal(d.state[i], BinState.Unknown, name);
      assert.equal(d.reason[i], col, name);
      assert.equal(d.alt[i], 90, name);
      assert.equal(d.top[i], 9, name);
    }
  }
});

test('Low publishes 90 with suggested = A, the higher of the bin\'s columns', () => {
  const c = columnSet({ alt: 11, state: ColState.Low });
  c.alt[300] = 12.5;                         // bin 200 is columns 300 and 301
  const d = binProfile(c);
  assert.equal(d.state[200], BinState.Low);
  assert.equal(d.reason[200], ColState.Low);
  assert.equal(d.alt[200], 90);
  assert.equal(d.suggested[200], 12.5);
  assert.equal(d.alt[250], 90);
  assert.equal(d.suggested[250], 11);
});

test('Measured publishes A: the higher of the bin\'s columns, 0 included', () => {
  const c = columnSet({ alt: 0 });
  c.alt[600] = 3.25; c.alt[601] = 1;         // bin 400
  const d = binProfile(c);
  assert.equal(d.state[400], BinState.Measured);
  assert.equal(d.reason[400], ColState.Measured);
  assert.equal(d.alt[400], 3.25);
  assert.equal(d.suggested[400], 3.25);
  assert.equal(d.alt[10], 0);
  assert.equal(d.top[10], 20);
});

test('a bin takes the worst state of its columns, over all 216 triples of states', () => {
  const names = ['UnknownUnseen', 'UnknownDark', 'UnknownContrast', 'Tall', 'Low', 'Measured'] as const;   // SPEC-v2 5.3, worst first
  const worse = (p: number, q: number) => (names.findIndex(n => ColState[n] === p) <= names.findIndex(n => ColState[n] === q) ? p : q);
  const binState = (s: number) => (s === ColState.Measured ? BinState.Measured : s === ColState.Low ? BinState.Low : s === ColState.Tall ? BinState.Tall : BinState.Unknown);
  for (const a of names) for (const b of names) for (const e of names) {
    const c = columnSet({ alt: 5 });
    c.state[3] = ColState[a]; c.state[4] = ColState[b]; c.state[5] = ColState[e];   // bin 2 is columns 3, 4; bin 3 is 4, 5
    const d = binProfile(c);
    assert.equal(d.reason[2], worse(ColState[a], ColState[b]), `${a}/${b}`);
    assert.equal(d.state[2], binState(worse(ColState[a], ColState[b])), `${a}/${b}`);
    assert.equal(d.reason[3], worse(ColState[b], ColState[e]), `${b}/${e}`);
    assert.equal(d.state[3], binState(worse(ColState[b], ColState[e])), `${b}/${e}`);
    assert.equal(d.alt[2], d.state[2] === BinState.Measured ? 5 : 90);
  }
});

test('a Measured column with no altitude cannot be measured: the bin is Unknown, unseen, at 90', () => {
  const c = columnSet({ alt: 4 });
  c.alt[3] = NaN;                            // Measured by state, but no boundary altitude
  const d = binProfile(c);
  assert.equal(d.state[2], BinState.Unknown);
  assert.equal(d.reason[2], ColState.UnknownUnseen);
  assert.equal(d.alt[2], 90);
  assert.equal(d.suggested[2], 4);           // column 4's altitude is still the suggestion
  assert.equal(d.state[1], BinState.Measured, 'bin 1 (columns 1, 2) is untouched');
});

test('top is the lowest coverage top of the bin\'s columns, NaN only when none has one', () => {
  const c = columnSet({ top: 20 });
  c.top[3] = 14; c.top[4] = NaN;             // bin 2: one top
  c.top[6] = NaN; c.top[7] = NaN;            // bin 4: none
  const d = binProfile(c);
  assert.equal(d.top[2], 14);
  assert.equal(d.top[3], 20);                // columns 4 (NaN) and 5 (20)
  assert.ok(Number.isNaN(d.top[4]));
});

test('lowLight is carried, the shapes are exact, and the input is not written', () => {
  const c = columnSet({ lowLight: true });
  const before = JSON.stringify([Array.from(c.alt), Array.from(c.state), Array.from(c.top)]);
  const d = binProfile(c);
  assert.equal(d.lowLight, true);
  assert.equal(binProfile(columnSet()).lowLight, false);
  assert.ok(d.alt instanceof Float32Array && d.suggested instanceof Float32Array && d.top instanceof Float32Array);
  assert.ok(d.state instanceof Uint8Array && d.reason instanceof Uint8Array);
  for (const a of [d.alt, d.suggested, d.state, d.reason, d.top]) assert.equal(a.length, PROFILE_BINS);
  assert.equal(JSON.stringify([Array.from(c.alt), Array.from(c.state), Array.from(c.top)]), before);
});

test('mismatched column arrays are refused, and another column count maps by the same geometry', () => {
  const bad = columnSet();
  bad.top = new Float32Array(10);
  assert.throws(() => binProfile(bad), RangeError);
  assert.throws(() => binProfile(columnSet({}, 0)), RangeError);

  // 1440 columns: two per bin. 720 columns: one per bin.
  const wide = columnSet({}, 1440);
  wide.alt[7] = 30;                          // bin 3, second column
  const w = binProfile(wide);
  assert.equal(w.alt[3], 30);
  assert.equal(w.alt[2], 0);
  assert.equal(w.alt[4], 0);
  const same = columnSet({}, 720);
  same.alt[9] = 12;
  const s = binProfile(same);
  assert.equal(s.alt[9], 12);
  assert.equal(s.alt[8], 0);
  assert.equal(s.alt[10], 0);
});

// ---- mergeKept ------------------------------------------------------------------

/**
 * Unseen columns 301..450 (az 100.33 to 150.33; no run, no top), dark columns 600..659, everything else Measured at 10.
 * Bin 200 (columns 300, 301) and bin 300 (columns 450, 451) are the two edge bins, half photographed.
 */
function partialDraft(): HorizonDraft {
  const c = columnSet({ alt: 10, top: 25 });
  for (let x = 301; x <= 450; x++) { c.alt[x] = NaN; c.state[x] = ColState.UnknownUnseen; c.top[x] = NaN; }
  for (let x = 600; x <= 659; x++) { c.alt[x] = NaN; c.state[x] = ColState.UnknownDark; }
  return binProfile(c);
}

// The previous line over the unseen sector is the segment (90, 20) to (180, 6): 20 - 14 / 90 per degree after 90.
const PREVIOUS: HorizonPoint[] = [{ az: 0, alt: 4 }, { az: 90, alt: 20 }, { az: 180, alt: 6 }, { az: 270, alt: 30 }];
const previousAt = (az: number) => 20 - (az - 90) * 14 / 90;

test('mergeKept fills only the bins nobody photographed, with the previous line at the bin\'s higher edge', () => {
  const d = partialDraft();
  assert.equal(d.state[200], BinState.Unknown, 'bin 200 is Unknown before the merge');
  assert.equal(d.reason[200], ColState.UnknownUnseen);
  const m = mergeKept(d, PREVIOUS);
  let kept = 0;
  for (let i = 0; i < PROFILE_BINS; i++) {
    if (i >= 201 && i <= 299) {
      kept++;
      assert.equal(m.state[i], BinState.Kept, `bin ${i}`);
      assert.equal(m.reason[i], ColState.UnknownUnseen, `bin ${i} keeps its reason`);
      // The line falls from 20 at 90 degrees to 6 at 180, so the bin's higher edge is its left one.
      assert.ok(Math.abs(m.alt[i] - previousAt(i / 2)) < 1e-5, `bin ${i}: ${m.alt[i]} vs ${previousAt(i / 2)}`);
      assert.ok(Number.isNaN(m.suggested[i]) && Number.isNaN(m.top[i]));
    } else {
      assert.equal(m.state[i], d.state[i], `bin ${i} state`);
      assert.equal(m.alt[i], d.alt[i], `bin ${i} alt`);
      assert.equal(m.reason[i], d.reason[i], `bin ${i} reason`);
    }
  }
  assert.equal(kept, 99);
  assert.equal(m.lowLight, d.lowLight);
});

test('mergeKept leaves dark bins Unknown at 90, and the half-photographed edge bins too', () => {
  const m = mergeKept(partialDraft(), PREVIOUS);
  for (let i = 400; i < 440; i++) {          // bins 400 (columns 600, 601) to 439 (columns 658, 659)
    assert.equal(m.state[i], BinState.Unknown, `dark bin ${i}`);
    assert.equal(m.reason[i], ColState.UnknownDark);
    assert.equal(m.alt[i], 90);
  }
  // Bin 200 has a Measured column beside the unseen one, bin 300 likewise: not every column is unseen.
  for (const i of [200, 300]) {
    assert.equal(m.state[i], BinState.Unknown, `edge bin ${i}`);
    assert.equal(m.alt[i], 90);
    assert.equal(m.suggested[i], 10, 'the measured column\'s altitude is still the suggestion');
  }
  // Measured bins are untouched.
  assert.equal(m.state[100], BinState.Measured);
  assert.equal(m.alt[100], 10);
});

test('mergeKept needs both: an unseen reason and no coverage top. Either one missing leaves the bin Unknown', () => {
  const d = binProfile(columnSet({ alt: NaN, state: ColState.UnknownUnseen, top: NaN }));
  d.reason[10] = ColState.UnknownDark;       // a dark bin that happens to have no top: the reason alone keeps it
  d.reason[11] = ColState.UnknownContrast;
  d.top[20] = 12;                            // an unseen reason with a coverage top (a gap in a photographed column): photographed
  const m = mergeKept(d, PREVIOUS);
  for (const i of [10, 11, 20]) {
    assert.equal(m.state[i], BinState.Unknown, `bin ${i}`);
    assert.equal(m.alt[i], 90, `bin ${i}`);
  }
  for (const i of [9, 12, 19, 21, 400]) assert.equal(m.state[i], BinState.Kept, `bin ${i}`);
  // A bin that is Low, Tall or Measured is never touched, whatever its top.
  const c = columnSet({ alt: 6, state: ColState.Tall, top: NaN });
  assert.equal(mergeKept(binProfile(c), PREVIOUS).state[5], BinState.Tall);
});

test('mergeKept with no previous line, or an empty one, changes nothing', () => {
  const d = partialDraft();
  const before = snapshot(d);
  assert.equal(mergeKept(d, null), d);
  assert.equal(mergeKept(d, []), d);
  assert.equal(snapshot(d), before);
});

test('mergeKept never writes its input and returns independent arrays', () => {
  const d = partialDraft();
  const before = snapshot(d);
  const m = mergeKept(d, PREVIOUS);
  assert.notEqual(m, d);
  assert.equal(snapshot(d), before);
  for (const key of ['alt', 'suggested', 'state', 'reason', 'top'] as const) assert.notEqual(m[key], d[key], key);
  m.alt[201] = 1; m.state[201] = BinState.Edited;
  assert.equal(snapshot(d), before, 'writing the merged draft does not reach the original');
});

test('mergeKept with every bin measured or dark returns the draft itself', () => {
  const d = binProfile(columnSet({ alt: 3 }));
  assert.equal(mergeKept(d, PREVIOUS), d);
  const dark = binProfile(columnSet({ alt: NaN, state: ColState.UnknownDark }));
  assert.equal(mergeKept(dark, PREVIOUS), dark);
});

test('mergeKept: a wholly unseen scan takes the whole previous line, wrapping at 360', () => {
  const d = binProfile(columnSet({ alt: NaN, state: ColState.UnknownUnseen, top: NaN }));
  const m = mergeKept(d, PREVIOUS);
  for (let i = 0; i < PROFILE_BINS; i++) assert.equal(m.state[i], BinState.Kept, `bin ${i}`);
  // Bin 719 is [359.5, 360): the line falls from 30 at 270 to 4 at 360 (= 0), so its higher edge is the left one.
  assert.ok(Math.abs(m.alt[719] - (30 - 26 * 89.5 / 90)) < 1e-5);
  // Bin 0 is [0, 0.5): the line rises from 4 toward 20 at 90, so its higher edge is the right one.
  assert.ok(Math.abs(m.alt[0] - (4 + 16 * 0.5 / 90)) < 1e-5);
  // Bin 180 is [90, 90.5): the peak at 90 is the bin's left edge.
  assert.ok(Math.abs(m.alt[180] - 20) < 1e-5);
  assert.ok(Math.abs(m.alt[179] - 20) < 1e-5, 'bin 179 is [89.5, 90): its right edge is the peak');
});

test('mergeKept counts a previous vertex inside a bin, not only the two edges', () => {
  const d = binProfile(columnSet({ alt: NaN, state: ColState.UnknownUnseen, top: NaN }));
  // A vertex at 100.2 degrees lies inside bin 200 ([100, 100.5)) and above the line at both of its edges.
  const m = mergeKept(d, [{ az: 10, alt: 5 }, { az: 100.2, alt: 50 }, { az: 200, alt: 5 }]);
  assert.equal(m.state[200], BinState.Kept);
  assert.equal(m.alt[200], 50);
  assert.ok(m.alt[199] < 50 && m.alt[201] < 50);
  // Across the wrap: a vertex at 359.8 lies inside bin 719.
  const w = mergeKept(d, [{ az: 10, alt: 5 }, { az: 359.8, alt: 50 }]);
  assert.equal(w.alt[719], 50);
  // A vertex exactly on an edge counts for the bins on both sides (a vertical step in the previous line).
  const e = mergeKept(d, [{ az: 100, alt: 5 }, { az: 100, alt: 40 }, { az: 200, alt: 5 }]);
  assert.equal(e.alt[199], 40);
  assert.equal(e.alt[200], 40);
});

test('binProfile then mergeKept: a scan of 120 degrees over an earlier full line, as Finish composes them', () => {
  // The scan saw 60..180 degrees (columns 180..539) and nothing else.
  const c = columnSet({ alt: NaN, state: ColState.UnknownUnseen, top: NaN });
  for (let x = 180; x < 540; x++) { c.alt[x] = 8; c.state[x] = ColState.Measured; c.top[x] = 30; }
  const previous: HorizonPoint[] = [{ az: 0, alt: 15 }, { az: 180, alt: 15 }];
  const m = mergeKept(binProfile(c), previous);
  for (let i = 0; i < PROFILE_BINS; i++) {
    const seen = i >= 120 && i < 360;
    if (seen) assert.equal(m.state[i], BinState.Measured, `bin ${i}`);
  }
  assert.equal(m.state[50], BinState.Kept);
  assert.equal(m.alt[50], 15);
  assert.equal(m.state[500], BinState.Kept);
  assert.ok(Math.abs(m.alt[500] - 15) < 1e-5);
  assert.equal(m.alt[200], 8);
});

console.log(`profile.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
