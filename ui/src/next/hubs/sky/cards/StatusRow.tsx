// StatusRow.tsx - the three-item line above the finder (hub-sky plan A.3).
//
// Every item is a NUMBER plus a way in. "Show 11 suggested targets" is the
// ranked list's count, "clear 82%" is the sky above this site right now, and the
// site pill names where "here" is. None of them is a caption: the row exists
// because a finder full of markers cannot say how many of them are worth
// pointing at, nor whether the sky is clear, nor which of the user's sites the
// horizon under those markers belongs to.
//
// The SKYDOME pill is a deviation from README section 1 and a de-scope of the
// prototype's inline 3D dome card (plan H.3): the Weather hub owns the dome, and
// two hemisphere renderers in two hubs would be two truths about the same sky.
// The pill carries the lock through as `?target=`, so the dome opens on whatever
// the finder is pointing at rather than on nothing.

import type { JSX } from "react";
import { Mono, Pill } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";

export interface StatusRowProps {
  reachCount: number;
  clearPct: number | null;
  siteName: string;
  lockId: string | null;
  canViewWeather: boolean;
  onExplain: (reason: string) => void;
}

export function StatusRow({
  reachCount,
  clearPct,
  siteName,
  lockId,
  canViewWeather,
  onExplain,
}: StatusRowProps): JSX.Element {
  const domeReason = canViewWeather ? null : "needs operator or admin access";
  return (
    <div
      data-testid="sky-status-row"
      style={{
        display: "flex",
        gap: 6,
        flexWrap: "wrap",
        alignItems: "center",
        fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
        fontSize: 10,
        color: "var(--text-dim)",
      }}
    >
      <Pill
        tone="dim"
        onClick={() => nav.sheet("targets")}
        data-testid="sky-suggested"
        ariaLabel={`show ${reachCount} suggested targets`}
      >
        Show <span style={{ color: "var(--accent)" }}>{reachCount}</span> suggested targets ›
      </Pill>

      {/* No reading is not zero cloud. `clear —` is the honest form and the
          layers popover carries the server's own reason for it. */}
      <Mono size={10} tone={clearPct == null ? "dim" : "good"}>
        {clearPct == null ? "clear —" : `clear ${Math.round(clearPct)}%`}
      </Mono>

      <Pill
        tone="dim"
        data-testid="sky-dome"
        onClick={() => {
          if (domeReason) { onExplain(domeReason); return; }
          nav.go(`/weather/sky${lockId ? `?target=${encodeURIComponent(lockId)}` : ""}`);
        }}
        ariaLabel="open the skydome"
        className={domeReason ? "nx-locked" : ""}
      >
        SKYDOME ›
      </Pill>

      <span style={{ marginLeft: "auto" }}>
        <Pill
          tone="dim"
          glyph={<NxIcon name="gps" size={12} />}
          onClick={() => nav.sheet("sites")}
          data-testid="sky-site"
          ariaLabel={`site: ${siteName}`}
        >
          {siteName} ›
        </Pill>
      </span>
    </div>
  );
}
