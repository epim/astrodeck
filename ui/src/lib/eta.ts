// Pure formatting + countdown math for the Monitor view (monitor spec §10).
// No React, no DOM — unit-tested in eta.test.ts. Also the single source of truth
// for the Monitor's shared timing constants (master §A.7).

// ----------------------------------------------------------------- constants
// One source of truth; the cells, store auto-select and stall logic all import
// from here so the numbers never drift (monitor spec §9 "Shared constants").
export const LIVE_WINDOW_S = 8; // "new frame arriving" window for the LIVE chip
export const STALL_MARGIN_S = 20; // grace before declaring a capture stall
export const GUIDE_STALE_S = 15; // guider-stale window
export const COOLER_AT_TARGET_C = 1.0; // MUST equal engine cool-and-wait threshold
export const ETA_MIN_FRAMES = 3; // ETA stays low-confidence (~) below this
export const OVERHEAD_EMA_ALPHA = 0.1; // per-frame overhead EMA smoothing
export const DEFAULT_OVERHEAD_S = 12; // seed overhead until ETA_MIN_FRAMES measured
export const AUTOSELECT_IDLE_MS = 8000; // only auto-select Monitor after this idle
export const SPARKLINE_SCALE_ARCSEC = 4; // FIXED guide y-scale (no auto-rescale)
export const THUMB_BRIGHTNESS_NIGHT_DEFAULT = 0.5; // per-tile night dimmer default

// ----------------------------------------------------------------- formatters

/**
 * Human duration. 7340 -> "2h 2m"; 95 -> "1m 35s"; 9 -> "9s"; <0 clamps to 0.
 * Hours mode drops seconds (h+m); minutes mode shows m+s; under a minute is s.
 */
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
  return `${t}s`;
}

/**
 * Local 24h wall clock from an epoch-ms instant: "03:41". Appends "(+1d)" when
 * the instant falls on a later calendar day than "now" (monitor spec §4.2:
 * crossing local midnight). `nowMs` defaults to Date.now().
 */
export function fmtClock(ms: number, nowMs: number = Date.now()): string {
  const d = new Date(ms);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const base = `${hh}:${mm}`;
  // Day-delta by local calendar date (not a 24h window) so 23:59→00:01 rolls.
  const dayOf = (x: number): number => {
    const c = new Date(x);
    return Math.floor(
      new Date(c.getFullYear(), c.getMonth(), c.getDate()).getTime() / 86400000,
    );
  };
  const delta = dayOf(ms) - dayOf(nowMs);
  if (delta === 1) return `${base} (+1d)`;
  if (delta > 1) return `${base} (+${delta}d)`;
  return base;
}

/**
 * Countdown string with a tested MM:SS ↔ H:MM boundary at exactly one hour.
 * Below 3600s: "MM:SS" (e.g. 95 -> "01:35"). At/above 3600s: "H:MM" (e.g.
 * 3600 -> "1:00", 7325 -> "2:02"). Negatives clamp to "00:00".
 */
export function fmtCountdown(s: number): string {
  const t = Math.max(0, Math.round(s));
  if (t >= 3600) {
    const h = Math.floor(t / 3600);
    const m = Math.floor((t % 3600) / 60);
    return `${h}:${String(m).padStart(2, "0")}`;
  }
  const m = Math.floor(t / 60);
  const sec = t % 60;
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

/**
 * Client-anchored finish derived from ONE clock (monitor spec §4.2 / crit C11/B4).
 * On each `sequence` event the client stores {eta_s, receivedAtMs}; this renders
 * the live remaining + finish instant from the client's own `nowMs`, so the
 * countdown and the absolute clock can never disagree. The server's
 * `server_now_ms` is used only to detect gross skew elsewhere, never rendered.
 * `remainingS` clamps at 0 (a finished/overdue run shows 0, not negative time).
 */
export function deriveFinish(
  etaS: number,
  receivedAtMs: number,
  nowMs: number,
): { remainingS: number; finishAtMs: number } {
  const elapsedSinceEmit = (nowMs - receivedAtMs) / 1000;
  const remainingS = Math.max(0, etaS - elapsedSinceEmit);
  return { remainingS, finishAtMs: nowMs + remainingS * 1000 };
}
