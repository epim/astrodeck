// logSources.ts - which log the Log screen is showing, and how it asks for it.
//
// THREE SOURCES, and the difference is the whole point (plan §B.2.1):
//
//  1. the live ring - `store.logs`, capped at 200 lines. Roughly the last 40
//     minutes of a ten-hour run. It is already in the browser, so selecting
//     TONIGHT must issue NO request; a screen that re-fetched what it already
//     had would look identical and cost a round trip per filter tap.
//  2. a persisted night - `GET /api/logs?night=YYYY-MM-DD&level=`. The route's
//     own docstring: "Default (no params) = the in-memory ring, byte-identical
//     to before. `night=YYYY-MM-DD` reads that night's PERSISTED file instead
//     (UX #9 - the ring is only the last ~40 minutes of a ten-hour run), and
//     `level` filters either source." (app.py:6999-7020)
//  3. which nights exist at all - `GET /api/logs/nights` ->
//     `{current, persisted, nights:[{night,bytes}]}` (app.py:7022-7030).
//
// The level filter therefore runs in two places for one meaning: client-side
// over the ring, `level=` on the wire for a night. Both must answer the same
// for the same rows, which is why the filter itself is ONE function here rather
// than an inline `.filter` in the screen.
//
// PURE: no React, no store, no `u()` (that would drag `lib/base.ts`'s
// `window.location` read into a plain-node test). The screen applies `u()` to
// `exportPath()`'s result.

import type { LogLine } from "../../../../types";

/** The value of the night chip that means "the live ring", not a file. */
export const RING = "";

export type LevelFilter = "" | "info" | "warning" | "error";

export const LEVELS: readonly { id: LevelFilter; label: string }[] = [
  { id: "", label: "ALL" },
  { id: "info", label: "INFO" },
  { id: "warning", label: "WARNING" },
  { id: "error", label: "ERROR" },
];

export type ExportFormat = "txt" | "jsonl";

export interface NightRow {
  night: string;
  bytes: number;
}

export interface NightsIndex {
  current: string;
  persisted: boolean;
  nights: NightRow[];
}

/** `"info"` when a stored value is not one of the four we offer, so a corrupted
 *  localStorage entry cannot leave the screen filtering on a level the server
 *  has never heard of and showing nothing. */
export function parseLevel(raw: string | null | undefined): LevelFilter {
  return LEVELS.some((l) => l.id === raw) ? (raw as LevelFilter) : "";
}

/** The chip row: TONIGHT first (the ring), then the persisted nights NEWEST
 *  first. The current night appears once - as TONIGHT - even when it already
 *  has a file on disk, because two chips for one night is two answers to
 *  "which log am I reading". */
export function nightOptions(idx: NightsIndex | null): { id: string; label: string }[] {
  const out = [{ id: RING, label: "TONIGHT" }];
  if (!idx || !idx.persisted) return out;
  const seen = new Set<string>([idx.current]);
  for (const n of [...idx.nights].sort((a, b) => (a.night < b.night ? 1 : -1))) {
    if (seen.has(n.night)) continue;
    seen.add(n.night);
    out.push({ id: n.night, label: n.night });
  }
  return out;
}

/** The request a night selection makes, or `null` for the ring - the ring is
 *  already in the store and asking for it again would be a request whose only
 *  effect is latency. */
export function logQueryPath(night: string, level: LevelFilter, limit = 0): string | null {
  if (!night) return null;
  const q = [`night=${encodeURIComponent(night)}`];
  if (level) q.push(`level=${encodeURIComponent(level)}`);
  if (limit > 0) q.push(`limit=${limit}`);
  return `/api/logs?${q.join("&")}`;
}

/** The same filter the route applies server-side, for the ring. */
export function filterByLevel(rows: readonly LogLine[], level: LevelFilter): LogLine[] {
  if (!level) return [...rows];
  return rows.filter((r) => r.data?.level === level);
}

/** Both sources arrive OLDEST first (the ring is append-order and so is the
 *  route). The screen reads newest first, like `LogDrawer`'s `[...logs].reverse()`. */
export function newestFirst(rows: readonly LogLine[]): LogLine[] {
  return [...rows].reverse();
}

/** Which night an export actually writes: TONIGHT means today's key, which only
 *  the server knows, so it comes from `/api/logs/nights`.`current`. */
export function resolveNight(night: string, idx: NightsIndex | null): string {
  if (night) return night;
  return idx?.current ?? "";
}

export function exportPath(night: string, format: ExportFormat): string {
  return `/api/logs/export?night=${encodeURIComponent(night)}&format=${format}`;
}

/** Why EXPORT cannot run, or null when it can.
 *
 *  Pre-checked rather than discovered: `/api/logs/export` 404s with
 *  "log persistence is disabled" or "no persisted log for <night>", and a
 *  download link that opens a tab onto an error page has spent the user's tap
 *  to tell them something this screen already knew. */
export function exportLockedReason(night: string, idx: NightsIndex | null): string | null {
  if (idx == null) return "checking which nights are on disk";
  if (!idx.persisted) return "log persistence is off on this rig, so there is no file to export";
  const target = resolveNight(night, idx);
  if (!target) return "this rig has not named a night yet";
  if (!idx.nights.some((n) => n.night === target)) {
    return "this night was never written to disk";
  }
  return null;
}
