export interface DeviceInfo {
  name: string;
  kind: string;
  connected: boolean;
}

// ---------------------------------------------------------------- navigation
// ViewName is the canonical session/view union. Lives here (not store.ts) so
// types that reference a view (e.g. CheckItem.fix.view) don't create an import
// cycle with the store. store.ts re-exports this for backward compatibility.
export type ViewName =
  | "connect"
  | "capture"
  | "focus"
  | "mount"
  | "polar"
  | "guide"
  | "sequence"
  | "power"
  | "settings"
  | "monitor"
  | "atlas"
  | "report";

export interface MountStatus {
  ra_hours: number;
  dec_deg: number;
  ra_str: string;
  dec_str: string;
  alt: number;
  az: number;
  tracking: boolean;
  parked: boolean;
  slewing: boolean;
}

export interface RigStatus {
  connected: Record<string, DeviceInfo>;
  looping: boolean;
  mode?: "none" | "sim" | "alpaca" | "nina";
  mount?: MountStatus;
  focuser?: { position: number; max: number; temperature: number | null };
  filterwheel?: { position: number; names: string[] };
  camera?: {
    temperature: number | null;
    can_cool: boolean;
    has_dew_heater?: boolean;
    width: number;
    height: number;
    max_gain: number;
    cooler?: CoolerInfo; // monitor (Batch-2) — null/absent when no cooler
  };
  guider?: GuideStats & { name: string };
  // --- guide-frame preview (SHARED lane; additive). Present only when the backend
  // can surface a guide-camera frame (GET /api/guide/frame.png). In NINA mode the
  // guider is PHD2 (status.guider) with no image; this optional field lets the UI
  // know a live guide-cam frame is fetchable without reading the preview itself.
  // The backend lane sets this (or extends status.guider) — coordinate the name. ---
  guide_camera?: { name: string; connected: boolean };
  // --- monitor (Batch-2; server-computed from HA for sim/Alpaca, device value for NINA) ---
  meridian?: MeridianInfo;
  // --- reliability (additive; old clients ignore) ---
  busy?: "slewing" | "solving" | "focusing" | "capturing" | null;
  nina_link?: {
    active: boolean;
    last_ok_age_s: number | null;   // seconds since last successful NINA HTTP call
    last_error: string | null;
    healthy: boolean;               // backend verdict (warming_up or age<=threshold)
    warming_up: boolean;            // true until first successful poll after bridge
  };
  // --- onboarding (additive) ---
  disk?: DiskInfo;
  // --- settings (additive; survives the 2s wholesale status replace) ---
  // Full poll_status site shape (6 fields) — superset of SiteInfo so the store
  // can refresh store.site (is_default/horizon_min_deg) live while connected.
  site?: {
    name: string;
    latitude: number;
    longitude: number;
    elevation_m: number;
    is_default: boolean;
    horizon_min_deg: number;
  };
  optics?: OpticsComputed;
}

export interface DiskInfo {
  free_gb: number;
  low: boolean;
  critical: boolean;
}

export interface GuideStats {
  guiding: boolean;
  rms_ra: number;
  rms_dec: number;
  rms_total: number;
  snr: number;
  recent: { t: number; ra: number; dec: number }[];
}

// SHARED lane (additive). Lightweight descriptor for the collapsible guide-cam
// preview (GuideFramePreview). The component fetches GET /api/guide/frame.png
// directly and self-manages its own loading/404 state, so this type is a small
// optional contract for any future store/aggregator that wants to surface guide
// frame availability without holding the image bytes. `ts` is server epoch seconds.
export interface GuideFrameInfo {
  available: boolean;
  ts?: number;
}

// ============================================================================
// LIVE PREVIEW (Batch-2, Pass-1) — owner: lane 2A (this lane).
// PreviewInfo is REWRITTEN: width/height → data_width/data_height plus the
// linear/full_well/display-dims/source/stretch fields. This is the program's
// only breaking rename (master §C-Risk-7). The single existing consumer
// (CaptureView header span) is rewritten by lane 2D; Monitor's PreviewTile (2E)
// uses /api/preview/{id}.png and the renamed fields. Be the contract SSOT.
// ============================================================================

export type PreviewSource = "sim" | "alpaca" | "nina";

export interface StarMark {
  x: number; // image-data pixel coords == frame.data space (NOT sensor space)
  y: number;
  hfr: number; // px
  ecc?: number; // 0..1, Pass 2 (optional; absent in Pass 1)
  theta?: number; // radians, Pass 2
}

export interface PreviewInfo {
  id: number;
  stats: { min: number; max: number; mean: number; median: number; std: number };
  histogram: number[]; // DISPLAY-domain bins (see histogram_domain)
  histogram_linear?: number[]; // linear bins, present only when data_is_linear
  histogram_domain: "display" | "linear";
  exposure_s: number;
  gain: number;
  binning: number;
  data_width: number; // == frame.data.shape[1]  (was `width`)
  data_height: number; // == frame.data.shape[0]  (was `height`)
  display_width: number; // encoded preview px after ≤1400 downscale
  display_height: number;
  mime: string; // "image/jpeg" | "image/png"
  source: PreviewSource;
  is_stretched: boolean; // true => no client re-stretch (NINA)
  data_is_linear: boolean; // false for NINA (decoded-from-render)
  has_lossless: boolean; // a lossless base is cached (paused/zoom)
  full_well: number | null; // driver-derived; null => clip mask disabled
  pixel_scale_arcsec?: number; // for arcsec HFR + scale bar, when known
  bayer_pattern?: string | null; // non-null => OSC frame (mono preview note)
  auto_levels: { black: number; mid: number; white: number }; // 0..1 in display domain
  saved_path?: string;
  saved_local?: boolean; // true only if saved_path is under CAPTURE_DIR (FITS dl ok)
  hfr?: number;
  stars?: number;
  star_list?: StarMark[];
  ts: number; // server epoch seconds (filmstrip age)
}

export interface Viewport {
  scale: number;
  x: number;
  y: number;
  fit: boolean;
}

export interface StretchParams {
  auto: boolean; // sticky; default true
  black: number; // display-domain 0..1, used in Manual
  mid: number;
  white: number;
  brightness: number; // simple primary slider, -1..1 (maps to mid)
  contrast: number; // display-only path (NINA), -1..1
  advancedOpen: boolean; // disclosure for B/M/W + curve, default false
}

export interface OverlayToggles {
  stars: boolean; // default false
  clip: boolean; // default false (only effective when linear+full_well)
  reticle: boolean; // default false (full reticle)
  centerMark: boolean; // default TRUE (subtle framing aid)
}

export interface FocusPoint {
  position: number;
  hfr: number;
}

export interface FocusEvent {
  state: "running" | "done" | "failed";
  points: FocusPoint[];
  best: { position: number; hfr: number | null } | null;
}

// ----------------------------------------------------------------- monitor (Batch-2)
// SequenceProgress is the named progress shape — server-computed magnitudes; the
// client derives wall-clock finish from its own clock (monitor spec §5). All ETA
// fields optional: absent => client renders "—"/low-confidence, elapsed only.
export interface SequenceProgress {
  frames_done: number;
  frames_total: number;
  percent: number; // 0..100
  elapsed_s: number; // server-computed, EXCLUDES paused time (§5)
  rejected: number; // engine already tracks _rejected

  eta_s?: number; // total predicted seconds to finish (authoritative magnitude)
  eta_confident?: boolean; // false until >= ETA_MIN_FRAMES real frames measured
  server_now_ms?: number; // server epoch at emit; client uses to offset-correct
  current_exposure_s?: number; // exposure of the step in flight (for sub-frame bar)
  frame_started_at_ms?: number; // server epoch when the in-flight exposure began
  // event-cost breakdown (transparency/debugging; not required by the UI):
  remaining_capture_s?: number;
  events_cost_s?: number; // sum of remaining dither/AF/flip costs
}

export interface SequenceState {
  // "nina_native" lets the Monitor show the honest "NINA is driving" state.
  state: "idle" | "running" | "paused" | "complete" | "aborted" | "error" | "nina_native";
  detail?: string;
  target?: string;
  target_index?: number;
  plan_name?: string;
  progress?: SequenceProgress;
  // --- automation (Batch-4b; additive — old clients ignore) ---
  // Autorun scheduling state attached while the engine is waiting for / running a
  // windowed target (schedule spec §1.9-C). `progress.rejected` already exists above.
  schedule?: {
    state: "waiting" | "ready" | "window_closed" | "never_rises";
    reason: string;
    eta_s: number;
    start_ts?: number; // unix seconds (resolved window open)
    stop_ts?: number;  // unix seconds (resolved window close)
  };
  // Live ETA chips (engine §1.9-E): meridian-flip ETA is SECONDS (ttf*3600); the
  // sensor temp / guide RMS are echoed for the run-time chips without a status poll.
  live?: { meridian_eta_s?: number; sensor_temp_c?: number; guide_rms?: number };
  // Terminal reason — drives the run-complete Badge + Report end-reason icon.
  end_reason?: "complete" | "aborted" | "error" | "unsafe" | "dawn_cutoff";
}

export interface CoolerInfo {
  // under RigStatus.camera.cooler
  on: boolean;
  power: number | null; // 0..100 %, null if camera can't report power
  target_c: number | null;
  at_target: boolean; // |temp - target| <= COOLER_AT_TARGET_C (shared 1.0)
  can_report_power: boolean; // false => ThermometerBar degrades to on/off + target
}

export type MeridianStatus =
  | "n_a_fork" // mount reports no flip needed (fork/non-GEM): informational
  | "flip_disabled" // plan.meridian_flip === false on a GEM: WARNING (pier risk)
  | "counting" // hours_to_flip is a real positive number
  | "due" // hours_to_flip <= 0
  | "unknown"; // can't determine (no mount / no data)

export interface MeridianInfo {
  // top-level on RigStatus
  status: MeridianStatus;
  hours_to_flip: number | null; // null unless status === "counting" | "due"
  flip_enabled: boolean; // plan.meridian_flip AND mount is GEM
  pier_side: "east" | "west" | "unknown";
}

// Cold-load aggregator response (monitor §8) for GET /api/monitor/snapshot.
export interface MonitorSnapshot {
  sequence: SequenceState;
  status: RigStatus;
  preview_id: number | null;
  guide_recent: { t: number; ra: number; dec: number }[];
}

export interface LogLine {
  type: string;
  data: { level: string; message: string; source: string };
  ts: number;
}

export interface PolarState {
  state: "idle" | "running" | "paused" | "done" | "error";
  az_error: number;   // signed arcmin
  alt_error: number;  // signed arcmin
  total_error: number;
  progress: number;
  message: string;
  source: "nina" | "sim" | null;
}

export interface CatalogEntry {
  id: string;
  name: string;
  type: string;
  ra_hours: number;
  dec_deg: number;
  mag: number;
  size_arcmin: number;
  alt: number;
  az: number;
}

export interface SwitchPort {
  id: number;
  name: string;
  can_write: boolean;
  is_boolean: boolean;
  value: number;
  min: number;
  max: number;
  unit: string;
}

export interface AlpacaServer {
  address: string;
  port: number;
  devices: { DeviceName: string; DeviceType: string; DeviceNumber: number }[];
}

export interface NinaInstance {
  host: string;
  hostname: string | null;
  port: number;
  url: string;
  api_version: string;
  nina_version: string | null;
  devices: Record<string, string>;
}

export interface ExposureStep {
  filter: string | null;
  exposure_s: number;
  gain: number;
  offset: number;
  binning: number;
  count: number;
  frame_type: string;
}

export interface Target {
  name: string;
  ra_hours: number;
  dec_deg: number;
  center: boolean;
  autofocus_first: boolean;
  calibration: boolean;
  steps: ExposureStep[];
  // --- atlas (additive; nullable so existing plans deserialize unchanged) ---
  rotation_deg?: number;    // target camera angle (PA) — guidance only, no rotator in rig
  mosaic_group?: string;    // e.g. "M31" to group panels in the Plan UI
  // --- automation (Batch-4b; additive — backfilled with defaultSchedule() so old
  //     plans deserialize unchanged, see store.defaultSchedule / C1-27). ---
  schedule?: Schedule;
}

export interface SequencePlan {
  name: string;
  targets: Target[];
  guide: boolean;
  dither_every: number;
  dither_pixels: number;
  autofocus_every: number;
  cool_to: number | null;
  cool_timeout_s: number;
  apply_filter_offsets: boolean;
  refocus_on_temp_delta_c: number;
  meridian_flip: boolean;
  recover_guiding: boolean;
  hfr_reject_factor: number;
  park_when_done: boolean;
  warm_cooler_when_done: boolean;
  // --- automation (Batch-4b; additive — safety/escalation are GLOBAL in config,
  //     the plan carries only the master toggle + a meridian-flip warning lead). ---
  safety_check?: boolean;          // honor the configured SafetyMonitor + floor
  meridian_flip_warn_min?: number; // lead time for the live meridian-flip ETA chip
}

// ============================================================================
// BATCH-1 ADDITIVE CONTRACTS (master-plan §A.1) — the one true shape.
// Other lanes compile against these. All additive; no existing field changes.
// ============================================================================

// ---------------------------------------------------------------- reliability
export type ToastLevel = "error" | "warning" | "info" | "success";

export interface Toast {
  id: number;          // monotonic, also React key
  level: ToastLevel;
  title: string;       // human, short, sentence-case (e.g. "Sequence failed")
  detail?: string;     // optional human second line (e.g. a suggested action)
  kind: "generic" | "sequence";  // "sequence" toasts are the de-duped focal one
  createdAt: number;   // Date.now() — NEVER mutated on coalesce
  ttl: number;         // ms before auto-dismiss; 0 = sticky (sequence-fatal only)
  count: number;       // coalesce counter for identical generic toasts
  action?: { label: string; kind: "openLog" };  // optional inline action
  source?: string;     // originating log source (for dedupe / debugging)
}

export type WsPhase = "connecting" | "up" | "down" | "reconnecting";

export type NinaState = "ok" | "stale" | "error" | "down" | "warming" | "na";

export interface NinaHealth {
  active: boolean;          // mode === "nina" && nina_link.active
  ageMs: number | null;     // ms since backend's last successful NINA call
  state: NinaState;
  lastError?: string | null;
}

// ---------------------------------------------------- settings: site + optics
export interface Site {
  name: string;
  latitude: number;   // +N (stored signed)
  longitude: number;  // +E (East-positive; matches coords.lst_hours)
  elevation_m: number;
}

export interface Optics {
  focal_length_mm: number;
  pixel_size_um: number;
  sensor_width_px: number;
  sensor_height_px: number;
  auto_from_camera: boolean;
}

export interface OpticsComputed {
  have_optics: boolean;
  source: "config" | "camera" | "mixed" | "none";
  focal_length_mm: number;
  pixel_size_um: number;
  sensor_width_px: number;
  sensor_height_px: number;
  image_scale_arcsec_px: number | null;
  fov_w_deg: number | null;
  fov_h_deg: number | null;
  fov_diag_deg: number | null;
}

export interface AppConfig {
  version: number;
  site: Site;
  optics: Optics;
  // Present on the REST GET/POST payload; OMITTED from the WS `hello` bootstrap
  // (hub.summary() seeds config without it, refreshed by the first `config`
  // event), so it must be optional to match the bootstrap reality.
  optics_computed?: OpticsComputed;
  active_profile_id: string | null;
  // --- automation (Batch-4b; additive — appended to the EXISTING config-backed
  //     AppConfig. Global safety/escalation/alerts live here, NOT on the plan;
  //     `deadman_url` is the external healthcheck ping target. Tokens are blanked
  //     server-side via ConfigStore.redacted() before they reach the client. ---
  safety: SafetyConfig;
  escalation: EscalationConfig;
  alerts: AlertSink[];
  deadman_url: string;
}

// ============================================================================
// AUTOMATION / SAFETY (Batch-4b; design spec §2.1). The one true shape —
// downstream lanes (Settings, Sequence schedule sub-panel, Report) compile
// against these. All additive; reuse the existing AppConfig/Target/SequenceState
// which were EXTENDED above. Tokens (telegram bot token) are never sent to the
// client — ConfigStore.redacted() blanks them, hence no `token` field on AlertSink.
// ============================================================================

// ---------------------------------------------------------------- safety device
export interface SafetyReading {
  is_safe: boolean;
  reason: string;                       // human string when unsafe (e.g. "cloud sensor")
  source: string;                       // device name
  detail?: Record<string, number>;      // optional sensor readouts
  stale: boolean;                       // true when the read timed out / device dropped
  ts: number;                           // server epoch seconds
}

export interface SafetyState {
  connected: boolean;
  reading: SafetyReading | null;
  streak: number;                       // consecutive unsafe reads (engine streak)
}

// ----------------------------------------------------------- per-target schedule
// Structured controls only — NO token mini-language (C1-24). Backfilled onto every
// Target with defaultSchedule() so old plans deserialize unchanged (C1-27).
export interface Schedule {
  start_mode: "now" | "dusk" | "dawn" | "time";
  start_offset_min: number;             // ± minutes relative to dusk/dawn
  start_time: string | null;            // "HH:MM" when start_mode === "time"
  min_altitude_deg: number;             // per-target START gate (target-alt). 0 = none
  stop_mode: "none" | "dawn" | "time";
  stop_offset_min: number;
  stop_time: string | null;
  max_run_min: number;                  // 0 = no cap
  on_missed: "wait" | "skip";           // default "wait" (C1-25)
}

// ------------------------------------------------------------ alerting / config
// `token` is intentionally absent — the server blanks it; the client never holds it.
export interface AlertSink {
  id: string;
  kind: "ntfy" | "webhook" | "telegram";
  enabled: boolean;
  url: string;                          // ntfy topic url / webhook url
  chat_id?: string;                     // telegram chat id
  min_level: "warning" | "error";
  events: string[];                     // ["run_start","run_end","safety","error",...]
  verified: boolean;                    // true only after a successful round-trip test
  heartbeat_min: number;                // 0 = off; periodic progress ping cadence
}

export interface SafetyConfig {
  enabled: boolean;
  preset: "backyard" | "remote" | "custom";
  min_alt_deg: number;                  // global pier-collision floor (mount-alt). 0 = off
  horizon: [number, number][] | null;   // sorted (az,alt) control points
  enforce_pier_limits: boolean;         // only settable when mount reports pier side
  twilight_deg: number;                 // nautical −12 default (C1-26)
  on_unsafe: "abort_park_warm" | "park" | "pause" | "warn";
  unsafe_consecutive: number;
  resume_when_safe: boolean;
  resume_safe_consecutive: number;
  max_pause_min: number;                // 0 = no cap; escalates to park on timeout
}

export interface EscalationConfig {
  require_cooling: boolean;
  cooling_action: "warn" | "abort" | "skip";
  require_guiding: boolean;
  guiding_action: "warn" | "abort" | "skip";
  af_failure_action: "warn" | "abort" | "skip";
  hfr_reject_action: "warn" | "discard" | "retake";  // retake = Advanced-only
  hfr_retake_limit_per_target: number;               // cap per target (C1-7)
  no_progress_watchdog_s: number;                     // 0 = off
  reconnect_resume: boolean;                          // Alpaca-only; off by default
  reconnect_retries: number;
}

// SiteConfig is the persisted lat/lon/elevation shape the automation config carries.
// (Distinct from the existing Site interface, which is the settings-panel view, and
// from SiteInfo, which is the live status view.) Mirrors backend config.SiteConfig.
export interface SiteConfig {
  latitude: number;
  longitude: number;
  elevation_m: number;
}

// ----------------------------------------------------------------- session report
export interface FilterBreakdown {
  filter: string;
  frames: number;
  rejected: number;
  integration_s: number;
  hfr_median: number | null;
}

export interface TargetBreakdown {
  name: string;
  frames: number;
  rejected: number;
  integration_s: number;
  by_filter: FilterBreakdown[];
}

// List-row summary (GET /api/reports) — header fields only, no per-frame payload.
export interface SessionReportSummary {
  id: string;                           // "<plan>-<YYYYMMDD-HHMMSS>"
  plan_name: string;
  started_at: number;                   // unix seconds
  ended_at: number | null;
  end_reason: string | null;            // complete|aborted|error|unsafe|dawn_cutoff
  frames_captured: number;
  frames_rejected: number;
  integration_s: number;
}

// Full detail (GET /api/reports/{id}) — trends DERIVED from frames at read time.
export interface SessionReport extends SessionReportSummary {
  by_filter: FilterBreakdown[];         // plan-wide per-filter totals (headline)
  targets: TargetBreakdown[];
  safety_events: { ts: number; reason: string; action: string }[];
  trends: {
    hfr: [number, number][];            // (ts, value) pairs
    temp: [number, number][];
    rms: [number, number][];
  };
}

export interface SkyInfo {
  sun_alt_deg: number;
  dark_window: { start_iso: string; end_iso: string } | null;
  place_hint: string;
  lst_str: string;
}

// ============================================================================
// ATLAS — Sky Atlas + Framing + Mosaic + Visibility (design spec §4.1).
// Owner A freezes these; lanes B–E compile against them. All additive.
// NOTE: `Optics` is the EXISTING px-suffixed interface above (sensor_width_px /
// sensor_height_px) — these types reference it, they do NOT redefine it.
// ============================================================================

export interface FovRect {                 // single sensor footprint
  ra_hours: number;
  dec_deg: number;
  fov_x_deg: number;
  fov_y_deg: number;
  rotation_deg: number;                    // position angle, N-up E-left
}

export interface MosaicSpec {
  ra_hours: number;
  dec_deg: number;
  rows: number;                            // 1..10
  cols: number;                            // 1..10
  overlap: number;                         // 0..0.5 (default 0.25)
  rotation_deg: number;                    // PA applied to whole mosaic
  fov_x_deg: number;                       // single-frame FOV at bin 1
  fov_y_deg: number;
}

export interface MosaicPanel {
  row: number;
  col: number;
  ra_hours: number;                        // server returns ra already %24-wrapped
  dec_deg: number;
  rotation_deg: number;
  transit_alt?: number;                    // peak alt tonight (NOT instantaneous "now" alt)
}

export interface MosaicResult {
  panels: MosaicPanel[];
  total_fov_x_deg: number;                 // tangent-plane extent
  total_fov_y_deg: number;
  frame_fov_x_deg: number;
  frame_fov_y_deg: number;
  pixel_scale_arcsec: number;
}

export interface FramingSession {
  target?: CatalogEntry;                   // origin object (undefined = free-roam)
  center: { ra_hours: number; dec_deg: number };
  rotation_deg: number;
  survey: string;                          // "CDS/P/DSS2/color" | "CDS/P/DSS2/red" | "CDS/P/2MASS/color"
  stretch: "linear" | "asinh";
  fovZoomDeg: number;                      // survey crop angular width
  mosaic: { rows: number; cols: number; overlap: number };
  panels: MosaicPanel[];                   // generated; length 1 when 1x1
  freeroamId?: string;                     // stable mosaic-group id for free-roam (no target) sends
}

export interface MoonInfo {
  illumination: number;                    // 0..1
  phase_name: string;                      // "Waning Gibbous"
  alt: number;
  az: number;                              // at session time
  separation_deg: number;                  // from target
  rise_unix: number | null;                // tonight, null if always up/down
  set_unix: number | null;
}

export interface VisibilitySample {
  t_unix: number;
  alt: number;
  moon_alt: number;
  sun_alt: number;
}

export interface VisibilityNight {
  date: string;
  transit_unix: number;
  transit_alt: number;                                 // peak within dark window (see §9)
  transit_in_daylight: boolean;                        // true if geometric transit is in daylight
  dark_start_unix: number | null;                      // astro-dark start (null if none)
  dark_end_unix: number | null;
  darkness_kind: "astronomical" | "nautical" | "none"; // fallback ladder
  samples: VisibilitySample[];
  moon: MoonInfo;
  best_window: { start_unix: number; end_unix: number; mean_alt: number } | null;
  alt_limit_deg: number;                               // horizon limit (default 30)
  never_rises_above_limit: boolean;
}

export interface VisibilityTarget {
  name: string;
  ra_hours: number;
  dec_deg: number;
  transit_unix: number;
  max_alt: number;
  best_window: { start_unix: number; end_unix: number } | null;
  moon_sep_deg: number;
}

// ---------------------------------------------------- onboarding: site + checks
// SiteInfo is the LIVE/status view of the site (distinct from the persisted Site).
export interface SiteInfo {
  latitude: number;
  longitude: number;
  is_default: boolean;
  horizon_min_deg: number;
}

export interface PreflightAlt {
  alt: number | null;
  az: number | null;
  verdict: "ok" | "low" | "below" | "unknown";
  horizon_min_deg: number;
  site_is_default: boolean;
  sets_in_min?: number | null;
}

export type CheckStatus = "ok" | "warn" | "blocked" | "checking" | "skipped" | "disabled";

export interface CheckItem {
  id: string;
  label: string;
  status: CheckStatus;
  word: string;               // redundant text token, always set
  detail?: { value: string; unit?: string };
  help?: string;              // HelpKey (the HELP map lives in the onboarding lane)
  fix?: { label: string; view?: ViewName; onClick?: () => void | Promise<void>; inPlace?: boolean };
}

// --------------------------------------------------- design-system display primitives
export type LedState = "off" | "on" | "warn" | "bad" | "busy";
export type Tone = "good" | "warn" | "bad";

// -------------------------------------------------------- profiles / plans (frozen)
export interface ProfileDevice {
  role: string;
  backend: "alpaca" | "nina";
  host: string;
  port: number;
  dev_type: string;
  dev_num: number;
  name: string;
}

export interface Profile {
  id: string;
  name: string;
  devices: ProfileDevice[];
  nina_host: string | null;
  nina_port: number;
  phd2_host: string | null;
  phd2_port: number;
  optics: Optics | null;
  site_name: string | null;
}

export interface ProfileRow {
  id: string;
  name: string;
  mode: "alpaca" | "nina" | "mixed" | "empty";
  devices_count: number;
  site_name: string | null;
  active: boolean;
}

export interface ApplyResult {
  summary: RigStatus;
  results: { role: string; ok: boolean; error?: string }[];
  connected: number;
  total: number;
}

export interface PlanRow {
  id: string;
  name: string;
  frames: number;
  integration_min: number;
  targets: number;
  mtime: number;
}

// ------------------------------------------------------------- touch ergonomics
export type SlewRateId = "pulse" | "fine" | "set";

export interface SlewRateOption {
  id: SlewRateId;
  label: string;
  rateDegS: number;
  pulseMs?: number;
}

export interface MountCaps {
  max_rate_deg_s?: number;
}

export interface TouchSettings {
  hapticsEnabled: boolean;
  touchSizing: "auto" | "on" | "off";
  reverseRa: boolean;
  reverseDec: boolean;
  autoLockMs: null | 180000 | 300000;
}
