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
import { getSatellitePasses } from "../../../../api/ephemeris";
import { useSkyRegion } from "../../../../lib/skyRegion";
import { breachSpans } from "../../../../lib/weather";
import { useCapability } from "../../../../lib/caps";
import { useConfig, useSite, useStatus, useStore, useWeather } from "../../../../store";
import type {
  CometRow,
  EphemerisCacheState,
  SatellitePass,
  SatelliteRow,
  TonightResponse,
  VisibilityNight,
} from "../../../../types";
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
  AIM_TRACK_ID,
  buildDomeTracks,
  buildTrack,
  FLOOR_DEG,
  isObstructedAt,
  MAX_DOME_TRACKS,
  minutesAboveFloor,
  walkTrack,
  type DomeTrack,
  type DomeTrackContext,
  type TrackContext,
  type TrackRender,
  type TrackSample,
  type TrackSubject,
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
  SATELLITE_MARKERS,
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

/** The dome's own accessors, re-exported from the pure module they live in.
 *
 *  They are DEFINED in `finder/track.ts` and not here on purpose: WEATHER > SKY
 *  draws the same arcs from its own subjects and its own context, and importing
 *  them from this file would drag the whole Sky model - the store, `api.ts`,
 *  the cloud-map poller, the AR camera - into the weather chunk for two pure
 *  functions. `track.ts` is what both hubs already share. */
export {
  AIM_TRACK_ID,
  MAX_DOME_TRACKS,
  buildDomeTracks,
  domeTrackLabel,
  pointLabel,
  trackSamplesFor,
  type DomeTrack,
  type DomeTrackContext,
  type TrackSubject,
} from "./track";

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

/** How far ahead the passes list looks. The route's own default is 24 h and its
 *  ceiling is 72 (`ephemeris/routes.py:81-84`); a day is what "tonight" means
 *  here and every hour past it costs another sweep of SGP4. */
export const PASS_WINDOW_HOURS = 24;

/** The fallback when the passes call fails with nothing to quote - a transport
 *  failure, not a refusal, so it says which and offers the retry. */
export const PASSES_FAILED =
  "Could not work out the passes. The rig answered nothing - try again.";

/** A comet computed from the centre of the Earth. `comets.py:453-490` sends
 *  `topocentric: false` with NO alt/az when there is no site to place it
 *  against, so it is listed and not drawn: an arc on the horizon dome would be
 *  a claim about a horizon nobody computed. */
export const COMET_GEOCENTRIC_NOTE =
  "Listed but not drawn: this comet was computed from the centre of the Earth, so there is no horizon position for it.";

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

/**
 * What the skydome card draws that the finder does not.
 *
 * The finder answers "what can I point at"; the dome answers "where does it GO
 * between now and dawn, and what is in the way". One arc answered that for the
 * locked target only, which left the dome bare on every sweep - and bare is
 * what it looked like even on a night with a dozen things up, because nothing
 * had been tapped yet.
 *
 * So the arcs are a LIST, and three kinds of subject can be on it:
 *
 *   * the lock (bright, labelled) - the object nearest the reticle;
 *   * the best of the ranked list (dim, unlabelled) - so the dome is never bare
 *     on a night with targets, and so "is anything in the clear tonight" is a
 *     glance rather than six taps;
 *   * THE AIMED POINT (bright, labelled with its coordinates) when the reticle
 *     is on empty sky. A patch with no catalogue object under it is exactly
 *     what looking for something nobody has catalogued looks like, and it had
 *     no way onto the dome at all before this.
 */
export interface DomeModel {
  /** Brightest first, capped at `MAX_DOME_TRACKS`. Empty when this role cannot
   *  place the sky (no site coordinates) or the night is already over. */
  tracks: DomeTrack[];
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
  /**
   * The walk `track` is drawn from, before it was projected into the finder's
   * flat sky box - alt/az samples, so a hemisphere could draw it too.
   *
   * WHY IT IS NOT THE SAME THING AS `dome.tracks`. This field is the TRACKED
   * object - `trackId`, whatever was last tapped or deep-linked - walked under
   * the finder's LIVE context, where the horizon mask follows the layers
   * popover. `dome.tracks` is a list of subjects (the lock or the aimed point,
   * then the ranked list) walked with the mask forced ON, because the overlay
   * draws the horizon profile on that hemisphere whether the finder's layer is
   * showing it or not, and an arc coloured as if there were no mask would run
   * clear over a drawn tree line.
   *
   * What both share, and the part worth sharing, is the WALK: every consumer
   * goes through `trackSamplesFor` over the same site and the same horizon, so
   * no two arcs in this product can disagree about where an object goes - only
   * about what each screen is entitled to say about it. The two contexts are
   * written out at `trackSamplesFor`'s own doc comment in `finder/track.ts`.
   */
  trackSamples: TrackSample[] | null;
  /** The hemisphere's own arcs - see `DomeModel`. `SkyHub` hands these straight
   *  to `DomeCard`; nothing walks a second track for the dome any more. */
  dome: DomeModel;
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
  /**
   * Satellites, as a LIST and never as markers (`targets.ts SATELLITE_MARKERS`).
   * Empty for a principal the server withheld them from - and then the reason
   * is in `ephemerisNotes`, in the server's own words.
   */
  satellites: SatelliteRow[];
  /** Comets. The topocentric ones are also in `targets`/`markers`; the
   *  geocentric ones are only here, and `COMET_GEOCENTRIC_NOTE` says why. */
  comets: CometRow[];
  /** The `/api/catalog` notes for those two searches, verbatim. */
  ephemerisNotes: string[];
  /** Just the satellite search's own notes. `[0]` is the refusal a principal
   *  without `view.site_derived` gets instead of rows, in the server's words -
   *  which is what a locked passes card shows, rather than a cap phrase we
   *  wrote ourselves. */
  satelliteNotes: string[];
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
            : `Couldn't rank tonight - ${msg}.`,
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

/** What `useEphemerisRows` hands back. `notes` is the SERVER's own sentences,
 *  verbatim and in the order it sent them: the withheld one, the no-elements one
 *  and the stale one all live there, and every one of them carries something the
 *  rows cannot (why the list is short, how old the elements are, where to go). */
export interface EphemerisRows {
  satellites: SatelliteRow[];
  comets: CometRow[];
  /**
   * The two searches' notes kept APART, not just merged.
   *
   * A surface that has to show "why is there no satellite list" must not have
   * to guess which of a merged array said it - a text match on the server's
   * wording is a test that passes until somebody improves a sentence. The
   * satellite refusal is `satelliteNotes[0]` because that is the note the
   * satellite search itself produced.
   */
  satelliteNotes: string[];
  cometNotes: string[];
  /** Both, de-duplicated, in the order the server sent them: for a surface that
   *  just prints every sentence it was given. */
  notes: string[];
  loading: boolean;
}

const EPHEMERIS_ROWS_EMPTY: EphemerisRows = {
  satellites: [], comets: [], satelliteNotes: [], cometNotes: [], notes: [], loading: false,
};

/**
 * Satellites and comets, off the same `/api/catalog` search the Moon and the
 * planets come from (`ephemeris/satellites.py:459-532`, `comets.py:511-546`
 * answer a 3+ character prefix of their own kind name).
 *
 * NEITHER CALL IS GATED ON `view.site_derived` HERE, and that is deliberate.
 * The satellite module withholds its own rows and answers with `WITHHELD_NOTE`
 * (`satellites.py:77-82`) for a principal that may not have a site-derived
 * answer; the comet module serves every caller and marks the row
 * `topocentric: false` instead (`comets.py:453-490`). Gating the fetch here
 * would replace the server's sentence with silence, and the sentence is the
 * only thing that tells a viewer why the list is empty.
 *
 * Same `SOLAR_TTL_MS` cadence as `useSolarSystem`: a satellite moves degrees a
 * second, but these rows are a LIST, not a marker, and the number that decays
 * on this payload is the element age, in days.
 */
export function useEphemerisRows(enabled: boolean): EphemerisRows {
  const [rows, setRows] = useState<EphemerisRows>(EPHEMERIS_ROWS_EMPTY);
  const [beat, setBeat] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => setBeat((n) => n + 1), SOLAR_TTL_MS);
    return () => clearInterval(id);
  }, [enabled]);
  useEffect(() => {
    if (!enabled) { setRows(EPHEMERIS_ROWS_EMPTY); return; }
    let alive = true;
    setRows((r) => ({ ...r, loading: true }));
    const one = (q: string): Promise<{ results: CatalogRowLike[]; notes: string[] }> =>
      api
        .get<{ results?: CatalogRowLike[]; notes?: string[] }>(`/api/catalog?q=${q}&explain=1`)
        .then((r) => ({
          results: Array.isArray(r?.results) ? r.results : [],
          notes: Array.isArray(r?.notes) ? r.notes.filter((n) => typeof n === "string") : [],
        }))
        // An engine without the ephemeris routes answers the search normally
        // and simply matches nothing, so a failure here is a transport failure
        // and there is no sentence of the server's to show for it.
        .catch(() => ({ results: [] as CatalogRowLike[], notes: [] as string[] }));
    void Promise.all([one("satellites"), one("comets")]).then(([s, c]) => {
      if (!alive) return;
      const notes: string[] = [];
      for (const n of [...s.notes, ...c.notes]) if (!notes.includes(n)) notes.push(n);
      setRows({
        satellites: s.results.filter((r) => r.kind === "satellite") as unknown as SatelliteRow[],
        comets: c.results.filter((r) => r.kind === "comet") as unknown as CometRow[],
        satelliteNotes: s.notes,
        cometNotes: c.notes,
        notes,
        loading: false,
      });
    });
    return () => { alive = false; };
  }, [enabled, beat]);
  return rows;
}

/** What `useSatellitePasses` hands back. `passes === null` means nobody has
 *  answered yet; `[]` means the search ran and found nothing above the horizon,
 *  which is a different statement and gets different copy. */
export interface PassesState {
  passes: SatellitePass[] | null;
  elements: EphemerisCacheState | null;
  notes: string[];
  loading: boolean;
  error: string | null;
  /**
   * `ApiError.code` from the refusal, or null.
   *
   * The two 409s this route answers need different FACES, not different
   * wording: `satellites_unavailable` (no site set) has nothing to retry, and
   * `passes_busy` (another search is already running behind the route's
   * semaphore) has nothing else. A card branching on the sentence would be
   * matching prose the server is free to reword.
   */
  errorCode: string | null;
  /** A VISIBLE retry, never an automatic one - see the cost note below. */
  refresh: () => void;
}

/**
 * Tonight's passes for one satellite.
 *
 * ONE FETCH PER SATELLITE, NEVER A POLL. `GET /api/satellites/passes` is
 * thousands of SGP4 evaluations plus the matching frame transforms, run off the
 * event loop on a worker thread (`ephemeris/routes.py:100-108`) precisely so it
 * cannot stall the 2 s status poll. A card that refreshed itself would spend a
 * phone's battery and a rig's CPU on numbers that change by seconds a day.
 *
 * `enabled` is `view.site_derived`: the WHOLE route is behind it and a
 * non-holder gets a 403, not an empty list. The right answer for them is the
 * server's withheld sentence out of the catalog notes, so this hook fires
 * nothing at all rather than collecting a 403 to translate.
 */
export function useSatellitePasses(noradId: number | null, enabled: boolean): PassesState {
  const [state, setState] = useState<Omit<PassesState, "refresh">>({
    passes: null, elements: null, notes: [], loading: false, error: null, errorCode: null,
  });
  const [attempt, setAttempt] = useState(0);
  const refresh = useCallback(() => setAttempt((a) => a + 1), []);
  useEffect(() => {
    if (noradId == null || !enabled) {
      setState({
        passes: null, elements: null, notes: [], loading: false, error: null, errorCode: null,
      });
      return;
    }
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: null, errorCode: null }));
    void getSatellitePasses({ hours: PASS_WINDOW_HOURS, ids: [noradId] })
      .then((res) => {
        if (!alive) return;
        setState({
          passes: Array.isArray(res?.passes) ? res.passes : [],
          elements: res?.elements ?? null,
          notes: Array.isArray(res?.notes) ? res.notes : [],
          loading: false,
          error: null,
          errorCode: null,
        });
      })
      .catch((e: unknown) => {
        if (!alive) return;
        // The refusals carry a bare-string detail, so the message IS the
        // server's sentence and is shown as it stands. The CODE travels beside
        // it because the two 409s need different faces (see `errorCode`), and
        // a card that matched on the prose would break the day it is reworded.
        const err = e as { message?: string; code?: string } | null;
        const msg = err?.message ?? "";
        setState({
          passes: null, elements: null, notes: [], loading: false,
          error: msg !== "" ? msg : PASSES_FAILED,
          errorCode: typeof err?.code === "string" ? err.code : null,
        });
      });
    return () => { alive = false; };
  }, [noradId, enabled, attempt]);
  return { ...state, refresh };
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

export function useSkyModel(boxPx: number, options: { initialMode?: SkyMode } = {}): SkyModel {
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
  // Classic embeds start as a map even if this device last used the AR camera.
  // Opening a monitor or a tool sheet must not request camera access by itself.
  const [mode, setModeState] = useState<SkyMode>(() => options.initialMode ?? prefs.getMode(defaultMode()));
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
  const ephemeris = useEphemerisRows(true);
  const { dome, motion } = useCloudDome(weatherAllowed);
  const appliedHorizon = useAppliedHorizon(`${siteKey}:${JSON.stringify(config?.safety?.horizon ?? null)}`);

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
  // Prefer whatever is tracked (its transit and moon numbers are shown); then
  // the top ranked pick so the DARK WINDOW - the same for every target - is
  // known before anything has been locked; then a fixed point at this site,
  // for the case where the ranking never answers at all (see below).
  const tracked = useMemo(
    () => tonight.rows.find((r) => r.id === trackId) ?? null,
    [tonight.rows, trackId],
  );
  /**
   * The anchor of last resort, and why one is needed at all.
   *
   * THE DARK WINDOW IS A PROPERTY OF THE SITE, NOT OF THE OBJECT.
   * `/api/visibility` returns the same `dark_start_unix` and `dark_end_unix`
   * whatever ra/dec it is asked about (measured on a live rig: three targets
   * 120 degrees apart, one of them circumpolar-below, all three identical) -
   * only the transit and moon numbers are the target's own, and this hub reads
   * none of those off this response.
   *
   * So anchoring solely on the RANKED list made the night window a hostage of
   * a ranking that can fail to arrive. On a rig still on the default site,
   * `GET /api/catalog/tonight?alt_limit=15` takes 90 SECONDS and the hub's own
   * request times out at 15, so `tonight.rows` stays empty - and with it
   * `hoursToDawn` is 0, every `walkTrack` returns nothing, and the skydome, the
   * finder's arc and every reach window are all silently blank with nothing on
   * screen to say why. That is exactly what "I'm not seeing the target tracks
   * on the skydome" looks like from the other end.
   *
   * A fixed point due south, halfway up, answers the same question and costs
   * one request. It is keyed on the SITE alone and not on the clock or the
   * reticle: the window does not move during a session, and re-deriving it from
   * either would re-ask the server every time the view drifted.
   */
  const fallbackAnchor = useMemo(
    () => (haveCoords ? raDecFromAltAz(45, 180, lat, lon, Date.now() / 1000) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [siteKey, haveCoords],
  );
  const anchorRa = tracked?.ra_hours ?? tonight.rows[0]?.ra_hours
    ?? fallbackAnchor?.ra_hours ?? null;
  const anchorDec = tracked?.dec_deg ?? tonight.rows[0]?.dec_deg
    ?? fallbackAnchor?.dec_deg ?? null;
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

  /**
   * The ephemeris rows that may be PLACED, which is not the same set as the
   * ephemeris rows that may be LISTED.
   *
   *   A comet is placeable when the server placed it (`topocentric: true`). Its
   *   RA/Dec is topocentric too, so the catalog route's recompute lands within
   *   arcseconds of the module's own answer and a marker is honest. A
   *   `topocentric: false` comet has no alt/az at all - see
   *   COMET_GEOCENTRIC_NOTE.
   *
   *   A satellite is placeable only when SATELLITE_MARKERS says so, and it does
   *   not: the server's overwrite is guarded now, but this hook refreshes every
   *   two minutes and a low-orbit satellite crosses the sky in five. See the
   *   constant for both halves of that.
   */
  const placeableEphemeris = useMemo(() => {
    const out: CatalogRowLike[] = [];
    for (const c of ephemeris.comets) {
      if (c.topocentric === true) out.push(c as unknown as CatalogRowLike);
    }
    if (SATELLITE_MARKERS) {
      for (const s of ephemeris.satellites) out.push(s as unknown as CatalogRowLike);
    }
    return out;
  }, [ephemeris.comets, ephemeris.satellites]);

  const ranked: SkyTarget[] = useMemo(() => {
    const merged = mergeRows(
      tonight.rows,
      region.rows as unknown as CatalogRowLike[],
      solarRows,
      placeableEphemeris,
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
          ? "-"
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
    tonight.rows, region.rows, solarRows, placeableEphemeris, nowMs, lat, lon,
    haveCoords, trackCtx, tiles, hourlyCloud, wheel,
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
    // SATELLITES AND COMETS ARE COUNTED DIFFERENTLY, and `LENS_COUNT_NOUN` says
    // so on the button. Reach is a horizon question, and neither kind answers
    // it for every row: a satellite's horizon position does not survive
    // `/api/catalog` at all (SATELLITE_MARKERS), and a comet the server could
    // not place topocentrically has none. So the number is what the ephemeris
    // CARRIES. A count computed the other way would read 0 next to a list with
    // things in it, which is a filter arguing for leaving itself off.
    out.satellite = ephemeris.satellites.length;
    out.comet = ephemeris.comets.length;
    return out;
  }, [ranked, floorOnly, ephemeris.satellites, ephemeris.comets]);

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
  //
  // The walk and the projection are SEPARATE memos on purpose: `TrackRender` is
  // box pixels and cannot be drawn on a hemisphere, so the alt/az samples are
  // published beside it (`SkyModel.trackSamples`) for any consumer whose
  // subject and context match this one's. The dome cards' do not - see the
  // field's own doc comment for which two things differ and why.
  const trackSamples = useMemo(() => {
    if (!trackId || !haveCoords) return null;
    const t = ranked.find((r) => r.id === trackId);
    if (!t || !(hoursToDawn > 0)) return null;
    const lst = lstHours(lon, nowMs / 1000);
    const samples = walkTrack(t.dec_deg * D2R, (lst - t.ra_hours) * 15 * D2R, trackCtx);
    return samples.length > 0 ? samples : null;
  }, [trackId, haveCoords, ranked, hoursToDawn, lon, nowMs, trackCtx]);

  const track = useMemo(() => {
    if (!trackSamples || !(hoursToDawn > 0)) return null;
    return buildTrack(trackSamples, projector, nowMs, hoursToDawn);
  }, [trackSamples, hoursToDawn, nowMs, projector]);

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
        : `Framing needs your optics - missing ${missingOpticsFields(mergedOptics).join(", ")}. ` +
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

  // ---- the skydome's arcs -------------------------------------------------
  //
  // THE MASK IS FORCED ON, where the finder's own `trackCtx` lets the layers
  // popover turn it off. The overlay draws the site's horizon profile on this
  // hemisphere unconditionally, so an arc classified as if there were no mask
  // would run clear-coloured straight through a drawn tree line - a picture
  // that contradicts itself in the same 280 px.
  const domeCtx: DomeTrackContext = useMemo(
    () => ({
      ...trackCtx,
      horizon: horizonPoints,
      horizonMinDeg,
      maskOn: true,
      lonDeg: lon,
      nowMs,
    }),
    [trackCtx, horizonPoints, horizonMinDeg, lon, nowMs],
  );

  const domeTracks: DomeTrack[] = useMemo(() => {
    if (!haveCoords || !(hoursToDawn > 0)) return [];
    const subjects: TrackSubject[] = [];
    // The bright one, and there is at most one. A lock beats the patch because
    // a named object is what the reader asked about; the patch only becomes the
    // subject when the reticle is on sky nothing is catalogued in, which is the
    // case this list was extended for.
    if (lock) {
      subjects.push({
        id: lock.id, name: lock.name, ra_hours: lock.ra_hours, dec_deg: lock.dec_deg,
        bright: true,
      });
    } else if (patch) {
      subjects.push({
        id: AIM_TRACK_ID, name: null,
        ra_hours: patch.ra_hours, dec_deg: patch.dec_deg,
        bright: true,
      });
    }
    // Then the ranking, lens-filtered: what the finder is already showing, in
    // the order it already ranked. `buildDomeTracks` takes the first
    // MAX_DOME_TRACKS that actually have a walk, so a target that never rises
    // tonight does not silently occupy one of the six.
    for (const t of visible) {
      if (lock && t.id === lock.id) continue;
      subjects.push({ id: t.id, name: t.name, ra_hours: t.ra_hours, dec_deg: t.dec_deg });
    }
    return buildDomeTracks(subjects, domeCtx, MAX_DOME_TRACKS);
  }, [haveCoords, hoursToDawn, lock, patch, visible, domeCtx]);

  const domeModel: DomeModel = useMemo(() => ({ tracks: domeTracks }), [domeTracks]);

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
    // Every mode that is NOT the camera clears the camera's error, not just
    // MAP. ATLAS leaves the camera exactly as MAP does (the effect above tears
    // the stream down for any `mode !== "cam"`), so a stale "camera refused"
    // note surviving into the atlas would describe a device nothing on screen
    // is asking for.
    if (m !== "cam") setCameraError(null);
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
    trackSamples,
    dome: domeModel,
    reticle,
    patch,
    kindCounts,
    kindIcon: KIND_ICON,
    layersNote,
    windNote,
    weatherAllowed,
    rankingAllowed,
    satellites: ephemeris.satellites,
    comets: ephemeris.comets,
    ephemerisNotes: ephemeris.notes,
    satelliteNotes: ephemeris.satelliteNotes,
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
