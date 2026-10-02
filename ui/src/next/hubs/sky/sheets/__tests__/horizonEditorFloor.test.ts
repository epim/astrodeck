// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Issue #53: the narrowest obstruction the planner honours is, by the owner's
// ruling, "the distance one could reasonably put two of the dots on the horizon
// editor". The simulator's scorer carries that as EDITOR_MIN_WIDTH_DEG, and may
// not read production code; so the cross-check lives here, reading the scorer's
// formula and the editor's own constants, and fails when either moves alone.
// Mutation: REVIEW_HIT_PX = 12 in horizonStrip.ts. Observed red: 'the editor's
// floor is 1.0385 degrees and the scorer's is 1.5577'.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { REVIEW_HIT_PX, REVIEW_STRIP_PX, REVIEW_ZOOM_MAX } from "../horizonStrip";

let passed = 0, failed = 0;
function test(name: string, fn: () => void) {
  try { fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; console.log(`FAIL ${name}\n     ${(e as Error).message}`); }
}

const SCORE = fileURLToPath(new URL("../../../../../../../tools/photosphere_sim/sim/score.py", import.meta.url));

test("the scorer's narrowest obstruction is the editor's finest dot spacing", () => {
  const src = readFileSync(SCORE, "utf8");
  const m = /^EDITOR_MIN_WIDTH_DEG = ([\d.]+) \/ \(([\d.]+) \* ([\d.]+) \/ 360\.0\)$/m.exec(src);
  assert.ok(m, "the scorer's EDITOR_MIN_WIDTH_DEG is no longer written as hit / (strip * zoom / 360)");
  const scorer = Number(m![1]) / (Number(m![2]) * Number(m![3]) / 360);
  const editor = REVIEW_HIT_PX / (REVIEW_STRIP_PX * REVIEW_ZOOM_MAX / 360);
  assert.ok(Math.abs(scorer - editor) < 1e-9,
    `the editor's floor is ${editor.toFixed(4)} degrees and the scorer's is ${scorer.toFixed(4)}`);
});

console.log(`horizonEditorFloor.test: ${passed}/${passed + failed} passed`);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
