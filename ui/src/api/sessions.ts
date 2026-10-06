// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// api/sessions.ts — typed wrappers for the multi-night session routes
// (sessions spec §6). Cookie auth is automatic; ApiError on non-2xx.
import { api } from "../api";
import type {
  SequencePlan, SequenceState, Session, SessionFilesIndex, SessionFrame, SessionListRow,
  SessionRow, UnreadableSessionRow,
} from "../types";

export interface SessionPatch {
  auto_resume?: boolean;
  status?: "abandoned";
  plan?: SequencePlan;
}

export interface MergeSummary {
  kept: string[];
  new: string[];
  dropped: string[];
}

export interface SessionPatchResult {
  id: string;
  status: string;
  auto_resume: boolean;
  remaining: Record<string, number>;
  merge?: MergeSummary;
}

/** An unreadable row as the server sends it since #266: `types.ts`'s #242
 *  row, plus whether a backup sits beside the file.
 *
 *  `isUnreadableRow` still narrows to the #242 row, not to this: a guard onto
 *  an intersection stops TypeScript narrowing the other branch to
 *  `SessionRow`, and every session card reads its counts off that branch.
 *  A #242 row is assignable to this type (the key is optional), so a reader
 *  that wants the flag takes this type as its parameter, as
 *  `unreadableDeleteBody` does. */
export type UnreadableListRow = UnreadableSessionRow & {
  /** True while `<id>.json.bak` sits beside the file, which DELETE keeps,
   *  with the thumbnails, because it can be the last good copy of the ledger
   *  (the copy taken before an ADOPT rewrote it). The server leaves the key
   *  OUT when there is none, so absent is the normal case, not an error. It
   *  says the file exists, not that it would load. */
  backup?: boolean;
};

/** Is this list entry a file the store could not read (#242)? */
export const isUnreadableRow = (r: SessionListRow): r is UnreadableSessionRow =>
  r.status === "unreadable";

/** What `DELETE /api/sessions/{id}` answers. `backup_kept` and `detail` come
 *  only from deleting an unreadable file with a backup beside it (#266): the
 *  backup's file name, and the server's words saying it remains and how it
 *  brings the ledger back. A readable session's delete answers
 *  `{deleted}` alone. */
export interface DeleteSessionResult {
  deleted: string;
  backup_kept?: string;
  detail?: string;
}

/** The delete confirm's body for an unreadable row (#266).
 *
 *  NOT THE SESSION DELETE'S SENTENCE. That one says "Removes the session
 *  ledger and thumbnails", and for this row it was wrong both ways: there is
 *  no ledger anyone can read, and the server keeps a backup beside the file,
 *  with the thumbnails, because that backup can be the last good copy of the
 *  ledger. So it names the one file that goes, by the name it has on disk,
 *  and, when the row reports a backup, names that and says it stays. With no
 *  backup the thumbnails go too, and it says so: a confirm that names less
 *  than the delete removes is the defect #266 was.
 *
 *  The FITS sentence stays word for word: it is the answer to the question
 *  every delete raises. */
export function unreadableDeleteBody(row: Pick<UnreadableListRow, "id" | "backup">): string {
  const file = `${row.id}.json`;
  if (row.backup) {
    return `Removes only the file ${file}, which cannot be read. Its backup ${file}.bak stays, `
      + "and so do the thumbnails, because the backup may be the last good copy of this session log. "
      + "Saved FITS frames are NOT deleted. Removing the damaged file cannot be undone.";
  }
  return `Removes the file ${file}, which cannot be read, and its thumbnails. `
    + "Saved FITS frames are NOT deleted. This cannot be undone.";
}

/** Every entry `GET /api/sessions` sends, unreadable files included (#242).
 *
 *  ONLY FOR A LIST THAT DRAWS THE UNREADABLE ROW ON PURPOSE: the classic
 *  SessionsPanel and the #/next Gallery, each of which shows it read-only with
 *  its reason and a DELETE. */
export const listSessionRows = (): Promise<SessionListRow[]> =>
  api.get<{ sessions: SessionListRow[] }>("/api/sessions").then((r) => r.sessions);

/** The readable sessions only.
 *
 *  THE DEFAULT IS THE SAFE ONE. Every other reader of the list (the Now
 *  screen's session lookup, the files sheet's picker, the plan editor's
 *  sessions section, Settings' gallery count) was written for a row with a
 *  name, counts and a status it can act on. Handed an unreadable row, each of
 *  them would draw a nameless session with no frames or sort on a missing
 *  timestamp, so they never see one: a reader has to ask for them by name.
 *
 *  A payload that is not a list passes through untouched, as it did before
 *  the split: `sessionData.ts` already guards it with `Array.isArray` so that a
 *  truncated answer costs one line rather than the screen, and a `.filter`
 *  here would have turned that into a rejection. */
export const listSessions = (): Promise<SessionRow[]> =>
  listSessionRows().then((rows) => (Array.isArray(rows)
    ? rows.filter((r): r is SessionRow => !isUnreadableRow(r))
    : rows as unknown as SessionRow[]));

export const getSession = (id: string): Promise<Session> =>
  api.get<Session>(`/api/sessions/${id}`);

export const resumeSession = (id: string): Promise<{ resumed: boolean; remaining: number }> =>
  api.post<{ resumed: boolean; remaining: number }>(`/api/sessions/${id}/resume`);

export const patchSession = (id: string, body: SessionPatch): Promise<SessionPatchResult> =>
  api.patch<SessionPatchResult>(`/api/sessions/${id}`, body);

export const patchFrame = (
  id: string,
  frameId: string,
  body: { override?: "accept" | "reject" | null; metrics?: Record<string, number> },
): Promise<{ frame: SessionFrame; remaining: Record<string, number> }> =>
  api.patch<{ frame: SessionFrame; remaining: Record<string, number> }>(
    `/api/sessions/${id}/frames/${frameId}`, body);

export const deleteSession = (id: string): Promise<DeleteSessionResult> =>
  api.del<DeleteSessionResult>(`/api/sessions/${id}`);

/** What is armed and waiting, and what is holding it.
 *
 *  The engine's own state cannot answer either half: `_set_state` clears the
 *  session sub-block on every terminal transition, so a night that ended owing
 *  58 frames leaves `sequence` as literally `{state: "idle"}`. Monitor rendered
 *  "No run active - plan a session" over exactly that, which is an instruction
 *  to strand the armed session (a fresh start disarms every other one). */
export interface ResumeArmState {
  armed: {
    id: string; name: string; owed: number;
    accepted: number; total: number;
    origin: string; origin_id: string;
  } | null;
  hold: {
    reason: string;
    since: number | null;
    retry_at: number | null;
    session_id: string;
    session_name: string;
    owed: number;
    /** The numbers behind a start-floor or slew-limit hold: the target's
     *  altitude, its floor, the wait until it rises, the gate's azimuth
     *  (#233). The server leaves the key OUT for a principal without
     *  view.site_derived, because each of those numbers is a function of the
     *  site. Absent is the normal case, not an error. */
    site_detail?: string;
  } | null;
  /** True while ResumeArm's recovery ladder runs: after a restart it
   *  blind-solves and re-centres the mount for minutes, with the engine idle,
   *  before it starts the session (#220, #246). False otherwise. Absent from a
   *  server older than H2, which reads the same as false. */
  recovering: boolean;
  /** Which session the ladder is recovering and which step it is on, while
   *  `recovering`; null otherwise. `step` is a word from the server's
   *  `LADDER_STEPS` ("starting", "safety", "focus", "autofocus", "solve",
   *  "limits", "recentre") and nothing more, because this route is view.status
   *  and a position would give a viewer the site (#140, #233). */
  recovery: { step: string; session_id: string; session_name: string } | null;
}

export const getResumeArm = (): Promise<ResumeArmState> =>
  api.get<ResumeArmState>("/api/sequence/resume-arm");

/** The sentence both Monitors show while the recovery ladder runs (#246), or
 *  null when it is not running.
 *
 *  WHY IT HAS TO BE SAID. The ladder moves the mount on its own for minutes
 *  with the engine idle, so without this line every screen showed an idle rig
 *  while the rig slewed, and the operator learnt the ladder existed only by
 *  pressing RUN and meeting the 409. The step is how an operator tells a
 *  ladder that is solving from one that has wedged.
 *
 *  THE STEP IS RENDERED AS THE SERVER SENDS IT. It is a word by design, and
 *  nothing positional (an altitude, a target position, a time) may be added
 *  to it here (#233): the route is view.status.
 *
 *  Both fields are asked, not just `recovery`: `recovering` is the server's
 *  flag and `recovery` its detail, and a stale detail on a lowered flag must
 *  say nothing.
 *
 *  AND NOTHING OVER A LIVE RUN. This state is POLLED (every 20 s, App.tsx and
 *  NextApp.tsx) while the engine's state arrives on the socket at once, and
 *  the ladder lowers its flag before it makes its own start. So a live run
 *  beside `recovering: true` is always the previous poll, never the rig: for
 *  up to 20 s after the ladder handed over, the line would have claimed a
 *  re-centring over a run that was already shooting. The live states are
 *  `lib/lastSessionFrame.ts` `runIsLive`'s four. */
export function resumeRecoveryLine(
  arm: ResumeArmState | null | undefined,
  engineState: SequenceState["state"] | undefined,
): string | null {
  if (!arm?.recovering || !arm.recovery) return null;
  if (engineState === "running" || engineState === "paused"
    || engineState === "holding" || engineState === "aborting") return null;
  const { step, session_id: id, session_name: name } = arm.recovery;
  return `Auto-resume is re-centring the mount for ${name || id} (${step})`;
}

/** Per-filter files index with per-frame grades (S5).
 *
 *  view.preview, not control.mount: the person who needs to know which subs
 *  were kept is the one watching the run. Rows carry the ledger frame id, so
 *  `patchFrame` above regrades straight off this list. */
export const getSessionFiles = (id: string): Promise<SessionFilesIndex> =>
  api.get<SessionFilesIndex>(`/api/sessions/${id}/files`);

/** The same index for whichever session a run is writing to right now.
 *  404s (ApiError) when no run is active -- that is the answer, not a fault. */
export const getCurrentSessionFiles = (): Promise<SessionFilesIndex> =>
  api.get<SessionFilesIndex>("/api/sessions/current/files");
