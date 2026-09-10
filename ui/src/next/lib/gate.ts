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
  /** Blocked while this server lane is busy (a `BusyLane` name from
   *  lib/useBusy.ts, or any lane string) — the caller passes this only when
   *  the lane IS busy; presence of the field is the signal. */
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
 *  ARCHITECTURE.md #8's `needsRole` list; telescope/switch/filterwheel get the
 *  UI's own vocabulary (mount / power switch / filter wheel), the rest pass
 *  through unchanged. */
export function roleLabel(role: string): string {
  switch (role) {
    case "telescope":
      return "mount";
    case "switch":
      return "power switch";
    case "filterwheel":
      return "filter wheel";
    default:
      return role;
  }
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

  if (inp.busyLane) {
    if (MAPPED_BUSY_LANES.includes(inp.busyLane)) {
      const sentence = humanizeLaneConflict(`'${inp.busyLane}' is already running`);
      if (sentence) return sentence;
    }
    return `${inp.busyLane} is running`;
  }

  if (inp.extra) return inp.extra;

  return null;
}
