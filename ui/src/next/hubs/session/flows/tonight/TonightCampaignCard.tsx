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

import type { JSX } from "react";

import {
  memberStatus,
  type CampaignMember, type CampaignRead,
} from "../../../../../components/flows/TonightCampaign";
import { Card, Label, Mono } from "../../../../ui";

function MemberRow({ m }: { m: CampaignMember }): JSX.Element {
  // Shape and word as well as colour: a DONE row already differs by its text,
  // and the fill token follows rather than leads.
  const tone = m.banked === null ? "faint" : m.done ? "good" : "accent";
  const fill = m.done ? "var(--good)" : "var(--accent)";
  return (
    <div className="nx-tn-member" data-testid={`tonight-member-${m.name}`}>
      <div className="nx-tn-member-head">
        <span className="nx-tn-member-name">{m.name}</span>
        <span className="nx-tn-member-status" data-tone={tone}>{memberStatus(m)}</span>
      </div>
      <div
        className="nx-tn-member-track"
        role="progressbar"
        aria-label={`${m.name} campaign progress`}
        aria-valuemin={0}
        aria-valuemax={m.quota}
        aria-valuenow={m.banked ?? undefined}
        aria-valuetext={memberStatus(m)}
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
function honestyLine(campaign: CampaignRead): string {
  if (!campaign.has_pool) {
    return "Progress is per pool member, so a flow with a single TARGET has nothing to track here.";
  }
  if (!campaign.has_ledger) {
    return "Counts come from the session ledger on the rig. None was readable, so no figure above is claimed.";
  }
  return "Counts are accepted subs from the session ledger, folded into complete cycles: "
    + "a cycle counts only once every filter in the table has its sub.";
}

export function TonightCampaignCard({ campaign }: {
  campaign: CampaignRead | null;
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
        <Label size={10}>CAMPAIGN LEDGER</Label>
        <Mono size={10} tone="dim">
          {campaign.members.length === 0
            ? "no pool members"
            : `${campaign.members.length} pool members · ${campaign.quota} cycles each`}
        </Mono>
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
