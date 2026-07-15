// planGroups.ts — pure helpers for mosaic-group plan editing (Plan panel
// "apply to all panels" — see docs/superpowers/specs/2026-07-14-mosaic-apply-
// steps-design.md). No server calls, no store access: takes/returns plain
// Target arrays so SequenceView can wrap the result in a single setPlan.
import type { ExposureStep, Target } from "../types";
import { uid } from "./ids";

/** Copy sourceSteps into every target of `group` (deep-cloned per member).
 *  Clones get FRESH step ids (sessions spec §1 — a copied id would alias two
 *  steps in a session ledger). Targets outside the group are returned
 *  untouched (same references). */
export function applyStepsToGroup(
  targets: Target[],
  group: string,
  sourceSteps: ExposureStep[],
): Target[] {
  return targets.map((t) =>
    t.mosaic_group === group
      ? { ...t, steps: sourceSteps.map((s) => ({ ...s, id: uid() })) }
      : t,
  );
}
