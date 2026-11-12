// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// WP 11.3 (#1058, #1117): a column is Measured only where the tracer observed the sky end. The two ways the tracer
// published a false-open Measured column on the chart yard and the treeline case, as scenes that need no recording:
//   - a dark floating structure at least 6 degrees tall stands above the boundary the walk chose (the chart yard's roof,
//     the treeline's crowns) and neither the column nor its neighbours vouched for it: the column answers for the lower
//     boundary and says nothing about the structure above it;
//   - the walk noticed a departure at the top of a low wall and then placed the boundary on the ground under it, more than
//     4 degrees below the row it noticed (the chart yard's wall at azimuth 293 to 302 read 0 where the truth is 4.5 to 6).
//
// Mutants this file must catch:
//   - every column published as Measured (the new branch of `extract` removed): the crown cases;
//   - the boundary allowed to sit any distance below the row the departure was noticed at (the first test of
//     `stepObserved` removed): 'rows that departed from the sky more than 4 degrees above the boundary ...';
//   - the floating structure ignored (the second test of `stepObserved` removed): the two crown cases.
import assert from 'node:assert/strict';
import { ColState, BinState, PROFILE_BINS } from '../../types';
import { acceptSuggested, draftSummary } from '../../../horizonDraft';
import { binProfile } from '../profile';
import { GROUND, SKY, col, extract, inSector, near, scene } from './traceScenes';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const HILL = 45, CROWN = 50;

/** A sector of azimuth 100 to 120 with a dark hill standing on the ground up to 12 degrees, and above it a dark
 *  structure floating over open sky from 22 degrees up: `tall` degrees of it, `lum` bright or dark. */
const hillUnderStructure = (tall: number, lum = CROWN) => scene((az, alt) => {
  if (alt > 60) return null;
  const here = inSector(az, 100, 120);
  if (alt < 0) return { lum: GROUND };
  if (here && alt < 12) return { lum: HILL };
  if (here && alt >= 22 && alt < 22 + tall) return { lum };
  return { lum: SKY };
});

/** The columns of azimuth 104 to 116, clear of the sector's own edges. */
const interior = (): number[] => {
  const xs: number[] = [];
  for (let x = col(104); x < col(116); x++) xs.push(x);
  return xs;
};

test('a dark structure 10 degrees tall floating above the boundary is not a step observed: UnknownContrast, boundary still traced', () => {
  // Mutation: every column published as Measured; the 6 degree floor read as 12 (the structure then needs a neighbour
  //   that cannot exist here).
  const c = extract(hillUnderStructure(10));
  for (const x of interior()) {
    assert.equal(c.state[x], ColState.UnknownContrast, `column ${x}`);
    near(c.alt[x], 12, 0.5, `column ${x} keeps its traced boundary, the hill, which the planner is not told:`);
  }
  // The rest of the ring keeps what it had: the open columns still read the horizon.
  assert.equal(c.state[col(250)], ColState.Measured);
});

test('the profile publishes those bins closed at 90 as Unknown, no reader offers the trace, and the review lists them', () => {
  // Mutation: every column published as Measured.
  const draft = binProfile(extract(hillUnderStructure(10)));
  const first = Math.ceil(104 * 2), last = Math.floor(116 * 2);
  for (let i = first; i < last; i++) {
    assert.equal(draft.state[i], BinState.Unknown, `bin ${i}`);
    assert.equal(draft.reason[i], ColState.UnknownContrast, `bin ${i}`);
    assert.equal(draft.alt[i], 90, `bin ${i} publishes closed`);
    near(draft.suggested[i], 12, 0.5, `bin ${i} keeps the traced altitude:`);
  }
  assert.strictEqual(acceptSuggested(draft), draft, 'Use the traced line takes Low bins only: it cannot reach these');
  assert.equal(draft.state[Math.floor(250 * 2)], BinState.Measured);
  const covered = draftSummary(draft).dark.some(s => s.fromDeg <= 104 && s.toDeg >= 116);
  assert.ok(covered, 'the review notes list the bins as photographed but unreadable');
  assert.equal(draft.state.length, PROFILE_BINS);
});

test('what the tracer already dismissed stays dismissed: a 3 degree marking, a bright band, a structure that stands on its own', () => {
  // Mutation: the floor dropped to one row (the marking demotes the column); the bright test removed (the band does);
  //   a qualifying run counted as unexplained (the 14 degree crown, which is the boundary itself, demotes).
  const x = col(110);
  const marking = extract(hillUnderStructure(3));
  assert.equal(marking.state[x], ColState.Measured, 'a 3 degree disc is a marking');
  near(marking.alt[x], 12, 0.5, 'a marking:');
  const bright = extract(hillUnderStructure(10, 200));
  assert.equal(bright.state[x], ColState.Measured, 'a bright floating band is a stripe or a cloud edge');
  near(bright.alt[x], 12, 0.5, 'a bright band:');
  const standing = extract(hillUnderStructure(14));
  assert.equal(standing.state[x], ColState.Measured, 'a 14 degree crown vouches for itself and is the boundary');
  near(standing.alt[x], 36, 0.5, 'the crown top:');
});

/** A wall of luma 100 and blueness 15 (within the sky's 32 per cent luma allowance, so only its colour sets it apart)
 *  standing `top` degrees over a ground of luma 66 and blueness -13, in azimuth 100 to 120. The photo ends `bottom` degrees
 *  under the horizon: the chart yard's rasters end at -2. */
const lowWall = (top: number, bottom: number) => scene((az, alt) => {
  if (alt > 60 || alt < bottom) return null;
  const here = inSector(az, 100, 120);
  if (here && alt < 0) return { lum: 66, blue: -13 };
  if (here && alt < top) return { lum: 100, blue: 15 };
  if (alt < 0) return { lum: GROUND };
  return { lum: SKY };
});

test('rows that departed from the sky more than 4 degrees above the boundary are not a step: UnknownContrast, not the ground at 0', () => {
  // Mutation: the `noticed` test of stepObserved removed. A 5 degree wall over its ground (the chart yard's wall at azimuth
  //   293 to 302 read 0 where the truth is 4.5 to 6): with the ground 10 degrees deep in the photo the ground is the surface
  //   the walk measures against, the wall is skipped as rows that match neither the sky nor the surface, and the boundary
  //   lands 5 degrees under the row the departure was noticed at.
  const x = col(110);
  const low = extract(lowWall(5, -10));
  assert.equal(low.state[x], ColState.UnknownContrast, 'a 5 degree wall over a deep ground');
  near(low.alt[x], 0, 1.1, 'the ground it was traced to:');
  // A wall taller than the skip's reach has no ground step to be taken for its top: it stays a measurement.
  const higher = extract(lowWall(8, -2));
  assert.equal(higher.state[x], ColState.Measured);
  near(higher.alt[x], 8, 0.7, 'an 8 degree wall:');
  // A 3 degree stripe lying on a wall is the skip the rule is written around: it stays a measurement.
  const stripe = extract(scene((az, alt) => {
    if (alt > 60 || alt < -10) return null;
    const here = inSector(az, 100, 120);
    if (here && alt < 25) return { lum: 70 };
    if (here && alt < 28) return { lum: 190 };
    return { lum: alt < 0 ? GROUND : SKY };
  }));
  assert.equal(stripe.state[x], ColState.Measured, 'a 3 degree stripe on the top of a wall');
  near(stripe.alt[x], 25, 0.7, 'the wall under the stripe:');
});

console.log(`w11_3MeasuredNeedsAStep.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
