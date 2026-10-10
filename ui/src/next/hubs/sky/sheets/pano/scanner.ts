// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The panorama scanner (SPEC-v2 2.4-2.12, 3.1, 3.5), stage v0 (T22): the one object PanoCapture, ScanView and the
// replay adapter talk to. It owns the camera, the sensor listeners, the pose track, the tracker and the raster, runs
// the frame path of 3.5, says what the user should do next (cueFor, 2.12), and at Finish turns the raster into the
// line Save publishes (2.8). It also writes the report (13.10) and, when asked before Begin, the recording (13.9).
//
// Everything below the frame callback is the tracker's (T19, then T27 and T28): the scanner wires every CueInput
// field and every report field from the tracker, the camera and the pose track as they stand, so a later tracker
// stage that starts to measure mismatches, innovations or a closure lights up its cue and its report field with no
// change here. v0's tracker places every keyframe by the predictor.
//
// Times. Everything inside runs on the performance.now() timeline (3.2): a frame's time is FrameMeta.t, which
// `frameTime` has already bounded, and an event's is its timeStamp when that is within 2000 ms of now. Every time
// copied OUT - into the report, the recording, the replay's diagnostics - is converted to ms since the camera opened
// (ruling S11), which is what `openMs` is for.
//
// Declination (4.12). `setDeclination` hands over a closure that maps a magnetic azimuth to a true one. Finish applies
// it once, to the north offset, and composes the result into the final render; the closure is never called anywhere
// else, and nothing it returns is reported, recorded or shown. The report carries only `declinationApplied`.
import { BandPanorama } from './bandPanorama';
import { CameraSource, cameraErrorText } from './cameraSource';
import { CUE_TEXT, ERROR_TEXT, fill } from './copy';
import { FocalEstimator, PRIOR_SD_PCT } from './focal';
import { TRACE, tracer } from './horizon/horizonTrace';
import { binProfile, mergeKept } from './horizon/profile';
import { conservativePolyline } from './horizon/simplify';
import { liveFrameMesh } from './liveFrame';
import { PoseTrack } from './poseTrack';
import { Recorder, jpegEncoder } from './recorder';
import { buildReport, statsOf } from './report';
import {
  angleBetweenDeg, elevationDeg, headingDeg, intrinsicsAt, longFovDeg as longFovOf, priorFNorm, qinv, qrotate, shortFovDeg,
} from './rotation';
import { queryMotionPermission, requestIosMotionPermission } from './support';
import { MAX_KEYFRAMES, Tracker } from './tracker';
import { BinState, ColState, PANO_H, PANO_W } from './types';
import type {
  AnalysisFrame, BandPanoramaLike, CameraSourceLike, ColumnHorizon, CueKey, DirtyRect, FrameEncoder, FrameMeta,
  HorizonPoint, KeyClass, Keyframe, LoopState, MotionIn, NorthEstimate, OrientationIn, Quat, ReportInput, RibbonView,
  ScanPhase, ScanReport, ScanResult, ScanStatus, ScannerLike, ScannerOptions, SliceSource, V3,
} from './types';

// ---- Cues (2.12) --------------------------------------------------------------

export interface CueInput {
  error: string | null; begun: boolean; portrait: boolean; paused: boolean; cameraStalled: boolean; sensorStalled: boolean;
  staleInLastSecond: number; gapDeg: number | null; rateDegS: number | null; rateMaxDegS: number;
  elevationDeg: number | null; green: readonly [number, number]; rollDeg: number | null; mismatchRun: number;
  skyLuma: number | null; capReached: boolean; coveredDeg: number; closed: boolean; tallSpans: number;
}

/** Rows 5-16 of 2.12 as numbers: stale refusals in a second, the gap cue's width, roll, textured mismatches in a row,
 *  newest sky luma, and the covered degrees from which the ring is almost round. */
const STALE_CUE_COUNT = 5, GAP_CUE_DEG = 2, ROLL_CUE_DEG = 15, MISMATCH_CUE_RUN = 3, DARK_SKY_LUMA = 40, ALMOST_DEG = 330;

/** How each cue is shown. 'block' is a cue the scan cannot get past by turning (an error, a phone held sideways),
 *  'warn' one the user should act on, 'info' the normal flow. The spec leaves the kinds to the scanner. */
const CUE_KIND: Readonly<Record<CueKey, 'info' | 'warn' | 'block'>> = {
  error: 'block', ready: 'info', portrait: 'block', paused: 'info', 'stalled-camera': 'warn', 'stalled-sensor': 'warn',
  stale: 'warn', gap: 'warn', 'too-fast': 'warn', 'tilt-up': 'warn', 'tilt-down': 'warn', roll: 'warn', mismatch: 'warn',
  dark: 'warn', cap: 'info', turning: 'info', almost: 'info', 'done-tall': 'info', done: 'info',
};

/** The one cue the user sees: the first row of 2.12 whose condition holds. A missing reading (null) never fires a
 *  row, and neither does NaN. The pitch band's ends are inclusive, as the rail's are. */
export function cueFor(c: CueInput): { key: CueKey; text: string; kind: 'info' | 'warn' | 'block' } {
  const cue = (key: CueKey, values?: Readonly<Record<string, number>>) =>
    ({ key, text: values ? fill(CUE_TEXT[key], values) : CUE_TEXT[key], kind: CUE_KIND[key] });
  if (c.error !== null) return { key: 'error', text: c.error, kind: CUE_KIND.error };
  if (!c.begun) return cue('ready');
  if (!c.portrait) return cue('portrait');
  if (c.paused) return cue('paused');
  if (c.cameraStalled) return cue('stalled-camera');
  if (c.sensorStalled) return cue('stalled-sensor');
  if (c.staleInLastSecond >= STALE_CUE_COUNT) return cue('stale');
  if (c.gapDeg !== null && c.gapDeg >= GAP_CUE_DEG) return cue('gap', { d: Math.round(c.gapDeg) });
  if (c.rateDegS !== null && c.rateDegS > c.rateMaxDegS) return cue('too-fast');
  if (c.elevationDeg !== null && c.elevationDeg < c.green[0]) return cue('tilt-up');
  if (c.elevationDeg !== null && c.elevationDeg > c.green[1]) return cue('tilt-down');
  if (c.rollDeg !== null && Math.abs(c.rollDeg) > ROLL_CUE_DEG) return cue('roll');
  if (c.mismatchRun >= MISMATCH_CUE_RUN) return cue('mismatch');
  if (c.skyLuma !== null && c.skyLuma < DARK_SKY_LUMA) return cue('dark');
  if (c.capReached) return cue('cap');
  if (!c.closed) {
    return c.coveredDeg < ALMOST_DEG ? cue('turning', { d: Math.max(0, Math.round(360 - c.coveredDeg)) }) : cue('almost');
  }
  return c.tallSpans > 0 ? cue('done-tall', { n: c.tallSpans }) : cue('done');
}

// ---- The rail and the speed limit (2.4, 4.3, 4.4) ---------------------------

const DEG = Math.PI / 180;
const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x));
/** A keyframe paints up to 0.9 of its half-height in tangent space (4.4). */
const COVER_FRAC = 0.9;
/** Before the lock the rail is drawn for the narrowest plausible lens, 33 degrees short at 9:16 (4.1), so that
 *  tan(long / 2) = tan 16.5 x 16 / 9 and h = 25.36; the target is fixed at 20 (2.4). */
const PRE_LOCK_TAN_HALF_LONG = Math.tan(16.5 * DEG) * 16 / 9, PRE_LOCK_TARGET_DEG = 20;

/** The level rail (2.4, 4.4). With h = atan(0.9 tan(long / 2)): before the lock, target 20 with h of the narrowest
 *  lens; after it, target p1 = clamp(h - 8, 12, 30). Green is [target - 8, min(target + 4, h - 2)], amber
 *  [target - 15, h]. */
export function pitchBand(locked: boolean, longFovDeg: number): { target: number; green: [number, number]; amber: [number, number] } {
  const h = Math.atan(COVER_FRAC * (locked ? Math.tan(longFovDeg / 2 * DEG) : PRE_LOCK_TAN_HALF_LONG)) / DEG;
  const target = locked ? clamp(h - 8, 12, 30) : PRE_LOCK_TARGET_DEG;
  return { target, green: [target - 8, Math.min(target + 4, h - 2)], amber: [target - 15, h] };
}

const RATE_MAX_DEG_S = 40, RATE_MAX_MIN_DEG_S = 10, RATE_MAX_FULL_INTERVAL_MS = 36;

/** The speed limit (4.3): 40 deg/s while the median frame interval is at most 36 ms, else 1000 / interval clamped to
 *  [10, 40], which is 15 deg/s at 15 fps. With no interval yet, 40. */
export function rateMaxFromInterval(medianIntervalMs: number | null): number {
  if (medianIntervalMs === null || !(medianIntervalMs > RATE_MAX_FULL_INTERVAL_MS)) return RATE_MAX_DEG_S;
  return clamp(1000 / medianIntervalMs, RATE_MAX_MIN_DEG_S, RATE_MAX_DEG_S);
}

// ---- Constants of the frame path (3.5) -----------------------------------------

/** The analysis frame before the camera says otherwise: 9:16 at 320 on the long edge. */
const DEFAULT_W = 180, DEFAULT_H = 320;
/** Frame intervals the speed limit takes its median over. */
const RATE_WINDOW_FRAMES = 30;
/** pump's budget per frame callback, ms of measured slice paints, and its bounds in slices (3.5 step 7). */
const PUMP_BUDGET_MS = 12, PUMP_MIN_SLICES = 2, PUMP_MAX_SLICES = 30;
/** The adaptive recording readback (3.5 step 4): after this many recording readbacks, a p95 above this halves them. */
const ADAPT_AFTER_FRAMES = 30, ADAPT_P95_MS = 8;
/** The watchdog (3.5 step 10): an image change is a ZNCC under 0.6, a still pose one that moved under 0.5 degrees. */
const WATCH_EVERY_MS = 1000, WATCH_ZNCC = 0.6, WATCH_POSE_DEG = 0.5;
/** No frame for this long is a stalled camera (2.6). */
const CAMERA_STALL_MS = 1500;
/** An event's own timeStamp is believed within this much of the moment it is handled (3.2). */
const EVENT_CLOCK_MS = 2000;
/** The live mesh drops triangles with a vertex above this altitude; 70 keeps the ribbon's top of 60 (S23). */
const LIVE_ALT_CAP_DEG = 70;
/** The pace gauge reads idle below 5 deg/s and green up to 0.7 rateMax (2.5). */
const PACE_IDLE_DEG_S = 5, PACE_GOOD_FRAC = 0.7;
/** Up to this many strips go into the report as JPEG photos (13.10). */
const MAX_SLICES = 16;
/** Every measured series is capped: a phone left scanning must not grow without bound. */
const MAX_SAMPLES = 20000;
/** The raster row nearest altitude 0, as BandPanorama reads it for `observedRun`. */
const ALT0_ROW = Math.round(90 * (PANO_H - 1) / 100);
/** The tracer's azimuth support and ring sky wake at 72 observed degrees; until then they are silent (5.1). */
const AZ_MIN_COLS = Math.round(TRACE.azMinObservedDeg * PANO_W / 360);
/** A column's state reads its window model (+-15 degrees) and its neighbours' candidates (+-4 degrees), so a paint can
 *  change the state of a column this many columns away. The live tracer retraces that far beyond what was painted. */
const TRACE_MARGIN_COLS = Math.ceil((TRACE.skyPoolDeg + TRACE.azSupportDeg) * PANO_W / 360);
/** No build-time commit is injected into the UI bundle (env.d.ts declares only __APP_VERSION__), so the report and the
 *  recording say so with null rather than name a version as a commit. */
const SCANNER_COMMIT: string | null = null;

const UP: V3 = [0, 0, 1];
const EMPTY_RGBA = new Uint8ClampedArray(0);
const finiteOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const frameIdOf = (frameNo: number) => `f${String(frameNo).padStart(6, '0')}`;
const messageOf = (e: unknown) => (e instanceof Error ? e.message : String(e));

function push(values: number[], v: number): void {
  if (Number.isFinite(v) && values.length < MAX_SAMPLES) values.push(v);
}

function median(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b), n = s.length;
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
}

/** Zero-normalised cross-correlation of two luma images of one size; NaN when either is flat. */
function zncc(a: Uint8Array, b: Uint8Array): number {
  const n = Math.min(a.length, b.length);
  if (n === 0) return NaN;
  let ma = 0, mb = 0;
  for (let i = 0; i < n; i++) { ma += a[i]; mb += b[i]; }
  ma /= n; mb /= n;
  let sab = 0, saa = 0, sbb = 0;
  for (let i = 0; i < n; i++) { const da = a[i] - ma, db = b[i] - mb; sab += da * db; saa += da * da; sbb += db * db; }
  return saa > 0 && sbb > 0 ? sab / Math.sqrt(saa * sbb) : NaN;
}

/** An event's time on the performance.now() timeline (3.2): its timeStamp when that is within 2000 ms of now. */
function eventTime(stamp: number, now: number): number {
  return Number.isFinite(stamp) && Math.abs(stamp - now) <= EVENT_CLOCK_MS ? stamp : now;
}

function screenAngle(): number {
  const angle = typeof window === 'undefined' ? undefined : window.screen?.orientation?.angle;
  return typeof angle === 'number' && Number.isFinite(angle) ? ((angle % 360) + 360) % 360 : 0;
}

/** The report's browser block from `navigator.userAgentData` alone, never the user-agent string (13.10). */
function browserFacts(): ScanReport['browser'] {
  type Brand = { brand?: unknown; version?: unknown };
  const data = typeof navigator === 'undefined' ? undefined
    : (navigator as Navigator & { userAgentData?: { brands?: unknown; mobile?: unknown; platform?: unknown } }).userAgentData;
  const list: unknown = data?.brands;
  const brands = Array.isArray(list)
    ? (list as Brand[]).filter(b => typeof b?.brand === 'string')
      .map(b => (typeof b.version === 'string' ? `${b.brand as string} ${b.version}` : b.brand as string))
    : [];
  return {
    brands, mobile: typeof data?.mobile === 'boolean' ? data.mobile : null,
    platform: typeof data?.platform === 'string' ? data.platform : null,
  };
}

/** Tall runs of columns as scan-frame azimuth spans: `from` the left edge of the first column, `to` the right edge of
 *  the last, both in [0, 360), so a run across column 0 has to < from (S26). A ring that is Tall all round is the one
 *  span from 0 to 360. */
function spansOf(tall: Uint8Array): { from: number; to: number }[] {
  const n = tall.length, deg = 360 / n;
  let open = -1;
  for (let x = 0; x < n; x++) if (!tall[x]) { open = x; break; }
  if (open < 0) return [{ from: 0, to: 360 }];
  const spans: { from: number; to: number }[] = [];
  let start = 0;
  // From just after a column that is not Tall, so a run across column 0 is one span.
  for (let i = 1; i <= n; i++) {
    const x = (open + i) % n;
    if (!tall[x]) continue;
    if (!tall[(x - 1 + n) % n]) start = x;
    if (!tall[(x + 1) % n]) spans.push({ from: start * deg, to: ((x + 1) % n) * deg });
  }
  return spans;
}

/** No column seen: what Finish falls back on when its own work throws (section 6). */
function unseenColumns(): ColumnHorizon {
  const nan = () => new Float32Array(PANO_W).fill(NaN);
  return {
    alt: nan(), state: new Uint8Array(PANO_W).fill(ColState.UnknownUnseen), top: nan(), bottom: nan(),
    contrastSigma: nan(), sigmaDeg: nan(), lowLight: false,
  };
}

function copyLoop(l: LoopState): LoopState {
  return { ...l, preDeg: l.preDeg ? [l.preDeg[0], l.preDeg[1], l.preDeg[2]] : null, match: l.match ? { ...l.match } : null };
}

// ---- The raster, as the scanner watches it -------------------------------------------------

/** A BandPanorama that also tells the scanner what each paint touched and cost. The tracker paints through it like
 *  any raster. `painted` marks the columns painted since the live tracer last read them (3.5 step 10), `sliceMs` is
 *  each paint's wall time for the report, and `onClear` lets the scanner forget the Tall columns of a raster that a
 *  re-render has just wiped. */
class ScanPanorama extends BandPanorama {
  readonly painted = new Uint8Array(PANO_W);
  readonly sliceMs: number[] = [];
  onClear: (() => void) | null = null;

  paint(s: SliceSource, nowMs: number): DirtyRect {
    const t0 = performance.now();
    const rect = super.paint(s, nowMs);
    push(this.sliceMs, performance.now() - t0);
    for (let i = 0; i < Math.min(rect.w, PANO_W); i++) this.painted[(rect.x + i) % PANO_W] = 1;
    return rect;
  }

  clear(): void {
    super.clear();
    this.painted.fill(0);
    this.onClear?.();
  }
}

// ---- The scanner ------------------------------------------------------------------------------

export interface ScannerInspection {
  tracker: Tracker; pano: BandPanoramaLike; track: PoseTrack;
  lastFrame: { frameNo: number; keyframe: boolean; cls: KeyClass | null; livePose: Quat | null; cue: string } | null;
  /** ms since the camera opened, as every time the report carries; null before begin(). */
  beginMs: number | null; result: ScanResult | null;
  // Added by T22 for the replay adapter, which writes the 13.7 files from them (SPEC-v2 3.3 rule 7: a type a task
  // finds missing is declared in its own module and reported). Nothing in the app reads them.
  /** performance.now() when the camera opened: the zero of every time the report and the diagnostics carry. */
  openMs: number | null;
  /** The yaw Finish composed into the final render (north, plus the declination when one was set); null before it.
   *  It is the rotation between the scan frame and the frame of the result, for the diagnostics' world poses and for
   *  `first_seen.bin`; it is never reported, recorded or shown. */
  worldYawDeg: number | null;
  /** `firstSeen` as it stood just before Finish's re-render, in the scan frame (S30); null before Finish. */
  firstSeenAtFinish: Uint16Array | null;
}

export class PanoramaScanner implements ScannerLike {
  private readonly camera: CameraSourceLike;
  private readonly sensorOnly: boolean;
  private readonly focalPriorScale: number;
  private readonly encoder: FrameEncoder | undefined;
  private readonly track = new PoseTrack();
  private readonly pano = new ScanPanorama();
  private focal: FocalEstimator;
  private tracker: Tracker;
  private st: ScanStatus;
  private readonly subscribers = new Set<() => void>();
  private listeners: [EventTarget, string, EventListener][] = [];
  private video: HTMLVideoElement | null = null;
  private toTrue: ((azMagDeg: number) => number) | null = null;

  // Lifecycle
  /** Bumped by every start() and stop(): a start() that finds it changed after its await was superseded. */
  private generation = 0;
  private opening = false;
  private opened = false;
  private stopped = false;
  private openMs = 0;
  private begun = false;
  private paused = false;
  private hidden = false;
  private finishing = false;
  private beginT: number | null = null;
  private finishT: number | null = null;
  private result: ScanResult | null = null;
  private worldYaw: number | null = null;
  private firstSeenSnap: Uint16Array | null = null;

  // Errors, worst first in `errorText`
  private cameraError: string | null = null;
  private noOrientation = false;
  private motionDenied = false;
  private permissionBlocked = false;
  private cameraStopped = false;
  private finishError: string | null = null;
  private recordingError: string | null = null;

  // The frame path
  private frameNo = 0;
  private framesDelivered = 0;
  private captureTimePresent = 0;
  private presentedGaps = 0;
  private lastPresented: number | null = null;
  private viaRvfc = false;
  private lastFrameT: number | null = null;
  private readonly intervals: number[] = [];
  private readonly recentIntervals: number[] = [];
  private rateMax = RATE_MAX_DEG_S;
  private rate: number | null = null;
  private portraitShape = true;
  private portrait = true;
  private livePose: Quat | null = null;
  private lastFrame: ScannerInspection['lastFrame'] = null;
  private cameraStalled = false;
  private sensorStalled = false;
  private lastWatchT: number | null = null;
  private watchTiny: Uint8Array | null = null;
  private watchPose: Quat | null = null;
  private north: NorthEstimate | null = null;
  private readonly tallCol = new Uint8Array(PANO_W);
  private tallSpans: { from: number; to: number }[] = [];
  private supportActive = false;

  // Costs
  private readonly keyframeMs: number[] = [];
  private readonly readbackMs: number[] = [];
  private readonly drawMs: number[] = [];
  private redraws = 0;

  // The recording
  private recordingOn = false;
  private recorder: Recorder | null = null;
  private recordingFailed = false;
  private readonly recordReadMs: number[] = [];

  constructor(o?: ScannerOptions & { camera?: CameraSourceLike }) {
    this.camera = o?.camera ?? new CameraSource();
    this.sensorOnly = o?.sensorOnly ?? false;
    this.focalPriorScale = o?.focalPriorScale ?? 1;
    this.encoder = o?.encoder;
    const built = this.buildTracker(DEFAULT_W, DEFAULT_H);
    this.focal = built.focal;
    this.tracker = built.tracker;
    this.pano.onClear = () => this.forgetTall();
    this.st = this.computeStatus(null);
  }

  // ---- Surface ----

  get status(): ScanStatus { return this.st; }

  subscribe(cb: () => void): () => void {
    this.subscribers.add(cb);
    return () => { this.subscribers.delete(cb); };
  }

  /** The camera is playing and some orientation sample had finite beta and gamma (2.4, RS M4). */
  get canBegin(): boolean {
    return this.opened && !this.stopped && this.result === null && this.camera.playing && this.track.canPredict;
  }

  get cameraChoices(): readonly { deviceId: string; label: string }[] { return this.camera.choices; }
  get activeCameraId(): string | undefined { return this.camera.activeId; }

  readonly ribbon: ScannerLike['ribbon'] = {
    pixels: () => this.pano.rgba,
    takeDirty: () => this.pano.takeDirty(),
    // The live frame at its live pose, from the live source at L1 intrinsics and the canvas's real size (90 x 160, or
    // 120 x 160 at 3:4, S14), with the 70-degree cap (S23).
    liveMesh: (view: RibbonView) => {
      const pose = this.livePose;
      if (pose === null) return null;
      const k1 = intrinsicsAt(1, this.camera.analysisW, this.camera.analysisH, this.focal.fBest);
      const src = this.camera.liveSource();
      return liveFrameMesh(pose, k1, src ? src.width : k1.w, src ? src.height : k1.h, view, { altCapDeg: LIVE_ALT_CAP_DEG });
    },
    liveSource: () => this.camera.liveSource(),
  };

  noteMeshDraw(ms: number): void {
    push(this.drawMs, ms);
    this.redraws++;
  }

  setDeclination(toTrue: ((azMagDeg: number) => number) | null): void {
    this.toTrue = toTrue;
  }

  /** Honoured only before begin(): the recording starts at Begin (2.4). */
  setRecording(on: boolean): void {
    if (this.begun) return;
    this.recordingOn = on;
    this.refresh(null);
    this.notify();
  }

  /** Open the camera, then attach the orientation, motion, screen and visibility listeners. The iOS motion prompt is
   *  asked first, synchronously and before any await, so a caller that starts the scan from a tap gets the prompt
   *  (2.3, S14); the sheet's own tap already asks it, and a second ask is harmless. */
  async start(video: HTMLVideoElement, deviceId?: string): Promise<void> {
    const permission = requestIosMotionPermission();
    if (this.begun) throw new Error('PanoramaScanner.start: the scan has already begun');
    const generation = ++this.generation;
    this.removeListeners();
    this.camera.onFrame(null);
    this.resetSession();
    this.stopped = false;
    this.opened = false;
    this.opening = true;
    this.cameraError = null;
    this.video = video;
    void permission.then(answer => {
      if (answer !== 'denied') return;
      this.motionDenied = true;
      this.refresh(null);
      this.notify();
    });
    if (typeof window === 'undefined' || typeof (window as unknown as { DeviceOrientationEvent?: unknown }).DeviceOrientationEvent === 'undefined') {
      this.noOrientation = true;
      this.opening = false;
      this.refresh(null);
      this.notify();
      return;
    }
    void queryMotionPermission().then(state => {
      if (state !== 'denied') return;
      this.permissionBlocked = true;
      this.refresh(null);
      this.notify();
    });
    this.refresh(null);
    this.notify();
    try {
      await this.camera.open(video, deviceId);
    } catch (e) {
      if (generation !== this.generation) return;
      this.opening = false;
      this.cameraError = cameraErrorText(e);
      this.refresh(null);
      this.notify();
      throw e;
    }
    // stop(), or a newer start(), while the camera was opening: the camera has released this session, and the newer
    // call (if any) attaches what it needs. Attaching here too would leave every listener attached twice.
    if (generation !== this.generation) return;
    this.opening = false;
    this.openMs = performance.now();
    this.opened = true;
    const built = this.buildTracker(this.camera.analysisW, this.camera.analysisH);
    this.focal = built.focal;
    this.tracker = built.tracker;
    this.camera.onFrame(this.frameHandler);
    this.addListeners();
    this.refresh(null);
    this.notify();
  }

  /** Latch the predictor and start the scan (2.4). The raster's firstSeen clock starts here, and so does the
   *  recording when it was asked for. A refusal is silent: canBegin says why. */
  begin(): void {
    if (this.begun || !this.canBegin) return;
    const now = performance.now();
    this.begun = true;
    this.beginT = now;
    this.track.latch(now);
    this.pano.begin(now);
    if (this.recordingOn) {
      this.recorder = new Recorder({
        encoder: this.encoder ?? jpegEncoder(document.createElement('canvas')),
        header: {
          video: { width: this.video?.videoWidth ?? 0, height: this.video?.videoHeight ?? 0 },
          analysis: { width: this.camera.analysisW, height: this.camera.analysisH },
          settings: this.camera.settings, commit: SCANNER_COMMIT,
        },
      });
      this.recorder.action(now - this.openMs, 'begin');
    }
    this.refresh(null);
    this.notify();
  }

  /** Keyframe selection stops; the camera and sensors keep running (2.5). */
  pause(): void {
    if (!this.begun || this.result !== null || this.paused) return;
    this.paused = true;
    this.refresh(null);
    this.notify();
  }

  /** Carries on in the same predictor frame, with no relocalisation (2.5). */
  resume(): void {
    if (!this.paused) return;
    this.paused = false;
    this.refresh(null);
    this.notify();
  }

  /** The time-based states, while frames may be absent (3.5): the stalled camera, `blocked` (which advances only when
   *  a call carries a time, and a blocked phone sends nothing, S11), and a camera that has stopped playing (its track
   *  ended: CameraSource pushes no event for that, S14). */
  tick(nowMs: number): void {
    if (!this.opened || this.stopped || this.result !== null) return;
    this.track.rateAt(nowMs);
    const last = this.camera.lastFrameAt;
    this.cameraStalled = nowMs - (last ?? this.openMs) > CAMERA_STALL_MS;
    this.cameraStopped = !this.camera.playing;
    this.refresh(null);
    this.notify();
  }

  /** "Working out the horizon" (2.8), synchronously: north composed (with the declination when set), the final
   *  re-render in that frame, the tracer, the bins, the O1 merge with `previous`, the polyline and the PNG. The camera
   *  and every listener are released after. A second call returns the first result. If the work throws, the result
   *  carries the raster painted so far, a line made of the previous one where it exists and blocked elsewhere, and
   *  the exception text (section 6). */
  finish(previous: readonly HorizonPoint[] | null, endedBy: 'user' | 'hidden' | 'error' = 'user'): ScanResult {
    if (this.result !== null) return this.result;
    this.finishing = true;
    const now = performance.now();
    this.finishT = now;
    this.recorder?.action(now - this.openMs, 'finish');
    let result: ScanResult;
    try {
      result = this.work(previous, endedBy);
    } catch (e) {
      result = this.fallback(previous, e);
    }
    this.result = result;
    this.finishing = false;
    this.removeListeners();
    this.releaseCamera();
    this.refresh(null);
    this.notify();
    return result;
  }

  /** Release the camera and remove every listener. Idempotent. */
  stop(): void {
    this.stopped = true;
    this.generation++;
    this.opening = false;
    this.removeListeners();
    this.releaseCamera();
    this.refresh(null);
    this.notify();
  }

  /** The scan report (13.10), from every source it names, through buildReport so that only declared fields leave. */
  report(): ScanReport {
    const open = this.openMs;
    const facts = this.track.facts();
    const kfs = this.tracker.keyframes;
    const count = (cls: KeyClass) => kfs.reduce((n, kf) => n + (kf.cls === cls ? 1 : 0), 0);
    const fBest = this.focal.fBest, shortFov = shortFovDeg(fBest);
    const innovation = this.innovations();
    const input: ReportInput = {
      scanner: { commit: SCANNER_COMMIT, sensorOnly: this.sensorOnly },
      browser: browserFacts(),
      timeline: {
        beginMs: this.beginT !== null ? this.beginT - open : null,
        finishMs: this.finishT !== null ? this.finishT - open : null,
        endedBy: this.result !== null ? this.result.endedBy : 'cancel',
      },
      camera: {
        settings: this.camera.settings, bench: this.camera.bench, farbled: this.camera.farbled,
        exposureReadable: this.camera.exposureReadable, analysis: { w: this.camera.analysisW, h: this.camera.analysisH },
      },
      frames: {
        delivered: this.framesDelivered, viaRvfc: this.viaRvfc, captureTimePresent: this.captureTimePresent,
        slips: this.camera.slips, lag: this.camera.lag, intervalMs: statsOf(this.intervals), presentedGaps: this.presentedGaps,
      },
      // Blocked is either sign of 2.11: the all-null event the pose track saw, or the permissions query saying denied.
      sensors: {
        ...facts, blocked: facts.blocked || this.permissionBlocked,
        modeChanges: facts.modeChanges.map(c => ({ tMs: c.tMs - open, from: c.from, to: c.to })),
      },
      focal: {
        source: 'default', state: this.focal.state, fNorm: fBest, sdPct: this.focal.sdPct, ratios: this.focal.ratios,
        shortFovDeg: shortFov,
        // The closure's measurements are T28's; v0 never closes.
        closurePct: null, gyroScale: null,
        ultraWideSuspected: this.focal.state !== 'prior' && shortFov > 60,
      },
      loop: copyLoop(this.tracker.loop),
      north: this.result !== null ? this.result.north : this.track.north.estimate(this.corrYawAt),
      declinationApplied: this.result !== null ? this.result.declinationApplied : false,
      keyframes: {
        total: kfs.length, aligned: count('aligned'), blurred: count('blurred'), sensor: count('sensor'),
        ms: statsOf(this.keyframeMs), readbackMs: statsOf(this.readbackMs),
        innovationSteadyDeg: statsOf(innovation.steady), innovationAccelDeg: statsOf(innovation.accel),
        perSliceMs: statsOf(this.pano.sliceMs),
      },
      liveFrame: { drawMs: statsOf(this.drawMs), redraws: this.redraws },
      horizon: this.result !== null ? horizonFacts(this.result) : null,
      recording: { on: this.recorder !== null, every: this.recorder ? this.recorder.every : 1, frames: this.recorder ? this.recorder.frames : 0 },
      captureLog: this.tracker.log.map(r => ({ ...r, at: r.at - open })),
      slices: this.slices(),
      error: this.reportError(),
    };
    return buildReport(input);
  }

  /** The 13.9 JSON Lines, with the report as the last line; null unless recording was on at begin(). */
  recording(): string | null {
    return this.recorder ? this.recorder.finish(this.report()) : null;
  }

  inspect(): ScannerInspection {
    return {
      tracker: this.tracker, pano: this.pano, track: this.track, lastFrame: this.lastFrame,
      beginMs: this.beginT !== null ? this.beginT - this.openMs : null, result: this.result,
      openMs: this.opened || this.result !== null ? this.openMs : null, worldYawDeg: this.worldYaw,
      firstSeenAtFinish: this.firstSeenSnap,
    };
  }

  // ---- The frame path (3.5) ----

  private readonly frameHandler = (meta: FrameMeta): void => this.onCameraFrame(meta);

  private onCameraFrame(meta: FrameMeta): void {
    if (this.stopped || this.result !== null) return;
    // 1. t_f, with frameTime and its 1000 ms bound already applied by the camera.
    const t = meta.t;
    this.noteFrame(meta);
    this.tracker.setRateMax(this.rateMax);
    // 2. The pose at t_f - tau, the rate at t_f, and world up in the camera frame.
    const pred = this.track.predictAt(t - this.track.latency.tauMs);
    const rate = this.track.rateAt(t);
    this.rate = rate;
    const up: V3 | null = pred ? qrotate(qinv(pred.q), UP) : null;
    // 3. Portrait or not, then the keyframe rule, exactly once per delivered frame: its call count is meta.frameNo, the
    //    frameId of every CaptureRecord and the recording's f%06d (S24).
    this.portraitShape = meta.height > meta.width;
    this.portrait = screenAngle() === 0 && this.portraitShape;
    this.tracker.setActive(this.begun && !this.paused && this.portrait && !this.hidden);
    const step = this.tracker.onFrame(t, pred, rate);
    // 4. The recording, which may read this frame back.
    const recorded = this.recordFrame(meta);
    // 5. The step's readback: the recording's when there is one. A failed readback ends the step (S28).
    let readFailed = false;
    if (step.read && pred !== null && up !== null) {
      const f = recorded ?? this.camera.readback(meta);
      if (f === null) {
        this.tracker.noteReadFailed(t);
        readFailed = true;
      } else {
        push(this.readbackMs, f.readbackMs);
        this.tracker.hold(f, pred, rate ?? Infinity, up);
      }
    }
    // 6. The commit, timed.
    let committed: Keyframe | null = null;
    if (step.commit && !readFailed) {
      const t0 = performance.now();
      committed = this.tracker.commit();
      push(this.keyframeMs, performance.now() - t0);
    }
    // 7. A pending re-render, in slices: 30 under the replay's frozen clock, so replays stay deterministic.
    const sliceMs = this.tracker.sliceMsP50;
    this.tracker.pump(sliceMs > 0 ? clamp(Math.floor(PUMP_BUDGET_MS / sliceMs), PUMP_MIN_SLICES, PUMP_MAX_SLICES) : PUMP_MAX_SLICES);
    // 8. The live source: a GPU draw, never read back.
    this.camera.refreshLiveSource();
    // 9. The live pose. 10. Once a second, the watchdog, north and the Tall spans. Then the status, and subscribers.
    this.livePose = this.tracker.livePose(pred);
    if (this.lastWatchT === null || t - this.lastWatchT >= WATCH_EVERY_MS) {
      this.lastWatchT = t;
      this.watch();
    }
    this.refresh(t);
    this.lastFrame = {
      frameNo: meta.frameNo, keyframe: committed !== null, cls: committed ? committed.cls : null,
      livePose: this.livePose, cue: this.st.cue,
    };
    this.notify();
  }

  /** A new camera session (start() before begin()): CameraSource numbers its frames from 1 again and clears its own
   *  diagnostics, so the frame facts and costs of the session before it go too. */
  private resetSession(): void {
    this.frameNo = 0; this.framesDelivered = 0; this.captureTimePresent = 0; this.presentedGaps = 0;
    this.lastPresented = null; this.viaRvfc = false; this.lastFrameT = null; this.rate = null; this.rateMax = RATE_MAX_DEG_S;
    this.intervals.length = 0; this.recentIntervals.length = 0; this.readbackMs.length = 0; this.keyframeMs.length = 0;
    this.livePose = null; this.lastFrame = null; this.cameraStalled = false; this.sensorStalled = false; this.cameraStopped = false;
    this.lastWatchT = null; this.watchTiny = null; this.watchPose = null;
  }

  /** The frame facts the report carries, and the speed limit from the median interval of the last 30 frames. */
  private noteFrame(meta: FrameMeta): void {
    this.frameNo = meta.frameNo;
    this.framesDelivered++;
    if (meta.captureTime !== null) this.captureTimePresent++;
    if (meta.presentedFrames !== null) {
      if (this.lastPresented !== null && meta.presentedFrames - this.lastPresented > 1) this.presentedGaps++;
      this.lastPresented = meta.presentedFrames;
    }
    this.viaRvfc = meta.viaRvfc;
    if (this.lastFrameT !== null) {
      const dt = meta.t - this.lastFrameT;
      if (dt > 0) {
        push(this.intervals, dt);
        this.recentIntervals.push(dt);
        if (this.recentIntervals.length > RATE_WINDOW_FRAMES) this.recentIntervals.shift();
      }
    }
    this.lastFrameT = meta.t;
    this.cameraStalled = false;
    this.cameraStopped = false;
    this.rateMax = rateMaxFromInterval(median(this.recentIntervals));
  }

  /** 3.5 step 4: `recorder.frame` on every delivered frame, with a readback only when the recorder keeps the next one
   *  (an empty buffer otherwise, which a skipped call never reads; S24). After 30 recording readbacks a p95 above 8 ms
   *  halves them. An exception from the encoder stops recording frames, never the scan, and goes into the report's
   *  error. Returns the frame read for the recording, which the tracker then reuses. */
  private recordFrame(meta: FrameMeta): AnalysisFrame | null {
    const rec = this.recorder;
    if (rec === null || this.recordingFailed) return null;
    let f: AnalysisFrame | null = null;
    try {
      const keep = rec.keepsNext;
      if (keep) {
        f = this.camera.readback(meta);
        // Nothing to keep: the call is skipped, so the recorder keeps the next frame instead.
        if (f === null) return null;
        push(this.recordReadMs, f.readbackMs);
      }
      rec.frame({
        frameId: frameIdOf(meta.frameNo),
        tCaptureMs: meta.captureTime !== null ? meta.captureTime - this.openMs : null,
        tPresentMs: (meta.presentationTime ?? this.camera.lastFrameAt ?? meta.t) - this.openMs,
        w: f ? f.w : this.camera.analysisW, h: f ? f.h : this.camera.analysisH, rgba: f ? f.rgba : EMPTY_RGBA,
      });
      if (rec.every === 1 && this.recordReadMs.length >= ADAPT_AFTER_FRAMES && (statsOf(this.recordReadMs).p95 ?? 0) > ADAPT_P95_MS) {
        rec.setEvery(2);
      }
    } catch (e) {
      this.recordingFailed = true;
      this.recordingError = `recording: ${messageOf(e)}`;
    }
    return f;
  }

  /** Once a second (3.5 step 10). The watchdog compares this tiny readback with the last one: an image that changed
   *  (ZNCC under 0.6) under a live pose that moved less than 0.5 degrees is a stalled motion sensor, a cue only. Then
   *  north, recomputed from every sample, and the Tall spans over the columns painted since the last call. */
  private watch(): void {
    const tiny = this.camera.readbackTiny();
    const pose = this.livePose;
    let stalled = false;
    if (tiny !== null && this.watchTiny !== null && pose !== null && this.watchPose !== null) {
      stalled = zncc(tiny, this.watchTiny) < WATCH_ZNCC && angleBetweenDeg(this.watchPose, pose) < WATCH_POSE_DEG;
    }
    this.sensorStalled = stalled;
    this.watchTiny = tiny;
    this.watchPose = pose;
    this.north = this.track.north.estimate(this.corrYawAt);
    this.refreshTall();
  }

  private readonly corrYawAt = (t: number): number => this.tracker.correctionYawAt(t);

  /** The live tracer for `tallSpans` (S16, S26): the columns painted since the last call, and those a paint there can
   *  move (TRACE_MARGIN_COLS), in the scan frame of the raster. When 72 degrees come into view the tracer's azimuth
   *  support and ring sky wake up and every column can change, so that call retraces them all. */
  private refreshTall(): void {
    if (!this.begun) return;
    const active = this.observedColumns() >= AZ_MIN_COLS;
    let range: [number, number] | null;
    if (active !== this.supportActive) {
      this.supportActive = active;
      this.pano.painted.fill(0);
      range = [0, PANO_W];
    } else {
      range = this.paintedRange();
    }
    if (range === null) return;
    const traced = tracer.extract(this.pano, { columns: range, focalSdPct: this.focal.sdPct, axisAltDeg: this.axisAltDeg() });
    for (let i = 0; i < range[1] - range[0]; i++) {
      const x = (range[0] + i) % PANO_W;
      this.tallCol[x] = traced.state[x] === ColState.Tall ? 1 : 0;
    }
    this.tallSpans = spansOf(this.tallCol);
  }

  /** The shortest arc of columns holding every column painted since the last call, widened by TRACE_MARGIN_COLS on
   *  both sides, as a [x0, x0 + n) range that may run past 1080; null when nothing was painted. Clears the marks. */
  private paintedRange(): [number, number] | null {
    const painted = this.pano.painted;
    const wide = new Uint8Array(PANO_W);
    let any = false;
    for (let x = 0; x < PANO_W; x++) {
      if (!painted[x]) continue;
      any = true;
      for (let d = -TRACE_MARGIN_COLS; d <= TRACE_MARGIN_COLS; d++) wide[(x + d + PANO_W) % PANO_W] = 1;
    }
    painted.fill(0);
    if (!any) return null;
    // The longest run of untouched columns, around the wrap; the arc to trace is everything else.
    let best = 0, bestEnd = -1, run = 0;
    for (let i = 0; i < 2 * PANO_W; i++) {
      if (wide[i % PANO_W]) { run = 0; continue; }
      run++;
      if (run > best && run <= PANO_W) { best = run; bestEnd = i; }
    }
    if (best === 0) return [0, PANO_W];
    const start = (bestEnd + 1) % PANO_W;
    return [start, start + PANO_W - best];
  }

  /** Columns with a painted pixel at altitude 0: the tracer's own count for azimuth support. */
  private observedColumns(): number {
    const rgba = this.pano.rgba, row = ALT0_ROW * PANO_W * 4;
    let n = 0;
    for (let x = 0; x < PANO_W; x++) if (rgba[row + x * 4 + 3] !== 0) n++;
    return n;
  }

  private forgetTall(): void {
    this.tallCol.fill(0);
    this.tallSpans = [];
  }

  /** The median keyframe optical-axis altitude, for the tracer's off-axis focal term; NaN with no keyframe (5.2). */
  private axisAltDeg(): number {
    const alts = this.tracker.keyframes.map(kf => elevationDeg(kf.pose));
    return median(alts) ?? NaN;
  }

  // ---- Finish ----

  private work(previous: readonly HorizonPoint[] | null, endedBy: 'user' | 'hidden' | 'error'): ScanResult {
    // 1. North: magnetic, with the tracker's yaw correction (4.12). The declination is the closure's.
    const north = this.track.north.estimate(this.corrYawAt);
    let yaw = 0, declinationApplied = false;
    if (north !== null) {
      yaw = north.offsetDeg;
      if (this.toTrue !== null) {
        const composed = this.toTrue(north.offsetDeg);
        // A closure that returns nothing usable leaves the panorama magnetic, and says so.
        if (Number.isFinite(composed)) { yaw = composed; declinationApplied = true; }
      }
    }
    // 2. The final re-render in that frame. firstSeen is indexed in the scan frame, and the re-render only adds to
    //    it, so its snapshot is taken first (S30).
    this.firstSeenSnap = this.pano.firstSeen.slice();
    this.worldYaw = yaw;
    this.tracker.renderAll(yaw);
    // 3-6. The tracer, the bins, the O1 merge, the polyline.
    const columns = tracer.extract(this.pano, { focalSdPct: this.focal.sdPct, axisAltDeg: this.axisAltDeg() });
    const draft = mergeKept(binProfile(columns), previous);
    const line = conservativePolyline(draft.alt);
    // 7. The PNG.
    const png = this.pano.toPNG(document.createElement('canvas'));
    return {
      png, rgba: this.pano.rgba.slice(), draft, points: line.points, tau: line.tau, north, declinationApplied,
      focal: { state: this.focal.state, sdPct: this.focal.sdPct, shortFovDeg: shortFovDeg(this.focal.fBest) },
      loop: copyLoop(this.tracker.loop), extractor: 'tracer', partial: !this.tracker.loop.closed, endedBy,
      error: this.errorText(),
    };
  }

  private fallback(previous: readonly HorizonPoint[] | null, e: unknown): ScanResult {
    this.finishError = messageOf(e);
    const draft = mergeKept(binProfile(unseenColumns()), previous);
    const line = conservativePolyline(draft.alt);
    let png = '';
    try { png = this.pano.toPNG(document.createElement('canvas')); } catch { /* no picture, and the error says why */ }
    return {
      png, rgba: this.pano.rgba.slice(), draft, points: line.points, tau: line.tau, north: null, declinationApplied: false,
      focal: { state: this.focal.state, sdPct: this.focal.sdPct, shortFovDeg: shortFovDeg(this.focal.fBest) },
      loop: copyLoop(this.tracker.loop), extractor: 'tracer', partial: !this.tracker.loop.closed, endedBy: 'error',
      error: this.finishError,
    };
  }

  // ---- Status ----

  private refresh(t: number | null): void {
    this.st = this.computeStatus(t);
  }

  private notify(): void {
    for (const cb of [...this.subscribers]) cb();
  }

  private phase(): ScanPhase {
    if (this.result !== null) return 'done';
    if (this.finishing) return 'finishing';
    if (this.cameraError !== null || this.noOrientation) return 'failed';
    if (this.begun) return this.paused ? 'paused' : 'scanning';
    if (this.opened) return 'ready';
    return this.opening ? 'opening' : 'idle';
  }

  /** The error the user sees, worst first: the camera would not open, the browser has no orientation API, iOS denied
   *  motion, motion is blocked for the site (2.11), the camera stopped playing (section 6). */
  private errorText(): string | null {
    if (this.cameraError !== null) return this.cameraError;
    if (this.noOrientation) return ERROR_TEXT.noOrientation;
    if (this.motionDenied) return ERROR_TEXT.motionDenied;
    if (this.permissionBlocked || this.track.blocked) return ERROR_TEXT.motionBlocked;
    if (this.cameraStopped) return ERROR_TEXT.cameraStopped;
    return null;
  }

  private reportError(): string | null {
    const all = [this.errorText(), this.finishError, this.recordingError].filter((e): e is string => e !== null);
    return all.length ? [...new Set(all)].join('; ') : null;
  }

  private computeStatus(t: number | null): ScanStatus {
    const at = t ?? this.lastFrameT;
    const elevation = at !== null ? this.track.elevationAt(at) : null;
    const roll = at !== null ? this.track.rollAt(at) : null;
    const locked = this.focal.state !== 'prior';
    const band = pitchBand(locked, longFovOf(this.focal.fBest, this.camera.analysisW, this.camera.analysisH));
    const kfs = this.tracker.keyframes;
    const coveredDeg = this.tracker.coveredDeg, gapDeg = this.tracker.gapDeg;
    const loop = copyLoop(this.tracker.loop);
    const error = this.errorText();
    const cue = cueFor({
      error, begun: this.begun, portrait: this.portrait, paused: this.paused,
      cameraStalled: this.cameraStalled, sensorStalled: this.sensorStalled,
      staleInLastSecond: this.tracker.staleInLastSecond, gapDeg, rateDegS: this.rate, rateMaxDegS: this.rateMax,
      elevationDeg: elevation, green: band.green, rollDeg: roll, mismatchRun: this.tracker.mismatchRun,
      skyLuma: kfs.length ? kfs[kfs.length - 1].skyLuma : null, capReached: kfs.length >= MAX_KEYFRAMES,
      coveredDeg, closed: loop.closed, tallSpans: this.tallSpans.length,
    });
    return {
      phase: this.phase(), cue: cue.text, cueKey: cue.key, cueKind: cue.kind, error,
      frameNo: this.frameNo,
      headingDeg: this.livePose ? headingDeg(this.livePose) : null,
      northOffsetDeg: this.north ? this.north.offsetDeg : null,
      elevationDeg: elevation, rollDeg: roll, rateDegS: this.rate,
      targetPitchDeg: band.target, pitchBand: { green: band.green, amber: band.amber },
      pace: paceOf(this.rate, this.rateMax), rateMaxDegS: this.rateMax,
      coverage: this.pano.coverage, coveredDeg, gapDeg,
      tallSpans: this.tallSpans, mode: this.track.mode, north: this.north,
      focal: { state: this.focal.state, shortFovDeg: shortFovDeg(this.focal.fBest) },
      loop, keyframes: kfs.length,
      portrait: this.portrait, canBegin: this.canBegin,
      recording: this.begun ? this.recorder !== null && !this.recordingFailed : this.recordingOn,
      farbled: this.camera.farbled,
    };
  }

  // ---- Report helpers ----

  /** Innovations of accepted keyframes (T27 measures them; v0 has none), split by the 4.7 EIS test on the change in
   *  rate since the previous accepted keyframe: under 2 deg/s is steady. */
  private innovations(): { steady: number[]; accel: number[] } {
    const steady: number[] = [], accel: number[] = [];
    let prev: number | null = null;
    for (const r of this.tracker.log) {
      if (r.outcome !== 'accepted') continue;
      if (r.innovationDeg !== undefined && prev !== null && r.rateDegS !== undefined) {
        (Math.abs(r.rateDegS - prev) < 2 ? steady : accel).push(Math.abs(r.innovationDeg));
      }
      if (r.rateDegS !== undefined) prev = r.rateDegS;
    }
    return { steady, accel };
  }

  /** Up to 16 kept strips, spread evenly over the keyframes, as JPEG data URLs (13.10). A browser that cannot encode
   *  JPEG on a canvas (the replay's has no codec) gives none. */
  private slices(): { kf: number; dataUrl: string }[] {
    const kfs = this.tracker.keyframes;
    if (kfs.length === 0 || typeof document === 'undefined') return [];
    const picks = kfs.length <= MAX_SLICES ? kfs.map((_, i) => i)
      : Array.from({ length: MAX_SLICES }, (_, i) => Math.round(i * (kfs.length - 1) / (MAX_SLICES - 1)));
    const out: { kf: number; dataUrl: string }[] = [];
    try {
      const encoder = jpegEncoder(document.createElement('canvas'));
      for (const i of picks) {
        const kf = kfs[i];
        const rgba = new Uint8ClampedArray(kf.stripW * kf.stripH * 4);
        for (let p = 0, q = 0; p < kf.stripW * kf.stripH; p++, q += 3) {
          rgba[p * 4] = kf.strip[q]; rgba[p * 4 + 1] = kf.strip[q + 1]; rgba[p * 4 + 2] = kf.strip[q + 2]; rgba[p * 4 + 3] = 255;
        }
        out.push({ kf: kf.id, dataUrl: `data:${encoder.mime};base64,${encoder.encode(rgba, kf.stripW, kf.stripH)}` });
      }
    } catch {
      // What was encoded before the failure stays; the next strip would fail the same way.
    }
    return out;
  }

  // ---- Listeners and the camera ----

  private addListeners(): void {
    const on = (target: EventTarget | null | undefined, type: string, fn: EventListener) => {
      if (!target || typeof target.addEventListener !== 'function') return;
      target.addEventListener(type, fn);
      this.listeners.push([target, type, fn]);
    };
    on(window, 'deviceorientation', this.onOrientation);
    on(window, 'deviceorientationabsolute', this.onOrientation);
    on(window, 'devicemotion', this.onMotion);
    on(window.screen?.orientation, 'change', this.onScreen);
    on(document, 'visibilitychange', this.onVisibility);
  }

  private removeListeners(): void {
    for (const [target, type, fn] of this.listeners) target.removeEventListener(type, fn);
    this.listeners = [];
  }

  private releaseCamera(): void {
    this.camera.onFrame(null);
    this.camera.close();
  }

  /** Both orientation events: timestamped by 3.2, to the pose track and, when recording, to the recorder. */
  private readonly onOrientation = (e: Event): void => {
    const now = performance.now();
    const ev = e as Event & { alpha?: unknown; beta?: unknown; gamma?: unknown; absolute?: unknown; webkitCompassHeading?: unknown };
    const type = e.type === 'deviceorientationabsolute' ? 'deviceorientationabsolute' : 'deviceorientation';
    const o: OrientationIn = {
      t: eventTime(e.timeStamp, now), type,
      alpha: finiteOrNull(ev.alpha), beta: finiteOrNull(ev.beta), gamma: finiteOrNull(ev.gamma),
      absolute: typeof ev.absolute === 'boolean' ? ev.absolute : null,
      compassHeading: finiteOrNull(ev.webkitCompassHeading),
    };
    this.track.onOrientation(o);
    this.recorder?.orientation({
      event: type, tEventMs: o.t - this.openMs, tReceiveMs: now - this.openMs,
      alpha: o.alpha, beta: o.beta, gamma: o.gamma, absolute: o.absolute,
    });
    this.afterEvent();
  };

  private readonly onMotion = (e: Event): void => {
    const now = performance.now();
    const r = (e as Event & { rotationRate?: { alpha?: unknown; beta?: unknown; gamma?: unknown } | null }).rotationRate;
    const m: MotionIn = {
      t: eventTime(e.timeStamp, now),
      rate: r ? { alpha: finiteOrNull(r.alpha), beta: finiteOrNull(r.beta), gamma: finiteOrNull(r.gamma) } : null,
    };
    this.track.onMotion(m);
    this.recorder?.motion({ tEventMs: m.t - this.openMs, tReceiveMs: now - this.openMs, rate: m.rate });
    this.afterEvent();
  };

  private readonly onScreen = (): void => {
    this.portrait = screenAngle() === 0 && this.portraitShape;
    this.refresh(null);
    this.notify();
  };

  private readonly onVisibility = (): void => {
    this.hidden = document.visibilityState === 'hidden';
    this.refresh(null);
    this.notify();
  };

  /** Sensor events refresh the status only when Start's gate or the error changes, the two things an event can change
   *  that the screen shows before frames carry the rest. */
  private afterEvent(): void {
    if (this.st.canBegin !== this.canBegin || this.st.error !== this.errorText()) {
      this.refresh(null);
      this.notify();
    }
  }

  private buildTracker(frameW: number, frameH: number): { focal: FocalEstimator; tracker: Tracker } {
    const focal = new FocalEstimator({ fNorm: priorFNorm(frameW, frameH) * this.focalPriorScale, source: 'default', sdPct: PRIOR_SD_PCT });
    const tracker = new Tracker({
      pano: this.pano, focal, latency: this.track.latency, frameW, frameH, sensorOnly: this.sensorOnly,
    });
    return { focal, tracker };
  }
}

function paceOf(rate: number | null, rateMax: number): ScanStatus['pace'] {
  if (rate === null || !(rate >= PACE_IDLE_DEG_S)) return 'idle';
  if (rate <= PACE_GOOD_FRAC * rateMax) return 'good';
  return rate <= rateMax ? 'fast' : 'too-fast';
}

/** The report's horizon block: the result's tau (which may lie off TAU_LADDER, 5.4, S22) and bin counts by state,
 *  Kept read as state === Kept (5.5, S22). */
function horizonFacts(r: ScanResult): NonNullable<ScanReport['horizon']> {
  const n = { measured: 0, low: 0, unknown: 0, tall: 0, kept: 0 };
  for (const s of r.draft.state) {
    if (s === BinState.Measured) n.measured++;
    else if (s === BinState.Low) n.low++;
    else if (s === BinState.Unknown) n.unknown++;
    else if (s === BinState.Tall) n.tall++;
    else if (s === BinState.Kept) n.kept++;
  }
  return { tau: r.tau, points: r.points.length, ...n, lowLight: r.draft.lowLight };
}
