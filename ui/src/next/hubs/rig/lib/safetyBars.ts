// safetyBars.ts - one bar per input the monitor ACTUALLY REPORTED.
//
// Plan hub-rig.md B.9 item 1, deviation E11. The design asks for five bars -
// rain, wind, cloud, power, humidity - with red limit ticks. The engine has no
// per-input readings: `SafetyReading` is
// `{is_safe, reason, source, detail?: Record<string, number>, stale, ts}`
// (types.ts:1330-1338), and the five bars in the prototype are fixtures
// (`seams/proto/logic.js:717`).
//
// So this module turns whatever `detail` the device happened to send into rows,
// and NOTHING ELSE. A key that is not there gets no bar. Five bars drawn over a
// monitor that reports one number would be the exact failure this codebase keeps
// re-learning - a claim outrunning its evidence - and the sheet's test asserts
// that RAIN/WIND/CLOUD do not appear when `detail` is absent.
//
// LIMIT TICKS. `SafetyConfig` carries no wind/cloud/humidity thresholds; the
// monitor device owns them, and there is no route that serves them. So `limits`
// is a caller-supplied map and today's caller (`safetyLimitsFromConfig`) returns
// an empty one - every bar's sub reads "the monitor decides this limit" and no
// tick is drawn. When the server grows per-input thresholds there is exactly one
// place to wire them in, and the tick appears without the sheet changing.
//
// SCALES ARE NOT LIMITS. `max` below only decides how far along the track a
// value sits. It is a drawing scale, never a threshold, which is why the value
// text is always printed and why a key with no conventional scale (volts, a raw
// power number) parks its bar at the half mark instead of inventing a full-scale
// reading it cannot justify.
//
// Pure: no React, no store, no fetch. Tested by `__tests__/safetyBars.test.ts`.

import type { SafetyReading } from "../../../../types";

/** Icon name from `next/icons.tsx` for a bar's 34 px glyph tile. */
export type BarGlyph = "drop" | "wind" | "weather" | "power" | "temp" | "gauge";

export interface SafetyBarRow {
  /** The raw `detail` key - also the row's test id suffix. */
  key: string;
  label: string;
  glyph: BarGlyph;
  value: number;
  /** The value with its unit, e.g. `"64%"`, `"12.1 V"`. The only truth on the row. */
  text: string;
  /** 0..1 along the track. 0.5 when the key has no conventional scale. */
  pct: number;
  /** 0..1 tick position, or null when no limit can be sourced. */
  limPct: number | null;
  sub: string;
  /** `bad` only when a SOURCED limit is exceeded; never a guess off the scale. */
  tone: "accent" | "bad" | "dim";
  /** False when the key is not in the table below - rendered anyway, raw. */
  known: boolean;
}

/** Every bar whose limit we cannot source says this, so a tickless track is
 *  never read as "no limit exists". */
export const NO_LIMIT_SUB = "the monitor decides this limit";

/** `reading.stale` - the whole card wears this and a warn border. */
export const STALE_NOTE =
  "the last read timed out - this is the last value, not the current one";

interface KeySpec {
  label: string;
  glyph: BarGlyph;
  unit: string;
  /** Track ends. `null` = no conventional scale; the bar parks at the half mark. */
  min: number | null;
  max: number | null;
  /** Decimal places for the printed value. */
  dp: number;
}

// The keys named in the plan. Scales are drawing scales (see the header):
// percentages and rain are bounded by their own definition; the two temperature
// rows use the span a sky-quality/IR sensor actually swings across; volts and a
// bare `power` number have no conventional full scale, so they get none.
const KEYS: Record<string, KeySpec> = {
  rain:        { label: "RAIN",        glyph: "drop",    unit: "",      min: 0,   max: 1,   dp: 2 },
  wind:        { label: "WIND",        glyph: "wind",    unit: " km/h", min: 0,   max: 100, dp: 1 },
  wind_kmh:    { label: "WIND",        glyph: "wind",    unit: " km/h", min: 0,   max: 100, dp: 1 },
  gust:        { label: "GUST",        glyph: "wind",    unit: " km/h", min: 0,   max: 120, dp: 1 },
  cloud:       { label: "CLOUD",       glyph: "weather", unit: "%",     min: 0,   max: 100, dp: 0 },
  cloud_pct:   { label: "CLOUD",       glyph: "weather", unit: "%",     min: 0,   max: 100, dp: 0 },
  sky_temp:    { label: "SKY TEMP",    glyph: "weather", unit: " C",    min: -40, max: 10,  dp: 1 },
  humidity:    { label: "HUMIDITY",    glyph: "drop",    unit: "%",     min: 0,   max: 100, dp: 0 },
  dewpoint:    { label: "DEW POINT",   glyph: "drop",    unit: " C",    min: -30, max: 40,  dp: 1 },
  power:       { label: "POWER",       glyph: "power",   unit: " V",    min: null, max: null, dp: 1 },
  voltage:     { label: "VOLTAGE",     glyph: "power",   unit: " V",    min: null, max: null, dp: 1 },
  temperature: { label: "TEMPERATURE", glyph: "temp",    unit: " C",    min: -30, max: 50,  dp: 1 },
};

/** An unrecognised key still gets a row: the device knows something we do not,
 *  and dropping it would hide a reading the operator can act on. */
function unknownSpec(key: string): KeySpec {
  return { label: key.replace(/_/g, " ").toUpperCase(), glyph: "gauge", unit: "", min: null, max: null, dp: 2 };
}

function trim(v: number, dp: number): string {
  const s = v.toFixed(dp);
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}

function frac(v: number, spec: KeySpec): number | null {
  if (spec.min == null || spec.max == null || spec.max === spec.min) return null;
  return Math.min(1, Math.max(0, (v - spec.min) / (spec.max - spec.min)));
}

/**
 * One row per key present in `detail`, in the order the device sent them.
 *
 * @param detail  `reading.detail` - absent or empty produces NO rows, and the
 *                sheet renders its single status row instead.
 * @param limits  key -> limit value, from a source that actually has one. A key
 *                missing here gets no tick and the "the monitor decides" sub.
 */
export function safetyBars(
  detail: Record<string, number> | null | undefined,
  limits: Record<string, number> = {},
): SafetyBarRow[] {
  if (!detail) return [];
  const rows: SafetyBarRow[] = [];
  for (const [key, raw] of Object.entries(detail)) {
    if (typeof raw !== "number" || !Number.isFinite(raw)) continue;
    const known = Object.prototype.hasOwnProperty.call(KEYS, key);
    const spec = known ? KEYS[key] : unknownSpec(key);
    const at = frac(raw, spec);
    const limit = limits[key];
    const limPct = limit == null ? null : frac(limit, spec);
    const over = limit != null && raw >= limit;
    rows.push({
      key,
      label: spec.label,
      glyph: spec.glyph,
      value: raw,
      text: `${trim(raw, spec.dp)}${spec.unit}`,
      // No scale -> the half mark. The bar is then decoration and the text is
      // the reading; saying so in the sub is what keeps that honest.
      pct: at ?? 0.5,
      limPct,
      sub: limit != null
        ? `limit ${trim(limit, spec.dp)}${spec.unit}`
        : at == null
          ? `${NO_LIMIT_SUB} - the number is the reading, the bar has no scale`
          : NO_LIMIT_SUB,
      tone: over ? "bad" : "accent",
      known,
    });
  }
  return rows;
}

/**
 * The limits we can source from the rig's own config - none today.
 *
 * `SafetyConfig` has `min_alt_deg`, `max_alt_deg`, `nogo_box`, `horizon`,
 * `twilight_deg` and `sky_fallback_hold`; not one of them is a threshold on a
 * monitor INPUT (they are mount-pointing limits and a cloud-hold switch, and
 * they get their own rows on the sheet). Returning `{}` here, rather than
 * guessing, is what makes "the monitor decides this limit" a true sentence.
 */
export function safetyLimitsFromConfig(): Record<string, number> {
  return {};
}

/** `"2 of 3 unsafe reads"` - the hysteresis counter, so the user can watch it
 *  count instead of wondering why a wet sensor has not stopped the run. */
export function streakLine(streak: number, unsafeConsecutive: number): string | null {
  if (!streak || streak <= 0) return null;
  return `${streak} of ${Math.max(1, unsafeConsecutive)} unsafe reads`;
}

/** Whole seconds since a reading's server timestamp. Negative clock skew reads
 *  as 0 rather than as a reading from the future. */
export function readingAgeS(reading: SafetyReading | null, nowMs: number): number | null {
  if (!reading || !Number.isFinite(reading.ts)) return null;
  return Math.max(0, Math.round(nowMs / 1000 - reading.ts));
}

/** The sheet's ONE live line (plan B.9 header), and the fallback status row's
 *  verdict word come from the same place so they cannot disagree. */
export function safetyLive(
  connected: boolean,
  reading: SafetyReading | null,
  nowMs: number,
): { text: string; tone: "good" | "warn" | "bad" } {
  if (!reading) return { text: "no monitor assigned", tone: "warn" };
  if (reading.stale) return { text: "stale - the monitor stopped answering", tone: "warn" };
  if (!connected) return { text: "no monitor assigned", tone: "warn" };
  if (!reading.is_safe) {
    return { text: `UNSAFE - ${reading.reason || "no reason given"}`, tone: "bad" };
  }
  const age = readingAgeS(reading, nowMs);
  const src = reading.source || "unnamed monitor";
  return {
    text: age == null ? `safe · ${src}` : `safe · ${src} · last read ${age}s ago`,
    tone: "good",
  };
}
