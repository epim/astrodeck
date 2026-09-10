// TabBar.tsx - the phone's six hubs (README "Information architecture").
//
// The active hub shows its label; the others are icon-only, which is what buys
// six 44 px targets on a 320 px screen. `aria-current="page"` carries the same
// fact to a screen reader, because on the inactive tabs the label is not there
// to be read.
//
// The Session tab wears a pulsing dot in the top incident's colour whenever an
// incident is live and the user is on another hub. That dot is the only thing
// on a Sky screen that says a run has stopped, so it is not decoration: it
// pulses (movement survives the near-monochrome night palette, where every
// token is the same red and a coloured dot is no longer a colour).

import type { JSX } from "react";
import { NxIcon } from "../icons";
import { nav, type HubId, type Route } from "../router";
import { HUB_META, HUB_ORDER } from "../hubs";
import { useIncidents } from "./useIncidents";

export function TabBar({ route, nowMs }: { route: Route; nowMs: number }): JSX.Element {
  const incidents = useIncidents(nowMs);
  const top = incidents[0] ?? null;

  return (
    <nav className="nx-tabbar" aria-label="Hubs" data-testid="tabbar">
      {HUB_ORDER.map((id: HubId) => {
        const meta = HUB_META[id];
        const active = route.hub === id;
        const alert = id === "session" && top != null && route.hub !== "session";
        return (
          <button
            key={id}
            type="button"
            className="nx-tab"
            data-active={active ? "true" : "false"}
            data-testid={`tab-${id}`}
            aria-current={active ? "page" : undefined}
            aria-label={alert ? `${meta.label}, ${top.title}` : meta.label}
            onClick={() => nav.hub(id)}
          >
            <NxIcon name={meta.icon} size={20} />
            {active && <span className="nx-tab-label">{meta.label}</span>}
            {alert && (
              <span
                className="nx-tab-dot"
                style={{ background: top.color, boxShadow: `0 0 8px ${top.color}` }}
                aria-hidden="true"
                data-testid="tab-session-dot"
              />
            )}
          </button>
        );
      })}
    </nav>
  );
}
