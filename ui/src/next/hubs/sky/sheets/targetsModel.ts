// targetsModel.ts - the ranked list behind the Suggested-targets sheet
// (hub-sky plan A.10, B.2, B.6, B.12).
//
// WHY THIS IS NOT `useSkyModel`. The finder's model derives the same list, and
// reusing it would be the obvious move - but it is one hook that ALSO opens the
// AR camera (`startCamera`), attaches the orientation listener and polls the
// cloud dome on its own interval. Mounting it a second time behind a sheet the
// user cannot see through would light the camera twice and hold two streams
// open, which on a phone is a recording light and a flat battery by midnight.
//
// So this module does the PLUMBING for a list and nothing else, and every
// DECISION in it is imported from `finder/` rather than re-derived:
//
//   kindOf / displayName / fullName  the two row-shape traps (a body's `id` is
//                                    its label and its `name` is a sentence)
//   mergeRows                        the union by id, nothing overwritten blank
//   decorate                         CLEAR / CLOUD n% / BEHIND HORIZON, and the
//                                    fact that a null reading is NOT 0% cloud
//   paletteFor                       LRGB / SHO / HOO, derived from the wheel
//   isObstructedAt                   polyline first, flat `horizon_min_deg` when
//                                    no line is drawn
//   walkTrack / minutesAboveFloor    the window, off the same to-dawn walk the
//                                    finder colours its track with
//   rankTargets                      the reach score
//
// If any of those is wrong, it is wrong in one place and both screens say the
// same wrong thing - which is the property that makes a bug findable.
//
// ONE FIX APPLIED HERE AND NOT THERE: the solar-system fetch reads `results`.
// `GET /api/catalog?q=…&explain=1` answers `{"results": [...], "notes": [...]}`
// (app.py's catalog handler), not `{"rows": …}`. Reported to the controller for
// `finder/model.ts`, which this task does not own.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../../../api";
import { getCloudmapDome, type CloudmapDome } from "../../../../api/cloudmap";
import { getSite } from "../../../../api/site";
import { lstHours, altAzOf } from "../../../../lib/altaz";
import { useCapability } from "../../../../lib/caps";
import { useConfig, useSite, useStore, useWeather } from "../../../../store";
import type { TonightResponse } from "../../../../types";
import { fmtClock } from "../../../lib/format";
import { rankTargets } from "../../../lib/reach";
import type { HorizonPoint } from "../../../lib/horizonModel";
import {
  D2R, SKY_KINDS, cloudPctAt, decorate, mergeRows, minutesAboveFloor, paletteFor,
  skyPrefs, tilesFromDome, walkTrack, isObstructedAt,
  type CatalogRowLike, type LensPrefs, type SkyKind, type SkyTarget, type TrackContext,
  type WheelLike,
} from "../finder";
import type { SearchRow } from "./targetsCatalog";
import { useVisibilityNight } from "./quickVisibility";

/** TonightPicker's two honest bounds, transcribed so the copy this sheet shows
 *  and the copy the Atlas shows cannot drift. */
export const SLOW_AFTER_S = 3;
export const GIVE_UP_S = 15;

/** The viewer's answer. `/api/catalog/tonight` is `view.site_derived`-gated, so
 *  a role without it gets the search field and this line - not an empty list,
 *  which would read as "nothing is up tonight". */
export const RANK_NEEDS_SITE = "Ranking tonight needs site access; search still works.";

export const RANK_DEFAULT_SITE =
  "Using a default location - set yours in Settings for accurate visibility.";

export const RANK_STILL_WORKING =
  "Still working - this works out where every catalog object will be tonight, which is "
  + `the slow part on a small rig. It stops after ${GIVE_UP_S}s and offers a retry rather `
  + "than spinning forever.";

export const RANK_TIMED_OUT = `No answer in ${GIVE_UP_S}s, so tonight wasn't ranked.`;

export const RANK_NOT_EMPTY =
  "This list is empty for that reason, not because nothing is up.";

export interface TargetsModel {
  /** Ranked and lens-filtered - the rows the sheet draws. */
  rows: SkyTarget[];
  /** Ranked, lens IGNORED - what each lens button's count has to promise. */
  all: SkyTarget[];
  reachCount: number;
  kindCounts: Record<SkyKind, number>;
  lens: LensPrefs;
  floorOnly: boolean;
  setLens: (kind: SkyKind, on: boolean) => void;
  setFloorOnly: (v: boolean) => void;
  clearPct: number | null;
  siteName: string;
  siteIsDefault: boolean;
  /** May this principal see a ranked list at all? */
  rankingAllowed: boolean;
  loading: boolean;
  /** Whole seconds waited, for the bounded-wait line. */
  waited: number;
  /** The reason there is no list, in the server's words where it gave one. */
  error: string | null;
  timedOut: boolean;
  retry: () => void;
  moonLine: string | null;
  darkLine: string | null;
}

const FLOOR_DEG = 25;

interface TonightState {
  rows: CatalogRowLike[];
  siteIsDefault: boolean;
  loading: boolean;
  error: string | null;
  timedOut: boolean;
}

export function useTargetsModel(): TargetsModel {
  const site = useSite();
  const config = useConfig();
  const weather = useWeather();
  const wheelKey = useStore((s) => (s.status?.filterwheel?.names ?? []).join("|"));
  const wheelOpaqueKey = useStore((s) =>
    (s.status?.filterwheel?.opaque ?? []).map((b) => (b ? 1 : 0)).join(""));
  const wheelNbKey = useStore((s) =>
    (s.status?.filterwheel?.narrowband ?? []).map((b) => (b ? 1 : 0)).join(""));

  const rankingAllowed = useCapability("view.site_derived");
  const weatherAllowed = useCapability("view.weather");

  const lat = site?.latitude ?? 0;
  const lon = site?.longitude ?? 0;
  const horizonMinDeg = site?.horizon_min_deg ?? 0;
  const siteKey = `${site?.name ?? ""}:${lat}:${lon}`;

  const [attempt, setAttempt] = useState(0);
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    // 30 s, per the plan's B.2: an altitude that is recomputed per animation
    // frame costs a phone its battery and moves nothing a reader can see.
    const id = setInterval(() => setNowMs(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  // ---------------------------------------------------------- the ranked set
  const [tonight, setTonight] = useState<TonightState>({
    rows: [], siteIsDefault: false, loading: true, error: null, timedOut: false,
  });
  const [waited, setWaited] = useState(0);

  useEffect(() => {
    if (!rankingAllowed) {
      setTonight({ rows: [], siteIsDefault: false, loading: false, error: null, timedOut: false });
      return;
    }
    let alive = true;
    setTonight((s) => ({ ...s, loading: true, error: null, timedOut: false }));
    setWaited(0);
    const t0 = Date.now();
    const tick = setInterval(() => {
      if (alive) setWaited(Math.floor((Date.now() - t0) / 1000));
    }, 1000);
    void api
      .get<TonightResponse>(`/api/catalog/tonight?alt_limit=${encodeURIComponent(horizonMinDeg)}`)
      .then((res) => {
        if (!alive) return;
        setTonight({
          rows: Array.isArray(res?.picks) ? (res.picks as unknown as CatalogRowLike[]) : [],
          siteIsDefault: res?.site_is_default === true,
          loading: false, error: null, timedOut: false,
        });
      })
      .catch((e: unknown) => {
        if (!alive) return;
        const msg = (e as { message?: string } | null)?.message ?? "couldn't rank tonight";
        const timedOut = (e as { timedOut?: boolean } | null)?.timedOut === true;
        setTonight({
          rows: [], siteIsDefault: false, loading: false,
          // A hyphen where TonightPicker has an em-dash: ARCHITECTURE
          // non-negotiable 5. Nothing the sentence says has changed.
          error: timedOut ? RANK_TIMED_OUT : `Couldn't rank tonight - ${msg}.`,
          timedOut,
        });
      })
      .finally(() => { clearInterval(tick); });
    return () => { alive = false; clearInterval(tick); };
  }, [rankingAllowed, horizonMinDeg, siteKey, attempt]);

  // ------------------------------------------------------- the solar system
  const [bodies, setBodies] = useState<CatalogRowLike[]>([]);
  useEffect(() => {
    if (!rankingAllowed) { setBodies([]); return; }
    let alive = true;
    const one = (q: string): Promise<SearchRow[]> =>
      api
        .get<{ results?: SearchRow[] }>(`/api/catalog?q=${q}&explain=1`)
        .then((r) => (Array.isArray(r?.results) ? r.results : []))
        .catch(() => [] as SearchRow[]);
    void Promise.all([one("planet"), one("moon")]).then(([p, m]) => {
      if (!alive) return;
      // `q=planet` also matches every PLANETARY NEBULA in the DSO catalogue
      // (`_TYPE_NAMES["PN"]`), so the filter is on the row's own discriminator,
      // not on the query that found it.
      setBodies(
        [...p, ...m].filter(
          (r) => r.kind === "solar_system" && (r.type === "Planet" || r.type === "Moon"),
        ) as unknown as CatalogRowLike[],
      );
    });
    return () => { alive = false; };
  }, [rankingAllowed, attempt]);

  // ------------------------------------------------------------- the clouds
  const [dome, setDome] = useState<CloudmapDome | null>(null);
  useEffect(() => {
    if (!weatherAllowed) { setDome(null); return; }
    let alive = true;
    void getCloudmapDome(6, 10).then((d) => { if (alive) setDome(d); }).catch(() => {});
    return () => { alive = false; };
  }, [weatherAllowed]);

  // ------------------------------------------------------------ the horizon
  // `GET /api/site` echoes the APPLIED polyline (`config.safety.horizon`) and is
  // the only route that does. A saved location's own copy is the library's -
  // drawing that one would show a line the engine is not gating on.
  const [applied, setApplied] = useState<HorizonPoint[] | null>(null);
  useEffect(() => {
    let alive = true;
    void getSite()
      .then((res) => {
        if (!alive) return;
        const raw = res?.site?.horizon_points;
        setApplied(
          Array.isArray(raw)
            ? raw.filter((p) => Array.isArray(p) && p.length >= 2).map(([az, alt]) => ({ az, alt }))
            : null,
        );
      })
      .catch(() => { if (alive) setApplied(null); });
    return () => { alive = false; };
  }, [siteKey]);

  const horizonPoints: HorizonPoint[] = useMemo(() => {
    if (applied && applied.length > 0) return applied;
    const cfg = config?.safety?.horizon;
    if (Array.isArray(cfg) && cfg.length > 0) return cfg.map(([az, alt]) => ({ az, alt }));
    return [];
  }, [applied, config]);

  // --------------------------------------------------------------- the lens
  const [lens, setLensObj] = useState<LensPrefs>(() => skyPrefs.getLens());
  const [floorOnly, setFloorOnlyState] = useState<boolean>(() => skyPrefs.getFloorOnly());
  const setLens = useCallback((kind: SkyKind, on: boolean) => {
    setLensObj((cur) => {
      const next = { ...cur, [kind]: on };
      skyPrefs.setLens(next);
      return next;
    });
  }, []);
  const setFloorOnly = useCallback((v: boolean) => {
    setFloorOnlyState(v);
    skyPrefs.setFloorOnly(v);
  }, []);

  // ----------------------------------------------------------- the ranking
  const tiles = useMemo(() => tilesFromDome(dome), [dome]);
  const hourlyCloud = useMemo(() => {
    const f = weather?.forecast;
    if (!f || !Array.isArray(f.times) || !Array.isArray(f.cloud)) return null;
    let best = -1;
    let gap = Infinity;
    for (let i = 0; i < f.times.length && i < f.cloud.length; i++) {
      const t = Date.parse(f.times[i]);
      if (Number.isNaN(t)) continue;
      const d = Math.abs(t - nowMs);
      if (d < gap) { gap = d; best = i; }
    }
    return best >= 0 && gap <= 3600_000 ? f.cloud[best] : null;
  }, [weather, nowMs]);

  const wheel: WheelLike | null = useMemo(() => {
    if (wheelKey === "") return null;
    return {
      names: wheelKey.split("|"),
      opaque: wheelOpaqueKey === "" ? undefined : wheelOpaqueKey.split("").map((c) => c === "1"),
      narrowband: wheelNbKey === "" ? undefined : wheelNbKey.split("").map((c) => c === "1"),
    };
  }, [wheelKey, wheelOpaqueKey, wheelNbKey]);

  const trackCtx: TrackContext = useMemo(
    () => ({
      latDeg: lat,
      // Eight hours when the ephemeris has not been asked for here: the window
      // column is a comparison between rows, and every row is walked over the
      // same span, so the ORDER is right even where the absolute number is a
      // bound rather than tonight's exact dawn.
      hoursToDawn: 8,
      horizon: horizonPoints,
      horizonMinDeg,
      maskOn: true,
      holdAt: () => false,
    }),
    [lat, horizonPoints, horizonMinDeg],
  );

  const all: SkyTarget[] = useMemo(() => {
    const merged = mergeRows(tonight.rows, bodies);
    const nowSec = nowMs / 1000;
    const lst = lstHours(lon, nowSec);
    const cloudById = new Map<string, number | null>();
    const scored = merged.map((m) => {
      const here = m.altHint != null && m.azHint != null
        ? { altDeg: m.altHint, azDeg: m.azHint }
        : altAzOf(m.ra_hours, m.dec_deg, lat, lon, nowSec);
      const obstructed = isObstructedAt(here.altDeg, here.azDeg, trackCtx);
      const cloudPct = cloudPctAt(tiles, here.altDeg, here.azDeg) ?? hourlyCloud;
      cloudById.set(m.id, cloudPct);
      const dec = decorate(cloudPct, obstructed);
      const winMin = minutesAboveFloor(
        walkTrack(m.dec_deg * D2R, (lst - m.ra_hours) * 15 * D2R, trackCtx),
      );
      const transitLabel = m.transitUnix == null
        ? "—"
        : m.transitUnix * 1000 < nowMs
          ? "passed"
          : fmtClock(m.transitUnix * 1000, m.transitUnix * 1000);
      return {
        id: m.id,
        name: m.name,
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
        // ReachInput. An ABSENT reading must not score against a target:
        // penalising an unknown cloud would rank an object down for a
        // measurement nobody took.
        minutesAboveFloorToDawn: winMin,
        cloudPct: cloudPct ?? 0,
        moonSepDeg: m.moonSepDeg ?? 90,
      };
    });
    return rankTargets(scored).map((r) => ({
      id: r.id,
      name: r.name,
      full: r.full,
      kind: r.kind,
      ra_hours: r.ra_hours,
      dec_deg: r.dec_deg,
      altNow: r.altNow,
      azNow: r.azNow,
      cloudPct: cloudById.get(r.id) ?? null,
      obstructed: r.obstructed,
      clouded: r.clouded,
      color: r.color,
      statusTxt: r.statusTxt,
      palette: r.palette,
      transitLabel: r.transitLabel,
      windowMinutes: r.windowMinutes,
      score: r.score,
      moonSepDeg: r.moonSepDeg,
      difficulty: r.difficulty,
    }));
  }, [tonight.rows, bodies, nowMs, lat, lon, trackCtx, tiles, hourlyCloud, wheel]);

  const rows = useMemo(
    () => all.filter((t) => lens[t.kind] !== false && !(floorOnly && t.altNow < FLOOR_DEG)),
    [all, lens, floorOnly],
  );

  const kindCounts = useMemo(() => {
    const out = {} as Record<SkyKind, number>;
    for (const k of SKY_KINDS) out[k] = 0;
    for (const t of all) {
      if (floorOnly && t.altNow < FLOOR_DEG) continue;
      if (!t.obstructed && !t.clouded) out[t.kind] += 1;
    }
    return out;
  }, [all, floorOnly]);

  const reachCount = useMemo(
    () => rows.filter((t) => !t.obstructed && !t.clouded).length,
    [rows],
  );

  const clearPct = hourlyCloud == null ? null : Math.round(100 - hourlyCloud);

  // The dark window and the moon are properties of the SITE, not of a target,
  // so one ephemeris call serves the whole header. It is anchored on the best
  // ranked object because the route needs a position to answer for; nothing in
  // the two lines below is a property of that object.
  const anchor = all[0] ?? null;
  const night = useVisibilityNight(anchor?.ra_hours ?? null, anchor?.dec_deg ?? null, horizonMinDeg);

  const darkLine = useMemo(() => {
    const a = night?.dark_start_unix;
    const b = night?.dark_end_unix;
    if (typeof a !== "number" || typeof b !== "number") {
      // Not a gap: a site can genuinely have no astronomical darkness tonight,
      // and printing a blank range would read as a failed fetch.
      return night ? "no astro-dark tonight" : null;
    }
    return `dark ${fmtClock(a * 1000, a * 1000)}-${fmtClock(b * 1000, b * 1000)}`;
  }, [night]);

  const moonLine = useMemo(() => {
    const m = night?.moon;
    if (!m) return null;
    const pct = Math.round((m.illumination ?? 0) * 100);
    const when = typeof m.set_unix === "number"
      ? `sets ${fmtClock(m.set_unix * 1000, m.set_unix * 1000)}`
      : typeof m.rise_unix === "number"
        ? `rises ${fmtClock(m.rise_unix * 1000, m.rise_unix * 1000)}`
        : (m.phase_name ?? "").toLowerCase();
    return `moon ${pct}%${when ? ` ${when}` : ""}`;
  }, [night]);

  return {
    rows,
    all,
    reachCount,
    kindCounts,
    lens,
    floorOnly,
    setLens,
    setFloorOnly,
    clearPct,
    siteName: site?.name ?? "Set a site",
    siteIsDefault: tonight.siteIsDefault || site?.is_default === true,
    rankingAllowed,
    loading: tonight.loading,
    waited,
    error: rankingAllowed ? tonight.error : RANK_NEEDS_SITE,
    timedOut: tonight.timedOut,
    retry: () => setAttempt((a) => a + 1),
    moonLine,
    darkLine,
  };
}
