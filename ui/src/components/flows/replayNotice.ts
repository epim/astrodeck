// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// replayNotice.ts - the editor's line that an armed auto-resume will replay
// the version of the flow its session froze, not the flow on screen (#473,
// S7 orchestrator ruling 1; spec 5.9).
//
// WHY THE LINE EXISTS. `engine.start` arms every run, and a night that ends
// short of its quota leaves the session dormant and still armed (auto-resume
// is on by default, Revision 2 ruling 7). At dusk ResumeArm starts that
// session on the plan it froze (`resume_arm.py`: `engine.start(
// replan_cooling(fresh.plan, ...), session=fresh)`), before anyone presses
// anything. Only CONTINUE replaces the session's plan with the saved flow
// (`_continue_flow_session`). So an operator who edited and saved the flow
// since would see the new graph, hear nothing, and the night would shoot the
// old exposures, filters, framing or counts. The line says so in spec 5.9's
// words: "the armed session will replay the version from 2026-09-22; press
// CONTINUE to apply your edits".
//
// THE RULE IS RULING 1'S, AND NOTHING MORE. The line shows only while the
// progress route says the session is armed (`session.armed`, the server's
// `Session.is_armed`: dormant with auto-resume on, the session ResumeArm
// would start) AND the flow record's saved time is later than the saved time
// of the version the session froze (`session.plan_saved_ts`). A null
// `plan_saved_ts` shows nothing: a session older than S7, a shipped
// Example's (never saved) and one whose plan a PATCH replaced all answer
// null, because the route never guesses a time, and so this does not either.
// An `armed` that is not exactly true (a server older than S7 sends none) is
// not armed.
//
// SAVED TIMES, NOT GRAPHS. Both sides of the comparison are the record's
// `updated_ts`: `run_flow` froze the one it read when the run started (or
// when CONTINUE last replaced the plan), and the store holds the one the last
// open or save answered. So unsaved edits do not raise the line, which is
// right, since CONTINUE starts the SAVED flow and would not apply them
// either. A save raises it whether or not it changed anything the run
// shoots, which is the ruling's trade. Renaming or deleting the flow's folder
// used to re-stamp `updated_ts` too, and the line then claimed edits nobody
// made (#512); since H4 a folder move leaves `updated_ts` alone
// (`FlowStore.rename_folder`, test_h4_folder_move_is_not_a_version.py).
//
// THE DATE is `plan_saved_ts` as a LOCAL YYYY-MM-DD, in the viewer's own
// zone: the day, on the operator's calendar, that the replayed version was
// saved. It is the moment somebody pressed Save, nothing computed from the
// site.
//
// Pure: no store, no React, no clock. The classic editor, the #/next canvas
// (under its toolbar) and the #/next phone stage list each read it through a
// string-or-null selector, so it re-renders nothing until the line changes.
import type { FlowProgress } from "../../lib/flowsApi";
import type { FlowRecordRec } from "./flowsTypes";

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** `ts` (unix seconds) as a local calendar date, YYYY-MM-DD, or null for a
 *  time the calendar cannot place: a finite number past the range `Date`
 *  holds would otherwise print "NaN-NaN-NaN" on the editor. */
function localDate(ts: number): string | null {
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** The replay line, or null when it must not show (see the header).
 *
 *  `progress` is the open flow's progress answer, `record` the open flow's
 *  record as the store holds it; both are the slice's, so the answer is
 *  always the open flow's own (the slice drops an answer for a flow that is
 *  no longer open). */
export function replayNotice(progress: FlowProgress | null | undefined,
                             record: Pick<FlowRecordRec, "updated_ts"> | null | undefined): string | null {
  const session = progress?.session;
  if (session?.armed !== true) return null;
  const frozen = session.plan_saved_ts;
  const saved = record?.updated_ts;
  if (!finite(frozen) || !finite(saved) || !(saved > frozen)) return null;
  const date = localDate(frozen);
  if (date === null) return null;
  return `the armed session will replay the version from ${date}; press CONTINUE to apply your edits`;
}
