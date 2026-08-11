import { useCallback, useEffect, useRef, useState } from "react";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { ReactNode } from "react";
import type {
  AppConfig,
  AuthMethods,
  BackendLink,
  CatalogEntry,
  FocusEvent,
  FrameScope,
  FrameSettings,
  FramingSession,
  GuideStats,
  LogLine,
  NinaHealth,
  OverlayToggles,
  PolarState,
  PreviewField,
  PreviewInfo,
  Principal,
  RigStatus,
  SafetyReading,
  SafetyState,
  Schedule,
  SequencePlan,
  SequenceState,
  SiteInfo,
  StretchParams,
  Target,
  Toast,
  ToastLevel,
  TouchSettings,
  TroubleshootTopic,
  UpdateStatus,
  ViewName,
  Viewport,
  WeatherState,
  WsPhase,
} from "./types";
import { accumulateLight, type LightSnapshot } from "./lib/calibration";
import type { MasterRow } from "./lib/calibrationLibrary";
// #117: the sign-in gate's three rules — nothing SPEAKS while a gate screen is
// up, the store DROPS the rig when the gate engages, and it stops TAKING THE RIG
// IN while the gate stays up — live in ONE pure module. This file holds all
// three choke points: announcementsBlocked in enqueueToast / pushConfirm /
// announce(), intakeBlocked at the top of handleEvent, gateEngaged in
// setAuthGate. EMPTY_SEQUENCE/EMPTY_POLAR/EMPTY_NINA_HEALTH come from there too,
// so "cleared" and "cold boot" are the same state by construction.
import {
  announcementsBlocked,
  clearedRigState,
  gateEngaged,
  intakeBlocked,
  EMPTY_FRAME_SETTINGS,
  EMPTY_NINA_HEALTH,
  EMPTY_POLAR,
  EMPTY_SEQUENCE,
  type AuthGate,
} from "./lib/authGate";
import { parseSeen, serializeSeen, withSeen, COACH_SEEN_KEY, WIZARD_SEEN_KEY, type SeenMap } from "./lib/coach";
import type { WizardStepId } from "./lib/firstRunWizard";
import { deriveNinaHealth } from "./lib/health";
import { normalizeSafety } from "./lib/safety";
import { normalizeWeather } from "./lib/weather";
import { normalizeAutofocusResult, filterNameFromStatus, type AutofocusResult } from "./lib/autofocus";
import { humanizeLog } from "./lib/humanize";
import { diagnoseFailure } from "./lib/troubleshoot";
import { tagGuideRms, type GuideRmsByKind } from "./lib/guideRms";
import { notifyAndBeep, requestNotifyPermission } from "./lib/notify";
import { haptics } from "./lib/haptics";
import { ensurePlanIds } from "./lib/ids";
import { isExposureValueInvalid } from "./lib/exposure";
import { api, ApiError } from "./api";
import { getMe, getAuthMethods } from "./api/backends";

// Re-export ViewName from its canonical home (types.ts) so existing imports
// `import type { ViewName } from "./store"` keep working.
export type { ViewName } from "./types";

// ---------------------------------------------------------------- providers
// Per-capability provider resolution surfaced by poll_status (native parity).
// Shape mirrors server/astrodeck/providers.py resolve_all(): each capability
// resolves to {kind,label,reason}. `kind` answers WHO actually ran it —
// "astrodeck" (our native/sim engine), "backend" (the connected backend, e.g.
// NINA/Alpaca), "astap" (the local ASTAP binary), "sim" (the built-in
// simulator), or "unavailable" (nothing can run it; `reason` names what's
// missing). Lives as a store view-model type because `providers` is attached
// to poll_status ADDITIVELY (hub.poll_status) and is not declared on
// RigStatus — useProviders reads it via a cast so this slice owns the shape
// without editing types.ts.
export type ResolvedProviderKind =
  | "astrodeck"
  | "backend"
  | "astap"
  | "sim"
  | "unavailable";
export interface ProviderChoiceView {
  kind: ResolvedProviderKind;
  label: string;
  reason: string;
  // Only the `guide` row carries this (P5-T1 fix round I1): the override VALUES
  // actually selectable on the connected rig, so the Guide view offers only
  // what applies (hub.poll_status → providers.guide_eligible_providers).
  eligible?: string[];
  // The same answer with the BLOCKED values KEPT and a reason attached
  // (hub.poll_status → providers.guide_provider_options). `eligible` alone
  // forced the client to drop an unavailable provider silently, and a missing
  // row reads as "this product cannot guide" rather than "assign a guide
  // camera" — which is the conclusion a real user reached. Optional: an older
  // server sends only `eligible`, so every consumer must degrade to that.
  options?: { value: string; eligible: boolean; reason: string | null }[];
}
export interface ProvidersStatus {
  autofocus?: ProviderChoiceView;
  polar_align?: ProviderChoiceView;
  solve?: ProviderChoiceView;
  guide?: ProviderChoiceView;
}

// ---------------------------------------------------------------- toast policy
const TOAST_MAX = 3; // hard cap; on phone effectively 1-2
// Minimum coalescing window for an identical generic toast. Sticky toasts (ttl
// 0) coalesce for as long as they are on screen, which is forever — see
// enqueueToast (UX review #33).
const TOAST_DEDUPE_MS = 5000;
const TTL: Record<ToastLevel, number> = {
  success: 3000,
  info: 4000,
  warning: 6000,
  error: 10000,
};

export type EnqueueInput = {
  level: ToastLevel;
  title: string;
  detail?: string;
  kind?: Toast["kind"];
  ttl?: number;
  action?: Toast["action"];
  source?: string;
};

// --------------------------------------------------------------- confirm host
export interface ConfirmRequest {
  title: string;
  body?: ReactNode; // ConfirmHost renders {req.body} as-is — rich bodies survive (F-D1)
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "warn" | "danger";
  mode?: "ok" | "confirm" | "hold";
  // PLAN-01-gemini: the default confirm-mode chrome reads as Cancel-is-primary
  // (solid text on .btn vs. an outline .btn-accent) — fine for a destructive
  // "are you sure", backwards for a "proceed anyway" advisory where completing
  // the action IS the safe/intended outcome. Opt-in per call site so this
  // doesn't reshuffle every confirm dialog in the app.
  confirmPrimary?: boolean;
  resolve: (ok: boolean) => void;
}

// ------------------------------------------------------------ default plan/state
// EMPTY_SEQUENCE / EMPTY_POLAR / EMPTY_NINA_HEALTH now live in lib/authGate.ts
// (imported above): they are both the cold-boot value and the value the auth
// gate clears back to, and two copies of that would drift.

// ------------------------------------------------ frame settings, by PURPOSE
// One home per (camera, purpose) — not per screen. Mirrors
// server/astrodeck/config.py FrameSettings / FRAME_SCOPES.
//
// The night this closes (2026-08-08): FILT=R on Align, Oiii on Capture. Every
// setting behind those screens was a private useState seeded from a constant,
// so no two surfaces could agree and a reload recovered none of them.
//
// The scopes stay SEPARATE on purpose — a guide camera's exposure is not the
// imaging camera's and a 0.3 s solve frame is not a light frame. What was
// missing was a name for which is which.
/** Re-exported so a component reads one module. The canonical declarations are
 *  in types.ts (shared with lib/authGate, which owns the cleared value). */
export type { FrameScope, FrameSettings };

/** Normalize a server payload into the full record, ignoring unknown scopes and
 *  keeping any scope the server did not mention. */
function mergeFrames(
  current: Record<FrameScope, FrameSettings>,
  incoming: unknown,
): Record<FrameScope, FrameSettings> {
  if (!incoming || typeof incoming !== "object") return current;
  const next = { ...current };
  for (const scope of Object.keys(current) as FrameScope[]) {
    const raw = (incoming as Record<string, unknown>)[scope];
    if (!raw || typeof raw !== "object") continue;
    next[scope] = { ...current[scope], ...(raw as Partial<FrameSettings>) };
  }
  return next;
}

const PLAN_KEY = "astrodeck-plan";

// Mirrors server/astrodeck/sequence/models.py SequencePlan defaults exactly —
// the full plan is POSTed to /api/sequence/start and overrides the backend's
// pydantic defaults, so a divergence here silently degrades every UI-started
// run (no dithering, no filter-offset refocus, no guide-loss recovery).
function defaultPlan(): SequencePlan {
  return {
    name: "Tonight",
    targets: [],
    guide: true,
    dither_every: 3,
    dither_pixels: 3,
    autofocus_every: 0,
    cool_to: null,
    cool_timeout_s: 600,
    apply_filter_offsets: true,
    refocus_on_temp_delta_c: 0,
    meridian_flip: true,
    recover_guiding: true,
    hfr_reject_factor: 0,
    // unattended safety (Batch-4b) — MUST mirror models.SequencePlan defaults
    // (safety_check=True, meridian_flip_warn_min=15.0) or a UI-started run silently
    // drops the SafetyMonitor gate / mis-times the meridian-flip warning.
    safety_check: true,
    meridian_flip_warn_min: 15,
    park_when_done: false,
    warm_cooler_when_done: false,
    // multi-night quota (sessions spec §3) — MUST mirror models.SequencePlan
    // defaults or a UI-started run silently changes quota/guard behavior, with
    // ONE deliberate divergence:
    //
    // count_mode (UX REVIEW #30). The server's pydantic default is "attempts"
    // (models.py:187) and this line used to mirror it — which is also why the
    // UI's `?? "attempts"` fallbacks never fired: the field is always present.
    // "attempts" means a REJECTED frame still consumes its count, so the moment
    // any quality gate is armed (min_stars / max_guide_rms / max_eccentricity /
    // hfr_reject_factor — see stepDefaults.armedQualityGates) "20 × 300s" quietly
    // becomes "at most 20 × 300s", and nothing reports the shortfall.
    //
    // "accepted" is the safe default because with every gate at 0 (which is the
    // novice default, right below) NO frame can ever be rejected, so the two
    // modes are behaviourally identical — engine._run_step's quota branch and
    // its attempts branch both terminate after `count` accepted frames. The
    // difference only appears once the user arms a gate, and at that moment the
    // count means what they asked for instead of silently shrinking.
    //
    // Safe against engine unboundedness: models.quota_unbounded() only refuses a
    // start when accepted-mode runs with BOTH reject guards at 0 and no stop
    // boundary — and the two guards below default to 10/20, so the default plan
    // is bounded. (Zeroing both is still possible; the server refuses that start
    // with a message naming the four ways out, rather than looping forever.)
    // The plan is POSTed whole to /api/sequence/start, so this value — not the
    // pydantic default — is what every UI-started run uses.
    count_mode: "accepted",
    min_stars: 0,
    max_guide_rms: 0,
    max_consecutive_rejects: 10,
    max_consecutive_rejects_night: 20,
    // conditional sequencer (PRO-3) — [] => byte-identical run. The loadPlan()
    // spread `{ ...defaultPlan(), ...parsed }` backfills [] onto legacy plans.
    instructions: [],
  };
}

function loadPlan(): SequencePlan {
  try {
    const raw = localStorage.getItem(PLAN_KEY);
    if (raw) {
      const parsed = { ...defaultPlan(), ...(JSON.parse(raw) as Partial<SequencePlan>) };
      // Backfill `schedule` (C1-27) AND stable ids (sessions spec §1) onto
      // legacy plans. Default spreads FIRST so a PRESENT schedule wins.
      return ensurePlanIds({ ...parsed, targets: parsed.targets.map((t) => ({ schedule: defaultSchedule(), ...t })) });
    }
  } catch {
    /* fall through to default */
  }
  return defaultPlan();
}

// ----------------------------------------------------------- automation defaults
// The per-target autorun schedule baseline (Batch-4b §2.4). Exported so the
// frontend workflow's SequenceView can backfill `schedule` onto every target in
// loadPlan()/addTarget() (resolves C1-27: a plan saved before 4b has no schedule,
// and a missing schedule would crash the schedule sub-panel). The shape mirrors
// backend sequence/models.Schedule's defaults exactly — "now" start, no gates, no
// stop, wait-on-missed — so a backfilled target preserves today's run behavior.
export function defaultSchedule(): Schedule {
  return {
    start_mode: "now",
    start_offset_min: 0,
    start_time: null,
    min_altitude_deg: 0,
    min_moon_sep_deg: 0,
    max_moon_illum_pct: 0,
    max_hour_angle_h: 0,
    stop_mode: "none",
    stop_offset_min: 0,
    stop_time: null,
    max_run_min: 0,
    on_missed: "wait",
  };
}

// ----------------------------------------------------------------- atlas/framing
// The framing session's initial survey crop width. Seed it from the persisted
// optics' computed FOV (the camera's own field, padded ~1.6×) so the camera
// rectangle lands at a sensible size; fall back to ~1.5° when optics are unset.
const FRAMING_DEFAULT_FOV_DEG = 1.5;
const FRAMING_DEFAULT_SURVEY = "CDS/P/DSS2/color";
// Night seeds the monochrome red survey (spec §8): full-color DSS2 is a
// dark-adaptation killer, so a fresh night session opens on the red HiPS.
const FRAMING_NIGHT_SURVEY = "CDS/P/DSS2/red";

function seedFovZoomDeg(config: AppConfig | null): number {
  const oc = config?.optics_computed;
  if (oc && oc.have_optics) {
    const diag = oc.fov_diag_deg ?? null;
    const w = oc.fov_w_deg ?? null;
    const base = diag ?? w ?? null;
    if (base && base > 0) return Math.min(10, Math.max(0.1, base * 1.6));
  }
  return FRAMING_DEFAULT_FOV_DEG;
}

// ------------------------------------------------------------------ site mirror
// `site` (the live/status view) and `config.site` (the persisted record) carry
// the same six fields from the same stored truth. loadConfig() keeps them in
// lock-step; this compares them field-by-field so an unchanged site keeps its
// object identity and useSite() consumers don't re-render on every unrelated
// config bump. Field-wise, not JSON.stringify: absent-vs-undefined coordinates
// (stripped for principals without view.site_precise) must compare equal.
function siteEquals(a: SiteInfo | null, b: SiteInfo | null | undefined): boolean {
  if (!a || !b) return a == null && b == null;
  return (
    a.name === b.name &&
    a.latitude === b.latitude &&
    a.longitude === b.longitude &&
    a.elevation_m === b.elevation_m &&
    a.is_default === b.is_default &&
    a.horizon_min_deg === b.horizon_min_deg
  );
}

// ----------------------------------------------------------- live-preview state
// Ring buffer + view state for the live-preview overhaul (live-preview spec §4.2).
// Persistence (`astrodeck-preview` key): ONLY `overlays`, `stretch.auto`,
// `stretch.advancedOpen` survive a reload. Absolute B/M/W/brightness/contrast,
// the viewport, and the pinned id are NEVER persisted (master §A.2) — manual
// stretch resets per session/target so a stale level never lies about a frame.
const PREVIEW_KEY = "astrodeck-preview";
const PREVIEW_CAP = 24; // client metadata ring; server keeps thumbs for many

function defaultViewport(): Viewport {
  return { scale: 1, x: 0, y: 0, fit: true };
}

function defaultStretch(): StretchParams {
  return {
    auto: true, // sticky, on by default (honest auto-stretch)
    black: 0,
    mid: 0.5,
    white: 1,
    brightness: 0,
    contrast: 0,
    advancedOpen: false,
  };
}

function defaultOverlays(): OverlayToggles {
  return {
    stars: false,
    clip: false,
    reticle: false,
    centerMark: true, // subtle framing aid on by default
    tilt: false, // PRO-13 tilt/aberration heatmap
    // #182: catalogued objects marked on the frame. On by default — it only ever
    // draws when the server has published objects placed on this exact frame, so
    // "on" is free until there is something true to show.
    objects: true,
    // ON by default: the Bahtinov spike overlay only draws while the aid is armed
    // and the fit is valid, so the novice gets the visual for free; this flag is
    // the expert's opt-out (polish grab-bag Decision B).
    bahtinov: true,
  };
}

interface PersistedPreview {
  overlays?: Partial<OverlayToggles>;
  auto?: boolean;
  advancedOpen?: boolean;
}

function loadPreviewPersisted(): { overlays: OverlayToggles; stretch: StretchParams } {
  const overlays = defaultOverlays();
  const stretch = defaultStretch();
  try {
    const raw = localStorage.getItem(PREVIEW_KEY);
    if (raw) {
      const p = JSON.parse(raw) as PersistedPreview;
      if (p.overlays) Object.assign(overlays, p.overlays);
      if (typeof p.auto === "boolean") stretch.auto = p.auto;
      if (typeof p.advancedOpen === "boolean") stretch.advancedOpen = p.advancedOpen;
    }
  } catch {
    /* defaults */
  }
  return { overlays, stretch };
}

// Remember the last-written serialized subset so a B/M/W/brightness drag (which
// fires setStretch dozens/sec but never touches the persisted {auto,advancedOpen,
// overlays}) doesn't hammer localStorage.setItem on every move (resolves P2-8).
let lastPersistedPreview: string | null = null;

function persistPreview(overlays: OverlayToggles, stretch: StretchParams): void {
  const payload: PersistedPreview = {
    overlays,
    auto: stretch.auto,
    advancedOpen: stretch.advancedOpen,
  };
  const serialized = JSON.stringify(payload);
  // No-op when the persisted subset is unchanged vs the last write — skips the
  // per-drag setItem entirely (the absolute levels in the drag never persist).
  if (serialized === lastPersistedPreview) return;
  try {
    localStorage.setItem(PREVIEW_KEY, serialized);
    lastPersistedPreview = serialized;
  } catch {
    /* quota / unavailable — keep in-memory */
  }
}

// ----------------------------------------------------------------- monitor state
const AUTO_MONITOR_KEY = "astrodeck-monitor-auto";
type RunBanner = { active: boolean; plan_name?: string; percent?: number } | null;
// states the engine sits in when NOT actively running (rising-edge detection).
const RUN_RISING_FROM = new Set(["idle", "complete", "aborted", "error", "nina_native"]);

// ------------------------------------------------------------------- touch state
// Touch-ergonomics slice (touch spec §2.2). localStorage keys per master §A.2:
//   astrodeck-haptics / -touch-size / -rev-ra / -rev-dec / -autolock.
// `locked` is NEVER persisted (a lock must not survive reload). `lockAvailable`
// is TRUE from init: its unblock precondition (the reliability sequence-error
// render — TouchGuard reads sequence.state==='error', StatusChip renders it)
// shipped in Batch 1, so the screen-lock feature is enabled (gate, R11).
const HAPTICS_KEY = "astrodeck-haptics";
const TOUCH_SIZE_KEY = "astrodeck-touch-size";
const REV_RA_KEY = "astrodeck-rev-ra";
const REV_DEC_KEY = "astrodeck-rev-dec";
const AUTOLOCK_KEY = "astrodeck-autolock";

function loadTouch(): TouchSettings {
  let hapticsEnabled = true;
  let touchSizing: TouchSettings["touchSizing"] = "auto";
  let reverseRa = false;
  let reverseDec = false;
  let autoLockMs: TouchSettings["autoLockMs"] = null;
  try {
    hapticsEnabled = localStorage.getItem(HAPTICS_KEY) !== "0";
    const sz = localStorage.getItem(TOUCH_SIZE_KEY);
    if (sz === "auto" || sz === "on" || sz === "off") touchSizing = sz;
    reverseRa = localStorage.getItem(REV_RA_KEY) === "1";
    reverseDec = localStorage.getItem(REV_DEC_KEY) === "1";
    const al = Number(localStorage.getItem(AUTOLOCK_KEY));
    autoLockMs = al === 180000 || al === 300000 ? al : null;
  } catch {
    /* unavailable — keep safe defaults */
  }
  return { hapticsEnabled, touchSizing, reverseRa, reverseDec, autoLockMs };
}

/** Reflect touchSizing onto the <html> class so the coarse-pointer size bumps can
 *  be forced on/off regardless of the inferred pointer (touch §3, R17). */
function applyTouchSizing(sizing: TouchSettings["touchSizing"]): void {
  const c = document.documentElement.classList;
  c.toggle("touch-ui", sizing === "on");
  c.toggle("no-touch-ui", sizing === "off");
}

// ------------------------------------------------------------ photometry profile
// Small persisted client "photometry profile" (photometry/SNR design §1.3): egain
// (e-/ADU), read noise (e-), and a bias-frame ADU pedestal — entered once by the
// user (read-noise harness or camera datasheet), never on the wire today (no
// config field/status payload carries them; see the design doc's seam survey).
// All-zero is the inert default: consumers (NOV-4 Suggest, PRO-6 estimators) show
// an honest "add gain + read noise" prompt rather than a wrong number.
export interface PhotometryProfile {
  egain: number;
  readNoiseE: number;
  biasAdu: number;
}

const PHOTOMETRY_KEY = "astrodeck-photometry";

function loadPhotometry(): PhotometryProfile {
  const def: PhotometryProfile = { egain: 0, readNoiseE: 0, biasAdu: 0 };
  try {
    const raw = localStorage.getItem(PHOTOMETRY_KEY);
    if (raw) {
      const p = JSON.parse(raw) as Partial<PhotometryProfile>;
      if (typeof p.egain === "number" && Number.isFinite(p.egain)) def.egain = p.egain;
      if (typeof p.readNoiseE === "number" && Number.isFinite(p.readNoiseE))
        def.readNoiseE = p.readNoiseE;
      if (typeof p.biasAdu === "number" && Number.isFinite(p.biasAdu)) def.biasAdu = p.biasAdu;
    }
  } catch {
    /* defaults */
  }
  return def;
}

// ----------------------------------------------------------------- dimmer state
// Global brightness dimmer — STORE-OWNED single source (F-dimmer). Day and night
// each remember their own brightness; toggling mode swaps to the other memory
// (design-system §7.5). The store applies the CSS vars (`--screen-brightness` +
// `--scrim-opacity`) on every change and at init, so App and HeaderControls both
// just subscribe to the slice — no MutationObserver/getComputedStyle round-trip.
// Mirrors index.html's pre-paint script so first paint never flashes.
const BRIGHT_DAY_KEY = "astrodeck-bright-day";
const BRIGHT_NIGHT_KEY = "astrodeck-bright-night";

// Floor is 0.5 (product decision): the screen can never be dimmed to unreadable.
// Both day and night sliders share this clamp, and readBright() re-clamps stale
// persisted values (< 0.5 from before this floor) up to 0.5 on load.
const clampBright = (v: number): number => Math.min(1, Math.max(0.5, v));

function readBright(key: string, def: number): number {
  try {
    const n = Number(localStorage.getItem(key));
    return Number.isFinite(n) && n > 0 ? clampBright(n) : def;
  } catch {
    return def;
  }
}

/** Write the dimmer CSS vars onto <html>. The single writer of these vars (DIMMER
 *  CONTRACT). Dimming is now pure filter:brightness — the scrim stays 0 across the
 *  whole 0.5..1 slider range. The OLD formula (1 - b*1.05) compounded WITH the
 *  filter into an effective ~b² crush (0.45 slider → ~0.21 on screen), which made
 *  even white text ~1.7:1; that was the dominant "unreadable at night" factor. */
function applyBrightnessVars(v: number): void {
  const b = clampBright(v);
  const d = document.documentElement;
  d.style.setProperty("--screen-brightness", String(b));
  d.style.setProperty("--scrim-opacity", String(Math.max(0, 0.5 - b)));
}

interface AppState {
  // --- core view/session ---
  view: ViewName;
  // NOV-9: the troubleshooting topic to scroll-into-view + highlight when
  // HelpView mounts via a deep-link (a toast/inline "How to fix →"). Never
  // persisted; null on a direct/manual visit to the Help view.
  helpTopic: TroubleshootTopic | null;
  night: boolean;
  status: RigStatus | null;
  preview: PreviewInfo | null;
  focus: FocusEvent | null;
  // Canonical latest-completed-autofocus-run record (F5: R2-FOC-01/DOC-FOC-01).
  // `focus` above stays the raw live bus slice (streams every "running" tick
  // for the in-progress V-curve chart, exactly as before); this is a SEPARATE
  // snapshot written ONLY on a terminal (done|failed) `focus` event, enriched
  // with the provider/filter that were live at that instant (the bus event
  // carries neither) and the event's own timestamp. It survives navigation
  // (it's store state, not view-local) and survives until the NEXT run's own
  // terminal event replaces it — a fresh run's early empty "running" ticks
  // never blank it. null until the first autofocus run completes this session.
  lastAutofocusResult: AutofocusResult | null;
  guide: (GuideStats & { name?: string }) | null;
  // Guiding Assistant live progress (design 2026-07-24 D5): the latest
  // `{phase, pct, message}` off the dedicated "guide_assistant" bus channel
  // (kept OFF the "guide" channel so the live guide graph is untouched). null
  // between runs; the GuideAssistantPanel drives its progress bar from this.
  guideAssistant: { phase: string; pct: number; message: string } | null;
  // Auto-learn loop progress (tech-debt hardening c1/c2). Each is the latest
  // tick off its own small bus channel; null between runs. Deliberately thin —
  // the actual per-slot autofocus sweeps ride the existing "focus" channel the
  // UI already renders.
  egainLearn:
    | { state: string; step?: number; of?: number; gain?: number; egain?: number;
        applied?: boolean; error?: string }
    | null;
  filterOffsetsLearn:
    | { state: string; slot?: number | null; of?: number; name?: string;
        ref_slot?: number; offsets?: number[]; kept?: number[]; error?: string }
    | null;
  // Same-night per-provider RMS windows (P5-T1, spec §6 P5): the LAST
  // guide-stats tick seen while a given provider KIND ("astrodeck"/"backend"/
  // "sim") was resolved, keyed by `status.providers.guide.kind` at the moment
  // each "guide" bus event is ingested (see handleEvent's "guide" case). The
  // `guide` bus channel itself carries no provider tag, so this is where it
  // gets one. Session-only (not persisted; resets on reload) — feeds
  // GuideView's head-to-head RMS comparison via lib/rmsCompare.ts. Empty
  // until the guide provider resolves AND a "guide" tick lands.
  guideRmsByKind: GuideRmsByKind;
  // The latest guide-calibration step off the "guide" bus channel (2026-08-07):
  // {leg, dir, ms, step, walk} where walk is the star's measured displacement
  // trail from its calibration origin. Only the WALK's cal_step publishes carry
  // it, and the 2 s status publisher interleaves without it — so it is kept as
  // its own slice, held while the phase is still "calibrating", and cleared
  // the moment the phase moves on. Feeds the GuideQuickBar chip + pulse ring
  // and the calibration walk plot.
  guideCal:
    | { leg?: string; dir?: string; ms: number; step: number;
        walk: [number, number][] }
    | null;
  // What the mount's one shared solve path is doing THIS second (2026-08-07):
  // goto centering, meridian flip, resume re-center all narrate through it.
  // `stuck` is the centering loop's "the mount is not executing slews" verdict
  // — sticky until the next goto starts, because it is precisely the state the
  // operator must not miss.
  mountOp:
    | { activity?: "exposing" | "solving" | null; exposure_s?: number;
        attempt?: number; stuck?: boolean; error_arcmin?: number }
    | null;
  sequence: SequenceState;
  polar: PolarState;
  // --- frame settings, by PURPOSE (#176) -------------------------------------
  // Server truth for what the next frame of each KIND will be shot at. Seeded
  // by the WS `hello` (hub.summary().frames) and replaced by the `frames`
  // event, so every surface that shoots a frame of a given purpose reads the
  // same numbers and a reload recovers them.
  //
  // Before this each screen kept its own useState seeded from a constant, and
  // the Align screen's solve settings rode the `polar` event — which start()
  // resets — so beginning an alignment reverted every face to a default while
  // the engine went on using the operator's values. See FrameSettings.
  frameSettings: Record<FrameScope, FrameSettings>;
  logs: LogLine[];
  // Snapshot of the last-shot batch of Light frames (calibration-capture spec
  // §1.3), accumulated by noteLightFrame each time a light frame lands. Drives
  // the Capture view's "Match last lights" prefill + the end-of-session "take
  // matching darks?" nudge. In-memory only (no persistence) — the nudge is a
  // same-session convenience, not a cross-reload record. null until the first
  // light frame of the session lands.
  lastLight: LightSnapshot | null;

  // Built master calibration frames (PRO-1), fetched from GET /api/calibration/
  // masters. Feeds the live pre-flight coverage row (via usePreflight) and the
  // Settings → Calibration library panel. [] until loadMasters() lands.
  masters: MasterRow[];

  // --- config / plan (settings + atlas, reconciled) ---
  config: AppConfig | null;
  // self-update snapshot (Phase 3). null until loadUpdate()/the first `update`
  // event lands. Non-secret; drives the Settings → Updates panel.
  update: UpdateStatus | null;
  // RBAC principal (W2.5). null = UNRESOLVED → treat as viewer (fail-closed) until
  // loadPrincipal() lands. Under the `none` provider this resolves to admin +
  // ALL caps, so the default LAN UI is unchanged. The cap-gate hooks (lib/caps.ts)
  // read this slice.
  principal: Principal | null;
  // The UNAUTHENTICATED "what login UI do I render?" signal (W2.6; GET
  // /api/auth/methods). null = not-yet-loaded. `methods == []` ⇒ open LAN, NO
  // login screen (today's default). Loaded at boot next to config/principal, and
  // re-loaded after login/logout/first-run/method-config so the gate flips live.
  authMethods: AuthMethods | null;
  // #117: which gate screen (if any) is currently standing in for the console —
  // App is the single writer (it renders those screens, so it cannot disagree
  // with itself) and everything that could SPEAK to a viewer reads it from here
  // rather than re-deriving the gate. See lib/authGate.ts for the two rules.
  //
  // Starts "open" because at module load it is literally true: no App is mounted,
  // so no gate screen is standing in front of anything. That is not a fail-open
  // hole — App publishes the real value from an effect declared AHEAD of the one
  // that calls connectWs(), so the gate is known before the transport that
  // carries rig data has been opened at all, and the store starts with no rig
  // state to leak regardless.
  authGate: AuthGate;
  plan: SequencePlan; // atlas SSOT; setPlan persists localStorage in the setter
  editorDirty: boolean;
  // id of the library plan currently loaded into the editor (null = a local draft
  // never saved to / loaded from the library). Drives the unified Plan panel's
  // saved/unsaved cue + "which saved plan is loaded" row highlight (G2). Session-
  // only (NOT persisted): a full reload treats the restored draft as unsaved.
  loadedPlanId: string | null;
  siteDirty: boolean;
  opticsDirty: boolean;

  // --- atlas / framing (design spec §4.2) ---
  // The active framing session (null until openFraming). Optics are read from
  // `config.optics_computed`; site from `site` — NO separate settings slice.
  framing: FramingSession | null;
  atlasHandoff: number; // bump flashes the "added to plan" banner in SequenceView
  // One-shot Atlas→Plan hand-off banner. addTargetsToPlan sets it to the panel
  // count of the latest Send; SequenceView renders "N panels added from Atlas"
  // and clears it via dismissAtlasBanner. Store-held so it survives SequenceView's
  // remount on navigation (App's single-slot <main key={view}>) — a useRef seed
  // would miss the already-bumped signal.
  atlasBannerPending: number | null;

  // --- site (onboarding) ---
  site: SiteInfo | null;
  equipConnected: boolean; // sticky equipment flag, NOT wsConnected

  // --- coach marks / first-run wizard (F-G + NOV-2) ---
  coachSeen: SeenMap;                  // persisted astrodeck-coach-seen
  wizardOpen: boolean;                 // session
  wizardStepId: WizardStepId | null;   // session (manual override)

  // --- reliability transport + toasts (the ONE toast model) ---
  wsPhase: WsPhase;
  wsLastEvent: number;
  telemetryStale: boolean;
  toasts: Toast[];
  logOpen: boolean;
  unseenError: number;
  ninaHealth: NinaHealth;
  notifyEnabled: boolean;

  // --- confirm host (onboarding) ---
  confirm: ConfirmRequest | null;

  // --- automation / safety (Batch-4b; design spec §2.2) ---
  // safety: latest SafetyMonitor snapshot (null until a `safety` event/hello). The
  // UNSAFE path enqueues a STICKY toast (ttl:0) via the existing queued toast model
  // — no second toast slot (C3-6c is satisfied by the queue that already shipped).
  safety: SafetyState | null;
  // alert: last outbound-alert delivery result; `key` bumps on each event so a UI
  // effect can react to repeats with identical {sink,ok}. null until first `alert`.
  alert: { sink: string; ok: boolean; error?: string; key: number } | null;
  // lastReportId: id of the most recently finalized SessionReport (a `report`
  // event). The run-complete panel + overflow deep-link to it in the next workflow.
  lastReportId: string | null;

  // --- weather (sub-project C §9) ---
  // Latest normalized weather payload (null until a `weather` event or the
  // panel's cold GET lands — non-holders never receive either, spec §8).
  weather: WeatherState | null;
  // Bumped ONLY when `alert` transitions null -> non-null (the `alert` slice
  // key idiom) so the popup effect fires exactly once per server-side latch.
  weatherAlertKey: number;

  // --- live-preview (Batch-2; master §A.2) ---
  previews: PreviewInfo[]; // newest last, cap 24 (client metadata ring)
  selectedPreviewId: number | null; // null => follow live
  livePreviewId: number | null;
  viewport: Viewport; // in-memory only (survives tab switch, NOT localStorage)
  stretch: StretchParams; // auto/advancedOpen persist; B/M/W do NOT
  overlays: OverlayToggles; // persists to localStorage
  hfrGood: number; // verdict threshold (px), default 2.5
  hfrWarn: number; // default 4.0

  // --- monitor (Batch-2; master §A.2 + monitor §3.2) ---
  // Set on each PREVIEW event. This is a picture-arrived clock and nothing
  // more — it drives the LIVE badge, which is exactly a claim about pictures.
  // It is NOT the stall clock; see lastCaptureAtMs and #206.
  lastFrameAtMs: number | null;
  // Set when the SERVER'S frames_done advances (or a run starts). "When did a
  // frame last actually land on the rig" — the question CAPTURE STALLED is
  // asking, and the one a dropped preview JPEG must not be able to answer.
  // null whenever no run is in flight, so nothing can be overdue.
  lastCaptureAtMs: number | null;
  lastFramesDone: number | null; // previous frames_done, to spot the advance
  lastGuideAtMs: number | null; // set on each guide event
  autoMonitor: boolean; // localStorage pref, default false (auto-SELECT only)
  runBanner: RunBanner; // persistent "Sequence running — open Live" banner

  // --- touch ergonomics (Batch-3 3B; master §A.2 / touch §2.2) ---
  locked: boolean; // touch-guard engaged; NEVER persisted
  lockAvailable: boolean; // true: reliability sequence-error render shipped (R11)
  monitorAwake: boolean; // explicit wake-lock-as-monitor toggle (decoupled from locked, R13)
  touch: TouchSettings; // haptics / sizing / reverse-axis / auto-lock prefs

  // --- photometry profile (photometry/SNR design §1.3) ---
  // Persisted client-only egain/read-noise/bias inputs for NOV-4 Suggest + PRO-6
  // integration/SNR estimators. All-zero (inert) until the user fills it in.
  photometry: PhotometryProfile;

  // --- the operator's target name (#182) ---
  // WHAT THE HUMAN TYPED, and only that. Lifted out of CaptureView's local
  // useState because `App.tsx` renders <ViewBoundary key={view}/>, so every tab
  // switch remounted the view and wiped the name back to "" — a name the
  // operator had typed, silently gone because they looked at the Atlas.
  //
  // NOT the derived identification. That lives on `preview.field` and the two
  // are never merged: this string names the FOLDER and the frame counter, and a
  // machine-derived name reaching either would split one night across two
  // directories with two overlapping 0001… runs.
  captureTarget: string;

  // --- dimmer (Batch-3 F-dimmer; design-system §7.5) ---
  brightDay: number; // remembered day brightness (0.5..1), persisted
  brightNight: number; // remembered night brightness (0.5..1), persisted
  // The ACTIVE brightness is derived from `night` via useBrightness(); the store
  // applies the CSS vars whenever either value or `night` changes (single source).

  // --- backward-compat shims ---
  wsConnected: boolean; // = wsPhase === "up"

  // --- actions: core ---
  setView: (v: ViewName) => void;
  // NOV-9: navigate to the Help view, optionally deep-linking a topic
  // (sets helpTopic; omit/undefined clears it — a plain "Help" nav row).
  openHelp: (topic?: TroubleshootTopic) => void;
  // One-shot clear of the deep-linked topic (HelpView calls this after the
  // scroll+highlight beat so a later manual visit isn't stuck highlighting).
  clearHelpTopic: () => void;
  toggleNight: () => void;
  handleEvent: (ev: { type: string; data: Record<string, unknown>; ts: number }) => void;

  // --- actions: calibration capture (calibration-capture spec §1.3) ---
  // Called when a Light frame lands; delegates to lib/calibration's
  // accumulateLight so batch-continuity-vs-reset lives in one tested place.
  noteLightFrame: (snap: Omit<LightSnapshot, "count">) => void;
  clearLastLight: () => void;

  // --- actions: Guiding Assistant ---
  // Drop the retained progress tick. The panel calls this the instant Run is
  // pressed: the previous run's terminal {phase:"done"} tick is deliberately
  // retained (so the bar can sit at 100% while the report is fetched), and
  // without this clear the next run's effect sees a "done" on its very first
  // commit and latches the PREVIOUS run's cached report as if it were fresh.
  clearGuideAssistant: () => void;

  // --- actions: config / plan / site ---
  loadConfig: () => Promise<void>;
  // GET /api/update/status → hydrate the `update` slice (boot + after a check).
  loadUpdate: () => Promise<void>;
  // GET /api/calibration/masters → hydrate the `masters` slice (drives the live
  // pre-flight coverage row + the Calibration library panel). Best-effort.
  loadMasters: () => Promise<void>;
  // GET /api/me → principal. On ApiError 401 (fail-closed server resolution) set a
  // viewer sentinel {role:"viewer",email:null,caps:[]} so the UI degrades to
  // read-only instead of hanging unresolved. Call from ws.ts onopen next to
  // loadConfig(), and re-call after sign-in / sign-out.
  loadPrincipal: () => Promise<void>;
  // GET /api/auth/methods → the login-screen signal (W2.6). Best-effort: a
  // failure leaves the prior value (or null) so a transient blip never strips the
  // gate. Call at boot next to loadConfig/loadPrincipal and after auth changes.
  loadAuthMethods: () => Promise<void>;
  // #117: tell the store which gate screen is on the glass. Called by App (the
  // one component that renders them). Transitioning INTO "login" also DROPS
  // every rig-describing slice — see the action for why that half is the
  // load-bearing one.
  setAuthGate: (g: AuthGate) => void;
  setPlan: (p: SequencePlan, dirty?: boolean) => void;
  setEditorDirty: (b: boolean) => void;
  setLoadedPlanId: (id: string | null) => void;
  setSiteDirty: (b: boolean) => void;
  setOpticsDirty: (b: boolean) => void;
  setSite: (s: SiteInfo) => void;

  // --- actions: coach marks / first-run wizard ---
  markSeen: (key: string) => void;
  resetCoach: () => void;
  openWizard: () => void;
  closeWizard: () => void;
  setWizardStep: (id: WizardStepId | null) => void;

  // --- actions: atlas / framing ---
  openFraming: (e?: CatalogEntry) => void; // view="atlas"; seed center+FOV from entry/optics
  setFraming: (patch: Partial<FramingSession>) => void;
  addTargetsToPlan: (targets: Target[], group?: string) => void; // replace-by-group then append
  dismissAtlasBanner: () => void; // clears the one-shot Atlas→Plan hand-off banner

  // --- actions: reliability ---
  setWsPhase: (p: WsPhase) => void;
  noteWsEvent: () => void;
  setTelemetryStale: (v: boolean) => void;
  enqueueToast: (input: EnqueueInput) => void;
  dismissToast: (id: number) => void;
  dismissExpired: () => void;
  openLog: () => void;
  closeLog: () => void;
  reconcileLogs: (history: LogLine[]) => void;
  setNotifyEnabled: (v: boolean) => void;

  // --- actions: confirm ---
  pushConfirm: (req: Omit<ConfirmRequest, "resolve">) => Promise<boolean>;
  resolveConfirm: (ok: boolean) => void;

  // --- actions: live-preview ---
  pushPreview: (p: PreviewInfo) => void;
  selectPreview: (id: number | null) => void; // null => snap back to live
  setViewport: (v: Partial<Viewport>) => void;
  setStretch: (s: Partial<StretchParams>) => void;
  setOverlays: (o: Partial<OverlayToggles>) => void;

  // --- actions: monitor ---
  setAutoMonitor: (v: boolean) => void;
  dismissRunBanner: () => void;

  // --- actions: touch ---
  setLocked: (v: boolean) => void; // setting true MUST stop any active slew (R11/§4.6)
  setMonitorAwake: (v: boolean) => void;
  setTouch: (patch: Partial<TouchSettings>) => void; // persists each key + mirrors side effects

  // --- actions: photometry profile ---
  setPhotometry: (p: Partial<PhotometryProfile>) => void; // merges + persists to localStorage
  /** #182 — the operator's typed target name. Survives a tab switch. */
  setCaptureTarget: (v: string) => void;
  /** Patch one purpose scope: optimistic locally, PUT to the server, and roll
   *  BACK if the server refuses. The rollback is not defensive tidiness — the
   *  guider refuses a binning change mid-session with a 409, and a dial left
   *  showing a value the rig rejected is this defect in a new place. */
  setFrameSettings: (scope: FrameScope, patch: Partial<FrameSettings>) => void;

  // --- actions: dimmer ---
  setBrightness: (v: number) => void; // sets ACTIVE mode's brightness, persists, applies CSS vars
  resetBrightness: () => void; // active mode → 1.0 (the always-reachable escape hatch)

  // --- actions: compat shims ---
  setWsConnected: (ok: boolean) => void;
  showToast: (level: string, message: string) => void;
}

let toastId = 0;
// Monotonic key bumped on every `alert` event so a UI effect watching `alert.key`
// re-fires even when two consecutive deliveries share the same {sink, ok} (§2.2).
let alertKey = 0;
let linkDownTimer: ReturnType<typeof setTimeout> | null = null;
const LINK_DOWN_ALERT_MS = 30000;

// #117 — the rig's OTHER voice, and the one that reaches furthest. notifyAndBeep
// raises an OS Notification and an audible beep, which leave the page entirely:
// no overlay, no mounted tree, nothing the login screen can cover. The
// link-down alert in particular needs NO open socket to fire — it runs off a
// 30s timer armed when wsPhase goes "down", which is exactly what happens once
// the gate engages and every reconnect starts getting refused, so blocking
// intake does not reach it. Left unguarded it announces "AstroDeck disconnected
// — The display lost the server for 30s — the rig keeps running" to whoever is
// standing in front of the sign-in form, plus a beep. One wrapper so the three
// call sites cannot each forget it.
function announce(
  state: { notifyEnabled: boolean; authGate: AuthGate },
  title: string,
  body: string,
): void {
  if (announcementsBlocked(state.authGate)) return;
  notifyAndBeep(state, title, body);
}

// Hydrate the persisted preview toggles once at module load (mirrors loadPlan()).
const PREVIEW_PERSISTED = loadPreviewPersisted();

// Hydrate the touch prefs once at module load; mirror haptics.enabled + the root
// touch-sizing class so first paint matches the persisted prefs (touch §2.2).
const TOUCH_INIT = loadTouch();
haptics.enabled = TOUCH_INIT.hapticsEnabled;

export const useStore = create<AppState>((set, get) => ({
  // --- core ---
  view: "connect",
  helpTopic: null,
  night: localStorage.getItem("astrodeck-night") === "1",
  status: null,
  preview: null,
  focus: null,
  lastAutofocusResult: null,
  guide: null,
  guideAssistant: null,
  egainLearn: null,
  filterOffsetsLearn: null,
  guideRmsByKind: {},
  guideCal: null,
  mountOp: null,
  sequence: EMPTY_SEQUENCE,
  polar: EMPTY_POLAR,
  frameSettings: EMPTY_FRAME_SETTINGS(),
  logs: [],
  lastLight: null,
  masters: [],

  // --- config / plan ---
  config: null,
  update: null,
  principal: null, // unresolved → fail-closed viewer until loadPrincipal()
  authMethods: null, // unresolved → no login gate until loadAuthMethods() lands
  authGate: "open", // #117 — see the slice comment: no gate screen exists yet
  plan: loadPlan(),
  editorDirty: false,
  loadedPlanId: null,
  siteDirty: false,
  opticsDirty: false,

  // --- atlas / framing ---
  framing: null,
  atlasHandoff: 0,
  atlasBannerPending: null,

  // --- site ---
  site: null,
  equipConnected: false,

  // --- coach marks / first-run wizard ---
  coachSeen: parseSeen(localStorage.getItem(COACH_SEEN_KEY)),
  wizardOpen: false,
  wizardStepId: null,

  // --- reliability ---
  wsPhase: "connecting",
  wsLastEvent: Date.now(),
  telemetryStale: false,
  toasts: [],
  logOpen: false,
  unseenError: 0,
  ninaHealth: EMPTY_NINA_HEALTH,
  notifyEnabled: false,

  // --- confirm ---
  confirm: null,

  // --- automation / safety (Batch-4b) ---
  safety: null,
  alert: null,
  lastReportId: null,
  weather: null,
  weatherAlertKey: 0,

  // --- live-preview ---
  previews: [],
  selectedPreviewId: null,
  livePreviewId: null,
  viewport: defaultViewport(),
  overlays: PREVIEW_PERSISTED.overlays,
  stretch: PREVIEW_PERSISTED.stretch,
  hfrGood: 2.5,
  hfrWarn: 4.0,

  // --- monitor ---
  lastFrameAtMs: null,
  lastCaptureAtMs: null,
  lastFramesDone: null,
  lastGuideAtMs: null,
  autoMonitor: localStorage.getItem(AUTO_MONITOR_KEY) === "1",
  runBanner: null,

  // --- touch ---
  locked: false, // never persisted
  lockAvailable: true, // reliability sequence-error render shipped (R11) → lock enabled
  monitorAwake: false,
  touch: TOUCH_INIT,

  // --- photometry profile ---
  photometry: loadPhotometry(),

  // #182 — deliberately NOT persisted. A target name is a fact about tonight;
  // reloading in the morning and finding last night's name pre-filled over a
  // different patch of sky is how frames get filed under the wrong object.
  captureTarget: "",

  // --- dimmer (F-dimmer) ---
  brightDay: readBright(BRIGHT_DAY_KEY, 1),
  brightNight: readBright(BRIGHT_NIGHT_KEY, 1), // night STARTS at 100% (brightest) — product decision

  // --- compat ---
  wsConnected: false,

  // --------------------------------------------------------------- core actions
  setView: (v) => set({ view: v }),

  openHelp: (topic) => set({ view: "help", helpTopic: topic ?? null }),
  clearHelpTopic: () => set({ helpTopic: null }),

  toggleNight: () => {
    const night = !get().night;
    localStorage.setItem("astrodeck-night", night ? "1" : "0");
    document.documentElement.classList.toggle("night", night);
    set({ night });
    // Re-assert the now-active mode's remembered brightness (F-dimmer: the store is
    // the single writer of the dimmer vars — no MutationObserver round-trip).
    applyBrightnessVars(night ? get().brightNight : get().brightDay);
  },

  // ------------------------------------------------------ calibration capture
  noteLightFrame: (snap) => set((s) => ({ lastLight: accumulateLight(s.lastLight, snap) })),
  clearLastLight: () => set({ lastLight: null }),

  // ---------------------------------------------------------- guide assistant
  clearGuideAssistant: () => set({ guideAssistant: null }),

  // --------------------------------------------------------- config/plan/site
  // The ONE refresh path for the persisted config — and therefore for the site.
  //
  // `site` and `config.site` are two mirrors of a single stored truth. Before
  // this, only ONE of them was refreshed by a save: SitePanel POSTs the site and
  // calls loadConfig(), which updated `config` and left `site` holding whatever
  // the WS `hello` bootstrap put there at boot (hub.summary() carries the site,
  // so it is non-null from the first frame). Everything that reads useSite() —
  // FirstRunWizard's "Set your location" step, PreflightStrip, AtlasView,
  // MonitorView, SequenceView, TonightPicker's altitude limit — therefore kept
  // the PRE-SAVE site until a `status` event happened to overwrite it, which
  // needs a connected rig and never arrives on a cold first run. MEASURED on a
  // phone (412x915, real touch): manual save returned is_default:false, the
  // panel's own header flipped to "Active site: … · manual", and 20s later the
  // wizard still read "Set your location · 1/5 · Next unlocks once your real
  // location is saved" with Next locked. Reported verbatim from the field as
  // "I just set my location manually annnnd... it doesn't show as having been
  // set. However when I skipped ahead then it showed it" — skipping to "Connect
  // a rig" and connecting is exactly what restarts the status stream that
  // refreshes the OTHER mirror.
  //
  // The fix is the cause, not a second poll: the successful save already funnels
  // through here, so refresh both mirrors from the one authoritative GET and
  // every consumer sees it in the same commit. Identity is preserved when
  // nothing changed, so unrelated config bumps (drivers/optics/safety) don't
  // re-render the site consumers.
  loadConfig: async () => {
    try {
      const config = await api.get<AppConfig>("/api/config");
      set((s) => (siteEquals(s.site, config.site) ? { config } : { config, site: config.site }));
    } catch {
      /* leave config as-is; UI shows loading/defaults */
    }
  },

  loadUpdate: async () => {
    try {
      const update = await api.get<UpdateStatus>("/api/update/status");
      set({ update });
    } catch {
      /* leave as-is; the panel shows a loading/unknown state */
    }
  },

  loadMasters: async () => {
    try {
      const masters = await api.get<MasterRow[]>("/api/calibration/masters");
      set({ masters });
    } catch {
      /* leave as-is; the pre-flight calibration row skips on an empty list */
    }
  },

  // Resolve the caller identity (W2.5). A 401 is the server's FAIL-CLOSED signal
  // (auth couldn't resolve a principal) — pin a viewer sentinel so cap gates deny
  // controls rather than hang unresolved. Any other failure (network/timeout)
  // leaves `principal` as-is so a transient blip doesn't strip an already-resolved
  // admin back to viewer mid-session.
  loadPrincipal: async () => {
    try {
      const principal = await getMe();
      set({ principal });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        set({ principal: { role: "viewer", email: null, caps: [] } });
      }
      /* else: keep the prior principal; transport blip, not a deauth */
    }
  },

  // Resolve the login-screen signal (W2.6). Best-effort: on ANY failure keep the
  // prior value so a transient blip never flips the gate. Under the open default
  // this returns {methods:[]} ⇒ no login screen, LAN UI unchanged.
  loadAuthMethods: async () => {
    try {
      const authMethods = await getAuthMethods();
      set({ authMethods });
    } catch {
      /* keep prior value; never strip the gate on a transport blip */
    }
  },

  // #117 — THE LOAD-BEARING HALF. When the login screen takes over, everything
  // the store is holding about the observatory goes with it: weather, status,
  // site, previews, logs, toasts, the lot (lib/authGate.clearedRigState lists
  // them, and says which slices are deliberately kept).
  //
  // Gating the one dialog we caught would have fixed one sentence. The state it
  // was reading is the actual leak: it was delivered while the session was live
  // and simply never dropped when the session ended, so any component that ever
  // renders from it — today's or next month's — is one unconditional hook away
  // from showing an unauthenticated viewer what the sky over this address is
  // doing tonight.
  //
  // This is only HALF of rule 2. Emptying the slices while the socket keeps
  // delivering into them is a two-second clear; handleEvent's intakeBlocked
  // guard is what keeps them empty. Neither is sufficient alone.
  //
  // Only on the rising EDGE into "login" (gateEngaged), never per-commit: the
  // clear exists to drop what the PREVIOUS session left behind, exactly once. A
  // level test would re-run it on every commit while the login screen is up,
  // which reads as "keep the rig state empty" but is really a second, weaker
  // copy of the intake guard sitting in the wrong place.
  //
  // A pending confirm is resolved false rather than dropped: `confirm` holds a
  // promise resolver, and clearing the slice without calling it wedges whatever
  // call site is awaiting it (the same leak pushConfirm already guards against).
  setAuthGate: (g) => {
    const prev = get().authGate;
    if (prev === g) return;
    if (!gateEngaged(prev, g)) {
      set({ authGate: g });
      return;
    }
    const pending = get().confirm;
    if (pending) pending.resolve(false);
    set({ ...clearedRigState(), confirm: null, authGate: g });
  },

  setPlan: (p, dirty = true) => {
    const withIds = ensurePlanIds(p);   // safety net: every write path carries ids
    try {
      localStorage.setItem(PLAN_KEY, JSON.stringify(withIds));
    } catch {
      /* quota / unavailable — keep in-memory */
    }
    set({ plan: withIds, editorDirty: dirty });
  },

  setEditorDirty: (b) => set({ editorDirty: b }),
  setLoadedPlanId: (id) => set({ loadedPlanId: id }),
  setSiteDirty: (b) => set({ siteDirty: b }),
  setOpticsDirty: (b) => set({ opticsDirty: b }),
  setSite: (s) => set({ site: s }),

  // -------------------------------------------------- coach marks / wizard
  markSeen: (key) => {
    const next = withSeen(get().coachSeen, key);
    if (next === get().coachSeen) return;
    try { localStorage.setItem(COACH_SEEN_KEY, serializeSeen(next)); } catch { /* quota */ }
    set({ coachSeen: next });
  },
  resetCoach: () => {
    try { localStorage.removeItem(COACH_SEEN_KEY); } catch { /* ignore */ }
    set({ coachSeen: {}, wizardOpen: false, wizardStepId: null });
  },
  openWizard: () => set({ wizardOpen: true, wizardStepId: null }),
  closeWizard: () => {
    const next = withSeen(get().coachSeen, WIZARD_SEEN_KEY);
    try { localStorage.setItem(COACH_SEEN_KEY, serializeSeen(next)); } catch { /* quota */ }
    set({ coachSeen: next, wizardOpen: false, wizardStepId: null });
  },
  setWizardStep: (id) => set({ wizardStepId: id }),

  // ----------------------------------------------------------- atlas / framing
  // Open the Atlas on `e` (or free-roam when no entry). Switches the view, seeds
  // the session center from the entry (or current mount/0,0 when free-roam), and
  // seeds the survey crop width from the persisted optics FOV (fallback ~1.5°).
  // Defaults: 1×1 mosaic, 25% overlap, DSS2 color, linear stretch, empty panels.
  openFraming: (e) => {
    const config = get().config;
    const center = e
      ? { ra_hours: e.ra_hours, dec_deg: e.dec_deg }
      : { ra_hours: get().status?.mount?.ra_hours ?? 0, dec_deg: get().status?.mount?.dec_deg ?? 0 };
    // Free-roam sends have no catalog id to group panels by; synthesize a stable
    // per-session group id from the rounded center so a multi-panel mosaic groups
    // in the Plan and re-framing REPLACES (not duplicates) its panels (C1-C2).
    const freeroamId = e
      ? undefined
      : `Sky ${center.ra_hours.toFixed(2)}h ${center.dec_deg >= 0 ? "+" : ""}${center.dec_deg.toFixed(1)}°`;
    const framing: FramingSession = {
      target: e,
      center,
      rotation_deg: 0,
      // Night seeds the monochrome red survey (spec §8) so a fresh session never
      // flashes a full-color JPEG at a dark-adapted eye — but ONLY when online
      // fetch is on: the offline pack is color-only (offline-pack spec §9), so
      // seeding red with fetch off would leave a night-mode user permanently
      // degraded. Offline/night falls back to the color default; the night
      // dimmer overlay already handles dark adaptation for it.
      survey:
        get().night && (get().config?.survey?.online_fetch ?? false)
          ? FRAMING_NIGHT_SURVEY
          : FRAMING_DEFAULT_SURVEY,
      stretch: "linear",
      fovZoomDeg: seedFovZoomDeg(config),
      mosaic: { rows: 1, cols: 1, overlap: 0.25 },
      panels: [],
      freeroamId,
    };
    set({ framing, view: "atlas" });
  },

  setFraming: (patch) =>
    set((s) => (s.framing ? { framing: { ...s.framing, ...patch } } : {})),

  // Replace-by-group then append (resolves dedupe critique C1-C2). If any incoming
  // target carries mosaic_group===group, every existing target with that group is
  // removed first, so re-framing the same object REPLACES its panels instead of
  // silently no-op'ing. Single (no group) frames append. Routes through setPlan so
  // the existing persist-in-setter writes localStorage; then bumps atlasHandoff.
  addTargetsToPlan: (targets, group) => {
    const plan = get().plan;
    const groupHit =
      group != null && targets.some((t) => t.mosaic_group === group);
    const kept = groupHit
      ? plan.targets.filter((t) => t.mosaic_group !== group)
      : plan.targets;
    // Atlas hand-off targets carry no schedule — backfill each (default FIRST so an
    // explicit schedule, if a future send ever attaches one, still wins) (C1-27).
    const incoming = targets.map((t) => ({ schedule: defaultSchedule(), ...t }));
    const nextPlan: SequencePlan = { ...plan, targets: [...kept, ...incoming] };
    get().setPlan(nextPlan);
    // Bump the hand-off signal AND record the panel count so SequenceView shows
    // "N panels added from Atlas" exactly once, surviving its remount-on-nav.
    set((s) => ({ atlasHandoff: s.atlasHandoff + 1, atlasBannerPending: targets.length }));
  },

  dismissAtlasBanner: () => set({ atlasBannerPending: null }),

  // ----------------------------------------------------------- reliability
  setWsPhase: (p) => {
    const up = p === "up";
    set({ wsPhase: p, wsConnected: up, telemetryStale: up ? get().telemetryStale : false });
    if (p === "down") {
      if (!linkDownTimer) {
        linkDownTimer = setTimeout(() => {
          linkDownTimer = null;
          if (get().wsPhase !== "up") {
            announce(
              get(),
              "AstroDeck disconnected",
              "The display lost the server for 30s — the rig keeps running.",
            );
          }
        }, LINK_DOWN_ALERT_MS);
      }
    } else if (up && linkDownTimer) {
      clearTimeout(linkDownTimer);
      linkDownTimer = null;
    }
  },

  noteWsEvent: () => set({ wsLastEvent: Date.now() }),
  setTelemetryStale: (v) => set({ telemetryStale: v }),

  enqueueToast: (input) =>
    set((s) => {
      // #117 — the choke point for rule 1 on the OTHER overlay the gate screens
      // keep mounted. A toast is a sentence about the rig: "UNSAFE: rain
      // detected", "Sequence failed: mount lost — check the mount's USB cable",
      // a humanized error log line naming a device. Gating the confirm host and
      // leaving this open would have moved the leak one overlay to the left.
      //
      // Blanket, not per-producer, because the producers are the part that keeps
      // being added to. Safe to be blanket today: Login raises its errors in its
      // own inline `err` state (views/Login.tsx), never through the queue, so
      // there is no gate-screen message this can swallow. If one is ever added,
      // it needs its own exemption HERE rather than a second toast path.
      if (announcementsBlocked(s.authGate)) return {} as Partial<AppState>;
      const now = Date.now();
      const level = input.level;
      const kind = input.kind ?? "generic";
      let toasts = s.toasts;

      if (kind === "sequence") {
        // exactly one focal sequence toast — replace any prior
        toasts = toasts.filter((t) => t.kind !== "sequence");
      } else {
        // Coalesce onto an identical toast that is STILL ON SCREEN (UX review
        // #33: "two identical toasts sometimes, zero other times"). The window
        // used to be a flat 5s, which is wrong for a STICKY toast (ttl 0, the
        // safety UNSAFE notice): it never auto-dismisses, so at +6s the identical
        // re-trip failed the age test and enqueued a SECOND identical sticky card
        // that sat there next to the first. A toast that is still visible is by
        // definition a duplicate of itself, whatever its age — so sticky toasts
        // coalesce forever and timed ones coalesce for as long as they are up
        // (floor of TOAST_DEDUPE_MS so rapid repeats of a short success toast
        // still merge). The ×N chip is exactly the channel for this.
        const dupe = toasts.find(
          (t) =>
            t.kind === "generic" &&
            t.title === input.title &&
            t.level === level &&
            (t.ttl === 0 || now - t.createdAt < Math.max(TOAST_DEDUPE_MS, t.ttl)),
        );
        if (dupe) {
          // coalesce: bump count, leave createdAt UNCHANGED so it still ages out
          const next = toasts.map((t) => (t.id === dupe.id ? { ...t, count: t.count + 1 } : t));
          return { toasts: next };
        }
      }

      const toast: Toast = {
        id: ++toastId,
        level,
        title: input.title,
        detail: input.detail,
        kind,
        createdAt: now,
        ttl: input.ttl ?? TTL[level],
        count: 1,
        action: input.action,
        source: input.source,
      };
      let next = [...toasts, toast];
      if (next.length > TOAST_MAX) {
        // Overflow eviction, other half of UX #33 ("zero other times"). The old
        // rule was `find(t => t.ttl > 0 && t.kind === "generic") ?? next[0]`,
        // which had two ways to throw away the one toast that matters: with the
        // queue full of sticky notices there is no dismissible generic, so it
        // fell through to next[0] — the OLDEST — i.e. the sticky UNSAFE toast,
        // which then existed nowhere in the DOM while the rig sat in the rain;
        // and the find could also select the toast being enqueued right now.
        // Replaced with an explicit severity ladder, oldest-first inside each
        // rung, never the focal sequence toast and never the new arrival: a
        // sticky error (UNSAFE) is the last thing in the queue to be dropped.
        const oldest = (pred: (t: Toast) => boolean) =>
          next.find((t) => t.id !== toast.id && t.kind !== "sequence" && pred(t))?.id;
        const victim =
          oldest((t) => t.ttl > 0 && t.level !== "error") ??   // ordinary transient
          oldest((t) => t.ttl > 0) ??                          // transient error
          oldest((t) => t.level !== "error") ??                // sticky, not an error
          oldest(() => true) ??                                // sticky error
          next[0].id;
        next = next.filter((t) => t.id !== victim);
      }
      return { toasts: next };
    }),

  dismissToast: (id) =>
    set((s) => {
      const next = s.toasts.filter((t) => t.id !== id);
      return { toasts: next };
    }),

  dismissExpired: () =>
    set((s) => {
      const now = Date.now();
      const next = s.toasts.filter((t) => t.ttl === 0 || now - t.createdAt < t.ttl);
      if (next.length === s.toasts.length) return {} as Partial<AppState>;
      return { toasts: next };
    }),

  openLog: () => set({ logOpen: true, unseenError: 0 }),
  closeLog: () => set({ logOpen: false }),

  reconcileLogs: (history) =>
    set((s) => {
      // #117 — the one rig-state write that does NOT come through handleEvent,
      // so it needs the intake rule stated again here. ws.onopen fetches
      // /api/logs and awaits it; if the gate engages inside that await, this
      // would drop 200 lines naming this rig's devices and failures straight
      // into the drawer behind the sign-in screen. Narrow window, free to close.
      // (Safe on the way back in: the post-login reconnect opens a NEW socket,
      // and its onopen cannot beat App's gate publish — a WS handshake is a
      // network round trip, the effect is the same tick as the state change.)
      if (intakeBlocked(s.authGate)) return {} as Partial<AppState>;
      // Refill the drawer from /api/logs after a reconnect; do NOT retroactively
      // inflate unseenError (we can't know which the user already saw).
      const logs = history.slice(-200);
      return { logs };
    }),

  setNotifyEnabled: (v) => {
    set({ notifyEnabled: v });
    if (v) void requestNotifyPermission();
  },

  // -------------------------------------------------------------- confirm host
  // Only one confirm can be live at a time. If a request is already pending when a
  // new one arrives, resolve the stale one `false` (cancel) BEFORE replacing it so
  // its awaiter never hangs — a leaked pending promise on a GOTO/abort guard would
  // wedge that call site forever (F-D1).
  pushConfirm: (req) =>
    new Promise<boolean>((resolve) => {
      // #117 — the choke point for "no dialog fires over a gate screen". Every
      // confirm in the app arrives here, so this is the one place the rule can
      // be stated once and hold for call sites that do not exist yet; the
      // weather-alert effect that leaked the forecast over the login screen was
      // not doing anything unusual, it was just first.
      //
      // Refused == resolve(false), the same answer a Cancel gives, because every
      // caller already treats false as "do not proceed" — and under a gate that
      // is exactly right. Silent by design: an explanatory modal would be one
      // more thing said to a viewer we have decided not to talk to
      // (gateBlockedReason exists for the developer reading this path).
      if (announcementsBlocked(get().authGate)) {
        resolve(false);
        return;
      }
      const prev = get().confirm;
      if (prev) prev.resolve(false);
      set({ confirm: { ...req, resolve } });
    }),

  resolveConfirm: (ok) => {
    const c = get().confirm;
    if (c) c.resolve(ok);
    set({ confirm: null });
  },

  // --------------------------------------------------------------- live-preview
  // Append, trim to cap, set livePreviewId. Pinning is sticky: if the user has
  // pinned a frame (selectedPreviewId !== null) the stage does NOT move — the
  // filmstrip badges the new live arrival and the pinned banner counts it.
  pushPreview: (p) =>
    set((s) => {
      const previews = [...s.previews, p];
      if (previews.length > PREVIEW_CAP) previews.splice(0, previews.length - PREVIEW_CAP);
      return { previews, livePreviewId: p.id };
    }),

  selectPreview: (id) => set({ selectedPreviewId: id }),

  setViewport: (v) => set((s) => ({ viewport: { ...s.viewport, ...v } })),

  setStretch: (s) =>
    set((st) => {
      const stretch = { ...st.stretch, ...s };
      // Persist only the toggles (auto/advancedOpen); B/M/W/brightness never persist.
      persistPreview(st.overlays, stretch);
      return { stretch };
    }),

  setOverlays: (o) =>
    set((st) => {
      const overlays = { ...st.overlays, ...o };
      persistPreview(overlays, st.stretch);
      return { overlays };
    }),

  // -------------------------------------------------------------------- monitor
  setAutoMonitor: (v) => {
    localStorage.setItem(AUTO_MONITOR_KEY, v ? "1" : "0");
    set({ autoMonitor: v });
  },

  dismissRunBanner: () => set({ runBanner: null }),

  // -------------------------------------------------------------------- touch
  // Engaging the lock MUST stop any active slew (R11/§4.6). The SlewPad's own
  // controller also forceStops on the `locked` flag, but we POST rate 0 here as
  // an authoritative backstop so locking halts the mount even with no pad mounted.
  setLocked: (v) => {
    if (v && !get().locked) {
      void api.post("/api/mount/stop").catch(() => {
        /* best-effort; the SlewPad controller + server deadman are the backstops */
      });
    }
    set({ locked: v });
  },

  setMonitorAwake: (v) => set({ monitorAwake: v }),

  // Persist each changed key, mirror hapticsEnabled to the haptics singleton, and
  // reflect touchSizing onto the root class (touch §2.2 / §3, R17).
  setTouch: (patch) => {
    const touch = { ...get().touch, ...patch };
    try {
      if (patch.hapticsEnabled !== undefined)
        localStorage.setItem(HAPTICS_KEY, patch.hapticsEnabled ? "1" : "0");
      if (patch.touchSizing !== undefined) localStorage.setItem(TOUCH_SIZE_KEY, patch.touchSizing);
      if (patch.reverseRa !== undefined)
        localStorage.setItem(REV_RA_KEY, patch.reverseRa ? "1" : "0");
      if (patch.reverseDec !== undefined)
        localStorage.setItem(REV_DEC_KEY, patch.reverseDec ? "1" : "0");
      if (patch.autoLockMs !== undefined)
        localStorage.setItem(AUTOLOCK_KEY, patch.autoLockMs ? String(patch.autoLockMs) : "0");
    } catch {
      /* quota / unavailable — keep in-memory */
    }
    if (patch.hapticsEnabled !== undefined) haptics.enabled = patch.hapticsEnabled;
    if (patch.touchSizing !== undefined) applyTouchSizing(patch.touchSizing);
    set({ touch });
  },

  // Merge + persist (best-effort). Inert defaults (all-zero) mean a partial patch
  // (e.g. only egain from Task 7's status prefill) never blanks the other fields.
  setPhotometry: (p) => {
    const next = { ...get().photometry, ...p };
    try {
      localStorage.setItem(PHOTOMETRY_KEY, JSON.stringify(next));
    } catch {
      /* quota / unavailable — keep in-memory */
    }
    set({ photometry: next });
  },

  setCaptureTarget: (v) => set({ captureTarget: v }),

  // ------------------------------------------- frame settings, by PURPOSE (#176)
  // OPTIMISTIC, then the server's answer, then a rollback if it refused.
  //
  // The write goes through the server on purpose. A store-only write would make
  // every surface agree with every other surface and with nothing on the rig —
  // which is the failure this replaced, wearing a tidier hat.
  setFrameSettings: (scope, patch) => {
    const before = get().frameSettings[scope];
    set((s) => ({
      frameSettings: { ...s.frameSettings, [scope]: { ...before, ...patch } },
    }));
    void api
      .put(`/api/camera/frame-settings?scope=${encodeURIComponent(scope)}`, patch)
      .then((r) => {
        // The server is the authority the moment it speaks: it clamps, and for
        // the guide scope it answers with the RUNNING guider's values.
        const body = r as { settings?: FrameSettings; frames?: unknown };
        if (body?.frames) {
          set((s) => ({ frameSettings: mergeFrames(s.frameSettings, body.frames) }));
        } else if (body?.settings) {
          set((s) => ({
            frameSettings: { ...s.frameSettings, [scope]: body.settings! },
          }));
        }
      })
      .catch((e) => {
        set((s) => ({ frameSettings: { ...s.frameSettings, [scope]: before } }));
        get().showToast("error", (e as Error).message);
      });
  },

  // -------------------------------------------------------------------- dimmer
  // Single source for the brightness dimmer (F-dimmer). Writes the ACTIVE mode's
  // remembered value + its localStorage key, then applies the CSS vars itself —
  // App + HeaderControls just subscribe to the slice via useBrightness().
  setBrightness: (v) => {
    const b = clampBright(v);
    const isNight = get().night;
    try {
      localStorage.setItem(isNight ? BRIGHT_NIGHT_KEY : BRIGHT_DAY_KEY, String(b));
    } catch {
      /* quota / unavailable — keep in-memory */
    }
    applyBrightnessVars(b);
    set(isNight ? { brightNight: b } : { brightDay: b });
  },

  // Always-reachable escape hatch (§7.2): reset the ACTIVE mode to 1.0.
  resetBrightness: () => {
    const isNight = get().night;
    try {
      localStorage.setItem(isNight ? BRIGHT_NIGHT_KEY : BRIGHT_DAY_KEY, "1");
    } catch {
      /* quota / unavailable — keep in-memory */
    }
    applyBrightnessVars(1);
    set(isNight ? { brightNight: 1 } : { brightDay: 1 });
  },

  // -------------------------------------------------------------- compat shims
  setWsConnected: (ok) => get().setWsPhase(ok ? "up" : "down"),

  showToast: (level, message) =>
    get().enqueueToast({ level: level as ToastLevel, title: humanizeLog(message) }),

  // ------------------------------------------------------------------ events
  handleEvent: (ev) => {
    // #117 — rule 3, and the reason the clear in setAuthGate is worth anything.
    // This is the ONE door every piece of rig telemetry comes through (the WS
    // frame handler, the reconnect snapshot, and the panels' cold GETs all route
    // here on purpose), so it is the one place "the store stops holding the rig"
    // can be made to mean "and stops re-acquiring it".
    //
    // Without this, the clear lasts about two seconds. Pressing Sign out does
    // NOT close the socket — nothing on that path calls reconnectWs — and the
    // server only re-authenticates an open /ws every 60s (WS_AUTH_RECHECK_S), so
    // until that check fires it is still serving the PRE-logout principal, i.e.
    // unredacted. hub publishes `status` every 2s. The next frame would restore
    // the site name and its precise coordinates, where the mount is pointed and
    // what is connected, then weather, previews and error toasts — behind the
    // sign-in form, for up to a minute.
    //
    // Dropped, not queued: a frame is a snapshot of a moment that has passed by
    // the time anyone signs back in, and replaying a minute of stale rig state
    // into a fresh session is its own wrong reading. The socket re-hydrates on
    // reconnect (ws.onopen: config, logs, the monitor snapshot) and App
    // re-fetches weather on the gate lift — see lib/authGate.ts.
    if (intakeBlocked(get().authGate)) return;
    switch (ev.type) {
      case "status": {
        const status = ev.data as unknown as RigStatus;
        const equipConnected = status.mode !== undefined && status.mode !== "none";
        set((s) => ({
          status,
          ninaHealth: deriveNinaHealth(status),
          equipConnected,
          // The 2s poll is the LIVE truth for the SafetyMonitor: poll_status
          // forwards the own-cadence poller's cached reading — the flat dict, or
          // `null` when no monitor is connected OR it dropped mid-session (the
          // poller clears its cache). Normalizing here is what makes
          // store.safety.connected self-heal on disconnect/reconnect within a
          // poll cycle, so the no-monitor confirm can be trusted. Carry the
          // UI-accumulated streak across the wholesale status replace.
          ...(status.safety !== undefined
            ? { safety: normalizeSafety(status.safety, s.safety?.streak ?? 0) }
            : {}),
        }));
        // poll_status carries the full 6-field site; keep store.site fresh while
        // connected so is_default/horizon_min_deg don't go stale until reconnect.
        if (status.site) set({ site: status.site });
        break;
      }
      case "hello": {
        // cold hydration snapshot — backend sends {"data": hub.summary()} with no
        // wrapper, so treat data directly as the summary dict (may carry site / mode
        // and, Batch-4b, a `safety` snapshot + redacted `config`).
        // NB: `safety` here is the FLAT server SafetyReading dict (or null), NOT a
        // SafetyState — hub.summary() emits _safety_reading_dict(); it must be
        // normalized, never assigned straight in (that left `connected` undefined).
        const summary = ev.data as unknown as Partial<RigStatus> & {
          site?: SiteInfo;
          safety?: SafetyReading | null;
          config?: AppConfig;
          frames?: unknown;
        };
        if (summary.site) set({ site: summary.site });
        // COLD-SEED the frame settings (#176). Half the R-vs-Oiii defect was
        // that no client ever learned the server's values: the Align screen's
        // numbers arrived only on a `polar` event, so a reload over a live pin
        // rendered mirrored defaults while the engine used something else.
        if (summary.frames) {
          set((s) => ({ frameSettings: mergeFrames(s.frameSettings, summary.frames) }));
        }
        if (summary.mode !== undefined) {
          set({ equipConnected: summary.mode !== "none" });
        }
        // Hydrate automation state on connect so the header safety chip + Settings
        // render correctly without waiting for the first periodic event (§2.2).
        // Normalize even when `null` (no monitor) so a reconnect with the monitor
        // gone correctly reads connected:false instead of a stale true.
        if (summary.safety !== undefined) {
          set((s) => ({ safety: normalizeSafety(summary.safety, s.safety?.streak ?? 0) }));
        }
        if (summary.config) set({ config: summary.config });
        break;
      }
      case "config":
        // re-GET, never partial-merge (settings spec C1-H28/C2-12)
        void get().loadConfig();
        break;
      case "update":
        // the self-update poller/pipeline pushes the whole snapshot; replace it.
        set({ update: ev.data as unknown as UpdateStatus });
        break;
      case "safety": {
        // SafetyMonitor reading (engine §1.9-A / status §1.11). A `stale` read means
        // the device dropped or the cached read timed out — treat as NOT connected.
        const reading = ev.data as unknown as SafetyReading;
        set((s) => ({ safety: normalizeSafety(reading, s.safety?.streak ?? 0) }));
        // Unsafe OR stale → persistent (sticky) alert through the EXISTING queued
        // toast model: enqueueToast({level,title,ttl:0}). ttl:0 = never auto-dismiss,
        // so the UNSAFE notice can't be overwritten by a later transient toast (C3-6c).
        if (reading.is_safe === false || reading.stale) {
          get().enqueueToast({
            level: "error",
            title: `UNSAFE: ${reading.reason || (reading.stale ? "safety read stale" : "unsafe condition")}`,
            ttl: 0,
          });
        }
        break;
      }
      case "alert": {
        // Outbound-alert delivery result (alerting.py publishes on every attempt).
        // Bump the monotonic key so a UI effect re-fires for repeat {sink,ok} events.
        const d = ev.data as unknown as { sink: string; ok: boolean; error?: string };
        set({ alert: { sink: d.sink, ok: d.ok, error: d.error, key: ++alertKey } });
        break;
      }
      case "report": {
        // A SessionReport finalized — stash its id so the run-complete panel +
        // overflow can deep-link to ReportView (lands in the next workflow).
        const d = ev.data as unknown as { id: string | null };
        if (d.id) set({ lastReportId: d.id });
        break;
      }
      case "weather": {
        // Weather payload (spec §7) — WS push, the panel's cold GET, and the
        // ignore-tonight POST all route through here (single application
        // path). Normalize (stale fail-closed, clamped) and replace; bump
        // weatherAlertKey ONLY on the alert null -> non-null edge so the
        // popup fires once per server-side once-per-night latch (spec §12).
        const raw = ev.data as unknown as WeatherState;
        const nw = normalizeWeather(raw, Date.now() / 1000);
        set((s) => ({
          weather: nw,
          weatherAlertKey:
            nw?.alert && !s.weather?.alert
              ? s.weatherAlertKey + 1
              : s.weatherAlertKey,
        }));
        break;
      }
      case "preview": {
        const p = ev.data as unknown as PreviewInfo;
        // Keep the single-frame `preview` (existing consumers) AND push into the
        // ring + stamp the liveness timestamp the Monitor's LIVE/STALL uses.
        set({ preview: p, lastFrameAtMs: Date.now() });
        get().pushPreview(p);
        break;
      }
      // #182 — A LATE SOLVE PATCHES; IT DOES NOT RE-PUBLISH. The background WCS
      // worker finishes seconds after the picture is already on screen, and
      // re-sending the whole `preview` event to carry a name would push the JPEG
      // again over field WiFi for a 200-byte change. `preview_id: null` is the
      // invalidation (the mount moved) and clears the block from every frame.
      case "preview_field": {
        const d = ev.data as unknown as {
          preview_id: number | null; field: PreviewField | null;
        };
        set((s) => {
          const patch = (p: PreviewInfo): PreviewInfo => {
            if (d.preview_id === null) {
              if (!p.field) return p;
              const { field: _drop, ...rest } = p;
              return rest as PreviewInfo;
            }
            if (p.id !== d.preview_id) return p;
            return d.field ? { ...p, field: d.field } : p;
          };
          const preview = s.preview ? patch(s.preview) : null;
          const previews = s.previews.map(patch);
          const changed =
            preview !== s.preview || previews.some((p, i) => p !== s.previews[i]);
          return changed ? { preview, previews } : {};
        });
        break;
      }
      case "focus": {
        set({ focus: ev.data as unknown as FocusEvent });
        // Snapshot the canonical latest-run record on a terminal tick only
        // (normalizeAutofocusResult itself returns null for "running", so
        // this is safe to call on every tick — but skipping the lookup for
        // the common "running" ticks avoids reading `status` on every
        // in-sweep point). provider/filter come from the store's OWN live
        // status at this instant (F5: R2-FOC-01) since the bus event has
        // neither; `ev.ts` (seconds) is the event's timestamp, not the
        // client's receive time.
        const state = (ev.data as { state?: unknown }).state;
        if (state === "done" || state === "failed") {
          const st = get();
          const providers = (st.status as (RigStatus & { providers?: ProvidersStatus }) | null)?.providers;
          const choice = providers?.autofocus;
          const result = normalizeAutofocusResult(ev.data, {
            provider: choice ? { kind: choice.kind, label: choice.label } : null,
            filter: filterNameFromStatus(st.status?.filterwheel),
            tsMs: ev.ts * 1000,
          });
          if (result) set({ lastAutofocusResult: result });
        }
        break;
      }
      case "guide": {
        const stats = ev.data as unknown as GuideStats;
        // Tag this tick by the CURRENTLY resolved guide provider (the "guide"
        // bus event itself carries no provider field) so GuideView's
        // same-night RMS comparison (lib/rmsCompare.ts) has a per-provider
        // window to read back later. After the C1 server honesty fix the badge
        // reports the ACTUAL serving guider, so the tag can't be mislabeled;
        // a provider switch takes effect on the NEXT guiding start (see
        // lib/guideRms.ts). `status.providers` may not have landed yet
        // (pre-first-poll) or the resolver may be between capabilities — in
        // that case tagGuideRms just drops the tag (never files it under an
        // arbitrary kind).
        const providers = (
          get().status as (RigStatus & { providers?: ProvidersStatus }) | null
        )?.providers;
        const choice = providers?.guide;
        // Calibration-walk narration (2026-08-07): only the walk's cal_step
        // publishes carry `cal`, and the 2 s status publisher interleaves
        // without it — so the last step is HELD while the phase is still
        // "calibrating" and cleared the moment the phase moves on. Without the
        // hold, the chip and walk plot flickered at the status cadence.
        const cal = (ev.data as { cal?: AppState["guideCal"] }).cal;
        set((s) => ({
          guide: stats,
          guideCal: cal ?? (stats.phase === "calibrating" ? s.guideCal : null),
          lastGuideAtMs: Date.now(),
          guideRmsByKind: tagGuideRms(s.guideRmsByKind, stats, choice, Date.now()),
        }));
        break;
      }
      case "mount": {
        // The shared solve path's narration + the centering loop's verdicts
        // (2026-08-07). `centering_stuck` is sticky: it means the mount is not
        // executing slews, and it must survive until the next goto proves
        // otherwise. Every other mount action (slew chatter) is ignored here —
        // `status.mount.slewing` already covers motion.
        const d = ev.data as { action?: string; activity?: "exposing" | "solving" | null;
                              exposure_s?: number; attempt?: number; error_arcmin?: number };
        if (d.action === "solve_activity") {
          set((s) => ({ mountOp: { ...(s.mountOp ?? {}), activity: d.activity ?? null,
                                   exposure_s: d.exposure_s ?? s.mountOp?.exposure_s } }));
        } else if (d.action === "centering") {
          set({ mountOp: { attempt: d.attempt, stuck: false } });
        } else if (d.action === "centered") {
          set({ mountOp: null });
        } else if (d.action === "centering_stuck") {
          set({ mountOp: { stuck: true, error_arcmin: d.error_arcmin, activity: null } });
        }
        break;
      }
      case "guide_assistant": {
        // Guiding Assistant progress (design D5). The final "done"/"error" tick
        // is left in place so the panel can show 100% (or the failure message)
        // until it fetches the report; a fresh run's first tick replaces it, and
        // the panel calls clearGuideAssistant() the moment Run is pressed so a
        // stale "done" can never be mistaken for this run's result.
        const d = ev.data as unknown as { phase: string; pct: number; message: string };
        set({ guideAssistant: d });
        break;
      }
      case "egain": {
        set({ egainLearn: ev.data as unknown as AppState["egainLearn"] });
        break;
      }
      case "filter_offsets": {
        set({
          filterOffsetsLearn: ev.data as unknown as AppState["filterOffsetsLearn"],
        });
        break;
      }
      case "sequence": {
        const seq = ev.data as unknown as SequenceState;
        const prevState = get().sequence.state;
        const percent = seq.progress?.percent;

        // --- monitor runBanner (monitor §3.2): persistent, NOT the focal toast ---
        // Rising edge (not-running → running) raises the banner; subsequent
        // progress refreshes percent; any terminal/idle state clears it.
        let runBanner: RunBanner = get().runBanner;
        if (seq.state === "running") {
          if (RUN_RISING_FROM.has(prevState)) {
            runBanner = { active: true, plan_name: seq.plan_name, percent };
          } else if (runBanner) {
            runBanner = { ...runBanner, percent };
          }
        } else if (seq.state === "paused" || seq.state === "aborting") {
          // "aborting" is NOT terminal: the engine publishes it for the whole
          // wind-down (types.ts). Clearing the banner there took the run off
          // every other screen — and the banner is the only thing outside the
          // Monitor that says a run is happening — while the rig was still
          // stopping. It clears when "aborted" lands, which is when it is true.
          if (runBanner) runBanner = { ...runBanner, percent };
        } else {
          // idle / complete / aborted / error / nina_native → clear the banner.
          runBanner = null;
        }
        // CAPTURE LIVENESS, from the SERVER'S OWN COUNTER (#206).
        //
        // The stall check used to time `lastFrameAtMs`, which is stamped in the
        // `preview` handler — so it measured the arrival of preview JPEGs at
        // this browser, not the capture of frames on the rig. Reported from the
        // rig 2026-08-09 02:12: a run saving a frame every 71 s, 70/150 done, 0
        // rejected, reading CAPTURE STALLED. It is worst exactly where it
        // matters least — over the relay a preview JPEG is the heaviest payload
        // and the first thing a slow link drops, so the alarm fired because the
        // network was slow, not because capture was.
        //
        // `frames_done` is computed on the rig and rides the tiny sequence
        // payload. Stamping the moment it ADVANCES gives "when did a frame last
        // actually land", which is the question the alarm is asking. Seeded on
        // the rising edge into `running` so the first frame of a run has an
        // anchor to be late against.
        const doneNow = seq.progress?.frames_done ?? null;
        const prevDone = get().lastFramesDone;
        let lastCaptureAtMs = get().lastCaptureAtMs;
        if (seq.state === "running"
            && (RUN_RISING_FROM.has(prevState)
                || (doneNow != null && prevDone != null && doneNow > prevDone))) {
          lastCaptureAtMs = Date.now();
        } else if (seq.state !== "running" && seq.state !== "paused"
                   && seq.state !== "aborting") {
          lastCaptureAtMs = null;          // no run: nothing can be overdue
        }
        set({ sequence: seq, runBanner, lastCaptureAtMs, lastFramesDone: doneNow });

        if (seq.state === "error" && prevState !== "error") {
          // NOV-9: the focal sequence-fatal toast now surfaces a plain cause +
          // first fix (diagnoseFailure) instead of just a humanized sentence,
          // and deep-links "How to fix →" into the matching Help topic when one
          // exists — falling back to "View log" for a generic (topic-null) failure.
          const diag = diagnoseFailure(seq.detail);
          get().enqueueToast({
            level: "error",
            kind: "sequence",
            ttl: 0,
            title: diag.title,
            detail: `${diag.cause} ${diag.fix}`,
            action: diag.topic
              ? { label: "How to fix →", kind: "openHelp", topic: diag.topic }
              : { label: "View log", kind: "openLog" },
          });
          announce(get(), diag.title, diag.fix);
        } else if (seq.state === "complete" && prevState !== "complete") {
          announce(
            get(),
            "Sequence complete",
            `${seq.progress?.frames_done ?? 0} frames captured`,
          );
          // no toast — the green panel is enough
        }

        // --- guarded auto-SELECT (monitor §3.2; resolves A3/B6) ---
        // NEVER a forced redirect. Only switch to Monitor on a rising-edge run
        // start when the pref is on AND the user is parked on the connect view
        // (a cold-start landing), never mid-workflow.
        if (
          seq.state === "running" &&
          RUN_RISING_FROM.has(prevState) &&
          get().autoMonitor &&
          get().view === "connect"
        ) {
          set({ view: "monitor" });
        }
        break;
      }
      case "polar":
        set({ polar: ev.data as unknown as PolarState });
        break;
      case "frames":
        // Its OWN event, not a field of `polar` (#176). The solve settings used
        // to ride the polar event, which the client applies wholesale — and
        // `start()` resets the session state to a dict with no solve_settings
        // key, so beginning an alignment reverted every Align face to mirrored
        // defaults while the engine kept solving at the operator's values.
        set((s) => ({ frameSettings: mergeFrames(s.frameSettings, ev.data) }));
        break;
      case "log": {
        const line = ev as unknown as LogLine;
        const level = (ev.data.level as string) ?? "info";
        const source = (ev.data.source as string) ?? "";
        set((s) => ({
          logs: [...s.logs.slice(-199), line],
          unseenError: s.unseenError + (!s.logOpen && level === "error" ? 1 : 0),
        }));
        // Errors → toast, EXCEPT sequence-fatal (handled by the sequence frame so
        // we don't double-surface). Warnings never toast.
        if (level === "error" && source !== "sequence") {
          get().enqueueToast({
            level: "error",
            title: humanizeLog({ message: ev.data.message as string, source }),
            source,
          });
        }
        break;
      }
    }
  },
}));

if (localStorage.getItem("astrodeck-night") === "1") {
  document.documentElement.classList.add("night");
}

// Reflect the persisted touch-sizing override onto the root class at load so the
// coarse-pointer size bumps honor an explicit on/off before first interaction.
applyTouchSizing(TOUCH_INIT.touchSizing);

// Apply the active mode's remembered brightness at load (F-dimmer single source).
// index.html's pre-paint script already set these so this is a no-op on first paint
// but guarantees the store and CSS vars agree if the pre-paint script is absent.
applyBrightnessVars(
  (localStorage.getItem("astrodeck-night") === "1"
    ? readBright(BRIGHT_NIGHT_KEY, 1)
    : readBright(BRIGHT_DAY_KEY, 1)),
);

// ============================================================================
// Narrow selector hooks (reliability §13). Subscribing to a single slice means a
// guide tick (which mutates only `guide`) re-renders only guide consumers, not
// the whole tree. Prefer these over a broad useStore() in components.
// ============================================================================
export const useView = () => useStore((s) => s.view);
export const useNight = () => useStore((s) => s.night);
export const useStatus = () => useStore((s) => s.status);
export const useMasters = () => useStore((s) => s.masters);
export const useSequence = () => useStore((s) => s.sequence);
export const useGuide = () => useStore((s) => s.guide);
export const useGuideAssistant = () => useStore((s) => s.guideAssistant);
export const useEgainLearn = () => useStore((s) => s.egainLearn);
export const useFilterOffsetsLearn = () => useStore((s) => s.filterOffsetsLearn);
// Same-night per-provider RMS windows (P5-T1) — see AppState.guideRmsByKind.
// useShallow so an unrelated guide tick under the SAME kind (which replaces
// the object at that key but leaves the other keys alone) doesn't spuriously
// re-render a consumer that only reads a different key.
export const useGuideRmsByKind = () =>
  useStore(useShallow((s) => s.guideRmsByKind));
export const useFocus = () => useStore((s) => s.focus);
export const useLastAutofocusResult = () => useStore((s) => s.lastAutofocusResult);
export const usePreview = () => useStore((s) => s.preview);
export const usePhotometry = () => useStore((s) => s.photometry);
export const usePolar = () => useStore((s) => s.polar);
/** What the next frame of this PURPOSE will be shot at — server truth, shared
 *  by every surface that shoots one. `useShallow` so an unrelated scope's
 *  update (the guide loop's, say) does not re-render the Capture panel. */
export const useFrameSettings = (scope: FrameScope): FrameSettings =>
  useStore(useShallow((s) => s.frameSettings[scope]));

/** A TEXT DRAFT over one numeric frame setting.
 *
 *  The single highest-risk part of moving these settings into the store, and
 *  the reason this exists rather than binding an input straight to a number:
 *  Capture's fields are strings ON PURPOSE. `isExposureInvalid` and the gain
 *  guard operate on the RAW string so that "", "1e9" and "-3" can block the
 *  shutter and mark the field. Bind those inputs to a number and all three
 *  guards silently stop guarding — a blank box becomes 0, and 0 s is the
 *  blank frame plus the misleading "few stars" that CAP-02 was filed for.
 *
 *  So: the draft holds the string, the store only ever holds a parsed number,
 *  and the two are reconciled at a COMMIT (blur / Enter / before a shot). The
 *  draft follows the server whenever the server moves and the box is not being
 *  fought over — same shape as the cooler set-point box.
 */
export function useFrameDraft(
  scope: FrameScope,
  field: "exposure_s" | "gain" | "offset" | "binning",
): { text: string; setText: (raw: string) => void; commit: () => void } {
  const server = useFrameSettings(scope)[field];
  const setFrameSettings = useStore((s) => s.setFrameSettings);
  const [text, setTextRaw] = useState(() => String(server));
  // What this box last agreed with the store about. Compared against the
  // server value so OUR OWN commit doesn't read as "the server moved".
  const agreed = useRef<number>(server);
  useEffect(() => {
    if (server !== agreed.current) {
      agreed.current = server;
      setTextRaw(String(server));
    }
  }, [server]);
  const commit = useCallback(() => {
    const n = Number(text);
    // An out-of-bounds draft is NOT committed and NOT reverted: the guards want
    // it on screen, marked, blocking the shutter. The bound is the SAME one the
    // shutter refuses on (`isExposureInvalid`, which exists because "1e9"
    // parses to a perfectly finite number), so a value this rejects and a value
    // the shot rejects can never be different values.
    if (text.trim() === "" || !Number.isFinite(n) || n < 0) return;
    if (field === "exposure_s" && isExposureValueInvalid(n)) return;
    if (n === agreed.current) return;
    agreed.current = n;
    setFrameSettings(scope, { [field]: n } as Partial<FrameSettings>);
  }, [text, scope, field, setFrameSettings]);
  return { text, setText: setTextRaw, commit };
}
export const useLogs = () => useStore((s) => s.logs);
export const useLastLight = () => useStore((s) => s.lastLight);

export const useToasts = () => useStore((s) => s.toasts);
export const useWsPhase = () => useStore((s) => s.wsPhase);
export const useWsConnected = () => useStore((s) => s.wsConnected);
export const useTelemetryStale = () => useStore((s) => s.telemetryStale);
export const useLinkDown = () => useStore((s) => s.wsPhase !== "up");
export const useNinaHealth = () => useStore((s) => s.ninaHealth);
export const useLogOpen = () => useStore((s) => s.logOpen);
export const useUnseenError = () => useStore((s) => s.unseenError);
export const useNotifyEnabled = () => useStore((s) => s.notifyEnabled);

export const useConfig = () => useStore((s) => s.config);
export const useUpdate = () => useStore((s) => s.update);
export const usePlan = () => useStore((s) => s.plan);

// ============================================================================
// RBAC / pluggable-backend narrow hooks (W1.6 / W2.5). The principal slice is a
// stable reference between polls (only loadPrincipal replaces it), so usePrincipal
// needs no useShallow. backend_links/boot_connect_failed live on the `status`
// object which is replaced wholesale every 2s — useBackendLinks uses useShallow so
// a poll that leaves the per-role tri-state unchanged doesn't re-render the grid.
// The cap-gate hooks themselves live in lib/caps.ts (useCan/useCapability).
// ============================================================================
export const usePrincipal = () => useStore((s) => s.principal);
export const useAuthMethods = () => useStore((s) => s.authMethods);
/** #117: which gate screen is standing in for the console ("open" when none).
 *  Read this — do NOT re-derive the gate — before showing a viewer anything the
 *  rig told us. */
export const useAuthGate = () => useStore((s) => s.authGate);
export const useCaps = () =>
  useStore(useShallow((s) => s.principal?.caps ?? []));
export const useBackendLinks = (): BackendLink[] =>
  useStore(useShallow((s) => s.status?.backend_links ?? []));
export const useBootConnectFailed = (): boolean =>
  useStore((s) => s.status?.boot_connect_failed ?? false);
// Per-capability provider resolution (native parity). `providers` rides on the
// `status` object (poll_status attaches it additively; not on RigStatus, so read
// via a cast). useShallow so only a status poll — not a guide/focus tick, which
// leaves `status` by-reference unchanged — re-renders the badges. null until the
// first status carrying `providers` lands.
export const useProviders = (): ProvidersStatus | null =>
  useStore(
    useShallow(
      (s) =>
        (s.status as (RigStatus & { providers?: ProvidersStatus }) | null)
          ?.providers ?? null,
    ),
  );
export const useFraming = () => useStore((s) => s.framing);
export const useAtlasHandoff = () => useStore((s) => s.atlasHandoff);
export const useAtlasBannerPending = () => useStore((s) => s.atlasBannerPending);
export const useSite = () => useStore((s) => s.site);
export const useEquipConnected = () => useStore((s) => s.equipConnected);
export const useConfirm = () => useStore((s) => s.confirm);

// Coach marks / first-run wizard (F-G + NOV-2) narrow hooks.
export const useHasSeen = (key: string) => useStore((s) => !!s.coachSeen[key]);
export const useWizardOpen = () => useStore((s) => s.wizardOpen);
export const useWizardStepId = () => useStore((s) => s.wizardStepId);

// ============================================================================
// Automation / safety narrow hooks (Batch-4b §2.2). Each subscribes to a single
// slice so a 2 s safety status refresh re-renders only the header chip, not the
// whole tree. `safety`/`alert` are object slices replaced by reference on update,
// so a primitive-returning consumer (e.g. is_safe) should derive from these.
// ============================================================================
export const useSafety = () => useStore((s) => s.safety);
export const useAlert = () => useStore((s) => s.alert);
export const useLastReportId = () => useStore((s) => s.lastReportId);
export const useWeather = () => useStore((s) => s.weather);
export const useWeatherAlertKey = () => useStore((s) => s.weatherAlertKey);

// ============================================================================
// Live-preview narrow hooks (live-preview spec §4.2). Each subscribes to one
// slice so a new frame re-renders only the stage/filmstrip, not the whole tree.
// ============================================================================
export const usePreviews = () => useStore((s) => s.previews);
/** Metadata for the frame the stage should show: pinned id if set, else live. */
export const useLivePreview = (): PreviewInfo | null =>
  useStore((s) => {
    const id = s.selectedPreviewId ?? s.livePreviewId;
    if (id == null) return null;
    // Search newest-first (the live/pinned frame is usually at/near the end).
    for (let i = s.previews.length - 1; i >= 0; i--) {
      if (s.previews[i].id === id) return s.previews[i];
    }
    return null;
  });
export const useSelectedPreviewId = () => useStore((s) => s.selectedPreviewId);
export const useLivePreviewId = () => useStore((s) => s.livePreviewId);
export const useViewport = () => useStore((s) => s.viewport);
export const useStretch = () => useStore((s) => s.stretch);
export const useOverlays = () => useStore((s) => s.overlays);
export const useHfrThresholds = () =>
  useStore(useShallow((s) => ({ good: s.hfrGood, warn: s.hfrWarn })));

// ============================================================================
// Monitor narrow hooks (monitor spec §2 "Selector discipline", resolves D20).
// `handleEvent` replaces the whole `status` object by reference every 2s poll,
// so any selector pulling a `status` sub-object MUST use shallow equality or it
// re-renders every tick. Primitive-returning selectors don't need useShallow.
// ============================================================================
export const useSeq = () => useStore((s) => s.sequence);
export const useGuideRecent = () =>
  useStore(useShallow((s) => s.guide?.recent ?? s.status?.guider?.recent ?? []));
export const useGuideRms = () => useStore(useShallow((s) => s.guide ?? s.status?.guider ?? null));
export const useCamera = () => useStore(useShallow((s) => s.status?.camera ?? null));
export const useMeridian = () => useStore(useShallow((s) => s.status?.meridian ?? null));
export const useMount = () => useStore(useShallow((s) => s.status?.mount ?? null));
/** `frame` = a PREVIEW arrived (the LIVE badge). `capture` = the rig's own
 *  frames_done advanced (the stall check). Two clocks, deliberately, because
 *  conflating them made a slow relay look like a dead camera (#206). */
export const useLiveness = () =>
  useStore(useShallow((s) => ({
    frame: s.lastFrameAtMs,
    capture: s.lastCaptureAtMs,
    guide: s.lastGuideAtMs,
  })));
export const useRunBanner = () => useStore(useShallow((s) => s.runBanner));
export const useAutoMonitor = () => useStore((s) => s.autoMonitor);

// ============================================================================
// Dimmer narrow hook (F-dimmer). Returns the ACTIVE mode's brightness — the store
// is the single writer of the CSS vars, so consumers (App reset hatch + Header
// steppers/slider) just read this and call setBrightness/resetBrightness.
// ============================================================================
export const useBrightness = (): number =>
  useStore((s) => (s.night ? s.brightNight : s.brightDay));
