// WeatherHub.tsx - PLACEHOLDER (T0.1 ships the shell, not the hubs).
//
// The WEATHER hub's screens land here in a later task. Until then this renders
// a named empty state so a walk through the tab bar shows six distinguishable
// screens - a placeholder that said nothing would let a routing bug read as a
// working app.

import type { JSX } from "react";
import { EmptyCard } from "../../ui";

export function WeatherHub(): JSX.Element {
  return (
    <div data-testid="hub-weather">
      <EmptyCard
        title="WEATHER HUB NOT BUILT YET"
        hint="Conditions, the cloud dome and the radar for this site."
      />
    </div>
  );
}
