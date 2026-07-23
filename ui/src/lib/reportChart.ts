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

export interface TrendGeom { d: string; yMin: number; yMax: number; }

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
