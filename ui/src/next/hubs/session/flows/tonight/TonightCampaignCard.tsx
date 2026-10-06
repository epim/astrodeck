// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// TonightCampaignCard.tsx - how much of the pool each member has actually
// banked (parity row A22).
//
// THIS TAB RENDERS WHAT THE SERVER SENT AND COMPUTES NOTHING. `tonight.py::
// _campaign` folds the session ledger; the completion projection does not exist
// server-side, so no number is shown for it - the server's own `note` is the
// only sentence about what happens next.
//
// `banked === null` IS NOT ZERO, and that distinction is why there are three
// render branches instead of one. "0/45 cycles" says the rig looked and found
// nothing; "not counted" says nobody looked. Only the first should make an
// operator re-plan a month, so a null renders as an EMPTY OUTLINED TRACK with
// an accessible value of "not counted", never as a bar sitting at 0%.
//
// The bar is hand-rolled rather than the `Bar` primitive for exactly that
// reason: `Bar` reports `aria-valuenow` from a number, and the unmeasured case
// has no number to report. A progressbar that announces "0" for "unknown" is
// the colour-alone defect in audio.
//
// CAMPAIGN survives a refusal: `_campaign` reads the GRAPH, not the ephemeris,
// so "no site is set" does not stop it saying which members owe what.
//
// A NULL QUOTA IS NOT ZERO EITHER (#424). A POOL quota the server cannot count
// against ("inf", 0, a negative) comes back null, with the reason in `note`.
// The head then gives the member count alone and a bar announces no maximum,
// where both used to say 0 right above the note saying there is no count.

import type { JSX } from "react";

import { memberStatus } from "../../../../../components/flows/TonightCampaign";
import { Card, Label, Mono } from "../../../../ui";
import {
  campaignHead,
  type TonightCampaignMember, type TonightCampaignRead,
} from "./tonightModel";

function MemberRow({ m }: { m: TonightCampaignMember }): JSX.Element {
  // Shape and word as well as colour: a DONE row already differs by its text,
  // and the fill token follows rather than leads.
  const tone = m.banked === null ? "faint" : m.done ? "good" : "accent";
  const fill = m.done ? "var(--good)" : "var(--accent)";
  // The shared `memberStatus`, which since the S7 integration takes a null
  // quota itself (no "of N" to print, #424) for this card and the classic
  // panel alike.
  const status = memberStatus(m);
  return (
    <div className="nx-tn-member" data-testid={`tonight-member-${m.name}`}>
      <div className="nx-tn-member-head">
        <span className="nx-tn-member-name">{m.name}</span>
        <span className="nx-tn-member-status" data-tone={tone}>{status}</span>
      </div>
      <div
        className="nx-tn-member-track"
        role="progressbar"
        aria-label={`${m.name} campaign progress`}
        aria-valuemin={0}
        // No quota, no maximum: React writes no attribute for undefined.
        aria-valuemax={m.quota ?? undefined}
        aria-valuenow={m.banked ?? undefined}
        aria-valuetext={status}
      >
        {m.pct !== null && (
          <div
            className="nx-tn-member-fill"
            style={{ width: `${Math.max(0, Math.min(100, m.pct))}%`, background: fill }}
          />
        )}
      </div>
    </div>
  );
}

/** The honesty line, and it says a different thing in each case rather than one
 *  hedge that covers all of them. A hedge that fits every state is one nobody
 *  reads. */
function honestyLine(campaign: TonightCampaignRead): string {
  if (!campaign.has_pool) {
    return "Progress is per pool member, so a flow with a single TARGET has nothing to track here.";
  }
  if (!campaign.has_ledger) {
    return "The session log on the rig could not be read, so progress counts are unavailable.";
  }
  return "Counts show accepted frames from the session log as complete cycles: "
    + "a cycle is complete when every filter in the table has its frame.";
}

export function TonightCampaignCard({ campaign }: {
  campaign: TonightCampaignRead | null;
}): JSX.Element {
  if (!campaign) {
    return (
      <p className="nx-tn-note" data-testid="tonight-campaign">
        This flow's campaign state has not been resolved yet.
      </p>
    );
  }

  return (
    <Card tone="purple" className="nx-tn-camp" data-testid="tonight-campaign">
      <div className="nx-tn-camp-head">
        <Label size={10}>CAMPAIGN PROGRESS</Label>
        <Mono size={10} tone="dim">{campaignHead(campaign)}</Mono>
      </div>

      {campaign.members.length > 0 && (
        <div className="nx-tn-members">
          {campaign.members.map((m) => <MemberRow key={m.name} m={m} />)}
        </div>
      )}

      {campaign.note !== "" && <p className="nx-tn-note">{campaign.note}</p>}

      <Mono size={10} tone="dim">{honestyLine(campaign)}</Mono>
    </Card>
  );
}

export default TonightCampaignCard;
