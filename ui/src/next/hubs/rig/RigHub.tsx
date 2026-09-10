// RigHub.tsx - PLACEHOLDER (T0.1 ships the shell, not the hubs).
//
// The RIG hub's screens land here in a later task. Until then this renders
// a named empty state so a walk through the tab bar shows six distinguishable
// screens - a placeholder that said nothing would let a routing bug read as a
// working app.

import type { JSX } from "react";
import { EmptyCard } from "../../ui";

export function RigHub(): JSX.Element {
  return (
    <div data-testid="hub-rig">
      <EmptyCard
        title="RIG HUB NOT BUILT YET"
        hint="Every device, its state, and the manual capture bench."
      />
    </div>
  );
}
