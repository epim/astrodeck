// SkyHub.tsx - PLACEHOLDER (T0.1 ships the shell, not the hubs).
//
// The SKY hub's screens land here in a later task. Until then this renders
// a named empty state so a walk through the tab bar shows six distinguishable
// screens - a placeholder that said nothing would let a routing bug read as a
// working app.

import type { JSX } from "react";
import { EmptyCard } from "../../ui";

export function SkyHub(): JSX.Element {
  return (
    <div data-testid="hub-sky">
      <EmptyCard
        title="SKY HUB NOT BUILT YET"
        hint="Point the phone at the sky, or pick from the ranked list."
      />
    </div>
  );
}
