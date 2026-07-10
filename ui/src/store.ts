import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { ReactNode } from "react";
import type {
  AppConfig,
  AuthMethods,
  BackendLink,
  CatalogEntry,
  FocusEvent,
  FramingSession,
  GuideStats,
  LogLine,
  NinaHealth,
  OverlayToggles,
  PolarState,
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
  UpdateStatus,
  ViewName,
  Viewport,
  WsPhase,
} from "./types";
import { deriveNinaHealth } from "./lib/health";
import { humanizeLog, humanizeSeqError } from "./lib/humanize";
import { notifyAndBeep, requestNotifyPermission } from "./lib/notify";
import { haptics } from "./lib/haptics";
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
}
export interface ProvidersStatus {
  autofocus?: ProviderChoiceView;
  polar_align?: ProviderChoiceView;
  solve?: ProviderChoiceView;
}

// ---------------------------------------------------------------- toast policy
const TOAST_MAX = 3; // hard cap; on phone effectively 1-2
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
  resolve: (ok: boolean) => void;
}

// ------------------------------------------------------------ default plan/state
const EMPTY_SEQUENCE: SequenceState = { state: "idle" };
const EMPTY_POLAR: PolarState = {
  state: "idle",
  az_error: 0,
  alt_error: 0,
  total_error: 0,
  progress: 0,
  message: "",
  source: null,
};
const EMPTY_NINA_HEALTH: NinaHealth = { active: false, ageMs: null, state: "na", lastError: null };

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
    park_when_done: false,
    warm_cooler_when_done: false,
  };
}

function loadPlan(): SequencePlan {
  try {
    const raw = localStorage.getItem(PLAN_KEY);
    if (raw) return { ...defaultPlan(), ...(JSON.parse(raw) as Partial<SequencePlan>) };
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

// ----------------------------------------------------------------- dimmer state
// Global brightness dimmer — STORE-OWNED single source (F-dimmer). Day and night
// each remember their own brightness; toggling mode swaps to the other memory
// (design-system §7.5). The store applies the CSS vars (`--screen-brightness` +
// `--scrim-opacity`) on every change and at init, so App and HeaderControls both
// just subscribe to the slice — no MutationObserver/getComputedStyle round-trip.
// Mirrors index.html's pre-paint script so first paint never flashes.
const BRIGHT_DAY_KEY = "astrodeck-bright-day";
const BRIGHT_NIGHT_KEY = "astrodeck-bright-night";

const clampBright = (v: number): number => Math.min(1, Math.max(0.08, v));

function readBright(key: string, def: number): number {
  try {
    const n = Number(localStorage.getItem(key));
    return Number.isFinite(n) && n > 0 ? clampBright(n) : def;
  } catch {
    return def;
  }
}

/** Write the dimmer CSS vars onto <html>. Scrim deepens past what filter:brightness
 *  can do (OLED black-pixel safe). The single writer of these vars (DIMMER CONTRACT). */
function applyBrightnessVars(v: number): void {
  const b = clampBright(v);
  const d = document.documentElement;
  d.style.setProperty("--screen-brightness", String(b));
  d.style.setProperty("--scrim-opacity", String(Math.min(0.92, Math.max(0, 1 - b * 1.05))));
}

interface AppState {
  // --- core view/session ---
  view: ViewName;
  night: boolean;
  status: RigStatus | null;
  preview: PreviewInfo | null;
  focus: FocusEvent | null;
  guide: (GuideStats & { name?: string }) | null;
  sequence: SequenceState;
  polar: PolarState;
  logs: LogLine[];

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
  plan: SequencePlan; // atlas SSOT; setPlan persists localStorage in the setter
  editorDirty: boolean;
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
  lastFrameAtMs: number | null; // set on each preview event (stall/LIVE detection)
  lastGuideAtMs: number | null; // set on each guide event
  autoMonitor: boolean; // localStorage pref, default false (auto-SELECT only)
  runBanner: RunBanner; // persistent "Sequence running — open Live" banner

  // --- touch ergonomics (Batch-3 3B; master §A.2 / touch §2.2) ---
  locked: boolean; // touch-guard engaged; NEVER persisted
  lockAvailable: boolean; // true: reliability sequence-error render shipped (R11)
  monitorAwake: boolean; // explicit wake-lock-as-monitor toggle (decoupled from locked, R13)
  touch: TouchSettings; // haptics / sizing / reverse-axis / auto-lock prefs

  // --- dimmer (Batch-3 F-dimmer; design-system §7.5) ---
  brightDay: number; // remembered day brightness (0.08..1), persisted
  brightNight: number; // remembered night brightness (0.08..1), persisted
  // The ACTIVE brightness is derived from `night` via useBrightness(); the store
  // applies the CSS vars whenever either value or `night` changes (single source).

  // --- backward-compat shims ---
  wsConnected: boolean; // = wsPhase === "up"

  // --- actions: core ---
  setView: (v: ViewName) => void;
  toggleNight: () => void;
  handleEvent: (ev: { type: string; data: Record<string, unknown>; ts: number }) => void;

  // --- actions: config / plan / site ---
  loadConfig: () => Promise<void>;
  // GET /api/update/status → hydrate the `update` slice (boot + after a check).
  loadUpdate: () => Promise<void>;
  // GET /api/me → principal. On ApiError 401 (fail-closed server resolution) set a
  // viewer sentinel {role:"viewer",email:null,caps:[]} so the UI degrades to
  // read-only instead of hanging unresolved. Call from ws.ts onopen next to
  // loadConfig(), and re-call after sign-in / sign-out.
  loadPrincipal: () => Promise<void>;
  // GET /api/auth/methods → the login-screen signal (W2.6). Best-effort: a
  // failure leaves the prior value (or null) so a transient blip never strips the
  // gate. Call at boot next to loadConfig/loadPrincipal and after auth changes.
  loadAuthMethods: () => Promise<void>;
  setPlan: (p: SequencePlan, dirty?: boolean) => void;
  setSiteDirty: (b: boolean) => void;
  setOpticsDirty: (b: boolean) => void;
  setSite: (s: SiteInfo) => void;

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

// Hydrate the persisted preview toggles once at module load (mirrors loadPlan()).
const PREVIEW_PERSISTED = loadPreviewPersisted();

// Hydrate the touch prefs once at module load; mirror haptics.enabled + the root
// touch-sizing class so first paint matches the persisted prefs (touch §2.2).
const TOUCH_INIT = loadTouch();
haptics.enabled = TOUCH_INIT.hapticsEnabled;

export const useStore = create<AppState>((set, get) => ({
  // --- core ---
  view: "connect",
  night: localStorage.getItem("astrodeck-night") === "1",
  status: null,
  preview: null,
  focus: null,
  guide: null,
  sequence: EMPTY_SEQUENCE,
  polar: EMPTY_POLAR,
  logs: [],

  // --- config / plan ---
  config: null,
  update: null,
  principal: null, // unresolved → fail-closed viewer until loadPrincipal()
  authMethods: null, // unresolved → no login gate until loadAuthMethods() lands
  plan: loadPlan(),
  editorDirty: false,
  siteDirty: false,
  opticsDirty: false,

  // --- atlas / framing ---
  framing: null,
  atlasHandoff: 0,
  atlasBannerPending: null,

  // --- site ---
  site: null,
  equipConnected: false,

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
  lastGuideAtMs: null,
  autoMonitor: localStorage.getItem(AUTO_MONITOR_KEY) === "1",
  runBanner: null,

  // --- touch ---
  locked: false, // never persisted
  lockAvailable: true, // reliability sequence-error render shipped (R11) → lock enabled
  monitorAwake: false,
  touch: TOUCH_INIT,

  // --- dimmer (F-dimmer) ---
  brightDay: readBright(BRIGHT_DAY_KEY, 1),
  brightNight: readBright(BRIGHT_NIGHT_KEY, 0.45),

  // --- compat ---
  wsConnected: false,

  // --------------------------------------------------------------- core actions
  setView: (v) => set({ view: v }),

  toggleNight: () => {
    const night = !get().night;
    localStorage.setItem("astrodeck-night", night ? "1" : "0");
    document.documentElement.classList.toggle("night", night);
    set({ night });
    // Re-assert the now-active mode's remembered brightness (F-dimmer: the store is
    // the single writer of the dimmer vars — no MutationObserver round-trip).
    applyBrightnessVars(night ? get().brightNight : get().brightDay);
  },

  // --------------------------------------------------------- config/plan/site
  loadConfig: async () => {
    try {
      const config = await api.get<AppConfig>("/api/config");
      set({ config });
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

  setPlan: (p, dirty = true) => {
    try {
      localStorage.setItem(PLAN_KEY, JSON.stringify(p));
    } catch {
      /* quota / unavailable — keep in-memory */
    }
    set({ plan: p, editorDirty: dirty });
  },

  setSiteDirty: (b) => set({ siteDirty: b }),
  setOpticsDirty: (b) => set({ opticsDirty: b }),
  setSite: (s) => set({ site: s }),

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
      // flashes a full-color JPEG at a dark-adapted eye.
      survey: get().night ? FRAMING_NIGHT_SURVEY : FRAMING_DEFAULT_SURVEY,
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
    const nextPlan: SequencePlan = { ...plan, targets: [...kept, ...targets] };
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
            notifyAndBeep(
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
      const now = Date.now();
      const level = input.level;
      const kind = input.kind ?? "generic";
      let toasts = s.toasts;

      if (kind === "sequence") {
        // exactly one focal sequence toast — replace any prior
        toasts = toasts.filter((t) => t.kind !== "sequence");
      } else {
        const dupe = toasts.find(
          (t) =>
            t.kind === "generic" &&
            t.title === input.title &&
            t.level === level &&
            now - t.createdAt < 5000,
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
        // drop oldest dismissible generic first; never drop the focal sequence toast
        const victim = next.find((t) => t.ttl > 0 && t.kind === "generic")?.id ?? next[0].id;
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
    set(() => {
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
    switch (ev.type) {
      case "status": {
        const status = ev.data as unknown as RigStatus;
        const equipConnected = status.mode !== undefined && status.mode !== "none";
        set({ status, ninaHealth: deriveNinaHealth(status), equipConnected });
        // poll_status carries the full 6-field site; keep store.site fresh while
        // connected so is_default/horizon_min_deg don't go stale until reconnect.
        if (status.site) set({ site: status.site });
        break;
      }
      case "hello": {
        // cold hydration snapshot — backend sends {"data": hub.summary()} with no
        // wrapper, so treat data directly as the summary dict (may carry site / mode
        // and, Batch-4b, a `safety` snapshot + redacted `config`).
        const summary = ev.data as unknown as Partial<RigStatus> & {
          site?: SiteInfo;
          safety?: SafetyState;
          config?: AppConfig;
        };
        if (summary.site) set({ site: summary.site });
        if (summary.mode !== undefined) {
          set({ equipConnected: summary.mode !== "none" });
        }
        // Hydrate automation state on connect so the header safety chip + Settings
        // render correctly without waiting for the first periodic event (§2.2).
        if (summary.safety) set({ safety: summary.safety });
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
        set((s) => ({
          safety: {
            streak: s.safety?.streak ?? 0,
            reading,
            connected: !reading.stale,
          },
        }));
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
      case "preview": {
        const p = ev.data as unknown as PreviewInfo;
        // Keep the single-frame `preview` (existing consumers) AND push into the
        // ring + stamp the liveness timestamp the Monitor's LIVE/STALL uses.
        set({ preview: p, lastFrameAtMs: Date.now() });
        get().pushPreview(p);
        break;
      }
      case "focus":
        set({ focus: ev.data as unknown as FocusEvent });
        break;
      case "guide":
        set({ guide: ev.data as unknown as GuideStats, lastGuideAtMs: Date.now() });
        break;
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
        } else if (seq.state === "paused") {
          if (runBanner) runBanner = { ...runBanner, percent };
        } else {
          // idle / complete / aborted / error / nina_native → clear the banner.
          runBanner = null;
        }
        set({ sequence: seq, runBanner });

        if (seq.state === "error" && prevState !== "error") {
          get().enqueueToast({
            level: "error",
            kind: "sequence",
            ttl: 0,
            title: "Sequence failed",
            detail: humanizeSeqError(seq.detail),
            action: { label: "View log", kind: "openLog" },
          });
          notifyAndBeep(get(), "Sequence failed", humanizeSeqError(seq.detail));
        } else if (seq.state === "complete" && prevState !== "complete") {
          notifyAndBeep(
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
    ? readBright(BRIGHT_NIGHT_KEY, 0.45)
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
export const useSequence = () => useStore((s) => s.sequence);
export const useGuide = () => useStore((s) => s.guide);
export const useFocus = () => useStore((s) => s.focus);
export const usePreview = () => useStore((s) => s.preview);
export const usePolar = () => useStore((s) => s.polar);
export const useLogs = () => useStore((s) => s.logs);

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

// ============================================================================
// Automation / safety narrow hooks (Batch-4b §2.2). Each subscribes to a single
// slice so a 2 s safety status refresh re-renders only the header chip, not the
// whole tree. `safety`/`alert` are object slices replaced by reference on update,
// so a primitive-returning consumer (e.g. is_safe) should derive from these.
// ============================================================================
export const useSafety = () => useStore((s) => s.safety);
export const useAlert = () => useStore((s) => s.alert);
export const useLastReportId = () => useStore((s) => s.lastReportId);

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
export const useLiveness = () =>
  useStore(useShallow((s) => ({ frame: s.lastFrameAtMs, guide: s.lastGuideAtMs })));
export const useRunBanner = () => useStore(useShallow((s) => s.runBanner));
export const useAutoMonitor = () => useStore((s) => s.autoMonitor);

// ============================================================================
// Dimmer narrow hook (F-dimmer). Returns the ACTIVE mode's brightness — the store
// is the single writer of the CSS vars, so consumers (App reset hatch + Header
// steppers/slider) just read this and call setBrightness/resetBrightness.
// ============================================================================
export const useBrightness = (): number =>
  useStore((s) => (s.night ? s.brightNight : s.brightDay));
