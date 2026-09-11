// tempComp.ts - the words and the arithmetic behind TEMPERATURE COMPENSATION
// (D-RIG-2, task T-U7b-5). Pure: a status node and two numbers in, sentences
// out, so the copy the operator reads is graded by a test rather than by
// somebody re-reading the screen.
//
// THE SIGN IS THE WHOLE REASON THIS MODULE EXISTS. The server's contract
// (`server/astrodeck/focus/tempcomp.py:50-61`) is
//
//     position = reference_position + steps_per_c * (temperature - reference_temp)
//
// so the coefficient is steps per degree of TEMPERATURE, not per degree of
// cooling, and a POSITIVE coefficient moves the drawtube IN as the night cools.
// Nothing on screen may leave that to be inferred from a minus sign, because
// getting it backwards does not fail to correct the drift - it DOUBLES it, all
// night, while the log says compensation is running. So the rule is spelled out
// beside the number (`signSentence`) and what THIS rig's coefficient does is
// spelled out too (`effectSentence`): a reader who never works out which way
// "positive" points still cannot set it backwards by accident.
//
// (`TempCompConfig`'s class docstring used to say the opposite - "a tube that
// shrinks as it cools needs the drawtube to come IN, which on this rig is
// negative" - contradicting the formula three paragraphs above it. S7L
// corrected that paragraph; `focus/tempcomp.py:87-92` now states the same rule
// this file does. That contradiction is why the ruling exists.)
//
// IN AND OUT ARE STEP COUNTS, not opinions: IN is a LOWER position, which is
// how this sheet's own jog buttons are named (`sheets/focuser.tsx` "IN 10"
// calls `moveTo(pos - 10)`). Every direction word below is derived that way.

import type { TempCompStatus } from "../../../../types";

/** The sign rule, verbatim. The one sentence that must never be paraphrased,
 *  because a paraphrase is where the direction gets inverted. */
export const SIGN_RULE =
  "The focuser moves by this many steps for each degree the temperature RISES. "
  + "Positive moves the drawtube in as the night cools.";

/** How to measure the coefficient, in one line. The sign clause is part of the
 *  recipe: a magnitude measured without it is a number that will be entered
 *  with whichever sign looks right, which is the failure above. */
export const MEASURE_RECIPE =
  "Measure it: focus at 12 C, focus again at 4 C, and divide the change in "
  + "position by the change in temperature, sign included.";

/** Why a wrong sign is worse than no compensation at all, and what the two
 *  backstops below are for (`focus/tempcomp.py:60-61`). */
export const DOUBLES_IT_NOTE =
  "Getting the sign backwards does not fail to correct the drift - it doubles "
  + "it. That is why the per-move limit below exists and why the refocus "
  + "trigger is never turned off.";

/** `TEMP_COMP_PRECEDENCE` (`focus/tempcomp.py:75-78`), quoted rather than
 *  paraphrased: the server keeps the rule as a string precisely so the docs and
 *  this screen say what the engine does, word for word. */
export const TEMP_COMP_PRECEDENCE =
  "Temperature compensation moves between frames; the refocus trigger stops "
  + "and re-measures. When both are on, compensation runs first and the trigger "
  + "still fires.";

/** The refusal when `focuser.temperature` is null. The engine's own decision in
 *  that state is "this focuser has no thermometer" and it NEVER GUESSES an
 *  ambient (`focus/tempcomp.py:147-152`), so a switch that could be turned on
 *  here would arm a loop that can only ever decline to move. */
export const NO_THERMOMETER_REASON =
  "This focuser reports no temperature, so compensation has nothing to follow";

/** The refusal while `GET /api/config` has not answered. The whole `focus`
 *  block is replaced by a write, so writing one built from a block we have not
 *  read would blank `approach_overshoot_steps` - the EAF backlash correction -
 *  on a rig that had it set. */
export const NO_FOCUS_BLOCK_REASON =
  "The rig has not sent its focus settings yet, and this write replaces the "
  + "whole block";

/** `-13.5` -> "-13.5", `14` -> "14". A coefficient is a measurement, so a
 *  trailing `.0` on a whole number is noise and 13.500000000000002 is a lie
 *  about the precision. */
function num(n: number): string {
  return String(Math.round(n * 100) / 100);
}

/** `14` -> "+14". The sign is the load-bearing half of this number, so it is
 *  always printed, never left to a minus sign's absence. */
export function signedSteps(n: number): string {
  return n > 0 ? `+${num(n)}` : num(n);
}

/** The sign rule and how to measure it: the sentence pair that sits beside the
 *  coefficient field. Takes the coefficient because at 0 the recipe is the
 *  thing to do next rather than a footnote. */
export function signSentence(stepsPerC: number): string {
  return `${SIGN_RULE} ${coefficientHint(stepsPerC)}`;
}

/** The measurement recipe in one line. At 0 it carries the consequence too:
 *  the server reads a zero coefficient as OFF even with the switch on
 *  (`focus/tempcomp.py:101-102,145-146`), so a rig sitting at 0 is not compensating
 *  however confident the toggle looks. */
export function coefficientHint(stepsPerC: number): string {
  if (!Number.isFinite(stepsPerC) || stepsPerC === 0) {
    return `${MEASURE_RECIPE} Until it is measured nothing moves, switch or no switch.`;
  }
  return MEASURE_RECIPE;
}

/** What THIS coefficient does to THIS drawtube, in the direction words the jog
 *  buttons use. Stated for a FALL in temperature because that is the night the
 *  operator is planning for; the rule sentence covers the rise. */
export function effectSentence(stepsPerC: number): string {
  if (!Number.isFinite(stepsPerC) || stepsPerC === 0) {
    return "At 0 steps per degree nothing moves, whatever the switch says.";
  }
  const word = stepsPerC > 0 ? "in" : "out";
  return `At ${signedSteps(stepsPerC)} steps per degree: as the tube cools by `
    + `1 C the drawtube moves ${num(Math.abs(stepsPerC))} steps ${word}.`;
}

/** Where compensation is measuring from, in one line.
 *
 *  `null` is NOT 0 here (`focus/tempcomp.py:103-105`): no reference has been
 *  taken, and the engine seeds one from the next reading it gets - which is the
 *  same call that re-anchors the refocus trigger's baseline, so the two clocks
 *  cannot drift apart (`focus/tempcomp.py`, "ONE BASELINE FOR BOTH"). */
export function referenceLine(node: TempCompStatus | null | undefined): string {
  if (!node || node.reference_temp_c == null || node.reference_position == null) {
    return "no reference yet - it is set by the next autofocus";
  }
  return `reference ${node.reference_temp_c.toFixed(1)} C at `
    + `${Math.round(node.reference_position)} steps`;
}

export interface PredictedMove {
  /** Signed steps from where the drawtube is NOW to where compensation would
   *  put it. Negative is IN, the way the jogs are named. */
  steps: number;
  direction: "in" | "out";
  /** The move is exactly the per-move backstop, so it does NOT fully correct
   *  the drift. Derived from the limit rather than read: `status_node`
   *  (`focus/tempcomp.py:216-238`) publishes the target and the reason but not
   *  the decision's own `clamped` flag, and the server's sentence in
   *  `last_reason` - which this sheet prints verbatim - is the authority. */
  clamped: boolean;
}

/** What the next frame boundary would do, from the server's own prediction.
 *
 *  `predicted_position` is `predict()` run against the LIVE reading server-side;
 *  it is NEVER re-derived here. Re-deriving it would mean a second copy of the
 *  deadband, the per-move clamp and the travel limits, and the moment the two
 *  disagreed the screen would be describing a move the engine is not going to
 *  make - which is the exact defect the status node exists to prevent. */
export function predictedMove(
  node: TempCompStatus | null | undefined,
  position: number | null | undefined,
  maxStepPerMove?: number | null,
): PredictedMove | null {
  if (!node || node.predicted_position == null || position == null) return null;
  const steps = Math.round(node.predicted_position - position);
  // The tick's position has already caught up with the prediction: there is
  // nothing to move, and "0 steps out" would be a direction nobody is going.
  if (steps === 0) return null;
  return {
    steps,
    direction: steps < 0 ? "in" : "out",
    clamped: maxStepPerMove != null && maxStepPerMove > 0
      && Math.abs(steps) >= maxStepPerMove,
  };
}

/** The NEXT MOVE tile: a signed step count with the direction word, or the
 *  refusal. `sub` names the target position, because "-14 in" is only checkable
 *  against the number the drawtube would land on; it is EMPTY when there is no
 *  move, since the server's own sentence for why is printed under the grid and
 *  a tile that re-announced "nothing" would be saying it twice. */
export function nextMoveTile(
  node: TempCompStatus | null | undefined,
  position: number | null | undefined,
  maxStepPerMove?: number | null,
): { value: string; sub: string } {
  const move = predictedMove(node, position, maxStepPerMove);
  if (!move) return { value: "no move", sub: "" };
  return {
    value: `${signedSteps(move.steps)} ${move.direction}`,
    sub: move.clamped
      ? `to ${node!.predicted_position} steps, at the per-move limit`
      : `to ${node!.predicted_position} steps`,
  };
}
