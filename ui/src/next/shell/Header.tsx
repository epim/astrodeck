// Header.tsx - the row every hub wears (README "Cross-hub chrome").
//
// Wordmark, the flows shortcut with its saved-flow count, and the rig chips:
// what the camera is at, what the mount is doing, which backend is driving, who
// you are signed in as when that is not "admin", and whether the link is up.
//
// EVERY CHIP CARRIES A NUMBER OR A STATE. A chip that said "CAM" and nothing
// else would be a label for a device the user can already see in the Rig hub;
// the reason this row is worth 44 px on a phone is that it answers "is the
// sensor cold and is the mount tracking" without leaving the screen you are on.
//
// Width: nothing here is `flex-shrink: 0`. The old header's un-shrinkable row
// set the whole app's minimum width at 382 px and scrolled a 375 px phone
// sideways, taking the link indicator off-screen - the one indicator you need
// when the link is the problem. The chips give first, the wordmark last.
//
// NIGHT LIVES HERE, NOT IN SETTINGS (review #14). It was reachable only from
// SETTINGS - GENERAL - scroll to PHONE, i.e. four navigations to go red at the
// eyepiece, and the argument that already won this row a global brightness
// hatch in `NextApp` applies to it exactly: a screen you cannot read is a
// screen whose fix you cannot find. It is the LAST chip so the container's
// `overflow: hidden` (which cuts from the left, the row being end-justified)
// can never take it off screen, and it carries a 44 px touch target through
// `boundary.css` without making the row any taller.

import { useEffect, useRef, type JSX } from "react";
import { useStore, useStatus, useWsPhase, useTelemetryStale, useNight } from "../../store";
import { usePrincipalRole } from "../../lib/caps";
import { backendBadge, backendBadgeIsSim } from "../../lib/equipment";
import { Pill, Wordmark, type Tone } from "../ui";
import { NxIcon } from "../icons";
import { nav } from "../router";

/** The LED that rides inside a rig chip. Shape and text carry the state too -
 *  under `:root.night` every token is the same red, so a hue-only dot is not a
 *  state at all. */
function ChipLed({ tone, off, pulse }: { tone: Tone; off?: boolean; pulse?: boolean }): JSX.Element {
  return (
    <span
      className="nx-chip-led"
      data-tone={tone}
      data-led={off ? "off" : undefined}
      data-pulse={pulse ? "true" : undefined}
      aria-hidden="true"
    />
  );
}

function mountChip(
  m: { tracking: boolean; parked: boolean; slewing: boolean } | undefined,
  connected: boolean,
): { text: string; tone: Tone; off: boolean } {
  if (!connected || !m) return { text: "MOUNT OFF", tone: "dim", off: true };
  if (m.slewing) return { text: "MOUNT SLEW", tone: "accent", off: false };
  if (m.parked) return { text: "MOUNT PARKED", tone: "dim", off: true };
  if (m.tracking) return { text: "MOUNT TRACK", tone: "good", off: false };
  return { text: "MOUNT IDLE", tone: "dim", off: true };
}

export function Header(): JSX.Element {
  const status = useStatus();
  const wsPhase = useWsPhase();
  const telemetryStale = useTelemetryStale();
  const role = usePrincipalRole();
  // The saved-flow library. `cards` is the field flowsSlice keeps the library
  // in; `libraryLoaded` says whether it has ever been read, so an unloaded
  // library shows no count rather than a confident zero.
  const flowCount = useStore((s) => (s.flows.libraryLoaded ? s.flows.cards.length : null));

  // ...and something has to read it, or the pill shows a dash all night on a
  // rig with nine saved flows. ONE fetch per session, guarded by a ref rather
  // than by `libraryLoaded` (which stays FALSE on an error, so keying off it
  // would retry on every render for as long as the rig is unreachable) and
  // never re-fired: the library changes when the user edits it, and the Flows
  // screen reloads it then.
  const loadLibrary = useStore((s) => s.flowsLoadLibrary);
  const asked = useRef(false);
  useEffect(() => {
    if (asked.current) return;
    asked.current = true;
    void loadLibrary().catch(() => { /* offline: the pill keeps its dash */ });
  }, [loadLibrary]);

  const night = useNight();
  const toggleNight = useStore((s) => s.toggleNight);

  const camConnected = !!status?.connected?.camera?.connected;
  const mountConnected = !!status?.connected?.telescope?.connected;
  const temp = status?.camera?.temperature;
  // The degree sign is not decoration: a bare "CAM -10.0" beside "MOUNT TRACK"
  // reads as one more state word rather than a temperature (review #52, design
  // screenshot 13 shows `CAM -10°`).
  const camText = camConnected
    ? (typeof temp === "number" ? `CAM ${temp.toFixed(1)}°` : "CAM ON")
    : "CAM OFF";

  const mount = mountChip(status?.mount, mountConnected);

  const badge = backendBadge(status?.mode);
  const isSim = backendBadgeIsSim(status?.mode);

  const linkDown = wsPhase !== "up";

  return (
    <header className="nx-header">
      <Wordmark />
      <div className="nx-header-chips">
        {/* The flows shortcut. Purple, per the design, and it carries the count
            so the tap is worth making: "9 saved flows" is news, "flows" is a
            label for a destination already in the tab bar. */}
        <Pill
          tone="accent2"
          glyph={<NxIcon name="flows" size={12} />}
          onClick={() => nav.go("/session/flows")}
          ariaLabel={flowCount == null ? "My flows" : `My flows, ${flowCount} saved`}
          data-testid="header-flows"
        >
          {flowCount == null ? "-" : String(flowCount)}
        </Pill>

        <Pill
          tone={camConnected ? "good" : "dim"}
          glyph={<ChipLed tone={camConnected ? "good" : "dim"} off={!camConnected} />}
          data-testid="header-cam"
        >
          {camText}
        </Pill>

        <Pill
          tone={mount.tone}
          glyph={<ChipLed tone={mount.tone} off={mount.off} />}
          data-testid="header-mount"
        >
          {mount.text}
        </Pill>

        {badge && (
          <Pill tone={isSim ? "warn" : "dim"} dashed={isSim} data-testid="header-backend">
            {badge}
          </Pill>
        )}

        {/* Role. Admin is the default open-LAN posture and stays silent; every
            other role is stated, because a viewer whose controls are all locked
            deserves to know that before pressing one. */}
        {role !== "admin" && (
          <Pill
            tone={role === "viewer" ? "warn" : "dim"}
            glyph={<NxIcon name="eye" size={11} />}
            data-testid="header-role"
          >
            {role === "viewer" ? "VIEW ONLY" : "OPERATOR"}
          </Pill>
        )}

        {/* The link chip only exists when there is something wrong with the
            link. NO LINK means this screen is not being told anything; STALE
            means the socket is open and nothing has come down it. The rig keeps
            imaging through both. */}
        {linkDown && (
          <Pill tone="bad" glyph={<ChipLed tone="bad" pulse />} data-testid="header-link">
            NO LINK
          </Pill>
        )}
        {!linkDown && telemetryStale && (
          <Pill tone="warn" glyph={<ChipLed tone="warn" pulse />} data-testid="header-stale">
            STALE
          </Pill>
        )}

        {/* NIGHT. The chip TEXT is the state the screen is in, like every other
            chip in this row; the accessible name says what pressing it does,
            which is the half a screen reader would otherwise have to guess. */}
        <Pill
          tone={night ? "bad" : "dim"}
          glyph={<NxIcon name={night ? "moon" : "sun"} size={12} />}
          onClick={toggleNight}
          ariaLabel={night
            ? "Night mode is on. Switch to day mode"
            : "Day mode is on. Switch to red night-vision mode"}
          className="nx-night-toggle"
          data-testid="header-night"
        >
          {night ? "NIGHT" : "DAY"}
        </Pill>
      </div>
    </header>
  );
}
