// api/sessions.ts — typed wrappers for the multi-night session routes
// (sessions spec §6). Cookie auth is automatic; ApiError on non-2xx.
import { api } from "../api";
import type { SequencePlan, Session, SessionFilesIndex, SessionFrame, SessionRow } from "../types";

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

export const listSessions = (): Promise<SessionRow[]> =>
  api.get<{ sessions: SessionRow[] }>("/api/sessions").then((r) => r.sessions);

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

export const deleteSession = (id: string): Promise<{ deleted: string }> =>
  api.del<{ deleted: string }>(`/api/sessions/${id}`);

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
  } | null;
}

export const getResumeArm = (): Promise<ResumeArmState> =>
  api.get<ResumeArmState>("/api/sequence/resume-arm");

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
