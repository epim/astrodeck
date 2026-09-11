// format.ts - shared display formatters for the next UI: clock, duration,
// degrees, percent, bytes, RA/Dec.
//
// REUSE: `ui/src/lib/eta.ts` already has `fmtClock(ms, nowMs?)` (local wall
// clock, "(+1d)" suffix on a day rollover) and `ui/src/lib/gallery.ts` already
// has `fmtBytes(bytes)` (1024-based, "38.2 GB") - both re-exported below
// rather than duplicated. `fmtDuration` is a FRESH implementation, not a
// re-export of `eta.ts`'s `fmtDuration`: that one renders sub-minute values as
// "9s" (no space) and folds seconds into the minutes tier ("1m 35s"), while
// this task's worked examples ("1h 30m", "45 s") want a bare space before a
// lone "s" unit and no seconds once minutes are shown. `ui/src/lib/
// catalogFormat.ts` has `fmtMag`/`fmtAlt`/`altTone` only - no RA/Dec formatter
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

/** "58°" / "2.54°" - degrees with a fixed decimal count. */
export function fmtDeg(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "--";
  return `${v.toFixed(digits)}°`;
}

/** "82%". */
export function fmtPct(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "--";
  return `${v.toFixed(digits)}%`;
}

// BOTH FORMATTERS ROUND IN ONE PLACE, TOTAL MINUTES, and then split - because
// rounding the minutes separately from the degrees/hours drops the carry. The
// wave-2 review found it: 22.9999 h printed "22h 00m" (the minutes rounded to
// 60 and `% 60` folded them back to 0 while the hours stayed at 22), an hour
// wrong on a readout an operator uses to check the mount is where the plan
// says. 62.996 degrees printed "+62° 00′" the same way. Splitting a single
// rounded total cannot do that: the carry is already inside the number.

/** "22h 57m" - right ascension in hours:minutes, wrapped into [0h, 24h). */
export function fmtRA(hours: number): string {
  const h = ((hours % 24) + 24) % 24;
  // Modulo AFTER rounding, so 23h 59.7m carries to 24h and then wraps to 0h
  // rather than printing "23h 00m" - a whole hour off, and on the one value
  // where a wrong answer looks most plausible.
  const total = Math.round(h * 60) % (24 * 60);
  const hh = Math.floor(total / 60);
  const mm = total % 60;
  return `${String(hh).padStart(2, "0")}h ${String(mm).padStart(2, "0")}m`;
}

/** "+62° 37′" (unicode minus for negative) - declination in degrees:minutes. */
export function fmtDec(deg: number): string {
  const sign = deg < 0 ? "−" : "+";
  // No wrap here, unlike RA: declination runs -90..+90 and +90° 00′ is the
  // pole, a real place to point, not an overflow to fold away.
  const total = Math.round(Math.abs(deg) * 60);
  const dd = Math.floor(total / 60);
  const mm = total % 60;
  return `${sign}${String(dd).padStart(2, "0")}° ${String(mm).padStart(2, "0")}′`;
}
