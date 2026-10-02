// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// planLibrary.ts — pure presentation helpers for the unified Plan panel (G2).
// The panel merges the plan IDENTITY (name + saved/unsaved cue) with the server
// plan LIBRARY (saved rows); these two pure functions carry the only branching
// logic worth a test, keeping the component a thin render.
import type { PlanRow } from "../types";

/** What an unreadable row says when the server marked it with no usable
 *  reason. The MARK is the server saying "this is a file, not a plan"; a
 *  blank sentence beside it is a broken reason, never a readable plan. */
export const PLAN_UNREADABLE_FALLBACK = "this AstroDeck cannot read this plan";

/** Why this saved-plan row cannot be loaded, run or exported, or null for a
 *  plan (#378).
 *
 *  THE ONE READING EVERY SURFACE SHARES - the classic Plan panel, the #/next
 *  plan library's summary and SESSION / NOW's tonight list - so no surface
 *  decides on its own that a row with no numbers is a plan with none. Read as
 *  a plan, the row printed "undefinedt · undefinedf · NaNm" beside a LOAD and
 *  an EXPORT that the server answers 422, and on the phone a RUN that fails
 *  the same way.
 *
 *  EITHER KEY MARKS IT. The server sends `status: "unreadable"` and the reason
 *  together, and a plan row carries neither, so a row with one of them is a
 *  file the server could not read, whatever the other says. Reading a damaged
 *  file as a plan offers verbs that all fail; the reverse cannot happen here,
 *  because no plan row has either key. */
export function planUnreadableReason(row: Pick<PlanRow, "status" | "unreadable">): string | null {
  const status = row.status as unknown;
  const reason = row.unreadable as unknown;
  if (status !== "unreadable" && (reason === undefined || reason === null)) return null;
  return typeof reason === "string" && reason.trim() !== "" ? reason : PLAN_UNREADABLE_FALLBACK;
}

/** The compact metadata chip for a saved-plan row: "6t · 300f · 480m".
 *  Integration minutes are rounded to whole minutes to match the server's list
 *  summary intent (plans.py::_summarize rounds to 0.1; the row shows whole).
 *
 *  An unreadable row has no numbers to show, so its chip is its reason
 *  ("unreadable: fails validation: ..."), the words the server's 422 would
 *  say to a LOAD. */
export function planRowSummary(
  row: Pick<PlanRow, "targets" | "frames" | "integration_min" | "status" | "unreadable">,
): string {
  const why = planUnreadableReason(row);
  if (why !== null) return `unreadable: ${why}`;
  return `${row.targets}t · ${row.frames}f · ${Math.round(row.integration_min)}m`;
}

/** The delete confirm's body for an unreadable row.
 *
 *  A readable plan's delete asks with its title alone. This one names the FILE,
 *  because that is what goes (there is no plan in it to name), and says why
 *  nothing was offered to save a copy first: the export answers 422 for a file
 *  that does not read as a plan. The id is a uuid stem, never a path. */
export function unreadablePlanDeleteBody(row: Pick<PlanRow, "id">): string {
  return `Removes the file ${row.id}.json from the rig's plan library. It does not read as a plan, `
    + "so it cannot be loaded or exported first. This cannot be undone.";
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
 *  NOT "the name exists somewhere" — two plans may share a name (spec §7).
 *
 *  `canWrite` (control.capture): a viewer can edit the plan BUILDER (existing
 *  contract) but has no Save/Save-as, so a warn-toned "Unsaved changes" would be
 *  an alarm with no actionable button — for non-writers the cue keeps its honest
 *  label but drops to dim (informational, not a call to action). */
export function planSavedCue(
  editorDirty: boolean, isSaved: boolean, canWrite = true,
): PlanSavedCue {
  if (editorDirty) {
    return { label: "Unsaved changes", tone: canWrite ? "warn" : "dim" };
  }
  return isSaved
    ? { label: "Saved", tone: "dim" }
    : { label: "Not saved yet", tone: "dim" };
}
