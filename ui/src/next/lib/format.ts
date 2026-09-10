// format.ts — shared display formatters for the next UI: clock, duration,
// degrees, percent, bytes, RA/Dec.
//
// REUSE: `ui/src/lib/eta.ts` already has `fmtClock(ms, nowMs?)` (local wall
// clock, "(+1d)" suffix on a day rollover) and `ui/src/lib/gallery.ts` already
// has `fmtBytes(bytes)` (1024-based, "38.2 GB") — both re-exported below
// rather than duplicated. `fmtDuration` is a FRESH implementation, not a
// re-export of `eta.ts`'s `fmtDuration`: that one renders sub-minute values as
// "9s" (no space) and folds seconds into the minutes tier ("1m 35s"), while
// this task's worked examples ("1h 30m", "45 s") want a bare space before a
// lone "s" unit and no seconds once minutes are shown. `ui/src/lib/
// catalogFormat.ts` has `fmtMag`/`fmtAlt`/`altTone` only — no RA/Dec formatter
// anywhere in `ui/src/lib`, so `fmtRA`/`fmtDec` below are new, lifted from the
// design prototype's `fmtRA()`/`fmtDec()` (scratchpad seams/proto/logic.js).

export { fmtClock } from "../../lib/eta";
export { fmtBytes } from "../../lib/gallery";

/** "1h 30m" (hours, drops seconds) / "12m 34s" (minutes) / "45 s" (seconds only). */
export function fmtDuration(s: number): string {
  const t = Math.max(0, Math.round(s));
  if (t >= 3600) {
    const h = Math.floor(t / 3600);
    const m = Math.floor((t % 3600) / 60);
    return `${h}h ${m}m`;
  }
  if (t >= 60) {
    const m = Math.floor(t / 60);
    const sec = t % 60;
    return `${m}m ${sec}s`;
  }
  return `${t} s`;
}

/** "58°" / "2.54°" — degrees with a fixed decimal count. */
export function fmtDeg(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "--";
  return `${v.toFixed(digits)}°`;
}

/** "82%". */
export function fmtPct(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "--";
  return `${v.toFixed(digits)}%`;
}

/** "22h 57m" — right ascension in hours:minutes. */
export function fmtRA(hours: number): string {
  const h = ((hours % 24) + 24) % 24;
  const hh = Math.floor(h);
  const mm = Math.round((h - hh) * 60) % 60;
  return `${String(hh).padStart(2, "0")}h ${String(mm).padStart(2, "0")}m`;
}

/** "+62° 37'" (unicode minus for negative) — declination in degrees:minutes. */
export function fmtDec(deg: number): string {
  const sign = deg < 0 ? "−" : "+";
  const a = Math.abs(deg);
  const dd = Math.floor(a);
  const mm = Math.round((a - dd) * 60) % 60;
  return `${sign}${String(dd).padStart(2, "0")}° ${String(mm).padStart(2, "0")}′`;
}
