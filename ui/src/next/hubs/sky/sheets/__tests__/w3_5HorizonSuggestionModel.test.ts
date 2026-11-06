// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_5HorizonSuggestionModel.test.ts - WP 3.5 (#599, #1093): the words of the
// learned-horizon offer carry information the page does not already show.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx <this file>
//
// Named mutant (horizonSuggestionModel.ts): the region line drops the height
// ("up to 40"), so the operator is told where but not how high.

import assert from 'node:assert/strict';
import {
  ACCEPT_NOTE, NO_HORIZON_GUIDE, acceptFailure, offerSummary, regionLine,
} from '../horizonSuggestionModel';

let passed = 0;
function test(name: string, fn: () => void): void { fn(); passed++; console.log(`PASS ${name}`); }
const D = String.fromCharCode(176);

const OFFER = {
  id: 'x', frames: 6, bins: 3, nights: 1, points: [] as [number, number][],
  regions: [{ az_from: 120, az_to: 150, alt_min: 40, alt_max: 40, frames: 6 }],
};

test('a region line names the direction, the span, the height and the frames', () => {
  assert.equal(regionLine(OFFER.regions[0]), `SE, 120${D} to 150${D}, up to 40${D} (6 frames)`);
});

test('a region of two heights names both', () => {
  const line = regionLine({ az_from: 120, az_to: 150, alt_min: 35, alt_max: 45, frames: 1 });
  assert.ok(line.includes(`35${D} to 45${D} high`) && line.includes('(1 frame)'), line);
});

test('a region across north is named for the middle of its span, not the average of its two edges', () => {
  const line = regionLine({ az_from: 350, az_to: 20, alt_min: 30, alt_max: 30, frames: 5 });
  assert.ok(line.startsWith('N,'), line);
});

test('the summary counts frames, nights and directions', () => {
  assert.equal(offerSummary(OFFER), '6 frames over 1 night showed the same obstruction in 1 direction.');
  assert.equal(offerSummary({ ...OFFER, nights: 3, regions: [...OFFER.regions, ...OFFER.regions] }),
    '6 frames over 3 nights showed the same obstruction in 2 directions.');
});

test('the no-horizon guide says what to do, and the accept note says nothing changes first', () => {
  assert.ok(/Draw the line above/.test(NO_HORIZON_GUIDE) && /scan your surroundings/.test(NO_HORIZON_GUIDE));
  assert.ok(/Nothing changes until you accept/.test(ACCEPT_NOTE) && /never lowers/.test(ACCEPT_NOTE));
});

test('a moved offer is told as such; any other failure names its reason', () => {
  assert.ok(/changed while you were looking/.test(acceptFailure({ code: 'suggestion_changed' })));
  assert.ok(/changed while you were looking/.test(acceptFailure({ code: 'no_suggestion' })));
  assert.equal(acceptFailure({ message: 'forbidden' }), 'Could not store the horizon: forbidden');
  assert.equal(acceptFailure({}), 'Could not store the horizon.');
});

console.log(`w3_5HorizonSuggestionModel: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
