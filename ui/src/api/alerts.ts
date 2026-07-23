// api/alerts.ts — typed client for the alert-sink CRUD/test/health surface
// (PRO-9). Thin over the shared `api` fetch wrapper (api.ts): same ApiError
// throwing. token is write-only (AlertSinkInput) — the server always blanks
// it outbound (see redacted()/list_alerts, token_configured marker).
import { api } from "../api";
import type { AlertSink, AlertSinkInput, AlertHealth } from "../types";

/** GET /api/alerts → configured sinks (view.status). Tokens blanked;
 *  `token_configured` tells a configured secret apart from an empty one. */
export const listAlerts = (): Promise<AlertSink[]> => api.get<AlertSink[]>("/api/alerts");

/** POST /api/alerts → upsert one sink by id (config.alerts). An empty `token`
 *  on update means "unchanged" — never send a blank string to wipe a stored
 *  secret. Returns the full sink list (tokens blanked). */
export const upsertAlert = (sink: AlertSinkInput): Promise<AlertSink[]> =>
  api.post<AlertSink[]>("/api/alerts", sink);

/** DELETE /api/alerts/{id} → {deleted:id}. config.alerts. */
export const deleteAlert = (id: string): Promise<{ deleted: string }> =>
  api.del<{ deleted: string }>(`/api/alerts/${encodeURIComponent(id)}`);

/** POST /api/alerts/{id}/test → a real round-trip test (config.alerts). Sets
 *  `verified` server-side only on a genuine 2xx. */
export const testAlert = (id: string): Promise<{ ok: boolean; error?: string; verified: boolean }> =>
  api.post(`/api/alerts/${encodeURIComponent(id)}/test`, {});

/** GET /api/alerts/health → dispatcher runtime state (view.status): retry-queue
 *  depth + dead-man's-switch state. Pure read; never does I/O server-side. */
export const getAlertHealth = (): Promise<AlertHealth> => api.get<AlertHealth>("/api/alerts/health");
