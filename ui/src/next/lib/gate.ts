// gate.ts - the ONE lock-reason helper for every control that issues a
// command (ARCHITECTURE.md #8 "RBAC and gating"). Pure: no React, no store, no
// fetch - `s` is the five narrow fields the caller already has (from the real
// store via `gateHook.ts`'s `useLock`, or from a test).
//
// Priority: link down -> LAN-only -> cap -> role not connected -> busy lane -> extra.
// Reason copy (ARCHITECTURE.md #8, verbatim):
//   link down: "the rig is not reachable"
//   LAN-only:  LOCAL_ONLY_REASON below
//   cap:       `needs ${accessPhrase(cap)}`              (ui/src/lib/caps.ts)
//   role:      `connect a ${humanRole} first`
//   busy:      the lib/humanize.ts lane sentence, else `${lane} is running`
//   extra:     the caller's string, verbatim
//
// The LAN-only rule sits ABOVE the capability rule on purpose: over the relay
// the rig refuses these writes for EVERY role, an admin included, so naming the
// capability there would be a true sentence about the wrong blocker - press it
// as an admin on the LAN and it works, press it as an admin on the relay and it
// 403s. Below the link rule for the same reason in reverse: with the socket
// down nothing about this origin is the reason the control cannot be pressed.
//
// `busyLane` is DECLARATIVE, not observational: the caller passes
// `busyLane: "capture"` to mean "block me while that lane is busy", not "the
// lane IS busy right now" - `lockReason` itself decides that by reading
// `s.status?.busy_lanes` (RigStatus.busy_lanes: string[], the per-lane list)
// and, as a fallback, the collapsed `s.status?.busy` word (RigStatus.busy:
// "slewing"|"solving"|"focusing"|"capturing"|null - built for the stale-
// telemetry banner, so it only distinguishes 4 words, mapped back to ONE lane
// each here: slewing->goto, solving->solve, focusing->autofocus,
// capturing->capture). A lane absent from both reads as NOT busy - presence
// of `inp.busyLane` is no longer itself the signal.

import { accessPhrase, capAllowed, resolveRoleConnected } from "../../lib/caps";
import { humanizeLaneConflict, MAPPED_BUSY_LANES } from "../../lib/humanize";
import type { Capability, Principal, RigStatus, WsPhase } from "../../types";

export interface GateInput {
  /** control.capture, control.mount, control.guide, control.power,
   *  config.backend, config.safety, config.solar_override, config.site_optics,
   *  config.alerts, admin.users, system.update, view.media, view.weather,
   *  view.site_precise, view.site_derived - any `Capability` from types.ts. */
  cap?: Capability;
  /** Device role that must be connected: camera | telescope | guider | switch
   *  | focuser | filterwheel | rotator. */
  needsRole?: string;
  /** Block while THIS server lane is busy (a `BusyLane` name from
   *  lib/useBusy.ts, or any lane string). Declarative: naming a lane here does
   *  not mean it IS busy - `lockReason` reads `s.status.busy_lanes`/`busy` to
   *  decide that itself. */
  busyLane?: string;
  /** This control issues a write the rig fences to the LAN (`app.py`'s
   *  `_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES` / `_REMOTE_LOCAL_ONLY_EXACT`:
   *  /api/config, /api/alerts, /api/drivers, /api/profiles, /api/connect,
   *  /api/survey/pack, /api/ephemeris, /api/locations, /api/switch/ports, the
   *  update routes, /api/users, /api/auth/*). Declarative like `busyLane`:
   *  saying so does not mean the tab IS on the relay - `lockReason` reads
   *  `s.onRelay` to decide that. */
  needsLan?: boolean;
  /** Caller-specific reason (e.g. "a flow owns the mount"). */
  extra?: string | null;
}

export interface GateStoreSlice {
  principal: Principal | null;
  status: RigStatus | null;
  equipConnected: boolean;
  wsPhase: WsPhase;
  /** Is this tab tunnelled through the relay (`next/lib/relay.ts`'s
   *  `onRelay()`, which `useLock` supplies)? Optional because the callers that
   *  build this slice by hand - `rig/capture/captureGate.ts`,
   *  `session/now/incidentActions.ts` - gate nothing LAN-only; a caller that
   *  passes `needsLan` and omits this would never lock, so pass both. */
  onRelay?: boolean;
}

/** The one sentence for a control the rig will refuse over the relay. A full
 *  sentence rather than the lowercase fragment the other reasons use, because
 *  it is also the toast a 403 `local_only` raises when a write slips past the
 *  gate (`isLocalOnly` below) - and there it stands alone. */
export const LOCAL_ONLY_REASON =
  "This changes the rig's own settings, so it needs the LAN - you are connected through the relay.";

/** Did the rig refuse this because the request arrived over the relay? The
 *  fence answers 403 with `code: "local_only"` (`app.py:2098-2106`), and
 *  `ApiError` carries `code`. Use it BEFORE any capability sentence in a catch:
 *  a `local_only` 403 is not a role the caller is missing, and telling an admin
 *  they need admin access is the exact wrong-blocker defect `needsLan` exists
 *  to prevent. */
export function isLocalOnly(err: unknown): boolean {
  return (
    !!err && typeof err === "object" && (err as { code?: unknown }).code === "local_only"
  );
}

/** Human name for a device role in a lock note - role ids from
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

/** True while `lane` is actually busy, per the rig's own status - either
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

  if (inp.needsLan && s.onRelay) return LOCAL_ONLY_REASON;

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
