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
import type { DomeTrack } from "../finder";
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
  /**
   * Every arc to draw, brightest first - `SkyModel.dome.tracks`. Empty draws no
   * path at all, which is what a role with no site coordinates gets: an arc
   * from a guessed site is a path no object takes (see `DomeScreen`'s
   * PATH_NEEDS_SITE for the same refusal in words).
   */
  tracks: DomeTrack[];
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
  canViewWeather, pointing, target, horizon, wind, tracks,
  height, lockId, onExplain,
}: DomeCardProps): JSX.Element {
  const arcs = tracks;

  /** The arc for a point with no catalogue object under it, if the reticle is
   *  on one. It is what makes WEATHER carry coordinates rather than a name. */
  const aimed = arcs.find((t) => t.point !== null) ?? null;

  /**
   * WEATHER OPENS ON WHAT THIS CARD IS ABOUT, and that is not always a name.
   * A locked object travels as its id; a bare patch of sky has no id, so it
   * travels as `?ra=&dec=` and `DomeScreen` draws the same arc from the same
   * two numbers. Without this the one case the aimed track exists for - "I am
   * pointed at nothing catalogued and want the full picture" - lost the aim the
   * moment the reader pressed the button that promised more of it.
   */
  const toWeather = () => {
    const q = lockId
      ? `?target=${encodeURIComponent(lockId)}`
      : aimed
        ? `?ra=${aimed.point!.ra_hours.toFixed(5)}&dec=${aimed.point!.dec_deg.toFixed(4)}`
        : "";
    nav.go(`/weather/sky${q}`);
  };

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
                  tracks={arcs}
                  // The canvas writes the locked target's name above its ring
                  // (`SkyDome`'s `target` marker), and only when it is up.
                  labelledOnCanvas={
                    target && target.alt >= 0 ? (target.name ?? null) : null
                  }
                />
              )}
            />
          ) : (
            <Mono size={11} tone="dim" data-testid="sky-dome-locked">
              {DOME_NEEDS_WEATHER}
            </Mono>
          )}

          {/* WHICH ARC IS WHICH. Only the bright one is labelled on the
              picture - six labels on a 280 px hemisphere is a page of text -
              so the dim ones are named here, in the order they are drawn,
              which is the ranking's own order. Without it the extra arcs are
              decoration: the reader can see that four things are up and not
              which four. */}
          {canViewWeather && arcs.length > 0 && (
            <Mono size={10} tone="dim" data-testid="sky-dome-tracks">
              {`to dawn: ${arcs.map((t) => t.label || "unnamed").join(" · ")}`}
            </Mono>
          )}
        </div>
      </Card>
    </div>
  );
}
