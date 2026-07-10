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
  | "driver-disabled";

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

/** Sticky-assignment state (spec §5): the user's choice persists through a
 *  driver outage and re-lights when it returns; a deleted driver reads
 *  "driver-removed" and never silently reconnects elsewhere. */
export function slotState(a: Assignment | null, drivers: DriverInfo[]): SlotState {
  if (!a) return "unassigned";
  const d = drivers.find((x) => x.id === a.driverId);
  if (!d) return "driver-removed";
  if (!d.enabled) return "driver-disabled";
  if (!d.status.reachable) return "driver-unreachable";
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
 *  role — mirrors BackendPicker's MOTION_ROLES rule. */
export function hasRealMotion(assignments: AssignmentMap, drivers: DriverInfo[]): boolean {
  return ["telescope", "focuser"].some((role) => {
    const a = assignments[role];
    if (!a || a.driverId === "sim") return false;
    return slotState(a, drivers) !== "driver-removed";
  });
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
