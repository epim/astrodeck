// CampaignStrip.tsx - the purple line under the banners while a multi-night
// campaign is live (README "Cross-hub chrome").
//
// The design's line is `CAMPAIGN - M31 LRGB - night 2 of 4 - 5.2 of 12 h banked`.
// The engine publishes only part of that on the live socket: `sequence.session`
// carries `{id, name, count_mode, accepted, target}` and nothing about which
// night this is or how many hours are in the bank (the night count lives on
// `GET /api/sessions`, which the shell does not poll and must not start
// polling - a strip is not worth a request every 20 seconds on a field link).
//
// So the strip prints the parts it can READ and omits the rest. Inventing
// "night 1 of 1" from a session that never said so would be worse than a
// shorter line: the campaign ledger is the thing the user checks to decide
// whether tonight can be cut short, and a made-up denominator there is a
// decision made on fiction. The Session hub's own ledger (task T3) fetches the
// session row and fills the full line in.

import type { JSX } from "react";
import { useStore } from "../../store";
import { nav, type Route } from "../router";

export function CampaignStrip({ route }: { route: Route }): JSX.Element | null {
  const sequence = useStore((s) => s.sequence);

  const live = sequence.state === "running" || sequence.state === "paused" || sequence.state === "holding";
  const session = sequence.session;
  if (!live || !session) return null;

  // Hidden on Session - Now, which IS the campaign screen. Everywhere else it
  // is the way back to it.
  if (route.hub === "session" && route.sub === "now") return null;

  const bits: string[] = [];
  const what = session.target || session.name;
  if (what) bits.push(what);
  if (typeof session.accepted === "number") {
    bits.push(`${session.accepted} frame${session.accepted === 1 ? "" : "s"} banked`);
  }
  if (sequence.state === "paused") bits.push("paused");
  if (sequence.state === "holding" && sequence.hold) bits.push(`holding: ${sequence.hold}`);

  const line = bits.join(" - ");

  return (
    <button
      type="button"
      className="nx-camp"
      onClick={() => nav.go("/session/now")}
      aria-label={`Campaign: ${line}. Open the session.`}
      data-testid="campaign-strip"
    >
      <span className="nx-camp-dot" aria-hidden="true" />
      <span className="nx-camp-tag">CAMPAIGN</span>
      <span className="nx-camp-line">{line}</span>
      <span className="nx-camp-chev" aria-hidden="true">&rsaquo;</span>
    </button>
  );
}
