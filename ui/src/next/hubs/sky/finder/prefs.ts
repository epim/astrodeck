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

import type { SkyKind } from "./targets";
import { SKY_KINDS } from "./targets";

const K = {
  lens: "astrodeck-next-sky-lens",
  layers: "astrodeck-next-sky-layers",
  mode: "astrodeck-next-sky-mode",
  floor: "astrodeck-next-sky-floor",
  site: "astrodeck-next-sky-site",
  pool: "astrodeck-next-sky-pool",
  quick: "astrodeck-next-sky-quick",
  frameMode: "astrodeck-next-sky-frame-mode",
  surveyBright: "astrodeck-next-sky-survey-bright",
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
export type SkyMode = "cam" | "map";

/** The default differs by device, so the caller passes it: a phone opens in AR,
 *  a desktop has no camera worth pointing at the sky and opens in MAP. */
export function getMode(fallback: SkyMode): SkyMode {
  const raw = readRaw(K.mode);
  return raw === "cam" || raw === "map" ? raw : fallback;
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

// -------------------------------------------------------------- site pick
/** The saved-location id the user last chose in the Sites sheet ("" = none). */
export function getSiteId(): string {
  return readRaw(K.site) ?? "";
}

export function setSiteId(v: string): void {
  writeRaw(K.site, v);
}

// ------------------------------------------------------------ target pool
export function getPool(): string[] {
  const v = readJson<unknown>(K.pool, []);
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

export function setPool(v: string[]): void {
  writeJson(K.pool, v);
}

// --------------------------------------------------- quick-session defaults
//
// ONE KEY AND ONE PARSER, and this is it.
//
// There were two: the Sky hub's quick sheet wrote `astrodeck-next-sky-quick`
// through this module on GENERATE FLOW, and Settings > SKY > QUICK SESSION
// DEFAULTS read and wrote its OWN `astrodeck-next-quick` with its own shape.
// Neither ever saw the other, so the settings sheet said "nothing learned yet"
// forever and every edit made there was ignored by the sheet that generates the
// night. Both directions were broken (review #4).
//
// The surviving key is this one, because it is the one that already holds real
// data: a phone that has generated quick sessions has learned defaults HERE,
// and nothing but the settings sheet's own edits was ever at the other key.
// `QuickDefaultsSheet` now imports `getQuick`/`setQuick`/`hasQuick`/`clearQuick`
// from this module and owns no parser of its own.
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

export function getQuick(): QuickPrefs {
  const s = readJson<Partial<QuickPrefs>>(K.quick, {});
  return {
    hours: typeof s.hours === "number" && s.hours > 0 ? s.hours : DEFAULT_QUICK.hours,
    dawn: s.dawn === true,
    on: coerceBoolMap(s.on),
    exp: coerceNumMap(s.exp),
    extras: { ...DEFAULT_QUICK.extras, ...coerceBoolMap(s.extras) },
    ditherN: typeof s.ditherN === "number" && s.ditherN > 0
      ? Math.round(s.ditherN)
      : DEFAULT_QUICK.ditherN,
  };
}

export function setQuick(v: QuickPrefs): void {
  writeJson(K.quick, v);
}

/**
 * Has anything been learned at all?
 *
 * NOT the same claim as "the defaults happen to be empty", and the settings
 * sheet renders the two differently: with nothing stored it says so and offers
 * no controls, rather than presenting `DEFAULT_QUICK` as a choice somebody made.
 */
export function hasQuick(): boolean {
  return readRaw(K.quick) != null;
}

export function clearQuick(): void {
  try {
    if (typeof localStorage !== "undefined") localStorage.removeItem(K.quick);
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
