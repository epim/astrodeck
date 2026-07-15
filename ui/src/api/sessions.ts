// api/sessions.ts — typed wrappers for the multi-night session routes
// (sessions spec §6). Cookie auth is automatic; ApiError on non-2xx.
import { api } from "../api";
import type { SequencePlan, Session, SessionFrame, SessionRow } from "../types";

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
