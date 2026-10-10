// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The horizon panorama scanner's shared declarations (SPEC-v2 3.3, 3.4). Type
// aliases, interfaces, `as const` objects and number constants only: no class,
// function, enum or `declare`, which is the form that compiles the same under
// `tsc -b`, tsx and Vite with `isolatedModules`. A class whose surface crosses
// modules appears here as `<Name>Like` and lives in its own module. Frozen once
// T02 lands; a task that finds a type missing declares it in its own module.

// [block: types:geometry] PANO/types.ts (T02): imports, geometry, statistics
import type { CameraBasis, V3 } from '../photosphereGeometry';
import type { HorizonPoint } from '../../../../lib/horizonModel';
export type { CameraBasis, V3, HorizonPoint };

/** Unit quaternion, camera frame to world (ENU) frame. Camera frame: x right, y up, looking along -z. */
export type Quat = readonly [w: number, x: number, y: number, z: number];
/** Pinhole intrinsics at one pyramid level, in pixels. Pixel i spans [i, i+1); cx = w / 2, cy = h / 2. */
export interface Intrinsics { w: number; h: number; f: number; cx: number; cy: number }
/** Summary statistics. p50 and p95 are nearest-rank; every field but n is null when n is 0. */
export interface Stats { n: number; p50: number | null; p95: number | null; max: number | null }

// [block: types:raster] PANO/types.ts (T02): the panorama raster
export const PANO_W = 1080;   // column x -> azimuth (x + 0.5) / 1080 * 360
export const PANO_H = 300;    // row y -> altitude 90 - y / 299 * 100
export const PixClass = { None: 0, Sensor: 1, Blurred: 2, Aligned: 3 } as const;
export type PixClass = (typeof PixClass)[keyof typeof PixClass];
export type KeyClass = 'aligned' | 'blurred' | 'sensor';
/** x in [0, 1080); x + w may pass 1080, in which case the rectangle wraps to column 0. */
export interface DirtyRect { x: number; y: number; w: number; h: number }
export interface SliceSource {
  id: number;
  strip: Uint8Array;            // RGB, 3 bytes per pixel, row-major, stripW x stripH
  stripX0: number;              // L0 column of the strip's first column
  stripW: number; stripH: number;
  pose: Quat;                   // camera -> world of the frame the strip came from (render frame: scan frame, or world at Finish)
  k0: Intrinsics;               // L0 intrinsics at the current best focal
  gainRGB: readonly [number, number, number];
  cls: KeyClass;
  sigmaDeg: number;             // placement sigma (4.7); stored per pixel as the minimum over slices with feather weight >= 0.5
  halfWidthDeg: 3;              // feather: plateau +-1, linear to 0 at +-3 (horizontal tangent angle)
  trailingDeg: number;          // 0..10: extra painted width on the trailing side (4.3 rule 6)
  trailingSide: -1 | 0 | 1;     // -1 the image's left side, +1 its right side, 0 none
  topFrac: 0.9;                 // vertical limit: |y_tan| <= 0.9 tan(long / 2)
}
export interface BandPanoramaLike {
  readonly rgba: Uint8ClampedArray;   // PANO_W * PANO_H * 4; alpha 255 where observed, 0 never seen
  readonly cls: Uint8Array;           // PixClass per pixel: the highest class painted with feather weight >= 0.5
  readonly sigma: Uint8Array;         // placement sigma per pixel in 0.02-degree units; 255 = none or >= 5.1 degrees
  readonly firstSeen: Uint16Array;    // deciseconds since begin(), 0 = never; never reset by clear()
  readonly coverage: Uint8Array;      // 720 cells of 0.5 degree: the highest PixClass painted between altitude -2 and +10
  begin(nowMs: number): void;
  paint(s: SliceSource, nowMs: number): DirtyRect;
  clear(): void;                      // before a full re-render; firstSeen is kept
  takeDirty(): DirtyRect[];           // rectangles painted since the previous call
  observedRun(x: number): { topAlt: number; bottomAlt: number } | null;   // the painted run containing altitude 0
}

// [block: types:sensors] PANO/types.ts (T02): sensors, prediction, north, latency
export type PredictorMode = 'relative' | 'absolute-gyro' | 'absolute-only' | 'none';
export interface OrientationIn {
  t: number;                                         // ms on the performance.now() timeline (3.2)
  type: 'deviceorientation' | 'deviceorientationabsolute';
  alpha: number | null; beta: number | null; gamma: number | null;
  absolute: boolean | null;                          // the event's own flag; null when the event has none
  compassHeading: number | null;                     // iOS webkitCompassHeading, else null
}
export interface MotionIn {
  t: number;
  rate: { alpha: number | null; beta: number | null; gamma: number | null } | null;   // DeviceMotionEvent.rotationRate as delivered
}
export interface Prediction {
  q: Quat;                 // camera -> scan frame: G(t) of 4.5
  t: number;               // the time asked for
  mode: PredictorMode;
  extrapolatedMs: number;  // 0 when bracketed by samples
  held: boolean;           // true when held through change-driven silence
}
/** How rotationRate components map to body (camera) axes: body[i] = sign[i] * rate[perm[i]] * (unit === 'rad' ? 180 / PI : 1), deg/s. */
export interface AxisMapping {
  perm: readonly [number, number, number]; sign: readonly [number, number, number];
  unit: 'deg' | 'rad'; fit: number; confirmed: boolean; samples: number;
}
export interface NorthEstimate {
  offsetDeg: number;       // MAGNETIC: world azimuth = scan azimuth + offsetDeg; declination is applied by the caller and never stored here
  sigmaDeg: number;        // max(spreadDeg / sqrt(nEff), 2) (4.12)
  spreadDeg: number; samples: number; nEff: number; stable: boolean;
  source: 'scan' | 'manual';
}
export interface LatencyLike {
  addPair(residualYawDeg: number, dOmegaDegS: number, tauUsedMs: number): void;   // 4.5
  readonly tauMs: number;     // the value to use now: the prior until applied, then the estimate
  readonly sigmaMs: number;
  readonly pairs: number;
  readonly applied: boolean;
}
export interface SensorFacts {
  modeAtBegin: PredictorMode | null;
  modeChanges: { tMs: number; from: PredictorMode; to: PredictorMode }[];
  events: { type: 'deviceorientation' | 'deviceorientationabsolute' | 'devicemotion'; total: number;
    absoluteTrue: number; absoluteFalse: number; absoluteMissing: number; nullReadings: number; duplicates: number }[];
  movingHz: { relative: number | null; absolute: number | null; motion: number | null };   // only over intervals with |omega| >= 10 deg/s
  rateByOmega: { omegaFrom: number; omegaTo: number; relativeHz: number | null; absoluteHz: number | null }[];   // bins 0-2, 2-5, 5-10, 10-20, 20-40
  blocked: boolean;
  axis: AxisMapping | null;
  gyroZeroTriples: number;    // exact {0,0,0} samples accepted after a non-zero one
  gyroSeenNonZero: boolean;
  staleRefusals: number;
  staleLimitMs: Stats;
  latency: { priorMs: number; tauMs: number; sigmaMs: number; pairs: number; applied: boolean };
}

// [block: types:focal] PANO/types.ts (T02): focal
export type FocalSource = 'default';              // v1.1 adds 'stored' and 'legacy-typed'
export type FocalState = 'prior' | 'locked' | 'closed';   // v1.1 adds 'verified'
export interface FocalLike {
  readonly fMeasure: number;    // fNorm that LK runs at: the prior until the lock, then the locked value
  readonly fBest: number;       // fNorm: prior, then f0 x median ratio, then locked, then closed
  readonly state: FocalState;
  readonly sdPct: number;       // relative sd, percent: the prior's 20; after the lock 1.4826 x MAD / median / sqrt(n) x 100; after closure 0.139
  readonly ratios: number;
  addRatio(imgYawDeg: number, gyroYawDeg: number): 'collecting' | 'locked-now' | 'locked';
  closeLoop(thetaImgDeg: number, thetaGyroDeg: number, trueDeg: number): { fNorm: number; gyroScale: number };
}

// [block: types:pyramid] PANO/types.ts (T02): pyramid and phase correlation
export interface Level { w: number; h: number; px: Uint8Array }   // luma 0..255, Rec. 601 as pixelLuminance
export interface Pyramid { l1: Level; l2: Level }                  // 90 x 160 and 45 x 80 for a 180 x 320 frame; L0 is transient
export interface TemplateLevel { gx: Float32Array; gy: Float32Array; jac: Float32Array /* 3 per pixel */; hess: Float64Array /* 3x3 */ }
export interface Template { pyr: Pyramid; l1: TemplateLevel; l2: TemplateLevel }
export interface PhaseScratch { w: number; h: number; re: Float64Array; im: Float64Array; re2: Float64Array; im2: Float64Array; win: Float64Array }
export interface PhaseResult { dx: number; dy: number; psr: number }

// [block: types:align] PANO/types.ts (T02): pair alignment
export interface Correspondence { ua: number; va: number; ub: number; vb: number }   // L0 pixels
export type AlignWindow = 'keyframe' | 'closure';
export interface AlignResult {
  ok: boolean;
  reason?: 'texture' | 'no-peak' | 'overlap' | 'diverged' | 'low-zncc' | 'few-inliers';
  qBA: Quat;                 // b-from-a camera rotation; equals the prediction when !ok
  covRad2: Float64Array;     // 3x3 covariance of the image rotation measurement (rad^2), WITHOUT focal uncertainty
  gain: number; bias: number; gainRGB: [number, number, number];
  meanA: [number, number, number]; meanB: [number, number, number]; n: number;   // overlap channel means (0..255) and pixel count, for gains
  psr: number; zncc: number; overlap: number; inlierFrac: number; iterations: number;
  textured: boolean;         // trace(alpha^2 H) / N above TEXTURE_FLOOR; a 'mismatch' is textured && !ok
  corr: Correspondence[];    // <= 16 on a 4x4 grid of the overlap, residual < 1.5 sigma; empty unless ok
}

// [block: types:tracker] PANO/types.ts (T02): tracker
export interface AnalysisFrame { frameId: number; t: number; w: number; h: number; rgba: Uint8ClampedArray; readbackMs: number }
export interface Keyframe {
  id: number; frameId: number; t: number;
  pred: Quat;              // G at t: scan frame, predictor only
  pose: Quat;              // P = C . G after fusion: camera -> scan frame, north not composed
  cls: KeyClass;
  rate: number;            // |omega| deg/s at t
  up: V3;                  // world up seen in the camera frame, from gravity
  sigmaDeg: number;        // placement sigma (4.7), updated after a lock or closure
  gainRGB: [number, number, number];
  strip: Uint8Array; stripX0: number; stripW: number; stripH: number;   // L0 RGB within +-10 degrees of the centre
  pyr: Pyramid | null;     // null when the tracker runs sensor-only
  skyLuma: number;         // median luma of the top 10 % of rows of the analysis frame
}
export interface Edge {
  a: number; b: number; kind: 'chain' | 'revisit' | 'closure';
  qBA: Quat; covRad2: Float64Array; corr: Correspondence[]; zncc: number;
  meanA: [number, number, number]; meanB: [number, number, number]; n: number;
}
export interface LoopState {
  closed: boolean;
  method: 'image' | 'gyro' | null;
  preDeg: V3 | null;                            // residual before correction, rotation vector in degrees
  postDeg: number | null;                       // after correction: angle between the corrected chain's early-to-late rotation and the match
  match: { early: number; late: number } | null;   // keyframe ids of the accepted closure match
  unwrappedDeg: number;                         // signed unwrapped yaw of the newest keyframe relative to keyframe 0
}
export interface FrameStep {
  read: boolean; commit: boolean;
  why?: 'first' | 'step' | 'waiting-sharper' | 'revisit' | 'stale-pose' | 'inactive' | 'covered' | 'cap';
}
export type FrameOutcome = 'accepted' | 'revisit' | 'waiting-sharper' | 'inactive' | 'stale-pose' | 'read-failed' | 'cap';
export type FrameDetail = 'first' | 'aligned' | 'blurred' | 'sensor' | 'mismatch' | 'no-texture' | 'gap'
  | 'closure' | 'closure-gyro' | 'focal-lock';
export interface CaptureRecord {
  at: number; frameId: number; outcome: FrameOutcome; detail?: FrameDetail; kf?: number;
  stepDeg?: number; rateDegS?: number; psr?: number; zncc?: number; innovationDeg?: number; wYaw?: number; extrapolatedMs?: number;
}
export interface TrackerOptions {
  pano: BandPanoramaLike;
  focal: FocalLike;
  latency: LatencyLike;
  frameW: number; frameH: number;   // analysis frame: 180 x 320 (9:16) or 240 x 320 (3:4)
  sensorOnly?: boolean;             // the control (7.7): no alignment; every keyframe is 'sensor'
  maxKeyframes?: number;            // default 140
}

// [block: types:horizon] PANO/types.ts (T02): horizon extraction and the draft
export const ColState = { Measured: 0, Low: 1, UnknownUnseen: 2, UnknownDark: 3, UnknownContrast: 4, Tall: 5 } as const;
export type ColState = (typeof ColState)[keyof typeof ColState];
export interface ColumnHorizon {
  alt: Float32Array;              // 1080 columns: traced boundary altitude A(x), NaN when none
  state: Uint8Array;              // ColState
  top: Float32Array; bottom: Float32Array;   // the painted run containing altitude 0; NaN when none
  contrastSigma: Float32Array;    // boundary contrast in noise sigmas
  sigmaDeg: Float32Array;         // combined placement and focal sigma at the boundary (5.2)
  lowLight: boolean;
}
export interface ExtractOptions {
  columns?: readonly [x0: number, x1: number];   // live use: only these columns (wrapping); Finish: all
  focalSdPct: number;            // FocalLike.sdPct at the time of extraction
  axisAltDeg: number;            // median keyframe optical-axis altitude, for the off-axis focal term
}
export interface HorizonExtractor { readonly name: 'tracer'; extract(p: BandPanoramaLike, o: ExtractOptions): ColumnHorizon }
export const PROFILE_BINS = 720;  // 0.5 degree; bin i covers [i / 2, (i + 1) / 2)
export const BinState = { Measured: 0, Low: 1, Unknown: 2, Tall: 3, Overhead: 4, Edited: 5, Kept: 6 } as const;   // Overhead is reserved for v1.1
export type BinState = (typeof BinState)[keyof typeof BinState];
export interface HorizonDraft {
  alt: Float32Array;        // 720: the altitude Save publishes per bin: A for Measured, the user's value for Edited, the previous line for Kept, 90 otherwise
  suggested: Float32Array;  // 720: traced A; NaN where no boundary was found
  state: Uint8Array;        // BinState
  reason: Uint8Array;       // the ColState of the bin's worst column (tells unseen, dark and contrast apart)
  top: Float32Array;        // 720: photo top (coverage top); NaN where unseen
  lowLight: boolean;
}

// [block: types:live] PANO/types.ts (T02): ribbon and live frame
export interface RibbonView { centreAz: number; altTop: number; altBottom: number; pxPerDeg: number; widthPx: number }
export interface LiveTriangle {
  src: [number, number, number, number, number, number];   // source canvas pixels, 3 vertices (x0, y0, x1, y1, x2, y2)
  dst: [number, number, number, number, number, number];   // ribbon view pixels, same vertex order
}

// [block: types:camera] PANO/types.ts (T02): camera source
export interface FrameMeta {
  t: number;                    // t_f on the performance.now() timeline (3.2)
  frameNo: number;              // 1 for the first delivered frame
  captureTime: number | null; presentationTime: number | null; expectedDisplayTime: number | null; mediaTime: number | null;
  width: number; height: number;   // the delivered frame's size (portrait when height > width)
  presentedFrames: number | null;
  viaRvfc: boolean;             // false on the rAF fallback
}
export interface CameraSettingsReport {
  width: number | null; height: number | null; frameRate: number | null; resizeMode: string | null;
  zoom: number | null; focusMode: string | null; focusDistance: number | null; facingMode: string | null; label: string | null;
}
export interface CameraBench { aMs: Stats; bMs: Stats; chosen: 'A' | 'B'; smoothing: 'medium' }
export interface CameraSourceLike {
  open(video: HTMLVideoElement, deviceId?: string): Promise<void>;
  onFrame(cb: ((meta: FrameMeta) => void) | null): void;   // one call per new frame; re-registers itself; null detaches
  readback(meta: FrameMeta): AnalysisFrame | null;          // the current frame into the fixed analysis canvas (4.2); null on failure
  readbackTiny(): Uint8Array | null;                        // 45 x 80 luma of the current frame, for the watchdog
  refreshLiveSource(): void;                                // GPU draw of the current frame into the 90 x 160 live source; never read back
  liveSource(): HTMLCanvasElement | null;
  readonly settings: CameraSettingsReport;
  readonly bench: CameraBench | null;
  readonly farbled: boolean | null;
  readonly exposureReadable: boolean | null;
  readonly slips: { pairs: number; slips: number };
  readonly lag: { nowMinusCapture: Stats; presentMinusCapture: Stats; expectedMinusNow: Stats };
  readonly choices: readonly { deviceId: string; label: string }[];
  readonly activeId: string | undefined;
  readonly analysisW: number; readonly analysisH: number;
  readonly playing: boolean;
  readonly lastFrameAt: number | null;
  close(): void;
}

// [block: types:scanner] PANO/types.ts (T02): scanner surface
export type ScanPhase = 'idle' | 'opening' | 'ready' | 'scanning' | 'paused' | 'finishing' | 'done' | 'failed';
export type CueKey = 'error' | 'ready' | 'portrait' | 'paused' | 'stalled-camera' | 'stalled-sensor' | 'stale' | 'gap'
  | 'too-fast' | 'tilt-up' | 'tilt-down' | 'roll' | 'mismatch' | 'dark' | 'cap' | 'turning' | 'almost' | 'done-tall' | 'done';
export interface ScanStatus {
  phase: ScanPhase;
  cue: string; cueKey: CueKey; cueKind: 'info' | 'warn' | 'block';
  error: string | null;
  frameNo: number;                 // last delivered frame (ScanView redraws the live mesh when it changes)
  headingDeg: number | null;       // scan-frame heading of the live pose
  northOffsetDeg: number | null;   // add to a scan azimuth for a magnetic azimuth (labels)
  elevationDeg: number | null; rollDeg: number | null; rateDegS: number | null;
  targetPitchDeg: number; pitchBand: { green: [number, number]; amber: [number, number] };
  pace: 'idle' | 'good' | 'fast' | 'too-fast'; rateMaxDegS: number;
  coverage: Uint8Array;            // 720 cells, PixClass
  coveredDeg: number; gapDeg: number | null;
  tallSpans: { from: number; to: number }[];
  mode: PredictorMode; north: NorthEstimate | null;
  focal: { state: FocalState; shortFovDeg: number };
  loop: LoopState; keyframes: number;
  portrait: boolean; canBegin: boolean; recording: boolean;
  farbled: boolean | null;         // CameraSourceLike.farbled; PanoCapture shows a note, never a blocking cue (2.11)
}
export interface ScanResult {
  png: string;                     // data:image/png;base64, true-north frame when a declination was set
  rgba: Uint8ClampedArray;         // the same raster, PANO_W x PANO_H
  draft: HorizonDraft; points: HorizonPoint[]; tau: number;
  north: NorthEstimate | null; declinationApplied: boolean;
  focal: { state: FocalState; sdPct: number; shortFovDeg: number };
  loop: LoopState;
  extractor: 'tracer';
  partial: boolean;                // the ring did not close
  endedBy: 'user' | 'hidden' | 'error';
  error: string | null;
}
export interface ScannerOptions {
  focalPriorScale?: number;        // multiplies the prior fNorm (the replay sets 1.1 on still-pivot cases)
  sensorOnly?: boolean;            // the control (7.7)
  encoder?: FrameEncoder;          // recording encoder; default jpegEncoder
}
export interface ScannerLike {
  readonly status: ScanStatus;
  subscribe(cb: () => void): () => void;
  readonly canBegin: boolean;
  begin(): void; pause(): void; resume(): void;
  tick(nowMs: number): void;       // time-based cues while frames are absent; called every 500 ms by PanoCapture
  readonly ribbon: {
    pixels(): Uint8ClampedArray;   // the live raster (BandPanoramaLike.rgba)
    takeDirty(): DirtyRect[];
    liveMesh(view: RibbonView): LiveTriangle[] | null;   // the live frame at its live pose; null before a pose exists
    liveSource(): CanvasImageSource | null;
  };
  readonly cameraChoices: readonly { deviceId: string; label: string }[];
  readonly activeCameraId: string | undefined;
  noteMeshDraw(ms: number): void;
}

// [block: types:report] PANO/types.ts (T02): report and recording (13.9, 13.10)
/** Synchronous; returns base64 with no data: prefix. */
export interface FrameEncoder { readonly mime: string; encode(rgba: Uint8ClampedArray, w: number, h: number): string }
export interface ScanReport {
  format: 'astrodeck-pano-report'; version: 2;
  scanner: { commit: string | null; sensorOnly: boolean };
  browser: { brands: string[]; mobile: boolean | null; platform: string | null };   // navigator.userAgentData only, never the user-agent string
  timeline: { beginMs: number | null; finishMs: number | null; endedBy: 'user' | 'hidden' | 'error' | 'cancel' };
  camera: { settings: CameraSettingsReport; bench: CameraBench | null; farbled: boolean | null; exposureReadable: boolean | null;
    analysis: { w: number; h: number } };
  frames: { delivered: number; viaRvfc: boolean; captureTimePresent: number; slips: { pairs: number; slips: number };
    lag: { nowMinusCapture: Stats; presentMinusCapture: Stats; expectedMinusNow: Stats }; intervalMs: Stats; presentedGaps: number };
  sensors: SensorFacts;
  focal: { source: FocalSource; state: FocalState; fNorm: number; sdPct: number; ratios: number; shortFovDeg: number;
    closurePct: number | null; gyroScale: number | null; ultraWideSuspected: boolean };
  loop: LoopState;
  north: NorthEstimate | null;
  declinationApplied: boolean;
  keyframes: { total: number; aligned: number; blurred: number; sensor: number; ms: Stats; readbackMs: Stats;
    innovationSteadyDeg: Stats; innovationAccelDeg: Stats; perSliceMs: Stats };
  liveFrame: { drawMs: Stats; redraws: number };
  horizon: { tau: number; points: number; measured: number; low: number; unknown: number; tall: number; kept: number; lowLight: boolean } | null;
  recording: { on: boolean; every: number; frames: number };
  captureLog: CaptureRecord[];
  slices: { kf: number; dataUrl: string }[];   // up to 16 JPEG data URLs of kept strips: photos of the surroundings
  error: string | null;
}
export type ReportInput = Omit<ScanReport, 'format' | 'version'>;
export interface RecordingHeader {
  kind: 'header'; format: 'astrodeck-pano-recording'; version: 1; encoder: string;
  video: { width: number; height: number }; analysis: { width: number; height: number };
  settings: CameraSettingsReport; commit: string | null;
}
