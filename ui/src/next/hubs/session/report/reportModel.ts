// reportModel.ts - the pure shaping and the copy for the night report.
//
// No React, no DOM, no fetch: every function here is a string or a number in,
// a string or a number out, so the sentences this screen makes a promise with
// can be asserted without mounting anything.
//
// WHAT IS SHARED AND WHAT IS RE-DERIVED (wave R7 section 2.1). The maths stays
// where it is: `lib/reportChart.ts` (`endReasonMeta`, `trendGeomTimed`),
// `lib/bundleView.ts` (`masterChips`, `keptSummary`, `layoutOptions`, the two
// disabled-reason helpers, `bundleQuery`) and `lib/eta.ts` (`fmtDuration`) are
// imported, not copied. What IS re-derived here is the filter-name -> design
// token map: the only other copy lives in `hubs/session/now/filters.ts`, which
// another area owns, and R7's rule is that no area imports a sibling area's
// module. Both copies exist for the same reason `next` and `classic` both have
// one - a shared helper across areas is a merge conflict waiting for two tasks.

import { fmtDuration } from "../../../../lib/eta";
import { endReasonMeta } from "../../../../lib/reportChart";
import { keptSummary, materializeDisabledReason } from "../../../../lib/bundleView";
import { accessPhrase } from "../../../../lib/caps";
import type { BundleGroupSummary, BundlePreview, FilterBreakdown } from "../../../../types";
import type { Tone } from "../../../ui";

// ------------------------------------------------------------ filter colour

const TOKENS = ["L", "R", "G", "B", "Ha", "OIII", "SII", "OSC"] as const;
export type FilterToken = (typeof TOKENS)[number];

/** Normalise a wheel's own filter name onto one of the eight design tokens.
 *
 *  The name comes from the DRIVER - one wheel says "Ha", another "H-alpha",
 *  another "S2" for the same glass - so the match is normalised, and a name
 *  this palette has no token for returns null rather than being painted as the
 *  nearest-looking filter. A frame stack labelled with someone else's colour is
 *  worse than one painted neutral. */
export function filterToken(name: string | null | undefined): FilterToken | null {
  if (!name) return null;
  const k = name.trim().toLowerCase().replace(/[\s_-]/g, "");
  if (k === "l" || k === "lum" || k === "luminance") return "L";
  if (k === "r" || k === "red") return "R";
  if (k === "g" || k === "green") return "G";
  if (k === "b" || k === "blue") return "B";
  if (k === "ha" || k === "halpha" || k === "h") return "Ha";
  if (k === "oiii" || k === "o3" || k === "oxygen") return "OIII";
  if (k === "sii" || k === "s2" || k === "sulphur" || k === "sulfur") return "SII";
  if (k === "osc" || k === "colour" || k === "color" || k === "rggb") return "OSC";
  return null;
}

/** The swatch colour for a filter. Never the only carrier of the filter's
 *  identity - `filterLabel` rides beside it in every row. */
export function filterColor(name: string | null | undefined): string {
  const t = filterToken(name);
  return t ? `var(--nx-filter-${t})` : "var(--text-faint)";
}

/** The wheel's own word, or the honest stand-in when a frame carries none. */
export function filterLabel(name: string | null | undefined): string {
  const s = (name ?? "").trim();
  return s === "" ? "NO FILTER" : s;
}

// ---------------------------------------------------------------- the lines

/** `12 frames - 1h 0m - HFR 2.31`. The HFR blank is a hyphen, not the legacy
 *  em-dash (copy rule), and it means "the server graded no frame here", which
 *  is why it is not rendered as 0.00. */
export function filterLine(f: FilterBreakdown): string {
  const hfr = f.hfr_median != null ? f.hfr_median.toFixed(2) : "-";
  return `${f.frames} frames · ${fmtDuration(f.integration_s)} · HFR ${hfr}`;
}

/** The rejected tail, or null when nothing was rejected - an explicit
 *  "0 rejected" reads as a problem report on a clean night. */
export function rejectedLine(rejected: number): string | null {
  return rejected > 0 ? `${rejected} rejected` : null;
}

/** `M42 · Ha · 300s · g100 · bin1` - the group's identity, in the order the
 *  server's own directory name uses. */
export function groupLine(g: BundleGroupSummary): string {
  const parts = [g.target, g.filter ?? "NoFilter", `${g.exposure_s}s`];
  if (g.gain != null) parts.push(`g${g.gain}`);
  if (g.binning != null) parts.push(`bin${g.binning}`);
  return parts.join(" · ");
}

/** `42 lights (40 accepted) · 38 kept` - the counts, with each clause dropped
 *  when it would only repeat the one before it. */
export function groupCountLine(g: BundleGroupSummary): string {
  let s = `${g.light_count} lights`;
  if (g.accepted_count !== g.light_count) s += ` (${g.accepted_count} accepted)`;
  if (g.kept_count != null && g.kept_count !== g.light_count) s += ` · ${g.kept_count} kept`;
  return s;
}

// -------------------------------------------------- the em-dash boundary

/** Hyphens, never em-dashes, in UI copy (ARCHITECTURE non-negotiable 5).
 *
 *  THREE strings this screen renders come from modules `#/classic` renders
 *  too, and all three still carry an em-dash: `endReasonMeta("unsafe")`'s
 *  "UNSAFE - STOPPED", `keptSummary`'s "rated good - the weakest N", and
 *  `materializeDisabledReason`'s "No local subs on this machine - the FITS
 *  live on your imaging host". Editing those modules would change the legacy
 *  UI's copy, which this wave does not do, so the dash is normalised HERE, at
 *  the one boundary every one of them crosses on the way to the new screen.
 *  Everything else passes through unchanged. */
export function hyphenate(s: string): string {
  return s.replace(/\s*[—–]\s*/g, " - ");
}

/** `hyphenate` for the helpers that legitimately return null. */
export function hyphenateOrNull(s: string | null): string | null {
  return s == null ? null : hyphenate(s);
}

// ------------------------------------------------------------- end reason

/** The end-reason WORD, hyphenated. */
export function endReasonWord(reason: string | null): string {
  return hyphenate(endReasonMeta(reason).word);
}

/** "38 of 42 photos rated good ..." - the OUTCOME of a weight cutoff, not the
 *  flag count, and null when no cutoff is active. Hyphenated. */
export function keptLine(preview: BundlePreview | null): string | null {
  return hyphenateOrNull(keptSummary(preview));
}

/** Why writing the export folder is not meaningful right now - no frames, no
 *  preview yet, or the FITS are not on this machine. Hyphenated. This is the
 *  DATA truth only; the capability truth is `MATERIALIZE_CAP_REASON`, and the
 *  two are never merged into one sentence. */
export function materializeReason(
  framesCaptured: number, preview: BundlePreview | null,
): string | null {
  return hyphenateOrNull(materializeDisabledReason(framesCaptured, preview));
}

/** The pill tone for an end reason. `good | warn | bad` are already the design
 *  tones, so this is a widening, not a translation. */
export function endReasonTone(reason: string | null): Tone {
  return endReasonMeta(reason).tone;
}

// -------------------------------------------------------------- trend data

export interface TrendPointT { t: number; v: number; }

/** `[ts, value][]` from the report -> the `{t, v}` shape `trendGeomTimed`
 *  takes. The report's timestamps are unix SECONDS. */
export function trendPoints(pairs: [number, number][]): TrendPointT[] {
  return pairs.map(([t, v]) => ({ t, v }));
}

/** hh:mm for a trend's start/end caption. */
export function fmtClock(t: number): string {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** The whole date-and-time stamp used by the picker rows and the summary. */
export function fmtStamp(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleString();
}

/** Clock only - a safety event's row already sits inside one night. */
export function fmtEventClock(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleTimeString();
}

// ------------------------------------------------------------------- copy

/** Every sentence this screen can print, in one place, so a test asserts the
 *  string the user reads rather than a paraphrase of it. Hyphens throughout;
 *  the legacy view's em-dashes are the defect this wave fixes. */
export const REPORT_COPY = {
  indexLoading: "Reading the session index...",
  indexError: (why: string) => `Couldn't read the list of session reports: ${why}`,
  noReportsTitle: "No reports yet",
  noReportsHint:
    "A report is generated whenever a sequence finishes, is aborted, or errors out.",
  detailLoading: "Reading this night's report...",
  detailErrorTitle: "Couldn't load report",
  noFrames: "No frames were recorded for this run.",
  noTargets: "No target was shot before this run ended.",
  safetyNone: "Nothing tripped the safety monitor during this run.",
  trendNone: "no samples",
  bundleBlurb:
    "One .zip with tonight's photos already sorted into folders your stacking "
    + "software understands - PixInsight, Siril or APP - together with the "
    + "matching calibration frames and a quality score for each photo.",
  previewError: (why: string) =>
    `Couldn't work out what this session would bundle: ${why}. The .zip below `
    + "still streams whatever is on disk; only the per-group breakdown is missing.",
  recounting: "recounting for the new cutoff",
  advancedSummary: "ADVANCED",
  advancedSub: "layout, weighting, and a folder on the rig",
  layoutLabel: "Folder layout for your stacker",
  weightAltLabel: "Weight subs by altitude",
  weightAltNote:
    "Folds a sin(altitude) transparency term into each sub's weight - higher "
    + "subs (less air to shoot through) score higher. Off by default: the weight "
    + "is sharpness (HFR) + roundness (ecc) + guide RMS.",
  keepLabel: "Flag the weakest subs",
  keepNote:
    "Subs whose weight is below this are marked keep=false in the manifest and "
    + "weights.csv. Weights are normalized per group (best sub = 1.0). Nothing "
    + "is deleted - every sub is still exported.",
  keepFieldLabel: "WEIGHT CUTOFF",
  materializeBlurb:
    "Sorts tonight's photos into stacker-ready folders under captures/exports/ "
    + "on this machine, without using extra disk space.",
  materializeDanger:
    "The photos in the exports folder are the SAME files as your originals, not "
    + "copies - deleting or editing one there deletes or edits your original "
    + "capture. Stack from this folder; don't tidy up inside it.",
  materializeLabel: "MAKE A FOLDER OF TONIGHT'S PHOTOS HERE",
  materializeBusy: "SORTING",
  materializeFailed: "Couldn't materialize the bundle",
  zipLabel: "DOWNLOAD BUNDLE.ZIP",
  csvLabel: "DOWNLOAD FRAMES.CSV",
  retryPreview: "RETRY PREVIEW",
  retryIndex: "RETRY",
} as const;

/** Writing the export folder is a WRITE to the capture box, gated by
 *  `control.capture` server-side (`api/reports` materialize route). The
 *  sentence names the capability through `accessPhrase` so it tracks the role
 *  table instead of hard-coding "admin".
 *
 *  Lower case and no full stop, unlike the legacy view's version of the same
 *  sentence: this string is BOTH the button's locked reason and the tail of
 *  `LockNote`'s "Read-only - ..." line, which is the shape every other panel in
 *  the new UI uses. One string, two places, no drift. */
export const MATERIALIZE_CAP_REASON =
  `writing to the capture box needs ${accessPhrase("control.capture")}`;

/** A failed preview is a THIRD truth, distinct from "you lack the capability"
 *  and from "there is nothing here to sort": `materializeDisabledReason` only
 *  knows `preview == null` and would say "Still loading", which after a dropped
 *  request is a wait that never ends and a button that never comes back. */
export const materializePreviewReason = (why: string): string =>
  `Couldn't read this session's bundle preview: ${why}`;
