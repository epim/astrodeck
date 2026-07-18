// lib/rmsCompare.ts — pure same-night head-to-head RMS comparison (P5-T1,
// spec §6 P5). GuideView's provider-switch panel compares the LAST guide-stats
// window recorded under two different guide providers within the same session
// (store.ts's `guideRmsByKind`, tagged at bus-ingest time from
// `status.providers.guide.kind` — see store.ts's "guide" case) and renders a
// verdict: which provider guided tighter, "comparable" when the gap doesn't
// clear a noise-floor threshold, or "insufficient data" when a window is
// missing or too short to trust.
//
// No React import — pure data in, data out (see
// lib/__tests__/rmsCompare.test.ts, run directly under tsx per repo
// convention, mirroring lib/apiError.ts + apiError.test.ts).

/** One labelled RMS window: the LAST guide-stats tick recorded while a given
 *  provider was resolved. `rmsTotal` is arcsec (GuideStats.rms_total, the
 *  server's rolling-window RMS — see docs/native-parity/algorithms/
 *  phd2-guiding.md §13). `samples` is the frame count backing that rolling
 *  window (GuideStats.recent.length); the server keeps roughly a 100-sample
 *  window (see progress notes on the P1-T10 review's "RMS window 100-sample
 *  EXACT parity" finding), so `samples` never needs to exceed that in
 *  practice. */
export interface RmsWindow {
  label: string;
  rmsTotal: number;
  samples: number;
}

/** A window needs at least this many frames before its RMS is trusted for a
 *  head-to-head verdict — a handful of frames right after a provider switch
 *  or a fresh calibration is settle/acquisition noise, not a guiding verdict.
 *  Inclusive: a window with exactly this many samples is usable. */
export const MIN_WINDOW_SAMPLES = 20;

/** Two windows within this relative percentage of each other read as
 *  "comparable" rather than crowning a winner — real-sky RMS jitters run to
 *  run by more than a few percent on a calm night, so a tighter margin would
 *  flip the verdict on ordinary noise. Computed as
 *  `(larger - smaller) / smaller * 100`; inclusive (exactly at the threshold
 *  still reads comparable). */
export const COMPARABLE_THRESHOLD_PCT = 10;

export type RmsCompareVerdict =
  | "a-better"
  | "b-better"
  | "comparable"
  | "insufficient-data";

export interface RmsCompareResult {
  verdict: RmsCompareVerdict;
  /** Human-readable one-liner for the GuideView readout. Always mentions
   *  "better"/"comparable"/"insufficient data" per the verdict so a caller
   *  can render it verbatim. */
  message: string;
  /** Relative difference between the two windows, as a percentage of the
   *  smaller (better) RMS. `0` when both windows are exactly equal (including
   *  the zero-RMS case). `null` when no verdict could be computed. */
  deltaPct: number | null;
}

function isUsable(w: RmsWindow | null | undefined): w is RmsWindow {
  return (
    !!w &&
    Number.isFinite(w.rmsTotal) &&
    w.rmsTotal >= 0 &&
    Number.isFinite(w.samples) &&
    w.samples >= MIN_WINDOW_SAMPLES
  );
}

/** Compare two labelled RMS windows (e.g. "AstroDeck native" vs "PHD2") and
 *  return a verdict. `a`/`b` are just the two windows being compared — the
 *  argument order carries no preference; a LOWER rmsTotal is "better"
 *  (tighter guiding). Either window may be `null`/`undefined` (the provider
 *  hasn't guided yet this session) or too short to trust
 *  (`samples < MIN_WINDOW_SAMPLES`); either case reads "insufficient-data". */
export function compareRmsWindows(
  a: RmsWindow | null | undefined,
  b: RmsWindow | null | undefined,
): RmsCompareResult {
  const aOk = isUsable(a);
  const bOk = isUsable(b);
  // Labels are read BEFORE any narrowing is applied to `a`/`b` below — a plain
  // optional-chain read, independent of the isUsable() type predicate, so it
  // can't fall into the predicate's narrowed-to-`never` trap (isUsable is a
  // VALIDITY check, not a precise type split: an RmsWindow can be structurally
  // valid yet still fail isUsable on `samples`, so its negation legitimately
  // still carries a `.label` — but re-deriving that from `!isUsable(a)`
  // inline confuses TS's control-flow narrowing into `never`).
  const aLabel = a?.label ?? "the first provider";
  const bLabel = b?.label ?? "the second provider";
  if (!aOk || !bOk) {
    const missing = [aOk ? null : aLabel, bOk ? null : bLabel].filter(
      (x): x is string => x !== null,
    );
    return {
      verdict: "insufficient-data",
      message:
        `insufficient data — need at least ${MIN_WINDOW_SAMPLES} guided ` +
        `frames recorded under each provider this session (missing or too ` +
        `short: ${missing.join(", ")})`,
      deltaPct: null,
    };
  }

  const lo = Math.min(a.rmsTotal, b.rmsTotal);
  const hi = Math.max(a.rmsTotal, b.rmsTotal);

  // Both windows converged to (near-)zero RMS — nothing meaningful to divide
  // by; that's a tie, not a winner.
  if (lo <= 0) {
    return {
      verdict: "comparable",
      message: `${a.label} and ${b.label} are comparable (both ~0" RMS)`,
      deltaPct: 0,
    };
  }

  const deltaPct = ((hi - lo) / lo) * 100;
  if (deltaPct <= COMPARABLE_THRESHOLD_PCT) {
    return {
      verdict: "comparable",
      message:
        `${a.label} (${a.rmsTotal.toFixed(2)}") and ${b.label} ` +
        `(${b.rmsTotal.toFixed(2)}") are comparable (${deltaPct.toFixed(1)}% apart)`,
      deltaPct,
    };
  }

  const aWins = a.rmsTotal <= b.rmsTotal;
  const winner = aWins ? a : b;
  const loser = aWins ? b : a;
  return {
    verdict: aWins ? "a-better" : "b-better",
    message:
      `${winner.label} better by ${deltaPct.toFixed(1)}% ` +
      `(${winner.rmsTotal.toFixed(2)}" vs ${loser.label} ${loser.rmsTotal.toFixed(2)}")`,
    deltaPct,
  };
}
