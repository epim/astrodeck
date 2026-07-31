// equipment.ts — the pure assignment model behind the Equipment tab (spec
// §4.1). No React, no fetch: the ONE RULE (a driver is offered on a row only
// if enabled + reachable + actually offering that role), sticky-assignment
// state, the AssignmentMap → RigSpec compiler (primary "none": explicit-only
// rigs), and localStorage persistence. Kept pure so the tsx harness tests it
// without a DOM.
import type {
  ConnSpec,
  DriverDeviceOffer,
  DriverInfo,
  RigSpec,
} from "../types";

export interface Assignment {
  driverId: string;
  devType?: string; // alpaca device selector (from the picked offer)
  devNum?: number;
  name?: string; // display name from the offer (folded into ConnSpec.extra)
}

// role -> assignment (null/absent = unassigned). Keyed by the server-fed role
// list, so a future role (rotator) needs zero changes here.
export type AssignmentMap = Record<string, Assignment | null>;

export type SlotState =
  | "unassigned"
  | "ok"
  | "driver-removed"
  | "driver-unreachable"
  | "driver-disabled"
  | "device-missing";

// Mirrors server drivers._DRIVER_TYPE_TO_BACKEND — the Alpaca lane's registry
// name is "native"; sim is its own backend for the one-tap sim rig.
export const DRIVER_TYPE_TO_BACKEND: Record<string, string> = {
  nina: "nina",
  alpaca: "native",
  phd2: "phd2",
  sim: "sim",
  // ZWO ASIAIR bridge — registry name matches the driver type (server:
  // AsiairBackend.name / .driver_type). Present in this map only so
  // buildRigSpec's placeholder backend is right; the server re-derives it
  // authoritatively in resolve_driver_ids either way.
  asiair: "asiair",
};

/** THE ONE RULE (spec §2): a driver appears as an option on a role's row only
 *  when it is enabled, reachable, and its probe actually offers that role. */
export function eligibleDrivers(role: string, drivers: DriverInfo[]): DriverInfo[] {
  return drivers.filter(
    (d) =>
      d.enabled &&
      d.status.reachable &&
      d.offers.devices.some((o) => o.role === role),
  );
}

/** The concrete enumerated devices a driver offers for a role (alpaca may
 *  have several; nina/phd2/sim at most one). */
export function deviceChoices(role: string, driver: DriverInfo): DriverDeviceOffer[] {
  return driver.offers.devices.filter((o) => o.role === role);
}

/** Sim-connect desync root-cause fix (spec review EQ-01, ×3 rounds): assign the
 *  implicit "sim" driver to every given role, in the EXACT shape RoleSlot's
 *  pickDriver("sim") would produce by hand. `▶ Simulator rig` builds this map
 *  and then drives it through the SAME connect-rig flow Connect Rig uses, so
 *  Devices (dropdowns), Connect Rig (assignedCount), and Link Status
 *  (backend_links, only populated by the RigSpec connect path) all read one
 *  AssignmentMap instead of three disjoint sources of truth. A role the sim
 *  driver doesn't offer (shouldn't happen -- it offers every ROLE) degrades to
 *  a bare `{driverId: "sim"}`, same as pickDriver does when the driver row is
 *  missing. */
export function simAssignments(roles: string[], drivers: DriverInfo[]): AssignmentMap {
  const sim = drivers.find((d) => d.id === "sim");
  const map: AssignmentMap = {};
  for (const role of roles) {
    const offer = sim ? deviceChoices(role, sim)[0] : undefined;
    map[role] = { driverId: "sim", devType: offer?.dev_type, devNum: offer?.dev_num, name: offer?.name };
  }
  return map;
}

/** The three task slots the Equipment Tasks section renders (spec §4.1). */
export type TaskCap = "autofocus" | "polar_align" | "solve";
export const TASK_CAPS: { cap: TaskCap; label: string }[] = [
  { cap: "autofocus", label: "Autofocus" },
  { cap: "polar_align", label: "Polar align" },
  { cap: "solve", label: "Plate solve" },
];

/** THE ONE RULE, task edition (spec §4.1): a driver appears in a task row's
 *  dropdown only when it is enabled, reachable, and its probe actually offers
 *  that task. */
export function eligibleTaskDrivers(cap: TaskCap, drivers: DriverInfo[]): DriverInfo[] {
  return drivers.filter(
    (d) => d.enabled && d.status.reachable && d.offers.tasks.includes(cap),
  );
}

/** Sticky-assignment state (spec §5, amended post-review): the user's choice
 *  persists through a driver outage and re-lights when it returns; a deleted
 *  driver reads "driver-removed" and never silently reconnects elsewhere.
 *  ``role`` is the AssignmentMap key (Assignment itself carries no role) --
 *  needed to check the driver's CURRENT offers, not just its reachability. */
export function slotState(
  role: string,
  a: Assignment | null,
  drivers: DriverInfo[],
): SlotState {
  if (!a) return "unassigned";
  const d = drivers.find((x) => x.id === a.driverId);
  if (!d) return "driver-removed";
  if (!d.enabled) return "driver-disabled";
  if (!d.status.reachable) return "driver-unreachable";
  // Honesty (post-review): an enabled + reachable driver whose LIVE probe no
  // longer offers the assigned role (e.g. a camera unplugged mid-session)
  // must not read "ok" -- the row would lie. Match at the granularity the
  // assignment actually pins: a devNum-addressed pick (Alpaca) must find
  // that EXACT device still enumerated; role-level presence is the floor
  // (nina/phd2/sim offers carry no dev_num to pin against).
  const stillOffered = d.offers.devices.some((o) => {
    if (o.role !== role) return false;
    if (a.devNum !== undefined) return o.dev_type === a.devType && o.dev_num === a.devNum;
    return true;
  });
  if (!stillOffered) return "device-missing";
  return "ok";
}

/** Compile assignments → RigSpec. primary "none" = explicit-only: the server
 *  requests exactly the assigned roles, nothing primary-derived. The sim
 *  built-in compiles to backend "sim" directly (no driver_id — sim is
 *  implicit); configured drivers ride their driver_id and let the SERVER
 *  resolve host/port at connect time (never client-synthesized addressing).
 *  NOTE on backend derivation below: the driver id prefix (nina-/alpaca-/
 *  phd2-) IS the driver type by construction (add_driver mints
 *  <type>-<4hex>), and the server re-derives the backend authoritatively in
 *  resolve_driver_ids anyway — the client value here is a placeholder that
 *  keeps ConnSpec.backend non-empty. */
export function buildRigSpec(assignments: AssignmentMap): RigSpec {
  const roles: Record<string, ConnSpec> = {};
  for (const [role, a] of Object.entries(assignments)) {
    if (!a) continue;
    const extra: Record<string, unknown> = {};
    if (a.name) extra.name = a.name;
    if (a.driverId === "sim") {
      roles[role] = { backend: "sim", role, ...(a.name ? { extra } : {}) };
      continue;
    }
    if (a.driverId === "ascom-local") {
      // Implicit backend (like sim): the server-owned COM host resolves the
      // endpoint, so no driver_id / host / port is sent — just the picked
      // device addressing. The server opens AscomLocalBackend and injects the
      // loopback port.
      const spec: ConnSpec = { backend: "ascom-local", role };
      if (a.devType) spec.dev_type = a.devType;
      if (a.devNum !== undefined) spec.dev_num = a.devNum;
      if (a.name) spec.extra = extra;
      roles[role] = spec;
      continue;
    }
    const spec: ConnSpec = {
      backend: DRIVER_TYPE_TO_BACKEND[a.driverId.split("-")[0]] ?? "native",
      role,
      driver_id: a.driverId,
    };
    if (a.devType) spec.dev_type = a.devType;
    if (a.devNum !== undefined) spec.dev_num = a.devNum;
    if (a.name) spec.extra = extra;
    roles[role] = spec;
  }
  return { primary: "none", roles };
}

/** Hold-to-confirm gate: true when a real (non-sim) driver fills a motion
 *  role — mirrors the retired connect picker's MOTION_ROLES rule. */
export function hasRealMotion(assignments: AssignmentMap, drivers: DriverInfo[]): boolean {
  return ["telescope", "focuser", "rotator"].some((role) => {
    const a = assignments[role];
    if (!a || a.driverId === "sim") return false;
    return slotState(role, a, drivers) !== "driver-removed";
  });
}

// ---------------------------------------------------- hardware auto-assign
// "▶ Detect hardware rig" (EquipmentView Rig Actions, native-hardware
// follow-up 2026-07-21): map each role to a SENSIBLE native driver from the
// (post-scan) driver list. Single-purpose native backends map straight to
// their one role; the two camera-capable backends (player-one, zwo-asi) BOTH
// offer both camera roles once configured (drivers.py _probe_native declares
// `roles = ("camera","guide_camera")` on each), so the split here is by
// INTENT (driver type) rather than by offers: player-one is the dedicated
// imaging camera, zwo-asi the dedicated guide camera. Pure — no fetch, no
// state; the caller (EquipmentView) is responsible for adding newly-scanned
// drivers first and re-fetching `drivers` before calling this.
const HARDWARE_SINGLE_ROLE: Record<string, string> = {
  telescope: "zwo-am5",
  focuser: "zwo-usb",
  rotator: "zwo-usb",
  filterwheel: "wanderer-snowflake",
};

function pickAssignment(role: string, d: DriverInfo): Assignment {
  const first = deviceChoices(role, d)[0];
  return { driverId: d.id, devType: first?.dev_type, devNum: first?.dev_num, name: first?.name };
}

export function hardwareAssignments(drivers: DriverInfo[], roles: string[]): AssignmentMap {
  const byType = (t: string): DriverInfo | undefined =>
    drivers.find((d) => d.type === t && d.enabled && d.status.reachable);
  // UX-26: offers-based fallback — the first REAL (non-implicit) driver that
  // offers this role; excludes the sim built-in (a hardware-detect must never
  // seed the simulator, symmetric with the camera rule below).
  const byOffer = (role: string): DriverInfo | undefined =>
    eligibleDrivers(role, drivers).find((c) => !c.implicit);
  const map: AssignmentMap = {};
  for (const role of roles) {
    if (role === "camera") {
      // player-one first (dedicated imaging camera); else any eligible
      // NON-IMPLICIT driver (zwo-asi / NINA / Alpaca). Exclude the built-ins:
      // a "Detect hardware rig" action must never silently seed the SIMULATOR
      // camera (implicit sim is always enabled+reachable+offers every role), so
      // with no real camera driver the slot is left unassigned -- symmetric with
      // guide_camera below.
      const d = byType("player-one") ?? eligibleDrivers("camera", drivers).find((c) => !c.implicit);
      if (d) map[role] = pickAssignment(role, d);
      continue;
    }
    if (role === "guide_camera") {
      // zwo-asi only — no generic fallback (spec: "else leave unassigned").
      const d = byType("zwo-asi");
      if (d) map[role] = pickAssignment(role, d);
      continue;
    }
    // UX-26: prefer the intended vendor driver; else ANY real driver that offers
    // this role, so a detected non-allowlisted mount/focuser/rotator/filterwheel
    // is auto-assigned instead of silently left unassigned. (camera/guide_camera
    // keep their intent-based split above — both cameras offer both roles.)
    const want = HARDWARE_SINGLE_ROLE[role];
    const d = (want ? byType(want) : undefined) ?? byOffer(role);
    if (d) map[role] = pickAssignment(role, d);
  }
  return map;
}

// ============================================================ PROFILES
// The decision layer behind "what is a profile made of" and "what does
// activating one cost me" (UX review round 4, S1). It lives here, pure and
// tested, for the same reason lib/profileDelete.ts exists: the judgement was
// wrong in the JSX and nothing pinned it.
//
// THE STRUCTURAL BUG. Two state models were presented as one screen. The rig
// you DRIVE is the server's connected device set; the rig the app could SAVE
// was this browser's AssignmentMap. Profiles were built out of the second one,
// and every persona hit a different edge of that:
//
//   * Connect through the setup guide (or a boot profile, or another browser)
//     and the map is empty, so Save was gated off — with the native `disabled`
//     attribute and no reason — while eleven devices sat there connected.
//   * Save an all-simulator rig and `doSaveProfile` skipped every sim row and
//     wrote `primary_backend: "none"`, i.e. the ONE profile shape that resolves
//     to `RigSpec(primary="none", roles={})`. Activating it ran the teardown
//     that precedes every activate and then connected nothing. MEASURED on a
//     live instance: 10 devices before the tap, 0 after, no confirmation.
//
// So the authority moves: a profile captures the CONNECTED rig (the server's
// own snapshot, POST /api/profiles/capture), and the AssignmentMap is only the
// fallback for a rig that has been PICKED but not yet connected.

/** How many roles are LIVE right now — the ONE count for "how big is this rig",
 *  so no two sentences on one screen can quote different sizes for it (the
 *  Disconnect dialog said "drops 10 connected devices" three inches under "save
 *  the 11 connected devices", because each counted a different thing).
 *
 *  It joins the two surfaces the server publishes, because neither alone is the
 *  whole rig: `status.connected` is the DEVICE map, and `backend_links` is the
 *  per-role live grid — the only one of the two that carries the GUIDER, which
 *  is an engine rather than a device. Both are recomputed per status frame
 *  (hub.backend_links → _role_live_connected), so neither is a stale echo. */
export function liveRoleCount(
  status:
    | {
        connected?: Record<string, { connected?: boolean } | null | undefined> | null;
        backend_links?: { role: string; connected: boolean }[] | null;
      }
    | null
    | undefined,
): number {
  const roles = new Set<string>();
  for (const [role, c] of Object.entries(status?.connected ?? {})) {
    if (c?.connected) roles.add(role);
  }
  for (const l of status?.backend_links ?? []) if (l.connected) roles.add(l.role);
  return roles.size;
}

/** The RigSpec primary the SERVER will derive for this profile.
 *
 *  Mirrors `profiles.py::Profile.to_rigspec` + `_derived_primary` (read
 *  2026-07-28), including the order that matters: a legacy nina_host-only
 *  profile forces "nina" AFTER the explicit primary is read, and an EMPTY
 *  `primary_backend` still resolves to a working rig ("native" with device
 *  rows, else "sim"). Only an explicit "none" asks the server for no roles. */
export function profilePrimary(p: {
  primary_backend?: string | null;
  devices?: { role: string }[] | null;
  nina_host?: string | null;
}): string {
  const devices = p.devices ?? [];
  if (p.nina_host && devices.length === 0) return "nina";
  const explicit = (p.primary_backend ?? "").trim();
  if (explicit) return explicit;
  return devices.length > 0 ? "native" : "sim";
}

/** True when activating this profile connects NOTHING — the teardown that
 *  precedes every activate is the whole of what happens. This is not a
 *  hypothetical: it is the exact shape the old assignments-only Save wrote for
 *  a simulator rig, which is how one tap destroyed a live 11-device rig. */
export function profileConnectsNothing(p: {
  primary_backend?: string | null;
  devices?: { role: string }[] | null;
  nina_host?: string | null;
}): boolean {
  return (p.devices ?? []).length === 0 && profilePrimary(p) === "none";
}

/** Does a full profile resolve a real (non-sim) mount or focuser? The older of
 *  the two activation gates — it asks what the profile will ATTACH, and it is
 *  kept because that is a genuine escalation. It is no longer the only gate:
 *  see `profileActivateConfirm`, which asks the question this one never could
 *  (what the activate DROPS).
 *
 *  Intent (explicit, no dead clauses): prompt when EITHER an explicit
 *  telescope/focuser device row points at a non-sim backend, OR a
 *  NINA-host-only legacy profile (real rig), OR a non-sim primary with no
 *  explicit motion rows — because the server may resolve a real mount/focuser
 *  from that primary. The last case is a deliberate over-prompt (we can't know
 *  what the primary resolves without connecting); we fail SAFE toward asking.
 *
 *  Lived in ProfileList.tsx until EquipmentView's own Activate needed the same
 *  judgement; hoisting it is a move, not a redesign. */
export function profileResolvesRealMotion(p: {
  primary_backend?: string | null;
  devices?: { role: string; backend?: string | null }[] | null;
  nina_host?: string | null;
}): boolean {
  // A profile that asks the server for zero roles cannot attach anything, so
  // the over-prompt below must not fire on it. Without this the confirm dialog
  // printed both halves at once — "stores no devices, so nothing reconnects"
  // immediately followed by "the hardware will attach and may move" — which is
  // the same class of defect the whole review is about, in the dialog written
  // to prevent it. Measured in the rendered dialog before this line existed.
  if (profileConnectsNothing(p)) return false;
  const MOTION = ["telescope", "focuser"];
  const devices = p.devices ?? [];
  const deviceMotion = devices.some(
    (d) => MOTION.includes(d.role) && d.backend !== "sim",
  );
  const ninaRig = !!p.nina_host && devices.length === 0;
  const primaryMayResolveMotion =
    p.primary_backend !== "sim" && !devices.some((d) => MOTION.includes(d.role));
  return deviceMotion || ninaRig || primaryMayResolveMotion;
}

/** Confirm-dialog spec — the subset of `ConfirmOpts` this decision produces.
 *  Structural (not an import) so this module stays React-free. */
export interface ActivateConfirmSpec {
  title: string;
  body: string;
  mode: "confirm" | "hold";
  tone: "danger";
  confirmLabel: string;
  cancelLabel: string;
}

/**
 * Confirm copy + friction for activating a profile, or `null` when activating
 * costs nothing and should just happen.
 *
 * The old gate asked the wrong question. It prompted when the PROFILE resolved
 * a real mount or focuser — so a simulator rig, or an assignments-only profile,
 * correctly skipped the dialog and silently tore down whatever was running.
 * What makes an activate expensive is not only what it attaches: every activate
 * calls `hub._teardown()` FIRST (`hub.py::_connect_rigspec_unlocked`), so the
 * cost is the rig you are DROPPING. That is what this asks about.
 *
 * Three escalations, any of which promotes a tap-confirm to a hold:
 *   - the profile puts nothing back (you end with no rig),
 *   - a sequence is running (the teardown aborts it),
 *   - the profile drives real hardware (it will attach and may move).
 */
export function profileActivateConfirm(opts: {
  name: string;
  /** `profileConnectsNothing(fullProfile)` — pass false when unknown. */
  connectsNothing: boolean;
  /** the profile resolves a real (non-sim) mount or focuser */
  realMotion: boolean;
  /** devices the server reports connected RIGHT NOW */
  liveDevices: number;
  sequenceRunning: boolean;
}): ActivateConfirmSpec | null {
  const { name, connectsNothing, realMotion, liveDevices, sequenceRunning } = opts;
  if (liveDevices === 0 && !realMotion) return null;

  const parts: string[] = [];
  if (liveDevices > 0) {
    parts.push(
      `Activating disconnects the ${liveDevices} device${liveDevices === 1 ? "" : "s"} ` +
        `running now — every activate tears the current rig down first.`,
    );
  }
  if (sequenceRunning) parts.push("A sequence is running, and that teardown aborts it.");
  parts.push(
    connectsNothing
      ? `"${name}" stores no devices, so nothing reconnects: you end up with no rig.`
      : `"${name}" then connects the devices it stores.`,
  );
  if (realMotion) {
    parts.push("It drives a real mount or focuser — the hardware will attach and may move.");
  }
  const hold = connectsNothing || realMotion || sequenceRunning;
  return {
    title: `Activate "${name}"?`,
    body: parts.join(" ") + (hold ? " Hold to confirm." : ""),
    mode: hold ? "hold" : "confirm",
    tone: "danger",
    confirmLabel: connectsNothing ? "Activate anyway" : "Activate & connect",
    cancelLabel: liveDevices > 0 ? "Keep this rig" : "Cancel",
  };
}

/** Where a Save on the Equipment screen takes its contents from.
 *  "connected-rig" = the server's snapshot of what is live (the authority);
 *  "assignments"   = this browser's dropdown picks, for a rig that has been
 *                    chosen but not yet connected. */
export type ProfileSaveSource = "connected-rig" | "assignments";

export function profileSaveSource(live: number, assigned: number): ProfileSaveSource | null {
  if (live > 0) return "connected-rig";
  if (assigned > 0) return "assignments";
  return null;
}

/** How many assignments would actually SURVIVE into a saved profile.
 *
 * The save loop skips simulator rows (`a.driverId === "sim"`) because the
 * simulator is the implicit built-in and carries no persistable `driver_id`.
 * The gate, meanwhile, counted every pick — so eleven simulator picks opened
 * Save and then wrote a profile containing zero devices. The gate was counting
 * a different thing from the save.
 *
 * Real drivers still persist unconnected, so "configure indoors, connect at the
 * scope" keeps working; only the case that cannot produce a device is blocked,
 * and a CONNECTED simulator rig still saves fine through the capture path. */
export function persistableAssignedCount(
  assignments: Record<string, { driverId?: string } | null | undefined>,
): number {
  return Object.values(assignments)
    .filter((a) => !!a && a.driverId !== "sim").length;
}

/**
 * Why Save cannot run, or `null` when it can.
 *
 * Returns the SENTENCE, never a boolean, so no call site can ship the dead grey
 * rectangle this replaces (measured: `disabled=true`, opacity 0.35, no title,
 * no aria-label, no aria-disabled — a designer typed a name, pressed Enter,
 * tapped it, and got silence, with a live 11-device rig on screen).
 *
 * `permission` is passed in rather than derived so this module stays free of
 * the caps/store graph and runs under a bare `tsx` test.
 */
export function profileSaveLock(opts: {
  /** null when this principal may write backend config, else the reason. */
  permission: string | null;
  name: string;
  live: number;
  /** MUST be persistableAssignedCount(), not the raw pick count — see there. */
  assigned: number;
  /** true when the only picks are simulator rows, so the reason can say so. */
  simOnly?: boolean;
  busy: boolean;
}): string | null {
  if (opts.permission) return opts.permission;
  if (opts.busy) return "Another rig action is still running.";
  if (profileSaveSource(opts.live, opts.assigned) === null)
    return opts.simOnly
      ? "Connect the simulator rig first — simulator picks alone can't be saved to a profile."
      : "Nothing to save yet — connect a rig, or pick drivers on the rows above.";
  if (!opts.name.trim()) return "Name it first — the profile is stored under this name.";
  return null;
}

// ------------------------------------------------------------- persistence
// Pre-profile stickiness across reloads. Profiles are the durable store; this
// is just "don't lose my dropdowns on F5". Any parse error degrades to {}.
const LS_KEY = "astrodeck.equipment.assignments.v1";

export function loadAssignments(): AssignmentMap {
  try {
    const raw = globalThis.localStorage?.getItem(LS_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as AssignmentMap) : {};
  } catch {
    return {};
  }
}

export function saveAssignments(a: AssignmentMap): void {
  try {
    globalThis.localStorage?.setItem(LS_KEY, JSON.stringify(a));
  } catch {
    // storage full / privacy mode — stickiness is best-effort, never fatal
  }
}

// ============================================================ GUIDER SLOT NOTE
// The guider row's dropdown is NOT where you choose your guider, and saying
// nothing about that made it read as a dead end: on a rig with a real guide
// camera the list showed "Simulator" and nothing else, so the honest conclusion
// was that AstroDeck cannot guide (user, 2026-07-30 — "how am I supposed to
// select a guider? the only option is simulator").
//
// It is not a dead end, it is a different shape. The native guider is not a
// DEVICE you assign; it is the Rust engine running on your guide CAMERA, picked
// on the Guide screen's provider row (ProvidersConfig.guide, resolved server-
// side by providers.py::_resolve_guide). Only BRIDGE guiders — PHD2, NINA — are
// devices this dropdown can fill, which is why an unconfigured rig sees only the
// simulator and concludes the worst.
//
// Lives here rather than in the JSX for the same reason profileDelete.ts does:
// the judgement is the part worth pinning, and a sentence that names the wrong
// screen is worse than no sentence at all.

/** Which situation the guider slot is in. `native` = the engine can run here;
 *  `no-guide-cam` = engine present but nothing to run it on; `no-engine` = the
 *  native wheel is missing, so a bridge really is the only option. */
export type GuiderSlotState = "native" | "no-guide-cam" | "no-engine";

export function guiderSlotState(
  nativeReachable: boolean,
  guideCamConnected: boolean,
): GuiderSlotState {
  if (!nativeReachable) return "no-engine";
  return guideCamConnected ? "native" : "no-guide-cam";
}

/** The sentence for that situation. Always names WHERE the control is, because
 *  "you can't pick it here" without "you pick it there" is the same dead end
 *  with extra words. */
export function guiderSlotNote(
  state: GuiderSlotState,
  opts: { guideCamName?: string | null; nativeError?: string | null } = {},
): string {
  if (state === "no-engine") {
    const why = opts.nativeError ? ` (${opts.nativeError})` : "";
    return (
      `The native guiding engine is unavailable${why}, so guiding needs a PHD2 ` +
      `or NINA driver — add one under Settings → Drivers.`
    );
  }
  if (state === "native") {
    const cam = opts.guideCamName || "your guide camera";
    return (
      `AstroDeck guides with its own engine on ${cam} — no guider device to ` +
      `assign here. Pick which guider runs on the Guide screen.`
    );
  }
  return (
    "AstroDeck's own guiding engine runs on a guide camera, not on a device " +
    "assigned here. Connect a guide camera above and it becomes available on " +
    "the Guide screen."
  );
}

// ============================================================== BACKEND BADGE
// The header chip that answers "what am I driving through right now".
//
// It used to be a two-branch ternary — nina, alpaca, ELSE "SIM" — written when
// those were the only three rigs that existed. The driver model broke that
// assumption: a rig assembled from per-role hardware drivers has no single
// primary, so hub._effective_primary falls through to the first session's
// backend name ("zwo-usb" on this rig), which matched neither branch and landed
// on the default. A telescope, two cameras, a filter wheel and a focuser, all
// real, all connected, badged SIM.
//
// That is the worst possible lie for this particular chip: it is the one thing
// on screen telling you whether commands reach the sky, and it was reading
// "simulator" while the mount was tracking.

/** The badge text for a backend mode, or null when nothing is connected. */
export function backendBadge(mode: string | null | undefined): string | null {
  const m = (mode ?? "").trim().toLowerCase();
  if (!m || m === "none") return null;
  if (m === "nina") return "NINA";
  // "alpaca" is the label the hub gives the native/Alpaca path (W1.6 PIN).
  if (m === "alpaca" || m === "native") return "ALPACA";
  if (m === "sim") return "SIM";
  // Anything else is a per-role hardware backend (zwo-am5, player-one,
  // wanderer-snowflake, zwo-usb, ...). It is a DIRECT rig, and the one thing it
  // is definitely not is the simulator.
  return "NATIVE";
}

/** Whether the badge should read as a warning: the simulator is the only mode
 *  where commands do not reach real hardware, and it deserves to look different
 *  from every mode that does. */
export function backendBadgeIsSim(mode: string | null | undefined): boolean {
  return (mode ?? "").trim().toLowerCase() === "sim";
}
