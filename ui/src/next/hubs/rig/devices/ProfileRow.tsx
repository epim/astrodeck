// ProfileRow.tsx - the PROFILE row and the popover it anchors (plan A.3).
//
// The row states which saved rig is in force and how big it is. The COUNT is
// the live role count while a rig is connected and the profile's own stored
// count otherwise, because those are two different claims and only one of them
// is true at a time: "7 devices" under a connected rig means seven are running,
// and under a disconnected one it means seven are remembered. `liveRoleCount`
// is the single authority for the first (it joins `status.connected` and
// `backend_links`, and neither alone is the whole rig).
//
// The row NEVER names a profile that does not exist: an `active_profile_id`
// pointing at a deleted profile is a real state (deleting the active profile
// leaves the pointer dangling - see lib/profileDelete.ts), and it reads as "no
// profile" rather than as a name we cannot show.

import { useRef, useState, type JSX } from "react";
import { Popover } from "../../../ui";
import type { ProfileRow as ProfileRowData } from "../../../../types";
import { ProfilesPopover } from "./ProfilesPopover";
import type { BusyWhat } from "./rigConnect";

export interface ProfileRowProps {
  rows: ProfileRowData[] | null;
  activeId: string | null;
  liveDevices: number;
  canConfig: boolean;
  /** `gate.ts`'s `LOCAL_ONLY_REASON` while this tab is on the relay, else null.
   *  Passed straight through to the popover, whose three verbs all write under
   *  `/api/profiles` - a prefix on the rig's LAN fence. */
  lanReason?: string | null;
  busy: BusyWhat;
  setBusy: (w: BusyWhat) => void;
  onRows: (rows: ProfileRowData[]) => void;
  reload: () => void;
}

export function ProfileRow(p: ProfileRowProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement | null>(null);

  const active = p.activeId ? (p.rows ?? []).find((r) => r.id === p.activeId) ?? null : null;
  const count = p.liveDevices > 0 ? p.liveDevices : active?.devices_count ?? 0;
  const value = `${active ? active.name : "no profile"} · ${count} device${count === 1 ? "" : "s"}`;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
      <button
        type="button"
        ref={anchor}
        data-testid="profile-row"
        onClick={() => setOpen((v) => !v)}
        style={{
          flex: 1, height: 40, padding: "0 12px", borderRadius: 12,
          border: "1px solid rgba(120,140,200,.3)", background: "var(--bg-raise)",
          color: "var(--text-1, #e8ecf7)", display: "flex", alignItems: "center",
          gap: 8, cursor: "pointer", minWidth: 0,
        }}
      >
        <span className="nx-label" data-size={10} style={{ letterSpacing: ".18em" }}>PROFILE</span>
        <span
          className="nx-mono"
          data-testid="profile-value"
          style={{
            fontSize: 11.5, flex: 1, textAlign: "left", whiteSpace: "nowrap",
            overflow: "hidden", textOverflow: "ellipsis",
          }}
        >
          {value}
        </span>
        <span className="nx-mono" style={{ fontSize: 10, color: "var(--accent)" }}>
          SWITCH {open ? "▴" : "▾"}
        </span>
      </button>

      <Popover
        open={open}
        anchorRef={anchor}
        align="start"
        onClose={() => setOpen(false)}
        data-testid="profiles-popover"
      >
        <ProfilesPopover
          rows={p.rows}
          liveDevices={p.liveDevices}
          canConfig={p.canConfig}
          lanReason={p.lanReason}
          busy={p.busy}
          setBusy={p.setBusy}
          onRows={p.onRows}
          reload={p.reload}
          onClose={() => setOpen(false)}
        />
      </Popover>
    </div>
  );
}
