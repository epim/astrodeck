// model.ts - `useSkyModel()`, the ONE hook that reads the store and the server on
// the Sky hub's behalf and hands back everything the finder, the lock card, the
// reach strip and the lens dial render (hub-sky plan section B).
//
// WHY ONE HOOK. Every number on this screen is a function of the same six inputs
// (site, time, optics, weather, horizon, catalogue). Computing them in six
// components means six answers that can disagree, and the way that shows up is a
// target the reticle calls CLEAR while the strip below it calls it CLOUDED. So
// the derivation happens once and the components are given the result.
//
// WHAT IS NOT HERE. No mount command, ever. `lib/altaz.ts` states its own
// boundary - good to a second of sidereal time, nowhere near good enough to slew
// on - and every command this hub issues travels as a target's catalogued RA/Dec.
// The screen alt/az is for aiming a human, not a telescope.
//
// STORE ACCESS is through the documented narrow selectors only
// (ARCHITECTURE.md section 9). Nothing here subscribes to the whole state.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../../../api";
import {
  getCloudmap,
  getCloudmapDome,
  type CloudMotion,
  type CloudmapDome,
} from "../../../../api/cloudmap";
import { getSite } from "../../../../api/site";
import { altAzOf, lstHours } from "../../../../lib/altaz";
import { effectiveOptics } from "../../../../lib/effective";
import {
  fovFromOptics,
  missingOpticsFields,
  type OpticsLike,
} from "../../../../lib/framing";
import { useSkyRegion } from "../../../../lib/skyRegion";
import { breachSpans } from "../../../../lib/weather";
import { useCapability } from "../../../../lib/caps";
import { useConfig, useSite, useStatus, useStore, useWeather } from "../../../../store";
import type { TonightResponse, VisibilityNight } from "../../../../types";
import type { NxIconName } from "../../../icons";
import { rankTargets, windowLabel } from "../../../lib/reach";
import { horizonAltAt, type HorizonPoint } from "../../../lib/horizonModel";
import type { CloudTile } from "../../../lib/cloudTiles";
import { fmtClock } from "../../../lib/format";

import * as prefs from "./prefs";
import type { LayerPrefs, LensPrefs, SkyMode } from "./prefs";
import { D2R, decDmsStr, raDecFromAltAz, raHmsStr } from "./equatorial";
import {
  altLines as altLinesOf,
  altStrOf,
  azStrOf,
  compassTicks,
  makeProjector,
  ppdFor,
  type AltLine,
  type CompassTick,
  type Projector,
} from "./projection";
import {
  buildTrack,
  FLOOR_DEG,
  isObstructedAt,
  minutesAboveFloor,
  walkTrack,
  type TrackContext,
  type TrackRender,
} from "./track";
import {
  cloudBlobLabels,
  cloudPctAt,
  cloudRects,
  TILE_MIN_PCT,
  tilesFromDome,
  type CloudLabel,
  type CloudRect,
} from "./clouds";
import { windArrows, windFrom, windLine, type WindArrow, type WindModel } from "./wind";
import {
  decorate,
  inReach,
  KIND_ICON,
  LOCK_RADIUS_PX,
  mergeRows,
  paletteFor,
  pickLock,
  SKY_KINDS,
  type CatalogRowLike,
  type Marker,
  type SkyKind,
  type SkyTarget,
  type WheelLike,
} from "./targets";
import { cameraErrorMessage, cameraSupport, startCamera, stopCamera } from "./camera";
import { startGyro, type GyroHandle } from "./gyro";

export type { SkyKind, SkyTarget, Marker } from "./targets";
export type { LayerPrefs, LensPrefs, SkyMode } from "./prefs";
export { windowLabel };

export { LOCK_RADIUS_PX };

/** How many markers may be drawn at once. Beyond this the labels overlap into
 *  illegibility; the cut is by score, and the tracked target is always kept. */
export const MAX_MARKERS = 40;

const SOLAR_TTL_MS = 120_000;
const CLOUD_REFRESH_MS = 60_000;
const TICK_MS = 30_000;
const VISIBILITY_DEBOUNCE_MS = 300;

/**
 * How long the opening aim waits for BOTH catalogue sources before it settles
 * for whichever one has answered.
 *
 * The ranked picks carry no alt/az, so their positions are computed here; the
 * region rows carry the SERVER's alt/az and win the merge. Aiming at the first
 * non-empty ranking therefore aimed at the client's arithmetic, and when the
 * region answered a second later the marker moved while the view stayed put -
 * the finder opening a few degrees off its own top target, in SWEEP, with
 * nothing on screen to say why.
 */
export const AIM_SETTLE_MS = 1500;

/** How far the top target has to move for the opening aim to follow it. One
 *  degree is a sixth of the box's width in sky, and well outside the 46 px lock
 *  radius - under that, following would be a twitch nobody asked for. */
export const AIM_FOLLOW_DEG = 1;

export const LAYERS_NOTE_DEFAULT =
  "Overlays redraw from the rig's own weather and this site's horizon.";
export const LAYERS_NOTE_NO_WEATHER =
  "Cloud and wind need weather access; the horizon is drawn from this site.";
export const LAYERS_NOTE_NO_HORIZON =
  "No horizon marked at this site yet - EDIT it under the site pill.";
export const WIND_NOTE_NONE = "no wind in the forecast - drift not drawn";
export const RANK_NEEDS_SITE = "Ranking tonight needs site access; search still works.";
/**
 * `view.site_precise` strips `latitude`/`longitude` off the site block. Every
 * altitude on this screen is f(site, target, time), so without them there is
 * nothing to compute from - and defaulting to 0,0 would place the whole sky as
 * seen from the Gulf of Guinea while every label still looked right. Rows the
 * SERVER already placed (the Moon and the planets carry their own alt/az) are
 * still drawn; nothing else is.
 */
export const NO_COORDS_NOTE =
  "Precise location is hidden for this role, so the finder can only place what the rig placed for it.";

export interface ReticleModel {
  haveOptics: boolean;
  /** The true field-of-view rectangle, px. Zero when optics are unusable. */
  w: number;
  h: number;
  rotationDeg: number;
  color: string;
  glow: string;
  /** The existing missing-optics sentence, or null when the frame can be drawn. */
  missingNote: string | null;
}

export interface PatchModel {
  ra_hours: number;
  dec_deg: number;
  raStr: string;
  decStr: string;
  cloudPct: number | null;
  obstructed: boolean;
  statusTxt: string;
  color: string;
}

export interface SkyModel {
  az: number;
  alt: number;
  azStr: string;
  altStr: string;
  mode: SkyMode;
  gyro: boolean;
  secureContext: boolean;
  /** Ranked and lens-filtered - what the finder and the lists actually show. */
  targets: SkyTarget[];
  markers: Marker[];
  lock: SkyTarget | null;
  lockNote: "LOCKED · CLEAR" | "CLOUDED" | "OBSTRUCTED" | "SWEEP";
  reachList: SkyTarget[];
  reachCount: number;
  clearPct: number | null;
  lens: Record<SkyKind, boolean>;
  lensHiddenCount: number;
  floorOnly: boolean;
  layers: LayerPrefs;
  windLine: string | null;
  siteName: string;
  horizonPoints: HorizonPoint[];
  cameraError: string | null;
  gyroError: string | null;
  setView(p: { az?: number; alt?: number; trackId?: string | null }): void;
  setLens(kind: SkyKind, on: boolean): void;
  setLayer(k: "clouds" | "horizon" | "wind", on: boolean): void;
  setMode(m: SkyMode): void;
  toggleGyro(): void;
  setFloorOnly(v: boolean): void;

  // ---- additions the plan's A.4/A.6/A.8/A.9 name as `model.*` ---------------
  /** The id whose to-dawn arc is drawn, if any. */
  trackId: string | null;
  /**
   * True once the merged ranking has settled: both catalogue sources have
   * answered, or the settle timer has run out.
   *
   * The opening aim waits on it, and so does the hub's `?lock=` deep link -
   * both aim at a POSITION, and until the region rows are in, the position on
   * offer is this file's arithmetic rather than the server's own alt/az.
   */
  aimReady: boolean;
  projector: Projector;
  boxW: number;
  boxH: number;
  ticks: CompassTick[];
  altLines: AltLine[];
  horizonPath: string | null;
  cloudTiles: CloudRect[];
  cloudLabels: CloudLabel[];
  windArrows: WindArrow[];
  wind: WindModel | null;
  track: TrackRender | null;
  reticle: ReticleModel;
  patch: PatchModel | null;
  /** In-reach count per kind, IGNORING the lens - the number on each lens button
   *  has to say what turning that kind back on would give you. */
  kindCounts: Record<SkyKind, number>;
  kindIcon: Record<SkyKind, NxIconName>;
  /** Why the overlays look the way they do, in one sentence. */
  layersNote: string;
  windNote: string | null;
  /** True when this principal may see cloud at all. */
  weatherAllowed: boolean;
  /** True when this principal may see the ranked list at all. */
  rankingAllowed: boolean;
  /** The server's own sentence for an empty cloud map, when it sent one. */
  cloudReason: string | null;
  /** Why the ranked list is empty, when it is. */
  rankingError: string | null;
  /** Set when this role cannot see the site coordinates, so most of the sky
   *  cannot be placed at all (see NO_COORDS_NOTE). */
  placementNote: string | null;
  visibility: VisibilityNight | null;
  /** The AR stream, for SkyView to attach to its `<video>`. */
  cameraStream: MediaStream | null;
  cameraSupported: boolean;
  gyroSupported: boolean;
  nowMs: number;
}

// --------------------------------------------------------------- sub-hooks

/** The ranked list, or the reason there is none. `answered` is the settle
 *  signal the opening aim waits on - true once the route has replied EITHER
 *  way, and true immediately when there is nothing to ask. */
function useTonight(altLimit: number, enabled: boolean, siteKey: string) {
  const [rows, setRows] = useState<CatalogRowLike[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [answered, setAnswered] = useState(false);
  useEffect(() => {
    if (!enabled) { setRows([]); setError(null); setAnswered(true); return; }
    let alive = true;
    setAnswered(false);
    void api
      .get<TonightResponse>(`/api/catalog/tonight?alt_limit=${encodeURIComponent(altLimit)}`)
      .then((res) => {
        if (!alive) return;
        setRows(Array.isArray(res?.picks) ? (res.picks as unknown as CatalogRowLike[]) : []);
        setError(null);
        setAnswered(true);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        const msg = (e as { message?: string } | null)?.message ?? "";
        const timedOut = (e as { timedOut?: boolean } | null)?.timedOut === true;
        setRows([]);
        setError(
          timedOut
            ? "No answer in 15s, so tonight wasn't ranked."
            : `Couldn't rank tonight — ${msg}.`,
        );
        setAnswered(true);
      });
    return () => { alive = false; };
    // siteKey is a dependency on purpose: moving site changes every altitude in
    // the answer, and this is the one fetch nobody would think to repeat by hand.
  }, [altLimit, enabled, siteKey]);
  return { rows, error, answered };
}

/**
 * The Moon and the planets. Two calls, both filtered by `type`, because
 * `q=planet` also matches every Planetary Nebula in the DSO catalogue.
 *
 * THE ANSWER IS `results`, NOT `rows`. `GET /api/catalog?q=…&explain=1` returns
 * `{"results": [...], "notes": [...]}` (app.py's catalog handler); the bare list
 * shape is what it answers WITHOUT `explain`. Reading `rows` here parsed every
 * response as empty, so no planet and no Moon ever reached the finder - and
 * nothing looked broken, because a sky with no planets in it is an ordinary
 * sight. `sheets/targetsModel.ts` already reads `results`; this is the same fix.
 */
function useSolarSystem(enabled: boolean): CatalogRowLike[] {
  const [rows, setRows] = useState<CatalogRowLike[]>([]);
  const [beat, setBeat] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => setBeat((n) => n + 1), SOLAR_TTL_MS);
    return () => clearInterval(id);
  }, [enabled]);
  useEffect(() => {
    if (!enabled) { setRows([]); return; }
    let alive = true;
    const one = (q: string): Promise<CatalogRowLike[]> =>
      api
        .get<{ results?: CatalogRowLike[] }>(`/api/catalog?q=${q}&explain=1`)
        .then((r) => (Array.isArray(r?.results) ? r.results : []))
        .catch(() => [] as CatalogRowLike[]);
    void Promise.all([one("planet"), one("moon")]).then(([p, m]) => {
      if (!alive) return;
      setRows(
        [...p, ...m].filter(
          (r) => r.kind === "solar_system" && (r.type === "Planet" || r.type === "Moon"),
        ),
      );
    });
    return () => { alive = false; };
  }, [enabled, beat]);
  return rows;
}

/** The cloud dome and the measured cloud motion, refreshed no faster than the
 *  server can afford: `dome_payload` walks every ray behind a one-at-a-time
 *  gate, so a 5 s poll would queue behind itself. */
function useCloudDome(enabled: boolean) {
  const [dome, setDome] = useState<CloudmapDome | null>(null);
  const [motion, setMotion] = useState<CloudMotion | null>(null);
  const [beat, setBeat] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => setBeat((n) => n + 1), CLOUD_REFRESH_MS);
    return () => clearInterval(id);
  }, [enabled]);
  useEffect(() => {
    if (!enabled) { setDome(null); setMotion(null); return; }
    let alive = true;
    void getCloudmapDome(6, 10).then((d) => { if (alive) setDome(d); }).catch(() => {});
    void getCloudmap().then((s) => { if (alive) setMotion(s?.motion ?? null); }).catch(() => {});
    return () => { alive = false; };
  }, [enabled, beat]);
  return { dome, motion };
}

/**
 * The horizon polyline the ENGINE is gating on.
 *
 * `GET /api/site` echoes `config.safety.horizon` as `horizon_points` and is the
 * one route that does; the site block on the status and summary frames does not
 * carry it, which is why this is a fetch and not a store read. A saved location's
 * own copy is the LIBRARY's - drawing that one would show a line the mount is not
 * obeying.
 */
function useAppliedHorizon(siteKey: string): HorizonPoint[] | null {
  const [points, setPoints] = useState<HorizonPoint[] | null>(null);
  useEffect(() => {
    let alive = true;
    void getSite()
      .then((res) => {
        if (!alive) return;
        const raw = res?.site?.horizon_points;
        setPoints(
          Array.isArray(raw)
            ? raw
                .filter((p) => Array.isArray(p) && p.length >= 2)
                .map(([az, alt]) => ({ az, alt }))
            : null,
        );
      })
      .catch(() => { if (alive) setPoints(null); });
    return () => { alive = false; };
  }, [siteKey]);
  return points;
}

/** Tonight's ephemeris for one anchor position. The dark window is a property of
 *  the SITE, not the target, so one call serves the whole screen; the transit and
 *  moon numbers it also carries belong to whatever is locked. */
function useVisibility(
  raHours: number | null,
  decDeg: number | null,
  altLimit: number,
  enabled: boolean,
): VisibilityNight | null {
  const [night, setNight] = useState<VisibilityNight | null>(null);
  // Rounded fetch keys: 0.001 h is 54 arcseconds of RA and 0.01 deg is 36
  // arcseconds of Dec, both far below anything this screen can show and both far
  // above what a pan would otherwise re-trigger the server's ephemeris for.
  const keyRa = raHours == null ? null : Math.round(raHours * 1000) / 1000;
  const keyDec = decDeg == null ? null : Math.round(decDeg * 100) / 100;
  useEffect(() => {
    if (!enabled || keyRa == null || keyDec == null) { setNight(null); return; }
    let alive = true;
    const timer = setTimeout(() => {
      void api
        .get<VisibilityNight>(
          `/api/visibility?ra=${encodeURIComponent(keyRa)}&dec=${encodeURIComponent(keyDec)}` +
            `&alt_limit=${encodeURIComponent(altLimit)}`,
        )
        .then((n) => { if (alive) setNight(n); })
        .catch(() => { /* the track is simply not drawn; nothing is invented */ });
    }, VISIBILITY_DEBOUNCE_MS);
    return () => { alive = false; clearTimeout(timer); };
  }, [enabled, keyRa, keyDec, altLimit]);
  return night;
}

// ------------------------------------------------------------------- helpers

function nearestForecastCloud(
  times: string[] | undefined,
  cloud: number[] | undefined,
  nowMs: number,
): number | null {
  if (!times || !cloud || times.length === 0) return null;
  let best = -1;
  let bestGap = Infinity;
  for (let i = 0; i < times.length && i < cloud.length; i++) {
    const t = Date.parse(times[i]);
    if (Number.isNaN(t)) continue;
    const gap = Math.abs(t - nowMs);
    if (gap < bestGap) { bestGap = gap; best = i; }
  }
  // More than an hour from the nearest sample is not "now".
  if (best < 0 || bestGap > 3600_000) return null;
  return cloud[best];
}

/** Angular gap between two screen aims, degrees. The azimuth difference is
 *  folded the short way round and narrowed by the cosine of the altitude, so
 *  ten degrees of azimuth at 80 deg up is the two degrees of sky it really is
 *  and not a re-aim. */
function aimGapDeg(
  a: { az: number; alt: number },
  b: { az: number; alt: number },
): number {
  const dAlt = b.alt - a.alt;
  const dAz = (((b.az - a.az) % 360) + 540) % 360 - 180;
  return Math.hypot(dAlt, dAz * Math.cos(((a.alt + b.alt) / 2) * D2R));
}

function defaultMode(): SkyMode {
  const desktop =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(min-width: 1200px)").matches;
  // A desktop has no camera worth pointing at the sky, and an insecure origin
  // has no camera at all - both open in MAP rather than opening on an error.
  if (desktop) return "map";
  return cameraSupport().ok ? "cam" : "map";
}

/** The horizon fill under the finder: the polyline swept across the visible
 *  azimuth range and closed off the bottom of the box. */
function horizonPathOf(
  points: HorizonPoint[],
  horizonMinDeg: number,
  p: Projector,
): string {
  const altAt = (az: number): number =>
    points.length > 0 ? horizonAltAt(points, az) : horizonMinDeg;
  const pts: string[] = [];
  for (let d = -44; d <= 44; d += 2) {
    const az = p.cAz + d;
    const at = p.proj(az, Math.max(0, altAt(az)));
    pts.push(`${at.x.toFixed(1)},${at.y.toFixed(1)}`);
  }
  const left = p.proj(p.cAz - 44, 0).x.toFixed(1);
  const right = p.proj(p.cAz + 44, 0).x.toFixed(1);
  return `M${pts.join("L")}L${right},${p.H + 40}L${left},${p.H + 40}Z`;
}

// --------------------------------------------------------------- the hook

export function useSkyModel(boxPx: number): SkyModel {
  const site = useSite();
  const config = useConfig();
  const status = useStatus();
  const weather = useWeather();
  const rotationDeg = useStore((s) => s.framing?.rotation_deg ?? 0);
  const weatherAllowed = useCapability("view.weather");
  const rankingAllowed = useCapability("view.site_derived");

  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), TICK_MS);
    return () => clearInterval(id);
  }, []);

  const [view, setViewState] = useState<{ az: number; alt: number }>({ az: 0, alt: 45 });
  const [trackId, setTrackId] = useState<string | null>(null);
  const [mode, setModeState] = useState<SkyMode>(() => prefs.getMode(defaultMode()));
  const [gyro, setGyroState] = useState(false);
  const [lens, setLensObj] = useState<LensPrefs>(() => prefs.getLens());
  const [layers, setLayersObj] = useState<LayerPrefs>(() => prefs.getLayers());
  const [floorOnly, setFloorOnlyState] = useState<boolean>(() => prefs.getFloorOnly());
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [gyroError, setGyroError] = useState<string | null>(null);
  const [cameraStream, setCameraStream] = useState<MediaStream | null>(null);

  const camSupport = useMemo(() => cameraSupport(), []);
  const secureContext = typeof window !== "undefined" && window.isSecureContext === true;

  const lat = site?.latitude ?? 0;
  const lon = site?.longitude ?? 0;
  const horizonMinDeg = site?.horizon_min_deg ?? 0;
  const siteKey = `${site?.name ?? ""}:${lat}:${lon}`;
  const haveSite = site != null;
  // Absent, not zero: `view.site_precise` strips these two keys rather than
  // nulling them, so `?? 0` above is a placeholder that must never be used as
  // an observing site.
  const haveCoords =
    typeof site?.latitude === "number" && typeof site?.longitude === "number";

  const tonight = useTonight(horizonMinDeg, rankingAllowed && haveSite, siteKey);
  const solarRows = useSolarSystem(rankingAllowed);
  const { dome, motion } = useCloudDome(weatherAllowed);
  const appliedHorizon = useAppliedHorizon(siteKey);

  // ---- horizon ------------------------------------------------------------
  const horizonPoints: HorizonPoint[] = useMemo(() => {
    if (appliedHorizon && appliedHorizon.length > 0) return appliedHorizon;
    const cfg = config?.safety?.horizon;
    if (Array.isArray(cfg) && cfg.length > 0) return cfg.map(([az, alt]) => ({ az, alt }));
    return [];
  }, [appliedHorizon, config]);

  // ---- cloud --------------------------------------------------------------
  const tiles: CloudTile[] = useMemo(() => tilesFromDome(dome), [dome]);
  const hasCloud = useMemo(() => tiles.some((t) => t.pct >= TILE_MIN_PCT), [tiles]);
  const cloudReason =
    dome && (!Array.isArray(dome.rows) || dome.rows.length === 0) ? (dome.reason ?? null) : null;
  const hourlyCloud = useMemo(
    () => nearestForecastCloud(weather?.forecast?.times, weather?.forecast?.cloud, nowMs),
    [weather, nowMs],
  );
  const clearPct = hourlyCloud == null ? null : Math.round(100 - hourlyCloud);

  // ---- the forecast breaches the track colours a hold by -------------------
  const holdSpans = useMemo(() => {
    const f = weather?.forecast;
    if (!weather || !f || weather.ignore_tonight || !Array.isArray(f.cloud) || f.cloud.length === 0) {
      return [] as { startMs: number; endMs: number }[];
    }
    const t0 = Date.parse(f.times[0] ?? "");
    if (Number.isNaN(t0)) return [];
    const stepMs = 15 * 60_000;
    return breachSpans(f.cloud, weather.threshold_pct, weather.sustain_minutes).map((s) => ({
      startMs: t0 + s.start * stepMs,
      endMs: t0 + (s.end + 1) * stepMs,
    }));
  }, [weather]);

  const holdAt = useCallback(
    (hoursFromNow: number): boolean => {
      const at = nowMs + hoursFromNow * 3600_000;
      return holdSpans.some((s) => at >= s.startMs && at <= s.endMs);
    },
    [holdSpans, nowMs],
  );

  // ---- wheel + optics -----------------------------------------------------
  // A string signature, not the live object: `status.filterwheel` is a fresh
  // object on every 2 s frame, and depending on it directly would re-rank the
  // whole catalogue thirty times a minute for a wheel that never changed.
  const wheelKey = status?.filterwheel
    ? [
        (status.filterwheel.names ?? []).join("|"),
        (status.filterwheel.narrowband ?? []).map((b) => (b ? 1 : 0)).join(""),
        (status.filterwheel.opaque ?? []).map((b) => (b ? 1 : 0)).join(""),
      ].join("::")
    : "";
  const wheel: WheelLike | null = useMemo(() => {
    if (wheelKey === "") return null;
    const [names, nb, op] = wheelKey.split("::");
    return {
      names: names === "" ? [] : names.split("|"),
      narrowband: nb === "" ? undefined : nb.split("").map((c) => c === "1"),
      opaque: op === "" ? undefined : op.split("").map((c) => c === "1"),
    };
  }, [wheelKey]);

  const mergedOptics: OpticsLike | null = useMemo(
    () =>
      effectiveOptics(
        config,
        config?.optics ?? null,
        status?.optics ?? config?.optics_computed ?? null,
      ),
    [config, status],
  );

  // ---- the visibility anchor ---------------------------------------------
  // Prefer whatever is tracked (its transit and moon numbers are shown); fall
  // back to the top ranked pick so the DARK WINDOW - the same for every target -
  // is known before anything has been locked.
  const tracked = useMemo(
    () => tonight.rows.find((r) => r.id === trackId) ?? null,
    [tonight.rows, trackId],
  );
  const anchorRa = tracked?.ra_hours ?? tonight.rows[0]?.ra_hours ?? null;
  const anchorDec = tracked?.dec_deg ?? tonight.rows[0]?.dec_deg ?? null;
  const visibility = useVisibility(
    anchorRa,
    anchorDec,
    horizonMinDeg,
    rankingAllowed && haveSite,
  );

  const hoursToDawn = useMemo(() => {
    const end = visibility?.dark_end_unix;
    if (typeof end !== "number") return 0;
    return Math.max(0, (end * 1000 - nowMs) / 3600_000);
  }, [visibility, nowMs]);

  // ---- the in-view region (source 2) --------------------------------------
  const pp = ppdFor(boxPx);
  const centreRaDec = useMemo(
    () => raDecFromAltAz(view.alt, view.az, lat, lon, nowMs / 1000),
    [view.alt, view.az, lat, lon, nowMs],
  );
  const region = useSkyRegion(centreRaDec, boxPx / pp, rankingAllowed && haveCoords, 80);

  // ---- the merged, decorated, ranked list ---------------------------------
  const trackCtx: TrackContext = useMemo(
    () => ({
      latDeg: lat,
      hoursToDawn,
      horizon: layers.horizon ? horizonPoints : [],
      horizonMinDeg: layers.horizon ? horizonMinDeg : 0,
      maskOn: layers.horizon,
      holdAt,
    }),
    [lat, hoursToDawn, horizonPoints, horizonMinDeg, layers.horizon, holdAt],
  );

  const ranked: SkyTarget[] = useMemo(() => {
    const merged = mergeRows(
      tonight.rows,
      region.rows as unknown as CatalogRowLike[],
      solarRows,
    );
    const nowSec = nowMs / 1000;
    const lst = lstHours(lon, nowSec);
    const cloudById = new Map<string, number | null>();
    const placeable = haveCoords
      ? merged
      : merged.filter((m) => m.altHint != null && m.azHint != null);
    const rows = placeable.map((m) => {
      const here =
        m.altHint != null && m.azHint != null
          ? { altDeg: m.altHint, azDeg: m.azHint }
          : altAzOf(m.ra_hours, m.dec_deg, lat, lon, nowSec);
      const obstructed = isObstructedAt(here.altDeg, here.azDeg, trackCtx);
      const domePct = cloudPctAt(tiles, here.altDeg, here.azDeg);
      const cloudPct = domePct ?? hourlyCloud;
      cloudById.set(m.id, cloudPct);
      const dec = decorate(cloudPct, obstructed);
      const winMin = minutesAboveFloor(
        walkTrack(m.dec_deg * D2R, (lst - m.ra_hours) * 15 * D2R, trackCtx),
      );
      const transitLabel =
        m.transitUnix == null
          ? "—"
          : m.transitUnix * 1000 < nowMs
            ? "passed"
            : fmtClock(m.transitUnix * 1000, m.transitUnix * 1000);
      return {
        id: m.id,
        name: m.name,
        // The type is the fallback second line; `fullName` leaves it blank
        // rather than pre-filling it, so a real sentence can still land later.
        full: m.full !== "" ? m.full : m.type,
        kind: m.kind,
        ra_hours: m.ra_hours,
        dec_deg: m.dec_deg,
        altNow: here.altDeg,
        azNow: here.azDeg,
        obstructed,
        clouded: dec.clouded,
        color: dec.color,
        statusTxt: dec.statusTxt,
        palette: paletteFor(m.kind, m.type, wheel),
        transitLabel,
        windowMinutes: winMin,
        difficulty: m.difficulty,
        // The catalogued extent, carried rather than dropped: FRAME's
        // catalogue-extent ellipse is drawn from it, and every one of the three
        // sources sends `size_arcmin`. `undefined` where none did - never 0,
        // which is the size of a star, not the absence of a measurement.
        sizeArcmin: m.sizeArcmin ?? undefined,
        // --- ReachInput, for rankTargets -----------------------------------
        minutesAboveFloorToDawn: winMin,
        // An absent reading does not score against a target: penalising an
        // unknown cloud or an unknown moon separation would rank an object down
        // for a measurement nobody took.
        cloudPct: cloudPct ?? 0,
        moonSepDeg: m.moonSepDeg ?? 90,
      };
    });
    return rankTargets(rows).map((r) => ({
      id: r.id,
      name: r.name,
      full: r.full,
      kind: r.kind,
      ra_hours: r.ra_hours,
      dec_deg: r.dec_deg,
      altNow: r.altNow,
      azNow: r.azNow,
      // Put the honest null back: rankTargets needed a number, the chip does not.
      cloudPct: cloudById.get(r.id) ?? null,
      obstructed: r.obstructed,
      clouded: r.clouded,
      color: r.color,
      statusTxt: r.statusTxt,
      palette: r.palette,
      sizeArcmin: r.sizeArcmin,
      transitLabel: r.transitLabel,
      windowMinutes: r.windowMinutes,
      score: r.score,
      moonSepDeg: r.moonSepDeg,
      difficulty: r.difficulty,
    }));
  }, [
    tonight.rows, region.rows, solarRows, nowMs, lat, lon, haveCoords, trackCtx,
    tiles, hourlyCloud, wheel,
  ]);

  // The lens and the floor chip HIDE, they do not re-rank: a hidden kind's count
  // still has to be shown on its lens button so the user knows what turning it
  // back on would give them.
  const visible = useMemo(
    () => ranked.filter((t) => lens[t.kind] !== false && !(floorOnly && t.altNow < FLOOR_DEG)),
    [ranked, lens, floorOnly],
  );

  const kindCounts = useMemo(() => {
    const out = {} as Record<SkyKind, number>;
    for (const k of SKY_KINDS) out[k] = 0;
    for (const t of ranked) {
      if (floorOnly && t.altNow < FLOOR_DEG) continue;
      if (inReach(t)) out[t.kind] += 1;
    }
    return out;
  }, [ranked, floorOnly]);

  const reachAll = useMemo(() => visible.filter(inReach), [visible]);
  const reachList = useMemo(() => reachAll.slice(0, 12), [reachAll]);

  // ---- aim at the best thing up, once the ranking has settled --------------
  //
  // The finder deliberately does NOT reopen where it was left: pointing at last
  // night's target is worse than pointing at tonight's best one.
  //
  // WHY THIS IS NOT "AIM AT THE FIRST NON-EMPTY RANKING". Two sources feed the
  // merged list and they answer at different times. The ranked picks arrive
  // first and carry no alt/az, so their positions are this file's arithmetic;
  // the region rows arrive after and carry the SERVER's alt/az, which win the
  // merge. Aiming on the first answer therefore aimed at the provisional
  // position and left the view there while the marker moved under it - the
  // finder opening a couple of degrees off its own top target, reading SWEEP
  // with the thing it chose sitting just outside the reticle.
  //
  // So: wait for both sources (or AIM_SETTLE_MS, because a source that answers
  // nothing is still an answer nobody can detect), then aim - and keep
  // following the top target while it MOVES by more than AIM_FOLLOW_DEG, until
  // the user touches the sky. The first pan, drag or marker tap goes through
  // `setView`, which sets `interactedRef` and ends every auto-aim for the life
  // of the screen; the aim below never sets it, because the finder aiming
  // itself is not the user choosing.
  const interactedRef = useRef(false);
  const autoAimRef = useRef<{ id: string; az: number; alt: number } | null>(null);
  const [settleElapsed, setSettleElapsed] = useState(false);
  useEffect(() => {
    const id = setTimeout(() => setSettleElapsed(true), AIM_SETTLE_MS);
    return () => clearTimeout(id);
  }, []);

  const regionAnswered =
    !(rankingAllowed && haveCoords) || region.rows.length > 0 || region.error != null;
  const aimReady = (tonight.answered && regionAnswered) || settleElapsed;

  useEffect(() => {
    if (interactedRef.current) return;
    if (!aimReady) return;
    const best = reachAll[0];
    if (!best) return;
    const prev = autoAimRef.current;
    const to = { az: best.azNow, alt: best.altNow };
    if (prev && prev.id === best.id && aimGapDeg(prev, to) <= AIM_FOLLOW_DEG) return;
    autoAimRef.current = { id: best.id, ...to };
    setViewState(to);
    setTrackId(best.id);
  }, [aimReady, reachAll]);

  // ---- projection ---------------------------------------------------------
  const projector = useMemo(
    () => makeProjector(boxPx, view.az, view.alt, false),
    [boxPx, view.az, view.alt],
  );

  const markers: Marker[] = useMemo(() => {
    const out: Marker[] = [];
    const push = (t: SkyTarget): void => {
      const at = projector.proj(t.azNow, t.altNow);
      out.push({
        id: t.id,
        name: t.name,
        kind: t.kind,
        color: t.color,
        x: at.x,
        y: at.y,
        altTag: `${Math.round(t.altNow)}°`,
        target: t,
      });
    };
    for (const t of visible) {
      if (out.length >= MAX_MARKERS) break;
      const at = projector.proj(t.azNow, t.altNow);
      if (at.x < -30 || at.x > projector.W + 30 || at.y < -24 || at.y > projector.H + 24) continue;
      push(t);
    }
    // The tracked target is never cut: its arc is drawn, and an arc with no
    // marker at its head reads as a line from nowhere.
    if (trackId && !out.some((m) => m.id === trackId)) {
      const t = visible.find((r) => r.id === trackId);
      if (t) push(t);
    }
    return out;
  }, [visible, projector, trackId]);

  const lock: SkyTarget | null = useMemo(
    () => pickLock(markers, projector.W, projector.H)?.target ?? null,
    [markers, projector],
  );

  const lockNote: SkyModel["lockNote"] = lock
    ? lock.obstructed
      ? "OBSTRUCTED"
      : lock.clouded
        ? "CLOUDED"
        : "LOCKED · CLEAR"
    : "SWEEP";

  // ---- overlays -----------------------------------------------------------
  const ticks = useMemo(() => compassTicks(projector), [projector]);
  const altLines = useMemo(() => altLinesOf(projector), [projector]);

  const horizonPath = useMemo(() => {
    if (!layers.horizon) return null;
    if (horizonPoints.length === 0 && !(horizonMinDeg > 0)) return null;
    return horizonPathOf(horizonPoints, horizonMinDeg, projector);
  }, [layers.horizon, horizonPoints, horizonMinDeg, projector]);

  const cloudTiles = useMemo(
    () => (layers.clouds && weatherAllowed ? cloudRects(tiles, projector) : []),
    [layers.clouds, weatherAllowed, tiles, projector],
  );
  const cloudLabels = useMemo(
    () => (mode === "map" ? cloudBlobLabels(cloudTiles, projector) : []),
    [mode, cloudTiles, projector],
  );

  const wind = useMemo(
    () => (weatherAllowed ? windFrom(weather?.now ?? null, motion) : null),
    [weatherAllowed, weather, motion],
  );
  const arrows = useMemo(
    () => (layers.wind && wind && hasCloud ? windArrows(projector, wind.wind, markers, false) : []),
    [layers.wind, wind, hasCloud, projector, markers],
  );
  const windLineStr = useMemo(
    () => (layers.wind && wind ? windLine(wind, tiles, nowMs) : null),
    [layers.wind, wind, tiles, nowMs],
  );

  // ---- the track ----------------------------------------------------------
  const track = useMemo(() => {
    if (!trackId || !haveCoords) return null;
    const t = ranked.find((r) => r.id === trackId);
    if (!t || !(hoursToDawn > 0)) return null;
    const lst = lstHours(lon, nowMs / 1000);
    const samples = walkTrack(t.dec_deg * D2R, (lst - t.ra_hours) * 15 * D2R, trackCtx);
    return buildTrack(samples, projector, nowMs, hoursToDawn);
  }, [trackId, haveCoords, ranked, hoursToDawn, lon, nowMs, trackCtx, projector]);

  // ---- the reticle --------------------------------------------------------
  const reticle: ReticleModel = useMemo(() => {
    const fov = fovFromOptics(mergedOptics);
    const haveOptics = fov.fov_x_deg > 0 && fov.fov_y_deg > 0;
    // With nothing locked the reticle is neutral text colour, through the token
    // rather than the prototype's literal: under night mode the literal would
    // stay the brightest thing on a dark-adapted screen.
    const color = lock ? lock.color : "color-mix(in srgb, var(--text) 70%, transparent)";
    return {
      haveOptics,
      w: haveOptics ? fov.fov_x_deg * pp : 0,
      h: haveOptics ? fov.fov_y_deg * pp : 0,
      rotationDeg,
      color,
      // color-mix rather than an appended "55": the colour is a design token, and
      // a token cannot be string-concatenated into an alpha the way a hex can.
      glow: lock ? `color-mix(in srgb, ${color} 35%, transparent)` : "rgba(0,0,0,0)",
      missingNote: haveOptics
        ? null
        : `Framing needs your optics — missing ${missingOpticsFields(mergedOptics).join(", ")}. ` +
          "Set them here, or connect your camera to fill pixel/sensor automatically.",
    };
  }, [mergedOptics, pp, rotationDeg, lock]);

  // ---- the empty patch under the reticle ----------------------------------
  const patch: PatchModel | null = useMemo(() => {
    // No longitude means no local sidereal time, so the reticle has no RA to
    // report and IMAGE THIS PATCH has nothing to send.
    if (view.alt < 0 || !haveCoords) return null;
    const domePct = cloudPctAt(tiles, view.alt, view.az);
    const cloudPct = domePct ?? hourlyCloud;
    const obstructed = isObstructedAt(view.alt, view.az, trackCtx);
    const dec = decorate(cloudPct, obstructed);
    return {
      ra_hours: centreRaDec.ra_hours,
      dec_deg: centreRaDec.dec_deg,
      raStr: raHmsStr(centreRaDec.ra_hours),
      decStr: decDmsStr(centreRaDec.dec_deg),
      cloudPct,
      obstructed,
      statusTxt: dec.statusTxt,
      color: dec.color,
    };
  }, [view.alt, view.az, haveCoords, centreRaDec, trackCtx, tiles, hourlyCloud]);

  // ---- the AR camera ------------------------------------------------------
  useEffect(() => {
    if (mode !== "cam") return;
    const support = cameraSupport();
    if (!support.ok) {
      setCameraError(support.reason);
      setModeState("map");
      prefs.setMode("map");
      return;
    }
    let alive = true;
    let stream: MediaStream | null = null;
    const release = (): void => {
      stopCamera(stream);
      stream = null;
      setCameraStream(null);
    };
    void startCamera()
      .then((s) => {
        if (!alive) { stopCamera(s); return; }
        stream = s;
        setCameraStream(s);
        setCameraError(null);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setCameraError(cameraErrorMessage(e));
        setModeState("map");
        prefs.setMode("map");
      });
    // A camera left running is a recording light left on and a battery gone by
    // midnight, so a hidden tab releases the track rather than holding it.
    const onHide = (): void => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") release();
    };
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", onHide);
    return () => {
      alive = false;
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onHide);
      release();
    };
  }, [mode]);

  // ---- the gyro -----------------------------------------------------------
  const altRef = useRef(view.alt);
  altRef.current = view.alt;
  useEffect(() => {
    if (!gyro) return;
    let handle: GyroHandle | null = null;
    handle = startGyro({
      onView: (v) => setViewState({ az: v.az, alt: v.alt }),
      currentAlt: () => altRef.current,
      onError: (m) => { setGyroError(m); setGyroState(false); },
    });
    if (handle == null) setGyroState(false);
    return () => { handle?.stop(); };
  }, [gyro]);

  // ---- setters ------------------------------------------------------------
  const setView = useCallback((p: { az?: number; alt?: number; trackId?: string | null }) => {
    if (p.az != null || p.alt != null) {
      setViewState((v) => ({
        az: p.az != null ? ((p.az % 360) + 360) % 360 : v.az,
        alt: p.alt != null ? Math.max(-12, Math.min(89, p.alt)) : v.alt,
      }));
    }
    if (p.trackId !== undefined) setTrackId(p.trackId);
    // A deliberate aim - a pan, a marker tap, a reach chip, a `?lock=` deep
    // link - ends the auto-aim for good, so the ranked list refreshing (or the
    // region answering a beat later) can never steal the view back.
    interactedRef.current = true;
  }, []);

  const setLens = useCallback((kind: SkyKind, on: boolean) => {
    setLensObj((cur) => {
      const next = { ...cur, [kind]: on };
      prefs.setLens(next);
      return next;
    });
  }, []);

  const setLayer = useCallback((k: "clouds" | "horizon" | "wind", on: boolean) => {
    setLayersObj((cur) => {
      const next = { ...cur, [k]: on };
      prefs.setLayers(next);
      return next;
    });
  }, []);

  const setMode = useCallback((m: SkyMode) => {
    setModeState(m);
    prefs.setMode(m);
    if (m === "map") setCameraError(null);
  }, []);

  const toggleGyro = useCallback(() => {
    setGyroError(null);
    setGyroState((g) => !g);
  }, []);

  const setFloorOnly = useCallback((v: boolean) => {
    setFloorOnlyState(v);
    prefs.setFloorOnly(v);
  }, []);

  // ---- notes --------------------------------------------------------------
  const layersNote = !weatherAllowed
    ? LAYERS_NOTE_NO_WEATHER
    : cloudReason
      ? cloudReason
      : horizonPoints.length === 0
        ? LAYERS_NOTE_NO_HORIZON
        : LAYERS_NOTE_DEFAULT;
  const windNote = weatherAllowed && wind == null ? WIND_NOTE_NONE : null;
  const rankingError = !rankingAllowed ? RANK_NEEDS_SITE : tonight.error;

  const lensHiddenCount = SKY_KINDS.filter((k) => lens[k] === false).length;
  const siteName = site == null || site.is_default ? "Set a site" : (site.name ?? "Set a site");

  return {
    az: view.az,
    alt: view.alt,
    azStr: azStrOf(view.az),
    altStr: altStrOf(view.alt),
    mode,
    gyro,
    secureContext,
    targets: visible,
    markers,
    lock,
    lockNote,
    reachList,
    reachCount: reachAll.length,
    clearPct,
    lens,
    lensHiddenCount,
    floorOnly,
    layers,
    windLine: windLineStr,
    siteName,
    horizonPoints,
    cameraError,
    gyroError,
    setView,
    setLens,
    setLayer,
    setMode,
    toggleGyro,
    setFloorOnly,

    trackId,
    aimReady,
    projector,
    boxW: projector.W,
    boxH: projector.H,
    ticks,
    altLines,
    horizonPath,
    cloudTiles,
    cloudLabels,
    windArrows: arrows,
    wind,
    track,
    reticle,
    patch,
    kindCounts,
    kindIcon: KIND_ICON,
    layersNote,
    windNote,
    weatherAllowed,
    rankingAllowed,
    cloudReason,
    rankingError,
    placementNote: haveCoords ? null : NO_COORDS_NOTE,
    visibility,
    cameraStream,
    cameraSupported: camSupport.ok,
    gyroSupported:
      typeof window !== "undefined" &&
      typeof (window as unknown as { DeviceOrientationEvent?: unknown }).DeviceOrientationEvent !==
        "undefined" &&
      secureContext,
    nowMs,
  };
}
