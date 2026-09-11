// effective.ts — read the WINNING layer, not the one that happens to be
// convenient (#129).
//
// The bug this file exists to end: `/api/config` is a dump of the GLOBAL
// AppConfig, but the ACTIVE PROFILE's values beat it at read time. Bind a form
// field to `config.providers.polar_align` and the console will confidently
// render "AstroDeck native" while the rig runs the built-in simulator, with no
// tell of any kind — which it did for twelve days. The numbers stayed
// plausible the whole time.
//
// Two rules follow, and every helper here enforces one of them:
//
//   1. The VALUE the user reads must come from `config.effective[key].value`.
//      Falling back to the raw global block is allowed ONLY as a degradation
//      path (the WS `hello` bootstrap omits `effective`), never as the default.
//   2. Showing the winning value alone does NOT catch this class. A profile
//      pinned to `sim` and a global config pinned to `sim` render identically,
//      so a value-only readout agrees with the rig in exactly the cases where
//      the bug is invisible. The LAYER IDENTITY is the signal — hence
//      `isProfileOverride` and the describe* helpers, which name the profile
//      and the value it is shadowing.
//
// Pure functions, no React: the panels bind, these decide.

import type {
  AppConfig,
  EffectiveEntry,
  EffectiveLayer,
  Optics,
  ProvidersConfig,
} from "../types";
// Type-only: the four numbers the FOV maths takes. Imported rather than
// redeclared so the framing overlay and this resolver can never disagree about
// the shape they are passing between them (erased at runtime).
import type { OpticsLike } from "./framing";

export type { OpticsLike };

/** The four capability-routing keys, in the order the server enumerates them
 *  (`config.PROVIDER_CAPABILITIES`, derived from `ProvidersConfig`). */
export const PROVIDER_CAPS = [
  "autofocus",
  "polar_align",
  "solve",
  "guide",
] as const;
export type ProviderCap = (typeof PROVIDER_CAPS)[number];

/** The optics fields a profile can override — ALL of them, because a profile
 *  optics block is swapped whole rather than merged. That is not a technicality:
 *  it is why a profile meaning to change only a focal length also reverts a
 *  pixel size, and why the panel has to say so. */
export const OPTICS_KEYS = [
  "focal_length_mm",
  "pixel_size_um",
  "sensor_width_px",
  "sensor_height_px",
  "auto_from_camera",
  "guide_focal_length_mm",
  "telescope_name",
  "aperture_mm",
  "reducer",
] as const;
export type OpticsKey = (typeof OPTICS_KEYS)[number];

export const providerKey = (cap: ProviderCap): string => `providers.${cap}`;
export const opticsKey = (key: OpticsKey): string => `optics.${key}`;

/** One key's provenance record, or null when the server did not send the block
 *  (an old build, or the WS `hello` bootstrap, which omits it). Null must always
 *  degrade to "no provenance available", NEVER to "no override" — the two look
 *  the same on screen and only one of them is safe to assert. */
export function entryOf<T = unknown>(
  config: AppConfig | null | undefined,
  key: string,
): EffectiveEntry<T> | null {
  const e = config?.effective?.[key];
  return (e as EffectiveEntry<T> | undefined) ?? null;
}

/** The value the rig actually runs for `key`, or `fallback` when the server
 *  sent no provenance. `fallback` should be the raw global value the caller
 *  used to read — degrading to the old (wrong-under-a-profile) behaviour is
 *  better than blanking a panel, but it is a degradation and callers should
 *  pass the global value knowingly. */
export function valueOf<T>(
  config: AppConfig | null | undefined,
  key: string,
  fallback: T,
): T {
  const e = entryOf<T>(config, key);
  if (!e) return fallback;
  // `null` is a legitimate winning value (guide_focal_length_mm unset), so only
  // `undefined` — a malformed entry — falls through to the caller's value.
  return (e.value === undefined ? fallback : e.value) as T;
}

export const layerOf = (
  config: AppConfig | null | undefined,
  key: string,
): EffectiveLayer | null => entryOf(config, key)?.layer ?? null;

/** True when the ACTIVE PROFILE is what supplies this key. This is the one
 *  predicate the badges hang off: it is true even when the profile's value
 *  equals global's, because "the profile is in charge here" is the fact the
 *  user could not previously learn, and the value agreeing today is an accident
 *  that a later global edit will silently undo. */
export function isProfileOverride(e: EffectiveEntry | null): boolean {
  return !!e && e.layer === "profile";
}

/** True when the profile is in charge AND holds something different from
 *  global — i.e. the console and the rig would disagree if the console showed
 *  the global block. Used to pick the WORDING, never to decide whether to badge
 *  (see `isProfileOverride` for why). */
export function overrideChangesValue(e: EffectiveEntry | null): boolean {
  return isProfileOverride(e) && !Object.is(e!.value, e!.config);
}

/** The profile name to blame/credit for `key`, or null. Falls back to the id
 *  when a profile somehow has no name, because "profile 3f2a…" is still an
 *  answer and "a profile" is not. */
export function overrideProfileName(e: EffectiveEntry | null): string | null {
  if (!isProfileOverride(e)) return null;
  return e!.profile_name || e!.profile_id || null;
}

/** Every provider capability the active profile currently pins, in enum order.
 *  Empty when nothing is overridden — callers can use `.length` as the "is this
 *  rig running on profile overrides at all" question. */
export function overriddenCaps(
  config: AppConfig | null | undefined,
): ProviderCap[] {
  return PROVIDER_CAPS.filter((c) =>
    isProfileOverride(entryOf(config, providerKey(c))),
  );
}

/** True when the active profile carries an optics block. Checked through the
 *  provenance of a single field rather than by looking for the block, because
 *  the block wins WHOLE: if one optics key reports `profile`, all nine do. */
export function opticsOverridden(
  config: AppConfig | null | undefined,
): boolean {
  return isProfileOverride(entryOf(config, opticsKey("focal_length_mm")));
}

/** The name of the profile supplying the optics block, or null. */
export function opticsOverrideProfile(
  config: AppConfig | null | undefined,
): string | null {
  return overrideProfileName(entryOf(config, opticsKey("focal_length_mm")));
}

/** The provider values in force, ready to spread over a `ProvidersConfig`
 *  seed. Only keys the server actually reported are present, so a missing
 *  `effective` block leaves the caller's own seed untouched. */
export function effectiveProviders(
  config: AppConfig | null | undefined,
): Partial<ProvidersConfig> {
  const out: Partial<ProvidersConfig> = {};
  for (const cap of PROVIDER_CAPS) {
    const e = entryOf<string>(config, providerKey(cap));
    if (e && typeof e.value === "string" && e.value) out[cap] = e.value;
  }
  return out;
}

const num = (v: unknown): number =>
  typeof v === "number" && Number.isFinite(v) ? v : 0;

/**
 * The optical train the rig is ACTUALLY using, for anything that draws or
 * computes (the Atlas FOV rectangle, the mosaic panel altitudes, the ″/px
 * plausibility hint).
 *
 * `global` is the raw `config.optics` the callers used to read, kept as the
 * degradation path. `live` is the camera-reported readout (`status.optics` or
 * `optics_computed`) that used to be merged in per field — still merged, but
 * only under the WINNING value, so a profile that pins a pixel size is not
 * quietly re-filled from the camera.
 *
 * Returns null when there is no optics block at all, matching the old callers'
 * "no optics yet" contract (they render a set-up-your-optics banner on null).
 */
export function effectiveOptics(
  config: AppConfig | null | undefined,
  global: Optics | null | undefined,
  live: Partial<OpticsLike> | null | undefined,
): OpticsLike | null {
  if (!global && !config?.effective) return null;
  const pick = (key: OpticsKey, fallback: number): number =>
    num(valueOf<unknown>(config, opticsKey(key), fallback));
  return {
    // Focal length has no camera source — a camera cannot know what it is
    // bolted to — so there is nothing to fall back to but the winning value.
    focal_length_mm: pick("focal_length_mm", num(global?.focal_length_mm)),
    // The camera fill is already applied server-side for these three (layer
    // "camera"), but `live` is retained as the fallback for the bootstrap case
    // where `effective` is absent, which is exactly when the old merge ran.
    pixel_size_um:
      pick("pixel_size_um", num(global?.pixel_size_um)) || num(live?.pixel_size_um),
    sensor_width_px:
      pick("sensor_width_px", num(global?.sensor_width_px)) ||
      num(live?.sensor_width_px),
    sensor_height_px:
      pick("sensor_height_px", num(global?.sensor_height_px)) ||
      num(live?.sensor_height_px),
  };
}

// ------------------------------------------------------------------ wording
//
// UI copy rule: a badge reading "overridden" with no value tells the user
// nothing they can act on. Every string below names the profile AND the value
// being shadowed, so the answer to "what is running, and what would run without
// this profile" is legible without a hover (hover does not exist on the tablet
// this product is used on).

/** Render a layer's holding for display. `null`/`""`/`0` all mean "nobody
 *  filled this in" for optics, and printing them raw reads as a real
 *  measurement of zero. Booleans are checked FIRST because `false === 0` is
 *  falsy and `auto_from_camera: false` is a deliberate setting, not an empty
 *  one. Mirrors `provenance._show` on the server so the two never disagree. */
export function showValue(
  v: unknown,
  fmt?: (v: unknown) => string,
): string {
  if (typeof v === "boolean") return v ? "on" : "off";
  if (v === null || v === undefined || v === "" || v === 0) return "not set";
  return fmt ? fmt(v) : String(v);
}

/** The one-line disclosure under an overridden control.
 *
 *  Three shapes, because three different things are true:
 *    - the profile changes the value  → name both values
 *    - the profile pins the same value → say so; it is still in charge, and a
 *      later global edit will not reach the rig
 *    - not overridden → null (callers render nothing)
 */
export function describeOverride(
  e: EffectiveEntry | null,
  fmt?: (v: unknown) => string,
): string | null {
  if (!isProfileOverride(e)) return null;
  const who = overrideProfileName(e) ?? "the active profile";
  const running = showValue(e!.value, fmt);
  const global = showValue(e!.config, fmt);
  if (overrideChangesValue(e)) {
    return `Running ${running} — pinned by profile “${who}”. Without it this rig would use the global setting, ${global}.`;
  }
  return `Running ${running} — pinned by profile “${who}”, which happens to match the global setting. Editing the global setting will not change what runs until the pin is cleared.`;
}

/** Human names for the IMPLICIT driver ids a profile can pin. Anything else is
 *  a configured driver id ("nina-1a2b"), which is printed literally — an
 *  unresolvable id is exactly the case where the user most needs to see the
 *  raw string the profile is carrying, because a pin naming a deleted driver is
 *  silently discarded at resolve time. */
const IMPLICIT_PROVIDER_LABELS: Record<string, string> = {
  auto: "auto",
  astrodeck: "AstroDeck native",
  astap: "ASTAP",
  sim: "built-in simulator",
  backend: "the connected backend",
};

const CAP_WORDS: Record<ProviderCap, string> = {
  autofocus: "autofocus",
  polar_align: "polar align",
  solve: "plate solve",
  guide: "guiding",
};

/**
 * What a PROFILE ROW overrides, in one readable clause — for the Profiles list,
 * where the row previously showed only name/mode/device-count and therefore
 * could not reveal that activating it would repoint a capability or swap the
 * whole optical train.
 *
 * Returns null when the profile overrides nothing, so the caller renders
 * nothing. `"auto"` is INCLUDED rather than filtered: a profile pinned to
 * `auto` still beats a global `astap` and changes which solver runs, so hiding
 * it would recreate the invisible override one layer up.
 */
export function profileOverrideSummary(row: {
  providers?: Partial<ProvidersConfig> | null;
  optics?: Optics | null;
}): string | null {
  const parts: string[] = [];
  const pov = row.providers;
  if (pov) {
    for (const cap of PROVIDER_CAPS) {
      const v = pov[cap];
      if (typeof v === "string" && v) {
        parts.push(`${CAP_WORDS[cap]} → ${IMPLICIT_PROVIDER_LABELS[v] ?? v}`);
      }
    }
  }
  if (row.optics) {
    // Only the two numbers that identify a telescope; the block carries nine
    // fields and listing them all here would bury the ones that matter.
    const o = row.optics;
    const bits = [
      o.focal_length_mm ? `${Math.round(o.focal_length_mm)} mm` : null,
      o.telescope_name || null,
    ].filter(Boolean);
    parts.push(
      bits.length
        ? `the whole optics block (${bits.join(", ")})`
        : "the whole optics block",
    );
  }
  return parts.length ? parts.join("; ") : null;
}

/** The disclosure for a key the CONNECTED CAMERA supplies. Not an override the
 *  user can clear, but the same class of surprise: the field reads 0 and the
 *  rig runs 3.76. */
export function describeCameraFill(
  e: EffectiveEntry | null,
  fmt?: (v: unknown) => string,
): string | null {
  if (!e || e.layer !== "camera") return null;
  return `Running ${showValue(e.value, fmt)} — read from the connected camera because this field is not set.`;
}
