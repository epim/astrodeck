// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// disarmed.ts - the one wording for "starting or resuming a run turned
// another session's auto-resume off" (#595, backlog ruling D-04).
//
// A run start is a SINGLETON (`SequenceEngine.start`): the session it arms is
// the only one left auto_resume'd, and until #595/D-04 every other armed
// session lost that switch with nothing on screen to say so - on 2026-09-29 a
// 7331 starter cost the owner's explicitly-armed NGC 1499 mosaic its
// auto-resume, found by chance five minutes later. WP-31 (a) made the server
// name what it disarmed, as a `disarmed` list on the response of every route
// that can trigger it; WP-65 showed it on the #/next Now empty state
// (`NowEmpty.tsx`'s two direct calls). W5 integration (#643) is the second
// surface: the classic flow run controls (`flowRunControls.tsx`), whose RUN
// path goes through `flowsSlice.ts`'s `flowsRun` and used to discard the
// response's `disarmed` list on a successful start.
//
// Shared here rather than left as NowEmpty.tsx's own private function, so a
// second surface reads the SAME sentence instead of growing a second,
// possibly-drifting one - the exact shape of bug #278 found in the naming
// preview (two hand-copied samples of the same table).
export interface DisarmedSession { id: string; name: string }

/** The one sentence every surface uses, so none of them can drift into a
 *  different wording for the same event. Falls back to the id for the rare
 *  row a legacy session saved with no name - the same fallback the server's
 *  own log line uses (`engine.py`'s `d["name"] or d["id"]`). */
export function disarmedWarningLine(disarmed: DisarmedSession[]): string {
  const names = disarmed.map((d) => (d.name && d.name.trim()) || d.id);
  return `Auto-resume was turned off for: ${names.join(", ")}.`;
}
