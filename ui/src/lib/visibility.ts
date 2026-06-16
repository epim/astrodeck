// visibility.ts — pure FORMATTING for the VisibilityPanel. The server
// (catalog/visibility.py) is the single source of truth for every number; this
// module only turns a VisibilityNight into memoizable SVG geometry + display
// strings. No ephemeris math, no React.
//
// Layers in the panel are differentiated by stroke / dash / hatch / glyph, NOT
// hue (the night palette collapses good/warn/bad toward coral — design spec §8),
// so the helpers here emit geometry and glyph+text, never colors.

import type { VisibilityNight, VisibilitySample } from "../types";

// SVG drawing box. The panel renders into this fixed user-space box and lets CSS
// scale it; all text lives in an HTML layer (the panel), never inside the SVG.
export const VIS_W = 320;
export const VIS_H = 150;
// inner plot rect (leave a little room for the alt-limit label gutter at right).
export const PLOT = { x: 4, y: 8, w: VIS_W - 8, h: VIS_H - 24 };
export const ALT_MAX = 90; // y axis: 0°..90°

export interface VisGeometry {
  /** target altitude curve — one memoizable path string (M…L…). */
  altPath: string;
  /** moon altitude curve (dotted). */
  moonPath: string;
  /** astro-dark band rect, or null when there is no window. */
  darkBand: { x: number; w: number } | null;
  /** best-window bracket span, or null. */
  bestWindow: { x: number; w: number } | null;
  /** y of the alt-limit dashed line. */
  altLimitY: number;
  /** x of the NOW line, or null when "now" is outside the plotted span. */
  nowX: number | null;
  /** x of the transit (peak) tick. */
  transitX: number | null;
  /** x of the moon's peak (for the ☾ glyph), or null. */
  moonPeakX: number | null;
  /** plotted time span (unix), for the panel's own tick labels. */
  t0: number;
  t1: number;
}

/** Map an altitude (deg, clamped 0..90) to a plot-y (inverted: 90° at top). */
function altToY(alt: number): number {
  const a = Math.max(0, Math.min(ALT_MAX, alt));
  return PLOT.y + (1 - a / ALT_MAX) * PLOT.h;
}

/** Map a unix time to a plot-x across [t0,t1]; clamps to the plot rect. */
function timeToX(t: number, t0: number, t1: number): number {
  if (t1 <= t0) return PLOT.x;
  const f = (t - t0) / (t1 - t0);
  return PLOT.x + Math.max(0, Math.min(1, f)) * PLOT.w;
}

function pathFrom(
  samples: VisibilitySample[],
  pick: (s: VisibilitySample) => number,
  t0: number,
  t1: number,
): string {
  if (!samples.length) return "";
  let d = "";
  for (let i = 0; i < samples.length; i++) {
    const x = timeToX(samples[i].t_unix, t0, t1).toFixed(1);
    const y = altToY(pick(samples[i])).toFixed(1);
    d += (i === 0 ? "M" : "L") + x + " " + y;
  }
  return d;
}

/** Build all SVG geometry for the night. Pure — memoize on the VisibilityNight
 *  reference + `nowUnix` in the component. */
export function buildGeometry(
  night: VisibilityNight,
  nowUnix: number,
): VisGeometry {
  const samples = night.samples;
  const t0 = samples.length ? samples[0].t_unix : night.transit_unix - 3600;
  const t1 = samples.length
    ? samples[samples.length - 1].t_unix
    : night.transit_unix + 3600;

  const altPath = pathFrom(samples, (s) => s.alt, t0, t1);
  const moonPath = pathFrom(samples, (s) => s.moon_alt, t0, t1);

  let darkBand: VisGeometry["darkBand"] = null;
  if (night.dark_start_unix != null && night.dark_end_unix != null) {
    const x = timeToX(night.dark_start_unix, t0, t1);
    const xe = timeToX(night.dark_end_unix, t0, t1);
    darkBand = { x, w: Math.max(0, xe - x) };
  }

  let bestWindow: VisGeometry["bestWindow"] = null;
  if (night.best_window) {
    const x = timeToX(night.best_window.start_unix, t0, t1);
    const xe = timeToX(night.best_window.end_unix, t0, t1);
    bestWindow = { x, w: Math.max(0, xe - x) };
  }

  // moon peak (for the ☾ glyph) — the sample with max moon_alt.
  let moonPeakX: number | null = null;
  if (samples.length) {
    let bi = 0;
    for (let i = 1; i < samples.length; i++) {
      if (samples[i].moon_alt > samples[bi].moon_alt) bi = i;
    }
    if (samples[bi].moon_alt > 0) moonPeakX = timeToX(samples[bi].t_unix, t0, t1);
  }

  const nowInSpan = nowUnix >= t0 && nowUnix <= t1;

  return {
    altPath,
    moonPath,
    darkBand,
    bestWindow,
    altLimitY: altToY(night.alt_limit_deg),
    nowX: nowInSpan ? timeToX(nowUnix, t0, t1) : null,
    transitX: timeToX(night.transit_unix, t0, t1),
    moonPeakX,
    t0,
    t1,
  };
}

// ----------------------------------------------------------------- formatters

/** Local clock time (HH:MM) from a unix epoch — uses the browser timezone,
 *  which is the right call (sky math is longitude-only server-side; display is
 *  the operator's wall clock). */
export function fmtTime(unix: number | null | undefined): string {
  if (unix == null || !Number.isFinite(unix)) return "—";
  const d = new Date(unix * 1000);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

/** "21:14–02:10" for a window, or "—". */
export function fmtWindow(
  w: { start_unix: number; end_unix: number } | null | undefined,
): string {
  if (!w) return "—";
  return `${fmtTime(w.start_unix)}–${fmtTime(w.end_unix)}`;
}

/** A signed altitude with the degree glyph, e.g. "68°". */
export function fmtAlt(alt: number | null | undefined): string {
  if (alt == null || !Number.isFinite(alt)) return "—";
  return `${Math.round(alt)}°`;
}

/** Duration above the alt limit across the dark window, as "5h12m". Computed
 *  from the samples (in-dark, alt≥limit) so it matches the curve the user sees. */
export function fmtHoursAboveLimit(night: VisibilityNight): string {
  const { dark_start_unix: ds, dark_end_unix: de, alt_limit_deg: lim } = night;
  if (ds == null || de == null) return "—";
  const inWin = night.samples.filter(
    (s) => s.t_unix >= ds && s.t_unix <= de && s.alt >= lim,
  );
  if (inWin.length < 2) return inWin.length ? "<1h" : "0h";
  // step between samples (uniform); total = (#intervals) * step.
  const step = night.samples.length > 1
    ? night.samples[1].t_unix - night.samples[0].t_unix
    : 600;
  const secs = (inWin.length - 1) * step;
  const h = Math.floor(secs / 3600);
  const m = Math.round((secs % 3600) / 60);
  return h > 0 ? `${h}h${String(m).padStart(2, "0")}m` : `${m}m`;
}

// ------------------------------------------------------------- status glyphs

/** Moon-separation status: a glyph paired with the number, never color alone
 *  (design spec §6/§8). ✕ < 15°, ⚠ < 30°, ☾ otherwise. The glyph is the carrier
 *  in night mode where tone color collapses. */
export function moonSepGlyph(sepDeg: number): "✕" | "⚠" | "☾" {
  if (sepDeg < 15) return "✕";
  if (sepDeg < 30) return "⚠";
  return "☾";
}

/** Tone for the moon separation (used only as a secondary channel; the glyph is
 *  primary). */
export function moonSepTone(sepDeg: number): "good" | "warn" | "bad" {
  if (sepDeg < 15) return "bad";
  if (sepDeg < 30) return "warn";
  return "good";
}

/** A human darkness-window label: "Astro-dark", "Nautical dark", or
 *  "Darkest (no astro-dark)" so the high-latitude fallback is honest. */
export function darknessLabel(kind: VisibilityNight["darkness_kind"]): string {
  switch (kind) {
    case "astronomical":
      return "Astro-dark";
    case "nautical":
      return "Nautical dark";
    default:
      return "Darkest (no astro-dark)";
  }
}

/** A compact phase + illumination string, e.g. "34% Waning Crescent". */
export function fmtMoonPhase(illumination: number, phaseName: string): string {
  return `${Math.round(illumination * 100)}% ${phaseName}`;
}
