// prefs.ts - the Sky hub's per-phone state, all of it, in one place.
//
// ARCHITECTURE.md section 9: every key is prefixed `astrodeck-next-`, every read
// and write is wrapped in try/catch, and every screen must render correctly with
// nothing stored. Private browsing, a cleared origin and an embedded WebView that
// throws on `localStorage` access all land in the same place: the default.
//
// This module is the ONLY file in the Sky hub allowed to touch `localStorage`.
// The reason is not tidiness: a key written in two places is a key that drifts,
// and the one that drifted here would silently un-hide a target kind the user
// switched off.
//
// Deliberately NOT persisted: `az`, `alt`, `trackId`, `gyro`, `frame.*`. A finder
// that reopens pointing where it was last night is worse than one that reopens
// at the best target tonight.
//
// THREE KEYS LEFT THIS FILE FOR THE RIG (D-FU-1). The quick-plan defaults, the
// target pool and the last-chosen site id were never per-phone preferences:
// they decide what tonight shoots, so the same operator on a tablet got a blank
// shortlist and the rig itself could not tell a second client what it had
// learned. They live on the server now (`config.planning`, and
// `AppConfig.active_location_id` for the site) and are read and written through
// `next/lib/planning.ts`.
//
// What survives here of those three is `LEGACY_RIG_KEYS` and the four
// `readLegacy*`/`writeLegacy*` functions below. They exist for exactly two jobs
// and are deleted in the wave after this one: the migration reads them once to
// hand the phone's copy to the rig, and a rig too old to carry the block (a 404
// from `GET /api/planning`) keeps working off them, unchanged, rather than
// losing a night's settings to an upgrade it never asked for.
//
// The six keys that REMAIN here are genuine per-phone preferences - lens,
// layers, mode, floor, frame mode, survey brightness. Each is a property of
// this screen on this device (which kinds this user wants drawn, how bright the
// survey is on this panel), none of them reaches the engine, and none of them
// would mean anything to a second client.

import type { SkyKind } from "./targets";
import { SKY_KINDS } from "./targets";

const K = {
  lens: "astrodeck-next-sky-lens",
  layers: "astrodeck-next-sky-layers",
  mode: "astrodeck-next-sky-mode",
  floor: "astrodeck-next-sky-floor",
  frameMode: "astrodeck-next-sky-frame-mode",
  surveyBright: "astrodeck-next-sky-survey-bright",
} as const;

/** The three keys D-FU-1 moved to the rig, named here because the migration
 *  still has to find them (and, for `site`, delete them) on a phone that has
 *  been running this app for months. `next/lib/planning.ts` is the only caller:
 *  it reads them once for the migration, and writes them ONLY while the rig has
 *  no home for the block. No screen touches them. */
export const LEGACY_RIG_KEYS = {
  quick: "astrodeck-next-sky-quick",
  pool: "astrodeck-next-sky-pool",
  /** The saved-location id the Sites sheet last applied. It never had a reader
   *  anywhere in the app - the server has held the pointer as
   *  `AppConfig.active_location_id` since `POST /api/locations/{id}/apply`
   *  landed - so the migration deletes it and writes nothing. */
  site: "astrodeck-next-sky-site",
} as const;

function readRaw(key: string): string | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeRaw(key: string, value: string): void {
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(key, value);
  } catch {
    /* quota, private mode, a WebView that throws - the UI still works */
  }
}

function readJson<T>(key: string, fallback: T): T {
  const raw = readRaw(key);
  if (raw == null) return fallback;
  try {
    const v = JSON.parse(raw) as unknown;
    return v == null ? fallback : (v as T);
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    writeRaw(key, JSON.stringify(value));
  } catch {
    /* a value that cannot be serialised is not worth crashing a finder over */
  }
}

// ------------------------------------------------------------------- lens
export type LensPrefs = Record<SkyKind, boolean>;

export function defaultLens(): LensPrefs {
  const out = {} as LensPrefs;
  for (const k of SKY_KINDS) out[k] = true;
  return out;
}

/** Stored value merged OVER the default, so a kind added in a later release
 *  shows up rather than being silently absent from an old stored object. */
export function getLens(): LensPrefs {
  const stored = readJson<Partial<Record<string, unknown>>>(K.lens, {});
  const out = defaultLens();
  for (const k of SKY_KINDS) {
    if (typeof stored[k] === "boolean") out[k] = stored[k] as boolean;
  }
  return out;
}

export function setLens(v: LensPrefs): void {
  writeJson(K.lens, v);
}

// ----------------------------------------------------------------- layers
export interface LayerPrefs {
  clouds: boolean;
  horizon: boolean;
  wind: boolean;
}

export const DEFAULT_LAYERS: LayerPrefs = { clouds: true, horizon: true, wind: true };

export function getLayers(): LayerPrefs {
  const stored = readJson<Partial<Record<string, unknown>>>(K.layers, {});
  return {
    clouds: typeof stored.clouds === "boolean" ? stored.clouds : DEFAULT_LAYERS.clouds,
    horizon: typeof stored.horizon === "boolean" ? stored.horizon : DEFAULT_LAYERS.horizon,
    wind: typeof stored.wind === "boolean" ? stored.wind : DEFAULT_LAYERS.wind,
  };
}

export function setLayers(v: LayerPrefs): void {
  writeJson(K.layers, v);
}

// ------------------------------------------------------------------- mode
/**
 * The three things the Sky hub's box can be.
 *
 * `atlas` is the fourth toolbar button (MAP / FRAME / GYRO / ATLAS) and mounts
 * the classic pannable survey canvas full-frame. It is a MODE rather than a
 * route because it is a way of looking at the same screen, and it is persisted
 * here with the other two for the same reason they are: which of them a phone
 * opens in is a property of that phone (a tablet on the desk wants the atlas,
 * a phone in the field wants the reticle), it never reaches the engine, and it
 * would mean nothing to a second client. Nothing about the rig is stored -
 * only which view this device last chose.
 */
export type SkyMode = "cam" | "map" | "atlas";

/** The default differs by device, so the caller passes it: a phone opens in AR,
 *  a desktop has no camera worth pointing at the sky and opens in MAP. ATLAS is
 *  never a default - it is only ever a choice this phone made. */
export function getMode(fallback: SkyMode): SkyMode {
  const raw = readRaw(K.mode);
  return raw === "cam" || raw === "map" || raw === "atlas" ? raw : fallback;
}

export function setMode(v: SkyMode): void {
  writeRaw(K.mode, v);
}

// ------------------------------------------------------------- floor chip
export function getFloorOnly(): boolean {
  return readRaw(K.floor) === "1";
}

export function setFloorOnly(v: boolean): void {
  writeRaw(K.floor, v ? "1" : "0");
}

// ------------------------------------------------------ target pool (LEGACY)
//
// The pool is `config.planning.pool` now. These two are the migration's readers
// and the older-rig fallback; they are deleted with the rest of the legacy block
// next wave. See the header.
export function readLegacyPool(): string[] {
  const v = readJson<unknown>(LEGACY_RIG_KEYS.pool, []);
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

export function writeLegacyPool(v: string[]): void {
  writeJson(LEGACY_RIG_KEYS.pool, v);
}

// ---------------------------------------------- quick-session defaults (SHAPE)
//
// ONE SHAPE AND ONE PARSER, and this is it - now for two sources rather than
// one. `QuickPrefs` is the browser's spelling of the server's `QuickDefaults`
// (`config.py:1094-1152`); the two differ in exactly one field name, and
// `next/lib/planning.ts` owns that single rename. `coerceQuickPrefs` is the
// parser BOTH sources go through, so a value off the wire and a value off an
// old phone cannot disagree about what "absent means checked" means.
//
// There were once two keys: the Sky hub's quick sheet wrote
// `astrodeck-next-sky-quick` through this module on GENERATE FLOW, and Settings
// > SKY > QUICK SESSION DEFAULTS read and wrote its OWN `astrodeck-next-quick`
// with its own shape. Neither ever saw the other, so the settings sheet said
// "nothing learned yet" forever and every edit made there was ignored by the
// sheet that generates the night (review #4). Both sheets now read the rig.
export interface QuickPrefs {
  /** Hours of night to claim. Ignored while `dawn` is true. */
  hours: number;
  /**
   * True when the choice was "until dawn" rather than a number of hours.
   *
   * This cannot be folded into `hours`. Dawn is a different length every night,
   * so storing tonight's 5.2 h and replaying it in December would silently turn
   * "all night" into "5h 12m" - and the screen would say 5h 12m with a straight
   * face. The flag says what was CHOSEN; the sheet resolves it against tonight.
   */
  dawn: boolean;
  /** Keyed by the WHEEL's slot names, never by index: a slot that moves must
   *  not take another filter's exposure with it. Absent means CHECKED - the
   *  same rule `wheelModel` applies, so both sheets agree about a fresh slot. */
  on: Record<string, boolean>;
  exp: Record<string, number>;
  extras: Record<string, boolean>;
  ditherN: number;
}

export const DEFAULT_QUICK: QuickPrefs = {
  hours: 2,
  dawn: false,
  on: {},
  exp: {},
  extras: { af: true, guide: true, dither: true, cloud: true, hfr: true, stack: true },
  ditherN: 3,
};

function coerceBoolMap(v: unknown): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  if (v && typeof v === "object" && !Array.isArray(v)) {
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) out[k] = !!val;
  }
  return out;
}

/** Only finite, positive seconds survive: a stored `0` or `"abc"` would
 *  otherwise reach `wheelModel` and silently become an exposure. */
function coerceNumMap(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (v && typeof v === "object" && !Array.isArray(v)) {
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      const n = Number(val);
      if (Number.isFinite(n) && n > 0) out[k] = n;
    }
  }
  return out;
}

/** The one parser, for a value off the wire as much as one off this phone.
 *
 *  Every field is defended separately because both sources can be wrong in
 *  different ways: an old phone can hold a shape three releases out of date, and
 *  a rig can be running an engine that does not carry every field yet. Neither
 *  is a reason to blank a night's plan. */
export function coerceQuickPrefs(s: Partial<QuickPrefs> | null | undefined): QuickPrefs {
  const v = s ?? {};
  return {
    hours: typeof v.hours === "number" && v.hours > 0 ? v.hours : DEFAULT_QUICK.hours,
    dawn: v.dawn === true,
    on: coerceBoolMap(v.on),
    exp: coerceNumMap(v.exp),
    extras: { ...DEFAULT_QUICK.extras, ...coerceBoolMap(v.extras) },
    ditherN: typeof v.ditherN === "number" && v.ditherN > 0
      ? Math.round(v.ditherN)
      : DEFAULT_QUICK.ditherN,
  };
}

// ------------------------------------------- quick-session defaults (LEGACY)
//
// The three below are the migration's readers and the older-rig fallback, and
// they go with the rest of the legacy block next wave. See the header.

export function readLegacyQuick(): QuickPrefs {
  return coerceQuickPrefs(readJson<Partial<QuickPrefs>>(LEGACY_RIG_KEYS.quick, {}));
}

export function writeLegacyQuick(v: QuickPrefs): void {
  writeJson(LEGACY_RIG_KEYS.quick, v);
}

/**
 * Has anything been learned on THIS PHONE at all?
 *
 * NOT the same claim as "the defaults happen to be empty", and the settings
 * sheet renders the two differently: with nothing learned it says so and offers
 * no controls, rather than presenting `DEFAULT_QUICK` as a choice somebody made.
 * On a rig that carries the block, `planning.quick.learned` is this answer -
 * which is what `config.py:1124-1128` says that flag is for.
 */
export function hasLegacyQuick(): boolean {
  return readRaw(LEGACY_RIG_KEYS.quick) != null;
}

export function forgetLegacyQuick(): void {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem(LEGACY_RIG_KEYS.quick);
    }
  } catch {
    /* private mode, a WebView that throws - forgetting is best-effort */
  }
}

// -------------------------------------------------------------- FRAME mode
export type FrameMode = "survey" | "schematic";

export function getFrameMode(): FrameMode {
  const raw = readRaw(K.frameMode);
  return raw === "schematic" ? "schematic" : "survey";
}

export function setFrameMode(v: FrameMode): void {
  writeRaw(K.frameMode, v);
}

/** Survey image brightness, 0.08..1. Out-of-range or unparseable stored values
 *  fall back to 1 rather than blanking the survey. */
export function getSurveyBright(): number {
  const raw = readRaw(K.surveyBright);
  const n = raw == null ? NaN : Number(raw);
  return Number.isFinite(n) && n >= 0.08 && n <= 1 ? n : 1;
}

export function setSurveyBright(v: number): void {
  writeRaw(K.surveyBright, String(Math.max(0.08, Math.min(1, v))));
}

export const SKY_PREF_KEYS = K;
