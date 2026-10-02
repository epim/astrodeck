// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowProgress.ts - the TARGET card's state chip, "212/315 subs" (#189 S1 item
// 9, spec 1.2), read off `GET /api/flows/{id}/progress` (`flowsApi.progress`).
//
// ONE FORMATTER, BOTH CANVASES. The classic card (FlowNodeCard.tsx) and the
// #/next stage (next/hubs/session/flows/canvas/FlowNode.tsx) both draw the chip
// from `progressChip`, so the rule for WHEN a count may be shown lives once.
// Two copies of it is how one canvas comes to show a number the other has
// stopped believing.
//
// Pure: no store, no fetch, no React. Both cards subscribe with
//   useStore((s) => progressChip(s.flows.progress, node.id, s.flows.dirty))
// and because this returns a string or null - never a fresh object - a
// progress answer that leaves a card's count alone does not wake that card:
// the re-render discipline both card files exist for.
//
// A formatter has no clock, so it cannot tell a fresh answer from a stale one.
// What keeps the answer current is the slice: flowsSlice.ts re-reads it while
// the open flow's run is banking frames (#214, at most once per
// LIVE_PROGRESS_MIN_MS and once more when the run ends), and leaves the count
// on the card while that re-read is in flight.
//
// A MOSAIC READS IN PANELS (#189 S3, spec 1.2). A TARGET block with a grid
// reads "4/6 panels done" in place of its subs: the question a mosaic's
// operator asks of the card is which part of the sky is finished, and a sub
// total over six panels answers it for none of them. S1's single target keeps
// "212/315 subs", byte for byte.
import type { FlowProgress, FlowProgressBlock } from "../../lib/flowsApi";

const finite = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

/** True for a block the progress route marks as a mosaic: a `grid` of more
 *  than one panel. The route adds `grid` to a multi-panel TARGET's block and
 *  to no other (server `progress._mosaic`), so its absence is a single target,
 *  and so is a 1x1 grid, which a server could only send by mistake. */
function isMosaic(block: FlowProgressBlock): boolean {
  const grid = block.grid as unknown;
  if (grid === null || typeof grid !== "object") return false;
  const rows = finite((grid as { rows?: unknown }).rows);
  const cols = finite((grid as { cols?: unknown }).cols);
  return rows !== null && cols !== null && rows * cols > 1;
}

/** "N/M panels done" for a mosaic block, or null.
 *
 *  N is the panels whose `owed` is 0: every step of the panel has banked its
 *  count, so the run will not visit it again. M is the panels the plan
 *  SHOOTS. A skipped panel is not one of them: the route lists it in the
 *  block's `skipped`, never in `panels`, so it is in neither number. Counted
 *  over the grid instead, a 2x4 with two panels skipped would read "6/8" when
 *  every panel the run will shoot is finished, and the operator would wait
 *  for two panels nobody is going to shoot.
 *
 *  Null when no panel is shot (every panel skipped, or a block the plan
 *  dropped for want of coordinates): "0/0 panels done" is a fraction of
 *  nothing. Null too when any panel's `owed` is not a finite number, since a
 *  panel that cannot be read cannot be counted done or not done, and leaving
 *  it out would shrink M without saying so. */
function panelsChip(block: FlowProgressBlock): string | null {
  const panels = block.panels as unknown;
  if (!Array.isArray(panels) || panels.length === 0) return null;
  let done = 0;
  for (const p of panels) {
    const owed = finite((p as { owed?: unknown } | null)?.owed);
    if (owed === null) return null;
    if (owed === 0) done++;
  }
  return `${done}/${panels.length} panels done`;
}

/** The chip for one canvas node, or null when there is nothing true to say.
 *
 *  `{banked}/{total} subs` for a TARGET block: the subs the flow's session
 *  (the one Run would continue) has banked on this target's steps, over the
 *  subs those steps plan. `{done}/{shot} panels done` for a TARGET block with
 *  a grid, a mosaic (`panelsChip`).
 *  Null when:
 *
 *  - THE GRAPH IS DIRTY. The answer is the SAVED flow's: the server compiles
 *    the stored graph, and a step's id is its recipe (#77), so an unsaved
 *    exposure change is a different step with nothing banked. Until the edit
 *    is saved the answer describes a flow the canvas no longer shows. This
 *    rule is only as good as `dirty`, which a save clears only when the
 *    canvas is still the graph and name its PUT carried (#215).
 *  - THERE IS NO SESSION. The flow has never run, or its newest run was
 *    abandoned (an older session is never counted in its place).
 *    "0/315" would read as a campaign that shot nothing, when there is no
 *    campaign to count yet.
 *  - THE NODE IS NOT IN THE ANSWER, or its block is not a TARGET. Only TARGET
 *    and POOL nodes compile to blocks. A POOL's members are candidates the
 *    scheduler picks between, not one target's quota; a capture, a cycle or an
 *    action has no block at all.
 *
 *  BANKED IS THE BLOCK'S OWN. `orphaned` counts frames on steps the flow no
 *  longer has - a 120 s sub never fills the 180 s step that replaced it - so
 *  they are in no block's `banked`, and adding them back here would show a
 *  quota met by subs the run is going to shoot again.
 *
 *  Defensive about the shape, because a server older than S1 answers 404 and
 *  a stub or a proxy may answer anything: a missing `session`, a `blocks` that
 *  is not a list, or a count that is not a finite number is "nothing to say",
 *  never a chip reading "undefined/315 subs" and never a throw inside a
 *  card's render. */
export function progressChip(
  progress: FlowProgress | null | undefined, nodeId: string, dirty: boolean,
): string | null {
  if (dirty) return null;
  if (!progress?.session) return null;
  if (!Array.isArray(progress.blocks)) return null;
  const block = progress.blocks.find((b) => b?.node_id === nodeId);
  if (!block || block.kind !== "target") return null;
  if (isMosaic(block)) return panelsChip(block);
  const banked = finite(block.banked);
  const total = finite(block.total);
  if (banked === null || total === null) return null;
  return `${banked}/${total} subs`;
}
