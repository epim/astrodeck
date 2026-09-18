// Camera capture retains a bounded colour panorama and an editable horizon
// draft. Phone sensor pose and lens angles remain estimates for user review.
import { DOME_CELLS, SkyPanorama, orientationBasis, dot, skyAngles, cameraLens, transferBasis, type CameraBasis } from './photosphereGeometry';
import { CameraPoseHistory, ScanPoseSource, poseSeparation, viewVouchesFor, type PoseEvidence } from './photospherePose';
import { registerFrame } from './photosphereRegistration';
import { VisualStability, GRID_W, GRID_H, STALE_FRAME_MS } from './photosphereStability';

export interface PhotosphereSupport {
  supported: boolean;
  reason: string | null;
}

/** Every branch `grabFrame` can return from, named in the order the gates
 *  appear. This is the minimal diagnostic recorder of doc 14 section 4.3:
 *  it exists so a session with zero accepted frames is still diagnosable. */
export type CaptureOutcome =
  | 'accepted'
  | 'not-recording'
  | 'not-ready'
  | 'unhealthy'
  | 'no-image'
  | 'alignment-wait'
  | 'overlap-wait'
  | 'no-target'
  | 'already-captured'
  | 'too-soon'
  | 'below-horizon'
  | 'read-failed';

export interface CaptureRecord {
  at: number;
  outcome: CaptureOutcome;
  cell?: number;
  basis?: CameraBasis;
  sensorBasis?: CameraBasis;
  adjusted?: boolean;
}

/** The log records; it decides nothing. Bounded so a long-running scan
 *  cannot grow this without limit. */
const CAPTURE_LOG_LIMIT = 4096;

/** Secure context + `getUserMedia` - what CAPTURE PHOTOSPHERE needs before it
 *  can even ask for a camera. Same reason string as the AR camera/gyro
 *  fallback (B.10) - one sentence for "this needs HTTPS", not three. */
export function checkPhotosphereSupport(): PhotosphereSupport {
  const insecure = typeof window === "undefined" || !window.isSecureContext;
  const md = typeof navigator === "undefined"
    ? undefined
    : (navigator as Navigator & { mediaDevices?: MediaDevices }).mediaDevices;
  if (insecure || !md || typeof md.getUserMedia !== "function") {
    return {
      supported: false,
      reason: "Photosphere capture needs a secure connection - set one up in Connection.",
    };
  }
  return { supported: true, reason: null };
}

/** Rec. 601 luma from an 8-bit RGB triple. */
export function luminance(r: number, g: number, b: number): number {
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

/** The value at percentile `p` (0..1), nearest-rank. Empty input is 0 - a
 *  column with nothing sampled must never read as a bright sky. */
export function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor(p * (sorted.length - 1))));
  return sorted[idx];
}

/** `skyLum` for `autoTraceSkyline`: the 80th percentile luminance of the top
 *  10% of rows across every sampled column (plan B.14 step 3) - read off
 *  whatever the top of THIS sweep actually was, so an overcast sky and a
 *  blue one both produce a usable threshold. */
export function skyLumFromColumns(columns: number[][]): number {
  const topRows: number[] = [];
  for (const col of columns) {
    const take = Math.max(1, Math.round(col.length * 0.1));
    for (let r = 0; r < take && r < col.length; r++) topRows.push(col[r]);
  }
  return percentile(topRows, 0.8);
}

/** Which azimuth bin (0..bins-1) a heading falls in. */
export function binForHeading(headingDeg: number, bins: number): number {
  const h = ((headingDeg % 360) + 360) % 360;
  return Math.min(bins - 1, Math.floor((h / 360) * bins));
}

export interface SweepFrame {
  bin: number;
  column: number[];
  altitude?: number;
  verticalFov?: number;
  band?: number;
  manualOverhead?: boolean;
}

/** Three overlapping elevation rings plus the single shared zenith point.
 * Coverage is earned by aiming at each ring, never inferred from elapsed time. */
export const SWEEP_BANDS = [
  { altitude: 0, label: "Low" },
  { altitude: 35, label: "Middle" },
  { altitude: 70, label: "High" },
] as const;
export const OVERHEAD_BAND = SWEEP_BANDS.length;

export function bandForAltitude(alt: number): number | null {
  if (alt >= 85) return OVERHEAD_BAND;
  const band = SWEEP_BANDS.findIndex(b => Math.abs(alt - b.altitude) <= 12);
  return band < 0 ? null : band;
}

/** Project every retained elevation into a common 90..-10 degree column.
 * NaN is unknown, not open sky. Overlapping frames favor their central rows;
 * an overhead sample covers only the shared zenith, not an invented sky cap.
 * Lens field of view is still an estimate and must be reviewed by the user. */
export function projectSweepColumns(frames: SweepFrame[], bins: number): number[][] {
  const cols = Array.from({ length: bins }, () => Array<number>(101).fill(NaN));
  const weights = Array.from({ length: bins }, () => Array<number>(101).fill(Infinity));
  for (const frame of frames) {
    if (!frame.column.length) continue;
    if (frame.band === OVERHEAD_BAND) {
      const sample = frame.column[Math.floor(frame.column.length / 2)];
      for (let bin = 0; bin < bins; bin++) { cols[bin][0] = sample; weights[bin][0] = -Infinity; }
      continue;
    }
    if (frame.bin < 0 || frame.bin >= bins) continue;
    const center = frame.altitude ?? 0, fov = frame.verticalFov ?? 45;
    for (let row = 0; row <= 100; row++) {
      const alt = 90 - row, fraction = .5 + (center - alt) / fov;
      if (fraction < 0 || fraction > 1) continue;
      const weight = Math.abs(fraction - .5);
      if (weight >= weights[frame.bin][row]) continue;
      const sample = frame.column[Math.round(fraction * (frame.column.length - 1))];
      if (!Number.isFinite(sample)) continue;
      cols[frame.bin][row] = sample; weights[frame.bin][row] = weight;
    }
  }
  return cols;
}

export interface SkyTrace {
  points: { az: number; alt: number }[];
  uncertainBins: number[];
}

/** Highest dark sample wins, including canopy above a lower patch of sky.
 * Missing upper-sky data and an unlit/covered zenith are conservatively blocked.
 * This produces the existing single-height horizon, not a mask of canopy gaps. */
export function traceSkyCoverage(columns: number[][]): SkyTrace {
  const skySamples = columns.flatMap(col => col.slice(0, 26).filter(Number.isFinite));
  const sky = percentile(skySamples, .8);
  const uncertainBins: number[] = [];
  const points = columns.map((column, bin) => {
    let alt = 0;
    const unknown = column.length < 101 || column.slice(0, 91).some(v => !Number.isFinite(v));
    if (unknown || sky < 40) {
      alt = 90; uncertainBins.push(bin);
    } else {
      const firstObstruction = column.findIndex(v => v < sky * .7);
      if (firstObstruction >= 0) alt = Math.max(0, Math.min(90, 91 - firstObstruction));
    }
    return { az: Math.round((bin + .5) / columns.length * 360), alt };
  });
  return { points, uncertainBins };
}

/** Fold captured frames into `autoTraceSkyline`'s `columns[]`: one column per
 *  azimuth bin, keeping the LAST frame seen for a bin (the most recent pass
 *  through that heading). Empty bins stay `[]`, never a zeroed column -
 *  `autoTraceSkyline` already reads a short/empty column as "no drop found",
 *  i.e. open to the bottom of frame, which is the honest default where there
 *  is no data. */
export function foldSweepColumns(frames: SweepFrame[], bins: number): number[][] {
  const out: (number[] | undefined)[] = new Array(bins);
  for (const f of frames) out[f.bin] = f.column;
  return Array.from(out, (c) => c ?? []);
}

/** Per-row luminance for one already-drawn video frame: `rows` samples
 *  spaced evenly down the frame, each a full-width average so a single hot
 *  pixel cannot fake a sky-to-ground transition. Pure function of the pixel
 *  buffer, so it is testable with a fabricated `Uint8ClampedArray` and no
 *  live video element. */
export function columnFromImageData(
  data: Uint8ClampedArray,
  width: number,
  height: number,
  rows: number,
): number[] {
  const out: number[] = [];
  for (let r = 0; r < rows; r++) {
    const y = Math.min(height - 1, Math.floor((r / Math.max(1, rows - 1)) * (height - 1)));
    let sum = 0;
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      sum += luminance(data[i], data[i + 1], data[i + 2]);
    }
    out.push(sum / Math.max(1, width));
  }
  return out;
}

export interface SweepCamera { deviceId: string; label: string }

/** Rear-camera elevation depends on tilt, not compass heading. In particular,
 * a phone looking straight up can report valid tilt with no absolute bearing. */
export function cameraElevation(e: { beta: number | null; gamma: number | null }): number | null {
  if (![e.beta, e.gamma].every(v => typeof v === "number" && Number.isFinite(v))) return null;
  const rad = Math.PI / 180;
  return Math.asin(Math.max(-1, Math.min(1, -Math.cos(e.beta! * rad) * Math.cos(e.gamma! * rad)))) / rad;
}

/** Labels are vendor-dependent. Never infer lens type from device order. */
export function preferredRearCamera(cameras: SweepCamera[]): string | undefined {
  const rear = cameras.filter(c => /back|rear|environment/i.test(c.label)
    && !/ultra|telephoto|front|\b0[.,][56]\b/i.test(c.label));
  return rear.find(c => /main|wide|standard|\b1x\b/i.test(c.label))?.deviceId
    ?? (rear.length === 1 ? rear[0].deviceId : undefined);
}

/** Rear camera's viewing ray in the earth frame (W3C Z-X-Y rotation).
 * Unlike a flat-phone compass, this works with an upright/landscape camera.
 * Relative orientation alone must never be treated as geographic north. */
export function cameraPose(e: { alpha: number | null; beta: number | null;
  gamma: number | null; absolute?: boolean; webkitCompassHeading?: number }, absoluteEvent = false): { az: number; alt: number } | null {
  const compass = e.webkitCompassHeading;
  const hasCompass = typeof compass === "number" && Number.isFinite(compass);
  if (!absoluteEvent && !e.absolute && !hasCompass) return null;
  if (![e.alpha, e.beta, e.gamma].every(v => typeof v === "number" && Number.isFinite(v))) return null;
  const rad = Math.PI / 180, a = e.alpha! * rad, b = e.beta! * rad, g = e.gamma! * rad;
  const x = -Math.cos(a) * Math.sin(g) - Math.sin(a) * Math.sin(b) * Math.cos(g);
  const y = -Math.sin(a) * Math.sin(g) + Math.cos(a) * Math.sin(b) * Math.cos(g);
  const z = -Math.cos(b) * Math.cos(g);
  const correction = hasCompass ? compass! - (360 - e.alpha!) : 0;
  // At the zenith azimuth is undefined. The capture treats it as one shared
  // overhead tile; it never bins that arbitrary bearing as a horizontal view.
  return { az: Math.hypot(x, y) < 1e-6 ? 0 : ((Math.atan2(x, y) / rad + correction) % 360 + 360) % 360,
    alt: Math.asin(Math.max(-1, Math.min(1, z))) / rad };
}

/** How long a heading or tilt reading stands on its own before the video has
 *  to vouch for it. Not a staleness timeout on a change-driven stream: a still
 *  phone sends nothing and that silence is not staleness (issue #37). But
 *  `sourceHealthy` measures LIFECYCLE only, and a magnetometer that simply
 *  stops - wedged sensor, permission revoked with no lifecycle event, a stuck
 *  Chromium pump - leaves every one of those flags true. Past this window the
 *  reading is believed only while the video says the view has not moved since
 *  it arrived, so a dead sensor over a moving view is caught within 2 s. */
const SENSOR_SILENCE_MS = 2000;
/** Consecutive failures to read the preview's pixels before the cue says so.
 *  Unknown stability means the strict rule applies, which means a still phone
 *  cannot capture at all - so this failing silently is the original deadlock
 *  with no diagnosis. Five frames is a sixth of a second on the video-frame
 *  path and under two seconds on the interval fallback. */
const STILLNESS_BLIND_AFTER = 5;

/** Opens a visible preview; recording begins only after begin() is pressed. */
export class PhotosphereSweep {
  private stream: MediaStream | null = null;
  private video: HTMLVideoElement | null = null;
  private canvas: HTMLCanvasElement | null = null;
  private frames: SweepFrame[] = [];
  private headingHandler: ((e: Event) => void) | null = null;
  private heading = 0;
  private altitude = 0;
  // Both on the PERFORMANCE clock, the one orientation timestamps and video
  // frame times share. A wall-clock reading here cannot be compared with either.
  private tiltAt: number | null = null;
  private headingAt: number | null = null;
  private hasOrientation = false;
  private basis: CameraBasis | null = null;
  private panorama: SkyPanorama | null = null;
  private coveredCells = new Set<number>();
  private lastAlpha = 0;
  private imageAspect = 4 / 3;
  private shortAxisFov=60;
  private lensCalibrated=false;
  private lensProfileKey='';
  private lastCaptureAt: number | null = null;
  private poses = new CameraPoseHistory();
  private poseSource=new ScanPoseSource();
  private visualAnchor:{raw:CameraBasis;aligned:CameraBasis}|null=null;
  private lastRegistrationAt=-Infinity;
  private tilts = new CameraPoseHistory();
  private stability = new VisualStability();
  private lumaCanvas: HTMLCanvasElement | null = null;
  private stillnessFailures = 0;
  /** The media clock of the last frame a NON-CALLBACK grab took as evidence, in
   *  seconds; -1 before any. Not the interval fallback alone: `captureOverhead`
   *  reaches `grabFrame` with no frame of its own, so it runs the same gate on a
   *  device that has requestVideoFrameCallback. The gate consumes as it answers
   *  (see `newMediaFrame`), so this field advances on every `true`. */
  private lastMediaTime = -1;
  private luma = new Uint8Array(GRID_W*GRID_H);
  private listening = false;
  private trackEnded = false;
  private alignmentWait = false;
  private overlapWait = false;
  private scanSamples:unknown[]=[];
  private lastDiagnosticAt=-Infinity;
  private lastSensorReading:unknown=null;
  private videoFrameHandle: number | null = null;
  private frameBasis: {basis:CameraBasis;at:number} | null = null;
  private recording = false;
  private ready = false;
  // The diagnostic recorder (doc 14 4.3): one record per grabFrame call,
  // naming the branch it returned from. Records, decides nothing.
  private captureRecords: CaptureRecord[] = [];
  // Distinct from `panorama` existing: `begin()` allocates an empty panorama
  // before any pixel has ever been written to it, and panoramaPixels must
  // stay null until a capture has actually landed, not merely been started.
  private hasCapturedFrame = false;
  private issue: string | null = null;
  private cameras: SweepCamera[] = [];
  private deviceId = "";
  private grabTimer: ReturnType<typeof setInterval> | null = null;
  private generation = 0;
  readonly bins: number;

  constructor(bins = 30) {
    this.bins = bins;
  }

  get frameCount(): number {
    return this.frames.length;
  }

  get currentHeading(): number {
    return this.heading;
  }

  /** Whether an absolute camera pose has arrived during this scan. */
  get usedOrientation(): boolean {
    return this.hasOrientation;
  }

  get previewReady(): boolean { return this.ready; }
  get isRecording(): boolean { return this.recording; }
  get cameraChoices(): SweepCamera[] { return this.cameras; }
  get activeCameraId(): string { return this.deviceId; }
  get error(): string | null { return this.issue; }
  /** The newest 4096 grabFrame outcomes, oldest first. Survives `stop()` -
   *  a finished scan must still be diagnosable - and is cleared only by a
   *  fresh `start()`. A fresh array each read: `readonly` is erased at
   *  runtime, and a consumer that memoises by reference would never see a
   *  record arrive in an array mutated in place. */
  get captureLog(): readonly CaptureRecord[] { return this.captureRecords.slice(); }
  /** A copy of the mosaic's own pixels, RGBA, or null before any frame has
   *  actually been written into it (an empty panorama from `begin()` alone
   *  does not count). Never the live buffer: the panorama keeps writing to
   *  it after this copy is taken. */
  get panoramaPixels(): { width: number; height: number; pixels: Uint8ClampedArray } | null {
    if (!this.panorama || !this.hasCapturedFrame) return null;
    return { width: this.panorama.width, height: this.panorama.height, pixels: new Uint8ClampedArray(this.panorama.pixels) };
  }
  // Ready iff a reading has arrived this session, the source is currently alive
  // (see sourceHealthy) AND that reading is still worth something - recent, or
  // vouched for by a video that says the view has not moved since it arrived.
  // No bare freshness window, which is what deadlocked a still phone (issue
  // #37); no lifecycle-only test either, which cannot see a sensor that stops.
  get compassReady(): boolean { return this.hasOrientation && this.sourceHealthy && this.vouched(this.headingAt); }
  get tiltReady(): boolean { return this.tiltAt !== null && this.sourceHealthy && this.vouched(this.tiltAt); }
  /** Is a reading taken at `at` still the phone's direction? Recent enough to
   *  stand alone, or the video vouches that nothing has moved since. */
  private vouched(at: number | null): boolean {
    if (at === null) return false;
    const now = performance.now();
    return now - at < SENSOR_SILENCE_MS || viewVouchesFor(at, this.stability.continuity(now));
  }
  get currentAltitude(): number { return this.altitude; }
  get cameraBasis(): CameraBasis | null {
    const b=this.compassReady && this.frameBasis && performance.now()-this.frameBasis.at<200?this.frameBasis.basis
      :this.compassReady ? this.basis : this.tiltReady && this.altitude >= 85 ? this.basis : null;
    return b?this.correctBasis(b):null;
  }
  private correctBasis(b:CameraBasis):CameraBasis {return this.visualAnchor?transferBasis(b,this.visualAnchor.raw,this.visualAnchor.aligned):b;}
  get aspectRatio(): number { return this.video?.videoWidth && this.video.videoHeight ? this.video.videoWidth/this.video.videoHeight : this.imageAspect; }
  get cameraViewAngle():number {return this.shortAxisFov;}
  get hasLensCalibration():boolean {return this.lensCalibrated;}
  get lens() {return cameraLens(this.aspectRatio,1,this.shortAxisFov);}
  setCameraViewAngle(degrees:number):boolean {
    if(this.recording || !Number.isFinite(degrees) || degrees<35 || degrees>100)return false;
    this.shortAxisFov=degrees;this.lensCalibrated=true;
    try {if(this.lensProfileKey)localStorage.setItem(this.lensProfileKey,String(degrees));}catch { /* session-only when storage is blocked */ }
    return true;
  }
  get cells() { return DOME_CELLS.map(c => ({ ...c, captured:this.coveredCells.has(c.id) })); }
  get aimTarget(): { id: number; captured: boolean } | null {
    const basis=this.cameraBasis;if(!basis)return null;
    // Eight degrees leaves every cell comfortably within the captured image,
    // including portrait framing. The UI and capture use this same forward ray.
    let nearest: {id:number;captured:boolean}|null=null, similarity=Math.cos(8*Math.PI/180);
    for(const cell of DOME_CELLS){
      if(!this.compassReady && cell.alt<89)continue;
      const alignment=dot(cell.center,basis.forward);
      if(cell.alt>89 && alignment<Math.cos(5*Math.PI/180))continue;
      if(alignment>similarity){similarity=alignment;nearest={id:cell.id,captured:this.coveredCells.has(cell.id)};}
    }
    return nearest;
  }
  get justCaptured(): boolean { return this.lastCaptureAt!==null && Date.now()-this.lastCaptureAt<1000; }
  get captureCue(): string {
    if(this.issue)return this.issue;
    if(!this.recording)return 'Tap Start scan to begin capturing.';
    if(!this.video?.videoWidth || !this.video?.videoHeight)return 'Waiting for a camera image…';
    // Not "hold still": holding still is exactly what cannot be confirmed here,
    // so asking for it would leave the user doing the one thing that can never
    // satisfy the rule. Moving produces a sensor event, which does.
    if(this.stillnessFailures>=STILLNESS_BLIND_AFTER)return 'I can’t read the camera image to tell whether the phone is holding still. Move the phone slightly to register a direction.';
    if(!this.cameraBasis)return 'Waiting for the compass. Keep the camera open and move the phone gently.';
    if(this.alignmentWait)return 'Hold the phone still for a moment so the image and direction line up.';
    if(this.overlapWait)return 'I can’t match this view yet. Return to a green patch, hold still, then move slowly toward the next blue dot. Keep the camera lens in the same spot.';
    if(this.justCaptured)return 'Captured. Move to another blue dot.';
    const target=this.aimTarget;
    if(target?.captured)return 'Already captured. Aim at a blue dot.';
    if(target)return 'Hold here… capturing this patch.';
    return 'Bring a blue dot into the centre ring.';
  }
  get coverageRows(): boolean[][] {
    return SWEEP_BANDS.map((_, band) => Array.from({ length: this.bins }, (_, bin) =>
      this.frames.some(f => f.band === band && f.bin === bin)));
  }
  get overheadCaptured(): boolean { return this.frames.some(f => f.band === OVERHEAD_BAND); }
  get usedManualOverhead(): boolean { return this.frames.some(f => f.manualOverhead); }
  get capturedTiles(): number {
    return this.coveredCells.size;
  }
  get totalTiles(): number { return DOME_CELLS.length; }
  get complete(): boolean { return this.capturedTiles === this.totalTiles; }
  get currentBand(): number | null { return this.tiltReady ? bandForAltitude(this.altitude) : null; }
  get nextBand(): number {
    const missing = this.coverageRows.findIndex(row => row.some(seen => !seen));
    return missing >= 0 ? missing : OVERHEAD_BAND;
  }

  begin(): void {
    if (!this.ready || !this.compassReady) return;
    this.frames = []; this.panorama = new SkyPanorama(); this.coveredCells.clear(); this.lastCaptureAt=null; this.scanSamples=[]; this.lastDiagnosticAt=-Infinity; this.hasCapturedFrame = false; this.recording = true;
  }

  /** The user explicitly aims the rear camera up. This still reads an actual
   * video frame and cannot stand in for any missing azimuth/elevation ring. */
  captureOverhead(): boolean {
    if (this.overheadCaptured) return false;
    return this.grabFrame(true);
  }

  async start(video: HTMLVideoElement, canvas: HTMLCanvasElement, deviceId?: string): Promise<void> {
    this.stop();
    const generation = this.generation;
    this.video = video;
    this.canvas = canvas;
    this.frames = []; this.issue = null; this.basis = null; this.panorama = null; this.coveredCells.clear(); this.lastCaptureAt=null;
    // A new scan: the diagnostic log from any earlier session is no longer
    // about this camera session, so it starts over. `stop()` never does this.
    this.captureRecords = []; this.hasCapturedFrame = false;
    this.poses.clear();this.tilts.clear();this.poseSource.clear();this.visualAnchor=null;this.lastRegistrationAt=-Infinity;this.frameBasis=null;this.alignmentWait=false;this.overlapWait=false;this.lastSensorReading=null;
    this.stability.clear();this.trackEnded=false;this.lastMediaTime=-1;
    this.hasOrientation = false; this.tiltAt = null; this.headingAt = null;
    const DOE = window.DeviceOrientationEvent as typeof DeviceOrientationEvent & { requestPermission?: () => Promise<string> };
    // Ask from the click gesture, before awaiting camera discovery (Safari).
    const motionPermission = DOE?.requestPermission?.().catch(() => "denied");
    const list = async (): Promise<SweepCamera[]> => {
      try { return (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "videoinput")
        .map((d, i) => ({ deviceId: d.deviceId, label: d.label || `Camera ${i + 1}` })); }
      catch { return []; }
    };
    this.cameras = await list();
    if (generation !== this.generation) return;
    const open = (id?: string) => navigator.mediaDevices.getUserMedia({ audio: false, video: {
      ...(id ? { deviceId: { exact: id } } : { facingMode: { ideal: "environment" } }),
      width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 24, max: 30 },
    } });
    let stream = await open(deviceId ?? preferredRearCamera(this.cameras));
    // Closing the editor while the browser permission prompt is open must
    // also release a camera granted after the editor has disappeared.
    if (generation !== this.generation) { stream.getTracks().forEach(t=>t.stop()); return; }
    this.stream = stream;
    this.cameras = await list();
    if (generation !== this.generation) return;
    const preferred = deviceId ?? preferredRearCamera(this.cameras);
    const current = stream.getVideoTracks?.()[0]?.getSettings?.().deviceId;
    if (preferred && current && preferred !== current) {
      stream.getTracks().forEach(t => t.stop());
      stream = await open(preferred);
      if (generation !== this.generation) { stream.getTracks().forEach(t => t.stop()); return; }
      this.stream = stream;
    }
    this.deviceId = stream.getVideoTracks?.()[0]?.getSettings?.().deviceId ?? preferred ?? "";
    video.srcObject = this.stream;
    try { await video.play(); }
    catch { this.stop(); throw new Error("The camera opened but its preview could not play. Try opening the camera again."); }
    if (generation !== this.generation) return;
    this.ready = true;
    this.imageAspect = video.videoWidth && video.videoHeight ? video.videoWidth/video.videoHeight : 4/3;
    // Calibration belongs to this lens and crop in this browser. It must not
    // become a universal constant for another phone, lens or video aspect.
    const crop=Math.min(this.imageAspect,1/this.imageAspect).toFixed(3);
    this.lensProfileKey=`astrodeck.photosphere.lens.${this.deviceId || 'default'}.${crop}`;
    this.shortAxisFov=60;this.lensCalibrated=false;
    try {const stored=Number(localStorage.getItem(this.lensProfileKey));if(stored>=35&&stored<=100){this.shortAxisFov=stored;this.lensCalibrated=true;}}catch { /* private browsing */ }
    stream.getVideoTracks?.().forEach(track => track.addEventListener?.("ended", () => {
      if (generation !== this.generation) return;
      this.issue = "The camera stopped. Close the scan and open it again.";
      this.ready = false; this.recording = false; this.trackEnded = true;
    }));

    if (motionPermission && await motionPermission !== "granted") {
      this.issue = "Motion access was denied. Allow motion sensors to scan, or draw the horizon by hand.";
    }
    if (generation !== this.generation) return;
    if (typeof window !== "undefined" && "DeviceOrientationEvent" in window) {
      this.headingHandler = (e: Event) => {
        const oe = e as DeviceOrientationEvent & { webkitCompassHeading?: number };
        const screenAngle = window.screen?.orientation?.angle ?? 0;
        const received=performance.now();
        // DOM event timestamps and video captureTime share the performance time
        // origin. Fall back for older implementations using epoch timestamps.
        const at=Number.isFinite(e.timeStamp)&&Math.abs(received-e.timeStamp)<2000?e.timeStamp:received;
        const elevation = cameraElevation(oe);
        if (elevation !== null) { this.altitude = elevation; this.tiltAt = at;
          this.tilts.add({at,screenAngle,basis:orientationBasis(0,oe.beta!,oe.gamma!,screenAngle)});
        }
        const pose = cameraPose(oe, e.type === "deviceorientationabsolute");
        const valid=[oe.alpha,oe.beta,oe.gamma].every(v=>typeof v==='number'&&Number.isFinite(v));
        const correction = typeof oe.webkitCompassHeading === 'number' ? oe.webkitCompassHeading - (360 - oe.alpha!) : 0;
        const accepted=valid?this.poseSource.accept(orientationBasis(oe.alpha!,oe.beta!,oe.gamma!,screenAngle,correction),!!pose,at):null;
        if (accepted) { this.heading = skyAngles(accepted.basis.forward).az;
          this.lastSensorReading={alpha:oe.alpha,beta:oe.beta,gamma:oe.gamma,absolute:oe.absolute,event:e.type,screenAngle,at};
          this.lastAlpha = oe.alpha!;
          this.basis = accepted.basis;
          if(accepted.changedSource){this.poses.clear();this.frameBasis=null;}
          this.poses.add({at,screenAngle,basis:this.basis});
          this.hasOrientation = true; this.headingAt = at;
        } else if (elevation !== null && elevation >= 85 && !this.compassReady) {
          // No absolute bearing at the zenith: retain the last azimuth frame
          // for display, but only project the single overhead pixel below.
          this.basis = orientationBasis(this.lastAlpha,oe.beta!,oe.gamma!,screenAngle);
        }
      };
      window.addEventListener("deviceorientationabsolute", this.headingHandler);
      window.addEventListener("deviceorientation", this.headingHandler);
      this.listening = true;
    }

    if(typeof video.requestVideoFrameCallback==='function'){
      let lastSample=-Infinity;
      const frame:VideoFrameRequestCallback=(now,metadata)=>{
        if(generation!==this.generation)return;
        // The sensor stops talking when the phone stops moving. Ask the video
        // and the page lifecycle instead, and hand both answers to forFrame.
        // What the frame shows happened when the CAMERA saw it, not when this
        // callback ran: captureTime is tens of ms earlier, and stamping the
        // pixels with the callback time pushes every still run forward of the
        // reading it has to reach back to. A capture time later than the
        // callback, or older than a stale frame, cannot belong to this frame,
        // so it is not believed and the callback time stands. Only the STILLNESS
        // stamp is decided here: the raw captureTime below is validated again
        // inside CameraPoseHistory.forFrame, by rules of its own that err toward
        // returning no pose at all. Two validations of one field, deliberately,
        // because they answer different questions - if either is changed, read
        // the other (photospherePose.ts, the captureTime branch).
        const capture=metadata.captureTime;
        const seen=capture!==undefined&&Number.isFinite(capture)&&capture<=now&&now-capture<=STALE_FRAME_MS?capture:now;
        this.observeStillness(video,seen);
        const evidence:PoseEvidence={view:this.stability.continuity(now),sourceHealthy:this.sourceHealthy};
        const basis=this.poses.forFrame(now,metadata.captureTime,evidence);
        if(basis)this.frameBasis={basis,at:now};
        else this.frameBasis=null;
        if(now-lastSample>=350){lastSample=now;this.grabFrame(false,{basis,tilt:this.tilts.forFrame(now,metadata.captureTime,evidence)});}
        this.videoFrameHandle=video.requestVideoFrameCallback(frame);
      };
      this.videoFrameHandle=video.requestVideoFrameCallback(frame);
    } else this.grabTimer = setInterval(() => this.grabFrame(), 350);
  }

  /** Is the pose stream ALIVE? Measured, never inferred from event silence:
   *  both orientation listeners attached, the page visible, and no camera
   *  track ended. Read at the moment of use so a visibility change or a
   *  removed listener takes effect without waiting for an event of its own. */
  private get sourceHealthy(): boolean {
    const visibility = typeof document === "undefined" ? undefined : document.visibilityState;
    return this.listening && !this.trackEnded && (visibility === undefined || visibility === "visible");
  }

  /** Sample the preview into a 32x24 luminance grid, the video's own answer to
   *  "is this view holding still". A plain detached canvas rather than an
   *  OffscreenCanvas: every browser that reaches this code already has one,
   *  and 768 pixels per frame is cheap enough for the UI thread.
   *  `at` is when the CAMERA saw this frame, the meaning
   *  `VisualStability.observe` gives its own `at` - but only one caller can
   *  honour it. The rVFC path has the frame's `captureTime` and passes that.
   *  The timer path has no frame metadata at all, so `grabFrame` (and
   *  `captureOverhead` through it) passes the READ instant, which is the
   *  capture time plus however long the camera pipeline took. That inflates
   *  every break's `from` on that path by the delay, and `from` is what the
   *  150 ms CONTINUITY_SLOP_MS margin is measured against, so a delayed camera
   *  there can lose a hold it earned (issue #48). */
  private observeStillness(video: HTMLVideoElement, at: number): void {
    if (!video.videoWidth || !video.videoHeight) return;
    try {
      if (!this.lumaCanvas) {
        this.lumaCanvas = document.createElement("canvas");
        this.lumaCanvas.width = GRID_W; this.lumaCanvas.height = GRID_H;
      }
      // This reads its own pixels back every single frame, which is the one
      // access pattern a GPU-backed canvas is worst at.
      const ctx = this.lumaCanvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) throw new Error("no 2d context for the stillness sample");
      ctx.drawImage(video, 0, 0, GRID_W, GRID_H);
      const { data } = ctx.getImageData(0, 0, GRID_W, GRID_H);
      for (let p = 0; p < this.luma.length; p++) this.luma[p] = luminance(data[p*4], data[p*4+1], data[p*4+2]);
      this.stability.observe(at, this.luma, GRID_W, GRID_H);
      this.stillnessFailures = 0;
    } catch {
      // A lost drawing context tells us nothing, so stability stays unknown -
      // and unknown means the strict rule, which means a STILL phone can never
      // capture, forever, behind a cue telling it to hold still. Swallowing
      // this reinstates the whole defect with nothing anywhere recording why,
      // so it is counted, said in the cue and carried in the alignment report.
      this.stillnessFailures++;
    }
  }

  /** The interval fallback runs on a TIMER, and a timer proves nothing about
   *  the camera: a paused or stalled element keeps its last decoded image and
   *  its dimensions, and re-reading that image every 350 ms would earn a hold
   *  the camera never witnessed (review 15, P1). A frame counts only when the
   *  element is playing with data and the media clock has moved since the last
   *  one, and the track behind it is live and not muted. */
  private newMediaFrame(video: HTMLVideoElement): boolean {
    const track = this.stream?.getVideoTracks?.()[0];
    if (video.paused || video.ended || video.readyState < 2) return false;           // 2 = HAVE_CURRENT_DATA
    if (track && (track.readyState !== "live" || track.muted)) return false;
    const t = video.currentTime;
    if (!(Number.isFinite(t) && t > this.lastMediaTime)) return false;
    this.lastMediaTime = t;
    return true;
  }

  /** Append one outcome to the diagnostic log. This records; it never decides
   *  anything - every gate below still returns its own `false` on its own
   *  terms, this just names which one fired. */
  private recordCapture(now: number, outcome: CaptureOutcome, extra?: { cell?: number; basis?: CameraBasis; sensorBasis?: CameraBasis; adjusted?: boolean }): void {
    this.captureRecords.push({ at: now, outcome, ...extra });
    if (this.captureRecords.length > CAPTURE_LOG_LIMIT) this.captureRecords.splice(0, this.captureRecords.length - CAPTURE_LOG_LIMIT);
  }

  private grabFrame(manualOverhead = false, frame?:{basis:CameraBasis|null;tilt:CameraBasis|null}): boolean {
    const { video, canvas } = this;
    const now=performance.now();
    // Browsers without requestVideoFrameCallback - Firefox Android, notably -
    // reach the preview's pixels only here, so this path takes its own sample.
    // Without it stability is permanently unknown there, the strict rule never
    // relaxes and a still phone deadlocks exactly as it did before any of this.
    // Only a NEWLY DELIVERED frame is evidence (see newMediaFrame): the timer
    // firing is not the camera producing a picture. A hold is therefore earned
    // only while the media clock is moving. While frames do keep arriving every
    // tick carries a new one - 350 ms at 24 fps is eight frames - so the gate
    // refuses nothing and the settle still lands inside the window
    // photosphereStillnessDom pins on this path, at least 500 ms of watched
    // stillness and no more than the 1.5 s acceptance budget. A frozen
    // or paused element contributes no observation at all, so stability goes
    // UNKNOWN STALE_FRAME_MS after the last real frame and the strict rule
    // takes back over - the honest outcome, and the one a timer on its own
    // could never reach.
    // The limit on that window claim, stated because nothing here tests it:
    // observations on this path carry the READ instant, not a capture time
    // (see observeStillness), so a camera whose frames arrive late pushes every
    // break's `from` later by the delay. Past the 150 ms margin that costs
    // holds - intermittently up to about half a second of delay, and then
    // always (issue #48). The window above is the window for an element whose
    // frames are not delayed, which is the only element the harness can model:
    // its interval tick has no capture time to lag.
    if (!frame && video && this.sourceHealthy && this.newMediaFrame(video)) this.observeStillness(video, now);
    // One visibility rule, not two. A second copy of the test here could
    // disagree with the sourceHealthy the evidence below is built from.
    // Split into three named outcomes rather than one combined check, so the
    // log says WHICH of these was true rather than just "some gate failed".
    if (!this.recording) { this.recordCapture(now, 'not-recording'); return false; }
    if (!this.ready || !video || !canvas) { this.recordCapture(now, 'not-ready'); return false; }
    if (!this.sourceHealthy) { this.recordCapture(now, 'unhealthy'); return false; }
    if (video.videoWidth === 0 || video.videoHeight === 0) {
      if (manualOverhead) this.issue = "Waiting for a camera image. Keep the rear camera pointing up and try again.";
      this.recordCapture(now, 'no-image');
      return false;
    }
    const evidence:PoseEvidence={view:this.stability.continuity(now),sourceHealthy:this.sourceHealthy};
    const rawBasis=frame ? frame.basis : this.poses.forFrame(now,undefined,evidence);
    let basis=rawBasis?this.correctBasis(rawBasis):null;
    const tilt=frame ? frame.tilt : this.tilts.forFrame(now,undefined,evidence);
    let measured=basis?skyAngles(basis.forward):tilt?skyAngles(tilt.forward):null;
    const overhead=manualOverhead || (!!measured && bandForAltitude(measured.alt)===OVERHEAD_BAND);
    if(!manualOverhead && !basis && !(tilt&&overhead)){this.alignmentWait=true;this.recordCapture(now,'alignment-wait');return false;}
    // A timestamp does not make a frame taken during motion sharp or account
    // for an entire low-light exposure. Hold still even with frame timestamps.
    const stable=basis?this.poses.forFrame(now,undefined,evidence):this.tilts.forFrame(now,undefined,evidence);
    if(!manualOverhead && (!stable || poseSeparation(stable,(rawBasis??tilt)!)>1.5)){this.alignmentWait=true;this.recordCapture(now,'alignment-wait');return false;}
    this.alignmentWait=false;
    if(!manualOverhead && basis){
      const target=DOME_CELLS.find(c=>dot(c.center,basis!.forward)>=Math.cos((c.alt>89?5:8)*Math.PI/180));
      if(!target){this.overlapWait=false;this.recordCapture(now,'no-target');return false;}
      if(now-this.lastRegistrationAt<600){this.recordCapture(now,'too-soon');return false;}
      this.lastRegistrationAt=now;
    }
    // All pixel positions and metadata use the pose of this frame. Relative
    // tilt must never be combined with a different absolute bearing.
    if (!manualOverhead && measured!.alt < -10) { this.recordCapture(now,'below-horizon'); return false; }
    const ctx = canvas.getContext("2d");
    if (!ctx) { this.issue = "Could not read the camera image. Close the scan and try again."; this.recording = false; this.recordCapture(now,'read-failed'); return false; }
    this.imageAspect = video.videoWidth / video.videoHeight;
    canvas.width = this.imageAspect >= 1 ? 320 : Math.round(320*this.imageAspect);
    canvas.height = this.imageAspect >= 1 ? Math.round(320/this.imageAspect) : 320;
    let data: Uint8ClampedArray;
    // Filled only on the normal, non-overhead capture path below, for the
    // 'accepted' record: a manual or tilt-only overhead capture has no single
    // DOME_CELLS target, so it is logged accepted with none of these set.
    let capturedCell: number | undefined;
    let capturedBasis: CameraBasis | undefined;
    let capturedSensorBasis: CameraBasis | undefined;
    let capturedAdjusted: boolean | undefined;
    try {
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      // Unreachable while `recording` is true: `begin()` allocates the
      // panorama in the same statement it sets `recording`, and nothing
      // clears one without the other. Kept, and logged as `no-image` (the
      // nearest existing outcome - nothing to add a frame to), as a
      // defensive TS non-null guard rather than a real behavioural gate.
      if (!this.panorama) { this.recordCapture(now,'no-image'); return false; }
      const lens=cameraLens(video.videoWidth,video.videoHeight,this.shortAxisFov);
      if (basis && !manualOverhead) {
        const registration=registerFrame(this.panorama,data,canvas.width,canvas.height,basis,lens);
        const overlap=registration.overlap;
        if(registration.adjusted && rawBasis){
          if(poseSeparation(rawBasis,registration.basis)>10){this.overlapWait=true;this.recordCapture(now,'overlap-wait');return false;}
          basis=registration.basis;this.visualAnchor={raw:rawBasis,aligned:basis};
          measured=skyAngles(basis.forward);
        }
        // Keep a small, local reproduction bundle. It is downloaded only when
        // requested, never uploaded; no site coordinates or device IDs included.
        if(now-this.lastDiagnosticAt>=1000){
          this.lastDiagnosticAt=now;
          this.scanSamples.push({at:now,basis,sensorBasis:rawBasis,relativeMotion:this.poseSource.usesRelative,adjusted:registration.adjusted,lens,videoWidth:video.videoWidth,videoHeight:video.videoHeight,
            sensor:this.lastSensorReading,stillnessReadFailures:this.stillnessFailures,overlap,image:canvas.toDataURL('image/jpeg',.8)});
          if(this.scanSamples.length>16)this.scanSamples.splice(1,1);
        }
        if(overlap.result==='conflict'){this.overlapWait=true;this.recordCapture(now,'overlap-wait');return false;}
        this.overlapWait=false;
        const target=DOME_CELLS.find(c=>dot(c.center,basis!.forward)>=Math.cos((c.alt>89?5:8)*Math.PI/180));
        if(!target){this.recordCapture(now,'no-target');return false;}
        if(this.coveredCells.has(target.id)){this.recordCapture(now,'already-captured');return false;}
        this.panorama.add(data,canvas.width,canvas.height,basis,lens);
        capturedCell=target.id;capturedBasis=basis;capturedSensorBasis=rawBasis??undefined;capturedAdjusted=registration.adjusted;
      } else if (overhead) {
        // Without heading, an entire overhead photograph cannot be oriented.
        // Keep only its centre at the shared zenith; do not invent a sky cap.
        this.panorama.addZenith(data,canvas.width,canvas.height,tilt&&skyAngles(tilt.forward).alt>=85?tilt:null,lens);
      }
    } catch { this.issue = "Could not read the camera image. Close the scan and try again."; this.recording = false; this.recordCapture(now,'read-failed'); return false; }
    const column = columnFromImageData(data, canvas.width, canvas.height, 24);

    const altitude=manualOverhead?90:measured!.alt;
    const band = overhead ? OVERHEAD_BAND : bandForAltitude(altitude) ?? -1;
    const bin = band === OVERHEAD_BAND ? 0 : binForHeading(measured!.az, this.bins);
    const targetAltitude = band === OVERHEAD_BAND ? 90 : Math.round(altitude / 5) * 5;
    const sameTile = (f:SweepFrame)=>f.bin===bin&&f.band===band&&(band===OVERHEAD_BAND||Math.round((f.altitude??0)/5)*5===targetAltitude);

    this.frames = this.frames.filter(f => !sameTile(f));
    // Browsers expose no calibrated lens FOV. This remains an editable estimate.
    this.issue = null;
    this.frames.push({ bin, band, column, altitude, manualOverhead,
      verticalFov: video.videoHeight > video.videoWidth ? 60 : 45 });
    const previousCoverage=this.coveredCells.size;
    for(const cell of DOME_CELLS) {
      if(this.panorama?.covered(cell) && (cell.alt < 89 || this.overheadCaptured)) this.coveredCells.add(cell.id);
    }
    if(this.coveredCells.size>previousCoverage)this.lastCaptureAt=Date.now();
    this.hasCapturedFrame = true;
    this.recordCapture(now, 'accepted', { cell: capturedCell, basis: capturedBasis, sensorBasis: capturedSensorBasis, adjusted: capturedAdjusted });
    return true;
  }

  columns(): number[][] {
    return this.panorama?.columns(this.bins) ?? projectSweepColumns(this.frames, this.bins);
  }

  panoramaImage(): string {
    if(!this.panorama) throw new Error('No camera images have been captured yet.');
    return this.panorama.toDataURL();
  }

  alignmentReport():string {
    // `stillnessReadFailures` sits in the ENVELOPE as well as in each sample,
    // because the run this field exists to explain is the one with no samples
    // at all: blind to the pixels means the strict rule means nothing was ever
    // accepted. Spec 4.3 - a session with zero accepted frames is diagnosable.
    return JSON.stringify({version:1,description:'Local camera samples for alignment debugging; contains photos of your surroundings.',
      browser:navigator.userAgent,stillnessReadFailures:this.stillnessFailures,samples:this.scanSamples},null,2);
  }

  stop(): void {
    this.generation++;
    this.ready = false; this.recording = false;
    if(this.videoFrameHandle!==null){this.video?.cancelVideoFrameCallback?.(this.videoFrameHandle);this.videoFrameHandle=null;}
    if (this.grabTimer != null) { clearInterval(this.grabTimer); this.grabTimer = null; }
    if (this.headingHandler && typeof window !== "undefined") {
      window.removeEventListener("deviceorientation", this.headingHandler);
      window.removeEventListener("deviceorientationabsolute", this.headingHandler);
    }
    this.headingHandler = null;
    // No listeners, no pose stream: the source is not healthy until start()
    // attaches them again, and the old view can vouch for nothing. The luma
    // canvas goes with it - it is lazy, so the next scan rebuilds it, and a
    // closed editor should not hold a canvas backing store open.
    this.listening = false; this.stability.clear();
    this.lumaCanvas = null; this.stillnessFailures = 0; this.lastMediaTime = -1;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    if (this.video) this.video.srcObject = null;
  }
}
