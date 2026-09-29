// flowRunState.ts - what a flow's run is doing, read off the two answers the
// run-mode surfaces hold (#189 S5; spec 2.6 run mode, 5.9 CONTINUE, 5.10
// published state, 6.9 privacy).
//
// THE TWO ANSWERS. The progress route (`flowsApi.progress`, server
// `flows/progress.py`) says what the flow has banked and which session Run
// would continue; the sequence state (`GET /api/sequence/state` and the WS
// `sequence` event, server `SequenceEngine._set_state`) says what the rig is
// doing now. The run-mode sheet, the Run button's copy and the phone
// readouts join the two, and every join is one of the readers below, so the
// rule for WHEN a run is this flow's, WHICH group is a block's and WHAT a
// panel is doing lives once.
//
// Pure: no store, no fetch, no React, no clock. Each answers from its
// arguments alone. `flowRunLive` answers a boolean and `groupForBlock` the
// published group itself or null, so a store selector over either wakes a
// component only when the answer changes; `panelStateOf` builds a small
// record per call, so a selector over it should compare `kind` and `reason`.
//
// IDENTITY, NEVER NAMES. A run is this flow's by SESSION ID: the session the
// engine writes (`state.session.id`) is the one the progress route counts
// (`progress.session.id`, server `current_for_flow`), because CONTINUE starts
// the engine on that very session. A live group is a block's by GROUP ID: the
// block's `group_id` (server `progress._mosaic`) is the id the plan minted and
// the engine publishes (`state.group.id`). A name is a label an operator can
// repeat: two TARGET blocks may both be called "M31", and a run of one must
// never light up the other's panels.
import type { FlowProgress } from "../../lib/flowsApi";
import { runIsLive } from "../../lib/lastSessionFrame";
import type { SequenceGroupState, SequenceState } from "../../types";

/** Is the rig's run this flow's run, and live?
 *
 *  True only when the sequence state is a live run (`runIsLive`: running,
 *  paused, holding for cloud or winding down from an abort, each of which is
 *  still the run) AND the session that run writes is the session the
 *  progress route counts for this flow. Another flow's run, a run that has
 *  ended, a flow that has never run (`progress.session` null) and an id that
 *  is empty on either side are all false: an empty id names no session, so
 *  two of them agreeing says nothing. */
export function flowRunLive(progress: FlowProgress | null | undefined,
                            sequence: SequenceState | null | undefined): boolean {
  const ours = progress?.session?.id;
  const running = sequence?.session?.id;
  if (typeof ours !== "string" || ours === "") return false;
  return runIsLive(sequence) && running === ours;
}

/** The published group of the block whose `group_id` is `groupId`, or null.
 *
 *  The engine publishes `state.group` only while the target it is on belongs
 *  to a group (spec 5.10), so this is the group a live run is shooting, and
 *  it is the block's only when its id IS the block's `group_id`. Never by
 *  name (see the header). Null when the run is not live: the last publish of
 *  a finished run still carries the group it ended on, and a panel that
 *  group names is not being shot. Null too for a block with no plan group
 *  (`group_id` null: every panel skipped) and for a single target's block
 *  (no `group_id` at all), neither of which any run publishes. */
export function groupForBlock(sequence: SequenceState | null | undefined,
                              groupId: string | null | undefined): SequenceGroupState | null {
  if (typeof groupId !== "string" || groupId === "") return null;
  if (!runIsLive(sequence)) return null;
  const group = sequence?.group;
  if (!group || typeof group !== "object") return null;
  return group.id === groupId ? group : null;
}

/** What a panel is doing in the live run, when that is something to draw. */
export type PanelRunState =
  | { kind: "shooting" }
  | { kind: "set_aside"; reason: string };

/** What the panel labelled `label` ("<row>-<col>", counted from 1, the
 *  engine's label) is doing in `group`, or null when it is doing nothing to
 *  draw (waiting its turn, done, or no group).
 *
 *  SET ASIDE first, with the engine's reason in its own words: a panel set
 *  aside tonight stays set aside for the night, whatever else the group is
 *  doing (spec 6.7).
 *
 *  SHOOTING is the panel the group names as current (`group.panel`), and
 *  never while the group waits on the meridian rule (`meridian_wait`). The
 *  panel a waiting group names is the last one it shot, or the one it is
 *  hopping to at the crossing, and neither is being shot. The answer is
 *  then the same for an operator, who is served that panel, and a viewer,
 *  who is not (spec 5.10: `panel` and `pass` are withheld across the wait),
 *  so the reader adds nothing to what either is allowed to see. */
export function panelStateOf(label: string,
                             group: SequenceGroupState | null | undefined): PanelRunState | null {
  if (!group || typeof group !== "object") return null;
  const aside = Array.isArray(group.set_aside)
    ? group.set_aside.find((a) => a && a.panel === label)
    : undefined;
  if (aside) {
    return { kind: "set_aside", reason: typeof aside.reason === "string" ? aside.reason : "" };
  }
  if (group.meridian_wait) return null;
  return typeof group.panel === "string" && group.panel === label ? { kind: "shooting" } : null;
}
