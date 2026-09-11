// SessionHub.tsx - the SESSION hub's body: NOW, GALLERY or FLOWS.
//
// THE SUB-NAV IS NOT HERE, deliberately. The shell renders it once for every
// hub (`shell/SubNav.tsx`, from `HUB_META[hub].subs`), which is what lets the
// Session chip wear the top incident's colour from inside any hub and what
// keeps a section change a REPLACE rather than a push. A second chips row in
// this file would be a second source of truth for which section is open, and
// the two would disagree the first time a deep link arrived with a section the
// registry does not know.
//
// So this is the switch and nothing else: three screens, each owning its own
// data, none of them told anything by this file. An unknown `sub` cannot reach
// here (the router resolves it against `SUBS.session` and falls back to `now`),
// but the default arm exists anyway - a hub that rendered blank on a section
// name it did not recognise would look exactly like a broken screen.

import type { JSX } from "react";

import { useRoute } from "../../router";
import { NowScreen } from "./now";
import { GalleryScreen } from "./gallery/GalleryScreen";
import { FlowsScreen } from "./flows/FlowsScreen";

export function SessionHub(): JSX.Element {
  const route = useRoute();
  return (
    <div data-testid="hub-session" data-sub={route.sub}>
      {route.sub === "gallery" ? <GalleryScreen />
        : route.sub === "flows" ? <FlowsScreen />
          : <NowScreen />}
    </div>
  );
}
