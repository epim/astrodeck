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

// THE MONITOR TAB WEARS THE UNSEEN-ERROR COUNT. `store.unseenError` is bumped
// for every error line the engine logs while the log screen is closed, and it
// had no reader anywhere in this UI (review #12). Once a fatal's toast is
// dismissed and the line has scrolled past the 200-line ring, this badge is the
// only thing left that says something went wrong. It is a NUMBER, not a dot:
// "3" is a fact the dot cannot carry.

import type { JSX } from "react";
import { NxIcon } from "../icons";
import { useStore } from "../../store";
import { nav, type HubId, type Route } from "../router";
import { HUB_META, HUB_ORDER } from "../hubs";
import { useSessionDot } from "../hubs/session/crossHub";
import { rememberedWeatherSub } from "./subContext";

/** The accessible name for a tab, with whatever the badge is saying folded in.
 *  Shared with the rail so the two navigations cannot describe one tab two
 *  ways. The hub NAME always leads: dropping it while an incident is live left
 *  the Session tab announcing only the incident, so a screen reader could not
 *  say which tab it was on (review #51). */
export function tabLabel(
  hubLabel: string, incident: string | null, unseenError: number,
): string {
  if (incident) return `${hubLabel}, ${incident}`;
  if (unseenError > 0) {
    return `${hubLabel}, ${unseenError} unseen error${unseenError === 1 ? "" : "s"}`;
  }
  return hubLabel;
}

export function TabBar({ route, nowMs }: { route: Route; nowMs: number }): JSX.Element {
  // ONE derivation for the dot, shared with the rail and the Session hub's own
  // card (`hubs/session/crossHub.ts`, plan E.2). It already returns null on the
  // Session hub itself: the tab you are looking at does not need to be told.
  const dot = useSessionDot(nowMs);
  const unseenError = useStore((s) => s.unseenError);

  return (
    <nav className="nx-tabbar" aria-label="Hubs" data-testid="tabbar">
      {HUB_ORDER.map((id: HubId) => {
        const meta = HUB_META[id];
        const active = route.hub === id;
        const alert = id === "session" && dot != null;
        const errors = id === "monitor" ? unseenError : 0;
        return (
          <button
            key={id}
            type="button"
            className="nx-tab"
            data-active={active ? "true" : "false"}
            data-testid={`tab-${id}`}
            aria-current={active ? "page" : undefined}
            aria-label={tabLabel(meta.label, alert ? dot!.label : null, errors)}
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
            {errors > 0 && (
              <span className="nx-tab-badge" aria-hidden="true" data-testid="tab-monitor-errors">
                {errors > 99 ? "99+" : errors}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
