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
// WIDTH: THE ROW DECIDES, THE CHIPS DO NOT (measured 2026-09-10). The chips
// used to be `flex-shrink: 1` with `text-overflow: ellipsis`, which is the same
// thing as having no width policy at all: at 390 px every chip shrank a little
// and EVERY ONE of them lost its payload - "CAM ...", "MOUNT...", "S...", "D"
// for an admin, and "C...", "MO...", "S.", "V..." for a viewer. An ellipsised
// readout is worse than an absent one, because it still costs the row its width
// while telling you nothing.
//
// So the chips are now `nowrap` + `flex-shrink: 0` (shell.css) - each one is
// either whole or not there - and the ROW sheds payload in a fixed order as the
// glass narrows, all of it in CSS keyed off `data-bp` and the chips container's
// own `data-dense` flag:
//
//   1. the mount's state word    (`MOUNT TRACK` -> `MOUNT`, phone)
//   2. the wordmark's tracking   (.22em -> .14em, phone, as App.tsx already does)
//   3. the flows pill's count    (glyph only, phone + a role chip present)
//   4. the flows pill itself     (dense under 400 px, everyone under 360 px)
//   5. the row's own air         (gap 6->5->4 px, pill padding 8->6->5 px)
//   6. the role chip             (dense under 360 px, and only there)
//
// The flows pill goes first and goes furthest because it is the only thing in
// this row that is a SHORTCUT rather than a READOUT: the Flows screen is two
// taps away in the tab bar, whereas nothing else on the screen you are on tells
// you the sensor is at -10 degrees. The role chip goes last and only at 320 px,
// where the measured alternative was a camera chip clipped 49 px in and reading
// "10°" - a temperature without its minus sign, which is not a degraded readout
// but a wrong one. What survives at every width is the four readouts and the
// one control: `CAM -10°`, `MOUNT`, `SIM`, and the sun/moon button.
//
// And nothing here may set a min-content floor: `.nx-header-chips` keeps
// `min-width: 0` + `overflow: hidden`, so an un-shrinkable chip is CLIPPED by
// the row rather than widening the app. The old header's un-shrinkable row set
// the whole app's minimum width at 382 px and scrolled a 375 px phone sideways,
// taking the link indicator off-screen - the one indicator you need when the
// link is the problem. That is why the drop order above exists at all, and why
// `documentElement.scrollWidth == clientWidth` is checked at 320 as well as 390.
//
// NIGHT LIVES HERE, NOT IN SETTINGS (review #14). It was reachable only from
// SETTINGS - GENERAL - scroll to PHONE, i.e. four navigations to go red at the
// eyepiece, and the argument that already won this row a global brightness
// hatch in `NextApp` applies to it exactly: a screen you cannot read is a
// screen whose fix you cannot find. It is the LAST chip so the container's
// `overflow: hidden` (which cuts from the left, the row being end-justified)
// can never take it off screen, and it carries a 44 px touch target through
// `boundary.css` without making the row any taller.
//
// The night toggle and the role chip are written out as `.nx-pill` markup
// rather than through `<Pill>`: both need a `title`, which `Pill` does not
// forward, and the night toggle needs NO text child at all (its state is a
// glyph plus its accessible name). They wear the same class, so they are the
// same 26 px pill the design specifies.

import { useEffect, useRef, type JSX } from "react";
import { useStore, useStatus, useWsPhase, useTelemetryStale, useNight } from "../../store";
import { usePrincipalRole } from "../../lib/caps";
import type { PrincipalRole } from "../../types";
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

/** The mount chip, split into the part that always prints and the state word
 *  that only prints when there is room. Both halves stay in the DOM at every
 *  width; `shell.css` hides the state word at `data-bp="phone"`, which is what
 *  the design's own phone screenshots show (`MOUNT`, not `MOUNT TRACK`). */
function mountChip(
  m: { tracking: boolean; parked: boolean; slewing: boolean } | undefined,
  connected: boolean,
): { state: string; tone: Tone; off: boolean } {
  if (!connected || !m) return { state: "OFF", tone: "dim", off: true };
  if (m.slewing) return { state: "SLEW", tone: "accent", off: false };
  if (m.parked) return { state: "PARKED", tone: "dim", off: true };
  if (m.tracking) return { state: "TRACK", tone: "good", off: false };
  return { state: "IDLE", tone: "dim", off: true };
}

/** The role badge, when the role is worth stating. Four characters at most -
 *  "VIEW ONLY" and "OPERATOR" are 54 px and 48 px of monospace, which is a
 *  third of the space the four readouts need on a 390 px phone. The sentence
 *  the short form stands in for is the chip's accessible name and its tooltip,
 *  so nothing is lost, it is just not spelled out in the one row that has no
 *  room to spell anything out. */
function roleChip(role: PrincipalRole): { short: string; full: string } | null {
  if (role === "admin") return null;
  if (role === "viewer") {
    return { short: "VIEW", full: "Signed in as a viewer: view only, no rig control" };
  }
  if (role === "syncer") {
    return { short: "SYNC", full: "Signed in as a syncer: downloads frames, no rig control" };
  }
  return { short: "OP", full: "Signed in as an operator: runs the rig, cannot change its settings" };
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
  // The degree sign is not decoration: a bare "CAM -10" beside "MOUNT TRACK"
  // reads as one more state word rather than a temperature (review #52, design
  // screenshot 13 shows `CAM -10°`). And it is the WHOLE unit - no "C", no
  // "degC". The tenth of a degree went with it: a set point is whole degrees,
  // the tenth changes on every poll, and `-10.0°` is 12 px of a 390 px row
  // spent on a digit nobody acts on. The Rig hub's camera screen still has it.
  const camText = camConnected
    ? (typeof temp === "number" ? `CAM ${Math.round(temp)}°` : "CAM ON")
    : "CAM OFF";

  const mount = mountChip(status?.mount, mountConnected);

  const badge = backendBadge(status?.mode);
  const isSim = backendBadgeIsSim(status?.mode);

  const linkDown = wsPhase !== "up";

  const rc = roleChip(role);
  const nightLabel = night
    ? "Night mode is on. Switch to day mode"
    : "Day mode is on. Switch to red night-vision mode";

  return (
    <header className="nx-header">
      <Wordmark className="nx-header-wordmark" />
      {/* `data-dense` is the row telling the stylesheet that it is carrying an
          extra chip this session. It is the only thing about this row that
          varies per USER rather than per WIDTH, so it cannot be a media query;
          it is what turns "drop the flows count" on for a viewer at 390 px
          while an admin at 390 px keeps it. */}
      <div className="nx-header-chips" data-dense={rc ? "true" : undefined}>
        {/* The flows shortcut. Purple, per the design, and it carries the count
            so the tap is worth making: "9 saved flows" is news, "flows" is a
            label for a destination already in the tab bar. That is also why it
            is the first thing the row sheds when the readouts need the room. */}
        <Pill
          tone="accent2"
          glyph={<NxIcon name="flows" size={12} />}
          onClick={() => nav.go("/session/flows")}
          ariaLabel={flowCount == null ? "My flows" : `My flows, ${flowCount} saved`}
          className="nx-flows-pill"
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
          MOUNT<span className="nx-mount-state">{` ${mount.state}`}</span>
        </Pill>

        {badge && (
          <Pill tone={isSim ? "warn" : "dim"} dashed={isSim} data-testid="header-backend">
            {badge}
          </Pill>
        )}

        {/* Role. Admin is the default open-LAN posture and stays silent; every
            other role is stated, because a viewer whose controls are all locked
            deserves to know that before pressing one. `role="img"` + the label
            is what makes the four-letter short form readable to a screen
            reader as the whole sentence - a bare <span> has no accessible name
            of its own to carry one. */}
        {rc && (
          <span
            className="nx-pill nx-role-chip"
            data-tone={role === "viewer" ? "warn" : "dim"}
            role="img"
            aria-label={rc.full}
            title={rc.full}
            data-testid="header-role"
          >
            <span className="nx-pill-glyph"><NxIcon name="eye" size={11} /></span>
            <span className="nx-pill-text">{rc.short}</span>
          </span>
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

        {/* NIGHT. Icon-only: the sun/moon glyph IS the state, and the word
            beside it cost 23 px that four readouts needed more (it was the "D"
            in the measured 390 px row - a truncated "DAY", which is not a state
            at all). The accessible name and the tooltip say both halves: what
            the screen is in now, and what pressing it does. */}
        <button
          type="button"
          className="nx-pill nx-night-toggle"
          data-tone={night ? "bad" : "dim"}
          onClick={toggleNight}
          aria-label={nightLabel}
          title={nightLabel}
          data-testid="header-night"
        >
          <span className="nx-pill-glyph"><NxIcon name={night ? "moon" : "sun"} size={14} /></span>
        </button>
      </div>
    </header>
  );
}
