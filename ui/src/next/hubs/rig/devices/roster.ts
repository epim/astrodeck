// roster.ts - the device list on RIG - Devices, as a PURE function of what the
// rig publishes (plan hub-rig.md A.4).
//
// WHY THIS IS A MODULE AND NOT JSX. Every row on that screen makes three claims
// at once - which devices exist, what each one is doing, and whether anything is
// wrong - and each claim has a different source: `backend_links` (the per-role
// live grid), `status.connected` (the device map), and the per-device live
// blocks. Deciding that inside the render is how a row ends up showing an alarm
// COLOUR with no stated reason, which is the exact defect `BackendLinkGrid`
// documents at its `linkReason` (UX #52). Here it is one function with a table
// test beside it.
//
// THREE RULES THAT ARE EASY TO GET WRONG:
//
//  1. A ROLE IS NOT A DEVICE. The guider is an ENGINE: it has no
//     `status.connected` entry at all, only a `backend_links` row and
//     `status.guider`. `resolveRoleConnected` already joins the two surfaces,
//     and this file uses it rather than re-deriving the join - reading only
//     `connected` is what once printed "GUIDING - UNASSIGNED" three inches from
//     "GUIDING - CONNECTED".
//  2. ROTATOR IS NOT IN THE DESIGN'S SEVEN. It gets a row when there is
//     EVIDENCE of one (a link, a device entry, or `status.rotator`), and no row
//     at all otherwise - offering a rotator sheet to a rig that has never had a
//     rotator is a promise nothing keeps. Same rule for any role the server
//     grows later: it appears because the server named it, not because this
//     file did.
//  3. AN ALARM WORD ALWAYS CARRIES ITS REASON. `reason` is non-null for every
//     FAILED / DEGRADED row, and `linkReason` (imported, not copied) is what
//     supplies the sentence when the backend gave none.

import { resolveRoleConnected } from "../../../../lib/caps";
import { linkReason, linkTriState } from "../../../../components/settings/BackendLinkGrid";
import { ROLE_LABEL } from "../../../../components/settings/backendMeta";
import type { BackendLink, DeviceInfo, RigStatus, SafetyState } from "../../../../types";
import type { DomeState } from "../../../../api/backends";
import type { NxIconName } from "../../../icons";
import type { Tone } from "../../../ui/types";

export type Led = "on" | "off" | "warn" | "bad";

export interface DeviceRowModel {
  /** status key: camera, telescope, focuser, filterwheel, guider, rotator,
   *  switch, safety - and whatever else the server reports. */
  role: string;
  /** CAMERA, MOUNT, FOCUSER, FILTER WHEEL, GUIDER, ROTATOR, POWER, SAFETY. */
  label: string;
  glyph: NxIconName;
  /** The sheet name to open, or null when this role has no screen in this
   *  build (a guide camera, a flat panel). A null row is not pressable, rather
   *  than pressable into nothing. */
  sheet: string | null;
  /** The mono second line: the driver's own name plus the one live clause the
   *  design shows for that role. Empty when the rig has told us neither. */
  driverLine: string;
  /** The right-hand word. */
  state: string;
  led: Led;
  /** The link error, rendered as the row's own third line. Never null while
   *  `state` is an alarm word. */
  reason: string | null;
}

export interface RosterInput {
  connected: Record<string, DeviceInfo> | undefined;
  links: BackendLink[];
  equipConnected: boolean;
  status: RigStatus | null;
  safety: SafetyState | null;
  /** Read on its own 15 s poll, not off the 2 s status frame. Carried here so
   *  a roof row can join the roster the day the design asks for one; no row
   *  reads it today, and inventing one from a poll this screen does not make
   *  would be worse than the gap. */
  dome: DomeState | null;
  sequenceRunning: boolean;
}

/** The design's seven, in the design's order, plus ROTATOR (GAP-2 "Missing -
 *  rotator") in the position the plan gives it. */
const CANONICAL: { role: string; label: string; glyph: NxIconName; sheet: string }[] = [
  { role: "camera", label: "CAMERA", glyph: "camera", sheet: "camera" },
  { role: "telescope", label: "MOUNT", glyph: "mount", sheet: "mount" },
  { role: "focuser", label: "FOCUSER", glyph: "focuser", sheet: "focuser" },
  { role: "filterwheel", label: "FILTER WHEEL", glyph: "wheel", sheet: "wheel" },
  { role: "guider", label: "GUIDER", glyph: "guider", sheet: "guider" },
  { role: "rotator", label: "ROTATOR", glyph: "rotator", sheet: "rotator" },
  { role: "safety", label: "SAFETY", glyph: "safety", sheet: "safety" },
  { role: "switch", label: "POWER", glyph: "power", sheet: "power" },
];

/** The roster a rig with NOTHING connected shows: the prototype's seven
 *  (logic.js:436), greyed. Rotator is absent on purpose - see rule 2. */
const BASE_ROLES = CANONICAL.filter((c) => c.role !== "rotator");

const DEG = "°";
const DOT = " · ";

function fmtTemp(c: number | null | undefined): string | null {
  return typeof c === "number" && Number.isFinite(c) ? c.toFixed(1) + DEG + "C" : null;
}

function join(parts: (string | null | undefined)[]): string {
  return parts.filter((p): p is string => !!p && p.trim() !== "").join(DOT);
}

/** The display name the rig gives this role, or "" when it gives none. The
 *  guider and the rotator carry theirs on their own live blocks, because one is
 *  an engine and the other predates the device map on some backends. */
function nameFor(role: string, inp: RosterInput): string {
  const dev = inp.connected?.[role];
  if (dev?.name) return dev.name;
  if (role === "guider" && inp.status?.guider?.name) return inp.status.guider.name;
  if (role === "rotator" && inp.status?.rotator?.name) return inp.status.rotator.name;
  return "";
}

/** The live clause the design prints beside the name, per role. Every clause is
 *  dropped when the field behind it is absent - a driver line must never carry
 *  a number nothing measured. */
function liveClause(role: string, inp: RosterInput): string | null {
  const st = inp.status;
  switch (role) {
    case "camera":
      return fmtTemp(st?.camera?.temperature);
    case "telescope": {
      const m = st?.mount;
      if (!m) return null;
      if (m.parked) return "parked";
      if (m.slewing) return "slewing";
      if (m.tracking) return "tracking " + (m.tracking_rate ?? "sidereal");
      return "idle";
    }
    case "focuser": {
      const f = st?.focuser;
      if (!f) return null;
      return join([typeof f.position === "number" ? String(f.position) : null, fmtTemp(f.temperature)]);
    }
    case "filterwheel": {
      const w = st?.filterwheel;
      if (!w) return null;
      const slots = Array.isArray(w.names) && w.names.length ? w.names.length + " slots" : null;
      return join([slots, w.current || null]);
    }
    case "guider": {
      const g = st?.guider;
      if (!g) return null;
      return g.guiding ? "guiding" : (g.phase || "idle");
    }
    case "rotator": {
      const r = st?.rotator;
      if (!r || typeof r.sky_deg !== "number") return null;
      return "PA " + r.sky_deg.toFixed(1) + DEG;
    }
    case "safety": {
      const src = inp.safety?.reading?.source;
      // The source IS the device name on most monitors, and printing it twice
      // in two typefaces is the `driverTypeChip` defect, so it only rides when
      // it adds something.
      return src && src !== nameFor(role, inp) ? src : null;
    }
    default:
      return null;
  }
}

/** The word and the lamp for a role the rig says is CONNECTED. */
function liveState(role: string, inp: RosterInput): { state: string; led: Led } {
  const st = inp.status;
  switch (role) {
    case "camera": {
      const cam = st?.camera;
      if (!cam || cam.can_cool === false) return { state: "OK", led: "on" };
      if (!cam.cooler?.on) return { state: "WARM", led: "warn" };
      const t = fmtTemp(cam.temperature);
      return t ? { state: t, led: "on" } : { state: "COOLING", led: "on" };
    }
    case "telescope": {
      const m = st?.mount;
      if (!m) return { state: "OK", led: "on" };
      if (m.parked) return { state: "PARKED", led: "off" };
      if (m.slewing) return { state: "SLEWING", led: "warn" };
      if (m.tracking) return { state: "TRACKING", led: "on" };
      return { state: "IDLE", led: "off" };
    }
    case "guider": {
      const g = st?.guider;
      if (g?.phase === "lost") return { state: "LOST", led: "bad" };
      if (g?.guiding) return { state: "GUIDING", led: "on" };
      return { state: "IDLE", led: "off" };
    }
    case "safety": {
      const r = inp.safety?.reading;
      if (!r) return { state: "OK", led: "on" };
      if (r.stale) return { state: "STALE", led: "warn" };
      return r.is_safe ? { state: "SAFE", led: "on" } : { state: "UNSAFE", led: "bad" };
    }
    default:
      return { state: "OK", led: "on" };
  }
}

/** Which `status` key carries a role's live block, for the "a rotator with no
 *  link is still a rotator" rule. */
const LIVE_KEY: Record<string, string> = {
  camera: "camera", telescope: "mount", focuser: "focuser",
  filterwheel: "filterwheel", guider: "guider", rotator: "rotator",
};

/** Is there any evidence at all that this role exists on this rig? */
function hasEvidence(role: string, inp: RosterInput): boolean {
  if (inp.links.some((l) => l.role === role)) return true;
  if (inp.connected?.[role]) return true;
  const key = LIVE_KEY[role];
  if (!key) return false;
  const st = inp.status as unknown as Record<string, unknown> | null;
  return !!st && st[key] != null;
}

interface RowSpec { role: string; label: string; glyph: NxIconName; sheet: string | null }

function rowFor(spec: RowSpec, inp: RosterInput): DeviceRowModel {
  const link = inp.links.find((l) => l.role === spec.role);
  const resolved = resolveRoleConnected(spec.role, inp.links, inp.connected, inp.equipConnected);
  const driverLine = join([nameFor(spec.role, inp), liveClause(spec.role, inp)]);

  let state = "NOT CONNECTED";
  let led: Led = "off";
  let reason: string | null = resolved.error;

  if (link) {
    const tri = linkTriState(link);
    if (tri === "failed") {
      state = "FAILED"; led = "bad"; reason = linkReason(link);
    } else if (tri === "degraded") {
      state = "DEGRADED"; led = "warn"; reason = linkReason(link);
    } else if (tri === "connected") {
      const s = liveState(spec.role, inp);
      state = s.state; led = s.led; reason = null;
    }
    // "skipped" - the connect never asked for this role - keeps NOT CONNECTED.
  } else if (resolved.connected) {
    const s = liveState(spec.role, inp);
    state = s.state; led = s.led; reason = null;
  }

  return { ...spec, driverLine, state, led, reason };
}

/**
 * The rows, top to bottom: the design's order first, then any other role the
 * server reports (a guide camera, a flat panel, a roof) appended rather than
 * dropped. Dropping them is not tidiness - this row's third line is now the
 * only place their link error is shown, `BackendLinkGrid` having become a
 * Settings surface in the new IA (plan E31).
 */
export function deviceRows(inp: RosterInput): DeviceRowModel[] {
  const known = new Set(CANONICAL.map((c) => c.role));
  const present: RowSpec[] = CANONICAL.filter((c) => hasEvidence(c.role, inp));

  // Nothing at all: the seven greyed rows, so the screen is somewhere to press
  // rather than an empty card claiming the app knows of no devices.
  const base: RowSpec[] = present.length > 0 ? present : BASE_ROLES.map((c) => ({ ...c }));

  const extraRoles: string[] = [];
  for (const l of inp.links) {
    if (!known.has(l.role) && !extraRoles.includes(l.role)) extraRoles.push(l.role);
  }
  for (const role of Object.keys(inp.connected ?? {})) {
    if (!known.has(role) && !extraRoles.includes(role)) extraRoles.push(role);
  }

  const extras: RowSpec[] = extraRoles.map((role) => ({
    role,
    label: (ROLE_LABEL[role] ?? role).toUpperCase(),
    glyph: "rig",
    sheet: null,
  }));

  return [...base, ...extras].map((spec) => rowFor(spec, inp));
}

// --------------------------------------------------------------- the summary

/** The mono line beside the RIG title (plan A.1). */
export interface RigSummary { text: string; tone: Tone }

/**
 * One line, built clause by clause from live evidence, and every clause whose
 * evidence is missing is DROPPED rather than filled with a default. The
 * prototype's version is a fixture - "cooled -10 C, tracking, 7 filters, safe"
 * is four claims a disconnected rig would happily print.
 *
 * `activeProfileName` is passed in already resolved against the profile list,
 * because the not-connected half of this line names a profile and must never
 * name one that does not exist (deleting the active profile leaves the pointer
 * dangling - lib/profileDelete.ts).
 */
export function rigSummary(inp: RosterInput, activeProfileName: string | null): RigSummary {
  const rows = deviceRows(inp);
  const anyLive = rows.some((r) => r.led === "on" || r.led === "warn");
  if (!inp.equipConnected && !anyLive) {
    return {
      text: activeProfileName ? `not connected · ${activeProfileName} ready` : "not connected",
      tone: "dim",
    };
  }

  const parts: string[] = [];
  let tone: Tone = "good";
  const warn = () => { if (tone !== "bad") tone = "warn"; };

  const cam = inp.status?.camera;
  if (cam) {
    const t = fmtTemp(cam.temperature);
    if (cam.warm?.active) {
      parts.push(t ? "warming " + t : "warming");
      warn();
    } else if (cam.cooler?.on) {
      const target = fmtTemp(cam.cooler.target_c);
      if (cam.cooler.at_target) parts.push(t ? "cooled " + t : "cooled");
      else { parts.push(t && target ? `cooling ${t} -> ${target}` : "cooling"); warn(); }
    } else if (cam.can_cool !== false) {
      parts.push("cooler off");
      warn();
    }
  }

  const m = inp.status?.mount;
  if (m) {
    if (m.parked) { parts.push("parked"); warn(); }
    else if (m.slewing) { parts.push("slewing"); warn(); }
    else if (m.tracking) parts.push("tracking");
  }

  const names = inp.status?.filterwheel?.names;
  if (Array.isArray(names) && names.length) parts.push(`${names.length} filters`);

  const safety = inp.safety;
  if (safety?.connected && safety.reading) {
    if (!safety.reading.is_safe) {
      parts.push(`UNSAFE - ${safety.reading.reason}`);
      tone = "bad";
    } else if (safety.reading.stale) { parts.push("safety stale"); warn(); }
    else parts.push("safe");
  } else if (rows.some((r) => r.role === "safety" && r.led !== "off")) {
    parts.push("no monitor");
    warn();
  }

  // A failed link outranks every calm clause above: something the user asked
  // for is not there.
  if (rows.some((r) => r.led === "bad")) tone = "bad";

  return { text: parts.length ? parts.join(DOT) : "connected", tone };
}
