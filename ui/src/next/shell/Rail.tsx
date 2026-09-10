// Rail.tsx - the tablet/desktop copy of the tab bar (README "Platform": "the
// tab bar becomes a left icon rail").
//
// 72 px wide, label under the icon, and the SAME incident dot on Session. Not a
// second navigation model: same hubs, same order, same vocabulary - what
// changes at the breakpoint is density, never what things are called.

import type { JSX } from "react";
import { NxIcon } from "../icons";
import { useStore } from "../../store";
import { nav, type HubId, type Route } from "../router";
import { HUB_META, HUB_ORDER } from "../hubs";
import { useSessionDot } from "../hubs/session/crossHub";
import { rememberedWeatherSub } from "./subContext";
import { tabLabel } from "./TabBar";

export function Rail({ route, nowMs }: { route: Route; nowMs: number }): JSX.Element {
  // The SAME derivation the tab bar reads (`hubs/session/crossHub.ts`, plan
  // E.2), so the two navigations can never disagree about whether something is
  // wrong on the Session hub. Same for the unseen-error count and for the
  // accessible name that folds both in.
  const dot = useSessionDot(nowMs);
  const unseenError = useStore((s) => s.unseenError);

  return (
    <nav className="nx-rail" aria-label="Hubs" data-testid="rail">
      {HUB_ORDER.map((id: HubId) => {
        const meta = HUB_META[id];
        const active = route.hub === id;
        const alert = id === "session" && dot != null;
        const errors = id === "monitor" ? unseenError : 0;
        return (
          <button
            key={id}
            type="button"
            className="nx-rail-btn"
            data-active={active ? "true" : "false"}
            data-testid={`rail-${id}`}
            aria-current={active ? "page" : undefined}
            // The rail renders its label, so a plain hub keeps the undefined
            // it always had and lets the visible text be the name; only a badge
            // needs the fuller sentence.
            aria-label={alert || errors > 0
              ? tabLabel(meta.label, alert ? dot!.label : null, errors)
              : undefined}
            onClick={() => nav.hub(id, id === "weather" ? rememberedWeatherSub() : undefined)}
          >
            <NxIcon name={meta.icon} size={20} />
            <span className="nx-rail-label">{meta.label}</span>
            {alert && (
              <span
                className="nx-rail-dot"
                style={{ background: dot!.color, boxShadow: `0 0 8px ${dot!.color}` }}
                data-testid="rail-session-dot"
                aria-hidden="true"
              />
            )}
            {errors > 0 && (
              <span className="nx-tab-badge" aria-hidden="true" data-testid="rail-monitor-errors">
                {errors > 99 ? "99+" : errors}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
