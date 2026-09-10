// SubNav.tsx - the chips row under the banners: the hub's own sections.
//
// A sub-nav change REPLACES the history entry rather than pushing one
// (ARCHITECTURE.md section 3). Flipping NOW / GALLERY / FLOWS is looking around
// one screen; if each flip pushed, leaving the hub would cost one Back press per
// look, and the browser's Back button is the phone's system-level gesture.

import type { JSX } from "react";
import { useStore } from "../../store";
import { SubNav as SubNavChips, type Tone } from "../ui";
import { buildHash, nav, SUBS, type Route } from "../router";
import { HUB_META } from "../hubs";
import { useIncidents } from "./useIncidents";

/** The incident model paints in colour VALUES; a chip dot takes a tone token.
 *  This is the one place the two vocabularies meet, and it keeps the severity:
 *  a safety trip or a failed solve is red, a lost link is dim, everything else
 *  is amber - the same ladder `next/lib/incidents.ts` uses. */
function toneOf(kind: string): Tone {
  if (kind === "safety" || kind === "solve") return "bad";
  if (kind === "link") return "dim";
  return "warn";
}

export function SubNavBar({ route, nowMs }: { route: Route; nowMs: number }): JSX.Element | null {
  const flowCount = useStore((s) => (s.flows.libraryLoaded ? s.flows.cards.length : null));
  const incidents = useIncidents(nowMs);

  const meta = HUB_META[route.hub];
  const items = meta.subs({
    flowCount,
    // The chip wears the incident's colour for the same reason the tab dot
    // does: from inside the hub, "something is wrong on NOW" has to be visible
    // without opening NOW.
    incidentTone: incidents.length > 0 ? toneOf(incidents[0].kind) : null,
  });

  if (items.length === 0) return null;

  return (
    <SubNavChips
      items={items}
      value={route.sub}
      ariaLabel={`${meta.label} sections`}
      onChange={(id) => {
        if (!SUBS[route.hub].includes(id)) return;
        // Sheets close with the section change: a sheet opened from GALLERY has
        // no meaning over FLOWS, and leaving it up would be a sheet whose BACK
        // returns to a screen it was never on.
        nav.replace(buildHash({ hub: route.hub, sub: id, sheets: [], params: {} }));
      }}
      data-testid="subnav"
    />
  );
}
