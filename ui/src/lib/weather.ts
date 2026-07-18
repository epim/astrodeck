// lib/weather.ts — pure weather-payload helpers (weather spec §9). No React,
// no DOM: npx-tsx testable (lib/safety.ts precedent).
//
// normalizeWeather derives `stale` FAIL-CLOSED client-side from fetched_ts age
// (> 45 min, the server's Open-Meteo staleness threshold), clamps every
// percentage series to 0-100, and pads missing/short series to the times
// length so the panel never renders NaN. breachSpans is the DISPLAY-ONLY twin
// of the server's consecutive-sample sustained-breach rule (spec §4).

import type { WeatherAstrospheric, WeatherForecast, WeatherState } from "../types";

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
