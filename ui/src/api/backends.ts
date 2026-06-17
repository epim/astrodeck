// api/backends.ts — typed client functions for the pluggable-backend / profile /
// RBAC surfaces (W1.C / W1.6 / W2.x). A thin module over the shared `api` fetch
// wrapper (api.ts): no new data-fetching lib, same ApiError throwing + per-path
// timeout behavior. Each function maps 1:1 to a verified server route; the
// `_spawn_connect`/`_spawn` routes return `{started}` immediately, so the default
// 15s budget in api.ts is correct for activate/apply (only the legacy synchronous
// /api/connect/{nina,alpaca,phd2} paths get the long budget).

import { api } from "../api";
import type {
  BackendInfo,
  ConnectRigResult,
  Principal,
  Profile,
  ProfileRow,
  RigSpec,
} from "../types";

// ----------------------------------------------------------------- backends
/** GET /api/backends → every registered backend, ordered by name. */
export const listBackends = (): Promise<BackendInfo[]> =>
  api.get<BackendInfo[]>("/api/backends");

/** GET /api/discover/{backend} → that backend's discover() payload (shape varies
 *  per backend; unknown backend → ApiError 404). Returns `unknown` — the caller
 *  narrows per the chosen backend. */
export const discoverBackend = (name: string): Promise<unknown> =>
  api.get<unknown>(`/api/discover/${encodeURIComponent(name)}`);

// ------------------------------------------------------------------ profiles
/** GET /api/profiles → lightweight rows for the picker (active flagged). */
export const listProfiles = (): Promise<ProfileRow[]> =>
  api.get<ProfileRow[]>("/api/profiles");

/** GET /api/profiles/{id} → the full Profile (404 → ApiError). */
export const getProfile = (id: string): Promise<Profile> =>
  api.get<Profile>(`/api/profiles/${encodeURIComponent(id)}`);

/** POST /api/profiles → upsert; the NEW id is server-minted (a client id is only
 *  honored as an upsert when a file for it already exists). Returns the row. */
export const saveProfile = (p: Profile): Promise<ProfileRow> =>
  api.post<ProfileRow>("/api/profiles", p);

/** POST /api/profiles/capture → snapshot the live rig into a new profile
 *  (ApiError 409 {code:"running"-ish} / "connect a rig first" when no rig). */
export const captureProfile = (name: string): Promise<ProfileRow> =>
  api.post<ProfileRow>("/api/profiles/capture", { name });

/** PATCH /api/profiles/{id} → rename in place (id/filename never change). */
export const renameProfile = (id: string, name: string): Promise<ProfileRow> =>
  api.patch<ProfileRow>(`/api/profiles/${encodeURIComponent(id)}`, { name });

/** DELETE /api/profiles/{id} → {deleted:id}. */
export const deleteProfile = (id: string): Promise<{ deleted: string }> =>
  api.del<{ deleted: string }>(`/api/profiles/${encodeURIComponent(id)}`);

/** POST /api/profiles/{id}/activate → set active AND connect (W1.6). Returns
 *  {started:"profile"} immediately; the per-role result streams over the WS
 *  (backend_links updates on the next `status`). 404 unknown id; ApiError 409
 *  {code:"running"} when busy and `force` is not set. */
export const activateProfile = (
  id: string,
  force = false,
): Promise<{ started: string }> =>
  api.post<{ started: string }>(
    `/api/profiles/${encodeURIComponent(id)}/activate`,
    { force },
  );

/** POST /api/profiles/{id}/apply → legacy apply (connect without changing the
 *  active pointer semantics of activate). Same {started}/409 contract. */
export const applyProfile = (
  id: string,
  force = false,
): Promise<{ started: string }> =>
  api.post<{ started: string }>(
    `/api/profiles/${encodeURIComponent(id)}/apply`,
    { force },
  );

// --------------------------------------------------------------- ad-hoc rig
/** POST /api/connect/rig → connect a whole rig by RigSpec (the picker's connect
 *  path). Resolves with {summary, results, backend_links}; map each RoleResult
 *  back onto its picker row by `role` for inline errors. ApiError 422 when
 *  `primary` is unknown or an explicit override can't fill its role; 502 on a
 *  connection failure. */
export const connectRig = (spec: RigSpec): Promise<ConnectRigResult> =>
  api.post<ConnectRigResult>("/api/connect/rig", spec);

// ------------------------------------------------------------ principal / me
/** GET /api/me → the genuinely-resolved caller identity {role, email, caps}.
 *  FAIL-CLOSED server-side: 401 on any resolution failure (the store's
 *  loadPrincipal maps that to a viewer sentinel). Under provider="none" this is
 *  admin + ALL caps, so the default LAN UI is unchanged. */
export const getMe = (): Promise<Principal> => api.get<Principal>("/api/me");

/** Sign-out: POST /auth/logout clears the HttpOnly cookie + revokes the jti.
 *  Re-fetch /api/me after this. Sign-IN is a browser redirect, NOT a fetch
 *  (window.location.href = "/auth/login"), so it is intentionally not here. */
export const logout = (): Promise<unknown> => api.post<unknown>("/auth/logout");
