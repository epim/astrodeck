// DomeCard.tsx - the hemisphere, on the Sky finder (decision D-SKY-2).
//
// WHAT THIS REVERSES. `StatusRow`'s header used to carry the argument for NOT
// having this card: "the Weather hub owns the dome, and two hemisphere
// renderers in two hubs would be two truths about the same sky". That was true
// of two RENDERERS. It is not true of two mounts of one renderer, which is what
// this is: `components/cloudmap/SkyDomePanel` whole, with the same overlay the
// Weather hub draws over it (`hubs/weather/dome/domeOverlay`). The dead-feed
// caption, the four-state freshness precedence, the age that extrapolates
// instead of freezing, the beam-versus-cell caveat and the yaw held above the
// canvas are all one implementation, so the two screens cannot disagree.
//
// WHY IT IS HERE AT ALL. The finder answers "what can I point at"; the dome
// answers "is there cloud between me and it, and is it coming this way". The
// pill above used to answer the second by leaving the hub - which is a fair
// answer for the full screen and a poor one for the glance you take between
// two frames. So: the picture here, the legend and the words on `#/weather/sky`
// behind WEATHER, and the pill above now scrolls to this card rather than
// navigating away from it.
//
// THE GATE IS THE POINT OF THE `canViewWeather` PROP. `/api/cloudmap*` needs
// `view.weather` (`app.py:2590-2612`) and the panel polls three of them a
// minute for as long as it is mounted. Rendering it for a role that cannot read
// them would put three 403s a minute into the log all night - the same defect
// 3f10681c closed for the radar map - so a role without the capability gets the
// dashed card and NO MOUNT, not a mounted panel that fails quietly.

import type { JSX } from "react";
import { SkyDomePanel, type DomeOverlayArgs } from "../../../../components/cloudmap/SkyDomePanel";
import { accessPhrase } from "../../../../lib/caps";
import { nav } from "../../../router";
import { ActionButton, Card, Label, Mono } from "../../../ui";
import type { HorizonPoint } from "../../../lib/horizonModel";
import type { TrackSample } from "../finder";
import { DomeOverlay, type WindSummary } from "../../weather/dome/domeOverlay";

/** The card's anchor, its testid and its probe name, written once. `SkyHub`'s
 *  SKYDOME pill scrolls to exactly this id. */
export const DOME_CARD_ID = "sky-dome-card";

/** What a role without `view.weather` is told, in the card's own body. It names
 *  the roles that DO hold the capability from `ROLE_CAPS` rather than a
 *  hand-written guess at them - see `accessPhrase`. */
export const DOME_NEEDS_WEATHER = `The cloud dome needs ${accessPhrase("view.weather")}.`;

/** The same sentence on the WEATHER button, which leads to a hub gated on the
 *  same capability (`WeatherHub.tsx:56`). It is shown locked rather than hidden
 *  (ARCHITECTURE section 8): a viewer sees the operator's screen with the
 *  reasons on it, never a screen with holes in it. */
export const WEATHER_LOCKED = `needs ${accessPhrase("view.weather")}`;

export interface DomeCardProps {
  canViewWeather: boolean;
  /** Where the scope is looking. Below the horizon is not a pointing, and the
   *  panel draws no marker for it. */
  pointing: { alt: number; az: number } | null;
  /** The locked target's place on the dome, or null when this role cannot
   *  resolve alt/az (no site coordinates) or nothing is locked. */
  target: { alt: number; az: number; name?: string } | null;
  /** The site's ACTIVE horizon polyline. Empty or null draws no profile. */
  horizon: HorizonPoint[] | null;
  wind: WindSummary | null;
  /** The lock's walk to dawn. Null draws no path - never a path from a guessed
   *  site (see `DomeScreen`'s PATH_NEEDS_SITE for the same refusal in words). */
  track: TrackSample[] | null;
  targetName: string | null;
  /** Canvas height in CSS px, worked out by the hub from the measured column -
   *  see `SkyHub`'s `domeHeight`. */
  height: number;
  /** Carried into `?target=` so WEATHER opens on what the finder is aimed at
   *  rather than on nothing. */
  lockId: string | null;
  onExplain: (reason: string) => void;
}

/**
 * The last card on the finder.
 *
 * ONE YAW PER INSTANCE, and that is deliberate rather than a limitation: the
 * yaw is `SkyDomePanel`'s own `useState` (its comment says why it is held above
 * the canvas), so this card and the Weather hub's screen each turn their own
 * dome. Sharing one yaw across two hubs would mean a drag here silently
 * re-orienting a screen the operator is not looking at.
 */
export function DomeCard({
  canViewWeather, pointing, target, horizon, wind, track, targetName,
  height, lockId, onExplain,
}: DomeCardProps): JSX.Element {
  const toWeather = () =>
    nav.go(`/weather/sky${lockId ? `?target=${encodeURIComponent(lockId)}` : ""}`);

  return (
    <div id={DOME_CARD_ID} data-testid={DOME_CARD_ID} style={{ scrollMarginTop: 8 }}>
      <Card tone={canViewWeather ? "default" : "dashed"} padding={12}>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{
            display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8,
          }}>
            <Label>SKYDOME</Label>
            <ActionButton
              kind="ghost"
              onPress={toWeather}
              lockedReason={canViewWeather ? null : WEATHER_LOCKED}
              onExplain={onExplain}
              data-testid="sky-dome-weather"
            >
              WEATHER &rsaquo;
            </ActionButton>
          </div>

          {canViewWeather ? (
            <SkyDomePanel
              pointing={pointing}
              target={target}
              height={height}
              // The Card above owns the title and the border; see the panel's
              // `chrome` prop.
              chrome="bare"
              overlay={(a: DomeOverlayArgs) => (
                <DomeOverlay
                  args={a}
                  horizon={horizon}
                  wind={wind}
                  track={track}
                  targetName={targetName}
                />
              )}
            />
          ) : (
            <Mono size={11} tone="dim" data-testid="sky-dome-locked">
              {DOME_NEEDS_WEATHER}
            </Mono>
          )}
        </div>
      </Card>
    </div>
  );
}
