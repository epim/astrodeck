// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The panorama scanner's keyframe tracker (SPEC-v2 4.3, 4.7, 4.11), stage v0 (T19).
//
// From sensors only, the tracker decides which camera frames become keyframes (the rule of 4.3), keeps each
// keyframe's central strip, places it and paints it into the band panorama. v0 places every keyframe by the
// predictor: the correction C is the identity, so a keyframe's pose is its prediction G. T27 adds alignment,
// fusion, the focal lock and rule 5 (revisits); T28 adds the loop closure. Sensor-only mode (7.7) keeps this
// path exactly. Everything here is synchronous plain TypeScript over typed arrays, so the replay harness times
// the production path (3.1).
import { DEG, pixelLuminance } from '../photosphereGeometry';
import { axisSeparationDeg, headingDeg, intrinsicsAt, qinv, qmul, qrotate, worldYaw } from './rotation';
import type {
  AnalysisFrame, BandPanoramaLike, CaptureRecord, Edge, FocalLike, FrameDetail, FrameOutcome, FrameStep, KeyClass,
  Keyframe, LatencyLike, LoopState, PredictorMode, Prediction, Quat, SliceSource, TrackerOptions, V3,
} from './types';

export const STEP_DEG = 4, WINDOW_DEG = 2, FIRST_KF_MAX_WAIT_MS = 300, MAX_KEYFRAMES = 140, STRIP_HALF_DEG = 10, TRAILING_MAX_DEG = 10;

// ---- Predictor variance, placement sigma and the innovation gate (4.7) ------

/** Gyro drift, 2 deg/min. */
const DRIFT_DEG_S = 2 / 60;
/** Gyro scale sd (sg): 2 % before closure; 0.5 degrees of closure noise over 360 after an image closure. */
const SCALE_SD = 0.02, SCALE_SD_CLOSED = 0.00139;
/** EIS offset: 0.3 between keyframes whose heading rates differ by under 2 deg/s, else 1.0 (ASSUMED until D0). */
const EIS_STEADY_DEG = 0.3, EIS_ACCEL_DEG = 1.0, EIS_STEADY_BELOW_DEG_S = 2;
/** The extra term of the absolute-only predictor (no usable gyro). */
const ABSOLUTE_ONLY_DEG = 1.0;
/** Per chain step of an aligned keyframe (the LK precision, ASSUMED), and the gravity-level floor of the rest. */
const ALIGNED_STEP_DEG = 0.05, PLACEMENT_FLOOR_DEG = 0.5;

/** Sigma_g of 4.7, deg^2 per axis: (0.02 dPsi)^2 + (drift dt)^2 + (sigma_tau dOmega)^2 + sigma_eis^2, plus 1.0^2 in
 *  absolute-only mode. sigma_tau is the latency sigma in seconds. The worked cases of a 4-degree step at
 *  4.6 keyframes/s give 0.311 (steady, tau applied), 0.321 (steady, prior tau), 1.006 (accelerating) and 1.047
 *  (absolute-only, steady). */
export function predictorVarianceDeg2(mode: PredictorMode, dPsiDeg: number, dtS: number, dOmegaDegS: number, sigmaTauMs: number): number {
  const eis = Math.abs(dOmegaDegS) < EIS_STEADY_BELOW_DEG_S ? EIS_STEADY_DEG : EIS_ACCEL_DEG;
  const v = (SCALE_SD * dPsiDeg) ** 2 + (DRIFT_DEG_S * dtS) ** 2 + (sigmaTauMs / 1000 * dOmegaDegS) ** 2 + eis ** 2;
  return mode === 'absolute-only' ? v + ABSOLUTE_ONLY_DEG ** 2 : v;
}

/** The placement sigma of 4.7, degrees. Aligned: 0.05 sqrt(max(1, s)) with s the chain steps to the gauge
 *  (keyframe 0). Otherwise sqrt((sg dPsi)^2 + (drift dt)^2 + 0.3^2 + (sigma_tau omega)^2 + sigma_anchor^2), at least
 *  0.5, where dPsi and dt run to the nearest anchor in chain order and 0.3 is the steady EIS term. The caller
 *  resolves the closure: after an image closure it passes s = min(s, K - s), the nearer anchor on either side, and
 *  `closed`, which switches sg from 0.02 to 0.00139 (a gyro closure measures no scale, so it passes false). */
export function placementSigmaDeg(o: { aligned: boolean; stepsToGauge: number; dPsiToAnchorDeg: number; dtToAnchorS: number;
  omegaDegS: number; sigmaTauMs: number; anchorSigmaDeg: number; closed: boolean }): number {
  if (o.aligned) return ALIGNED_STEP_DEG * Math.sqrt(Math.max(1, o.stepsToGauge));
  const sg = o.closed ? SCALE_SD_CLOSED : SCALE_SD;
  const s = Math.sqrt((sg * o.dPsiToAnchorDeg) ** 2 + (DRIFT_DEG_S * o.dtToAnchorS) ** 2 + EIS_STEADY_DEG ** 2
    + (o.sigmaTauMs / 1000 * o.omegaDegS) ** 2 + o.anchorSigmaDeg ** 2);
  return Math.max(PLACEMENT_FLOOR_DEG, s);
}

/** |v| <= 1 + 3 sigma_g once the focal is locked or has at least 3 ratios (v is then re-solved at f_best); before
 *  that the gate widens by 0.5 |dPsi_gyro|, 2 degrees at a 4-degree step. */
export function innovationGateDeg(o: { locked: boolean; ratios: number; dPsiGyroDeg: number; sigmaGDeg: number }): number {
  const gate = 1 + 3 * o.sigmaGDeg;
  return o.locked || o.ratios >= 3 ? gate : gate + 0.5 * Math.abs(o.dPsiGyroDeg);
}

// ---- The tracker ------------------------------------------------------------

/** Degrees into [-180, 180). */
const wrap180 = (deg: number) => ((((deg + 180) % 360) + 360) % 360) - 180;
/** The camera looks along its own -z (3.2). */
const FORWARD: V3 = [0, 0, -1];
/** Commits and replacements compare a rate with these fractions of rateMax and of the held candidate's rate (4.3). */
const STEADY_FRAC = 0.3, REPLACE_FRAC = 0.8;
/** sliceMsP50 is the median over this many of the newest slice paints. */
const SLICE_MS_WINDOW = 32;

/** What onFrame decided for a frame it asked the scanner to read back, kept until hold() brings the pixels. */
interface Ask { commit: boolean; d: number | null; nearest: number; unwrapped: number }

/** The held candidate (4.3 rule 3): everything a keyframe needs, extracted at hold() because the camera may reuse
 *  the readback buffer. */
interface Candidate {
  frameId: number; t: number; pred: Quat; rate: number; up: V3; extrapolatedMs: number;
  strip: Uint8Array; stripX0: number; stripW: number; stripH: number; skyLuma: number;
  d: number | null;      // axis distance to the nearest keyframe; null for the first keyframe
  nearest: number;       // that keyframe's id; -1 for the first
  unwrapped: number;     // the unwrapped heading of the frame (see trackHeading)
}

export class Tracker {
  private readonly pano: BandPanoramaLike;
  private readonly focal: FocalLike;
  private readonly latency: LatencyLike;
  private readonly frameW: number;
  private readonly frameH: number;
  private readonly maxKeyframes: number;
  /** v0 runs the same sensor placement in both modes; T27 aligns only when this is false. */
  protected readonly sensorOnly: boolean;
  private active = false;
  private rateMax = 40;

  private readonly kfs: Keyframe[] = [];
  /** Per keyframe id: the trailing fill it was committed with (4.3 rule 6), repainted with it. */
  private readonly trailing: { deg: number; side: -1 | 0 | 1 }[] = [];
  /** Per keyframe id: the unwrapped heading of its frame. */
  private readonly unwrappedAt: number[] = [];
  private readonly edgeList: Edge[] = [];
  private readonly records: CaptureRecord[] = [];
  private readonly loopState: LoopState = { closed: false, method: null, preDeg: null, postDeg: null, match: null, unwrappedDeg: 0 };

  private frames = 0;                 // onFrame calls: the frame number of a record whose frame was never read back
  private lastT = 0;                  // t of the newest onFrame call
  private beginT: number | null = null;   // t of the first active frame, from which rule 0's 300 ms run
  private wasActive = false;
  private capNoted = false;
  private heading: number | null = null;
  private unwrapped = 0;
  /** The span of the unwrapped live heading over active frames since keyframe 0, relative to it. */
  private visitedLo = 0;
  private visitedHi = 0;
  private ask: Ask | null = null;
  private cand: Candidate | null = null;
  private readonly staleAt: number[] = [];

  private queue: number[] = [];       // keyframe ids awaiting a repaint after scheduleRerender()
  private renderYawDeg = 0;           // 0 while scanning (scan frame); the world yaw after renderAll
  private readonly sliceMs: number[] = [];
  private sliceNext = 0;
  private sliceP50 = 0;

  constructor(o: TrackerOptions) {
    this.pano = o.pano;
    this.focal = o.focal;
    this.latency = o.latency;
    this.frameW = o.frameW;
    this.frameH = o.frameH;
    this.sensorOnly = o.sensorOnly ?? false;
    this.maxKeyframes = o.maxKeyframes ?? MAX_KEYFRAMES;
  }

  setActive(active: boolean): void { this.active = active; }
  setRateMax(degS: number): void { this.rateMax = degS; }

  get keyframes(): readonly Keyframe[] { return this.kfs; }
  get edges(): readonly Edge[] { return this.edgeList; }
  get loop(): LoopState { return this.loopState; }
  get log(): readonly CaptureRecord[] { return this.records; }
  /** Textured mismatches need alignment (T27); 0 until then and always in sensor-only mode. */
  get mismatchRun(): number { return 0; }
  get staleInLastSecond(): number { return this.staleAt.length; }
  get pendingSlices(): number { return this.queue.length; }
  get sliceMsP50(): number { return this.sliceP50; }

  /** Painted cells times the cell width: 720 cells of 0.5 degree, so the count / 2. */
  get coveredDeg(): number {
    const cov = this.pano.coverage;
    let n = 0;
    for (let i = 0; i < cov.length; i++) if (cov[i] !== 0) n++;
    return n * 360 / cov.length;
  }

  /** The largest unpainted run of coverage cells, 2 degrees or more, inside the swept range: between the lowest and
   *  the highest unwrapped keyframe heading, or the whole ring once the active live axis has turned through 360
   *  degrees. Unpainted cells beyond the keyframes are the part not yet turned to, not a gap. The full turn needs its
   *  own test because the seam between the newest keyframe and keyframe 0 is behind the user by then while no
   *  keyframe lies beyond it: the frames past the seam are within S of keyframe 0. Null while a re-render is
   *  pending, because the raster is then partly cleared on purpose and a gap cue would send the user back for
   *  nothing. */
  get gapDeg(): number | null {
    const n = this.kfs.length;
    if (n === 0 || this.queue.length > 0) return null;
    const cov = this.pano.coverage, cells = cov.length, cellDeg = 360 / cells;
    let lo = 0, hi = 0;
    for (let k = 1; k < n; k++) {
      const u = this.unwrappedAt[k] - this.unwrappedAt[0];
      if (u < lo) lo = u;
      if (u > hi) hi = u;
    }
    const base = headingDeg(this.kfs[0].pose) + this.renderYawDeg;
    let first: number, count: number;
    if (this.visitedHi - this.visitedLo >= 360) {
      // The whole ring: start just after a painted cell so a run across column 0 is counted once.
      let painted = -1;
      for (let i = 0; i < cells; i++) if (cov[i] !== 0) { painted = i; break; }
      if (painted < 0) return 360;
      first = painted + 1; count = cells;
    } else {
      // Cells whose centre (i + 0.5) cellDeg lies in [base + lo, base + hi].
      first = Math.ceil((base + lo) / cellDeg - 0.5);
      count = Math.floor((base + hi) / cellDeg - 0.5) - first + 1;
    }
    let run = 0, best = 0;
    for (let j = 0; j < count; j++) {
      if (cov[(((first + j) % cells) + cells) % cells] === 0) { run++; if (run > best) best = run; } else run = 0;
    }
    const deg = best * cellDeg;
    return deg >= 2 ? deg : null;
  }

  /** The keyframe rule of 4.3, from sensors only. Order: inactive (rule 1), stale pose (rule 1), the cap (rule 7),
   *  the first keyframe (rule 0), then the step and its window (rules 2-4, with rule 6 for a frame past the window). */
  onFrame(t: number, pred: Prediction | null, rateDegS: number | null): FrameStep {
    this.frames++;
    this.lastT = t;
    this.ask = null;   // a read the scanner did not follow with hold() was a failed readback
    while (this.staleAt.length > 0 && this.staleAt[0] <= t - 1000) this.staleAt.shift();
    const live = this.livePose(pred);
    if (live) this.trackHeading(live);

    const wasActive = this.wasActive;
    this.wasActive = this.active;
    if (!this.active) {
      if (wasActive && this.beginT !== null) this.note(t, this.frames, 'inactive');
      return { read: false, commit: false, why: 'inactive' };
    }
    if (this.beginT === null) this.beginT = t;
    if (!pred || !live) {
      this.staleAt.push(t);
      this.note(t, this.frames, 'stale-pose');
      return { read: false, commit: false, why: 'stale-pose' };
    }
    if (this.kfs.length > 0) {
      const u = this.unwrapped - this.unwrappedAt[0];
      if (u < this.visitedLo) this.visitedLo = u;
      if (u > this.visitedHi) this.visitedHi = u;
    }
    if (this.kfs.length >= this.maxKeyframes) {
      if (!this.capNoted) { this.capNoted = true; this.note(t, this.frames, 'cap'); }
      return { read: false, commit: false, why: 'cap' };
    }

    // An unknown rate is never steady enough for an early commit and never preferred to a known one.
    const rate = rateDegS ?? Infinity;
    const steady = rate <= STEADY_FRAC * this.rateMax;
    if (this.kfs.length === 0) {
      // Rule 0: a steady frame, or any frame once 300 ms have passed since the first active one.
      if (steady || t - this.beginT >= FIRST_KF_MAX_WAIT_MS) return this.read(true, 'first', null, -1);
      return { read: false, commit: false, why: 'waiting-sharper' };
    }
    const held = this.cand;
    // Rule 4: a held candidate commits as soon as it is steady, which after hold() only a raised rateMax can make it.
    if (held && held.rate <= STEADY_FRAC * this.rateMax) return { read: false, commit: true, why: 'step' };

    let d = Infinity, nearest = -1;
    for (const kf of this.kfs) {
      const s = axisSeparationDeg(kf.pose, live);
      if (s < d) { d = s; nearest = kf.id; }
    }
    // Rule 2. Rule 5 (revisit) belongs here and needs alignment, so it is T27's: when the 6-degree slit under the
    // frame is painted only sensor or blurred and the rate is at most 0.5 rateMax, read back and commit a revisit
    // (never in sensor-only mode). The held candidate is kept, so turning back and forth does not read it again.
    if (d < STEP_DEG) return { read: false, commit: false, why: 'covered' };
    // Rule 4 at the far end of the window: the held candidate commits. Without one, a stall, a stale run or an
    // inactive stretch carried the axis past the window, and this frame is read and committed with trailing fill.
    if (d >= STEP_DEG + WINDOW_DEG) {
      if (held) return { read: false, commit: true, why: 'step' };
      return this.read(true, 'step', d, nearest);
    }
    // Rule 3, the window: the first frame is held; a later one replaces it only when clearly slower.
    if (!held || rate < REPLACE_FRAC * held.rate) return this.read(steady, 'step', d, nearest);
    return { read: false, commit: false, why: 'waiting-sharper' };
  }

  /** Keep this candidate's pixels: the kept strip and the sky luma. Call only after an onFrame that asked to read. */
  hold(f: AnalysisFrame, pred: Prediction, rateDegS: number, up: V3): void {
    const ask = this.ask;
    if (!ask) throw new Error('Tracker.hold: the last onFrame did not ask for a read');
    this.ask = null;
    if (f.w !== this.frameW || f.h !== this.frameH) {
      throw new RangeError(`Tracker.hold: frame ${f.w} x ${f.h}, analysis frame ${this.frameW} x ${this.frameH}`);
    }
    // The kept strip: the columns within +-10 degrees of the centre at L0, 2 f tan 10 wide, as RGB (4.11).
    const k0 = intrinsicsAt(0, this.frameW, this.frameH, this.focal.fBest);
    const stripW = Math.min(this.frameW, Math.ceil(2 * k0.f * Math.tan(STRIP_HALF_DEG * DEG)));
    const stripX0 = Math.max(0, Math.min(this.frameW - stripW, Math.round(k0.cx - stripW / 2)));
    const stripH = this.frameH;
    const strip = new Uint8Array(stripW * stripH * 3);
    for (let y = 0; y < stripH; y++) {
      let src = (y * this.frameW + stripX0) * 4, dst = y * stripW * 3;
      for (let x = 0; x < stripW; x++, src += 4, dst += 3) {
        strip[dst] = f.rgba[src]; strip[dst + 1] = f.rgba[src + 1]; strip[dst + 2] = f.rgba[src + 2];
      }
    }
    this.cand = {
      frameId: f.frameId, t: f.t, pred: pred.q, rate: rateDegS, up, extrapolatedMs: pred.extrapolatedMs,
      strip, stripX0, stripW, stripH, skyLuma: skyLumaOf(f.rgba, f.w, f.h),
      d: ask.d, nearest: ask.nearest, unwrapped: ask.unwrapped,
    };
    if (!ask.commit) {
      const r: CaptureRecord = { at: this.lastT, frameId: f.frameId, outcome: 'waiting-sharper', rateDegS, extrapolatedMs: pred.extrapolatedMs };
      if (ask.d !== null) r.stepDeg = ask.d;
      this.records.push(r);
    }
  }

  /** Commit the held candidate: place it by the predictor (P = C . G with C the identity) and paint it. */
  commit(): Keyframe {
    const c = this.cand;
    if (!c) throw new Error('Tracker.commit: no candidate is held');
    this.cand = null;
    this.ask = null;
    const id = this.kfs.length;
    const cls: KeyClass = c.rate > this.rateMax ? 'blurred' : 'sensor';
    const kf: Keyframe = {
      id, frameId: c.frameId, t: c.t, pred: c.pred, pose: c.pred, cls, rate: c.rate, up: c.up, sigmaDeg: 0,
      gainRGB: [1, 1, 1], strip: c.strip, stripX0: c.stripX0, stripW: c.stripW, stripH: c.stripH, pyr: null,
      skyLuma: c.skyLuma,
    };
    // Rule 6: a commit at d >= S + 2 came from no window, so it paints its trailing side to meet the slice of the
    // keyframe d was measured to. The side is where that keyframe's axis falls in this camera's image.
    let deg = 0, side: -1 | 0 | 1 = 0;
    if (c.d !== null && c.d >= STEP_DEG + WINDOW_DEG) {
      deg = Math.min(c.d - STEP_DEG, TRAILING_MAX_DEG);
      const prev = qrotate(qinv(kf.pose), qrotate(this.kfs[c.nearest].pose, FORWARD));
      side = prev[0] < 0 ? -1 : 1;
    }
    this.kfs.push(kf);
    this.trailing.push({ deg, side });
    this.unwrappedAt.push(c.unwrapped);
    kf.sigmaDeg = this.sigmaFor(kf);
    this.loopState.unwrappedDeg = c.unwrapped - this.unwrappedAt[0];
    this.paintKeyframe(kf);

    let detail: FrameDetail = cls;
    if (id === 0) detail = 'first';
    else if (c.d !== null && c.d - STEP_DEG > TRAILING_MAX_DEG) detail = 'gap';   // beyond the fill: the gap stays grey
    const r: CaptureRecord = { at: this.lastT, frameId: c.frameId, outcome: 'accepted', detail, kf: id, rateDegS: c.rate, extrapolatedMs: c.extrapolatedMs };
    if (c.d !== null) r.stepDeg = c.d;
    this.records.push(r);
    return kf;
  }

  /** Repaint up to maxSlices keyframes of a pending re-render; returns how many are still pending. */
  pump(maxSlices: number): number {
    for (let i = 0; i < maxSlices && this.queue.length > 0; i++) {
      const id = this.queue.shift() as number;
      this.paintKeyframe(this.kfs[id]);
    }
    return this.queue.length;
  }

  livePose(pred: Prediction | null): Quat | null {
    if (!pred) return null;
    const newest = this.kfs[this.kfs.length - 1];
    if (!newest) return pred.q;
    // C_k = P_k . G_k^-1 (4.5): the identity in v0, where every keyframe is placed by its prediction.
    return qmul(qmul(newest.pose, qinv(newest.pred)), pred.q);
  }

  /** The yaw of C at t, degrees. v0 never corrects, so 0; T27 interpolates the keyframe corrections. */
  correctionYawAt(_t: number): number { return 0; }

  /** Finish: clear the raster and repaint every keyframe with worldYaw(worldYawDeg) composed, which ADDS the yaw to
   *  every azimuth. A pending re-render is dropped, because this repaints everything. */
  renderAll(worldYawDeg: number): void {
    this.queue = [];
    this.renderYawDeg = worldYawDeg;
    this.pano.clear();
    for (const kf of this.kfs) this.paintKeyframe(kf);
  }

  /** Clear the raster and queue every keyframe for a repaint through pump(): T27 calls this after the focal lock,
   *  T28 after a closure, once the sigmas are recomputed. A keyframe committed meanwhile paints at once and is not
   *  queued. Calling it again restarts the queue. */
  protected scheduleRerender(): void {
    this.pano.clear();
    this.queue = this.kfs.map(kf => kf.id);
  }

  // ---- Internals ----------------------------------------------------------

  private read(commit: boolean, why: 'first' | 'step', d: number | null, nearest: number): FrameStep {
    this.ask = { commit, d, nearest, unwrapped: this.unwrapped };
    return { read: true, commit, why };
  }

  /** Unwrapped heading of the live pose, accumulated over every frame with a prediction, active or not, so a turn
   *  made while paused is counted. Each frame-to-frame change is wrapped to [-180, 180). */
  private trackHeading(live: Quat): void {
    const h = headingDeg(live);
    if (this.heading !== null) this.unwrapped += wrap180(h - this.heading);
    this.heading = h;
  }

  /** Keyframe 0 is the gauge and, in v0, the only anchor. As an anchor it adds nothing, because it defines the scan
   *  frame (4.7's sensor-only example, 3.6, has no anchor term); its own sigma is the formula at dPsi = dt = 0, which
   *  is the 0.5 floor unless sigma_tau omega is large. dPsi is the net unwrapped turn, which is what a scale error
   *  multiplies. */
  private sigmaFor(kf: Keyframe): number {
    const gauge = this.kfs[0];
    return placementSigmaDeg({
      aligned: kf.cls === 'aligned', stepsToGauge: kf.id,
      dPsiToAnchorDeg: Math.abs(this.unwrappedAt[kf.id] - this.unwrappedAt[0]), dtToAnchorS: Math.abs(kf.t - gauge.t) / 1000,
      omegaDegS: kf.rate, sigmaTauMs: this.latency.sigmaMs, anchorSigmaDeg: 0, closed: false,
    });
  }

  private paintKeyframe(kf: Keyframe): void {
    const fill = this.trailing[kf.id];
    const s: SliceSource = {
      id: kf.id, strip: kf.strip, stripX0: kf.stripX0, stripW: kf.stripW, stripH: kf.stripH,
      pose: this.renderYawDeg === 0 ? kf.pose : qmul(worldYaw(this.renderYawDeg), kf.pose),
      k0: intrinsicsAt(0, this.frameW, this.frameH, this.focal.fBest),
      gainRGB: kf.gainRGB, cls: kf.cls, sigmaDeg: kf.sigmaDeg, halfWidthDeg: 3,
      trailingDeg: fill.deg, trailingSide: fill.side, topFrac: 0.9,
    };
    const t0 = performance.now();
    this.pano.paint(s, this.lastT);
    this.noteSliceMs(performance.now() - t0);
  }

  private noteSliceMs(ms: number): void {
    if (this.sliceMs.length < SLICE_MS_WINDOW) this.sliceMs.push(ms);
    else this.sliceMs[this.sliceNext] = ms;
    this.sliceNext = (this.sliceNext + 1) % SLICE_MS_WINDOW;
    const sorted = [...this.sliceMs].sort((a, b) => a - b);
    this.sliceP50 = sorted[Math.ceil(sorted.length / 2) - 1];   // nearest rank
  }

  private note(at: number, frameId: number, outcome: FrameOutcome): void {
    this.records.push({ at, frameId, outcome });
  }
}

/** Median luma (Rec. 601, as pixelLuminance) of the top 10 % of rows, nearest rank over values rounded to integers. */
function skyLumaOf(rgba: Uint8ClampedArray, w: number, h: number): number {
  const n = Math.max(1, Math.round(h * 0.1)) * w;
  const hist = new Uint32Array(256);
  for (let i = 0, p = 0; i < n; i++, p += 4) hist[Math.round(pixelLuminance(rgba[p], rgba[p + 1], rgba[p + 2]))]++;
  const rank = Math.ceil(n / 2);
  let seen = 0;
  for (let v = 0; v < 256; v++) { seen += hist[v]; if (seen >= rank) return v; }
  return 255;
}
