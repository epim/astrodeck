// flowRunState.ts - what a flow's run is doing, read off what the run-mode
// surfaces hold (#189 S5, #449, #451; spec 2.6 run mode, 5.8 holds, 5.9
// CONTINUE, 5.10 published state, 6.9 privacy).
//
// WHAT IT READS. The sessions known to be the open flow's (the slice's
// `flows.sessionIds`: every session a progress answer counted from and the
// one RUN's answer named) say which runs are this flow's; the sequence state
// (`GET /api/sequence/state` and the WS `sequence` event, server
// `SequenceEngine._set_state`) says what the rig is doing now; the progress
// route's answer (`flowsApi.progress`, server `flows/progress.py`) says what
// the flow has banked and which group is a block's. The run-mode sheet, the
// Run button and the run readouts join them, and every join is one of the
// readers below, so the rule for WHEN a run is this flow's, WHICH group is a
// block's and WHAT a panel is doing lives once.
//
// WHEN, FROM THE KNOWN SESSIONS, NEVER FROM THE PROGRESS ANSWER (#449). The
// answer is a cache that every open, save and RUN blanks for a round trip
// (the slice clears it before each re-read), and a failed read leaves blank.
// Run mode read off it let go of a running flow for a round trip after every
// save made during the run, offering DONE over a night in progress, and never
// engaged at all behind a read that failed (422, or a server older than S1).
// The known sessions are kept across all of those.
//
// Pure: no store, no fetch, no React, no clock. Each answers from its
// arguments alone. `flowRunLive` answers a boolean and `groupForBlock` the
// published group itself or null, so a store selector over either wakes a
// component only when the answer changes; `panelStateOf` builds a small
// record per call, so a selector over it should compare its fields (`kind`,
// and `reason` or `run` and `hold`).
//
// IDENTITY, NEVER NAMES. A run is this flow's by SESSION ID: the session the
// engine writes (`state.session.id`) is one the slice knows as the flow's,
// learned from the progress route (`progress.session.id`, server
// `current_for_flow`, the session CONTINUE starts the engine on) or from
// RUN's answer. A live group is a block's by GROUP ID: the block's
// `group_id` (server `progress._mosaic`) is the id the plan minted and the
// engine publishes (`state.group.id`). A name is a label an operator can
// repeat: two TARGET blocks may both be called "M31", and a run of one must
// never light up the other's panels.
import { runIsLive } from "../../lib/lastSessionFrame";
import type { SequenceGroupState, SequenceState } from "../../types";

/** No session known. Shared, so a selector that answers it twice answers the
 *  same object. */
const NONE: readonly string[] = [];

/** The sessions known to be the OPEN flow's, from the flows state: its
 *  `sessionIds` while a record is open, and none while none is (a list left
 *  behind by a write that cleared only the record belongs to no flow).
 *
 *  The one way every reader asks, so the store's list is read the same at
 *  both Target modal doors, the RUN button, the readouts and the slice's
 *  own live refresh. Structural, so this module takes no import from the
 *  slice; `sessionIds` is optional so a hand-built state without it reads
 *  as knowing none. */
export function knownSessions(
  flows: { record: unknown; sessionIds?: readonly string[] } | null | undefined,
): readonly string[] {
  if (!flows || !flows.record) return NONE;
  return Array.isArray(flows.sessionIds) ? flows.sessionIds : NONE;
}

/** Is the rig's run this flow's run, and live?
 *
 *  True only when the sequence state is a live run (`runIsLive`: running,
 *  paused, holding for cloud or winding down from an abort, each of which is
 *  still the run) AND the session that run writes is one of `known`, the
 *  sessions known to be this flow's (`knownSessions`). Another flow's run, a
 *  run that has ended, a flow with no session known (never run, or not yet
 *  answered for) and an empty id are all false: an empty id names no
 *  session, so finding one in the list would say nothing. */
export function flowRunLive(known: readonly string[] | null | undefined,
                            sequence: SequenceState | null | undefined): boolean {
  const running = sequence?.session?.id;
  if (typeof running !== "string" || running === "") return false;
  return runIsLive(sequence) && Array.isArray(known) && known.includes(running);
}

/** True while the engine still owns the rig, from `flows.run.phase` (the
 *  client's own OPTIMISTIC latch, never the server's own answer - that is
 *  `flowRunLive` above).
 *
 *  `stopping` counts: the engine publishes "aborting" the moment teardown
 *  starts and only says stopped once the rig has, so a button that flipped
 *  back to RUN here would offer to start over a moving mount.
 *
 *  LIVES HERE, NOT IN `flowRunControls.tsx` (moved at W5 integration, #647):
 *  `flowsSlice.ts`'s own `onSequence` needs it too, to clear a finished run's
 *  `phase` back to idle the moment the server reports the run over, and this
 *  module is the one both can import without a cycle (`flowRunControls.tsx`
 *  imports FROM `flowsSlice.ts`). Re-exported from `flowRunControls.tsx` so
 *  every existing import of it keeps resolving. */
export function isRunPhaseLive(phase: string): boolean {
  return phase === "running" || phase === "holding" || phase === "stopping";
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

/** The live run states in which the group's panel is the current one and no
 *  exposure of it is being made: `runIsLive`'s three states besides
 *  "running". */
export type HeldRun = "paused" | "holding" | "aborting";

/** What a panel is doing in the live run, when that is something to draw.
 *
 *  CURRENT is the panel the group is on while the run is paused, holding or
 *  winding down from an Abort (#451): it is still the panel the visit is on,
 *  so it is drawn as the current one, and nothing is exposing it, so it is
 *  never worded as shot. `run` is the run's state, and `hold` the engine's
 *  reason for a hold ("clouds") while `run` is "holding", null otherwise. */
export type PanelRunState =
  | { kind: "shooting" }
  | { kind: "current"; run: HeldRun; hold: string | null }
  | { kind: "set_aside"; reason: string };

/** What `panelStateOf` reads of the run: the sequence state's `state` and,
 *  for a hold, its `hold`. The sequence state itself is one. */
export type PanelRunSource = Pick<SequenceState, "state" | "hold">;

/** What the panel labelled `label` ("<row>-<col>", counted from 1, the
 *  engine's label) is doing in `group`, or null when it is doing nothing to
 *  draw (waiting its turn, done, or no group). `run` is what the run is
 *  doing (the sequence state the group came from).
 *
 *  SET ASIDE first, with the engine's reason in its own words: a panel set
 *  aside tonight stays set aside for the night, whatever else the group or
 *  the run is doing (spec 6.7).
 *
 *  Then the panel the group names as current (`group.panel`), and never
 *  while the group waits on the meridian rule (`meridian_wait`). The panel
 *  a waiting group names is the last one it shot, or the one it is hopping
 *  to at the crossing, and neither is being shot. The answer is then the
 *  same for an operator, who is served that panel, and a viewer, who is
 *  not (spec 5.10: `panel` and `pass` are withheld across the wait), so the
 *  reader adds nothing to what either is allowed to see.
 *
 *  THE CURRENT PANEL IS SHOOTING ONLY WHILE THE RUN IS "running" (#451).
 *  The engine publishes the group through a pause, a cloud hold and an
 *  Abort's wind-down (each is still the run, `runIsLive`), and the panel it
 *  names is the visit's; but a paused run lets the frame it has open end
 *  and then waits at the frame boundary, a hold takes unsaved check frames
 *  and darks, never a sub of the panel, and an Abort is ending the
 *  exposure. So those three answer CURRENT with the
 *  run's state, worded by the state and never by `hold`: an Abort pressed
 *  in a cloud hold publishes "aborting" with the hold's `hold: "clouds"`
 *  still riding it (#513), and that run is stopping. Any other state (a
 *  run that has ended, or none) shoots nothing: null.
 *
 *  `run` IS REQUIRED. Until the S7 integration it could be left out, and a
 *  caller that did got S5's `shooting` for the current panel: framingModel
 *  `runPanelsOf`, which draws the Target modal's panels, asked that way, so
 *  the modal still said "shooting now" through a pause, a cloud hold and an
 *  Abort's wind-down. It passes the sequence state now and draws CURRENT with
 *  the corner ticks (`panelDrawState`), and a caller that forgets the state
 *  is a type error rather than a panel worded as shot. A `run` given as null
 *  is a run nobody knows, which shoots nothing. */
export function panelStateOf(label: string,
                             group: SequenceGroupState | null | undefined,
                             run: PanelRunSource | null): PanelRunState | null {
  if (!group || typeof group !== "object") return null;
  const aside = Array.isArray(group.set_aside)
    ? group.set_aside.find((a) => a && a.panel === label)
    : undefined;
  if (aside) {
    return { kind: "set_aside", reason: typeof aside.reason === "string" ? aside.reason : "" };
  }
  if (group.meridian_wait) return null;
  if (typeof group.panel !== "string" || group.panel !== label) return null;
  const state = run?.state;
  if (state === "running") return { kind: "shooting" };
  if (state === "paused" || state === "aborting") return { kind: "current", run: state, hold: null };
  if (state === "holding") {
    return { kind: "current", run: state, hold: typeof run?.hold === "string" && run.hold ? run.hold : null };
  }
  return null;
}
