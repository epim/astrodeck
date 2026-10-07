// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// sessionReview.ts — pure helpers for the review drawer (sessions spec §7):
// verdicts, grid filtering, bulk-selection reducer, local override apply, and
// the mosaic grouping a session's plan carries (#188).
import type { SequencePlan, SessionFrame } from "../types";

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
  /** A MOSAIC (#188): every frame of every panel in the group. A frame carries
   *  only its `target_id`, so matching needs the plan-side resolver below. The
   *  drawer keeps this and `target_id` exclusive (`withTargetChoice`): a panel
   *  is already one target, and both keys set would mean "this panel AND this
   *  mosaic", which reads as a panel filter that hides the rest of its group. */
  group_id?: string;
  night?: string;
  verdict?: Verdict;
  /** Filter BAND (UX #37) — "Ha", "OIII", … A frame carries only its
   *  `step_id`, so matching needs the plan-side resolver below. */
  filter?: string;
}

/** Resolve a frame's filter band from its plan step ("—" when the step has
 *  none / can't be found — the same string the review cards already print). */
export type FilterOfStep = (stepId: string) => string;

/** The mosaic a target is a panel of, or undefined for a single target. */
export type GroupOfTarget = (targetId: string) => string | undefined;

/** Filter semantics: accepted/rejected filter by EFFECTIVE acceptance;
 *  overridden = any frame carrying an override. `filterOf` is required only
 *  when `flt.filter` is set (UX #37), and `groupOf` only when `flt.group_id`
 *  is (#188); without them those filters are inert rather than silently
 *  hiding everything. */
export function filterFrames(
  frames: SessionFrame[], flt: FrameFilters, filterOf?: FilterOfStep,
  groupOf?: GroupOfTarget,
): SessionFrame[] {
  return frames.filter((f) => {
    if (flt.target_id && f.target_id !== flt.target_id) return false;
    if (flt.group_id && groupOf && groupOf(f.target_id) !== flt.group_id) return false;
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

// ---------------------------------------------------------------- mosaics (#188)
// A mosaic's panels are N ordinary targets named "<name> r-c". Without the plan
// they read as N unrelated targets, in the select, the grid and every count, so
// the grouping is read off the session's frozen plan snapshot: its `groups`
// (written by the flow compile) and each member's `mosaic_group`, `panel_row`
// and `panel_col`. A frame carries only `target_id`, so everything here goes
// through the plan. Pure, and shared by the classic and #/next UIs through the
// one drawer they both mount.

/** One panel of a mosaic, as the review drawer lists it. */
export interface MosaicPanel {
  target_id: string;
  /** "<row+1>-<col+1>", the label the FITS PANEL card, the Plan and the report
   *  all print; the target's own name when it has no grid place (a Plan-UI
   *  mosaic, which never carried one). */
  label: string;
  row: number | null;
  col: number | null;
}

export interface MosaicGroup {
  /** `TargetGroup.id`, or a Plan-UI mosaic's `mosaic_group` string. The id is
   *  the key and the name is only a caption: a mosaic renamed between nights
   *  is the same group, and two mosaics that share a name are not. */
  id: string;
  name: string;
  /** In grid order (row, then column); a panel with no grid place after them,
   *  in the plan's order. */
  panels: MosaicPanel[];
}

const isIndex = (v: unknown): v is number =>
  typeof v === "number" && Number.isInteger(v) && v >= 0;

/** Row-major, gridded panels first. `Array.sort` is stable, so panels that tie
 *  (none gridded) keep the plan's order. */
function byGridPlace(a: MosaicPanel, b: MosaicPanel): number {
  if (a.row == null || a.col == null) return b.row == null || b.col == null ? 0 : 1;
  if (b.row == null || b.col == null) return -1;
  return a.row - b.row || a.col - b.col;
}

/** The mosaics in a session's plan: one per `plan.groups` entry that has
 *  member targets, then one per Plan-UI `mosaic_group` string no entry covers
 *  (legacy plans carry only the string). A string shared by a single target is
 *  not a mosaic, matching the Plan's own rule that "apply to all panels" needs
 *  more than one; a `groups` entry is one however many panels it holds,
 *  because the compile wrote it. A target with no `id` cannot own a frame and
 *  is skipped. */
export function mosaicGroupsOf(
  plan: Pick<SequencePlan, "targets" | "groups">,
): MosaicGroup[] {
  const byId = new Map<string, MosaicGroup>();
  const written = new Set<string>();
  for (const g of plan.groups ?? []) {
    if (byId.has(g.id)) continue;
    written.add(g.id);
    byId.set(g.id, { id: g.id, name: String(g.name ?? "").trim() || g.id, panels: [] });
  }
  for (const t of plan.targets) {
    const key = t.mosaic_group;
    if (!key || !t.id) continue;
    let grp = byId.get(key);
    if (!grp) {
      grp = { id: key, name: key, panels: [] };
      byId.set(key, grp);
    }
    const row = isIndex(t.panel_row) ? t.panel_row : null;
    const col = isIndex(t.panel_col) ? t.panel_col : null;
    grp.panels.push({
      target_id: t.id,
      label: row != null && col != null ? `${row + 1}-${col + 1}` : t.name,
      row,
      col,
    });
  }
  return [...byId.values()]
    .filter((g) => g.panels.length > (written.has(g.id) ? 0 : 1))
    .map((g) => ({ ...g, panels: [...g.panels].sort(byGridPlace) }));
}

/** target id -> the id of the mosaic it is a panel of (undefined for a single),
 *  the `groupOf` that `filterFrames` takes for a `group_id` filter. */
export function groupIdResolver(groups: MosaicGroup[]): GroupOfTarget {
  const m = new Map<string, string>();
  for (const g of groups) for (const p of g.panels) m.set(p.target_id, g.id);
  return (targetId) => m.get(targetId);
}

export interface PanelTally extends MosaicPanel {
  accepted: number;
  rejected: number;
}
export interface GroupTally {
  id: string;
  name: string;
  accepted: number;
  rejected: number;
  panels: PanelTally[];
}

/** Accepted and rejected counts per panel and per mosaic. Both are the
 *  EFFECTIVE verdict (`effectiveAccepted`), so an operator's regrade moves a
 *  frame between them and the roll-up never contradicts the grid below it. Every
 *  panel gets a row, a panel set aside included, at 0 and 0: a panel that has
 *  no frames is the one the operator most needs to see is missing. */
export function mosaicRollup(groups: MosaicGroup[], frames: SessionFrame[]): GroupTally[] {
  const byTarget = new Map<string, { accepted: number; rejected: number }>();
  for (const f of frames) {
    const t = byTarget.get(f.target_id) ?? { accepted: 0, rejected: 0 };
    if (effectiveAccepted(f)) t.accepted++;
    else t.rejected++;
    byTarget.set(f.target_id, t);
  }
  return groups.map((g) => {
    const panels = g.panels.map((p) => {
      const t = byTarget.get(p.target_id);
      return { ...p, accepted: t?.accepted ?? 0, rejected: t?.rejected ?? 0 };
    });
    return {
      id: g.id,
      name: g.name,
      panels,
      accepted: panels.reduce((n, p) => n + p.accepted, 0),
      rejected: panels.reduce((n, p) => n + p.rejected, 0),
    };
  });
}

// The target select carries one value for two filter keys: a target id, or
// "group:<id>" for "all panels" of a mosaic. A target id is a generated uid and
// never starts with the prefix.
const GROUP_CHOICE = "group:";

/** The select value for a mosaic's "all panels" option. */
export const groupChoice = (groupId: string): string => GROUP_CHOICE + groupId;

/** What the target select should read for the current filters. */
export function targetChoiceOf(flt: FrameFilters): string {
  return flt.group_id ? groupChoice(flt.group_id) : flt.target_id ?? "";
}

/** The filters after the target select (or a roll-up row) picks `value`: a
 *  mosaic sets `group_id`, a target sets `target_id`, "" clears both, and the
 *  two keys are never set together. */
export function withTargetChoice(flt: FrameFilters, value: string): FrameFilters {
  if (value.startsWith(GROUP_CHOICE)) {
    return { ...flt, group_id: value.slice(GROUP_CHOICE.length) || undefined, target_id: undefined };
  }
  return { ...flt, target_id: value || undefined, group_id: undefined };
}
