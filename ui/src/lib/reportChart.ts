// lib/reportChart.ts — pure, DOM-free report→chart shaping + the live-strip ring
// (report viewer spec §3 Task 1). No React, no DOM — unit-tested directly with
// `npx tsx` (same harness convention as lib/eta.ts / lib/health.ts).

export interface LiveSample {
  t: number;                 // ms epoch of the sub
  hfr: number | null;
  stars: number | null;
  rms: number | null;
  temp: number | null;
}

export interface TrendGeom {
  d: string; yMin: number; yMax: number;
  // Present only on the TIME-axis path (trendGeomTimed) and only when the series
  // actually spans time — the caller uses them for the start/end clock captions.
  tMin?: number; tMax?: number;
}

/** Polyline over a fixed w×h box; x by index, y auto-scaled to data
 *  min/max with `padY` fractional headroom. Handles negatives (temp) and
 *  flat series (yMin==yMax => padded so the line sits mid-box). null if empty. */
export function trendGeom(
  values: number[], w: number, h: number, padY = 0.1,
): TrendGeom | null {
  if (values.length === 0) return null;
  let yMin = Math.min(...values), yMax = Math.max(...values);
  const span = Math.max(yMax - yMin, 1e-6);
  yMin -= span * padY;
  yMax += span * padY;
  const n = values.length;
  const toX = (i: number) => (n <= 1 ? w : (i / (n - 1)) * w);
  const toY = (v: number) => h - ((v - yMin) / (yMax - yMin)) * h;
  const d = values
    .map((v, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)} ${toY(v).toFixed(1)}`)
    .join(" ");
  return { d, yMin, yMax };
}

export interface TrendPoint { t: number; v: number; }

/** Polyline with x scaled by REAL time — the report trends carry `[ts, v]` pairs
 *  and each series is downsampled INDEPENDENTLY (HFR only on lights, temp on every
 *  frame), so index spacing silently distorts them: an hour-long guiding gap
 *  compresses to one pixel step and the three panels don't line up in wall-clock
 *  time. This is the sibling of `trendGeom`, not a replacement — the live
 *  in-acquisition strip is genuinely index-native and keeps using that one.
 *
 *  Degenerate input degrades safely: a single point, all-identical timestamps, or
 *  any non-finite timestamp falls back to even index spacing (never a divide by
 *  zero, never a collapse to x=0). Y-scaling is identical to `trendGeom`,
 *  including the flat-series padding. `null` when empty. */
export function trendGeomTimed(
  points: TrendPoint[], w: number, h: number, padY = 0.1,
): TrendGeom | null {
  if (points.length === 0) return null;
  const values = points.map((p) => p.v);
  let yMin = Math.min(...values), yMax = Math.max(...values);
  const span = Math.max(yMax - yMin, 1e-6);
  yMin -= span * padY;
  yMax += span * padY;

  const times = points.map((p) => p.t);
  const n = points.length;
  const usable = n > 1 && times.every((t) => Number.isFinite(t));
  const tMin = usable ? Math.min(...times) : 0;
  const tMax = usable ? Math.max(...times) : 0;
  const tSpan = tMax - tMin;
  const timed = usable && tSpan > 0;

  const toX = (t: number, i: number) =>
    timed ? ((t - tMin) / tSpan) * w : n <= 1 ? w : (i / (n - 1)) * w;
  const toY = (v: number) => h - ((v - yMin) / (yMax - yMin)) * h;
  // On a real time axis the polyline must walk left-to-right, so order by t (the
  // server already emits time-ordered frames; this just makes it impossible for an
  // out-of-order point to draw a backwards zigzag). The index fallback keeps the
  // caller's order — there, order IS the x axis.
  const ordered = timed ? [...points].sort((a, b) => a.t - b.t) : points;
  const d = ordered
    .map((p, i) => `${i === 0 ? "M" : "L"}${toX(p.t, i).toFixed(1)} ${toY(p.v).toFixed(1)}`)
    .join(" ");
  return timed ? { d, yMin, yMax, tMin, tMax } : { d, yMin, yMax };
}

/** Append one sample; keep at most `cap`, newest last (bounded ring). */
export function pushLiveSample(
  prev: LiveSample[], s: LiveSample, cap: number,
): LiveSample[] {
  const next = [...prev, s];
  return next.length > cap ? next.slice(next.length - cap) : next;
}

/** One series, nulls dropped. */
export function pickSeries(
  ring: LiveSample[], key: "hfr" | "stars" | "rms" | "temp",
): number[] {
  return ring.map((s) => s[key]).filter((v): v is number => v != null);
}

export interface EndReasonMeta { word: string; tone: "good" | "warn" | "bad"; }

/** Maps the engine's real end reasons (engine.py:621-690: complete, dawn_cutoff,
 *  aborted, error, quality, cooling_skip, unsafe) to a labelled tone. Unknown/
 *  null reasons fall back to an honest "IN PROGRESS" / uppercased-word warn. */
export function endReasonMeta(reason: string | null): EndReasonMeta {
  switch (reason) {
    case "complete":     return { word: "COMPLETE", tone: "good" };
    case "dawn_cutoff":  return { word: "DAWN CUTOFF", tone: "good" };
    case "aborted":      return { word: "ABORTED", tone: "warn" };
    case "quality":      return { word: "QUALITY STOP", tone: "warn" };
    case "cooling_skip": return { word: "COOLING SKIP", tone: "warn" };
    case "unsafe":       return { word: "UNSAFE — STOPPED", tone: "bad" };
    case "error":        return { word: "ERROR", tone: "bad" };
    default:             return { word: reason ? reason.toUpperCase() : "IN PROGRESS", tone: "warn" };
  }
}
