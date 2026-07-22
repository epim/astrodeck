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
