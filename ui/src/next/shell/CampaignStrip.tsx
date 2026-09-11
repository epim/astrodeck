// CampaignStrip.tsx - the purple line under the banners while a multi-night
// campaign is live (README "Cross-hub chrome").
//
// THE NUMBERS ARE NOT DERIVED HERE. `hubs/session/crossHub.ts` owns them
// (`useCampaignStrip`, plan section E.1), because the Session hub's own ledger
// card reads the same fold - and a strip that counted nights one way while the
// ledger counted them another is not a cosmetic disagreement: the ledger is
// what an operator reads to decide whether tonight can be cut short. One
// derivation, two renderings.
//
// This file used to compose its own line from `sequence.session`, which carries
// `{id, name, count_mode, accepted, target}` and nothing about which night this
// is or how many hours are banked - so the strip said "41 frames banked" where
// the design says "night 2 of ~4 - 5.2 of 12 h banked". The hook has the flow's
// tonight payload and can say both.
//
// The hook also owns the two suppressions: no campaign, and the screen that IS
// the campaign (Session - Now). A link to where you already are is not
// information.

import type { JSX } from "react";
import { useCampaignStrip } from "../hubs/session/crossHub";

const TAG = "CAMPAIGN";
const SEP = " · ";

export function CampaignStrip(): JSX.Element | null {
  const strip = useCampaignStrip();
  if (!strip) return null;

  // The hook's line leads with the word the design renders as its own micro
  // label. Split it back off rather than printing "CAMPAIGN CAMPAIGN · ...".
  const rest = strip.line.startsWith(TAG + SEP) ? strip.line.slice(TAG.length + SEP.length) : strip.line;

  return (
    <button
      type="button"
      className="nx-camp"
      onClick={strip.onPress}
      aria-label={`${TAG}: ${rest}. Open the session.`}
      data-testid="campaign-strip"
    >
      <span className="nx-camp-dot" aria-hidden="true" />
      <span className="nx-camp-tag">{TAG}</span>
      <span className="nx-camp-line">{rest}</span>
      <span className="nx-camp-chev" aria-hidden="true">&rsaquo;</span>
    </button>
  );
}
