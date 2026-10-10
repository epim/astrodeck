// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T10: the column tracer ported to degrees (SPEC-v2 5.1, 5.2) and the noise estimator beside it. The three old
// suites run through the raster adapter in tracerVertical, tracerSeed and tracerGradient; the cases the raster adds
// are in two files, because as one file they outgrew the runner's 60 s per-file limit on a loaded CI runner (see the
// CI-timeout round below). This one holds the constants, `noiseSigma`, the selection and rolling-window primitives,
// the column states, the fails-closed and 6-degree rules, and the extraction interface. horizonTraceRules.test.ts
// holds the placement-sigma rule, the focal term, contrast, the texture channel, azimuth support and the ring sky,
// with the mutants SPEC-v2 7.2 names for the tracer row.
//
// Mutants named by the T10 fix rounds that this file holds, each in the 'Mutation:' line of the case that catches it:
//   - the sky above A counted from the A row (17 rows of height read as 18);
//   - the sigma test back to `sigma > TRACE.measuredMaxSigmaDeg`, which a NaN sigma passes: 'every comparison fails
//     closed: an unknown axis altitude is a NaN sigma, which is Low and never Measured'.
// The CI-timeout round (this file was killed at the runner's 60 s limit with 22 of its cases printed): nothing in the
// tracer loops without a bound, so the cure was cost and not a guard. The sky window of the walk and its MAD were
// rewritten to do less work for the same numbers, and these cases hold the rewrite and the one loop that is not
// counted in rows:
//   - 'kth ends on any contents ...': K1-K5, K7, K8 (the selection loop, with NaN and the infinities);
//   - 'RollingWindow holds the last cap values ...': W1-W7 (the rewritten window, against percentile and robustSpread);
//   - 'extraction ends on painted runs that are not numbers ...': D1.
// Every case below opens with a 'Mutation:' note naming the mutants it was seen to turn red, as the old suites did
// (the ids are those of the T10 report's mutant run, which lists the edit of each).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { noiseSigma } from '../noise';
import { RollingWindow, TRACE, kth, percentile, robustSpread, tracer } from '../horizonTrace';
import { ColState, PANO_W } from '../../types';
import type { StubPanorama } from './rasterAdapter';
import { SKY, WALL, col, extract, gauss, inSector, near, scene, unit, wallScene } from './traceScenes';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

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

// ---- Selection and the rolling window --------------------------------------------------------------

/** The contents of `a[0..n)` as a sorted list of strings, so that two arrays holding the same values (NaN, -0 and the
 *  infinities among them) compare equal whatever order they are in. */
const contents = (a: Float64Array, n: number): string[] =>
  Array.from(a.subarray(0, n), v => (Number.isNaN(v) ? 'NaN' : Object.is(v, -0) ? '-0' : String(v))).sort();

test('kth ends on any contents, NaN and the infinities included, and stays inside the range it is given', () => {
  // Mutation: step a scan over values equal to the pivot (`while (a[i] <= x) i++`, K1, or `while (x <= a[j]) j--`, K4) or
  //   stop shrinking the range (`m = j` or `l = i` dropped, K2, K3): a pass makes no progress and the loop never ends,
  //   so the case never returns and the runner's timeout is what turns it red. Return the wrong slot (`a[l]`, K5),
  //   widen the range by a slot (`m = n`, K7) or let a swap skip one (K8): a wrong order statistic, a slot past the
  //   range written, or a range that is not partitioned about k.
  // The selection loop of the window model is the one place the tracer iterates without a row counter. It was suspected
  // of stalling a CI worker; it cannot (its comment says why), and this holds that to be so for the contents a bad
  // raster could give it.
  const CANARY = 777.5, FINITE = [0, -0, 1, 1, 2, 3, 7, 255, -5.5], WITH_INF = [...FINITE, Infinity, -Infinity], WITH_NAN = [...WITH_INF, NaN, NaN];
  let state = 20260710;
  const next = () => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 4294967296; };
  let selected = 0, withNaN = 0;
  for (let trial = 0; trial < 8000; trial++) {
    const mode = trial % 5;
    const n = trial < 4 ? trial : Math.floor(next() * 41);
    const pool = mode === 0 ? FINITE : mode === 1 ? WITH_INF : mode === 2 ? WITH_NAN : mode === 3 ? [7] : null;
    const a = new Float64Array(n + 3).fill(CANARY);
    for (let i = 0; i < n; i++) a[i] = pool === null ? next() * 1000 - 500 : pool[Math.floor(next() * pool.length)];
    const given = Array.from(a.subarray(0, n));
    const p = [0, 0.3, 0.5, 0.95, 1][Math.floor(next() * 5)];
    const k = Math.floor(p * (n - 1));   // as `rank` reads it: -1 for an empty range
    const before = contents(a, n);
    const got = kth(a, n, k);
    const where = `n ${n} k ${k} mode ${mode} [${given.join(',')}]`;
    assert.deepEqual(contents(a, n), before, `the range is only reordered: ${where}`);
    for (let i = n; i < a.length; i++) assert.equal(a[i], CANARY, `slot ${i} past the range was written: ${where}`);
    if (n === 0 || given.some(Number.isNaN)) { withNaN += n > 0 ? 1 : 0; continue; }
    const sorted = [...given].sort((x, y) => x - y);
    assert.ok(got === sorted[k], `the ${k}-th smallest is ${sorted[k]}, not ${got}: ${where}`);   // === : 0 and -0 are one value here
    for (let i = 0; i < n; i++) assert.ok(i < k ? a[i] <= a[k] : i > k ? a[i] >= a[k] : true, `partitioned about slot ${k}: ${where}`);
    selected++;
  }
  assert.ok(selected > 5000 && withNaN > 1000, `the fuzz must reach both kinds of contents (${selected} ordered, ${withNaN} with NaN)`);
  assert.equal(kth(new Float64Array(0), 0, -1), undefined, 'an empty range has no k-th value and no loop');
});

test('RollingWindow holds the last cap values in order: at() is percentile() and spread() is robustSpread() of them', () => {
  // Mutation: forget to move the ring head when full, so the wrong value leaves (W1); look for the evicted value's place
  //   with `<=` (W2: wrong once values repeat); leave the smallest value out of the insertion walk when not yet full
  //   (W3); stop the walk that follows an eviction one slot short (W7); read the halving of spread one step short (W4)
  //   or the wrong way (W5), or return the nearer deviation and not the farther (W6).
  // The window is pushed once per row of every column in two channels and read for its median and MAD at every one, so
  // it is written to avoid the work (a walk between two slots, halving for the MAD). It must stay what it was: the
  // sorted last `cap` values. Compared with the statistics on a plain copy, over streams that repeat, plateau, run
  // up and down, and with every cap from 1 to one past the tracer's own.
  const streams: Record<string, (i: number) => number> = {
    noisy: i => 130 + 4 * gauss(i, 3, 91),
    plateau: () => 130,
    twoLevels: i => (i % 7 < 3 ? 40 : 200),
    ascending: i => i * 0.25,
    descending: i => 90 - i * 0.5,
    zigzag: i => (i % 2 === 0 ? i % 13 : 100 - (i % 17)),
    ties: i => Math.floor(unit(i, 5, 92) * 4),
    signed: i => -50 + 100 * unit(i, 6, 93),
  };
  let compared = 0;
  for (const cap of [1, 2, 3, 4, 5, 36, 37]) {
    for (const [name, stream] of Object.entries(streams)) {
      const w = new RollingWindow(cap);
      const seen: number[] = [];
      for (let i = 0; i < 160; i++) {
        const v = Math.round(stream(i) * 1024) / 1024 + 0;   // + 0 turns a -0 into 0
        w.push(v); seen.push(v);
        const last = seen.slice(-cap);
        const label = `cap ${cap}, ${name}, after ${i + 1} pushes`;
        assert.equal(w.n, last.length, `${label}: count`);
        for (const p of [0, 0.3, 0.5, 0.95, 1]) assert.equal(w.at(p), percentile(last, p), `${label}: percentile ${p}`);
        assert.equal(w.spread(), robustSpread(last), `${label}: spread`);
        compared++;
      }
      // A cleared window starts over, whatever the ring and the order held.
      w.clear();
      assert.equal(w.n, 0, `${name}: cleared`);
      assert.equal(w.spread(), 0, `${name}: nothing to spread`);
      const again = [stream(7), stream(8), stream(9)].map(x => Math.round(x * 1024) / 1024 + 0);
      for (const x of again) w.push(x);
      const lastAgain = again.slice(-cap);
      assert.equal(w.n, lastAgain.length, `cap ${cap}, ${name}: count after clear`);
      assert.equal(w.at(0.5), percentile(lastAgain, 0.5), `cap ${cap}, ${name}: median after clear`);
      assert.equal(w.spread(), robustSpread(lastAgain), `cap ${cap}, ${name}: spread after clear`);
    }
  }
  assert.equal(compared, 7 * Object.keys(streams).length * 160);
});

// ---- The column states (5.2) --------------------------------------------------------------------------

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

test('extraction ends on painted runs that are not numbers: none is Measured, and the rest of the ring does not notice', () => {
  // Mutation: read a run end that is NaN as the edge of the raster (`altRow(run.bottomAlt) || PANO_H - 1` in `col`, D1):
  //   the column is walked as though it were covered and published as a measurement.
  // A raster that reports a run with NaN, infinite, inverted or absurd ends must still be traced to the end (every
  // loop of the extraction counts rows, so none can wait on a comparison that NaN fails), and no such column may be
  // handed on as a measurement. Every other column reads as it would if they were not there.
  const base = scene((_az, alt, x, y) => (alt > 53.96 || alt < -8 ? null : { lum: SKY + 2 * gauss(x, y, 5) }));
  const shapes: [string, number, number][] = [
    ['NaN ends', NaN, NaN], ['infinite ends', Infinity, -Infinity], ['infinite ends, reversed', -Infinity, Infinity],
    ['inverted', 10, 50], ['absurd', 1e9, -1e9], ['NaN top', NaN, -8], ['NaN bottom', 50, NaN], ['a single row', 0, 0],
  ];
  const odd = new Map<number, [string, number, number]>();
  shapes.forEach(([what, top, bottom], i) => { for (let x = 100 + i * 60; x < 106 + i * 60; x++) odd.set(x, [what, top, bottom]); });
  const pano: StubPanorama = {
    ...base,
    observedRun: x => { const o = odd.get(x); return o === undefined ? base.observedRun(x) : { topAlt: o[1], bottomAlt: o[2] }; },
  };
  const h = extract(pano);
  const states = new Set<number>(Object.values(ColState).filter(v => typeof v === 'number') as number[]);
  for (let x = 0; x < PANO_W; x++) {
    assert.ok(states.has(h.state[x]), `column ${x} has state ${h.state[x]}, which is not a column state`);
    const o = odd.get(x);
    if (o !== undefined) {
      assert.notEqual(h.state[x], ColState.Measured, `column ${x} (${o[0]}) was published as a measurement`);
    } else {
      assert.equal(h.state[x], ColState.Measured, `column ${x} beside the odd runs is state ${h.state[x]}`);
      assert.equal(h.alt[x], 0, `column ${x} beside the odd runs reads ${h.alt[x]}`);
    }
  }
  assert.equal(odd.size, 8 * 6);
});

console.log(`horizonTrace.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
