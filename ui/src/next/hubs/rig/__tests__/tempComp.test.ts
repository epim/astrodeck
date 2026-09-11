// tempComp.test.ts - the sentences and the arithmetic behind TEMPERATURE
// COMPENSATION (D-RIG-2, task T-U7b-5).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/tempComp.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT THIS GUARDS, and why a copy test earns its place here. This block's one
// serious failure mode is not a crash: it is a coefficient entered with the
// wrong sign, which does not fail to correct the focus drift - it DOUBLES it,
// all night, while the log says compensation is running. The only defence is
// that the screen states the convention in words instead of leaving it to be
// inferred from a minus sign, so the words are graded here:
//
//   1. THE RULE SAYS "RISES". The server's formula is per degree of
//      TEMPERATURE, not per degree of cooling. An edit that reworded it to
//      "falls" or "cools" would invert the meaning of every coefficient on
//      every rig without changing a line of arithmetic.
//   2. THE EFFECT LINE AGREES WITH THE RULE. A positive coefficient moves the
//      drawtube IN as the tube cools. Rule and effect are written by two
//      different functions on purpose: if either is inverted alone they
//      contradict each other on screen, and this test fails.
//   3. THE PREDICTION IS THE SERVER'S. `predictedMove` subtracts the live
//      position from `predicted_position` and does not re-derive anything, so a
//      null prediction is "no move" and never a locally computed guess.
//   4. `null` IS NOT 0 for the reference: no reference taken reads as its own
//      sentence, not as 0 C at step 0.
//
// Pure module, printed tally + the counts export (shell-and-tests.md section 4).

import {
  DOUBLES_IT_NOTE, MEASURE_RECIPE, SIGN_RULE, TEMP_COMP_PRECEDENCE,
  coefficientHint, effectSentence, nextMoveTile, predictedMove, referenceLine,
  signSentence, signedSteps,
} from "../lib/tempComp";
import type { TempCompStatus } from "../../../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

function node(over: Partial<TempCompStatus> = {}): TempCompStatus {
  return {
    enabled: true,
    steps_per_c: 14,
    reference_temp_c: 11.2,
    reference_position: 11218,
    predicted_position: null,
    last_move_steps: null,
    last_reason: "temperature compensation is off",
    ...over,
  };
}

// ========================================================= 1. the sign rule
test("the sign sentence says the temperature RISES, not falls", () => {
  const s = signSentence(14);
  assert(/RISES/.test(s),
    `the rule does not say RISES, so the coefficient's sign is left to be inferred: "${s}"`);
  assert(!/\bfalls\b|\bcools by\b/.test(SIGN_RULE),
    "the rule was reworded to talk about cooling - the server's formula is per degree of "
    + "TEMPERATURE, and that rewording inverts every coefficient on every rig");
  assert(s.includes(SIGN_RULE),
    "the sign sentence no longer carries the rule verbatim");
  assert(s.includes(MEASURE_RECIPE),
    "the sign sentence dropped the measurement recipe, so nothing tells the operator how to "
    + "get the number with its sign attached");
});

test("the rule and the recipe are the plan's copy, hyphens and all", () => {
  for (const s of [SIGN_RULE, MEASURE_RECIPE, DOUBLES_IT_NOTE, TEMP_COMP_PRECEDENCE,
    signSentence(14), signSentence(0), effectSentence(-14), effectSentence(0),
    referenceLine(node()), referenceLine(null)]) {
    assert(!/[\u2014\u2013]/.test(s), `an em-dash or en-dash reached a UI string: ${s}`);
  }
  eq(TEMP_COMP_PRECEDENCE,
    "Temperature compensation moves between frames; the refocus trigger stops and "
    + "re-measures. When both are on, compensation runs first and the trigger still fires.",
    "the precedence sentence is not `TEMP_COMP_PRECEDENCE` verbatim - the server keeps it as "
    + "a string so the engine, the docs and this screen cannot drift apart");
});

// ===================================================== 2. the effect, in words
test("a positive coefficient moves the drawtube IN as the tube cools", () => {
  const s = effectSentence(14);
  assert(/\+14/.test(s), `the effect line does not name the coefficient: "${s}"`);
  assert(/14 steps in\b/.test(s),
    `a positive coefficient must move the drawtube IN on a 1 C fall: "${s}"`);
  assert(!/steps out/.test(s), `the effect line points the wrong way: "${s}"`);
});

test("a negative coefficient moves it OUT, and says so", () => {
  const s = effectSentence(-14);
  assert(/-14/.test(s), `the effect line does not name the coefficient: "${s}"`);
  assert(/14 steps out\b/.test(s),
    `a negative coefficient must move the drawtube OUT on a 1 C fall: "${s}"`);
});

test("a zero coefficient says nothing moves, switch or no switch", () => {
  // `focus/tempcomp.py:145-146`: 0 returns "no coefficient set" and never moves,
  // even with `enabled` true. A screen that showed an armed switch and said
  // nothing else would be claiming a loop that cannot run.
  assert(/nothing moves/.test(effectSentence(0)),
    `a zero coefficient must say the loop cannot move: "${effectSentence(0)}"`);
  assert(/nothing moves/.test(coefficientHint(0)),
    "the hint at 0 does not say the loop is off");
  eq(coefficientHint(14), MEASURE_RECIPE,
    "a set coefficient's hint is no longer just the recipe");
});

test("the sign is printed on a positive number, never left to be assumed", () => {
  eq(signedSteps(14), "+14", "a positive coefficient printed without its sign");
  eq(signedSteps(-14), "-14", "a negative coefficient lost its sign");
  eq(signedSteps(0), "0", "zero must not carry a sign");
  eq(signedSteps(13.5), "+13.5", "a fractional coefficient was rounded away");
});

// ======================================= 3. the prediction is the server's own
test("a falling tube with a positive coefficient predicts a move IN", () => {
  // The server has already decided: reference 11218 at 11.2 C, now 9.2 C, so
  // 11218 + 14 * (-2) = 11190. This module subtracts, and nothing else.
  const m = predictedMove(node({ predicted_position: 11190 }), 11218, 200);
  assert(m != null, "no move was read from a prediction the server published");
  eq(m!.steps, -28, "the step count is not `predicted_position` minus the live position");
  eq(m!.direction, "in", "a lower target must read as IN - that is how the jogs are named");
  eq(m!.clamped, false, "a 28-step move under a 200-step limit was called clamped");
});

test("a move at the per-move backstop says it did not cover the drift", () => {
  const m = predictedMove(node({ predicted_position: 11418 }), 11218, 200);
  eq(m!.steps, 200, "the step count is wrong");
  eq(m!.direction, "out", "a higher target must read as OUT");
  eq(m!.clamped, true,
    "a move exactly at the per-move limit did not say so - the drift it left behind is what "
    + "the refocus trigger is there to catch");
});

test("no prediction is no move, and never a locally derived one", () => {
  eq(predictedMove(node({ predicted_position: null }), 11218, 200), null,
    "a null prediction produced a move: `predicted_position` is the SERVER running its own "
    + "rule table, and a second copy of the deadband and the clamps here would describe a "
    + "move the engine is not going to make");
  eq(predictedMove(node({ predicted_position: 11190 }), null, 200), null,
    "a move was drawn with no live position to measure it from");
  eq(predictedMove(null, 11218, 200), null, "a move was drawn from an absent status node");
  eq(predictedMove(node({ predicted_position: 11218 }), 11218, 200), null,
    "a prediction that lands where the focuser already is is not a move");
});

test("the NEXT MOVE tile prints the signed count, the word and the target", () => {
  const t = nextMoveTile(node({ predicted_position: 11190 }), 11218, 200);
  eq(t.value, "-28 in", "the tile does not carry the signed step count and the direction word");
  assert(/11190/.test(t.sub), `the tile does not name the target position: "${t.sub}"`);
  const none = nextMoveTile(node({ predicted_position: null }), 11218, 200);
  eq(none.value, "no move", "a refusal did not read as one");
  eq(none.sub, "",
    "the no-move tile invented a second line: the server's own sentence for why is printed "
    + "under the grid, and saying it twice is saying it once badly");
});

// ============================================== 4. the reference, and its null
test("no reference reads as its own sentence, not as 0 C at step 0", () => {
  eq(referenceLine(node({ reference_temp_c: null, reference_position: null })),
    "no reference yet - it is set by the next autofocus",
    "a missing reference did not say so");
  eq(referenceLine(node({ reference_temp_c: null })),
    "no reference yet - it is set by the next autofocus",
    "half a reference is not a reference");
  eq(referenceLine(node()), "reference 11.2 C at 11218 steps",
    "the reference line is not the pair the arithmetic uses");
});

const total = passed + failed;
console.log(`tempComp.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
