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

export interface DeriveAfInputs {
  focuserMax: number | null; // foc.max when > 0, else null
  maxBin: number | null; // status.camera?.max_bin
  maxGain: number | null; // status.camera?.max_gain
  liveExposureS: number | null; // shown?.exposure_s
  liveGain: number | null; // shown?.gain
  liveStars: number | null; // shown?.stars
  liveHfr: number | null; // shown?.hfr
}
export interface DerivedAfParams {
  exposure_s: number;
  gain: number;
  step: number;
  steps_each_side: number;
  binning: number;
  basis: { exposure: string; step: string; binning: string };
}

const clampI = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, Math.round(v)));
const round1 = (v: number) => Math.round(v * 10) / 10;

export function deriveAutofocusParams(inp: DeriveAfInputs): DerivedAfParams {
  // exposure — reuse the live frame only if it is already showing stars
  const liveUsable =
    inp.liveExposureS != null && Number.isFinite(inp.liveExposureS) &&
    (inp.liveStars ?? 0) >= AF_STAR_FLOOR && inp.liveHfr != null;
  const rawExp = liveUsable ? (inp.liveExposureS as number) : AF_EXPOSURE_FALLBACK_S;
  const exposure_s = Math.min(AF_EXPOSURE_MAX_S, Math.max(AF_EXPOSURE_MIN_S, round1(rawExp)));
  const exposure = liveUsable
    ? `matched the live frame (${round1(rawExp)}s, ${inp.liveStars} stars)` +
      (exposure_s !== round1(rawExp) ? `, clamped to ${exposure_s}s` : "")
    : `default ${AF_EXPOSURE_FALLBACK_S}s (no measured stars in the live frame yet)`;

  // binning — 2× sweet spot, respect a bin-1-only sensor
  const cap = inp.maxBin != null && inp.maxBin >= 1 ? Math.floor(inp.maxBin) : 4;
  const binning = Math.min(2, cap);
  const binBasis = binning === 2
    ? "2× — brighter stars, fast download"
    : "1× (camera has no higher binning)";

  // gain — reuse live gain else default, clamp to sensor ceiling
  const rawGain = inp.liveGain != null && Number.isFinite(inp.liveGain)
    ? inp.liveGain : AF_DEFAULT_GAIN;
  const gain = inp.maxGain != null && inp.maxGain > 0
    ? clampI(rawGain, 0, inp.maxGain) : Math.max(0, Math.round(rawGain));

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
           basis: { exposure, step: stepBasis, binning: binBasis } };
}

// ---- (B) plain verdict ----
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
}): PlainVerdict {
  const level = autofocusLevel(args);
  switch (level) {
    case "excellent":
      return { level, tone: "good", headline: "Sharp!", detail: "Stars are tight — you're focused." };
    case "good":
      return { level, tone: "good", headline: "Focused.", detail: "Stars look good — you're ready to shoot." };
    case "soft":
      return {
        level, tone: "warn", headline: "Almost there.",
        detail: "Stars are still a little soft — tap Focus my scope to try again.",
      };
    case "failed":
      return {
        level, tone: "bad", headline: "Couldn't focus.",
        detail: "Not enough stars to lock onto — check the sky is clear and roughly focused, then try again.",
      };
    case "pending":
    default:
      return args.state === "running"
        ? { level: "pending", tone: "neutral", headline: "Focusing…", detail: "Measuring your stars…" }
        : { level: "pending", tone: "neutral", headline: "Not focused yet.", detail: "Tap Focus my scope to start." };
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
  return { disabled: false, label: "Focus my scope", reason: null, locked: false };
}
