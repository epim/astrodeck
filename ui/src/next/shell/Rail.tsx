// Rail.tsx - the tablet/desktop copy of the tab bar (README "Platform": "the
// tab bar becomes a left icon rail").
//
// 72 px wide, label under the icon, and the SAME incident dot on Session. Not a
// second navigation model: same hubs, same order, same vocabulary - what
// changes at the breakpoint is density, never what things are called.

import type { JSX } from "react";
import { NxIcon } from "../icons";
import { nav, type HubId, type Route } from "../router";
import { HUB_META, HUB_ORDER } from "../hubs";
import { useSessionDot } from "../hubs/session/crossHub";
import { rememberedWeatherSub } from "./subContext";

export function Rail({ route, nowMs }: { route: Route; nowMs: number }): JSX.Element {
  // The SAME derivation the tab bar reads (`hubs/session/crossHub.ts`, plan
  // E.2), so the two navigations can never disagree about whether something is
  // wrong on the Session hub.
  const dot = useSessionDot(nowMs);

  return (
    <nav className="nx-rail" aria-label="Hubs" data-testid="rail">
      {HUB_ORDER.map((id: HubId) => {
        const meta = HUB_META[id];
        const active = route.hub === id;
        const alert = id === "session" && dot != null;
        return (
          <button
            key={id}
            type="button"
            className="nx-rail-btn"
            data-active={active ? "true" : "false"}
            data-testid={`rail-${id}`}
            aria-current={active ? "page" : undefined}
            aria-label={alert ? dot!.label : undefined}
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
          </button>
        );
      })}
    </nav>
  );
}
