// schedule.ts — pure presentation helpers for the per-target autorun schedule
// (wave-3 §1). No React, no DOM: npx-tsx testable (rotatorDial.ts precedent).
// The engine's semantics live server-side (sequence/schedule.py); this module
// only summarizes the CONFIG shape for the collapsed sub-panel chip.

import type { Schedule } from "../types";

function offset(min: number): string {
  if (!min) return "";
  return ` ${min > 0 ? "+" : "−"}${Math.abs(min)}m`;
}

function startLabel(s: Schedule): string {
  switch (s.start_mode) {
    case "now": return "";
    case "dusk": return `Dusk${offset(s.start_offset_min)}`;
    case "dawn": return `Dawn${offset(s.start_offset_min)}`;
    case "time": return s.start_time ?? "??:??";
    default: return "";
  }
}

function stopLabel(s: Schedule): string {
  const parts: string[] = [];
  if (s.stop_mode === "dawn") parts.push(`dawn${offset(s.stop_offset_min)}`);
  if (s.stop_mode === "time") parts.push(s.stop_time ?? "??:??");
  if (s.max_run_min > 0) parts.push(`max ${s.max_run_min}m`);
  return parts.join(" · ");
}

/** Collapsed-chip summary, e.g. "Runs immediately", "Dusk +30m → dawn",
 *  "22:00 → max 90m · skip if missed", "Alt ≥ 35° → dawn −20m". */
export function scheduleSummary(s: Schedule | undefined): string {
  if (!s) return "Runs immediately";
  const start = startLabel(s);
  const gate = s.min_altitude_deg > 0 ? `Alt ≥ ${Math.round(s.min_altitude_deg)}°` : "";
  const ha = s.max_hour_angle_h > 0 ? `HA ±${s.max_hour_angle_h}h` : "";
  const sep = s.min_moon_sep_deg > 0 ? `Moon ≥ ${Math.round(s.min_moon_sep_deg)}°` : "";
  const illum = s.max_moon_illum_pct > 0 ? `Moon ≤ ${Math.round(s.max_moon_illum_pct)}%` : "";
  const begin = [start, gate, ha, sep, illum].filter(Boolean).join(" & ");
  const stop = stopLabel(s);
  const missed = s.on_missed === "skip" ? "skip if missed" : "";
  if (!begin && !stop && !missed) return "Runs immediately";
  const head = begin || "Now";
  const tail = [stop, missed].filter(Boolean).join(" · ");
  return tail ? `${head} → ${tail}` : head;
}
