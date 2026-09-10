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
//
// WEATHER REOPENS WHERE IT WAS LEFT. The other five hubs have one screen worth
// returning to; Weather has three, and which one matters depends on why you are
// looking (the radar during a front, the dome while deciding what to point at).
// The hub writes its last section to localStorage and the tab reads it, so the
// tap lands where the last one did rather than resetting to CONDITIONS each
// time. A deep link still wins: `WeatherHub` only restores when the hash named
// no section.

import type { JSX } from "react";
import { NxIcon } from "../icons";
import { nav, type HubId, type Route } from "../router";
import { HUB_META, HUB_ORDER } from "../hubs";
import { useSessionDot } from "../hubs/session/crossHub";
import { rememberedWeatherSub } from "./subContext";

export function TabBar({ route, nowMs }: { route: Route; nowMs: number }): JSX.Element {
  // ONE derivation for the dot, shared with the rail and the Session hub's own
  // card (`hubs/session/crossHub.ts`, plan E.2). It already returns null on the
  // Session hub itself: the tab you are looking at does not need to be told.
  const dot = useSessionDot(nowMs);

  return (
    <nav className="nx-tabbar" aria-label="Hubs" data-testid="tabbar">
      {HUB_ORDER.map((id: HubId) => {
        const meta = HUB_META[id];
        const active = route.hub === id;
        const alert = id === "session" && dot != null;
        return (
          <button
            key={id}
            type="button"
            className="nx-tab"
            data-active={active ? "true" : "false"}
            data-testid={`tab-${id}`}
            aria-current={active ? "page" : undefined}
            aria-label={alert ? dot!.label : meta.label}
            onClick={() => nav.hub(id, id === "weather" ? rememberedWeatherSub() : undefined)}
          >
            <NxIcon name={meta.icon} size={20} />
            {active && <span className="nx-tab-label">{meta.label}</span>}
            {alert && (
              <span
                className="nx-tab-dot"
                style={{ background: dot!.color, boxShadow: `0 0 8px ${dot!.color}` }}
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
