// lib/weather.ts — pure weather-payload helpers (weather spec §9). No React,
// no DOM: npx-tsx testable (lib/safety.ts precedent).
//
// normalizeWeather derives `stale` FAIL-CLOSED client-side from fetched_ts age
// (> 45 min, the server's Open-Meteo staleness threshold), clamps every
// percentage series to 0-100, and pads missing/short series to the times
// length so the panel never renders NaN. breachSpans is the DISPLAY-ONLY twin
// of the server's consecutive-sample sustained-breach rule (spec §4).

import type {
  WeatherAstrospheric, WeatherForecast, WeatherNow, WeatherState, WeatherSurface,
} from "../types";

export const OPEN_METEO_STALE_S = 45 * 60;

const clampPct = (v: unknown): number =>
  typeof v === "number" && Number.isFinite(v)
    ? Math.max(0, Math.min(100, v))
    : 0;

function normalizeSeries(a: unknown, n: number): number[] {
  const src = Array.isArray(a) ? (a as unknown[]) : [];
  const out: number[] = [];
  for (let i = 0; i < n; i++) out.push(clampPct(src[i]));
  return out;
}

export function normalizeWeather(
  raw: WeatherState | null | undefined,
  nowTs: number,
): WeatherState | null {
  if (!raw) return null;
  const fetched = typeof raw.fetched_ts === "number" ? raw.fetched_ts : null;
  const stale = fetched === null || nowTs - fetched > OPEN_METEO_STALE_S;
  let forecast: WeatherForecast | null = null;
  const f = raw.forecast;
  if (f && Array.isArray(f.times) && f.times.length > 0) {
    const n = f.times.length;
    forecast = {
      times: f.times.slice(0, n).map(String),
      cloud: normalizeSeries(f.cloud, n),
      cloud_low: normalizeSeries(f.cloud_low, n),
      cloud_mid: normalizeSeries(f.cloud_mid, n),
      cloud_high: normalizeSeries(f.cloud_high, n),
    };
  }
  let astro: WeatherAstrospheric | null = null;
  const a = raw.astrospheric;
  if (a && Array.isArray(a.times) && a.times.length > 0) {
    const fetchedA = typeof a.fetched_ts === "number" ? a.fetched_ts : null;
    astro = {
      times: a.times.map(String),
      seeing: Array.isArray(a.seeing) ? a.seeing : [],
      transparency: Array.isArray(a.transparency) ? a.transparency : [],
      fetched_ts: fetchedA,
      stale: fetchedA === null || nowTs - fetchedA > 12 * 3600,
      credits_used_today:
        typeof a.credits_used_today === "number" ? a.credits_used_today : null,
    };
  }
  return {
    enabled: !!raw.enabled,
    fetched_ts: fetched,
    stale,
    ignore_tonight: !!raw.ignore_tonight,
    threshold_pct: clampPct(raw.threshold_pct),
    sustain_minutes:
      typeof raw.sustain_minutes === "number" && Number.isFinite(raw.sustain_minutes)
        ? raw.sustain_minutes
        : 30,
    // Site fix for RadarMap centering (2026-07-17 decisions wave I2) — passed
    // through as-is; null on the default site (server contract) or if absent
    // (an older/other caller of normalizeWeather that never set them).
    site_lat: typeof raw.site_lat === "number" ? raw.site_lat : null,
    site_lon: typeof raw.site_lon === "number" ? raw.site_lon : null,
    forecast,
    astrospheric: astro,
    alert: raw.alert ?? null,
    surface: normalizeSurface(raw.surface),
    now: normalizeNow(raw.now),
  };
}

/** Display-only sustained-breach spans (the server rule, spec §4): runs of
 *  >= max(1, floor(sustain/15)) CONSECUTIVE samples with cloud >= threshold.
 *  Returns inclusive index ranges into the given series. */
export function breachSpans(
  cloud: number[],
  thresholdPct: number,
  sustainMinutes: number,
): { start: number; end: number }[] {
  const needed = Math.max(1, Math.floor(sustainMinutes / 15));
  const spans: { start: number; end: number }[] = [];
  let runStart = -1;
  for (let i = 0; i <= cloud.length; i++) {
    const breach = i < cloud.length && cloud[i] >= thresholdPct;
    if (breach) {
      if (runStart < 0) runStart = i;
    } else {
      if (runStart >= 0 && i - runStart >= needed) {
        spans.push({ start: runStart, end: i - 1 });
      }
      runStart = -1;
    }
  }
  return spans;
}

/** "HH:MM" (viewer-local time) from an ISO-Z stamp — chart ticks, popup and
 *  chip copy. Garbage-safe: unparseable input renders as "--:--". */
export function fmtHm(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** Staleness chip copy: "updated 12 min ago" / "STALE — last fetch 2.0 h ago". */
export function agoLabel(fetchedTs: number | null, nowTs: number): string {
  if (fetchedTs === null) return "STALE — never fetched";
  const ageS = Math.max(0, nowTs - fetchedTs);
  const mins = Math.round(ageS / 60);
  if (ageS > OPEN_METEO_STALE_S) {
    const h = ageS / 3600;
    return `STALE — last fetch ${h >= 1 ? `${h.toFixed(1)} h` : `${mins} min`} ago`;
  }
  return `updated ${mins} min ago`;
}

/** Data-source attribution (F7 #3, SkyConditionsPanel): mechanically truthful
 *  — Astrospheric is named ONLY when the payload actually carries astrospheric
 *  samples (WeatherState.astrospheric non-null), never hardcoded, so the label
 *  never claims a source that isn't flowing. */
export function weatherSourceLabel(hasAstrospheric: boolean): string {
  return hasAstrospheric ? "Open-Meteo + Astrospheric" : "Open-Meteo";
}

/** Collision-consolidates SVG chart end-labels that land within `minGap` px of
 *  one another (e.g. all-zero series stack exactly on the 0% baseline and
 *  would otherwise overprint — SkyConditionsPanel spec §10). Sorts by y, then
 *  greedily clusters CONSECUTIVE entries whose gap to the previous member is
 *  <= minGap (a chain: A+B may merge, B+C may merge, joining A/B/C into one
 *  row even though A and C alone are > minGap apart — intentional, since each
 *  adjacent pair is visually touching). Each cluster becomes one row: label =
 *  member names joined with "+" in sorted-y order, y = the cluster's mean.
 *  Pure — no DOM, no chart-scale knowledge; the caller supplies pixel y's. */
export function groupEndLabels(
  entries: { name: string; y: number }[],
  minGap: number,
): { label: string; y: number }[] {
  if (entries.length === 0) return [];
  const sorted = [...entries].sort((a, b) => a.y - b.y);
  const clusters: { name: string; y: number }[][] = [];
  for (const e of sorted) {
    const cur = clusters[clusters.length - 1];
    if (cur && e.y - cur[cur.length - 1].y <= minGap) {
      cur.push(e);
    } else {
      clusters.push([e]);
    }
  }
  return clusters.map((c) => ({
    label: c.map((e) => e.name).join("+"),
    y: c.reduce((sum, e) => sum + e.y, 0) / c.length,
  }));
}

// --- surface conditions (server wave S2) -------------------------------------
// Additive: a payload without them normalizes exactly as it did before, with
// both fields null. NOT run through clampPct/normalizeSeries -- those exist for
// percentages, and putting a -3 C dew point through a 0-100 clamp would report
// a freezing night as 0 C. The server owns the ranges; this only guards types.

/** A finite number, or null for anything else (missing, null, string, NaN). */
function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** One hourly series, padded to `n` with nulls so every index means the same
 *  hour in every series and the caller never reads past the end. */
function numSeries(a: unknown, n: number): (number | null)[] {
  const src = Array.isArray(a) ? (a as unknown[]) : [];
  const out: (number | null)[] = [];
  for (let i = 0; i < n; i++) out.push(numOrNull(src[i]));
  return out;
}

/** The hourly surface block, or null when the payload carries none (an older
 *  engine, weather disabled, or an upstream that dropped the hourly block). */
export function normalizeSurface(
  s: WeatherSurface | null | undefined,
): WeatherSurface | null {
  if (!s || !Array.isArray(s.times) || s.times.length === 0) return null;
  const times = s.times.map(String);
  const n = times.length;
  return {
    times,
    temp_c: numSeries(s.temp_c, n),
    dewpoint_c: numSeries(s.dewpoint_c, n),
    humidity_pct: numSeries(s.humidity_pct, n),
    wind_kmh: numSeries(s.wind_kmh, n),
    wind_dir_deg: numSeries(s.wind_dir_deg, n),
    gust_kmh: numSeries(s.gust_kmh, n),
    cloud_base_m: numSeries(s.cloud_base_m, n),
  };
}

/** The single "right now" reading, or null when the payload carries none.
 *  Each value is independently nullable: a station reporting wind but no dew
 *  point is a real thing, and it must not take the whole reading down. */
export function normalizeNow(
  w: WeatherNow | null | undefined,
): WeatherNow | null {
  if (!w || typeof w.ts !== "string") return null;
  return {
    ts: w.ts,
    temp_c: numOrNull(w.temp_c),
    dewpoint_c: numOrNull(w.dewpoint_c),
    humidity_pct: numOrNull(w.humidity_pct),
    wind_kmh: numOrNull(w.wind_kmh),
    wind_dir_deg: numOrNull(w.wind_dir_deg),
    gust_kmh: numOrNull(w.gust_kmh),
    cloud_base_m: numOrNull(w.cloud_base_m),
  };
}
