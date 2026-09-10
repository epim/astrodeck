// gate.ts — the ONE lock-reason helper for every control that issues a
// command (ARCHITECTURE.md #8 "RBAC and gating"). Pure: no React, no store, no
// fetch — `s` is the four narrow fields the caller already has (from the real
// store via `gateHook.ts`'s `useLock`, or from a test).
//
// Priority: link down -> cap -> role not connected -> busy lane -> extra.
// Reason copy (ARCHITECTURE.md #8, verbatim):
//   link down: "the rig is not reachable"
//   cap:       `needs ${accessPhrase(cap)}`              (ui/src/lib/caps.ts)
//   role:      `connect a ${humanRole} first`
//   busy:      the lib/humanize.ts lane sentence, else `${lane} is running`
//   extra:     the caller's string, verbatim
//
// `busyLane` is DECLARATIVE, not observational: the caller passes
// `busyLane: "capture"` to mean "block me while that lane is busy", not "the
// lane IS busy right now" — `lockReason` itself decides that by reading
// `s.status?.busy_lanes` (RigStatus.busy_lanes: string[], the per-lane list)
// and, as a fallback, the collapsed `s.status?.busy` word (RigStatus.busy:
// "slewing"|"solving"|"focusing"|"capturing"|null — built for the stale-
// telemetry banner, so it only distinguishes 4 words, mapped back to ONE lane
// each here: slewing->goto, solving->solve, focusing->autofocus,
// capturing->capture). A lane absent from both reads as NOT busy — presence
// of `inp.busyLane` is no longer itself the signal.

import { accessPhrase, capAllowed, resolveRoleConnected } from "../../lib/caps";
import { humanizeLaneConflict, MAPPED_BUSY_LANES } from "../../lib/humanize";
import type { Capability, Principal, RigStatus, WsPhase } from "../../types";

export interface GateInput {
  /** control.capture, control.mount, control.guide, control.power,
   *  config.backend, config.safety, config.solar_override, config.site_optics,
   *  config.alerts, admin.users, system.update, view.media, view.weather,
   *  view.site_precise, view.site_derived — any `Capability` from types.ts. */
  cap?: Capability;
  /** Device role that must be connected: camera | telescope | guider | switch
   *  | focuser | filterwheel | rotator. */
  needsRole?: string;
  /** Block while THIS server lane is busy (a `BusyLane` name from
   *  lib/useBusy.ts, or any lane string). Declarative: naming a lane here does
   *  not mean it IS busy — `lockReason` reads `s.status.busy_lanes`/`busy` to
   *  decide that itself. */
  busyLane?: string;
  /** Caller-specific reason (e.g. "a flow owns the mount"). */
  extra?: string | null;
}

export interface GateStoreSlice {
  principal: Principal | null;
  status: RigStatus | null;
  equipConnected: boolean;
  wsPhase: WsPhase;
}

/** Human name for a device role in a lock note — role ids from
 *  ARCHITECTURE.md #8's `needsRole` list; telescope/switch/filterwheel/safety
 *  get the UI's own vocabulary (mount / power box / filter wheel / safety
 *  monitor), the rest pass through unchanged.
 *
 *  `switch` is "power box", not "power switch": the rig plan's role table
 *  (hub-rig.md #0.5) names the DEVICE, and "connect a power switch first" reads
 *  as a toggle in this app rather than the mains box the user has to plug in.
 *  `safety` would otherwise fall through to "connect a safety first". */
export function roleLabel(role: string): string {
  switch (role) {
    case "telescope":
      return "mount";
    case "switch":
      return "power box";
    case "filterwheel":
      return "filter wheel";
    case "safety":
      return "safety monitor";
    default:
      return role;
  }
}

// RigStatus.busy is the per-lane set collapsed to one word for the stale-
// telemetry banner (lib/useBusy.ts's own doc comment: "it cannot tell one
// control's operation from another's"), so it is only a fallback for when
// `busy_lanes` is absent (a server older than 2026-08-05). Each collapsed word
// maps back to exactly the one lane named here, not the fuller many-to-one
// table `busy_lanes` itself would show.
const BUSY_WORD_FOR_LANE: Record<string, string> = {
  goto: "slewing",
  solve: "solving",
  autofocus: "focusing",
  capture: "capturing",
};

/** True while `lane` is actually busy, per the rig's own status — either
 *  listed in `busy_lanes`, or (fallback) the collapsed `busy` word for it. */
function isLaneBusy(status: RigStatus | null | undefined, lane: string): boolean {
  const lanes = status?.busy_lanes;
  if (Array.isArray(lanes) && lanes.includes(lane)) return true;
  const word = BUSY_WORD_FOR_LANE[lane];
  return word != null && status?.busy === word;
}

/** The single lock-reason decision every honest-disabled control renders.
 *  `null` means unlocked. */
export function lockReason(inp: GateInput, s: GateStoreSlice): string | null {
  if (s.wsPhase !== "up") return "the rig is not reachable";

  if (inp.cap && !capAllowed(s.principal, inp.cap)) {
    return `needs ${accessPhrase(inp.cap)}`;
  }

  if (inp.needsRole) {
    const { connected } = resolveRoleConnected(
      inp.needsRole,
      s.status?.backend_links,
      s.status?.connected,
      s.equipConnected,
    );
    if (!connected) return `connect a ${roleLabel(inp.needsRole)} first`;
  }

  if (inp.busyLane && isLaneBusy(s.status, inp.busyLane)) {
    if (MAPPED_BUSY_LANES.includes(inp.busyLane)) {
      const sentence = humanizeLaneConflict(`'${inp.busyLane}' is already running`);
      if (sentence) return sentence;
    }
    return `${inp.busyLane} is running`;
  }

  if (inp.extra) return inp.extra;

  return null;
}
