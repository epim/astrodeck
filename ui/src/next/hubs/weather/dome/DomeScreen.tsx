// DomeScreen.tsx - WEATHER · SKY, the cloud dome (plan section A.3).
//
// `components/cloudmap/SkyDomePanel` is MOUNTED WHOLE. Every behaviour in it is
// a defect this codebase has already paid for once: the dead-feed caption at
// three consecutive failures, an age readout that keeps ticking instead of
// freezing into a memory, the four-state precedence dead > off > never > stale >
// fresh, the yaw held in the panel so a 2 s status frame cannot reset a drag,
// and the beam-versus-cell caveat that says a gap narrower than a satellite cell
// cannot appear on the picture at all. Redrawing the dome would re-open all of
// them for a different set of tiles.
//
// What this file adds is the words around it, and the three marks that go ON it
// through the panel's `overlay` slot - the horizon profile, the +30 min cloud
// ghosts and the target's path to dawn, each drawn with the geometry the canvas
// published rather than a re-derivation (see `domeOverlay.tsx`'s header for the
// argument that used to forbid this and what retired it).

import { useEffect, useMemo, useState, type CSSProperties, type JSX } from "react";
import {
  SkyDomePanel, type DomeOverlayArgs,
} from "../../../../components/cloudmap/SkyDomePanel";
import { getSite } from "../../../../api/site";
import { altAzOf, lstHours } from "../../../../lib/altaz";
import { useCan } from "../../../../lib/caps";
import { useLock } from "../../../lib/gateHook";
import { useConfig, useMount, usePlan, useSeq, useSite, useWeather } from "../../../../store";
import { horizonAltAt, isObstructed, summary, type HorizonPoint } from "../../../lib/horizonModel";
import { D2R } from "../../sky/finder/equatorial";
import { walkTrack, type TrackSample } from "../../sky/finder/track";
import { nav, useRoute } from "../../../router";
import { ActionButton, Card, Label, Mono } from "../../../ui";
import type { VisibilityNight } from "../../../../types";
import { contextTarget, fetchVisibility, targetNotes } from "../conditions/moon";
import {
  DomeLegend, DomeOverlay, windSummary, type DrawnMarks,
} from "./domeOverlay";

/** The clause a role without `view.site_precise` gets instead of the path. The
 *  path is a function of the site's latitude and longitude; drawing one from a
 *  guessed site would be an arc across the sky that no object takes. */
export const PATH_NEEDS_SITE =
  "the path to dawn needs the site's coordinates - your role gets it in words above";

const NO_MARKS: DrawnMarks = { horizon: false, ghosts: false, path: false };

export const CLOUDMAP_OFF_TITLE = "The cloud model is off.";
export const CLOUDMAP_OFF_HINT = "Turn it on in Cloud map settings.";

const COL: CSSProperties = {
  display: "flex", flexDirection: "column", gap: 10, padding: "0 2px 24px",
};

export function DomeScreen(): JSX.Element {
  const weather = useWeather();
  const config = useConfig();
  const site = useSite();
  const mount = useMount();
  const route = useRoute();
  const seq = useSeq();
  const plan = usePlan();
  const canSiteDerived = useCan("view.site_derived");
  // Same gate as the hub header's gear: one sheet, one rule for every door.
  const { lockedReason: settingsLock, onExplain } = useLock({ cap: "config.site_optics" });

  const [points, setPoints] = useState<HorizonPoint[] | null>(null);
  const [night, setNight] = useState<VisibilityNight | null>(null);

  // The ACTIVE horizon polyline - `config.safety.horizon`, the line the engine
  // is actually gating slews with, not the library's copy. Only GET /api/site
  // carries it (`types.ts:1768-1774`), so the status block's site is not enough.
  // view.status: every role can read it.
  useEffect(() => {
    let gone = false;
    void getSite()
      .then((res) => {
        if (gone) return;
        const raw = res?.site?.horizon_points;
        setPoints(Array.isArray(raw)
          ? raw.map(([az, alt]) => ({ az, alt }))
          : []);
      })
      .catch(() => { if (!gone) setPoints(null); });
    return () => { gone = true; };
  }, []);

  const target = useMemo(
    () => contextTarget(seq?.target ?? null, route.params, plan?.targets),
    [seq?.target, route.params, plan?.targets],
  );

  useEffect(() => {
    if (!canSiteDerived || !target) { setNight(null); return; }
    let gone = false;
    void fetchVisibility(target.ra_hours, target.dec_deg)
      .then((n) => { if (!gone) setNight(n); })
      .catch(() => { if (!gone) setNight(null); });
    return () => { gone = true; };
  }, [canSiteDerived, target]);

  const nowTs = Date.now() / 1000;

  // Where the scope is looking: the dome's pierce point, drawn by the panel
  // from this prop. Below the horizon is not a pointing anyone can image
  // through, and the panel treats alt < 0 as "no marker".
  const pointing = mount && typeof mount.alt === "number" && typeof mount.az === "number"
    && mount.alt >= 0
    ? { alt: mount.alt, az: mount.az }
    : null;

  // The target's own marker needs alt/az, which needs the site's coordinates -
  // view.site_precise. An operator holds weather but not coordinates, and
  // `weather.site_lat/lon` is the ONE deliberate exception, reserved for the
  // radar map's centring; a second consumer of it would make it a general
  // coordinate channel. So the marker is admin-only and the words below carry
  // the target for everyone else.
  const lat = typeof site?.latitude === "number" ? site.latitude : null;
  const lon = typeof site?.longitude === "number" ? site.longitude : null;
  const targetAltAz = useMemo(() => {
    if (!target || lat === null || lon === null) return null;
    const { altDeg, azDeg } = altAzOf(target.ra_hours, target.dec_deg, lat, lon, Date.now() / 1000);
    return { alt: altDeg, az: azDeg, name: target.name };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, lat, lon]);

  const wind = windSummary(weather?.now ?? null);

  /** What the overlay actually drew, reported back by it. The legend may name
   *  only those marks: an entry for a horizon profile that is not on the dome
   *  sends the reader looking for a red band that is not there. */
  const [drawn, setDrawn] = useState<DrawnMarks>(NO_MARKS);

  // The path to dawn, walked at the sidereal rate from the target's hour angle
  // NOW - the same walk the Sky finder's arc uses, so the two hubs cannot
  // disagree about where the object goes. It needs the site's own coordinates
  // (the hour angle is longitude, the altitude is latitude), so a role without
  // `view.site_precise` gets no path at all and the clause below says why: an
  // arc drawn from a guessed site is a path no object takes.
  const track = useMemo<TrackSample[] | null>(() => {
    if (!target || lat === null || lon === null) return null;
    const dawn = night?.dark_end_unix;
    if (typeof dawn !== "number") return null;
    const nowSec = Date.now() / 1000;
    const hoursToDawn = (dawn - nowSec) / 3600;
    if (!(hoursToDawn > 0)) return null;
    const lst = lstHours(lon, nowSec);
    const samples = walkTrack(target.dec_deg * D2R, (lst - target.ra_hours) * 15 * D2R, {
      latDeg: lat,
      hoursToDawn,
      horizon: points ?? [],
      horizonMinDeg: site?.horizon_min_deg ?? 0,
      maskOn: true,
      // No forecast is consulted here, so nothing is ever coloured "cloud
      // hold": an invented hold is worse than none, and the cloud on this
      // screen is the dome underneath, not a scalar.
      holdAt: () => false,
    });
    return samples.length > 0 ? samples : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, lat, lon, night?.dark_end_unix, points, site?.horizon_min_deg]);

  // The cloud model's switch lives in config, so the off state is known without
  // asking the model (which would be a request for a feature that is off). When
  // config has not arrived, nothing is claimed and the panel speaks for itself.
  const cloudmapOff = config?.cloudmap ? !config.cloudmap.enabled : false;

  const clauses: string[] = [];
  if (target) {
    const path = targetNotes(night, nowTs);
    if (path) clauses.push(`${target.name}: ${path}`);
    if (targetAltAz && points && points.length > 0) {
      clauses.push(
        isObstructed(points, targetAltAz.alt, targetAltAz.az)
          ? `${target.name} is behind your horizon profile right now `
            + `(${Math.round(horizonAltAt(points, targetAltAz.az))}° at that azimuth)`
          : `${target.name} clears your horizon profile at this azimuth`,
      );
    }
    if (lat === null || lon === null) clauses.push(PATH_NEEDS_SITE);
  }
  if (points === null) clauses.push("horizon profile could not be read");
  else if (points.length === 0) clauses.push("no horizon profile drawn for this site");
  else clauses.push(`horizon profile ${summary(points)}`);

  return (
    <div data-testid="wx-sky" style={COL}>
      {cloudmapOff && (
        <Card tone="dashed" data-testid="wx-cloudmap-off">
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <Label size={11}>CLOUD MODEL OFF</Label>
            <Mono size={11} tone="dim">{`${CLOUDMAP_OFF_TITLE} ${CLOUDMAP_OFF_HINT}`}</Mono>
            <ActionButton
              kind="secondary"
              onPress={() => nav.sheet("cloudmap")}
              lockedReason={settingsLock}
              onExplain={onExplain}
              data-testid="wx-cloudmap-cta"
            >
              CLOUD MAP SETTINGS
            </ActionButton>
          </div>
        </Card>
      )}

      <Card>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{
            display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8,
          }}>
            <Label size={10}>SKYDOME</Label>
            <Mono size={10} tone="dim">
              {target ? target.name : "no target picked"}
            </Mono>
          </div>

          <SkyDomePanel
            pointing={pointing}
            target={targetAltAz}
            // The Card above already says SKYDOME and draws the border. Without
            // this the legacy `Panel` drew a second title ("Sky dome") inside a
            // second box - two titles for one dome, and legacy chrome in a
            // screen the design specifies.
            chrome="bare"
            overlay={(a: DomeOverlayArgs) => (
              <DomeOverlay
                args={a}
                horizon={points}
                wind={wind}
                track={track}
                targetName={target?.name ?? null}
                onDrawn={setDrawn}
              />
            )}
          />

          <DomeLegend hasTarget={!!targetAltAz} wind={wind} drawn={drawn} />

          <Mono size={10} tone="dim">{clauses.join(" · ")}</Mono>
        </div>
      </Card>
    </div>
  );
}
