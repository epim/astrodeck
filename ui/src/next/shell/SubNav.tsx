// SubNav.tsx - the chips row under the banners: the hub's own sections.
//
// A sub-nav change REPLACES the history entry rather than pushing one
// (ARCHITECTURE.md section 3). Flipping NOW / GALLERY / FLOWS is looking around
// one screen; if each flip pushed, leaving the hub would cost one Back press per
// look, and the browser's Back button is the phone's system-level gesture.
//
// The counts and dots come from `shell/subContext.ts` - one bag, assembled
// once, so `HUB_META[hub].subs(ctx)` stays a pure function of it and the chips
// can be tested without a store.

import type { JSX } from "react";
import { SubNav as SubNavChips } from "../ui";
import { buildHash, nav, SUBS, type Route } from "../router";
import { HUB_META } from "../hubs";
import { useSubContext } from "./subContext";

export function SubNavBar({ route, nowMs }: { route: Route; nowMs: number }): JSX.Element | null {
  const ctx = useSubContext(nowMs);

  const meta = HUB_META[route.hub];
  const items = meta.subs(ctx);

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
