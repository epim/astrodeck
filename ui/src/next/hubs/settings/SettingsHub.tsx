// SettingsHub.tsx - the SETTINGS hub root (plan section C.1).
//
// The shell draws the GENERAL / USERS / ABOUT chips (`shell/SubNav.tsx` reads
// `HUB_META.settings.subs`), so this file does NOT re-render them; it reads
// `route.sub` and mounts the screen. USERS and ABOUT are T-SET-3's, imported
// from `./sheets/set3` - the same components the route table calls sub-screens
// rather than sheets, because they are full screens with their own sheets
// hanging off them.
//
// THE VERSIONS LINE IS HONEST-ABSENT (plan D.2). `/healthz` is the one
// unauthenticated route and the only place the engine's version comes from;
// while that call is in flight, or after it fails, the line reads `app <x>`
// alone. It never prints a placeholder engine version and never a dash where a
// version was promised - "the engine did not answer" and "the engine is version
// 0" are different claims, and only one of them is ever true here.
//
// One fetch on mount, no poll: the engine's version changes when the engine
// restarts, and a restart takes the WebSocket with it.

import { useEffect, useState, type JSX } from "react";
import { Mono } from "../../ui";
import { useRoute } from "../../router";
import { appVersion } from "../../lib/versions";
import { getHealth } from "../../../api/backends";
import { GeneralScreen } from "./general/GeneralScreen";
import { AboutScreen, UsersScreen } from "./sheets/set3";

// `.nx-body` gives the hub 16 px side padding and a 10 px gap between its OWN
// children; this root is one of them, so it repeats the gap for the screen it
// wraps rather than letting every group sit flush against the next.
const COL = { display: "flex", flexDirection: "column", gap: "10px" } as const;

const HEAD = {
  display: "flex", justifyContent: "space-between",
  alignItems: "baseline", gap: "10px",
} as const;

const TITLE = {
  fontFamily: '"Chakra Petch", sans-serif', fontWeight: 600,
  fontSize: "15px", letterSpacing: ".1em",
} as const;

export function SettingsHub(): JSX.Element {
  const route = useRoute();
  const [engine, setEngine] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void getHealth()
      .then((h) => { if (alive) setEngine(h.version || null); })
      .catch(() => { /* leave the engine clause off; never a placeholder */ });
    return () => { alive = false; };
  }, []);

  const versions = engine
    ? `engine ${engine} · app ${appVersion()}`
    : `app ${appVersion()}`;

  return (
    <div data-testid="hub-settings" style={COL}>
      <div style={HEAD}>
        <div style={TITLE}>SETTINGS</div>
        <span data-testid="settings-versions"><Mono size={10}>{versions}</Mono></span>
      </div>

      {route.sub === "users" ? (
        <UsersScreen />
      ) : route.sub === "about" ? (
        <AboutScreen />
      ) : (
        <GeneralScreen />
      )}
    </div>
  );
}
