// instructionsModel.ts - the pure half of the when/then rule editor.
//
// The rule GRAMMAR is not re-derived here. `lib/instructions.ts` owns the
// labels, the seeds, the change patches, the validator and `describeInstruction`,
// and `lib/instructionSim.ts` owns the dry run; both are already tested against
// the server's own evaluator. This module holds only the two things that lived
// INSIDE the legacy presentation file and would otherwise have to be imported
// from it - which would drag `components/ui.tsx`'s `Panel`, `Toggle` and the
// whole Tailwind tree into the lazily-split next bundle for the sake of two
// pure functions (wave R7 section 2.1's finding).
//
// Re-exported, not moved: `components/sequence/InstructionsPanel.tsx` keeps its
// own copies for `#/classic`.

import type { TriggerKind } from "../../../../../types";
import {
  PREDICATE_OF, RELATIVE_HFR_HINT, THRESHOLD_HINT,
} from "../../../../../lib/instructions";

/** Copy for the collapsed row when the plan has no rules at all. Verbatim from
 *  `InstructionsPanel.tsx:47`. */
export const NO_INSTRUCTIONS_SUMMARY = "Add conditional rules (optional)";

/** Collapsed summary of the rule list: the FIRST rule in full plus a "+N more"
 *  count.
 *
 *  Not a join of every rule. The earlier summary joined them all with " · " and
 *  let CSS truncate, so a four-rule plan rendered as half of rule 1 and no hint
 *  that three others existed - the count was invisible and the visible text was
 *  a sentence fragment. First-plus-count keeps one complete, readable rule and
 *  states the remainder honestly.
 *
 *  Pure: takes the already-rendered descriptions, so it is testable without
 *  React, a store or a plan fixture. */
export function instructionsSummary(descriptions: readonly string[]): string {
  if (descriptions.length === 0) return NO_INSTRUCTIONS_SUMMARY;
  const [first, ...rest] = descriptions;
  return rest.length === 0 ? first : `${first}  ·  +${rest.length} more`;
}

/** Threshold hint for whichever metric a trigger reads.
 *
 *  `hfr_above`'s RELATIVE form needs the opposite advice from its absolute one
 *  (a multiple of the post-focus baseline, not a pixel value), so a relative
 *  rule gets its own sentence rather than the trigger's usual one. */
export function thresholdHint(t: TriggerKind, relative?: boolean): string | null {
  const k = PREDICATE_OF[t];
  if (k === "hfr_above" && relative) return hyphenate(RELATIVE_HFR_HINT);
  return k === "hfr_above" || k === "guide_rms_above"
    ? hyphenate(THRESHOLD_HINT[k]) : null;
}

/** Em-dashes out of copy that reaches the screen.
 *
 *  Several of the shared strings this editor renders (`SIM_CAVEAT`,
 *  `THRESHOLD_HINT`, `ACTION_CONSEQUENCE`, the validator's messages) were
 *  written with em-dashes and typographic quotes, and they are shared with the
 *  legacy UI, so they cannot simply be rewritten at the source without editing a
 *  file this wave must not touch. Normalising at the render boundary is the same
 *  fix wave R7 applies to `FlowLogStrip.IDLE_LOG_TEXT`: the rebuild says it with
 *  a hyphen, `#/classic` keeps its own copy.
 *
 *  Substitutions only, never a reflow: the information is the sentence, and this
 *  changes punctuation. */
export function hyphenate(s: string): string {
  return s
    .replace(/—/g, "-")     // em dash
    .replace(/–/g, "-")     // en dash
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"');
}

/** The one sentence a target jump cannot do without. Verbatim from
 *  `InstructionsPanel.tsx:38`, plus the legacy row's own follow-on clause. */
export const JUMP_HINT =
  "Target jumps need 2 or more targets. Add another target to this plan to jump between them.";

/** Notify severities, in the order the legacy picker offered them. */
export const LEVELS: readonly ("info" | "warning" | "error")[] = ["info", "warning", "error"];
