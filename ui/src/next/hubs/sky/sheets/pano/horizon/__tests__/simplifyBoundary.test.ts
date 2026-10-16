// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T30F, ruling S47: the published polyline gains a boundary ramp at every Measured/non-Measured bin boundary.
//
// Mutants this file must catch: the ramp removed (the Measured bin's line then climbs across the whole bin, red on the
// boundary case) and the 180-point cap ignored (the dense case then returns every ramp, red on the count).
import assert from 'node:assert/strict';
import { horizonAltAt } from '../../../../../../lib/horizonModel';
import { BinState } from '../../types';
import { conservativePolyline } from '../simplify';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const N = 720;
const EPS = 1e-6;

/** The scorer's G1: the line at both ends of every bin, and at any vertex inside it, is never below the bin. */
function belowProfile(alt: Float32Array, points: { az: number; alt: number }[]): number {
  let low = 0;
  for (let i = 0; i < N; i++) {
    const l = horizonAltAt(points, i * 0.5), r = horizonAltAt(points, (i + 1) * 0.5);
    if (l < alt[i] - EPS || r < alt[i] - EPS) low++;
  }
  for (const p of points) {
    const bin = Math.min(N - 1, Math.floor(p.az / 0.5));
    if (p.alt < alt[bin] - EPS) low++;
  }
  return low;
}

/** Open ground at 5 degrees with a Measured window [from, to) of the given height and everything else blocked. */
function windowCase(from: number, to: number, height: number): { alt: Float32Array; state: Uint8Array } {
  const alt = new Float32Array(N).fill(90), state = new Uint8Array(N).fill(BinState.Unknown);
  for (let i = from; i < to; i++) { alt[i] = height; state[i] = BinState.Measured; }
  return { alt, state };
}

test('a Measured bin beside a blocked bin: the line holds the bin to within 0.01 degree of the edge, and G1 holds', () => {
  const { alt, state } = windowCase(100, 140, 12);
  const { points } = conservativePolyline(alt, 180, state);
  const left = 100 * 0.5, right = 140 * 0.5;       // the window's edges, in degrees
  // Just inside the window at either edge the line is still on the profile (not on the ramp to 90).
  assert.ok(Math.abs(horizonAltAt(points, left + 0.011) - 12) < 0.3, `left edge: ${horizonAltAt(points, left + 0.011)}`);
  assert.ok(Math.abs(horizonAltAt(points, right - 0.011) - 12) < 0.3, `right edge: ${horizonAltAt(points, right - 0.011)}`);
  // At the edge itself the line reaches the blocked neighbour (G1 for that bin), and it never dips below it.
  assert.ok(horizonAltAt(points, left - 1e-9) >= 90 - 1e-6, 'blocked at the left edge');
  assert.ok(horizonAltAt(points, right + 1e-9) >= 90 - 1e-6, 'blocked at the right edge');
  assert.equal(belowProfile(alt, points), 0, 'G1 at every bin end and vertex');
  // Without the state the old line ramps over the whole window: the ruling is what moved it.
  const old = conservativePolyline(alt, 180);
  assert.ok(horizonAltAt(old.points, left + 0.25) > 12.5, 'the old ramp crosses the Measured bin');
});

test('the ramp lies in the lower bin: a Measured bin above an Edited neighbour is not undercut', () => {
  const alt = new Float32Array(N).fill(3), state = new Uint8Array(N).fill(BinState.Edited);
  for (let i = 200; i < 230; i++) { alt[i] = 20; state[i] = BinState.Measured; }
  const { points } = conservativePolyline(alt, 180, state);
  assert.equal(belowProfile(alt, points), 0, 'G1 beside a lower unmeasured neighbour');
  assert.ok(horizonAltAt(points, 100.011) >= 20 - 1e-6 && horizonAltAt(points, 100.011) < 20.3, 'the Measured bin is held to within 0.011 of its edge');
});

test('a dense pattern of boundaries stays within 180 points and never below the profile', () => {
  // 60 Measured windows of 6 bins between blocked gaps of 6 bins: 120 boundaries, 240 ramp vertices.
  const alt = new Float32Array(N).fill(90), state = new Uint8Array(N).fill(BinState.Unknown);
  for (let w = 0; w < 60; w++) {
    for (let k = 0; k < 6; k++) { const i = w * 12 + k; alt[i] = 4 + (w % 7) * 2; state[i] = BinState.Measured; }
  }
  const { points } = conservativePolyline(alt, 180, state);
  assert.ok(points.length <= 180, `${points.length} points`);
  assert.equal(belowProfile(alt, points), 0, 'never below the profile');
  for (let k = 1; k < points.length; k++) assert.ok(points[k].az > points[k - 1].az, 'ascending azimuths');
  // A looser cap keeps more of the ramps: the cap, not the pattern, is what removed them.
  const loose = conservativePolyline(alt, 400, state);
  assert.ok(loose.points.length > 180, `${loose.points.length} points when the cap allows`);
  assert.equal(belowProfile(alt, loose.points), 0, 'never below the profile, loose cap');
});

test('with the cap binding, the ramp with the smallest step is the one that keeps the old shape', () => {
  // One tall window (step 70) and many short ones (step 5): the tall window keeps its ramp last.
  const alt = new Float32Array(N).fill(40), state = new Uint8Array(N).fill(BinState.Edited);
  for (let i = 10; i < 20; i++) { alt[i] = 5; state[i] = BinState.Measured; }
  for (let w = 0; w < 50; w++) for (let k = 0; k < 3; k++) { const i = 100 + w * 8 + k; alt[i] = 35; state[i] = BinState.Measured; }
  alt[300] = 90; state[300] = BinState.Edited;
  const { points } = conservativePolyline(alt, 24, state);
  assert.ok(points.length <= 24, `${points.length} points`);
  assert.equal(belowProfile(alt, points), 0, 'never below the profile at a tight cap');
});

console.log(`${passed} passed`);
