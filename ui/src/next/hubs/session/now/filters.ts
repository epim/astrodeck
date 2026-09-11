// filters.ts - the wheel's own filter names, mapped onto the eight design
// tokens, and the per-filter folds every band on this screen shares.
//
// FILTER NAMES COME FROM THE DRIVER, not from a list in this file: a wheel can
// say "Ha", "H-alpha", "Oiii", "OIII" or "S2" for the same piece of glass, and
// asking the user to type one is the copy rule this UI is built against. So the
// match is normalised and the UNKNOWN case falls back to the accent rather than
// picking the nearest-looking token - a filter painted as if it were something
// else is worse than a filter painted neutral.

import type { SequencePlan, Session, SessionFrame } from "../../../../types";
import { effectiveAccepted } from "../../../../lib/sessionReview";

const TOKENS = ["L", "R", "G", "B", "Ha", "OIII", "SII", "OSC"] as const;
export type FilterToken = (typeof TOKENS)[number];

/** Normalise a driver's filter name onto one of the eight tokens, or null. */
export function filterToken(name: string | null | undefined): FilterToken | null {
  if (!name) return null;
  const k = name.trim().toLowerCase().replace(/[\s_-]/g, "");
  if (k === "l" || k === "lum" || k === "luminance") return "L";
  if (k === "r" || k === "red") return "R";
  if (k === "g" || k === "green") return "G";
  if (k === "b" || k === "blue") return "B";
  if (k === "ha" || k === "halpha" || k === "h" || k === "hα") return "Ha";
  if (k === "oiii" || k === "o3" || k === "oxygen" || k === "oiii3") return "OIII";
  if (k === "sii" || k === "s2" || k === "sulphur" || k === "sulfur") return "SII";
  if (k === "osc" || k === "colour" || k === "color" || k === "rggb") return "OSC";
  return null;
}

/** The CSS colour for a filter, or the accent when the wheel names glass this
 *  palette has no token for. */
export function filterColor(name: string | null | undefined): string {
  const t = filterToken(name);
  return t ? `var(--nx-filter-${t})` : "var(--accent)";
}

/** Every distinct capture step of a plan, folded per filter. ONE ROW PER
 *  DISTINCT STEP, not per target: `compile_plan` copies each capture step onto
 *  every target in a pool and only one of them is shot on a given night, so
 *  summing per target promises four times the integration the rig can deliver
 *  (server `flows/tonight.py::_budget` makes the same cut for the same reason). */
export interface PlannedFilter {
  filter: string;
  /** Frames asked for across the distinct steps. */
  count: number;
  /** Seconds of planned integration. */
  plannedS: number;
  /** The step ids that feed it, so accepted frames can be counted back. */
  stepIds: string[];
}

export function plannedByFilter(plan: SequencePlan | null | undefined): PlannedFilter[] {
  if (!plan) return [];
  const seen = new Set<string>();
  const out = new Map<string, PlannedFilter>();
  for (const t of plan.targets ?? []) {
    for (const s of t.steps ?? []) {
      if ((s.frame_type ?? "Light") !== "Light") continue;
      const sig = [s.filter ?? "", s.exposure_s, s.gain, s.binning, s.count].join("|");
      const filter = s.filter || "-";
      // Every step id is collected (a pool's copies all bank into the same
      // filter row), but the PLANNED total counts one copy of each distinct
      // step, exactly as the server's budget does.
      const row = out.get(filter)
        ?? { filter, count: 0, plannedS: 0, stepIds: [] as string[] };
      if (s.id) row.stepIds.push(s.id);
      if (!seen.has(sig)) {
        seen.add(sig);
        row.count += s.count;
        row.plannedS += s.count * s.exposure_s;
      }
      out.set(filter, row);
    }
  }
  return [...out.values()];
}

/** The report id this run is writing to. `session.nights` is appended at RUN
 *  START (engine.py), so the LAST entry is tonight while a run is live and the
 *  ones before it are the nights already banked. */
export function tonightNightKey(session: Session | null): string | null {
  const n = session?.nights ?? [];
  return n.length ? n[n.length - 1] : null;
}

/** Accepted frames per filter, restricted to one night when `night` is given.
 *  Acceptance is EFFECTIVE (an override beats the auto grade). */
export function acceptedByFilter(
  session: Session | null, night: string | null,
): Map<string, number> {
  const out = new Map<string, number>();
  if (!session) return out;
  const filterOf = new Map<string, string>();
  for (const t of session.plan?.targets ?? []) {
    for (const s of t.steps ?? []) if (s.id) filterOf.set(s.id, s.filter || "-");
  }
  const rows: SessionFrame[] = session.frames ?? [];
  for (const f of rows) {
    if (night != null && f.night !== night) continue;
    if (!effectiveAccepted(f)) continue;
    const filt = filterOf.get(f.step_id) ?? "-";
    out.set(filt, (out.get(filt) ?? 0) + 1);
  }
  return out;
}

/** One accepted frame's exposure, by filter, from the frozen plan. */
export function exposureByFilter(plan: SequencePlan | null | undefined): Map<string, number> {
  const out = new Map<string, number>();
  for (const t of plan?.targets ?? []) {
    for (const s of t.steps ?? []) {
      const filt = s.filter || "-";
      if (!out.has(filt)) out.set(filt, s.exposure_s);
    }
  }
  return out;
}
