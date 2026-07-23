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
  // Multi-rate tracking (2026-07-21): the active drive rate + whether this mount
  // supports changing it. Absent/false on backends that don't (control hidden).
  tracking_rate?: "sidereal" | "lunar" | "solar";
  can_set_tracking_rate?: boolean;
}

export interface RigStatus {
  connected: Record<string, DeviceInfo>;
  looping: boolean;
  mode?: "none" | "sim" | "alpaca" | "nina";
  mount?: MountStatus;
  focuser?: { position: number; max: number; temperature: number | null };
  filterwheel?: { position: number; names: string[]; offsets?: number[] };
  rotator?: RotatorStatus;
  camera?: {
    temperature: number | null;
    can_cool: boolean;
    has_dew_heater?: boolean;
    width: number;
    height: number;
    max_gain: number;
    max_bin?: number; // UX-27: bin ceiling; UI offers 1..max_bin (default 4)
    cooler?: CoolerInfo; // monitor (Batch-2) — null/absent when no cooler
    // photometry/SNR design Task 7: e-/ADU at the current gain (native adapters
    // only; 0/absent = unknown). Optional — older servers omit the field.
    egain?: number;
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
  // --- pluggable backends (W1.6; additive — old clients ignore). Present on
  //     every poll AND `hello`. `backend_links` is [] until a RigSpec/profile
  //     connect happened (the legacy connect_* paths leave it empty). ---
  backend_links?: BackendLink[];
  boot_connect_failed?: boolean;
  // --- onboarding (additive) ---
  disk?: DiskInfo;
  // --- automation (Batch-4b; additive). The own-cadence SafetyMonitor poller's
  //     latest cached reading, forwarded on EVERY 2s poll (the flat SafetyReading
  //     dict). `null` when no monitor is connected OR it dropped mid-session (the
  //     poller clears its cache to None) — this null is what lets the store's
  //     safety.connected self-heal on disconnect. The store normalizes it via
  //     normalizeSafety(); do NOT read the flat dict's `connected` (it has none). ---
  safety?: SafetyReading | null;
  // --- settings (additive; survives the 2s wholesale status replace) ---
  // Full poll_status site shape — superset of SiteInfo. name/lat/lon/elevation
  // are stripped over the wire for non-holders of view.site_precise (spec §2),
  // so they are optional; is_default/horizon_min_deg are always present.
  site?: {
    name?: string;
    latitude?: number;
    longitude?: number;
    elevation_m?: number;
    is_default: boolean;
    horizon_min_deg: number;
  };
  optics?: OpticsComputed;
}

// Rotator live state (CAA spec §3.2/§5.2). `sky_deg`/`mech_deg` are the sky
// position-angle and the raw mechanical reading; the offset between them
// (mechanical − sky) is NOT on the wire — lib/rotation.ts derives it from
// both live values (see adjustedPa).
export interface RotatorStatus {
  name: string;
  sky_deg: number;
  mech_deg: number;
  moving: boolean;
  synced: boolean;
  can_reverse: boolean;
  reverse: boolean;
}

export interface DiskInfo {
  free_gb: number;
  low: boolean;
  critical: boolean;
}

// UX-23: guider calibration report — pass/fail + the geometry that exposes a
// bad/flipped calibration, plus the engine's human-readable advisories.
export interface CalibrationReport {
  is_valid: boolean;
  ortho_error_deg: number;
  declination_deg: number | null;
  pier_side: string | null;
  binning: number;
  advisories: string[];
  source: string;
}

export interface GuideStats {
  guiding: boolean;
  rms_ra: number;
  rms_dec: number;
  rms_total: number;
  snr: number;
  recent: { t: number; ra: number; dec: number }[];
  // UX-15: whether rms_* / recent are true ARCSEC (guide-scope focal length
  // known) or guide-camera PIXELS (no FL → the native guider's 1:1 fallback).
  // Optional — absent on an older server payload, treated as arcsec (the prior
  // label) unless the server explicitly says false.
  is_arcsec?: boolean;
  image_scale?: number;
  // NOV-7: plain-language narration phase ("idle" | "finding" |
  // "calibrating" | "settling" | "guiding" | "lost"), or "" / absent when
  // unknown (the PHD2/NINA bridge guider leaves it unset — the narration
  // falls back to `guiding`). A bare string (not the GuidePhase union) so an
  // older/legacy payload still type-checks.
  phase?: string;
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
  // Multi-night session sub-state (sessions spec §6): present while a session
  // is running; cleared with explicit-None semantics like `schedule`. `target`
  // is a raw dict key server-side — it serializes as an explicit null (key
  // PRESENT) until the first target starts, so it must admit null, not just
  // undefined. The other fields are pydantic str/int-typed: never null.
  session?: { id: string; name: string; count_mode: string; accepted: number; target?: string | null };
  // Terminal reason — drives the run-complete Badge + Report end-reason icon.
  end_reason?: "complete" | "aborted" | "error" | "unsafe" | "dawn_cutoff" | "cooling_skip" | "quality";
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

// NOV-3: server-derived beginner difficulty tag (catalog/difficulty.py). The
// canonical union — lib/difficulty.ts re-exports it for display-helper callers.
export type DifficultyTier = "easy" | "moderate" | "hard";

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
  // NOV-3 (additive): server-derived beginner difficulty. Optional so payloads
  // that predate it (older /api/catalog, test doubles) still type-check.
  difficulty?: DifficultyTier;
  surface_brightness?: number;              // mag/arcmin^2
  difficulty_source?: "heuristic" | "curated";
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
  // stable identity for multi-night session ledgers (sessions spec §1).
  // Optional: legacy localStorage plans lack it; store backfills via ensurePlanIds.
  id?: string;
  filter: string | null;
  exposure_s: number;
  gain: number;
  offset: number;
  binning: number;
  count: number;
  frame_type: string;
}

export interface Target {
  // stable identity for multi-night session ledgers (sessions spec §1).
  id?: string;
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
  // --- multi-night quota (sessions spec §3; additive — server defaults apply) ---
  count_mode?: "attempts" | "accepted";
  min_stars?: number;                     // star floor (0 = off)
  max_guide_rms?: number;                 // guide-RMS ceiling, arcsec (0 = off)
  max_consecutive_rejects?: number;       // per-step guard (0 = off)
  max_consecutive_rejects_night?: number; // per-night guard (0 = off)
  max_eccentricity?: number;              // per-frame median-ecc ceiling, 0..1 (0 = off)
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
  // Strippable over the wire for principals lacking view.site_precise (spec §2):
  // absent, not nulled. is_default + horizon_min_deg are always present.
  name?: string;
  latitude?: number;   // +N (stored signed)
  longitude?: number;  // +E (East-positive; matches coords.lst_hours)
  elevation_m?: number;
  is_default: boolean;
  horizon_min_deg: number;
}

export interface Optics {
  focal_length_mm: number;
  pixel_size_um: number;
  sensor_width_px: number;
  sensor_height_px: number;
  auto_from_camera: boolean;
  guide_focal_length_mm?: number | null; // A4: optional guide-scope focal length (mm)
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
  // REDACTED outbound (P2-12): the deadman url can carry a per-ping secret in its
  // path/query, so the server blanks it and exposes only `deadman_configured`.
  // POST an empty `deadman_url` to leave the stored value unchanged (mirrors the
  // alert-token "empty means unchanged" contract); POST a non-empty url to set it.
  deadman_url: string;
  deadman_configured?: boolean;
  // --- weather (sub-project C; additive). The Astrospheric key is blanked
  //     server-side via redacted(); astrospheric_configured is the marker the
  //     write-only Settings field reads ("set" / "not set"). ---
  weather?: {
    enabled: boolean;
    cloud_threshold_pct: number;
    sustain_minutes: number;
    astrospheric_api_key: string | null;
    astrospheric_configured?: boolean;
  };
  // --- RBAC (W2.5; additive). The redacted auth state block — non-secret
  //     booleans + role allowlist. Optional: the WS `hello` bootstrap config may
  //     omit it; the first `config` event / REST GET carries it. ---
  auth?: AuthState;
  // --- self-update (Phase 3; additive). Non-secret (signing_pubkey is PUBLIC). ---
  update?: UpdateConfig;
  // --- capability providers (native parity; additive). Per-capability routing
  //     override persisted server-side (config.py ProvidersConfig); "auto" lets
  //     providers.resolve() pick, "backend"/"astrodeck" pin a family. Optional:
  //     an old WS `hello` bootstrap predates the field. ---
  providers?: ProvidersConfig;
  // --- backend drivers (equipment-drivers spec; global, never per-profile) ---
  drivers?: DriverEntry[];
  // --- rotator range-of-motion + tolerance (CAA spec §3.2; global config,
  //     mirrors server config.py RotatorConfig). Optional: an old WS `hello`
  //     bootstrap predates the field. ---
  rotator?: RotatorConfig;
  // --- offline survey pack (spec 2026-07-13). Optional: an old WS `hello`
  //     bootstrap predates the field. ---
  survey?: SurveyConfig;
  // --- file-naming template (PRO-11; appended). Optional: an old WS `hello`
  //     bootstrap predates the field. ---
  naming?: NamingConfig;
}

// ---------------------------------------------------------- rotator config
// Mirrors server/astrodeck/config.py RotatorConfig (CAA spec §3.2) — the
// CONFIG (write) side of POST /api/config/rotator. range_type bounds the
// mechanical sweep (full 360° / half 180° / quarter 90°) starting at
// range_start_deg; tolerance_deg is the "close enough" band for rotate-to-PA.
export interface RotatorConfig {
  range_type: "full" | "half" | "quarter";
  range_start_deg: number;
  tolerance_deg: number;
}

// ------------------------------------------------------- capability providers
// Mirrors server/astrodeck/config.py ProvidersConfig — the CONFIG (write) side.
// Vocabulary (spec §3.4, registry-driven): "auto" | "backend" (LEGACY alias for
// the connected backend's own implementation — kept accepted forever) |
// "astrodeck" | "astap" | "sim" (implicit driver ids) | a configured driver id
// ("nina-a3f2"). Dynamic — a plain string; the server 422s unknown values.
export type ProviderKind = string;
export interface ProvidersConfig {
  autofocus: ProviderKind;
  polar_align: ProviderKind;
  solve: ProviderKind;
  // Who autoguides (P5-T1; mirrors server config.py ProvidersConfig.guide,
  // landed P2-T3). "auto" | "backend" (PHD2/NINA bridge) | "astrodeck"
  // (native engine) | "sim" (implicit driver id — see providers.py
  // valid_override_values; _resolve_guide has no dedicated "sim" branch so it
  // behaves like "auto" on a sim rig today, kept for vocabulary parity with
  // the other three capabilities).
  guide: ProviderKind;
}

// ------------------------------------------------------------- backend drivers
// Mirrors server/astrodeck/drivers.py describe_all() + config.py DriverEntry
// (equipment-drivers spec 2026-07-08 §3.1/§3.2). DriverEntry is the CONFIG
// (write) side; DriverInfo is the READ side (probe status + offers) the
// Equipment/Settings surfaces render from. The listed literals are the known
// built-ins + the native-hardware-onramp driver_types (2026-07-21: zwo-am5,
// wanderer-snowflake, zwo-usb, zwo-asi, player-one) + the implicit
// "ascom-local" row; `(string & {})` keeps it OPEN (mirrors ProfileDevice's
// free-string `backend`) since the registry lets a plugin backend declare a
// new driver_type with zero core/client edits (drivers.py driver_type_to_backend).
export type DriverType =
  | "nina" | "alpaca" | "phd2" | "sim" | "astrodeck" | "astap" | "ascom-local"
  | "zwo-am5" | "wanderer-snowflake" | "zwo-usb" | "zwo-asi" | "player-one"
  | (string & {});

export interface DriverEntry {
  id: string;            // server-minted "<type>-<4hex>", immutable
  type: DriverType;      // configured entries are only nina|alpaca|phd2
  host: string;
  port: number;
  enabled: boolean;
  label: string;
  extra: Record<string, unknown>;
}

export interface DriverDeviceOffer {
  role: string;
  name: string;
  dev_type?: string;     // Alpaca only — ALWAYS present there (ConnSpec addressing)
  dev_num?: number;      // Alpaca only
}

export interface DriverStatus {
  reachable: boolean;
  error: string | null;
  detail: string | null; // NINA API version / ASTAP path / native wheel version
  probed_at: number;     // unix seconds
}

export interface DriverInfo {
  id: string;
  type: DriverType;
  label: string;
  enabled: boolean;
  implicit: boolean;     // sim/astrodeck/astap = detected built-ins, not stored
  host?: string;
  port?: number;
  // --- native hardware on-ramp follow-ups (2026-07-21): per-unit addressing
  //     echoed on configured rows so the client can tell two identical units
  //     of the same driver_type apart. `transport` is "network"|"serial"|
  //     "local"; `port_path` (serial, e.g. "COM3") is REDACTED (absent) for
  //     callers without config.backend, mirroring `host`; `index` is the
  //     0-based SDK-enumerated unit (zwo-asi/player-one cameras only). ---
  transport?: string;
  port_path?: string;
  index?: number;
  status: DriverStatus;
  offers: { devices: DriverDeviceOffer[]; tasks: string[] };
}

export interface DriversResponse {
  roles: string[];       // fed from devices/backend.py ROLES — a new role appears free
  drivers: DriverInfo[];
}

// ------------------------------------------------ offline survey pack (spec 2026-07-13)
export interface SurveyConfig {
  /** True => hips2fits upstream is used for FOV < 4°; false (default) => pack only. */
  online_fetch: boolean;
}

// -------------------------------------------------------- file naming (PRO-11)
// Mirrors server/astrodeck/config.py NamingConfig — the CONFIG (write) side of
// POST /api/config/naming. The client PREVIEW render (ui/src/lib/naming.ts) is
// advisory only; the server render is authoritative for the real path.
export interface NamingConfig {
  template: string;
}
export interface PackFetchProgress { done: number; total: number; failed: number; }
export interface PackStatus {
  present: boolean;
  slug: string;
  survey: string;
  order: number | null;
  bytes: number | null;
  tile_count: number | null;
  fetched_at: number | null;
  fetching: PackFetchProgress | null;
}

// ---------------------------------------------------------------- self-update
// UpdateConfig is the persisted config block; UpdateStatus is the live snapshot
// the server pushes over the `update` WS event and returns from GET /api/version
// and /api/update/status. Holds no secret (the signing key here is public).
export interface UpdateConfig {
  enabled: boolean;
  auto_check: boolean;
  check_interval_hours: number;
  channel: "stable" | "prerelease";
  repo: string;
  signing_pubkey: string;
  health_timeout_s: number;
  last_check_ts?: number | null;
}

export interface UpdateStatus {
  current: string;
  latest: string | null;
  update_available: boolean;
  notes_md: string;
  channel: string;
  last_check_ts: number | null;
  phase: string; // idle | checking | downloading | verifying | staging | applying
  progress: number;
  error: string | null;
  last_result: { ok: boolean; version?: string; from?: string; reason?: string } | null;
  // only on /api/update/status (not the raw snapshot):
  supervised?: boolean;
  can_apply?: boolean;
  apply_blocked_reason?: string;
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
  // --- pro visibility constraints (PRO-14; additive, 0 = off) ---
  min_moon_sep_deg: number;             // ≥ this from the Moon while it's up (0 = off)
  max_moon_illum_pct: number;           // skip while Moon > this % illuminated (0 = off)
  max_hour_angle_h: number;             // image within ±this h of the meridian (0 = off)
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
  kind: "ntfy" | "webhook" | "telegram" | "discord" | "slack" | "email";
  enabled: boolean;
  url: string;                          // ntfy topic url / webhook url
  chat_id?: string;                     // telegram chat id
  min_level: "warning" | "error";
  events: string[];                     // ["run_start","run_end","safety","error",...]
  verified: boolean;                    // true only after a successful round-trip test
  heartbeat_min: number;                // 0 = off; periodic progress ping cadence
  token_configured?: boolean;   // derived server marker: is the secret set?
  smtp_host?: string;
  smtp_port?: number;
  smtp_user?: string;
  smtp_from?: string;
  smtp_to?: string;
  smtp_starttls?: boolean;
}

// write body: adds the write-only secret
export interface AlertSinkInput extends AlertSink {
  token?: string;   // telegram bot token | discord/slack webhook url | smtp password
}

export interface AlertHealth {
  undelivered: number;
  undelivered_by_sink: Record<string, number>;
  deadman: { configured: boolean; healthy: boolean; last_ping_age_s: number | null };
}

export interface SafetyConfig {
  enabled: boolean;
  preset: "backyard" | "remote" | "custom";
  min_alt_deg: number;                  // global pier-collision floor (mount-alt). 0 = off
  horizon: [number, number][] | null;   // sorted (az,alt) control points
  enforce_pier_limits: boolean;         // only settable when mount reports pier side
  twilight_deg: number;                 // nautical −12 default (C1-26)
  // Sun-exclusion cone (W1.10). ON by default to protect deep-sky gear; a
  // deliberate solar-astronomy session disarms it via solar_avoidance=false
  // (route-gated behind config.solar_override). RA/Dec-based ⇒ site-independent.
  solar_avoidance: boolean;             // master enable; true = cone armed (deep-sky default)
  solar_exclusion_deg: number;          // cone half-angle (deg); 0..90, ≤0 = inert
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
  prev_zoom_deg?: number;                  // zoom saved by the FOV-lock toggle (in-memory; not persisted)
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

// ------------------------------------------------ tonight picker (NOV-3)
// GET /api/catalog/tonight — the ranked "what can I image tonight?" list. Each
// pick is a CatalogEntry-shaped object plus tonight's visibility summary and a
// difficulty tag; ordered best-window-first by the server (score desc).
export interface TonightPick {
  id: string;
  name: string;
  type: string;
  ra_hours: number;
  dec_deg: number;
  mag: number;
  size_arcmin: number;
  difficulty: DifficultyTier;
  surface_brightness: number;
  difficulty_source: "heuristic" | "curated";
  max_alt: number;
  transit_unix: number;
  best_window: { start_unix: number; end_unix: number } | null;
  moon_sep_deg: number;
  never_rises_above_limit: boolean;
  score: number;                            // server ranking key (visibility)
}

export interface TonightResponse {
  date: string;
  site_is_default: boolean;
  picks: TonightPick[];
}

// ---------------------------------------------------- onboarding: site + checks
// SiteInfo is the LIVE/status view of the site (distinct from the persisted Site).
export interface SiteInfo {
  name?: string;
  latitude?: number;
  longitude?: number;
  elevation_m?: number;
  is_default: boolean;
  horizon_min_deg: number;
}

// Saved observing location (server astrodeck/locations.py SavedLocation). Served
// ONLY by /api/locations — never embedded in config/status/WS payloads.
export interface SavedLocation {
  id: string;
  name: string;
  latitude: number;   // +N (signed)
  longitude: number;  // +E (East-positive)
  elevation_m: number;
  horizon_min_deg: number | null;
  created_ts: number;
  updated_ts: number;
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
// `backend` is a free string (registry name): the server allows "native", "sim",
// "nina", "alpaca" (legacy alias for "native"), etc. — NOT a closed union. `extra`
// carries backend-specific options (e.g. the managed-PHD2 toggle writes
// extra.managed) and is persisted verbatim; mirrors server profiles.ProfileDevice.
export interface ProfileDevice {
  role: string;
  backend: string;
  host: string;
  port: number;
  dev_type: string;
  dev_num: number;
  name: string;
  extra: Record<string, unknown>;
  // Reference to AppConfig.drivers[].id (spec §3.3/§4.4); server-persisted
  // (profiles.ProfileDevice.driver_id, default ""). Optional here so
  // pre-drivers profile payloads type-check unchanged.
  driver_id?: string;
}

export interface Profile {
  id: string;
  name: string;
  // The RigSpec primary (default backend for any role with no explicit per-device
  // override). Server defaults to "sim"; the picker writes the primary selection
  // here. Mirrors server profiles.Profile.primary_backend.
  primary_backend: string;
  devices: ProfileDevice[];
  nina_host: string | null;
  nina_port: number;
  phd2_host: string | null;
  phd2_port: number;
  optics: Optics | null;
  site_name: string | null;
  // Per-rig task overrides (spec §4.4) — wins over global config when this
  // profile is ACTIVE (server providers._override). Partial dict server-side;
  // absent on old profiles.
  providers?: Partial<ProvidersConfig> | null;
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

// ============================================================================
// PLUGGABLE BACKENDS + RBAC (W1.C / W1.6 / W2.x). The one true client shape,
// verified against server: devices/backend.list_backends (BackendInfo),
// devices/orchestrator.RoleResult (RoleResult), hub.backend_links (BackendLink),
// devices.backend.RigSpec/ConnSpec (RigSpec/ConnSpec), hub.connect_rigspec
// (ConnectRigResult), auth/principal.Principal.to_public (Principal), and
// auth/capabilities (Capability). All additive — old code ignores them.
// ============================================================================

// GET /api/backends row — devices.backend.list_backends().
export interface BackendInfo {
  name: string;          // registry key, stored in ConnSpec.backend ("sim"|"nina"|"native"|...)
  label: string;         // human name for the UI
  roles: string[];       // subset of ROLES this backend can fill
  discoverable: boolean; // true => discover() does real network work
  // Native-hardware on-ramp (2026-07-21): USB/serial backends (zwo-am5,
  // wanderer-snowflake, zwo-usb, zwo-asi, player-one) carry these so the
  // client can offer them in "Scan for USB/serial hardware" without a
  // per-backend switch statement. Optional — network backends (sim/nina/
  // native/phd2) omit `driver_type` (falsy => not a configurable driver_type).
  hardware?: boolean;    // real hardware (device-borne safety flag), not a network service
  driver_type?: string;  // the `type` to pass to addDriver() when configuring this backend
  transport?: string;    // "network" | "serial" | "local"
}

// One per-role connection override in a RigSpec (mirrors server ConnSpecBody /
// devices.backend.ConnSpec). Only `backend` is required; the rest carry the
// addressing a given backend needs (native: host/port/dev_*; nina: host/port;
// sim/phd2: nothing). `extra` carries backend options (e.g. {managed:true}).
export interface ConnSpec {
  backend: string;
  host?: string | null;
  port?: number | null;
  dev_type?: string | null;
  dev_num?: number | null;
  role?: string | null;
  driver_id?: string | null; // reference to AppConfig.drivers[].id (spec §3.3)
  extra?: Record<string, unknown>;
}

// POST /api/connect/rig body (mirrors server RigSpecBody / devices.backend.RigSpec):
// a `primary` backend that fills every ROLE it can + optional per-role overrides.
export interface RigSpec {
  primary: string;
  roles: Record<string, ConnSpec>;
}

// The tri-state outcome for ONE requested role, as returned in
// /api/connect/rig.results (devices.orchestrator.RoleResult — no live `connected`).
// attempted=false => never tried (unfillable / not requested) => NOT a red LED.
// attempted=true, ok=true => connected. attempted=true, ok=false => failed (error).
export interface RoleResult {
  role: string;
  ok: boolean;
  error: string | null;
  attempted: boolean;
}

// The per-role boot-LED surface (hub.backend_links): a retained RoleResult joined
// with the role's LIVE `connected` state. Present on every `status` poll AND
// `hello`, plus inside ConnectRigResult. `[]` until a RigSpec/profile connect
// happened (the legacy connect_* paths leave it empty).
export interface BackendLink {
  role: string;
  ok: boolean;
  error: string | null;
  attempted: boolean;
  connected: boolean;
}

// POST /api/connect/rig response (hub.connect_rigspec).
export interface ConnectRigResult {
  summary: RigStatus;
  results: RoleResult[];
  backend_links: BackendLink[];
}

// The 16 capability strings (auth/capabilities.py). view.* read; control.* device
// motion/imaging; config.* settings writes; admin.* auth/remote admin.
export type Capability =
  | "view.status"
  | "view.preview"
  | "view.media"
  | "view.site_precise"
  | "view.weather"
  | "control.capture"
  | "control.mount"
  | "control.guide"
  | "control.power"
  | "config.safety"
  | "config.solar_override"
  | "config.backend"
  | "config.site_optics"
  | "config.alerts"
  | "admin.users"
  | "system.update";

export type PrincipalRole = "viewer" | "operator" | "admin";

// GET /api/me — auth/principal.Principal.to_public(). FAIL-CLOSED on the server:
// 401 on any resolution failure (never default-admin). Under provider="none"
// every caller resolves to admin + ALL caps, so the default LAN UI is unchanged.
export interface Principal {
  role: PrincipalRole;
  email: string | null;
  caps: Capability[];
}

// Non-secret auth state on the REDACTED GET /api/config (config.redacted()). Only
// present when the server has an `auth` block; *_configured booleans replace the
// scrubbed secrets. `auth` is optional on AppConfig because the WS `hello`
// bootstrap config may omit it.
//
// MULTI-METHOD (W2.3-bis/W2.6): `methods` is the source of truth — a subset of
// {"local","google"}. EMPTY ⇒ OPEN/admin-for-all (today's LAN default; NO login
// screen). `provider` is the LEGACY single-provider field, kept for read-time
// migration only. Both `local` and `google` can be enabled at once. The redacted
// block is a full model_dump of the server AuthConfig with secrets scrubbed, so it
// also carries google_client_id/redirect (non-secret), session_ttl_s, and the
// first-run flag.
export interface AuthState {
  // Source of truth for enabled login methods. [] ⇒ open/admin (no login).
  methods: string[];
  // LEGACY single-provider field (migrate-only; methods wins when non-empty).
  provider: "none" | "google";
  google_configured: boolean;
  admin_token_configured: boolean;
  session_signing_configured: boolean;
  role_allowlist: Record<string, string>; // email -> role
  default_role: string | null;
  // Session lifetime (seconds) for BOTH local + google logins.
  session_ttl_s?: number;
  // First-run create-admin path is allowed while the user store is empty.
  local_enabled_first_run?: boolean;
  // G4: False => a loopback (127.0.0.1) caller no longer auto-resolves to admin
  // under the open/no-method default — it must sign in like any other client.
  // Test-mode knob for verifying operator/viewer gating from this machine.
  // Default true; not a secret, passes through redaction unscrubbed.
  trust_loopback?: boolean;
  // Non-secret google fields pass through redaction (the client_secret is blanked).
  google_client_id?: string;
  google_redirect_uri?: string;
  google_hd?: string;
}

// GET /api/auth/methods — the tiny UNAUTHENTICATED "what login UI do I render?"
// signal (auth/local_routes.py). Read by the Login screen BEFORE any session
// exists. `methods == []` ⇒ no login at all (open LAN). `first_run` is true only
// while local is enabled, the first-run flag is on, AND the user store is empty.
export interface AuthMethods {
  methods: string[]; // subset of {"local","google"}
  google_configured: boolean;
  first_run: boolean;
  // A break-glass admin token is configured → the login screen offers the
  // "access token" field. Optional so older payloads/test doubles omit it.
  admin_token_configured?: boolean;
}

// A local user record as returned by the admin user-management routes
// (User.to_public() — auth/users.py). `password_hash` is structurally ABSENT.
export interface User {
  id: string;
  username: string;
  email: string | null;
  role: PrincipalRole;
  enabled: boolean;
  created: number; // epoch seconds
}

// -------------------------------------------------------- multi-night sessions
// Mirrors server sequence/session.py (sessions spec §2/§6).
export interface SessionFrame {
  id: string;
  ts: number;
  night: string;                       // report_id captured under
  target_id: string;
  step_id: string;
  path?: string;                       // ABSENT for principals w/o config.backend (§8)
  thumb: string | null;
  metrics: Record<string, number>;     // hfr, stars, guide_rms, sensor_temp_c, ...
  auto_accepted: boolean;
  override: "accept" | "reject" | null;
}

export interface Session {
  id: string;
  schema_version: number;
  name: string;
  created_ts: number;
  updated_ts: number;
  status: "active" | "dormant" | "complete" | "abandoned";
  plan: SequencePlan;                  // frozen snapshot WITH ids
  nights: string[];
  frames: SessionFrame[];
  auto_resume: boolean;
}

export interface SessionRow {
  id: string;
  name: string;
  status: "active" | "dormant" | "complete" | "abandoned";
  created_ts: number;
  updated_ts: number;
  nights: number;
  accepted: number;
  total: number;
  auto_resume: boolean;
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

// ===================================================================== weather
// Sub-project C (weather spec §7): the GET /api/weather + WS `weather` event
// payload. ALL weather data is view.weather-gated server-side (2026-07-17
// decisions wave I2 -- split off view.site_precise so operators see it too;
// WS events are DROPPED for non-holders), so these only ever populate for
// holders (operator + admin). site_lat/site_lon are the one deliberate
// coordinate exception (the radar map needs them to center its tiles) --
// every OTHER coordinate-bearing surface stays view.site_precise-gated
// (admin only), unchanged.
export interface WeatherForecast {
  times: string[];        // ISO-8601 Z, 15-min grid, <= 192 samples (48 h)
  cloud: number[];        // TOTAL cloud cover % — the breach metric
  cloud_low: number[];
  cloud_mid: number[];
  cloud_high: number[];
}

export interface WeatherAstrospheric {
  times: string[];        // ISO-8601 Z, hourly (81 h horizon)
  seeing: (number | null)[];
  transparency: (number | null)[];
  fetched_ts: number | null;
  stale: boolean;         // > 12 h old (two 6-hourly model cycles)
  credits_used_today: number | null;  // 5 credits/call on a 100/day Pro budget
}

export interface WeatherAlert {
  kind: "high_cloud";
  start_iso: string;
  end_iso: string;
  peak_pct: number;
  dominant_layer: "low" | "mid" | "high";
}

export interface WeatherState {
  enabled: boolean;
  fetched_ts: number | null;   // Open-Meteo fetch time (unix s)
  stale: boolean;              // client re-derives FAIL-CLOSED (> 45 min)
  ignore_tonight: boolean;
  threshold_pct: number;
  sustain_minutes: number;
  // Site fix for the radar map (RadarMap.tsx) to center on -- null on the
  // default (0,0) site. The one deliberate exception to "coordinates are
  // view.site_precise-gated everywhere else" (see the module comment above).
  site_lat: number | null;
  site_lon: number | null;
  forecast: WeatherForecast | null;
  astrospheric: WeatherAstrospheric | null;
  alert: WeatherAlert | null;
}
