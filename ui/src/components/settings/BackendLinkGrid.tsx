// BackendLinkGrid.tsx — the tri-state per-role connection readout (W1.6 boot-LED
// grid). Reads `backend_links` (hub.backend_links): a retained RoleResult joined
// with the role's LIVE `connected` state. Present on every status poll + hello,
// and inside ConnectRigResult. `[]` until a RigSpec/profile connect happened
// (legacy connect_* paths leave it empty), so this grid renders an empty-state
// hint then, NOT a wall of red.
//
// Tri-state mapping (NEVER color alone — night mode collapses good/warn/bad toward
// red, so each cell reads by Led SHAPE + glyph + word):
//   attempted=false                  → "not requested"  → led "off"  (a dash, not red)
//   attempted=true,  connected=true  → "connected"      → led "on"
//   attempted=true,  ok=true, !conn  → "degraded"       → led "warn" (attached, link dropped)
//   attempted=true,  ok=false        → "failed"         → led "bad"  (+ inline error)
//
// `connected` is the role's LIVE state from the hub, and a role can be served by
// an ENGINE rather than a device object (the guider: AstroDeck native / NINA /
// PHD2) — hub._role_live_connected covers that. This grid additionally
// guarantees an alarm word NEVER renders alone: see linkReason (UX #52).

import type { JSX } from "react";
import type { BackendLink, LedState } from "../../types";
import { Led, EmptyState } from "../ui";
// Canonical role order + labels live in backendMeta (mirrors server
// devices.backend.ROLES) — this grid had drifted its own copy; single source now.
import { ALL_ROLES, ROLE_LABEL } from "./backendMeta";

type TriState = "connected" | "degraded" | "failed" | "skipped";

export function linkTriState(link: BackendLink): TriState {
  if (!link.attempted) return "skipped";
  if (link.connected) return "connected";
  if (link.ok) return "degraded"; // attached at connect but the live link is down
  return "failed";
}

const TRI_META: Record<
  TriState,
  { led: LedState; word: string; tone: string }
> = {
  connected: { led: "on", word: "CONNECTED", tone: "text-good" },
  degraded: { led: "warn", word: "DEGRADED", tone: "text-warn" },
  failed: { led: "bad", word: "FAILED", tone: "text-bad" },
  skipped: { led: "off", word: "NOT REQUESTED", tone: "text-faint" },
};

/** An alarm word with NO reason attached is the bug, not the fix (UX #52): the
 *  backend can report `ok` with a null `error`, and the grid used to render a
 *  bare orange DEGRADED with nothing under it. So every alarm state gets a
 *  stated reason — the backend's when it has one, an honest "we don't know"
 *  sentence when it doesn't. `null` only for the two calm states. */
export function linkReason(link: BackendLink): string | null {
  if (link.error) return link.error;
  switch (linkTriState(link)) {
    case "degraded":
      return "Came up at connect, but this role is not reporting a live link " +
        "now and the backend gave no reason. Reconnect it from Equipment.";
    case "failed":
      return "The backend reported no reason. Check the log for this role.";
    default:
      return null;
  }
}

/** One tri-state row. `dense` drops the reason line for tight side panels —
 *  EXCEPT on an alarm state, which must never show a bare word (UX #52). */
function LinkRow({ link, dense }: { link: BackendLink; dense?: boolean }): JSX.Element {
  const tri = linkTriState(link);
  const meta = TRI_META[tri];
  const label = ROLE_LABEL[link.role] ?? link.role;
  const alarm = tri === "degraded" || tri === "failed";
  const reason = !dense || alarm ? linkReason(link) : null;
  return (
    <div className="flex items-center gap-3 border border-line bg-bg/60 px-3 py-2.5">
      <Led state={meta.led} label={`${label}: ${meta.word.toLowerCase()}`} />
      <span className="label w-28 shrink-0">{label}</span>
      <div className="min-w-0 flex-1">
        <span className={`mono text-[11px] tracking-wider ${meta.tone}`}>{meta.word}</span>
        {/* The inline failure/degraded reason — the WHOLE point of the tri-state:
            a viewer can see WHY a role didn't come up without opening the log.
            Wraps rather than truncating: a `title=` tooltip never fires on a
            tablet in the field, so a clipped reason would be no reason. */}
        {reason && <div className="text-[10px] text-dim mt-0.5">{reason}</div>}
      </div>
    </div>
  );
}

export default function BackendLinkGrid({
  links,
  dense = false,
  emptyHint = "Connect a rig from the picker or activate a profile to see per-role link status here.",
}: {
  links: BackendLink[];
  dense?: boolean;
  emptyHint?: string;
}): JSX.Element {
  if (links.length === 0) {
    return (
      <EmptyState
        icon="link"
        title="No rig connected yet"
        hint={emptyHint}
        size={dense ? "inline" : "hero"}
      />
    );
  }

  // Order canonically (camera→rotator); any unknown trailing role keeps its order.
  const ordered = [...links].sort((a, b) => {
    const ia = ALL_ROLES.indexOf(a.role as (typeof ALL_ROLES)[number]);
    const ib = ALL_ROLES.indexOf(b.role as (typeof ALL_ROLES)[number]);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });

  return (
    <div className="flex flex-col gap-2">
      {ordered.map((l) => (
        <LinkRow key={l.role} link={l} dense={dense} />
      ))}
    </div>
  );
}
