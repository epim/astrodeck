export interface DeviceInfo {
  name: string;
  kind: string;
  connected: boolean;
}

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
  mount?: MountStatus;
  focuser?: { position: number; max: number; temperature: number | null };
  filterwheel?: { position: number; names: string[] };
  camera?: { temperature: number | null; can_cool: boolean; width: number; height: number; max_gain: number };
  guider?: GuideStats & { name: string };
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
  plan_name?: string;
  progress?: { frames_done: number; frames_total: number; percent: number; elapsed_s: number };
}

export interface LogLine {
  type: string;
  data: { level: string; message: string; source: string };
  ts: number;
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
  steps: ExposureStep[];
}

export interface SequencePlan {
  name: string;
  targets: Target[];
  guide: boolean;
  dither_every: number;
  dither_pixels: number;
  autofocus_every: number;
  park_when_done: boolean;
  warm_cooler_when_done: boolean;
}
