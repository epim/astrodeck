// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The panorama scanner's keyframe tracker (SPEC-v2 4.3, 4.6-4.8, 4.10, 4.11), stages v0 (T19) and 2 (T27).
//
// From sensors only, the tracker decides which camera frames become keyframes (the rule of 4.3), keeps each
// keyframe's central strip, places it and paints it into the band panorama. v0 places every keyframe by the
// predictor: the correction C is the identity, so a keyframe's pose is its prediction G. Sensor-only mode (7.7) keeps
// that path exactly.
//
// Stage 2 (T27, when `sensorOnly` is false) aligns each committed frame against the previous keyframe, or the nearest
// one for a revisit (`alignPair`, 4.6), fuses the image pose with the gyro-predicted one by their variances (4.7), grades
// the result against the innovation gate, keeps the chain of image edges, runs the steady-pair focal ratios of 4.8 and,
// at the lock, re-solves every edge at the locked focal, re-fuses the chain, recomputes the sigmas and repaints through
// `pump`. It also chains the exposure gains (4.10), revisits amber stretches (rule 5) and feeds the latency estimator
// with dOmega, the change of heading rate between the two keyframes of a pair (4.5). T28 adds the loop closure.
//
// Conventions kept from the spec, because a sign here shows only as a lock that never comes (S19):
//   * qBA is the b-from-a camera rotation, qBA = qinv(P_b) . P_a (S1); the prediction is qinv(C_a . G_b) . P_a =
//     qinv(G_b) . G_a with C_a = P_a . G_a^-1 (C of the newest keyframe on a chain step), and the image pose is
//     P_a . qinv(qBA).
//   * the innovation v = log(P_g^-1 . P_img) and every variance are in the camera axes of the candidate: x pitch, y
//     yaw, z roll (S38).
//   * every rate behind dOmega and the steady-pair test is the magnitude PoseTrack.rateAt hands in (the mapped gyro once
//     its axis mapping is confirmed, else its own orientation chord; S33, S41) with the sign of the predictor's heading
//     chord, never two consecutive samples (S10, S31). A gyro that reads quiet while the predictor turns is not believed.
// Everything here is synchronous plain TypeScript over typed arrays, so the replay harness times the production path (3.1).
import { DEG, pixelLuminance } from '../photosphereGeometry';
import { alignPair } from './align';
import type { RgbStrip } from './align';
import { CHORD_MAX_MS, CHORD_MIN_MS } from './poseTrack';
import { buildPyramid, prepareTemplate } from './pyramid';
import {
  axisSeparationDeg, elevationDeg, expSO3, headingDeg, intrinsicsAt, logSO3, qinv, qmul, qrotate, rotationFromRays,
  unprojectPixel, worldYaw,
} from './rotation';
import { PixClass } from './types';
import type {
  AlignResult, AnalysisFrame, BandPanoramaLike, CaptureRecord, Edge, FocalLike, FrameDetail, FrameOutcome, FrameStep,
  Intrinsics, KeyClass, Keyframe, LatencyLike, LoopState, PredictorMode, Prediction, Pyramid, Quat, SliceSource, Template,
  TrackerOptions, V3,
} from './types';

export const STEP_DEG = 4, WINDOW_DEG = 2, FIRST_KF_MAX_WAIT_MS = 300, MAX_KEYFRAMES = 140, STRIP_HALF_DEG = 10, TRAILING_MAX_DEG = 10;
  // a trailing-fill commit keeps its strip out to 3 + trailingDeg on the trailing side (at most 13 degrees), so a fill of up to
  // TRAILING_MAX_DEG meets the previous slice with no alpha or coverage hole (4.11, S12); off the level its trailingDeg is
  // (d - S) / cos(pitch), capped at TRAILING_MAX_DEG, which is where the raster clamps it (S34)

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
/** The gap cue's threshold: an unpainted run this wide is a gap (gapDeg) and, bracketed by two keyframes, fires rule 8. */
const GAP_MIN_DEG = 2;
/** A slice's feather reaches 0 this far from its centre, or 3 + trailingDeg on the trailing side of a fill (4.11). */
const FEATHER_EDGE_DEG = 3;
/** sliceMsP50 is the median over this many of the newest slice paints. */
const SLICE_MS_WINDOW = 32;

// ---- Stage 2 (T27) -------------------------------------------------------------

/** Rule 5 (4.3): the slit under the frame is 2 x this many degrees wide, and a revisit needs a rate this fraction of rateMax. */
const SLIT_HALF_DEG = 3, REVISIT_RATE_FRAC = 0.5;
/** Class (4.7): aligned needs W_yaw at least this, computed without the focal term. */
const ALIGNED_W_YAW = 0.5;
/** Steady pairs (4.8): the heading rates of every keyframe in the span differ by less than this (strict), and the span
 *  starts this long after the onset, which is the first keyframe or the last time the rate rose through ONSET_RATE. */
const STEADY_SPAN_DEG_S = 2, ONSET_SKIP_MS = 500, ONSET_RATE_DEG_S = 5;
/** A heading rate below this is no motion: its sign is whatever, and the heading chord is not needed. */
const STILL_RATE_DEG_S = 0.5;
/** The gyro's magnitude takes the heading chord's sign only when the chord shows at least this fraction of it, so a
 *  nod (a large |omega| and no heading change) is not read as a pan. */
const HEADING_SHARE = 0.3;
/** A gyro rate below the heading chord by more than this factor and this margin is not the same turn (S33). */
const QUIET_GYRO_FACTOR = 2, QUIET_GYRO_DEG_S = 1;
/** Chained gains (4.10): each step's ratio of overlap means is clamped to this range. */
const GAIN_STEP_MIN = 0.67, GAIN_STEP_MAX = 1.5;
/** The tilt of C is pulled toward zero (4.5, Level). The gravity-referenced predictor's tilt is good to TILT_SIGMA_DEG, and the
 *  image chain adds a random walk of ALIGNED_STEP_DEG a keyframe to the tilt of C, so the steady-state gain of that walk
 *  observed through the pull is 0.05 / 0.5 = 0.1 of C's tilt a keyframe. A pull of 1 %, which is what the image's own sigma
 *  alone would give, let a 2-degree tilt of C build over a ring: the image chain turns about a slightly tilted axis whenever
 *  its focal is a little off, and nothing but this pull holds it to gravity. */
const TILT_SIGMA_DEG = 0.5, TILT_PULL = ALIGNED_STEP_DEG / TILT_SIGMA_DEG;
/** Horn re-solve needs at least this many correspondences (the grid gives up to 16). */
const HORN_MIN_CORR = 4;
/** A chain commit aligns against the previous keyframe unless the gyro puts it farther than this from it, and then against
 *  the nearest one in the corrected frame (a turn back past the first keyframes). Past 20 degrees less than half of a
 *  41-degree frame overlaps. */
const CHAIN_REF_MAX_DEG = 20;
/** The S34 widening of a trailing fill is 1 / cos(pitch), with the cosine kept from collapsing near the zenith. */
const MIN_COS_PITCH = 0.25;
/** S32: a relative sample within this long of Begin replaces a first keyframe taken from an absolute-only sample. */
const REANCHOR_WITHIN_MS = 1000;
/** Samples of the predictor's heading kept for the heading chord, ms. */
const HEADING_KEEP_MS = 600;
/** An unknown heading-rate change is treated as an accelerating one (|dOmega| = 2 is not below 2). */
const UNKNOWN_DOMEGA_DEG_S = 2;

const finite = (x: number | null | undefined): x is number => typeof x === 'number' && Number.isFinite(x);
const RAD_TO_DEG2 = (180 / Math.PI) ** 2;
const IDENTITY: Quat = [1, 0, 0, 0];
const UP: V3 = [0, 0, 1];

/** What onFrame decided for a frame it asked the scanner to read back, kept until hold() brings the pixels. */
interface Ask {
  commit: boolean; d: number | null; nearest: number; unwrapped: number;
  revisit: boolean;      // rule 5: held apart from the window's candidate, and aligned against `nearest`
  gUnwrapped: number;    // the predictor's unwrapped heading at this frame
  omega: number | null;  // the signed heading rate at this frame, deg/s (null when unknown)
}

/** A trailing fill (4.3 rule 6): the extra degrees painted on one side, -1 the image's left, +1 its right, 0 none. */
interface Fill { deg: number; side: -1 | 0 | 1 }

/** The held candidate (4.3 rule 3): everything a keyframe needs, extracted at hold() because the camera may reuse
 *  the readback buffer. */
interface Candidate {
  frameId: number; t: number; pred: Quat; rate: number; up: V3; extrapolatedMs: number;
  strip: Uint8Array; stripX0: number; stripW: number; stripH: number; skyLuma: number;
  d: number | null;      // axis distance to the nearest keyframe; null for the first keyframe
  nearest: number;       // that keyframe's id; -1 for the first
  unwrapped: number;     // the unwrapped heading of the frame (see trackHeading)
  fill: Fill;            // decided at hold(), because the strip is sized from it
  // Stage 2 only:
  pyr: Pyramid | null;   // L1 and L2 luma of the frame, built at hold() for the same reason as the strip
  mode: PredictorMode;   // the predictor that produced `pred` (Sigma_g reads it)
  tauMs: number;         // the latency the prediction was taken at (tauUsed of 4.5)
  revisit: boolean;
  gUnwrapped: number;
  omega: number | null;
}

/** What the fusion needs to run again for a keyframe, kept per id: the lock re-fuses the whole chain from these. */
interface Link {
  ref: number;               // the keyframe this one was aligned against (-1 for keyframe 0)
  revisit: boolean;
  edge: Edge | null;         // the accepted image edge; null when the gyro placed the keyframe
  fMeas: number;             // the fNorm edge.qBA is expressed at
  mode: PredictorMode;
  dPsiDeg: number;           // heading change of the predictor from ref to this keyframe, signed
  dPitchDeg: number;         // elevation change likewise
  dtS: number;               // |t - t_ref|, seconds
  dOmega: number | null;     // signed heading rate here minus there, deg/s
  omega: number | null;      // signed heading rate here
  gUnwrapped: number;        // the predictor's unwrapped heading
  tauMs: number;
  fast: boolean;             // committed above rateMax: class blurred whatever the image says
  steps: number;             // chain steps to the gauge (keyframe 0)
  mismatch: boolean;         // textured and refused, or accepted by alignPair and refused by the gate
}

/** The outcome of fusing one keyframe: what the capture log, the class and the latency estimator need. */
interface Fused {
  accepted: boolean; innovationDeg: number | null; wYaw: number | null;
  imgMinusGyroDeg: number | null;   // heading of the image pose minus heading of the gyro pose: the r of 4.5
}

export class Tracker {
  private readonly pano: BandPanoramaLike;
  private readonly focal: FocalLike;
  private readonly latency: LatencyLike;
  private readonly frameW: number;
  private readonly frameH: number;
  private readonly maxKeyframes: number;
  /** True is the control (7.7): the v0 sensor placement, no alignment. False aligns, fuses and locks the focal (T27). */
  protected readonly sensorOnly: boolean;
  private active = false;
  private rateMax = 40;

  private readonly kfs: Keyframe[] = [];
  /** Per keyframe id: the trailing fill it was committed with (4.3 rule 6), repainted with it. */
  private readonly trailing: Fill[] = [];
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
  /** Rule 5: the revisit's candidate, held apart so that the window's candidate survives it (T19: turning back and forth
   *  does not read it again). It lives from hold() to the commit() that follows in the same frame. */
  private rev: Candidate | null = null;
  private readonly staleAt: number[] = [];
  /** Rule 8: the nearest keyframe of the previous frame with a prediction (-1 for none), and how many keyframes existed then. */
  private prevNearest = -1;
  private prevCount = 0;

  private queue: number[] = [];       // keyframe ids awaiting a repaint after scheduleRerender()
  private renderYawDeg = 0;           // 0 while scanning (scan frame); the world yaw after renderAll
  private readonly sliceMs: number[] = [];
  private sliceNext = 0;
  private sliceP50 = 0;

  // Stage 2 state (unused when sensorOnly):
  private readonly links: Link[] = [];
  /** Per keyframe id: the yaw of C in degrees (heading of the pose minus heading of the prediction), unwrapped along the ids. */
  private readonly cYaw: number[] = [];
  /** Per 0.5-degree coverage cell: a keyframe placed there was blurred or refused its match, so rule 5 may revisit it once. */
  private readonly revisitCells = new Uint8Array(720);
  /** The newest template (gradients, Jacobian, Hessian of a keyframe's L1 and L2) and what it was built for: only the live
   *  one is cached, an older keyframe's is rebuilt from its pyramid (a revisit). */
  private tplCache: { id: number; f: number; tpl: Template } | null = null;
  private mismatches = 0;
  /** The predictor's unwrapped heading over frames with a prediction, active or not, and the recent history for the chord. */
  private gHeading: number | null = null;
  private gUnwrapped = 0;
  private readonly gHist: { t: number; u: number }[] = [];
  private frameOmega: number | null = null;
  private lastRate: number | null = null;
  private riseT = -Infinity;          // the last time the rate rose through ONSET_RATE_DEG_S

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
  /** Consecutive textured mismatches: a frame with texture whose match was refused by the acceptance tests or the gate
   *  (2.6). Any other commit resets it. Always 0 in sensor-only mode. */
  get mismatchRun(): number { return this.mismatches; }
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
    const deg = this.unpaintedRunDeg(first, count);
    return deg >= GAP_MIN_DEG ? deg : null;
  }

  /** The keyframe rule of 4.3, from sensors only. Order: inactive (rule 1), stale pose (rule 1), the cap (rule 7),
   *  the first keyframe (rule 0), the bracketed gap (rule 8), then the step and its window (rules 2-4, with rule 6 for
   *  a frame past the window). */
  onFrame(t: number, pred: Prediction | null, rateDegS: number | null): FrameStep {
    this.frames++;
    this.lastT = t;
    this.ask = null;   // a read the scanner did not follow with hold() was a failed readback
    while (this.staleAt.length > 0 && this.staleAt[0] <= t - 1000) this.staleAt.shift();
    const live = this.livePose(pred);
    if (live) this.trackHeading(live);
    if (pred && !this.sensorOnly) this.trackPredictor(t, pred, rateDegS);
    // The nearest keyframe and the axis distance d to it, on every frame with a prediction, active or not, because rule 8
    // compares it with the previous such frame's.
    let d = Infinity, nearest = -1;
    if (live) {
      for (const kf of this.kfs) {
        const s = axisSeparationDeg(kf.pose, live);
        if (s < d) { d = s; nearest = kf.id; }
      }
    }
    const crossed = live ? this.crossedFrom(nearest) : -1;

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
    // S32: a first keyframe taken from an absolute-only sample, with a relative sample following within 1 s of Begin, is
    // dropped and taken again from the relative one: the compass wanders by degrees while the phone stands still, and
    // the scan frame's yaw zero would carry that wander. Rule 0 below then runs for this frame.
    if (!this.sensorOnly && this.kfs.length === 1 && this.links[0].mode === 'absolute-only' && pred.mode === 'relative'
      && t - this.beginT <= REANCHOR_WITHIN_MS) this.dropFirstKeyframe();
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

    // Rule 8, the bracketed gap: the axis has crossed the midpoint between two keyframes with an unpainted run of at
    // least GAP_MIN_DEG between them, which the window cannot fill: between keyframes w degrees apart d peaks at w / 2,
    // so for w < 12 it never reaches S + 2, and for w < 8 the window never opens (the ring seam is one such region). The
    // held candidate commits, else this frame is read and committed; either way d < S does not stop it. Not while a
    // re-render is pending, because the raster is then cleared on purpose (as gapDeg).
    if (crossed >= 0 && this.queue.length === 0 && this.unpaintedBetween(crossed, nearest) >= GAP_MIN_DEG) {
      if (held) return { read: false, commit: true, why: 'step' };
      return this.read(true, 'step', d, nearest);
    }
    // Rule 2, with rule 5 (revisit) inside it: when d < S but the 6-degree slit under the frame is painted only sensor or
    // blurred, and the rate is at most 0.5 rateMax, the frame is read back and committed as a revisit, aligned against
    // the nearest keyframe (never in sensor-only mode, which has no alignment). The held candidate is kept, so turning
    // back and forth does not read it again.
    if (d < STEP_DEG) {
      if (!this.sensorOnly && rateDegS !== null && rate <= REVISIT_RATE_FRAC * this.rateMax && this.queue.length === 0
        && this.slitIsAmber(live)) return this.read(true, 'revisit', d, nearest);
      return { read: false, commit: false, why: 'covered' };
    }
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
    // The kept strip: the columns within +-10 degrees of the centre at L0, 2 f tan 10 wide, as RGB (4.11). A slice paints
    // only what its strip holds, so a trailing-fill commit, which paints its trailing side out to 3 + trailingDeg (up to
    // 13 degrees), keeps that side of the strip out as far (ruling S12); a fill of 7 or less needs nothing beyond 10.
    const fill = this.fillFor(ask.d, ask.nearest, pred.q);
    const k0 = intrinsicsAt(0, this.frameW, this.frameH, this.focal.fBest);
    const plainW = Math.min(this.frameW, Math.ceil(2 * k0.f * Math.tan(STRIP_HALF_DEG * DEG)));
    let x0 = Math.max(0, Math.min(this.frameW - plainW, Math.round(k0.cx - plainW / 2))), x1 = x0 + plainW;
    const reach = FEATHER_EDGE_DEG + fill.deg;
    if (reach > STRIP_HALF_DEG) {
      const ext = k0.f * Math.tan(reach * DEG);
      if (fill.side < 0) x0 = Math.max(0, Math.min(x0, Math.floor(k0.cx - ext)));
      else x1 = Math.min(this.frameW, Math.max(x1, Math.ceil(k0.cx + ext)));
    }
    const stripX0 = x0, stripW = x1 - x0, stripH = this.frameH;
    const strip = new Uint8Array(stripW * stripH * 3);
    for (let y = 0; y < stripH; y++) {
      let src = (y * this.frameW + stripX0) * 4, dst = y * stripW * 3;
      for (let x = 0; x < stripW; x++, src += 4, dst += 3) {
        strip[dst] = f.rgba[src]; strip[dst + 1] = f.rgba[src + 1]; strip[dst + 2] = f.rgba[src + 2];
      }
    }
    const c: Candidate = {
      frameId: f.frameId, t: f.t, pred: pred.q, rate: rateDegS, up, extrapolatedMs: pred.extrapolatedMs,
      strip, stripX0, stripW, stripH, skyLuma: skyLumaOf(f.rgba, f.w, f.h),
      d: ask.d, nearest: ask.nearest, unwrapped: ask.unwrapped, fill,
      // Stage 2: the pyramid of the frame (L0 luma is dropped here), and what the fusion needs to know about the prediction.
      pyr: this.sensorOnly ? null : buildPyramid(f.rgba, f.w, f.h).pyr,
      mode: pred.mode, tauMs: this.latency.tauMs, revisit: ask.revisit, gUnwrapped: ask.gUnwrapped, omega: ask.omega,
    };
    if (ask.revisit) this.rev = c; else this.cand = c;
    if (!ask.commit) {
      const r: CaptureRecord = { at: this.lastT, frameId: f.frameId, outcome: 'waiting-sharper', rateDegS, extrapolatedMs: pred.extrapolatedMs };
      if (ask.d !== null) r.stepDeg = ask.d;
      this.records.push(r);
    }
  }

  /** The step's readback returned null (3.5 step 5): log 'read-failed' for this frame, with the onFrame count as its
   *  frame id, and drop the step. The scanner then calls neither hold() nor commit() for it. An earlier held candidate
   *  stays held; with none, nothing is held and the next frame in the window reads again (ruling S28). */
  noteReadFailed(t: number): void {
    this.ask = null;
    this.note(t, this.frames, 'read-failed');
  }

  /** Commit the held candidate (a revisit's, when this frame was one). Sensor-only: place it by the predictor
   *  (P = C . G with C the identity) and paint it. Otherwise align it against its reference keyframe, fuse the two poses,
   *  grade the match, chain the gains and the edge, and feed the focal and latency estimators (T27). */
  commit(): Keyframe {
    const rev = this.rev, c = rev ?? this.cand;
    if (!c) throw new Error('Tracker.commit: no candidate is held');
    if (rev) this.rev = null; else this.cand = null;
    this.ask = null;
    const id = this.kfs.length;
    const cls: KeyClass = c.rate > this.rateMax ? 'blurred' : 'sensor';
    const kf: Keyframe = {
      id, frameId: c.frameId, t: c.t, pred: c.pred, pose: c.pred, cls, rate: c.rate, up: c.up, sigmaDeg: 0,
      gainRGB: [1, 1, 1], strip: c.strip, stripX0: c.stripX0, stripW: c.stripW, stripH: c.stripH, pyr: c.pyr,
      skyLuma: c.skyLuma,
    };
    // Rule 6: the trailing fill decided at hold() (see fillFor).
    this.kfs.push(kf);
    this.trailing.push(c.fill);
    this.unwrappedAt.push(c.unwrapped);
    this.loopState.unwrappedDeg = c.unwrapped - this.unwrappedAt[0];
    if (!this.sensorOnly) return this.commitAligned(kf, c);
    kf.sigmaDeg = this.sigmaFor(kf);
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

  /** The yaw of C at t, degrees, linear between the keyframes' corrections (4.12: NorthAnchor subtracts it from the compass
   *  offset). A keyframe's correction is the heading of its pose minus the heading of its prediction; before the first it is
   *  that keyframe's, after the newest it is held. Sensor-only never corrects, so 0. */
  correctionYawAt(t: number): number {
    const n = this.kfs.length;
    if (this.sensorOnly || n === 0 || !finite(t)) return 0;
    // Keyframes are in commit order, which is not time order once a revisit has been committed ahead of a held candidate.
    let before = -1, after = -1;
    for (let i = 0; i < n; i++) {
      const ti = this.kfs[i].t;
      if (ti <= t && (before < 0 || ti >= this.kfs[before].t)) before = i;
      if (ti >= t && (after < 0 || ti <= this.kfs[after].t)) after = i;
    }
    if (before < 0) return this.cYaw[after];
    if (after < 0 || before === after) return this.cYaw[before];
    const t0 = this.kfs[before].t, t1 = this.kfs[after].t;
    return t1 > t0 ? this.cYaw[before] + (this.cYaw[after] - this.cYaw[before]) * (t - t0) / (t1 - t0) : this.cYaw[before];
  }

  /** Finish: clear the raster and repaint every keyframe with worldYaw(worldYawDeg) composed, which ADDS the yaw to
   *  every azimuth. A pending re-render is dropped, because this repaints everything. The chained gains are first
   *  normalised so that the median keyframe gain is 1 per channel (4.10); sensor-only gains are all 1 already. */
  renderAll(worldYawDeg: number): void {
    this.queue = [];
    this.renderYawDeg = worldYawDeg;
    if (!this.sensorOnly) this.normaliseGains();
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

  private read(commit: boolean, why: 'first' | 'step' | 'revisit', d: number | null, nearest: number): FrameStep {
    this.ask = {
      commit, d, nearest, unwrapped: this.unwrapped, revisit: why === 'revisit', gUnwrapped: this.gUnwrapped, omega: this.frameOmega,
    };
    return { read: true, commit, why };
  }

  /** Rule 6: a frame read at d >= S + 2 came from no window, so it paints its trailing side to meet the slice of the
   *  keyframe d was measured to, min((d - S) / cos(pitch), 10) degrees. The side is where that keyframe's axis falls in
   *  the image of `q`, the pose the frame is placed at (its prediction in v0). The painted limit is a tangent angle in
   *  the image and a degree of it spans only cos(pitch) degrees of azimuth off the level, so at ring pitch 23 the plain
   *  d - S left alpha-0 columns between the slices; the extension is widened by 1 / cos of the optical axis's altitude
   *  (ruling S34), and the strip is cut out as far (hold). The raster clamps a slice's fill at TRAILING_MAX_DEG. */
  private fillFor(d: number | null, nearest: number, q: Quat): Fill {
    if (d === null || d < STEP_DEG + WINDOW_DEG) return { deg: 0, side: 0 };
    const prev = qrotate(qinv(q), qrotate(this.kfs[nearest].pose, FORWARD));
    const cosPitch = Math.max(MIN_COS_PITCH, Math.cos(elevationDeg(q) * DEG));
    return { deg: Math.min((d - STEP_DEG) / cosPitch, TRAILING_MAX_DEG), side: prev[0] < 0 ? -1 : 1 };
  }

  /** Rule 8's trigger, called once per frame with a prediction: the previous such frame's nearest keyframe P when this
   *  frame's nearest differs from it and both existed at that frame, so the axis has crossed the midpoint between them;
   *  else -1. A keyframe committed since that frame has an id at or past its count, so a new commit does not count. */
  private crossedFrom(nearest: number): number {
    const p = this.prevNearest, existed = this.prevCount;
    this.prevNearest = nearest;
    this.prevCount = this.kfs.length;
    return p >= 0 && nearest >= 0 && nearest !== p && nearest < existed ? p : -1;
  }

  /** The largest unpainted run, degrees, of the coverage cells whose centres lie on the shorter arc between the headings
   *  of keyframes a and b. */
  private unpaintedBetween(a: number, b: number): number {
    const cellDeg = 360 / this.pano.coverage.length;
    const ha = headingDeg(this.kfs[a].pose), hb = ha + wrap180(headingDeg(this.kfs[b].pose) - ha);
    const lo = Math.min(ha, hb) + this.renderYawDeg, hi = Math.max(ha, hb) + this.renderYawDeg;
    const first = Math.ceil(lo / cellDeg - 0.5);
    return this.unpaintedRunDeg(first, Math.floor(hi / cellDeg - 0.5) - first + 1);
  }

  /** The largest run of unpainted coverage cells among `count` cells from `first` (wrapping), degrees. */
  private unpaintedRunDeg(first: number, count: number): number {
    const cov = this.pano.coverage, cells = cov.length;
    let run = 0, best = 0;
    for (let j = 0; j < count; j++) {
      if (cov[(((first + j) % cells) + cells) % cells] === 0) { run++; if (run > best) best = run; } else run = 0;
    }
    return best * 360 / cells;
  }

  /** Unwrapped heading of the live pose, accumulated over every frame with a prediction, active or not, so a turn
   *  made while paused is counted. Each frame-to-frame change is wrapped to [-180, 180). */
  private trackHeading(live: Quat): void {
    const h = headingDeg(live);
    if (this.heading !== null) this.unwrapped += wrap180(h - this.heading);
    this.heading = h;
  }

  // ---- Stage 2: alignment, fusion and the focal lock (T27) -------------------------

  /** Per frame with a prediction: the predictor's unwrapped heading, the signed heading rate at this frame, and the onset
   *  of the motion. The rate's magnitude is the mapped gyro's (`rateDegS`: PoseTrack reads it only once the axis mapping
   *  is confirmed, else from its own orientation chord; S33, S41), and its sign is the heading chord's, taken over more
   *  than CHORD_MIN_MS and at most CHORD_MAX_MS of the predictor, never over two consecutive frames (S10, S31). With no
   *  gyro rate the chord's own rate stands in, and with no chord the rate is unknown unless the phone is still. */
  private trackPredictor(t: number, pred: Prediction, rateDegS: number | null): void {
    const h = headingDeg(pred.q);
    if (this.gHeading !== null) this.gUnwrapped += wrap180(h - this.gHeading);
    this.gHeading = h;
    const hist = this.gHist;
    hist.push({ t: pred.t, u: this.gUnwrapped });
    while (hist.length > 2 && hist[0].t < pred.t - HEADING_KEEP_MS) hist.shift();
    let j = hist.length - 2;
    while (j >= 0 && pred.t - hist[j].t <= CHORD_MIN_MS) j--;
    const chord = j >= 0 && pred.t - hist[j].t <= CHORD_MAX_MS ? (this.gUnwrapped - hist[j].u) / ((pred.t - hist[j].t) / 1000) : null;
    const rate = finite(rateDegS) ? rateDegS : null;
    let omega: number | null;
    if (rate === null) omega = chord;
    // A gyro quieter than the predictor's own turn is a unit not yet confirmed (rad/s read as deg/s) or a dead sensor: the
    // chord is the rate (S33).
    else if (chord !== null && Math.abs(chord) > QUIET_GYRO_FACTOR * rate + QUIET_GYRO_DEG_S) omega = chord;
    else if (rate < STILL_RATE_DEG_S) omega = 0;
    else if (chord !== null && Math.abs(chord) >= HEADING_SHARE * rate) omega = Math.sign(chord) * rate;
    else omega = chord;
    this.frameOmega = omega;
    if (rate !== null) {
      if (this.lastRate !== null && this.lastRate < ONSET_RATE_DEG_S && rate >= ONSET_RATE_DEG_S) this.riseT = t;
      this.lastRate = rate;
    }
  }

  /** Rule 5's test: the 6-degree slit under the live heading holds a cell that a blurred or refused keyframe painted and
   *  that no revisit has tried (`revisitCells`), none of its painted cells is aligned, and it is not yet aligned over. */
  private slitIsAmber(live: Quat): boolean {
    const cov = this.pano.coverage, cells = cov.length, cellDeg = 360 / cells;
    const h = headingDeg(live) + this.renderYawDeg;
    const first = Math.ceil((h - SLIT_HALF_DEG) / cellDeg - 0.5), last = Math.floor((h + SLIT_HALF_DEG) / cellDeg - 0.5);
    let amber = false;
    for (let i = first; i <= last; i++) {
      const c = ((i % cells) + cells) % cells, v = cov[c];
      if (v === PixClass.Aligned) return false;
      if (v !== PixClass.None && this.revisitCells[c] !== 0) amber = true;
    }
    return amber;
  }

  /** Sets (on) or clears the revisit cells within the slit's half width of `headingDeg`. */
  private markRevisit(heading: number, on: boolean): void {
    const cells = this.revisitCells.length, cellDeg = 360 / cells;
    const first = Math.ceil((heading - SLIT_HALF_DEG) / cellDeg - 0.5), last = Math.floor((heading + SLIT_HALF_DEG) / cellDeg - 0.5);
    for (let i = first; i <= last; i++) this.revisitCells[((i % cells) + cells) % cells] = on ? 1 : 0;
  }

  /** S32: forget the first keyframe, its raster, its capture record and everything chained to it, and start again. The log
   *  holds one 'accepted' record for each keyframe, in order (the scanner pairs them by index), so the retaken keyframe 0
   *  must not be the second record to say 'first'. */
  private dropFirstKeyframe(): void {
    this.kfs.length = 0; this.trailing.length = 0; this.unwrappedAt.length = 0; this.links.length = 0; this.cYaw.length = 0;
    this.edgeList.length = 0;
    for (let i = this.records.length - 1; i >= 0; i--) if (this.records[i].kf !== undefined) this.records.splice(i, 1);
    this.tplCache = null; this.cand = null; this.rev = null; this.queue = [];
    this.visitedLo = 0; this.visitedHi = 0; this.prevNearest = -1; this.prevCount = 0;
    this.loopState.unwrappedDeg = 0;
    this.mismatches = 0;
    this.revisitCells.fill(0);
    this.pano.clear();
  }

  /** The template of a keyframe at the focal LK runs at: the live one is cached, an older one is rebuilt from its pyramid. */
  private templateFor(kf: Keyframe, k: { l1: Intrinsics; l2: Intrinsics }, f: number): Template {
    const cached = this.tplCache;
    if (cached && cached.id === kf.id && cached.f === f) return cached.tpl;
    const tpl = prepareTemplate(kf.pyr as Pyramid, k.l1, k.l2);
    this.tplCache = { id: kf.id, f, tpl };
    return tpl;
  }

  /** alignPair (4.6) of the candidate against its reference keyframe at the focal LK runs at (the prior until the lock).
   *  The prediction is qinv(C_ref . G_b) . P_a = qinv(G_b) . G_a, the gyro's own rotation between the two instants. */
  private alignAgainst(a: Keyframe, b: Keyframe, pyr: Pyramid): AlignResult {
    const f = this.focal.fMeasure;
    const k = {
      l0: intrinsicsAt(0, this.frameW, this.frameH, f), l1: intrinsicsAt(1, this.frameW, this.frameH, f),
      l2: intrinsicsAt(2, this.frameW, this.frameH, f),
    };
    const rgbA: RgbStrip = { strip: a.strip, x0: a.stripX0, w: a.stripW, h: a.stripH };
    const rgbB: RgbStrip = { strip: b.strip, x0: b.stripX0, w: b.stripW, h: b.stripH };
    return alignPair(this.templateFor(a, k, f), pyr, qmul(qinv(b.pred), a.pred), k, { window: 'keyframe', rgbA, rgbB });
  }

  /** The rotation of an edge re-solved at `fNorm` from its correspondences (Horn): a pixel displacement is what LK measured,
   *  and the focal turns it into an angle (4.8). */
  private resolveAt(e: Edge, fNorm: number): Quat {
    const k0 = intrinsicsAt(0, this.frameW, this.frameH, fNorm);
    const a: V3[] = [], b: V3[] = [];
    for (const c of e.corr) { a.push(unprojectPixel(k0, c.ua, c.va)); b.push(unprojectPixel(k0, c.ub, c.vb)); }
    return rotationFromRays(a, b);
  }

  /** Fuse keyframe `id` with its reference (4.7) and set its pose and class. Everything is read from the link, so the lock
   *  can run it again for the whole chain.
   *    P_g = C_ref . G_k            the gyro-predicted pose (C_ref = P_ref . G_ref^-1, the newest C on a chain step)
   *    P_img = P_ref . qinv(qBA)    the image pose, from the edge re-solved at f_best once the focal is locked or has
   *                                 3 ratios (the Horn re-solve of 4.8)
   *    v = log(P_g^-1 . P_img)      the innovation, in the candidate's camera axes (x pitch, y yaw, z roll)
   *    W = Sigma_g / (Sigma_g + Sigma_img) per axis;  P_k = P_g . exp(W v);  then the tilt pull.
   *  Sigma_img is the edge's covariance in deg^2 plus the focal term diag((sdF dPsi_pitch)^2, (sdF dPsi_yaw)^2, 0) (S38).
   *  The class is blurred above rateMax; else aligned when the gate passes and W_yaw WITHOUT the focal term is at least
   *  0.5, so a good match is aligned whatever the focal state; else sensor. A match beyond the gate is refused and the
   *  gyro places the keyframe (`accepted` false). */
  private fuse(id: number): Fused {
    const kf = this.kfs[id], L = this.links[id];
    const none: Fused = { accepted: false, innovationDeg: null, wYaw: null, imgMinusGyroDeg: null };
    const gyroCls: KeyClass = L.fast ? 'blurred' : 'sensor';
    if (L.ref < 0) { kf.pose = kf.pred; kf.cls = gyroCls; return none; }
    const ref = this.kfs[L.ref];
    const pGyro = qmul(qmul(ref.pose, qinv(ref.pred)), kf.pred);
    kf.cls = gyroCls;
    const e = L.edge;
    if (!e) { kf.pose = pullTilt(pGyro, kf.pred, 1 - TILT_PULL); return none; }

    const f = this.focal;
    const fTarget = f.state !== 'prior' || f.ratios >= 3 ? f.fBest : L.fMeas;
    const qBA = Math.abs(fTarget - L.fMeas) > 1e-9 * L.fMeas && e.corr.length >= HORN_MIN_CORR ? this.resolveAt(e, fTarget) : e.qBA;
    const pImg = qmul(ref.pose, qinv(qBA));
    const v = logSO3(qmul(qinv(pGyro), pImg));
    const innovationDeg = Math.hypot(v[0], v[1], v[2]) / DEG;
    const imgMinusGyroDeg = wrap180(headingDeg(pImg) - headingDeg(pGyro));

    const sg2 = predictorVarianceDeg2(L.mode, Math.abs(L.dPsiDeg), L.dtS, L.dOmega ?? UNKNOWN_DOMEGA_DEG_S, this.latency.sigmaMs);
    const cov = e.covRad2, sdF = f.sdPct / 100;
    const cx = cov[0] * RAD_TO_DEG2, cy = cov[4] * RAD_TO_DEG2, cz = cov[8] * RAD_TO_DEG2;
    const ix = cx + (sdF * L.dPitchDeg) ** 2, iy = cy + (sdF * L.dPsiDeg) ** 2, iz = cz;
    const weight = (img: number) => { const w = sg2 / (sg2 + img); return Number.isFinite(w) ? w : 0; };
    const wYaw = weight(iy), wYawNoFocal = weight(cy);
    const gate = innovationGateDeg({ locked: f.state !== 'prior', ratios: f.ratios, dPsiGyroDeg: L.dPsiDeg, sigmaGDeg: Math.sqrt(sg2) });
    const accepted = Number.isFinite(innovationDeg) && innovationDeg <= gate;
    let pose = pGyro;
    if (accepted) {
      pose = qmul(pGyro, expSO3([weight(ix) * v[0], wYaw * v[1], weight(iz) * v[2]]));
      if (!L.fast && wYawNoFocal >= ALIGNED_W_YAW) kf.cls = 'aligned';
    }
    kf.pose = pullTilt(pose, kf.pred, 1 - TILT_PULL);
    return { accepted, innovationDeg, wYaw, imgMinusGyroDeg };
  }

  /** Stage 2 of commit(): the keyframe is pushed; align it, fuse it, chain the gain and the edge, feed the estimators, run
   *  the focal ratio and the lock, paint, and log. */
  private commitAligned(kf: Keyframe, c: Candidate): Keyframe {
    const id = kf.id, fast = c.rate > this.rateMax;
    // 4.6: the pair is (template = the previous keyframe, candidate). A revisit aligns against the nearest keyframe in the
    // corrected frame (rule 5): it is somewhere the map already is, perhaps long ago. So does a commit the previous keyframe is
    // too far from to overlap. Time order beats nearness otherwise: at the ring seam the nearest keyframe in the corrected
    // frame can be keyframe 0, 350 degrees of chain away, while the step before is 5 degrees of gyro away (and the gyro's own
    // frame has lapped even earlier, so its nearest is keyframe 0 as well).
    const ref = id === 0 ? -1
      : c.revisit || axisSeparationDeg(this.kfs[id - 1].pred, c.pred) > CHAIN_REF_MAX_DEG ? c.nearest : id - 1;
    const a = ref >= 0 ? this.kfs[ref] : null, la = ref >= 0 ? this.links[ref] : null;
    const link: Link = {
      ref, revisit: c.revisit, edge: null, fMeas: this.focal.fMeasure, mode: c.mode,
      dPsiDeg: a ? wrap180(headingDeg(c.pred) - headingDeg(a.pred)) : 0,
      dPitchDeg: a ? elevationDeg(c.pred) - elevationDeg(a.pred) : 0,
      dtS: a ? Math.abs(c.t - a.t) / 1000 : 0,
      dOmega: la && la.omega !== null && c.omega !== null ? c.omega - la.omega : null,
      omega: c.omega, gUnwrapped: c.gUnwrapped, tauMs: c.tauMs, fast, steps: la ? la.steps + 1 : 0, mismatch: false,
    };
    this.links.push(link);

    let res: AlignResult | null = null;
    if (a) {
      res = this.alignAgainst(a, kf, c.pyr as Pyramid);
      if (res.ok) {
        link.edge = {
          a: ref, b: id, kind: c.revisit ? 'revisit' : 'chain', qBA: res.qBA, covRad2: res.covRad2, corr: res.corr, zncc: res.zncc,
          meanA: res.meanA, meanB: res.meanB, n: res.n,
        };
      }
    }
    const fused = this.fuse(id);
    if (link.edge && !fused.accepted) link.edge = null;   // beyond the gate: the match is refused and the gyro placed it
    link.mismatch = res !== null && (res.ok ? !fused.accepted : res.textured);
    this.mismatches = link.mismatch ? this.mismatches + 1 : 0;

    if (a) kf.gainRGB = link.edge ? chainGain(a.gainRGB, link.edge) : [a.gainRGB[0], a.gainRGB[1], a.gainRGB[2]];
    if (link.edge) {
      this.edgeList.push(link.edge);
      if (!c.revisit && ref === id - 1 && kf.cls === 'aligned' && fused.imgMinusGyroDeg !== null) this.feedLatency(id, fused.imgMinusGyroDeg);
    }
    kf.sigmaDeg = this.sigmaFor(kf);
    this.refreshCYaw(id);
    if (c.revisit) this.markRevisit(headingDeg(kf.pose), false);
    else if (kf.cls === 'blurred' || (kf.cls === 'sensor' && link.mismatch)) this.markRevisit(headingDeg(kf.pose), true);

    const locked = link.edge !== null && !c.revisit && this.tryRatio(id);
    if (locked) this.onLock(); else this.paintKeyframe(kf);

    let detail: FrameDetail;
    if (id === 0) detail = 'first';
    else if (c.d !== null && c.d - STEP_DEG > TRAILING_MAX_DEG) detail = 'gap';   // beyond the fill: the gap stays grey
    else if (locked) detail = 'focal-lock';
    else if (kf.cls === 'aligned') detail = 'aligned';
    else if (kf.cls === 'blurred') detail = 'blurred';
    else detail = link.mismatch ? 'mismatch' : res !== null && !res.ok && !res.textured ? 'no-texture' : 'sensor';
    const r: CaptureRecord = {
      at: this.lastT, frameId: c.frameId, outcome: c.revisit ? 'revisit' : 'accepted', detail, kf: id, rateDegS: c.rate,
      extrapolatedMs: c.extrapolatedMs,
    };
    if (c.d !== null) r.stepDeg = c.d;
    if (res) { r.psr = res.psr; r.zncc = res.zncc; }
    if (fused.innovationDeg !== null) r.innovationDeg = fused.innovationDeg;
    if (fused.wYaw !== null) r.wYaw = fused.wYaw;
    this.records.push(r);
    return kf;
  }

  /** latency.addPair for an aligned chain pair (a, b) (4.5): r is the image yaw minus the predictor yaw over the pair, both
   *  at the tau used for the frames, and dOmega is the SIGNED heading rate at b minus that at a. The model is
   *  r = a + (tauUsed - tau) dOmega / 1000 for one tauUsed; when tau was applied between the two frames the two differ and
   *  the single value that reproduces r is (tau_b omega_b - tau_a omega_a) / dOmega. */
  private feedLatency(id: number, rDeg: number): void {
    const b = this.links[id], a = this.links[b.ref];
    if (a.omega === null || b.omega === null) return;
    const dOmega = b.omega - a.omega;
    if (!(Math.abs(dOmega) > 0)) return;
    const tauUsed = (b.tauMs * b.omega - a.tauMs * a.omega) / dOmega;
    this.latency.addPair(rDeg, dOmega, finite(tauUsed) ? tauUsed : b.tauMs);
  }

  /** The onset of the motion for the steady-pair rule: the first keyframe, or the last time the rate rose through 5 deg/s. */
  private onsetT(): number { return Math.max(this.kfs[0].t, this.riseT); }

  /** 4.8: every keyframe of the span (a..k) is an aligned chain step of the one before it, the heading rates differ by
   *  less than 2 deg/s over the span (strict), and the span starts 0.5 s or more after the onset. */
  private steadySpan(a: number, k: number): boolean {
    if (this.kfs[a].t - this.onsetT() < ONSET_SKIP_MS) return false;
    let lo = Infinity, hi = -Infinity;
    for (let i = a; i <= k; i++) {
      const L = this.links[i];
      if (this.kfs[i].cls !== 'aligned' || L.omega === null) return false;
      if (i > a && (L.revisit || !L.edge || L.ref !== i - 1)) return false;
      if (L.omega < lo) lo = L.omega;
      if (L.omega > hi) hi = L.omega;
    }
    return hi - lo < STEADY_SPAN_DEG_S;
  }

  /** The chain's composed image yaw over keyframes a..k, degrees, measured at the focal LK ran at (4.8): the edges'
   *  rotations composed, applied to the predictor's pose at a, and the change of heading that results. The gyro's yaw over
   *  the same span is the predictor's heading change, so the two share their base and only the rotation differs. */
  private chainYawDeg(a: number, k: number): number {
    let q: Quat = IDENTITY;
    for (let i = a + 1; i <= k; i++) q = qmul((this.links[i].edge as Edge).qBA, q);
    const base = this.kfs[a].pred;
    return wrap180(headingDeg(qmul(base, qinv(q))) - headingDeg(base));
  }

  /** One focal ratio per aligned chain commit, over k-3 or, when that span is not steady, k-2 (4.8). True when this ratio
   *  locks the focal. */
  private tryRatio(k: number): boolean {
    if (this.focal.state !== 'prior') return false;
    for (const j of [3, 2]) {
      const a = k - j;
      if (a < 0 || !this.steadySpan(a, k)) continue;
      return this.focal.addRatio(this.chainYawDeg(a, k), this.links[k].gUnwrapped - this.links[a].gUnwrapped) === 'locked-now';
    }
    return false;
  }

  /** The focal has locked (4.8): re-solve every edge from its correspondences at f_lock, re-fuse the chain in order with the
   *  locked sd (classes follow), recompute the sigmas and the corrections, and repaint every keyframe through pump(). */
  private onLock(): void {
    const target = this.focal.fBest;
    for (const L of this.links) {
      if (L.edge && L.fMeas !== target && L.edge.corr.length >= HORN_MIN_CORR) { L.edge.qBA = this.resolveAt(L.edge, target); L.fMeas = target; }
    }
    this.tplCache = null;
    for (let id = 1; id < this.kfs.length; id++) {
      const L = this.links[id], fused = this.fuse(id);
      if (L.edge && !fused.accepted) {
        const at = this.edgeList.indexOf(L.edge);
        if (at >= 0) this.edgeList.splice(at, 1);
        L.edge = null;
      }
    }
    for (const kf of this.kfs) { kf.sigmaDeg = this.sigmaFor(kf, true); this.refreshCYaw(kf.id); }
    this.scheduleRerender();
  }

  /** The yaw of C at keyframe `id`: the heading of its pose minus that of its prediction, unwrapped along the ids. */
  private refreshCYaw(id: number): void {
    const kf = this.kfs[id], prev = id > 0 ? this.cYaw[id - 1] : 0;
    const raw = wrap180(headingDeg(kf.pose) - headingDeg(kf.pred));
    this.cYaw[id] = prev + wrap180(raw - prev);
  }

  /** Normalise the chained gains so that the median keyframe gain is 1 per channel (4.10, at Finish). */
  private normaliseGains(): void {
    const n = this.kfs.length;
    for (let ch = 0; ch < 3; ch++) {
      const g = this.kfs.map(kf => kf.gainRGB[ch]).sort((x, y) => x - y);
      const med = n % 2 ? g[(n - 1) / 2] : (g[n / 2 - 1] + g[n / 2]) / 2;
      if (!(med > 0)) continue;
      for (const kf of this.kfs) kf.gainRGB[ch] /= med;
    }
  }

  /** Keyframe 0 is the gauge and, in v0, the only anchor. As an anchor it adds nothing, because it defines the scan
   *  frame (4.7's sensor-only example, 3.6, has no anchor term); its own sigma is the formula at dPsi = dt = 0, which
   *  is the 0.5 floor unless sigma_tau omega is large. dPsi is the net unwrapped turn, which is what a scale error
   *  multiplies. */
  private sigmaFor(kf: Keyframe, bothSides = false): number {
    const gauge = this.kfs[0];
    if (this.sensorOnly) {
      return placementSigmaDeg({
        aligned: kf.cls === 'aligned', stepsToGauge: kf.id,
        dPsiToAnchorDeg: Math.abs(this.unwrappedAt[kf.id] - this.unwrappedAt[0]), dtToAnchorS: Math.abs(kf.t - gauge.t) / 1000,
        omegaDegS: kf.rate, sigmaTauMs: this.latency.sigmaMs, anchorSigmaDeg: 0, closed: false,
      });
    }
    // Stage 2 (4.7): an aligned keyframe is 0.05 sqrt(s) for s chain steps to the gauge; any other runs from the nearest
    // aligned keyframe in chain order, on either side when the later ones exist (after a lock or closure), and keyframe 0
    // counts as an anchor with sigma 0, because it is the gauge. dPsi is the net unwrapped turn.
    const id = kf.id, n = this.kfs.length;
    let anchor = id, best = Infinity;
    for (let j = 0; j < (bothSides ? n : id); j++) {
      if (j !== 0 && this.kfs[j].cls !== 'aligned') continue;
      const dPsi = Math.abs(this.unwrappedAt[j] - this.unwrappedAt[id]);
      if (dPsi < best) { best = dPsi; anchor = j; }
    }
    const anchorSigma = anchor === 0 || anchor === id ? 0
      : placementSigmaDeg({ aligned: true, stepsToGauge: this.links[anchor].steps, dPsiToAnchorDeg: 0, dtToAnchorS: 0,
        omegaDegS: 0, sigmaTauMs: 0, anchorSigmaDeg: 0, closed: false });
    return placementSigmaDeg({
      aligned: kf.cls === 'aligned', stepsToGauge: this.links[id].steps,
      dPsiToAnchorDeg: Math.abs(this.unwrappedAt[anchor] - this.unwrappedAt[id]), dtToAnchorS: Math.abs(kf.t - this.kfs[anchor].t) / 1000,
      omegaDegS: kf.rate, sigmaTauMs: this.latency.sigmaMs, anchorSigmaDeg: anchorSigma, closed: false,
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

/** The tilt of C pulled toward zero (4.5, Level): C = P . G^-1 is split into T . Y, T the shortest rotation taking world up
 *  to C's up and Y a rotation about up, and T is scaled by `kappa` (1 keeps it, 0 removes it). Returns the pose C' . G. */
function pullTilt(pose: Quat, pred: Quat, kappa: number): Quat {
  const c = qmul(pose, qinv(pred)), cz = qrotate(c, UP);
  const ax: V3 = [UP[1] * cz[2] - UP[2] * cz[1], UP[2] * cz[0] - UP[0] * cz[2], UP[0] * cz[1] - UP[1] * cz[0]];
  const s = Math.hypot(ax[0], ax[1], ax[2]);
  if (!(s > 1e-12)) return pose;
  const angle = Math.atan2(s, cz[2]);
  const rot = (k: number) => expSO3([ax[0] / s * angle * k, ax[1] / s * angle * k, ax[2] / s * angle * k]);
  return qmul(qmul(rot(kappa), qmul(qinv(rot(1)), c)), pred);
}

/** The gain of a keyframe chained from its reference's (4.10): g_b = g_a . meanA / meanB per channel over the edge's
 *  overlap, each step clamped to 0.67..1.5. A channel with no overlap statistic keeps the reference's gain. */
function chainGain(ref: readonly [number, number, number], e: Edge): [number, number, number] {
  const out: [number, number, number] = [ref[0], ref[1], ref[2]];
  if (!(e.n > 0)) return out;
  for (let ch = 0; ch < 3; ch++) {
    if (e.meanA[ch] > 0 && e.meanB[ch] > 0) out[ch] = ref[ch] * Math.min(GAIN_STEP_MAX, Math.max(GAIN_STEP_MIN, e.meanA[ch] / e.meanB[ch]));
  }
  return out;
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
