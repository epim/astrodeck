// targets.ts - the three catalogue sources merged into one list of things the
// finder can draw, plus the kind mapping, the status decoration and the filter
// palette (hub-sky plan B.2 and B.12).
//
// TWO ROW-SHAPE TRAPS, both verified in the server source and both encoded here
// rather than at every call site:
//
//   1. `/api/catalog` and `/api/catalog/region` rows carry a `kind` discriminator
//      ("dso" | "star" | "solar_system" | "satellite" | "comet" | "coordinates");
//      `/api/catalog/tonight` builds its picks inline and carries NONE, because
//      every pick is deep-sky. So `kind` is read FIRST and `type` only refines it.
//   2. For a solar-system row the fields are the other way round from a DSO:
//      `id` is the LABEL ("Jupiter") and `name` is the whole describe SENTENCE
//      (solar_system.py row()). A marker pill or a lock-card title that renders
//      `name` for a body prints a paragraph. `displayName`/`fullName` below are
//      the only two functions allowed to decide which field is which.
//      SATELLITES AND COMETS ARE THE SECOND AND THIRD ROW FAMILIES WITH THAT
//      SHAPE (`ephemeris/satellites.py:421-441`, `ephemeris/comets.py:453-490`),
//      which is why the branch below tests a SET of kinds and not one string.
//
// The Sun is dropped outright: it is offered only inside a solar session and has
// no business on a deep-sky finder. Satellites and comets are no longer dropped
// (wave S7 gave the engine SGP4 and MPC elements - decision D-SKY-1); what a
// satellite may NOT do is be drawn on the reticle, see SATELLITE_MARKERS below.

import type { DifficultyTier } from "../../../../types";
import type { NxIconName } from "../../../icons";

/** The seven kinds the lens can show - the README's own seven, complete since
 *  wave S7 put satellite and comet ephemerides on the engine (D-SKY-1). */
export type SkyKind =
  | "galaxy"
  | "nebula"
  | "cluster"
  | "planet"
  | "moon"
  | "satellite"
  | "comet";

export const SKY_KINDS: readonly SkyKind[] = [
  "galaxy", "nebula", "cluster", "planet", "moon", "satellite", "comet",
];

/**
 * MAY A SATELLITE BE DRAWN ON THE RETICLE? No, and this is the one line that
 * decides it. Two reasons, and only the first of them has been fixed.
 *
 * 1. THE SERVER OVERWRITE - FIXED, VERIFIED 2026-09-10. `api/app.py` used to
 *    recompute `alt`/`az` from `ra_hours`/`dec_deg` for every row it served a
 *    `view.site_derived` caller. A satellite's RA/Dec is GEOCENTRIC
 *    (`ephemeris/satellites.py:414-416`) while the alt/az the module computed
 *    is TOPOCENTRIC (`:411`), so at 400 km the route replaced the right horizon
 *    position with one tens of degrees away. S7L landed the guard
 *    (`api/app.py:7473-7482`, `if r.get("kind") == "satellite": continue`), so
 *    the alt/az on the wire are now correct AT THE MOMENT THEY WERE COMPUTED.
 *
 * 2. THE CADENCE - NOT FIXED, and it is why this stays false. `useEphemerisRows`
 *    refreshes on `SOLAR_TTL_MS` (120 s), which is right for a comet and absurd
 *    for a body in low Earth orbit: the ISS covers about four degrees of sky a
 *    SECOND, so a two-minute-old position is not slightly stale, it is a
 *    different part of the sky. Everything else the finder would do with the
 *    row is sidereal too - `walkTrack` draws an hour-angle arc, `rankTargets`
 *    scores minutes above the floor to dawn, `paletteFor` names filters - and
 *    none of that describes a five-minute pass.
 *
 * So a satellite is a LIST target: it appears in the search sheet, and picking
 * it opens the passes list, which is the answer the decision actually asked for
 * (wave-u7b risk R1's recommended default). Flipping this to `true` needs a
 * per-second position source, not a constant change - but every branch behind
 * it is written, so the constant is where that work starts.
 */
export const SATELLITE_MARKERS = false;

/** Lens-dial captions, in the design's own words. */
export const KIND_LABEL: Record<SkyKind, string> = {
  galaxy: "GALAXIES",
  nebula: "NEBULAE",
  cluster: "CLUSTERS",
  planet: "PLANETS",
  moon: "MOON",
  satellite: "SATELLITES",
  comet: "COMETS",
};

/** Which `next/icons.tsx` glyph draws each kind - the same paths the prototype's
 *  `KG` table used, so a marker on the finder and a row in the ranked list are
 *  the same drawing. */
export const KIND_ICON: Record<SkyKind, NxIconName> = {
  galaxy: "galaxy",
  nebula: "nebula",
  cluster: "cluster",
  planet: "planet",
  moon: "moon",
  satellite: "satellite",
  comet: "comet",
};

/** A row from any of the three sources, in the loosest shape all three satisfy. */
export interface CatalogRowLike {
  id: string;
  name?: string | null;
  type?: string | null;
  /** "dso" | "star" | "solar_system" | "coordinates" - absent on tonight picks. */
  kind?: string | null;
  ra_hours: number;
  dec_deg: number;
  /** Present only for a caller holding view.site_derived. */
  alt?: number | null;
  az?: number | null;
  mag?: number | null;
  size_arcmin?: number | null;
  difficulty?: DifficultyTier;
  moon_sep_deg?: number | null;
  /** Region rows carry a composed sentence; tonight picks do not. */
  describe?: string | null;
  /** Region rows call the short label `label`, not `name`. */
  label?: string | null;
  transit_unix?: number | null;
  never_rises_above_limit?: boolean;
}

const NEBULA_TYPES = new Set([
  "Emission Nebula",
  "Reflection Nebula",
  "Planetary Nebula",
  "Dark Nebula",
  "Supernova Remnant",
  "Nebula",
]);
const CLUSTER_TYPES = new Set(["Open Cluster", "Globular Cluster"]);

/** Types that answer to narrowband. Reflection nebulae and dark nebulae do not:
 *  they are dust lit (or not) by broadband starlight, so an Ha filter shows
 *  nothing. That is why this is a smaller set than NEBULA_TYPES. */
const NARROWBAND_TYPES = new Set([
  "Emission Nebula",
  "Planetary Nebula",
  "Supernova Remnant",
]);

/**
 * The finder kind for a catalogue row, or `null` when the row must be dropped.
 *
 * Dropped: the Sun (solar sessions only), and any other solar-system body the
 * engine does not carry an ephemeris for. Everything unrecognised that is NOT a
 * solar-system row falls back to `nebula`, which is the prototype's own default
 * and keeps a star or a typed coordinate drawable.
 */
export function kindOf(row: CatalogRowLike): SkyKind | null {
  const type = (row.type ?? "").trim();
  // BEFORE the type table, and deliberately: a satellite row's `type` is the
  // word "Satellite" and a comet's is "Comet", neither of which the DSO tables
  // below know, so both would fall through to the `nebula` default and a comet
  // would be drawn with a nebula's glyph on the finder and in the ranked list.
  if (row.kind === "satellite") return "satellite";
  if (row.kind === "comet") return "comet";
  if (row.kind === "solar_system") {
    if (type === "Moon") return "moon";
    if (type === "Planet") return "planet";
    // Sun, and anything else the ephemeris might grow later, is not drawn here.
    return null;
  }
  if (type === "Galaxy") return "galaxy";
  if (NEBULA_TYPES.has(type)) return "nebula";
  if (CLUSTER_TYPES.has(type)) return "cluster";
  return "nebula";
}

/**
 * Row families whose `id` is the LABEL and whose `name` is a composed SENTENCE.
 *
 * Three of them now, which is why this is a set and not a string comparison:
 * `solar_system.py row()`, `ephemeris/satellites.py:421-441` and
 * `ephemeris/comets.py:453-490` all build a row that way. A card that printed
 * `name` for one of these would print a paragraph where a title belongs.
 */
const SENTENCE_NAMED = new Set(["solar_system", "satellite", "comet"]);

/** The short label: `id` for a body, satellite or comet; the common name (or
 *  designation) otherwise. */
export function displayName(row: CatalogRowLike): string {
  if (row.kind != null && SENTENCE_NAMED.has(row.kind)) return row.id;
  const n = (row.label ?? row.name ?? "").trim();
  return n !== "" ? n : row.id;
}

/**
 * The second line, when the server actually wrote one: the composed sentence for
 * a body or a region row, or a common name that differs from the short label.
 *
 * Returns "" when there is none. It deliberately does NOT fall back to the
 * readable type - a merge that filled this in with "Galaxy" would then refuse the
 * real sentence when the region row for the same object arrives, because it
 * already had something non-empty. The caller applies the type fallback at read
 * time instead.
 */
export function fullName(row: CatalogRowLike): string {
  if (row.kind != null && SENTENCE_NAMED.has(row.kind)) return (row.name ?? "").trim();
  const d = (row.describe ?? "").trim();
  if (d !== "") return d;
  // `name` is a SECOND line only where the row also carries a short `label` (the
  // region shape). On a ranked pick, `name` IS the short label - returning it
  // here would print "M31" twice, once on each line of the lock card.
  const label = (row.label ?? "").trim();
  const n = (row.name ?? "").trim();
  if (label !== "" && n !== "" && n !== label) return n;
  return "";
}

/** Cloud fraction at or above which a target counts as clouded (proto logic.js:338). */
export const CLOUDED_PCT = 40;

export interface Decoration {
  clouded: boolean;
  /** A design token, not a hex literal: night mode swaps the token, and a
   *  hard-coded #00D2FF would stay cyan on a red-adapted screen. */
  color: string;
  statusTxt: string;
}

/**
 * Ring colour and status chip for one target.
 *
 * `cloudPct === null` is NOT zero cloud - it means no granule pair exists yet
 * (cloudmap `basis: "no_data"`) or weather is off. Saying "CLEAR" there would be
 * a claim nothing keeps, so the chip says the altitude is known and the cloud is
 * not.
 */
export function decorate(cloudPct: number | null, obstructed: boolean): Decoration {
  const clouded = cloudPct != null && cloudPct >= CLOUDED_PCT;
  const color = obstructed ? "var(--bad)" : clouded ? "var(--warn)" : "var(--accent)";
  const statusTxt = obstructed
    ? "BEHIND HORIZON"
    : cloudPct == null
      ? "UP · cloud —"
      : clouded
        ? `CLOUD ${Math.round(cloudPct)}%`
        : `CLEAR · ${Math.round(cloudPct)}%`;
  return { clouded, color, statusTxt };
}

/**
 * "In reach" - the one predicate behind the status row's count, the reach
 * strip, the per-kind counts on the lens dial and the auto-aim's first pick.
 *
 * It is exported rather than inlined at each of those four sites because the
 * count on the status row and the list underneath it MUST be the same rule: a
 * strip showing four chips over a row saying "3 in reach" is the finder
 * disagreeing with itself, and both halves would look entirely plausible.
 *
 * Deliberately NOT part of it: the 25 degree seeing floor. That is an advisory
 * quality line the user switches on (`floorOnly`), not a reason a target is out
 * of reach - the horizon is what decides reach, and the horizon is the site's.
 */
export function inReach(t: { obstructed: boolean; clouded: boolean }): boolean {
  return !t.obstructed && !t.clouded;
}

export interface WheelLike {
  names?: string[];
  narrowband?: boolean[];
  opaque?: boolean[];
}

const NB_NAME = /^(ha|h-?alpha|hb|h-?beta|oiii|o3|sii|s2|nii|n2|narrow)/i;

/** Usable narrowband slots on the wheel. The server's own `narrowband[]` flag
 *  wins where it exists; the name test is the fallback for a wheel that never
 *  reported one. Blackout slots are excluded - they hold no glass. */
export function narrowbandSlots(wheel: WheelLike | null | undefined): number {
  const names = wheel?.names ?? [];
  let n = 0;
  for (let i = 0; i < names.length; i++) {
    if (wheel?.opaque?.[i]) continue;
    const flag = wheel?.narrowband?.[i];
    if (flag === true) { n++; continue; }
    if (flag === false) continue;
    if (NB_NAME.test((names[i] ?? "").trim())) n++;
  }
  return n;
}

/**
 * The filter palette this target would be shot in - DERIVED from the wheel the
 * rig reports, never stored and never typed. Filter names land in the FITS
 * FILTER card and in the calibration key, so the only honest source is the
 * driver.
 */
export function paletteFor(
  kind: SkyKind,
  type: string,
  wheel: WheelLike | null | undefined,
): string {
  if (kind === "moon" || kind === "planet") return "RGB video";
  const usable = (wheel?.names ?? []).filter((n, i) => !wheel?.opaque?.[i] && (n ?? "").trim() !== "");
  if (usable.length === 0) return "OSC";
  if (!NARROWBAND_TYPES.has(type)) return "LRGB";
  const nb = narrowbandSlots(wheel);
  if (nb >= 3) return "SHO";
  if (nb === 2) return "HOO";
  if (nb === 1) return "Ha + LRGB";
  return "LRGB";
}

/** One entry of the merged list, before altitude/cloud/horizon are applied. */
export interface MergedRow {
  id: string;
  name: string;
  full: string;
  kind: SkyKind;
  type: string;
  ra_hours: number;
  dec_deg: number;
  moonSepDeg: number | null;
  transitUnix: number | null;
  difficulty?: DifficultyTier;
  /**
   * The catalogued MAJOR axis in arcminutes, or null where the row carried no
   * extent at all. All three sources send `size_arcmin` (objects.py, region.py,
   * solar_system.py) and none of them sends a minor axis or a position angle -
   * `ngc_extras.minax` exists on the server but is never put on a row - so this
   * is one number, not an ellipse. `0` is a real answer (a star IS a point
   * source at any focal length this rig has); `null` is "nobody said".
   */
  sizeArcmin: number | null;
  /** Server-supplied alt/az where the route sent one (solar-system rows). */
  altHint: number | null;
  azHint: number | null;
}

function toMerged(row: CatalogRowLike): MergedRow | null {
  const kind = kindOf(row);
  if (kind == null) return null;
  if (!Number.isFinite(row.ra_hours) || !Number.isFinite(row.dec_deg)) return null;
  return {
    id: row.id,
    name: displayName(row),
    full: fullName(row),
    kind,
    type: (row.type ?? "").trim(),
    ra_hours: row.ra_hours,
    dec_deg: row.dec_deg,
    moonSepDeg: typeof row.moon_sep_deg === "number" ? row.moon_sep_deg : null,
    transitUnix: typeof row.transit_unix === "number" ? row.transit_unix : null,
    difficulty: row.difficulty,
    sizeArcmin:
      typeof row.size_arcmin === "number" && Number.isFinite(row.size_arcmin)
        ? row.size_arcmin
        : null,
    altHint: typeof row.alt === "number" ? row.alt : null,
    azHint: typeof row.az === "number" ? row.az : null,
  };
}

/**
 * Union the three sources by id, earlier sources winning on identity and later
 * ones filling in whatever they know that the earlier one did not (a region row
 * carries `describe`, which a tonight pick has no field for; a tonight pick
 * carries `moon_sep_deg` and `transit_unix`, which a region row has no field
 * for). Nothing is overwritten with a blank.
 */
export function mergeRows(...sources: CatalogRowLike[][]): MergedRow[] {
  const byId = new Map<string, MergedRow>();
  for (const src of sources) {
    for (const raw of src) {
      const row = toMerged(raw);
      if (!row) continue;
      const prev = byId.get(row.id);
      if (!prev) { byId.set(row.id, row); continue; }
      byId.set(row.id, {
        ...prev,
        full: prev.full !== "" ? prev.full : row.full,
        type: prev.type !== "" ? prev.type : row.type,
        moonSepDeg: prev.moonSepDeg ?? row.moonSepDeg,
        transitUnix: prev.transitUnix ?? row.transitUnix,
        difficulty: prev.difficulty ?? row.difficulty,
        sizeArcmin: prev.sizeArcmin ?? row.sizeArcmin,
        altHint: prev.altHint ?? row.altHint,
        azHint: prev.azHint ?? row.azHint,
      });
    }
  }
  return Array.from(byId.values());
}

/** The finder's view of one target - the public shape T-SKY-2 codes against. */
export interface SkyTarget {
  id: string;
  name: string;
  full: string;
  kind: SkyKind;
  ra_hours: number;
  dec_deg: number;
  altNow: number;
  azNow: number;
  /** null = no reading, which is NOT 0% cloud. */
  cloudPct: number | null;
  obstructed: boolean;
  clouded: boolean;
  color: string;
  statusTxt: string;
  palette: string;
  /**
   * The catalogued major axis, arcminutes. Absent where the row carried no
   * extent - and absent is NOT zero: FRAME draws the catalogue-extent ellipse
   * from this, and a 0 handed over as if it were a measurement would draw M31
   * as a point.
   */
  sizeArcmin?: number;
  /** "23:52" / "passed" / "—" - the bare value; callers prefix "transit ". */
  transitLabel: string;
  windowMinutes: number;
  score: number;
  moonSepDeg: number | null;
  difficulty?: DifficultyTier;
}

/** A projected target, ready to draw. */
export interface Marker {
  id: string;
  name: string;
  kind: SkyKind;
  color: string;
  x: number;
  y: number;
  /** "58°" - the altitude tail on the marker pill. */
  altTag: string;
  target: SkyTarget;
}

/** Lock radius, px (proto/logic.js:347). It is the same 46 px as the dashed
 *  square drawn at the box centre, so the rule and the affordance cannot drift
 *  apart: a target the user can see inside the square is the target that locks. */
export const LOCK_RADIUS_PX = 46;

/**
 * The lock: the NEAREST marker whose centre is inside the radius, or null.
 *
 * Nearest and not first - with two objects in the square, the one the reticle is
 * actually on is the one the IMAGE THIS button should name.
 */
export function pickLock(markers: Marker[], boxW: number, boxH: number): Marker | null {
  let best: Marker | null = null;
  let bestD = Infinity;
  for (const m of markers) {
    const d = Math.hypot(m.x - boxW / 2, m.y - boxH / 2);
    if (d < LOCK_RADIUS_PX && d < bestD) { bestD = d; best = m; }
  }
  return best;
}

