// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The seam between the replay driver and a scanner (SPEC-v2 3.4, 7.4).
//
// `replay.ts` used to know one scanner, `PhotosphereSweep`, and read its
// members directly. It now knows this interface, and an adapter per scanner
// (`legacyAdapter.ts`, later `panoAdapter.ts`) maps the scanner onto it. The
// driver stays what it always was - a browser that plays a recorded case into
// whatever has been started in it, and a writer of the result files the scorer
// reads (13.7) - and gains no knowledge of either scanner's internals.
//
// Declared here and nowhere else (SPEC-v2 3.3 rule 4): production code never
// imports from `__sim__/`.
import type { CameraBasis, HorizonPoint, ScannerOptions, Stats } from '../pano/types';

export interface FrameReport {
  basis: CameraBasis | null; cue: string; aim: number | null; compassReady: boolean; tiltReady: boolean; frameCount: number;
  extra?: { keyframe: boolean; cls: 'aligned' | 'blurred' | 'sensor' | null };   // absent for the legacy scanner
}
export interface HorizonOutV1 { bins: number; points: HorizonPoint[]; uncertain_bins: number[] }
export interface HorizonOutV2 {
  version: 2; interpolation: 'linear-wrap'; profile_bins: 720;
  profile: number[];                 // published altitude per bin (HorizonDraft.alt)
  profile_traced: (number | null)[]; // traced altitude per bin (suggested), null where none
  profile_state: number[];           // BinState per bin
  points: HorizonPoint[]; tau: number; bins: 720; uncertain_bins: number[];   // bins in Low, Unknown or Tall
}
export interface Diagnostics {   // 13.7
  version: 1; scanner: 'pano'; sensor_only: boolean;
  begin_ms: number | null; finish_ms: number | null;
  predictor_mode: string | null; mode_changes: { t_ms: number; from: string; to: string }[];
  axis_mapping: { perm: number[]; sign: number[]; unit: string; fit: number; confirmed: boolean } | null;
  tau_ms: number; tau_sigma_ms: number; tau_pairs: number; tau_applied: boolean;
  focal: { state: string; f_norm: number; sd_pct: number; ratios: number; short_fov_deg: number };
  loop: { closed: boolean; method: string | null; pre_deg: number[] | null; post_deg: number | null;
    match: { early_kf: number; late_kf: number } | null; unwrapped_deg: number };
  north: { offset_deg: number; sigma_deg: number; spread_deg: number; samples: number; n_eff: number; stable: boolean; source: string } | null;
  declination_applied: boolean;
  keyframes: { id: number; frame_id: string; t_ms: number; q: [number, number, number, number]; cls: string; sigma_deg: number }[];   // q: final world pose
  keyframe_ms: Stats; readback_ms: Stats; stale_refusals: number;
  extractor: 'tracer';
}
export interface LiveSnapshot { t_ms: number; kf: number; painted_fraction: number; closure_state: 'open' | 'closed-image' | 'closed-gyro'; focal_state: string }
export interface ScannerUnderTest {
  start(video: HTMLVideoElement, canvas: HTMLCanvasElement): Promise<void>;
  readonly canBegin: boolean;
  begin(): void;
  readonly isRecording: boolean;      // the scan has started
  noteFrame?(frameId: string): void;  // called by the replay just before each frame is delivered
  frameReport(): FrameReport;
  finishScan(): void;                 // pano: the finish-time work; legacy: no-op
  panorama(): { width: 1080; height: 300; pixels: Uint8ClampedArray } | null;
  horizon(): HorizonOutV1 | HorizonOutV2;
  firstSeen(): Uint16Array | null;
  diagnostics(): Diagnostics | null;
  liveSnapshot(): LiveSnapshot | null;
  captureLog(): Record<string, unknown>[];   // rows already in the captures.jsonl shape
  cells?(): { total: number; covered: number };     // legacy only (summary.json)
  legacyColumns?(): (number | null)[][];            // legacy only (columns.json)
  stop(): void;
}
export interface ScannerFactory { create(o: ScannerOptions & { declinationDeg: number | null }): ScannerUnderTest }
