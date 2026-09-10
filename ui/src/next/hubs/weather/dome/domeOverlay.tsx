// domeOverlay.tsx - what is drawn ON the sky dome (the horizon profile, the
// +30 min cloud ghosts and the target's path to dawn), plus the wind rose and
// the legend that go around it (plan F.6).
//
// THE ARGUMENT THIS FILE USED TO MAKE, AND WHAT RETIRED IT. Until D-WX-1 the
// header here refused to draw any of it, on two facts that were true of the
// code as it then stood:
//
//   1. `SkyDomePanel` took `{pointing, target}` and nothing else, and its yaw
//      was a private `useState` with no hook out - so an overlay would sit at
//      yaw 0 over a dome the operator had turned, marking the wrong part of the
//      sky. That is worse than not drawing, and it still would be.
//   2. The canvas's centre and radius came from padding constants private to
//      `SkyDome.tsx`. Re-deriving them is a copy that drifts the first time
//      either file is retuned.
//
// Neither is a fact about the geometry; both were facts about a missing seam,
// and the seam now exists. `SkyDome` publishes a `DomeGeometry` after every
// paint - `{cssW, cssH, cx, cy, r, tiltDeg, yawDeg}`, the numbers the paint
// ITSELF used, including the yaw the panel still privately owns - and
// `SkyDomePanel` forwards an `overlay` slot that is handed that geometry.
// Nothing below re-derives a radius, a centre or an angle: every coordinate
// here goes through `projectAltAz` with the published values, which is the only
// construction under which "a few degrees off is worse than none" is satisfied
// rather than merely asserted. The geometry is published ABOVE `getContext`'s
// null guard precisely so a jsdom test can compare a mark against the
// projection without a canvas (`SkyDome.tsx`, the hoist comment).
//
// WHAT IS STILL REFUSED, and why each refusal is a measurement and not a mood:
// no ghosts without a measured wind SPEED (the prototype's 12 km/h default
// would be a drawing of a wind nobody measured); no ghosts from a stale granule
// (advecting a reading the panel has already faded is a forecast built on a
// memory); no path without the site's own coordinates (see `DomeScreen`).

import { useEffect, useRef } from "react";
import type { JSX } from "react";
import {
  domeCells,
  occlusionFill,
  projectAltAz,
  type DomePoint,
} from "../../../../lib/domeProjection";
import type { DomeGeometry } from "../../../../components/cloudmap/SkyDome";
import type { DomeOverlayArgs } from "../../../../components/cloudmap/SkyDomePanel";
import {
  binTiles, ghostTiles, type CloudSample, type CloudTile,
} from "../../../lib/cloudTiles";
import { horizonAltAt, type HorizonPoint } from "../../../lib/horizonModel";
import { TRACK_COLORS, type TrackSample, type TrackState } from "../../sky/finder/track";
import { Mono } from "../../../ui";
import type { WeatherNow } from "../../../../types";
import { drift } from "../conditions/verdict";

/** The prototype's own cloud-base default, used only when the feed carries no
 *  `cloud_base_m` - and said out loud when it is (README "Formulas to lift":
 *  "h = 2.2 km, v = 12 km/h in the prototype; take both from the weather feed"). */
export const ASSUMED_BASE_KM = 2.2;

export const WIND_ABSENT = "wind not reported";

/** How far ahead the ghost tiles are advected, minutes. Matches the design's
 *  "+30 min ghost tiles dashed" and the panel's own look-ahead ladder, so the
 *  outline and the "+30m" rung below it are the same forecast. */
export const GHOST_MINUTES = 30;

/** The bin the ghosts are drawn on, degrees. The same 6 the Sky finder's cloud
 *  layer uses, so a tile means the same thing in both hubs. */
export const GHOST_STEP_DEG = 6;

/** Azimuth step of the horizon fill, degrees. Fine enough that a polyline
 *  through the tops is indistinguishable from the curve, coarse enough that the
 *  ring is 120 polygons and not a thousand. */
export const HORIZON_STEP_DEG = 3;

const HORIZON_FILL = "rgba(255,84,112,.14)";
const HORIZON_LINE = "rgba(255,84,112,.7)";
const HORIZON_DASH = "4 3";
const GHOST_LINE = "rgba(200,208,228,.55)";
const GHOST_DASH = "3 3";

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
  swatch: "fill" | "hatch" | "ring" | "cross" | "line" | "box";
  color: string;
  dash?: string;
}

/** Which of the overlay's three marks actually reached the picture. The legend
 *  names a mark ONLY when it is drawn: a legend entry for a horizon profile
 *  that is not on the dome sends the reader looking for a red band that is not
 *  there and lets them conclude the sky is clear of obstructions. */
export interface DrawnMarks {
  horizon: boolean;
  ghosts: boolean;
  path: boolean;
}

const NOTHING_DRAWN: DrawnMarks = { horizon: false, ghosts: false, path: false };

/** The legend describes THE CANVAS THAT IS MOUNTED, not the prototype's.
 *
 *  The design's legend names six marks - cloud hold, obstruction, below floor,
 *  deck in 30 min, a 25 degree floor ring and wind arrows - that
 *  `components/cloudmap/SkyDome.tsx` does not draw. Printing them anyway would
 *  make the legend decoration, and a legend that names marks the picture does
 *  not have is worse than no legend: the reader goes looking for a red patch
 *  that is not there and concludes the sky is clear. Colours below are lifted
 *  from `SkyDome.tsx:196-266` and `lib/domeProjection.ts:205-215`. */
function keys(hasTarget: boolean, drawn: DrawnMarks): Key[] {
  const out: Key[] = [
    { label: "clear", swatch: "fill", color: occlusionFill(0.0) },
    { label: "patchy", swatch: "fill", color: occlusionFill(0.3) },
    { label: "socked in", swatch: "fill", color: occlusionFill(0.9) },
    { label: "no reading", swatch: "hatch", color: "rgba(155,168,190,0.62)" },
    { label: "plan targets", swatch: "ring", color: "rgba(150,235,190,0.8)" },
  ];
  if (hasTarget) out.push({ label: "this target", swatch: "ring", color: "rgba(140,200,255,0.75)" });
  out.push({ label: "where the scope points", swatch: "cross", color: "rgba(255,214,102,0.98)" });
  if (drawn.horizon) {
    out.push({ label: "horizon profile", swatch: "line", color: HORIZON_LINE, dash: HORIZON_DASH });
  }
  if (drawn.ghosts) {
    out.push({ label: "cloud in 30 min", swatch: "box", color: GHOST_LINE, dash: GHOST_DASH });
  }
  if (drawn.path) {
    out.push({ label: "path to dawn", swatch: "line", color: "var(--accent)" });
  }
  return out;
}

export function DomeLegend({ hasTarget, wind, drawn = NOTHING_DRAWN }: {
  hasTarget: boolean;
  wind: WindSummary | null;
  drawn?: DrawnMarks;
}): JSX.Element {
  return (
    <div
      style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}
      data-testid="wx-dome-legend"
    >
      {keys(hasTarget, drawn).map((k) => (
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
  if (k.swatch === "line") {
    return (
      <svg width="12" height="8" viewBox="0 0 12 8" aria-hidden="true">
        <path
          d="M0 4H12"
          stroke={k.color}
          strokeWidth="1.6"
          strokeDasharray={k.dash}
          fill="none"
        />
      </svg>
    );
  }
  if (k.swatch === "box") {
    return (
      <svg width="12" height="8" viewBox="0 0 12 8" aria-hidden="true">
        <rect
          x="0.7" y="0.7" width="10.6" height="6.6"
          fill="none"
          stroke={k.color}
          strokeWidth="1.2"
          strokeDasharray={k.dash}
        />
      </svg>
    );
  }
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <circle cx="6" cy="6" r="4" fill="none" stroke={k.color} strokeWidth="1.4" />
    </svg>
  );
}

// ------------------------------------------------------------- the marks

/** One projection call, in one place. Every coordinate in this file comes
 *  through here with the geometry the canvas published - never a re-derived
 *  centre, radius or angle. */
function proj(g: DomeGeometry, altDeg: number, azDeg: number): DomePoint {
  return projectAltAz(altDeg, azDeg, g.cx, g.cy, g.r, g.tiltDeg, g.yawDeg);
}

const xy = (p: DomePoint): string => `${p.x.toFixed(1)},${p.y.toFixed(1)}`;

/**
 * The +30 min ghost tiles, or an empty list and the reason it is empty.
 *
 * Pure, and exported so the wind convention can be tested without a DOM. THE
 * WIND CONVENTION IS THE TRAP: `windTowardDeg` takes `wind.towardDeg`, which
 * `windSummary` already turned around from the feed's METEOROLOGICAL
 * `wind_dir_deg`. Passing the feed's bearing straight through advects every
 * tile 180 degrees wrong, and the picture stays entirely plausible - dashed
 * outlines beside solid cloud, just on the side the cloud is leaving.
 *
 * Three things each produce NO ghosts rather than a plausible guess: no grid,
 * a stale granule (a forecast off a reading the panel has already faded), and
 * a feed with a direction but no SPEED (the prototype's 12 km/h default would
 * be a drawing of a wind nobody measured).
 */
export function ghostsFor(
  grid: DomeOverlayArgs["grid"],
  stale: boolean,
  wind: WindSummary | null,
): CloudTile[] {
  if (!grid || stale || !wind || wind.kmh === null) return [];
  const samples: CloudSample[] = [];
  for (const c of domeCells(grid)) {
    // A GAP IS NOT A ZERO. Folding a null cell to 0 % would advect a patch of
    // clear sky nobody measured into the picture as a hole in the cloud.
    if (c.p === null || !Number.isFinite(c.p)) continue;
    samples.push({
      alt: (c.altLo + c.altHi) / 2,
      az: (c.azLo + c.azHi) / 2,
      pct: c.p * 100,
    });
  }
  const tiles = binTiles(samples, GHOST_STEP_DEG);
  return ghostTiles(
    tiles,
    GHOST_MINUTES,
    { baseKm: wind.baseKm, windKmh: wind.kmh, windTowardDeg: wind.towardDeg },
    GHOST_STEP_DEG,
  );
}

function HorizonFill({ g, horizon }: {
  g: DomeGeometry;
  horizon: HorizonPoint[];
}): JSX.Element {
  const quads: JSX.Element[] = [];
  const tops: string[] = [];
  for (let az = 0; az <= 360; az += HORIZON_STEP_DEG) {
    const h = horizonAltAt(horizon, az);
    tops.push(xy(proj(g, h, az)));
    if (az === 360) continue;
    const azNext = az + HORIZON_STEP_DEG;
    const hNext = horizonAltAt(horizon, azNext);
    const points = [
      proj(g, 0, az), proj(g, h, az), proj(g, hNext, azNext), proj(g, 0, azNext),
    ].map(xy).join(" ");
    // The quad's own centroid: mean altitude ((h + hNext) / 2) halved again,
    // because the quad runs from the horizon up to the profile.
    const mid = proj(g, (h + hNext) / 4, az + HORIZON_STEP_DEG / 2);
    quads.push(
      <polygon
        key={az}
        points={points}
        fill={HORIZON_FILL}
        // SkyDome's own near/far rule: the back of the dome is seen THROUGH the
        // front, so a tree line behind the observer must not read as one in the
        // way.
        fillOpacity={mid.facing ? 1 : 0.5}
      />,
    );
  }
  return (
    <g data-testid="wx-dome-horizon">
      {quads}
      <polyline
        points={tops.join(" ")}
        fill="none"
        stroke={HORIZON_LINE}
        strokeWidth="1.2"
        strokeDasharray={HORIZON_DASH}
      />
    </g>
  );
}

/** The ghosts that are actually on the near half of the dome, as SVG polygon
 *  point strings. Culling happens HERE and not inside the `<g>` so the legend's
 *  "cloud in 30 min" entry counts what is drawn rather than what was computed:
 *  a bank that advects entirely round the back leaves nothing to look at. */
function ghostPolygons(g: DomeGeometry, ghosts: CloudTile[]): { key: string; points: string }[] {
  const out: { key: string; points: string }[] = [];
  // ONE OUTLINE PER CELL. `ghostTiles` re-bins onto the same grid without
  // merging, so a wind that converges two source tiles onto one cell returns it
  // twice - which draws the same dashed outline over itself, darkening a cell
  // for no reason anyone could read, and duplicates the React key.
  const seen = new Set<string>();
  for (const t of ghosts) {
    const key = `${t.alt0}:${t.az0}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const corners = [
      proj(g, t.alt0, t.az0),
      proj(g, t.alt0, t.az0 + GHOST_STEP_DEG),
      proj(g, t.alt0 + GHOST_STEP_DEG, t.az0 + GHOST_STEP_DEG),
      proj(g, t.alt0 + GHOST_STEP_DEG, t.az0),
    ];
    if (corners.every((c) => !c.facing)) continue;   // round the back of the dome
    out.push({ key, points: corners.map(xy).join(" ") });
  }
  return out;
}

function Ghosts({ polys }: { polys: { key: string; points: string }[] }): JSX.Element {
  return (
    <g data-testid="wx-dome-ghost">
      {polys.map((p) => (
        <polygon
          key={p.key}
          points={p.points}
          fill="none"
          stroke={GHOST_LINE}
          strokeWidth="1"
          strokeDasharray={GHOST_DASH}
        />
      ))}
    </g>
  );
}

export interface PathPoint {
  x: number;
  y: number;
  st: Exclude<TrackState, "below">;
}

/** Contiguous drawable runs of the track: one per colour, broken wherever the
 *  target goes below the horizon or round the back of the dome.
 *
 *  `below` IS NOT A COLOUR (track.ts): a line drawn across a stretch where the
 *  target is under the ground would say it travelled through it. `facing` is
 *  the same refusal in the other axis - the far side of the dome is drawn over
 *  by the near side, so a segment there would appear to cross the sky the short
 *  way round. */
export function pathRuns(g: DomeGeometry, track: TrackSample[]): PathPoint[][] {
  const runs: PathPoint[][] = [];
  let run: PathPoint[] | null = null;
  let st: TrackState | null = null;
  for (const s of track) {
    const p = proj(g, s.alt, s.az);
    if (s.st === "below" || !p.facing) { run = null; st = null; continue; }
    const here = s.st as Exclude<TrackState, "below">;
    if (run === null || st !== here) {
      run = [];
      runs.push(run);
      st = here;
    }
    run.push({ x: p.x, y: p.y, st: here });
  }
  return runs.filter((r) => r.length > 0);
}

function TargetPath({ g, runs, track, targetName }: {
  g: DomeGeometry;
  runs: PathPoint[][];
  track: TrackSample[];
  targetName: string | null;
}): JSX.Element {
  const last = runs.length > 0 ? runs[runs.length - 1] : null;
  const end = last ? last[last.length - 1] : null;
  // An hour dot every fourth sample: STEP_HOURS is a quarter of an hour, so
  // this is the hour ticks and not a bead on every sample.
  const dots: { x: number; y: number; st: Exclude<TrackState, "below"> }[] = [];
  track.forEach((s, i) => {
    if (i % 4 !== 0 || s.st === "below") return;
    const p = proj(g, s.alt, s.az);
    if (!p.facing) return;
    dots.push({ x: p.x, y: p.y, st: s.st as Exclude<TrackState, "below"> });
  });
  return (
    <g data-testid="wx-dome-path">
      {runs.map((r, i) => (
        <polyline
          key={i}
          points={r.map((q) => `${q.x.toFixed(1)},${q.y.toFixed(1)}`).join(" ")}
          fill="none"
          stroke={TRACK_COLORS[r[0].st]}
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
      {dots.map((d, i) => (
        <circle key={i} cx={d.x} cy={d.y} r="1.6" fill={TRACK_COLORS[d.st]} />
      ))}
      {end && targetName && (
        <text
          x={end.x + 6}
          y={end.y - 5}
          fontSize="10"
          fontFamily="IBM Plex Mono, ui-monospace, monospace"
          fill="var(--text-dim)"
          stroke="rgba(8,10,16,0.85)"
          strokeWidth="2.6"
          paintOrder="stroke"
        >
          {`${targetName} to dawn`}
        </text>
      )}
    </g>
  );
}

export interface DomeOverlayProps {
  args: DomeOverlayArgs;
  /** The site's ACTIVE horizon polyline, or null when it could not be read. */
  horizon: HorizonPoint[] | null;
  wind: WindSummary | null;
  /** The target's walk to dawn, or null when the site's coordinates are not
   *  readable by this role - never a path from a guessed site. */
  track: TrackSample[] | null;
  targetName: string | null;
  /** What actually reached the picture, so the legend can name those marks and
   *  no others. Fired only when the answer changes. */
  onDrawn?: (d: DrawnMarks) => void;
}

/**
 * Everything drawn ON the dome, in the dome's own projection.
 *
 * Three groups, back to front: the horizon profile (the wall the sky rises out
 * of), the +30 min cloud ghosts, then the target's path - the mark the reader
 * came for, and the one that must never sit underneath the other two.
 */
export function DomeOverlay({
  args, horizon, wind, track, targetName, onDrawn,
}: DomeOverlayProps): JSX.Element {
  const g = args.geom;
  const hz = horizon && horizon.length > 0 ? horizon : null;
  const polys = ghostPolygons(g, ghostsFor(args.grid, args.stale, wind));
  const runs = track && track.length > 0 ? pathRuns(g, track) : [];

  const drawn: DrawnMarks = {
    horizon: hz !== null,
    ghosts: polys.length > 0,
    path: runs.length > 0,
  };
  // The legend names marks, and it may only name the ones this render actually
  // produced - hence a report of what was drawn rather than a second copy of
  // the three conditions above, which would drift.
  const seen = useRef<DrawnMarks | null>(null);
  useEffect(() => {
    const p = seen.current;
    if (p && p.horizon === drawn.horizon && p.ghosts === drawn.ghosts
        && p.path === drawn.path) {
      return;
    }
    seen.current = drawn;
    onDrawn?.(drawn);
  });

  const named: string[] = [];
  if (drawn.horizon) named.push("the horizon profile");
  if (drawn.ghosts) named.push("where the cloud is in 30 minutes");
  if (drawn.path) named.push(`the path ${targetName ?? "the target"} takes to dawn`);

  return (
    <svg
      width={g.cssW}
      height={g.cssH}
      role="img"
      aria-label={named.length > 0
        ? `Drawn over the dome: ${named.join(", ")}`
        : "Nothing is drawn over the dome"}
      data-testid="wx-dome-overlay"
      style={{ display: "block" }}
    >
      {hz && <HorizonFill g={g} horizon={hz} />}
      {polys.length > 0 && <Ghosts polys={polys} />}
      {runs.length > 0 && track && (
        <TargetPath g={g} runs={runs} track={track} targetName={targetName} />
      )}
    </svg>
  );
}
