// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// coverageModel.ts - what the Target modal says of the coverage route in run
// mode (#177 package 2, WP 6.5; spec 2026-09-23 flows mosaic, 2.6).
//
// `GET /api/flows/{id}/coverage` (server sequence/coverage.py) lays each
// banked light's plate solve over the panel it was shot for and counts the
// ones that cover too little of it. This file is the pure reading of that
// answer: which block it is about, what each panel's PANELS line says, and
// whether the sky tints the panel. It fetches nothing (useCoverage.ts does)
// and draws nothing (PanelsSection and PanelLayer do).
//
// THE CHECK ONLY SAYS WHAT IT CAN SEE. A light with no plate solve is
// UNSTAMPED, and an unstamped frame is not a covered one, so a panel whose
// frames are all unstamped says nothing was checked yet, never "covered". A
// clean panel is plain: the sky and its row already say it is being shot. A
// panel with no frames has nothing to check and says nothing.
//
// WITH STAMPING OFF no light carries a solve this run, so "not checked yet"
// under every panel would say the same thing the route's note says once:
// the note is shown once (`BlockCoverage.note`) and the per-panel
// unchecked line is not. A panel that was checked earlier, while stamping
// was on, still says what fell short.
//
// THE ANSWER IS THIS BLOCK'S ONLY BY ITS GROUP AND ITS SESSION. The group is
// the one the progress route names on the block (`group_id`, the id the plan
// gives the block's panels), never a name: two blocks may share one. The
// session is the one that progress answer counted from, so the map and the
// progress bars can never describe two ledgers when a new session starts
// between the two reads.

import type { CoverageAnswer, CoveragePanel } from "../../../types";

/** What the sky does for a panel: `short` draws the tint. */
export type CoverageVerdict = "short";

/** One panel's coverage as PANELS and the sky use it. */
export interface PanelCoverage {
  /** "short" when some stamped frame covers less of the panel than the
   *  group's threshold, else null. */
  verdict: CoverageVerdict | null;
  /** The PANELS line, or null where the panel is plain. */
  line: string | null;
}

/** One block's coverage: the group's panels by 0-based "row,col". */
export interface BlockCoverage {
  threshold: number;
  /** The route's sentence for a mosaic that runs with stamping off, said
   *  once for the block; null when stamping is on. */
  note: string | null;
  stamping: boolean;
  panels: ReadonlyMap<string, CoveragePanel>;
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The answer's group for this block, or null: no answer, no session to read,
 *  another session than the progress answer counted, or no group of the
 *  block's `group_id`. */
export function blockCoverage(
  answer: CoverageAnswer | null | undefined,
  groupId: string | null | undefined,
  sessionId: string | null | undefined,
): BlockCoverage | null {
  if (!answer || !answer.session || !groupId) return null;
  if (sessionId && answer.session.id !== sessionId) return null;
  const group = (answer.groups ?? []).find((g) => g.group_id === groupId);
  if (!group || !finite(group.threshold)) return null;
  const panels = new Map<string, CoveragePanel>();
  for (const p of group.panels ?? []) {
    if (finite(p.row) && finite(p.col)) panels.set(`${p.row},${p.col}`, p);
  }
  return {
    threshold: group.threshold, stamping: answer.stamping !== false,
    note: answer.stamping === false && answer.note ? answer.note : null, panels,
  };
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** A percentage with no more digits than it needs: 0.875 is "87.5%", 0.6 is
 *  "60%". The threshold is shown as the server holds it, the worst overlap
 *  rounded DOWN, so the line never says a light covers as much as the
 *  threshold it was flagged for missing. */
function percent(fraction: number, down: boolean): string {
  const v = down ? Math.floor(fraction * 100) : Math.round(fraction * 1000) / 10;
  return `${v}%`;
}

/** The sentence for a panel with frames that fall short, in the words an
 *  operator acts on: how many, against what, how bad, and how far off. */
function shortfall(p: CoveragePanel, label: string, threshold: number): string {
  const head = `${p.flagged} of ${p.stamped} checked ${plural(p.stamped, "frame covers", "frames cover")}`
    + ` under ${percent(threshold, false)} of panel ${label}`;
  const worst = finite(p.worst_overlap_frac) ? `; the worst is ${percent(p.worst_overlap_frac, true)}` : "";
  const off = finite(p.worst_offset_arcmin)
    ? `${worst ? ", and" : ";"} the furthest is ${Math.round(p.worst_offset_arcmin * 10) / 10} arcmin off centre`
    : "";
  return head + worst + off;
}

/** One panel's coverage, or null when it has nothing to say: no frames, or
 *  a clean panel. `label` is the panel's `r-c`, 1-based. */
export function panelCoverage(
  p: CoveragePanel | undefined, label: string, block: BlockCoverage,
): PanelCoverage | null {
  if (!p || p.frames <= 0) return null;
  const short = p.flagged > 0;
  if (short) return { verdict: "short", line: shortfall(p, label, block.threshold) };
  if (p.stamped === 0 && block.stamping) {
    return {
      verdict: null,
      line: `not checked yet: ${p.frames} ${plural(p.frames, "frame", "frames")} banked, none plate-solved`,
    };
  }
  return null;
}
