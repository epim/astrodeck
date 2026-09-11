// verdict.ts - the Conditions headline, and the 24 h window that both it and
// the chart under it are computed from.
//
// ONE WINDOW, ONE RULE. `windowSamples` is transcribed from
// `components/weather/SkyConditionsPanel.tsx:56-70` and `breachSpans` is
// imported from `lib/weather.ts:87-107` rather than re-derived, because the
// headline and the shading beneath it are two renderings of the same claim: if
// the chart shades 03:00-06:00 and the headline says GOOD ALL NIGHT, one of
// them is lying and the screen gives the operator no way to tell which.
//
// WHAT THE ENGINE ACTUALLY DOES WITH THIS FORECAST, verified 2026-09-10 against
// `server/astrodeck/weather.py:685-726` (`WeatherService.veto_reason`) and
// `server/astrodeck/sequence/resume_arm.py:218-224` (the only caller):
//
//   * Auto-resume's weather gate vetoes on FORECAST RAIN ONLY. Cloud stopped
//     gating anything: "RAIN VETOES. CLOUD DOES NOT" (weather.py:687), after
//     the cloud gate refused two nights that turned out clear (:709-714).
//   * A cloudy forecast therefore ARMS NOTHING. The in-run cloud hold is the
//     `hold_for_clear` flow action (`sequence/engine.py:5432-5438`), which is
//     driven by the frames the camera is taking, not by this series.
//   * `threshold_pct` / `sustain_minutes` drive exactly two things now: the
//     high-cloud ALERT latch (`weather.py:849-852`) and `cloud_outlook`
//     (:749-769), which is advice with no consumer.
//
// So the sub-lines here say "a run holds on what its frames show, not on this
// forecast" where the design's prototype said "hold armed, resumes when it
// clears". The prototype was describing an engine that no longer exists, and a
// headline that promises a hold nobody armed is the exact shape of defect this
// codebase keeps a taxonomy for.

import { agoLabel, breachSpans, fmtHm } from "../../../../lib/weather";
import type { WeatherState } from "../../../../types";

/** The x-domain of the strip: now -> now + 24 h (SkyConditionsPanel.tsx:46). */
export const HORIZON_S = 24 * 3600;

export interface Windowed {
  /** epoch seconds, ascending, within [now-450s, now+24h] */
  ts: number[];
  cloud: number[];
  low: number[];
  mid: number[];
  high: number[];
}

/** The forecast samples that fall in the strip's window. Verbatim from
 *  `SkyConditionsPanel.tsx:56-70`, including the 450 s of slack behind `now`
 *  that keeps the leftmost sample on screen between 15-minute grid points. */
export function windowSamples(w: WeatherState, nowTs: number): Windowed {
  const out: Windowed = { ts: [], cloud: [], low: [], mid: [], high: [] };
  const f = w.forecast;
  if (!f) return out;
  for (let i = 0; i < f.times.length; i++) {
    const t = Date.parse(f.times[i]) / 1000;
    if (!Number.isFinite(t) || t < nowTs - 450 || t > nowTs + HORIZON_S) continue;
    out.ts.push(t);
    out.cloud.push(f.cloud[i]);
    out.low.push(f.cloud_low[i]);
    out.mid.push(f.cloud_mid[i]);
    out.high.push(f.cloud_high[i]);
  }
  return out;
}

// --------------------------------------------------------------- compass

const POINTS = [
  "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
] as const;

/** 16-point compass name for a bearing in degrees. */
export function compass(deg: number): string {
  const d = ((deg % 360) + 360) % 360;
  return POINTS[Math.round(d / 22.5) % 16];
}

/** Both ends of a METEOROLOGICAL wind bearing.
 *
 *  `wind_dir_deg` is where the wind comes FROM (`types.ts:2172-2174`: "Turn it
 *  around before drawing an arrow that points downwind"). The design's copy is
 *  a DRIFT direction - "SW to NE at cloud base" - so both ends are printed and
 *  every arrow on this hub is drawn along `towardDeg`. Getting this backwards
 *  is silent: the numbers stay plausible and the cloud is drawn arriving from
 *  the side it is actually leaving towards. */
export function drift(fromDeg: number): { from: string; toward: string; towardDeg: number } {
  const towardDeg = ((fromDeg % 360) + 360 + 180) % 360;
  return { from: compass(fromDeg), toward: compass(towardDeg), towardDeg };
}

// ---------------------------------------------------------------- verdict

export type VerdictKind =
  | "off" | "waiting" | "nowindow"
  | "override" | "alert" | "goodUntil" | "goodAllNight";

export type VerdictTone = "good" | "warn" | "bad" | "dim";

export interface Verdict {
  kind: VerdictKind;
  headline: string;
  sub: string;
  tone: VerdictTone;
}

export interface VerdictInput {
  weather: WeatherState | null;
  /** `windowSamples(weather, nowTs)`, or null when there is no forecast. */
  win: Windowed | null;
  /** `/api/site/sky` -> `dark_window` (view.site_derived), or null. */
  dark: { start_iso: string; end_iso: string } | null;
  /** Why `dark` is null, in a clause. Appended to the sub-line so the reader
   *  learns the verdict covers 24 h rather than tonight - never a silent
   *  widening of what the headline is about. */
  darkNote: string | null;
  nowTs: number;
}

// A.1.6's three states, verbatim from `SkyConditionsPanel.tsx:217,318,324`,
// except the disabled sentence: the old one names "Settings -> Connect", a tab
// this IA does not have, and an instruction to a route that does not exist is
// worse than no instruction (plan F.4).
export const WEATHER_OFF_TITLE = "Weather is off.";
export const WEATHER_OFF_HINT = "Turn it on in Weather settings.";
export const NO_WINDOW_NOTE = "no forecast data for the current window";
export const WAITING_NOTE = "waiting for first forecast…";

export function deriveVerdict(inp: VerdictInput): Verdict {
  const w = inp.weather;
  if (!w || !w.enabled) {
    return {
      kind: "off",
      headline: "WEATHER IS OFF",
      sub: `${WEATHER_OFF_TITLE} ${WEATHER_OFF_HINT}`,
      tone: "dim",
    };
  }
  if (!w.forecast) {
    return {
      kind: "waiting",
      headline: "NO FORECAST YET",
      sub: WAITING_NOTE,
      tone: "dim",
    };
  }
  const win = inp.win;
  if (!win || win.ts.length < 2) {
    return {
      kind: "nowindow",
      headline: "NOTHING IN THE NEXT 24 H",
      sub: NO_WINDOW_NOTE,
      tone: "dim",
    };
  }

  const at = (i: number): string => {
    const j = Math.max(0, Math.min(win.ts.length - 1, i));
    return fmtHm(new Date(win.ts[j] * 1000).toISOString());
  };

  let dark: { s: number; e: number } | null = null;
  if (inp.dark) {
    const s = Date.parse(inp.dark.start_iso) / 1000;
    const e = Date.parse(inp.dark.end_iso) / 1000;
    if (Number.isFinite(s) && Number.isFinite(e)) dark = { s, e };
  }

  const spans = breachSpans(win.cloud, w.threshold_pct, w.sustain_minutes);
  // With a dark window, only a breach that overlaps it is tonight's problem -
  // a bank of cloud at noon tomorrow is inside the 24 h series and is not.
  // Without one, every breach in the window counts and the sub-line says so.
  const night = spans.find((sp) => {
    if (!dark) return true;
    const a = win.ts[sp.start];
    const b = win.ts[Math.min(sp.end + 1, win.ts.length - 1)];
    return b >= dark.s && a <= dark.e;
  }) ?? null;

  const nowPct = win.cloud.length > 0 ? Math.round(win.cloud[0]) : null;
  const darkClause = dark || !inp.darkNote ? "" : ` · ${inp.darkNote}`;

  let out: Verdict;
  if (w.ignore_tonight) {
    // Corrected against the engine: `ignore_tonight` is read in exactly one
    // place, `veto_reason`'s `_ignore_active` short-circuit (weather.py:721),
    // and what it disarms there is the RAIN veto on auto-resume. It touches no
    // cloud hold, and it expires at the next dusk rather than "until dawn"
    // (`_ignore_active` is keyed to tonight's dusk, weather.py:632-639).
    out = {
      kind: "override",
      headline: "OVERRIDE · IMAGING THROUGH CLOUD",
      sub: "rain veto disarmed until the next dusk · the safety monitor still stops the run",
      tone: "warn",
    };
  } else if (w.alert) {
    const a = w.alert;
    out = {
      kind: "alert",
      headline: "HIGH CLOUD TONIGHT",
      sub: `peak ${a.peak_pct}% (${a.dominant_layer} layer) ${fmtHm(a.start_iso)} - `
        + `${fmtHm(a.end_iso)} · at or above your ${w.threshold_pct}% threshold`,
      tone: "bad",
    };
  } else if (night) {
    out = {
      kind: "goodUntil",
      headline: `GOOD UNTIL ${at(night.start)}`,
      sub: `high cloud ${at(night.start)} - ${at(night.end + 1)} · a run holds on what its `
        + `frames show, not on this forecast${darkClause}`,
      tone: "warn",
    };
  } else {
    const nowClause = nowPct === null ? "" : ` · ${nowPct}% now`;
    out = {
      kind: "goodAllNight",
      headline: dark ? "GOOD ALL NIGHT" : "NO CLOUD BREACH IN 24 H",
      sub: dark
        ? `no sustained breach in the dark window${nowClause}`
        : `no sustained breach in the next 24 h${nowClause}${darkClause}`,
      tone: "good",
    };
  }

  // A stale forecast must never be shown as a live verdict. `normalizeWeather`
  // is fail-CLOSED at 45 minutes (`lib/weather.ts:14,33`), so `stale` here is a
  // measurement, and the age leads the sub-line rather than hiding in a chip.
  if (w.stale) {
    return { ...out, tone: "dim", sub: `${agoLabel(w.fetched_ts, inp.nowTs)} · ${out.sub}` };
  }
  return out;
}
