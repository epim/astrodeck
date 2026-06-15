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
  | "atlas";

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
  camera?: { temperature: number | null; can_cool: boolean; has_dew_heater?: boolean; width: number; height: number; max_gain: number };
  guider?: GuideStats & { name: string };
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

export interface PreviewInfo {
  id: number;
  stats: { min: number; max: number; mean: number; median: number; std: number };
  histogram: number[];
  exposure_s: number;
  gain: number;
  binning: number;
  width: number;
  height: number;
  saved_path?: string;
  hfr?: number;
  stars?: number;
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

export interface SequenceState {
  state: "idle" | "running" | "paused" | "complete" | "aborted" | "error";
  detail?: string;
  target?: string;
  target_index?: number;
  plan_name?: string;
  progress?: { frames_done: number; frames_total: number; percent: number; elapsed_s: number; rejected?: number };
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
  optics_computed: OpticsComputed;
  active_profile_id: string | null;
}

export interface SkyInfo {
  sun_alt_deg: number;
  dark_window: { start_iso: string; end_iso: string } | null;
  place_hint: string;
  lst_str: string;
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
