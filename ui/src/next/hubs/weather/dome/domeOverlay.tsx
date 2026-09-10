// domeOverlay.tsx - the wind arrow, the legend and the notes that go around the
// mounted sky dome.
//
// WHY THIS IS NOT AN OVERLAY ON THE CANVAS (plan F.6, extended - named in the
// task report). The plan's shape was an SVG registered over `SkyDomePanel`'s
// canvas carrying the horizon fill, the +30 min ghost tiles and wind arrows in
// the dome's own tilted-orthographic projection. Two facts on disk make that
// unbuildable without forking the panel:
//
//   1. `SkyDomePanel` (`components/cloudmap/SkyDomePanel.tsx:57-60`) takes
//      `{pointing, target}` and NOTHING else. `yawDeg` is its own `useState`
//      (:69) and there is no hook out of it - deliberately, so a 2 s status
//      frame cannot reset a drag. An overlay drawn at yaw 0 over a dome the
//      operator has turned 90 degrees puts every mark on the wrong part of the
//      sky, and the marks in question are "where the obstruction is" and "which
//      way the cloud is going".
//   2. The canvas's radius and centre come from three padding constants private
//      to `SkyDome.tsx:120-127`. Re-deriving them is a copy that drifts silently
//      the first time either file is retuned.
//
// F.6's rule is that an obstruction drawn a few degrees off is worse than none,
// so the geometry stays with the panel and the new chrome says the things it
// can say truthfully: which way the cloud is drifting (a compass rose, north-up,
// in its own box - not a claim about a position on the sphere), what the marks
// on the dome mean, and what is NOT drawn on it.

import type { JSX } from "react";
import { occlusionFill } from "../../../../lib/domeProjection";
import { Mono } from "../../../ui";
import type { WeatherNow } from "../../../../types";
import { drift } from "../conditions/verdict";

/** The prototype's own cloud-base default, used only when the feed carries no
 *  `cloud_base_m` - and said out loud when it is (README "Formulas to lift":
 *  "h = 2.2 km, v = 12 km/h in the prototype; take both from the weather feed"). */
export const ASSUMED_BASE_KM = 2.2;

export const WIND_ABSENT = "wind not reported";

/** What is deliberately absent from the dome picture, and where to get it.
 *  Said on screen rather than only in a commit message: a missing horizon line
 *  reads as "nothing is in the way" to anyone who does not know it was never
 *  drawn. */
export const NOT_DRAWN_NOTE =
  "Not drawn on the dome: the horizon profile, the +30 min drift and the target's "
  + "path to dawn. The dome is turned by dragging and the panel owns that angle, so an "
  + "overlay would sit at the wrong azimuth the moment you turned it - the horizon "
  + "profile is drawn in the Sky hub, and the path is written out below instead.";

export interface WindSummary {
  /** Compass bearing the cloud is drifting TOWARD, degrees. */
  towardDeg: number;
  fromName: string;
  towardName: string;
  kmh: number | null;
  baseKm: number;
  baseAssumed: boolean;
  /** The legend's own wording, e.g. "wind 12 km/h to NE at 2.2 km base". */
  label: string;
}

/** The wind, turned around.
 *
 *  `wind_dir_deg` is METEOROLOGICAL - where the wind comes FROM
 *  (`types.ts:2172-2174`). Every arrow this hub draws points along
 *  `towardDeg`, and there is no arrow at all when the feed carries no
 *  direction: an assumed 12 km/h from the prototype would be a drawing of a
 *  wind nobody measured. */
export function windSummary(now: WeatherNow | null | undefined): WindSummary | null {
  if (!now || now.wind_dir_deg === null || now.wind_dir_deg === undefined) return null;
  const d = drift(now.wind_dir_deg);
  const kmh = now.wind_kmh ?? null;
  const baseM = now.cloud_base_m;
  const baseAssumed = baseM === null || baseM === undefined;
  const baseKm = baseAssumed ? ASSUMED_BASE_KM : (baseM as number) / 1000;
  const speed = kmh === null ? "wind" : `wind ${Math.round(kmh)} km/h`;
  const base = baseAssumed
    ? `assumed ${ASSUMED_BASE_KM} km base`
    : `${baseKm.toFixed(1)} km base`;
  return {
    towardDeg: d.towardDeg,
    fromName: d.from,
    towardName: d.toward,
    kmh,
    baseKm,
    baseAssumed,
    label: `${speed} ${d.from} to ${d.toward} at ${base}`,
  };
}

/** A north-up arrow along a compass bearing. Its own box, not a mark on the
 *  dome: a rose that is always north-up cannot be mis-registered against a
 *  sphere the reader has turned. */
export function WindArrow({ towardDeg, size = 24 }: {
  towardDeg: number;
  size?: number;
}): JSX.Element {
  const deg = Math.round(((towardDeg % 360) + 360) % 360);
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      role="img"
      aria-label={`cloud drifting toward ${deg} degrees`}
      data-testid="wx-wind-arrow"
      data-toward={String(deg)}
      style={{ flexShrink: 0 }}
    >
      <circle cx="12" cy="12" r="10.5" fill="none" stroke="var(--line)" strokeWidth="1" />
      <text
        x="12" y="4.6" textAnchor="middle" fontSize="5.5" fontFamily="IBM Plex Mono"
        fill="var(--text-faint)"
      >
        N
      </text>
      <g
        transform={`rotate(${deg} 12 12)`}
        stroke="var(--text-dim)"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      >
        <path d="M12 17.5V7.5" />
        <path d="M8.4 10.6L12 7L15.6 10.6" />
      </g>
    </svg>
  );
}

interface Key {
  label: string;
  swatch: "fill" | "hatch" | "ring" | "cross" | "line";
  color: string;
  dash?: string;
}

/** The legend describes THE CANVAS THAT IS MOUNTED, not the prototype's.
 *
 *  The design's legend names six marks - cloud hold, obstruction, below floor,
 *  deck in 30 min, a 25 degree floor ring and wind arrows - that
 *  `components/cloudmap/SkyDome.tsx` does not draw. Printing them anyway would
 *  make the legend decoration, and a legend that names marks the picture does
 *  not have is worse than no legend: the reader goes looking for a red patch
 *  that is not there and concludes the sky is clear. Colours below are lifted
 *  from `SkyDome.tsx:196-266` and `lib/domeProjection.ts:205-215`. */
function keys(hasTarget: boolean): Key[] {
  const out: Key[] = [
    { label: "clear", swatch: "fill", color: occlusionFill(0.0) },
    { label: "patchy", swatch: "fill", color: occlusionFill(0.3) },
    { label: "socked in", swatch: "fill", color: occlusionFill(0.9) },
    { label: "no reading", swatch: "hatch", color: "rgba(155,168,190,0.62)" },
    { label: "plan targets", swatch: "ring", color: "rgba(150,235,190,0.8)" },
  ];
  if (hasTarget) out.push({ label: "this target", swatch: "ring", color: "rgba(140,200,255,0.75)" });
  out.push({ label: "where the scope points", swatch: "cross", color: "rgba(255,214,102,0.98)" });
  return out;
}

export function DomeLegend({ hasTarget, wind }: {
  hasTarget: boolean;
  wind: WindSummary | null;
}): JSX.Element {
  return (
    <div
      style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}
      data-testid="wx-dome-legend"
    >
      {keys(hasTarget).map((k) => (
        <span key={k.label} style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <Swatch k={k} />
          <Mono size={10} tone="dim">{k.label}</Mono>
        </span>
      ))}
      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {wind ? <WindArrow towardDeg={wind.towardDeg} size={22} /> : null}
        <Mono size={10} tone="dim">{wind ? wind.label : WIND_ABSENT}</Mono>
      </span>
    </div>
  );
}

function Swatch({ k }: { k: Key }): JSX.Element {
  if (k.swatch === "fill") {
    return (
      <span style={{
        width: 12, height: 8, display: "inline-block",
        background: k.color, border: "1px solid var(--line)",
      }} />
    );
  }
  if (k.swatch === "hatch") {
    return (
      <svg width="12" height="8" viewBox="0 0 12 8" aria-hidden="true">
        <rect x="0" y="0" width="12" height="8" fill="rgba(120,130,150,0.10)" />
        <path d="M0 8L8 0M4 8L12 0" stroke={k.color} strokeWidth="1.4" />
      </svg>
    );
  }
  if (k.swatch === "cross") {
    return (
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <circle cx="6" cy="6" r="3.4" fill="none" stroke={k.color} strokeWidth="1.2" />
        <path d="M6 0.6V2.6M6 9.4V11.4M0.6 6H2.6M9.4 6H11.4" stroke={k.color} strokeWidth="1.2" />
      </svg>
    );
  }
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <circle cx="6" cy="6" r="4" fill="none" stroke={k.color} strokeWidth="1.4" />
    </svg>
  );
}
