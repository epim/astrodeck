// api/backends.ts — typed client functions for the pluggable-backend / profile /
// RBAC surfaces (W1.C / W1.6 / W2.x). A thin module over the shared `api` fetch
// wrapper (api.ts): no new data-fetching lib, same ApiError throwing + per-path
// timeout behavior. Each function maps 1:1 to a verified server route; the
// `_spawn_connect`/`_spawn` routes return `{started}` immediately, so the default
// 15s budget in api.ts is correct for activate/apply (only the legacy synchronous
// /api/connect/{nina,alpaca,phd2} paths get the long budget).

import { api } from "../api";
import type {
  AppConfig,
  AuthMethods,
  AuthState,
  BackendInfo,
  ConnectRigResult,
  Principal,
  PrincipalRole,
  Profile,
  ProfileRow,
  ProvidersConfig,
  RigSpec,
  SafetyConfig,
  UpdateConfig,
  UpdateStatus,
  User,
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

// ------------------------------------------------------ local auth + setup
/** GET /api/auth/methods → the UNAUTHENTICATED "what login UI do I show?" signal
 *  (open, leaks no secret). The Login screen reads this before any session
 *  exists. `methods == []` ⇒ no login at all (open LAN). */
export const getAuthMethods = (): Promise<AuthMethods> =>
  api.get<AuthMethods>("/api/auth/methods");

/** POST /auth/local {username,password} → verify + mint the ad_session cookie.
 *  200 {role,email} on success; generic 401 on any failure (no enumeration
 *  oracle); 404 when local auth is not enabled. After this, re-fetch /api/me. */
export const localLogin = (
  username: string,
  password: string,
): Promise<{ role: PrincipalRole; email: string | null }> =>
  api.post<{ role: PrincipalRole; email: string | null }>("/auth/local", {
    username,
    password,
  });

/** POST /auth/setup/local {username,password,email?} → FIRST-RUN create the first
 *  admin (and log in). 409 once any user exists (auto-closed); 404 when local is
 *  off / the first-run flag is off; 422 too-long password; 400 blank/dup. */
export const setupLocalAdmin = (body: {
  username: string;
  password: string;
  email?: string | null;
}): Promise<User & { role: PrincipalRole }> =>
  api.post<User & { role: PrincipalRole }>("/auth/setup/local", body);

// ----------------------------------------------------- user management (admin)
/** GET /api/users → {users:[to_public()]}. All user routes need admin.users. */
export const listUsers = (): Promise<User[]> =>
  api.get<{ users: User[] }>("/api/users").then((r) => r.users);

/** POST /api/users → create a user (201). 409 dup username, 422 too-long pw,
 *  400 unknown role. Returns to_public(). */
export const createUser = (body: {
  username: string;
  password: string;
  role: PrincipalRole;
  email?: string | null;
  enabled?: boolean;
}): Promise<User> => api.post<User>("/api/users", body);

/** PATCH /api/users/{id} → set role / enabled / username / email (only the
 *  provided fields). 409 "last admin" or dup; 400 unknown role; 404 unknown. */
export const patchUser = (
  id: string,
  patch: {
    role?: PrincipalRole;
    enabled?: boolean;
    username?: string;
    email?: string | null;
  },
): Promise<User> => api.patch<User>(`/api/users/${encodeURIComponent(id)}`, patch);

/** POST /api/users/{id}/password → reset a password. 404 unknown, 422 too-long. */
export const resetUserPassword = (id: string, password: string): Promise<User> =>
  api.post<User>(`/api/users/${encodeURIComponent(id)}/password`, { password });

/** DELETE /api/users/{id} → {ok:true}. 404 unknown, 409 last admin. */
export const deleteUser = (id: string): Promise<{ ok: boolean }> =>
  api.del<{ ok: boolean }>(`/api/users/${encodeURIComponent(id)}`);

// --------------------------------------------------- auth-method config (admin)
/** POST /api/auth/config → persist a new AuthConfig (admin.users-gated). Returns
 *  the REDACTED auth block. We send a partial that the server merges/validates;
 *  the toggle panel sends the full set of fields it owns. */
export const setAuthConfig = (auth: Partial<AuthState> & Record<string, unknown>):
  Promise<AuthState> => api.post<AuthState>("/api/auth/config", auth);

// ------------------------------------------------------ safety config (W1.10)
/** POST /api/config {safety} → persist the WHOLE safety block (the server's
 *  set_safety REPLACES SafetyConfig, so the caller MUST echo the full current
 *  block with its edits applied — exactly like the auth panel). Requires
 *  config.safety; touching solar_avoidance/solar_exclusion_deg ALSO requires
 *  config.solar_override (server field-level rule). Returns the merged AppConfig.
 *  A 403 {code:"forbidden"} surfaces when the override cap is missing. */
export const setSafetyConfig = (safety: SafetyConfig): Promise<AppConfig> =>
  api.post<AppConfig>("/api/config", { safety });

// ----------------------------------------------------------------- self-update
/** GET /api/update/status → live snapshot + can_apply/supervised/blocked reason. */
export const getUpdateStatus = (): Promise<UpdateStatus> =>
  api.get<UpdateStatus>("/api/update/status");

/** POST /api/update/check → force a GitHub poll (system.update). */
export const checkUpdate = (): Promise<UpdateStatus> =>
  api.post<UpdateStatus>("/api/update/check");

/** POST /api/update/apply → start the download/verify/stage/restart pipeline
 *  (system.update + rig-idle safety gate). Returns {started} immediately; phases
 *  stream over the `update` WS event and the scope restarts via the supervisor. */
export const applyUpdate = (): Promise<{ started: string }> =>
  api.post<{ started: string }>("/api/update/apply");

/** POST /api/update/config → persist the UpdateConfig block (system.update). */
export const setUpdateConfig = (cfg: UpdateConfig): Promise<AppConfig> =>
  api.post<AppConfig>("/api/update/config", cfg);

// ------------------------------------------------------- capability providers
/** POST /api/config/providers → persist the ProvidersConfig block (native
 *  parity — Settings → Connect "Capabilities" card override dropdowns).
 *  config.backend-gated, same cap as the rest of the backend/connect surface.
 *  Returns the full merged AppConfig (mirrors setSafetyConfig/setUpdateConfig). */
export const setProvidersConfig = (cfg: ProvidersConfig): Promise<AppConfig> =>
  api.post<AppConfig>("/api/config/providers", cfg);
