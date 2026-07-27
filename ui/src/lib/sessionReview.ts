// sessionReview.ts — pure helpers for the review drawer (sessions spec §7):
// verdicts, grid filtering, bulk-selection reducer, local override apply.
import type { SessionFrame } from "../types";

export type Verdict = "accepted" | "rejected" | "overridden";

/** Effective acceptance = override if set else auto_accepted (spec §2). */
export function effectiveAccepted(f: SessionFrame): boolean {
  return f.override != null ? f.override === "accept" : f.auto_accepted;
}

/** Badge verdict: an override always shows as "overridden". */
export function verdictOf(f: SessionFrame): Verdict {
  if (f.override != null) return "overridden";
  return f.auto_accepted ? "accepted" : "rejected";
}

export interface FrameFilters {
  target_id?: string;
  night?: string;
  verdict?: Verdict;
  /** Filter BAND (UX #37) — "Ha", "OIII", … A frame carries only its
   *  `step_id`, so matching needs the plan-side resolver below. */
  filter?: string;
}

/** Resolve a frame's filter band from its plan step ("—" when the step has
 *  none / can't be found — the same string the review cards already print). */
export type FilterOfStep = (stepId: string) => string;

/** Filter semantics: accepted/rejected filter by EFFECTIVE acceptance;
 *  overridden = any frame carrying an override. `filterOf` is required only
 *  when `flt.filter` is set (UX #37); without it the band filter is inert
 *  rather than silently hiding everything. */
export function filterFrames(
  frames: SessionFrame[], flt: FrameFilters, filterOf?: FilterOfStep,
): SessionFrame[] {
  return frames.filter((f) => {
    if (flt.target_id && f.target_id !== flt.target_id) return false;
    if (flt.night && f.night !== flt.night) return false;
    if (flt.filter && filterOf && filterOf(f.step_id) !== flt.filter) return false;
    if (flt.verdict === "accepted" && !effectiveAccepted(f)) return false;
    if (flt.verdict === "rejected" && effectiveAccepted(f)) return false;
    if (flt.verdict === "overridden" && f.override == null) return false;
    return true;
  });
}

/** Toggle `id` in the selection (pure — returns a new array). */
export function toggleSel(sel: string[], id: string): string[] {
  return sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id];
}

/** Drop selected ids that are not in the visible set (pure). Filter changes
 *  must prune the selection so hidden-but-selected frames can't be regraded
 *  invisibly by a later bulk action. */
export function pruneSelection(sel: string[], visible: SessionFrame[]): string[] {
  const vis = new Set(visible.map((f) => f.id));
  return sel.filter((fid) => vis.has(fid));
}

/** Apply an override locally after a successful PATCH (pure; untouched frames
 *  stay reference-equal for React re-render scoping). */
export function withOverride(
  frames: SessionFrame[], id: string,
  override: "accept" | "reject" | null,
): SessionFrame[] {
  return frames.map((f) => (f.id === id ? { ...f, override } : f));
}
