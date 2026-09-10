// MonitorHub.tsx - the MONITOR hub: LIVE, LOG, ALERTS.
//
// The hub itself is a switch. The sub-nav chips are the shell's (it renders
// `HUB_META.monitor.subs`); everything below the chips is one of the three
// screens, and each screen owns its own reads - there is no hub-level state to
// share, and a hub that fetched on behalf of its screens would fetch for the
// two nobody is looking at.
//
// `data-testid="hub-monitor"` is kept from the placeholder: the shell's own
// tests assert a walk through the six tabs finds six distinguishable screens,
// and the marker is what makes "the router works" different from "the router
// appears to work".

import type { JSX } from "react";
import { useRoute } from "../../router";
import { LiveScreen } from "./live/LiveScreen";
import { LogScreen } from "./log/LogScreen";
import { AlertsScreen } from "./alerts/AlertsScreen";

export function MonitorHub(): JSX.Element {
  const route = useRoute();
  const sub = route.hub === "monitor" ? route.sub : "live";
  return (
    <div data-testid="hub-monitor" data-sub={sub}>
      {sub === "log" ? <LogScreen />
        : sub === "alerts" ? <AlertsScreen />
          : <LiveScreen />}
    </div>
  );
}
