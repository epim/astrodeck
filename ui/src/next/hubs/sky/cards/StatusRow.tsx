// StatusRow.tsx - the three-item line above the finder (hub-sky plan A.3).
//
// Every item is a NUMBER plus a way in. "Show 11 suggested targets" is the
// ranked list's count, "clear 82%" is the sky above this site right now, and the
// site pill names where "here" is. None of them is a caption: the row exists
// because a finder full of markers cannot say how many of them are worth
// pointing at, nor whether the sky is clear, nor which of the user's sites the
// horizon under those markers belongs to.
//
// The SKYDOME pill used to LEAVE the hub, and the header here used to argue that
// it had to: the prototype's inline dome card was de-scoped (plan H.3) on the
// grounds that two hemisphere renderers in two hubs would be two truths about
// the same sky. D-SKY-2 landed the card instead, and the argument does not
// survive it - `cards/DomeCard.tsx` mounts the SAME renderer the Weather hub
// mounts, so there is one implementation and one truth. The pill is now a
// scroll-to: the dome is on this screen, below the reach strip, and a pill that
// navigated away from a card six rows down would be the odd one out in a row
// where every other item opens what it names.
//
// It is never locked, for the same reason: scrolling this screen needs no
// capability. The dome card carries its own `view.weather` gate and says so in
// its own words, which is where a refusal belongs - on the thing refused.

import type { JSX } from "react";
import { Mono, Pill } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";

export interface StatusRowProps {
  reachCount: number;
  clearPct: number | null;
  siteName: string;
  /** Scrolls the skydome card into view. The hub owns the anchor and honours
   *  `prefers-reduced-motion`; see `SkyHub`'s `onDome`. */
  onDome: () => void;
}

export function StatusRow({
  reachCount,
  clearPct,
  siteName,
  onDome,
}: StatusRowProps): JSX.Element {
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

      {/* No reading is not zero cloud. `clear -` is the honest form and the
          layers popover carries the server's own reason for it. */}
      <Mono size={10} tone={clearPct == null ? "dim" : "good"}>
        {clearPct == null ? "clear -" : `clear ${Math.round(clearPct)}%`}
      </Mono>

      {/* No chevron. The chevron in this row means "this opens something
          else"; this one moves the page. */}
      <Pill
        tone="dim"
        data-testid="sky-dome"
        onClick={onDome}
        ariaLabel="scroll to the skydome card"
      >
        SKYDOME
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
