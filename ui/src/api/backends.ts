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
  DriverEntry,
  DriverInfo,
  DriversResponse,
  NamingConfig,
  PackStatus,
  Principal,
  PrincipalRole,
  Profile,
  ProfileRow,
  ProvidersConfig,
  RigSpec,
  RotatorConfig,
  SafetyConfig,
  EscalationConfig,
  CalibrationConfig,
  SurveyConfig,
  UpdateConfig,
  UpdateStatus,
  User,
  WcsStampConfig,
} from "../types";
import type { DiscoveredHardware } from "../components/settings/backendMeta";

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

/** POST /api/profiles with parsed import JSON (F7 #5b). Same upsert-by-id rule
 *  as `saveProfile` (app.py::save_profile): a client-carried id only wins as an
 *  upsert when a profile with that id already exists on THIS server, otherwise
 *  the server mints a fresh uuid — so importing a stranger's export always
 *  creates a new record. Body is `unknown`-shaped (lib/profileFile.ts only
 *  screens the obvious non-files); the server's Profile model is the real
 *  validator. */
export const importProfile = (raw: Record<string, unknown>): Promise<ProfileRow> =>
  api.post<ProfileRow>("/api/profiles", raw);

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

/** POST /auth/token {token} → exchange the break-glass admin token for the
 *  ad_session cookie (a normal admin session). The token is sent in the body
 *  (never a URL) and NOT persisted client-side. 200 {role,email} on success;
 *  generic 401 on a bad token; 404 when no admin token is configured. */
export const tokenLogin = (
  token: string,
): Promise<{ role: PrincipalRole; email: string | null }> =>
  api.post<{ role: PrincipalRole; email: string | null }>("/auth/token", { token });

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

/** POST /api/config {escalation} → persist the WHOLE escalation block. Same
 *  wholesale-replace contract as setSafetyConfig, but gated on config.alerts
 *  (the server files recovery/notification policy under the alerts cap). */
export const setEscalationConfig = (escalation: EscalationConfig):
  Promise<AppConfig> => api.post<AppConfig>("/api/config", { escalation });

// ------------------------------------------------------ dome / roof (PRO-4)
import type { DomeShutter } from "../lib/dome";

export interface DomeState {
  connected: boolean;
  shutter: DomeShutter;
  requires_park_before_close: boolean;
  can_slave: boolean;
}

/** GET /api/dome/state → the roof shutter status + capabilities. Honest defaults
 *  (connected:false, shutter:"unknown") when no dome is connected. */
export const getDomeState = (): Promise<DomeState> =>
  api.get<DomeState>("/api/dome/state");

/** POST /api/dome/close → the tested park-and-close ordering guard: fence gotos,
 *  park under the motion lock, THEN close the roof. Requires control.mount (the
 *  close moves the mount). Returns the spawned-task marker. */
export const closeDome = (): Promise<{ started: string }> =>
  api.post<{ started: string }>("/api/dome/close");

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

// ------------------------------------------------------------ rotator config
/** POST /api/config/rotator → persist the rotator range-of-motion + tolerance
 *  (CAA spec §3.2). config.backend-gated; 422 on invalid values. */
export const setRotatorConfig = (cfg: RotatorConfig): Promise<AppConfig> =>
  api.post<AppConfig>("/api/config/rotator", cfg);

// ------------------------------------------------------------ backend drivers
/** GET /api/drivers → configured + implicit drivers with probe status + offers
 *  (spec 2026-07-08 §3.2). Never 500s: failures land in each row's status. */
export const listDrivers = (): Promise<DriversResponse> =>
  api.get<DriversResponse>("/api/drivers");

/** POST /api/drivers/{id}/probe → force ONE driver's re-probe (bypass the 15s
 *  TTL); returns the full refreshed DriversResponse. 404 unknown id. */
export const probeDriver = (id: string): Promise<DriversResponse> =>
  api.post<DriversResponse>(`/api/drivers/${encodeURIComponent(id)}/probe`);

/** POST /api/config/drivers → create (server mints the id; port defaults per
 *  type). 422 unknown type / blank host. config.backend-gated.
 *
 *  `type` is any server-registry driver_type (widened from the old
 *  nina|alpaca|phd2 union — native-hardware on-ramp 2026-07-21, spec
 *  2026-07-21-native-hardware-onramp.md task 2), so the 5 native hardware
 *  driver_types (zwo-am5, wanderer-snowflake, zwo-usb, zwo-asi, player-one)
 *  can be added too. `transport`/`port_path` address serial/local drivers
 *  (mirrors DriverCreateBody: `host`/`port` stay network-only and are simply
 *  omitted for serial/local — the server model defaults them to ""/0). */
export const addDriver = (body: {
  type: string;
  host?: string;
  port?: number;
  transport?: "network" | "serial" | "local";
  port_path?: string;
  label?: string;
  extra?: Record<string, unknown>;
}): Promise<{ driver: DriverEntry }> =>
  api.post<{ driver: DriverEntry }>("/api/config/drivers", body);

/** PATCH /api/config/drivers/{id} → patch host/port/enabled/label/extra/
 *  port_path/transport (id/type immutable). `port_path` lets a moved COM
 *  port be fixed in place (server config.py update_driver "B follow-up C")
 *  without delete+recreate — the DriversPanel "Edit port" control uses this.
 *  404 unknown, 422 invalid. */
export const updateDriver = (
  id: string,
  patch: Partial<Pick<DriverEntry, "host" | "port" | "enabled" | "label" | "extra">> & {
    port_path?: string;
    transport?: string;
  },
): Promise<{ driver: DriverEntry }> =>
  api.patch<{ driver: DriverEntry }>(
    `/api/config/drivers/${encodeURIComponent(id)}`, patch);

/** DELETE /api/config/drivers/{id} → {deleted:id}. 404 unknown. */
export const deleteDriver = (id: string): Promise<{ deleted: string }> =>
  api.del<{ deleted: string }>(`/api/config/drivers/${encodeURIComponent(id)}`);

// -------------------------------------------------- native hardware scanning
// One physical device found by a hardware backend's discover(), GROUPED by
// (driver_type, port_path, index) — a local/USB backend's discover() emits one
// entry PER ROLE it can fill (zwo-usb: "focuser" for the EAF, "rotator" for the
// CAA; a camera: both "camera" and "guide_camera" for the one unit). One
// configured driver row addresses the whole bus, so grouping avoids N redundant
// rows for one connection. `index` (zwo-asi/player-one cameras only) tells two
// identical USB cameras apart.
//
// Grouping merges roles but must NOT merge identities: the server now offers
// exactly the roles discovery reported, named per unit, so this row keeps the
// per-unit names in `devices` and only borrows the backend's name when it is
// genuinely covering more than one device.
export type HwFound = {
  driver_type: string;
  transport: "network" | "serial" | "local";
  /** What to CALL this row: the unit's own name when the group is one device,
   *  the backend's name when it is a bus carrying several. */
  name: string;
  /** The backend's own name, kept so the row can be renamed after grouping. */
  backend_label: string;
  /** Every distinct unit in this group — the EAF and the CAA on one ZWO bus. */
  devices: string[];
  port_path?: string;
  index?: number;
  roles: string[];
};

/** Scan EVERY registered hardware backend (zwo-am5, wanderer-snowflake,
 *  zwo-usb, zwo-asi, player-one today; a future plugin backend needs zero
 *  client changes as long as it sets `hardware`+`discoverable`+`driver_type`
 *  in the registry) and group the results into one row per physical unit.
 *  ONE implementation shared by DriversPanel's "Scan for USB/serial
 *  hardware" and EquipmentView's "Detect hardware rig" (native-hardware
 *  follow-ups 2026-07-21). One backend's discover() failing (missing DLL, no
 *  serial lib, ...) must not blank the whole scan, so each call is caught
 *  independently. */
export async function discoverHardware(): Promise<HwFound[]> {
  const backends: BackendInfo[] = await listBackends();
  const hw = backends.filter((b) => b.hardware && b.discoverable && b.driver_type);
  const perBackend = await Promise.all(
    hw.map(async (b) => {
      try {
        const entries = (await discoverBackend(b.name)) as DiscoveredHardware[];
        return { b, entries: Array.isArray(entries) ? entries : [] };
      } catch {
        return { b, entries: [] as DiscoveredHardware[] };
      }
    }),
  );
  const grouped = new Map<string, HwFound>();
  for (const { b, entries } of perBackend) {
    for (const e of entries) {
      const key = `${b.driver_type}::${e.port_path ?? ""}::${e.index ?? ""}`;
      const row = grouped.get(key);
      if (row) {
        if (!row.roles.includes(e.role)) row.roles.push(e.role);
        if (e.name && !row.devices.includes(e.name)) row.devices.push(e.name);
      } else {
        grouped.set(key, {
          driver_type: b.driver_type as string,
          transport: (b.transport as "network" | "serial" | "local") ?? "local",
          name: e.name,
          backend_label: b.label,
          devices: e.name ? [e.name] : [],
          port_path: e.port_path,
          index: e.index,
          roles: [e.role],
        });
      }
    }
  }
  // A group holding SEVERAL distinct devices is a bus, not a device, so it takes
  // the backend's name. Naming it after whichever unit enumerated first is what
  // labelled the ZWO accessory bus "ZWO EAF (USB)" — one row, named for its
  // focuser, with the CAA hidden inside it, so the rotator read as unsupported
  // and users went to ASCOM for a device we drive natively (#97). A camera
  // reporting two ROLES is still one device, so it keeps its own name.
  for (const row of grouped.values()) {
    if (row.devices.length > 1) row.name = row.backend_label;
  }
  return Array.from(grouped.values());
}

/** Already configured iff a non-implicit driver of the same type — and, when
 *  distinguishable, the same UNIT — exists. Serial units are told apart by
 *  `port_path`; USB cameras (zwo-asi/player-one) by `index`; anything else
 *  (e.g. zwo-usb, one row per accessory bus) falls back to type-only (a
 *  second physical unit can't be told apart and is conservatively treated as
 *  already configured — same gap the plan documents for that case). */
export function hwAlreadyConfigured(f: HwFound, drivers: DriverInfo[]): boolean {
  return drivers.some((d) => {
    if (d.implicit || d.type !== f.driver_type) return false;
    if (f.transport === "serial") return (d.port_path ?? "") === (f.port_path ?? "");
    if (f.index !== undefined) return d.index === f.index;
    return true;
  });
}

/** POST /api/config/drivers for one scanned hardware unit — the ONE place
 *  that turns a discovered device into an addDriver() call (the index rides
 *  as `extra.index` for USB cameras), shared by DriversPanel's per-row Add
 *  and EquipmentView's "Detect hardware rig". */
export const addDriverForHardware = (f: HwFound): Promise<{ driver: DriverEntry }> =>
  addDriver({
    type: f.driver_type,
    transport: f.transport,
    port_path: f.port_path,
    label: f.name,
    ...(f.index !== undefined ? { extra: { index: f.index } } : {}),
  });

// -------------------------------------------------- offline survey pack (spec 2026-07-13)
/** POST /api/config/survey → config payload. config.site_optics. */
export const setSurveyConfig = (survey: SurveyConfig): Promise<AppConfig> =>
  api.post<AppConfig>("/api/config/survey", survey);

/** GET /api/survey/pack → offline pack status + fetch progress. view.status. */
export const getPackStatus = (): Promise<PackStatus> =>
  api.get<PackStatus>("/api/survey/pack");

// -------------------------------------------------------- file naming (PRO-11)
/** POST /api/config/calibration → config payload. config.site_optics.
 *  422 when the temp bin is narrower than the temp match tolerance (a relational
 *  rule pydantic can't express, so the server checks it at write time). */
export const setCalibrationConfig = (calibration: CalibrationConfig):
  Promise<AppConfig> => api.post<AppConfig>("/api/config/calibration", calibration);

/** POST /api/config/naming → config payload. config.site_optics. */
export const setNamingConfig = (naming: NamingConfig): Promise<AppConfig> =>
  api.post<AppConfig>("/api/config/naming", naming);

// ------------------------------------------------- per-frame WCS (per-frame-wcs)
/** POST /api/config/wcs → config payload. config.site_optics.
 *  Master enable + advanced block travel together (one atomic version bump). */
export const setWcsStampConfig = (
  body: { solve_saved_lights: boolean; wcs_stamp: WcsStampConfig },
): Promise<AppConfig> => api.post<AppConfig>("/api/config/wcs", body);

/** POST /api/survey/pack/fetch → 202 {started} | 200 {already} | 507 no space. config.site_optics. */
export const startPackFetch = (order = 4): Promise<{ started: boolean; already?: boolean }> =>
  api.post<{ started: boolean; already?: boolean }>("/api/survey/pack/fetch", { order });

/** DELETE /api/survey/pack → {deleted}. 409 while a fetch runs. config.site_optics. */
export const deletePack = (): Promise<{ deleted: boolean }> =>
  api.del<{ deleted: boolean }>("/api/survey/pack");
