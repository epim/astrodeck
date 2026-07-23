// calibrationLibrary.ts — pure formatting + a client-side coverage MIRROR for
// PRO-1's master library (calibration-library spec §1.3 / §6). No React, no DOM:
// npx-tsx testable (eta.ts / calibration.ts precedent).
//
// The AUTHORITATIVE matcher is the Python core (matcher.py, fully pytest'd, used
// by the library + POST /api/sequence/preflight). This mirrors the same rule so
// the live pre-flight row can re-check SYNCHRONOUSLY as the plan is edited,
// rather than round-tripping the server on every keystroke (Open Decision D3).
// The parallel is kept honest by tsx tests against the same tolerance cases the
// Python matcher uses.
//
// NOTE: this is a DIFFERENT file from lib/calibration.ts (NOV-10's capture-nudge
// logic) — only its FrameType union is reused.

import type { FrameType } from "./calibration";
import type { SequencePlan } from "../types";

/** One master row as returned by GET /api/calibration/masters. `frame_type` is
 *  normalized to the title-case FrameType union at the API boundary. */
export interface MasterRow {
  id: string;
  frame_type: FrameType;
  exposure_s: number;
  gain: number;
  offset: number;
  temp_c: number | null;
  binning: number;
  filter: string;
  frame_count: number;
  built_ts: number;
}

/** POST /api/calibration/build response. */
export interface CalibrationBuildReport {
  masters_built: number;
  frames_indexed: number;
  buckets: number;
}

export interface CoverageTol {
  exposureTolPct: number;
  tempTolC: number;
}

const fmtExp = (s: number): string => (s >= 1 ? `${Math.round(s)}s` : `${s}s`);

/** A one-line human summary of a master row (Bias drops the exposure). */
export function masterRowSummary(m: MasterRow): string {
  const p: string[] = [];
  if (m.frame_type !== "Bias") p.push(fmtExp(m.exposure_s));
  p.push(`gain ${m.gain}`);
  if (m.temp_c != null) p.push(`${m.temp_c}°C`);
  p.push(`bin${m.binning}`);
  if (m.filter) p.push(m.filter);
  p.push(`×${m.frame_count}`);
  return p.join(" · ");
}

/** Group masters by type in a stable Dark → Flat → Bias order, dropping empty
 *  groups (mono rigs never have Flats-with-filters, etc.). */
export function groupMasters(rows: MasterRow[]): { type: FrameType; rows: MasterRow[] }[] {
  const order: FrameType[] = ["Dark", "Flat", "Bias"];
  return order
    .map((type) => ({ type, rows: rows.filter((r) => r.frame_type === type) }))
    .filter((g) => g.rows.length > 0);
}

const withinPct = (a: number, b: number, pct: number): boolean =>
  b === 0 ? a === 0 : Math.abs(a - b) <= Math.abs(b) * (pct / 100);

// Mirrors matcher.py dark_matches/flat_matches (Open Decision D3). tsx-tested
// against the same tolerance cases the Python matcher uses.
export function planCalibrationGaps(
  plan: SequencePlan,
  masters: MasterRow[],
  tol: CoverageTol,
): { missing: string[] } {
  if (masters.length === 0) return { missing: [] }; // empty library never nags
  const t = plan.cool_to ?? null;
  const needDark = new Set<string>();
  const needFlat = new Set<string>();
  const haveDark = (n: {
    exposure_s: number;
    gain: number;
    offset: number;
    binning: number;
  }): boolean =>
    masters.some(
      (m) =>
        m.frame_type === "Dark" &&
        m.gain === n.gain &&
        m.offset === n.offset &&
        m.binning === n.binning &&
        withinPct(n.exposure_s, m.exposure_s, tol.exposureTolPct) &&
        (t == null || m.temp_c == null || Math.abs(t - m.temp_c) <= tol.tempTolC),
    );
  const haveFlat = (filter: string, gain: number, binning: number): boolean =>
    masters.some(
      (m) =>
        m.frame_type === "Flat" &&
        m.filter === filter &&
        m.gain === gain &&
        m.binning === binning,
    );
  for (const tg of plan.targets) {
    if (tg.calibration) continue;
    for (const s of tg.steps) {
      const n = { exposure_s: s.exposure_s, gain: s.gain, offset: s.offset, binning: s.binning };
      if (!haveDark(n)) needDark.add(`${s.exposure_s}/${s.gain}/${s.binning}`);
      if (s.filter && !haveFlat(s.filter, s.gain, s.binning)) needFlat.add(s.filter);
    }
  }
  const missing: string[] = [];
  if (needDark.size) missing.push("darks");
  if (needFlat.size) missing.push(`flats (${[...needFlat].join(", ")})`);
  return { missing };
}
