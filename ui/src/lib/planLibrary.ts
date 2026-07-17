// planLibrary.ts — pure presentation helpers for the unified Plan panel (G2).
// The panel merges the plan IDENTITY (name + saved/unsaved cue) with the server
// plan LIBRARY (saved rows); these two pure functions carry the only branching
// logic worth a test, keeping the component a thin render.
import type { PlanRow } from "../types";

/** The compact metadata chip for a saved-plan row: "6t · 300f · 480m".
 *  Integration minutes are rounded to whole minutes to match the server's list
 *  summary intent (plans.py::_summarize rounds to 0.1; the row shows whole). */
export function planRowSummary(
  row: Pick<PlanRow, "targets" | "frames" | "integration_min">,
): string {
  return `${row.targets}t · ${row.frames}f · ${Math.round(row.integration_min)}m`;
}

export type PlanCueTone = "warn" | "dim";
export interface PlanSavedCue {
  label: string;
  tone: PlanCueTone;
}

/** Saved/unsaved ownership cue shown next to the plan name (codex R3-PLAN-03).
 *
 *  - dirty                → "Unsaved changes" (warn) — the editor has diverged
 *    from the last save/load checkpoint, regardless of library membership.
 *  - clean + isSaved      → "Saved" (dim) — matches the loaded library plan.
 *  - clean + !isSaved     → "Not saved yet" (dim) — a fresh local draft that has
 *    never been written to (or loaded from) the library.
 *
 *  `isSaved` is "this editor is tied to a library plan" (loadedPlanId != null),
 *  NOT "the name exists somewhere" — two plans may share a name (spec §7). */
export function planSavedCue(editorDirty: boolean, isSaved: boolean): PlanSavedCue {
  if (editorDirty) return { label: "Unsaved changes", tone: "warn" };
  return isSaved
    ? { label: "Saved", tone: "dim" }
    : { label: "Not saved yet", tone: "dim" };
}
