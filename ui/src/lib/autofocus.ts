// lib/autofocus.ts — pure helpers for the persisted latest-autofocus-run
// record (F5: R2-FOC-01/DOC-FOC-01). No React, no DOM: npx-tsx testable
// (lib/weather.ts precedent).
//
// The `focus` WS event (types.ts FocusEvent — state/points/best, untouched)
// plus the native engine's additive `fit`/`message` fields (server
// focus/native.py _fit_payload) already flow through the store's `focus`
// slice on every tick, including the terminal done/failed tick — so the
// *raw* event data was never local-component-only. What the bus event does
// NOT carry is which provider ran the sweep or which filter was active (the
// server publishes neither), and it has no first-class timestamp of its own.
// normalizeAutofocusResult snapshots those two from the store's own live
// `status` at the exact moment a run reaches a terminal state, stamps the
// event's `ts`, and produces the canonical record the Focus view's Result
// panel renders — separate from the live `focus` slice (which keeps
// streaming intermediate "running" ticks for the in-progress V-curve chart)
// so a fresh run's early empty tick can never blank out the last completed
// run's evidence before its own terminal tick lands.

export interface AutofocusFit {
  method?: string | null;
  r2?: number | null;
  r2s?: Record<string, number | null>;
  curve?: [number, number][]; // [[position, hfr], …] sampled fitted polyline
  trendlines?: {
    left?: { slope: number; r2: number };
    right?: { slope: number; r2: number };
    intersection?: [number, number] | null;
  } | null;
}

export interface AutofocusPoint {
  position: number;
  hfr: number;
  sigma?: number;
}

export interface AutofocusProvider {
  kind: string;
  label: string;
}

export interface AutofocusResult {
  state: "done" | "failed";
  points: AutofocusPoint[];
  best: { position: number; hfr: number | null } | null;
  fit: AutofocusFit | null;
  message: string | null;
  /** The server's OWN guidance for this run, in its own words. Additive and
   *  optional: null means the provider could not say (an older server, or a
   *  backend/NINA result), NOT that everything was fine. The Result panel
   *  renders this instead of the client's per-enum sentence when present —
   *  the server knows the exposure, the per-point star counts and the frames
   *  themselves, and the client knows none of that. */
  advice: string | null;
  provider: AutofocusProvider | null;
  filter: string | null;
  ts: number; // epoch ms of the terminal event
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function normalizePoints(raw: unknown): AutofocusPoint[] {
  if (!Array.isArray(raw)) return [];
  const out: AutofocusPoint[] = [];
  for (const p of raw) {
    if (!p || typeof p !== "object") continue;
    const rec = p as Record<string, unknown>;
    const position = num(rec.position);
    const hfr = num(rec.hfr);
    if (position === null || hfr === null) continue;
    const sigma = num(rec.sigma);
    out.push(sigma === null ? { position, hfr } : { position, hfr, sigma });
  }
  return out;
}

function normalizeFit(raw: unknown): AutofocusFit | null {
  if (!raw || typeof raw !== "object") return null;
  const f = raw as Record<string, unknown>;
  const curve = Array.isArray(f.curve)
    ? (f.curve as unknown[]).filter(
        (p): p is [number, number] =>
          Array.isArray(p) && p.length === 2 && typeof p[0] === "number" && typeof p[1] === "number",
      )
    : undefined;
  return {
    method: typeof f.method === "string" ? f.method : null,
    r2: num(f.r2),
    r2s: f.r2s && typeof f.r2s === "object" ? (f.r2s as Record<string, number | null>) : undefined,
    curve,
    trendlines:
      f.trendlines && typeof f.trendlines === "object"
        ? (f.trendlines as AutofocusFit["trendlines"])
        : null,
  };
}

/** Build the canonical persisted record from a raw `focus` bus event's
 *  `data` payload — ONLY for a terminal state (done|failed); returns null
 *  for "running" (and any unrecognized state) so the store's case handler
 *  can call this unconditionally on every `focus` tick without an extra
 *  state check of its own. `ctx.provider`/`ctx.filter` are snapshots the
 *  caller reads from its own live `status` slice (the bus event carries
 *  neither); `ctx.tsMs` is the event's own timestamp (ws.ts's `ev.ts`,
 *  seconds, * 1000), not `Date.now()` — so a slow/backpressured bus frame
 *  still stamps the moment the server actually finished, not when the
 *  client happened to receive it. */
export function normalizeAutofocusResult(
  raw: unknown,
  ctx: { provider: AutofocusProvider | null; filter: string | null; tsMs: number },
): AutofocusResult | null {
  if (!raw || typeof raw !== "object") return null;
  const d = raw as Record<string, unknown>;
  const state = d.state;
  if (state !== "done" && state !== "failed") return null;

  const bestRaw = d.best;
  let best: { position: number; hfr: number | null } | null = null;
  if (bestRaw && typeof bestRaw === "object") {
    const position = num((bestRaw as Record<string, unknown>).position);
    if (position !== null) best = { position, hfr: num((bestRaw as Record<string, unknown>).hfr) };
  }

  return {
    state,
    points: normalizePoints(d.points),
    best,
    fit: normalizeFit(d.fit),
    message: typeof d.message === "string" ? d.message : null,
    // Blank strings are not advice: a provider that sends "" has told us
    // nothing, and rendering an empty sentence would hide the fallback that
    // does have something to say.
    advice: typeof d.advice === "string" && d.advice.trim() !== "" ? d.advice : null,
    provider: ctx.provider,
    filter: ctx.filter,
    ts: ctx.tsMs,
  };
}

/** The currently-selected filter name from `status.filterwheel`
 *  ({position, names}) — index-of-names, defensively bounds-checked. */
export function filterNameFromStatus(
  fw: { position: number; names: string[] } | null | undefined,
): string | null {
  if (!fw || !Array.isArray(fw.names)) return null;
  return fw.names[fw.position] ?? null;
}

/** Compact "how long ago" label for the Result panel's persisted-run line
 *  (mirrors lib/weather.ts's agoLabel phrasing but without the staleness
 *  framing — a persisted AF result never goes "stale," it's just old). */
export function afResultAgeLabel(tsMs: number, nowMs: number): string {
  const ageS = Math.max(0, (nowMs - tsMs) / 1000);
  if (ageS < 60) return "just now";
  const mins = Math.round(ageS / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = ageS / 3600;
  if (hrs < 24) return `${hrs.toFixed(1)}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

// ============================================================ AUTOFOCUS VERDICT
// The autofocus-RESULT verdict (implementation brief §3 / design-reference §02):
// "Focus — excellent · HFR 1.82 px · 2.4″ · R² 0.997 · hyperbolic" — verdict word
// first, raw numbers second (brief §0.1). Distinct from FocusVerdict, which
// judges the CURRENT live frame; this judges the completed sweep from the engine's
// best HFR + fit R² + state. Pure mapping is exported for unit testing.
export type AfLevel = "excellent" | "good" | "soft" | "failed" | "pending";

export function autofocusLevel(args: {
  state?: string;
  hfr?: number | null;
  r2?: number | null;
  hfrGood: number;
  hfrWarn: number;
}): AfLevel {
  const { state, hfr, r2, hfrGood, hfrWarn } = args;
  if (state === "failed") return "failed";
  if (state !== "done" || hfr == null) return "pending";
  // excellent needs a tight HFR AND a confident fit (R²≥0.98); when the provider
  // emits no R² (e.g. a backend/NINA result), judge on HFR alone.
  if (hfr <= hfrGood && (r2 == null || r2 >= 0.98)) return "excellent";
  if (hfr <= hfrWarn) return "good";
  return "soft";
}

// ============================================================ ONE-TAP FOCUS
// NOV-6: "Focus my scope" one-tap param derivation + plain verdict + hero-button
// gating copy (design spec 2026-07-23-one-tap-focus-design.md §1.3). No physics
// is invented: we derive only from the camera bin ceiling/max gain, the
// focuser's travel range, and the current live frame's already-measured stars.

// ---- (A) param derivation ----
export const AF_EXPOSURE_MIN_S = 1;
export const AF_EXPOSURE_MAX_S = 6;
export const AF_EXPOSURE_FALLBACK_S = 2;
export const AF_STAR_FLOOR = 5;
export const AF_DEFAULT_STEP = 350;
export const AF_STEPS_EACH_SIDE = 4;
export const AF_STEP_MIN = 20;
export const AF_STEP_MAX = 1500;
export const AF_SPAN_TARGET_FRAC = 0.12;
export const AF_SPAN_MIN_FRAC = 0.04;
export const AF_SPAN_MAX_FRAC = 0.30;
export const AF_DEFAULT_GAIN = 120;
/** Binning when there is no live frame to copy one from.
 *
 *  It used to be 2 ("brighter stars, fast download"), and that reasoning is
 *  wrong for THIS job. A sweep's only task is to keep finding measurable stars
 *  while it deliberately defocuses, and the one binning measurement this repo
 *  actually owns says bin 2 costs two thirds of them: focus/native.py records
 *  "24 stars at bin 1 became 8 at bin 2" from a real rig. Download time is not
 *  the constraint on a nine-frame sweep. So the blind guess is the setting that
 *  finds the most stars, and a real frame's binning is copied over it. */
export const AF_FALLBACK_BIN = 1;

/** Where the sweep's exposure/gain/binning actually came from.
 *
 *  This exists because on 2026-07-31 there was no way to tell. The Focus screen
 *  could not start the camera, so its live preview read "No capture yet" all
 *  night, so every derivation below fell through to the office defaults — 2s,
 *  gain 120, bin 2 — and said nothing about it. At those settings the field
 *  measured 8 stars; at 4s / gain 220 / bin 1 the same field measures 2100.
 *  Two four-minute sweeps died on that and the screen looked identical to a
 *  sweep that had copied a real frame.
 *
 *   - "measured": a live frame supplied the numbers and it had stars to measure.
 *   - "sparse":   a live frame exists but is too star-poor to copy from, so the
 *                 defaults are in force. Doubtful, not hopeless (see below).
 *   - "no-frame": no frame has been taken at all. Nothing was measured; every
 *                 number is a guess.
 */
export type AfParamSource = "measured" | "sparse" | "no-frame";

export interface DeriveAfInputs {
  focuserMax: number | null; // foc.max when > 0, else null
  maxBin: number | null; // status.camera?.max_bin
  maxGain: number | null; // status.camera?.max_gain
  liveExposureS: number | null; // shown?.exposure_s
  liveGain: number | null; // shown?.gain
  /** The live frame's binning. NOT optional, deliberately: the first cut of
   *  this file computed binning from the camera's ceiling alone while three
   *  user-facing sentences said it was copied from the frame, so a user who
   *  shot at bin 1 was told bin 1 was copied and swept at bin 2. A required
   *  field forces every caller to answer rather than silently take the
   *  fallback. PreviewInfo.binning is a required field, so a caller holding a
   *  frame always has it; null means there is no frame. */
  liveBinning: number | null; // shown?.binning
  liveStars: number | null; // shown?.stars
  liveHfr: number | null; // shown?.hfr
  /** Whether a live frame exists AT ALL, independent of what it measured.
   *  Optional: PreviewInfo.exposure_s is a required field, so a non-null
   *  liveExposureS already implies a frame — but a caller holding the preview
   *  object should say so outright rather than have this infer it. */
  hasLiveFrame?: boolean;
}
export interface DerivedAfParams {
  exposure_s: number;
  gain: number;
  step: number;
  steps_each_side: number;
  binning: number;
  /** Provenance of exposure/gain/binning — see AfParamSource. Callers must
   *  surface this: a guessed sweep that looks exactly like a measured one is
   *  the bug this whole field was added for. */
  source: AfParamSource;
  basis: { exposure: string; gain: string; step: string; binning: string };
}

const clampI = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, Math.round(v)));
const round1 = (v: number) => Math.round(v * 10) / 10;

export function deriveAutofocusParams(inp: DeriveAfInputs): DerivedAfParams {
  // exposure — reuse the live frame only if it is already showing stars
  const liveUsable =
    inp.liveExposureS != null && Number.isFinite(inp.liveExposureS) &&
    (inp.liveStars ?? 0) >= AF_STAR_FLOOR && inp.liveHfr != null;
  const hasFrame = inp.hasLiveFrame ?? inp.liveExposureS != null;
  const source: AfParamSource = liveUsable ? "measured" : hasFrame ? "sparse" : "no-frame";
  const rawExp = liveUsable ? (inp.liveExposureS as number) : AF_EXPOSURE_FALLBACK_S;
  const exposure_s = Math.min(AF_EXPOSURE_MAX_S, Math.max(AF_EXPOSURE_MIN_S, round1(rawExp)));
  // Name the SOURCE, not just the value. "default 2s" reads like a decision;
  // "guessed — nothing has been measured" reads like what it is.
  const exposure = liveUsable
    ? `matched the live frame (${round1(rawExp)}s, ${inp.liveStars} stars)` +
      (exposure_s !== round1(rawExp) ? `, clamped to ${exposure_s}s` : "")
    : hasFrame
      ? `${AF_EXPOSURE_FALLBACK_S}s default — the live frame measured only ${inp.liveStars ?? 0} stars, too few to copy from`
      : `${AF_EXPOSURE_FALLBACK_S}s guess — no frame has been taken, so there was nothing to copy`;

  // binning — COPIED from the live frame, clamped to the sensor's ceiling.
  // Like gain (below) this is a setting the user chose rather than something
  // the frame measured, so it survives a star-poor frame. It was previously
  // derived from the camera's bin ceiling alone — min(2, cap) — while the
  // Camera panel defaulted to bin 1 and three sentences on this screen claimed
  // the frame's binning had been copied. A user could shoot 4s / gain 220 /
  // bin 1, be told all three were copied, and get a bin-2 sweep: the exact
  // setting focus/native.py measured turning 24 stars into 8.
  const cap = inp.maxBin != null && inp.maxBin >= 1 ? Math.floor(inp.maxBin) : 4;
  const liveBin =
    inp.liveBinning != null && Number.isFinite(inp.liveBinning) && inp.liveBinning >= 1
      ? Math.floor(inp.liveBinning) : null;
  const binning = Math.min(liveBin ?? AF_FALLBACK_BIN, cap);
  // No "camera bins no higher" case to write: the fallback is 1× and every
  // sensor can do 1×, so with no frame the guess is never clamped. It is still
  // a guess and says so — the whole point of the source/basis split.
  const binBasis = liveBin != null
    ? `copied from the live frame (bin ${binning})`
      + (binning !== liveBin ? ` — clamped to this camera's ${cap}× ceiling` : "")
    : `bin ${binning} guess — no frame has been taken; bin ${AF_FALLBACK_BIN} is the `
      + "setting that leaves the most stars to measure as the sweep defocuses";

  // gain — reuse live gain else default, clamp to sensor ceiling.
  // The live frame's gain is copied whenever a frame exists, even a star-poor
  // one: gain is a setting the user chose, not a measurement, so it survives a
  // frame whose star count does not. With NO frame it is pure invention, and
  // gain 120 against the 220 this rig needs is the number that cost the night.
  const rawGain = inp.liveGain != null && Number.isFinite(inp.liveGain)
    ? inp.liveGain : AF_DEFAULT_GAIN;
  const gain = inp.maxGain != null && inp.maxGain > 0
    ? clampI(rawGain, 0, inp.maxGain) : Math.max(0, Math.round(rawGain));
  const gainBasis = inp.liveGain != null && Number.isFinite(inp.liveGain)
    ? `copied from the live frame (gain ${gain})`
    : `gain ${gain} guess — no frame has been taken, so nothing has shown this exposes stars`;

  // step — keep proven default; rescale only at focuser-range extremes
  let step = AF_DEFAULT_STEP;
  let stepBasis = `default ${AF_DEFAULT_STEP} steps`;
  const fm = inp.focuserMax;
  if (fm != null && fm > 0) {
    const frac = (AF_DEFAULT_STEP * AF_STEPS_EACH_SIDE * 2) / fm;
    if (frac > AF_SPAN_MAX_FRAC || frac < AF_SPAN_MIN_FRAC) {
      step = clampI((fm * AF_SPAN_TARGET_FRAC) / (AF_STEPS_EACH_SIDE * 2), AF_STEP_MIN, AF_STEP_MAX);
      stepBasis = `${step} steps — sized to sweep ~${Math.round(AF_SPAN_TARGET_FRAC * 100)}% of focuser travel`;
    } else {
      stepBasis = `default ${AF_DEFAULT_STEP} steps (~${Math.round(frac * 100)}% of travel)`;
    }
  }
  return { exposure_s, gain, step, steps_each_side: AF_STEPS_EACH_SIDE, binning,
           source,
           basis: { exposure, gain: gainBasis, step: stepBasis, binning: binBasis } };
}

// ---- (B) plain verdict ----

// ---------------------------------------------------- why the sweep failed
// The engine reports failures as machine tokens (native/crates/astrodeck-native/
// src/lib.rs `fail_reason_label`), and the Result panel printed the token
// VERBATIM directly under a sentence that contradicted it: "Not enough stars to
// lock onto — check the sky is clear" above "r_squared_below_threshold", which
// is a fit-quality failure that happens perfectly well with two thousand stars.
// The user followed the sentence, not the token, and spent the night chasing
// star count while the actual complaint was the shape of the curve.
//
// So: recognise the token, and say the SAME thing the token says. This is the
// fallback for a provider that sends no `advice` — when the server sends its
// own (it knows the exposure, the per-point counts and the frames), that wins.
const FAIL_REASONS: { code: string; explain: string }[] = [
  {
    code: "not_enough_spread",
    explain: "HFR barely changed from one end of the sweep to the other, so there "
      + "was no V to find the bottom of. Increase the step size so each point is "
      + "measurably more defocused than the last.",
  },
  {
    code: "r_squared_below_threshold",
    explain: "The measured points did not lie on a focus curve, so the fit was too "
      + "poor to trust — this is about the SHAPE of the sweep, not the number of "
      + "stars. Usual causes: focuser backlash, a step size too small to move HFR, "
      + "or cloud/seeing changing between points.",
  },
  {
    code: "out_of_bounds",
    explain: "The curve's minimum landed outside the range actually swept, so the "
      + "answer would have been an extrapolation. Get closer to focus first, or "
      + "sweep a wider range.",
  },
  {
    code: "hfr_worse_than_start",
    explain: "The position it computed measured WORSE than where it began, so it "
      + "went back rather than leave you defocused. The curve was probably fitted "
      + "to noise: try a longer exposure or a larger step.",
  },
  {
    code: "fit_unavailable",
    explain: "Too few measurable points to fit a curve at all — most frames in the "
      + "sweep had no stars the detector could use. Longer exposure, bin 1, or a "
      + "richer field.",
  },
];

export interface FocusFailure {
  /** The engine's own token, when we recognise one. Kept so the technical chip
   *  can still show it — a breadcrumb for a bug report — now that the sentence
   *  above it explains that exact token instead of arguing with it. */
  code: string | null;
  /** The token said in words, or null when the message is not one we know
   *  (a server-composed sentence, or a NINA/backend string). */
  explain: string | null;
}

/** Read a terminal `focus` event's `message` as a failure code + explanation. */
export function readFocusFailure(message: string | null | undefined): FocusFailure {
  const raw = (message ?? "").trim().toLowerCase();
  if (!raw) return { code: null, explain: null };
  // Exact first, then substring: the engine publishes the bare token, but a
  // wrapper that decorates it ("native engine: out_of_bounds") must not lose
  // the explanation over a prefix.
  const hit = FAIL_REASONS.find((r) => r.code === raw)
    ?? FAIL_REASONS.find((r) => raw.includes(r.code));
  return hit ? { code: hit.code, explain: hit.explain } : { code: null, explain: null };
}

export type FocusTone = "good" | "warn" | "bad" | "neutral";
export interface PlainVerdict {
  level: AfLevel;
  tone: FocusTone;
  headline: string;
  detail: string;
}

export function plainFocusVerdict(args: {
  state?: string;
  hfr?: number | null;
  r2?: number | null;
  hfrGood: number;
  hfrWarn: number;
  /** The terminal event's raw message (engine token or server sentence). */
  message?: string | null;
  /** The server's own guidance, when it had any. Outranks everything below:
   *  it is the only party that saw the frames. */
  advice?: string | null;
}): PlainVerdict {
  const level = autofocusLevel(args);
  switch (level) {
    // A SUCCESS is where the server's advice matters most, and it was being
    // thrown away here. focus/native.py calls _advice(ok=True) precisely to
    // stop "letting a confident R² stand on four five-star samples", and the
    // sentence it produces — "N of M points came from fewer than X stars and
    // carry little weight in the fit, so this is thinner evidence than the R²
    // suggests" — only ever fires on a run that fitted well enough to reach
    // `excellent` (R² ≥ 0.98). Printing "Stars are tight" over it deleted the
    // one warning the server took the trouble to compute. The headline and
    // tone stay: the HFR and R² beside them are genuinely good and the caveat
    // is about the evidence behind them, not the focus position itself.
    case "excellent":
      return {
        level, tone: "good", headline: "Sharp!",
        detail: args.advice ?? "Stars are tight — you're focused.",
      };
    case "good":
      return {
        level, tone: "good", headline: "Focused.",
        detail: args.advice ?? "Stars look good — you're ready to shoot.",
      };
    case "soft":
      return {
        level, tone: "warn", headline: "Almost there.",
        // A run can succeed and still be worth explaining ("the minimum sat at
        // the edge of the range swept"), so advice outranks the canned line
        // here too — a soft SUCCESS is the case where the server's extra
        // knowledge most often changes what the user should do next.
        detail: args.advice
          ?? "Stars are still a little soft — tap Focus my scope to try again.",
      };
    case "failed":
      return {
        level, tone: "bad", headline: "Couldn't focus.",
        // Order of authority: the server's advice (it saw the frames), then the
        // engine token said in words, then — only when nobody could tell us
        // anything — a sentence that does NOT invent a cause. The generic used
        // to read "Not enough stars to lock onto", which sent the user after a
        // star count on a run that failed on curve shape.
        detail: args.advice
          ?? readFocusFailure(args.message).explain
          ?? (args.message
            ? `The sweep stopped without a focus position: ${args.message}. `
              + "Check the focus log for what each point measured, then try again."
            : "The sweep stopped without a focus position, and reported no reason. "
              + "Check the focus log for what each point measured, then try again."),
      };
    case "pending":
    default:
      // A run that finished with no HFR at all still lands here, and the server
      // may well have said why. "Tap Focus my scope to start" under a run that
      // just ran is the same throw-away this whole branch chain now avoids; with
      // no advice (and before any run) the canned line is all there is.
      return args.state === "running"
        ? { level: "pending", tone: "neutral", headline: "Focusing…", detail: "Measuring your stars…" }
        : {
            level: "pending", tone: "neutral", headline: "Not focused yet.",
            detail: args.advice ?? "Tap Focus my scope to start.",
          };
  }
}

// ---- (C) hero-button gating copy ----
export interface FocusButtonState {
  disabled: boolean;
  label: string;
  reason: string | null;
  locked: boolean;
}

export function focusButtonState(a: {
  canFocus: boolean;
  hasFocuser: boolean;
  running: boolean;
  /** Why the SWEEP cannot honestly start even though the focuser is ready —
   *  today: nothing has been measured for it to copy (see lib/focusCapture.ts
   *  sweepReadiness). Optional so the gate's older three-condition callers
   *  compile unchanged. Ranked last: a permission or hardware problem is a
   *  bigger fact about the rig than an unmeasured parameter. */
  sweepBlock?: string | null;
}): FocusButtonState {
  if (!a.canFocus) {
    return { disabled: true, label: "Focus my scope", reason: "Read-only — focusing needs operator access", locked: true };
  }
  if (!a.hasFocuser) {
    return { disabled: true, label: "Focus my scope", reason: "Connect a focuser to enable one-tap focus", locked: false };
  }
  if (a.running) {
    return { disabled: true, label: "Focusing…", reason: "Autofocus is running", locked: false };
  }
  if (a.sweepBlock) {
    return { disabled: true, label: "Focus my scope", reason: a.sweepBlock, locked: false };
  }
  return { disabled: false, label: "Focus my scope", reason: null, locked: false };
}
