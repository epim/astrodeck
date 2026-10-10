// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The panorama scanner, `PanoramaScanner`, behind the replay's seam (SPEC-v2 7.4, 13.7).
//
// Everything here reads the scanner through its public surface (`status`, `report()`, `inspect()`, the result of
// `finish`) and writes it in the snake_case shapes the scorer reads. The replay hands the case's options to
// `create`; the declination is applied here as the closure the sheet would hand over (`az => az + d`, ruling S20), so
// the scanner sees exactly what it sees on the phone and the replay never touches its internals.
//
// Frame ids. The scanner numbers frames by delivery (`FrameMeta.frameNo`, 1 for the first), and that number is every
// `CaptureRecord.frameId` and every keyframe's `frameId` (S24). The replay calls `noteFrame` with the observation's
// id just before each frame, so the n-th id noted is frame n, and every `frame_id` written below is the case's own.
//
// Times. The report and the diagnostics carry ms since the camera opened. The replay's camera opens at its clock's
// zero, so on a replay those are the case's own times, which the scorer compares with frame capture times.
import { BinState, PANO_H, PANO_W } from '../pano/types';
import type { HorizonPoint, ScanResult, ScannerOptions } from '../pano/types';
import { basisFromQuat, qmul, worldYaw } from '../pano/rotation';
import { PanoramaScanner } from '../pano/scanner';
import type {
  Diagnostics, FrameReport, HorizonOutV2, LiveSnapshot, ScannerFactory, ScannerUnderTest,
} from './scannerUnderTest';

/** A CaptureRecord field and the snake_case key captures.jsonl gives it (13.7), in the order they are written. */
const CAPTURE_FIELDS: readonly (readonly [string, string])[] = [
  ['detail', 'detail'], ['kf', 'kf'], ['stepDeg', 'step_deg'], ['rateDegS', 'rate_deg_s'], ['psr', 'psr'], ['zncc', 'zncc'],
  ['innovationDeg', 'innovation_deg'], ['wYaw', 'w_yaw'], ['extrapolatedMs', 'extrapolated_ms'],
];

class PanoScanner implements ScannerUnderTest {
  private readonly scanner: PanoramaScanner;
  /** The observation id of each delivered frame, in delivery order: ids[n - 1] is frame n. */
  private readonly ids: string[] = [];
  private result: ScanResult | null = null;

  constructor(o: ScannerOptions & { declinationDeg: number | null }) {
    this.scanner = new PanoramaScanner({ focalPriorScale: o.focalPriorScale, sensorOnly: o.sensorOnly, encoder: o.encoder });
    const declination = o.declinationDeg;
    if (declination !== null) this.scanner.setDeclination(az => az + declination);
  }

  start(video: HTMLVideoElement): Promise<void> { return this.scanner.start(video); }

  get canBegin(): boolean { return this.scanner.canBegin; }

  begin(): void { this.scanner.begin(); }

  get isRecording(): boolean { return this.scanner.inspect().beginMs !== null; }

  noteFrame(frameId: string): void { this.ids.push(frameId); }

  frameReport(): FrameReport {
    const st = this.scanner.status, last = this.scanner.inspect().lastFrame;
    const pose = last?.livePose ?? null;
    return {
      // The live corrected pose, in the scan frame (13.7).
      basis: pose ? basisFromQuat(pose) : null,
      cue: st.cue, aim: null, compassReady: st.north !== null, tiltReady: st.elevationDeg !== null, frameCount: st.frameNo,
      extra: { keyframe: last?.keyframe ?? false, cls: last?.cls ?? null },
    };
  }

  /** Finish with no previous line: the replay saves nothing, so there is nothing for O1 to keep. */
  finishScan(): void {
    this.result = this.scanner.finish(null, 'user');
  }

  panorama(): { width: 1080; height: 300; pixels: Uint8ClampedArray } | null {
    return { width: PANO_W, height: PANO_H, pixels: this.finished().rgba };
  }

  horizon(): HorizonOutV2 {
    const { draft, points, tau } = this.finished();
    const uncertain: number[] = [];
    draft.state.forEach((s, i) => {
      if (s === BinState.Low || s === BinState.Unknown || s === BinState.Tall) uncertain.push(i);
    });
    return {
      version: 2, interpolation: 'linear-wrap', profile_bins: 720,
      profile: Array.from(draft.alt),
      profile_traced: Array.from(draft.suggested, v => (Number.isFinite(v) ? v : null)),
      profile_state: Array.from(draft.state),
      points: points.map((p: HorizonPoint) => ({ az: p.az, alt: p.alt })), tau, bins: 720, uncertain_bins: uncertain,
    };
  }

  /** `first_seen.bin` in the frame of `panorama.png` (13.7, S30): the scanner's scan-frame snapshot, taken before the
   *  Finish re-render, moved by round(worldYaw x 3) columns, since worldYaw adds its angle to every azimuth. */
  firstSeen(): Uint16Array | null {
    this.finished();
    const { firstSeenAtFinish: snap, worldYawDeg } = this.scanner.inspect();
    if (snap === null) return null;
    const shift = ((Math.round((worldYawDeg ?? 0) * PANO_W / 360) % PANO_W) + PANO_W) % PANO_W;
    const out = new Uint16Array(snap.length);
    for (let y = 0; y < PANO_H; y++) {
      const row = y * PANO_W;
      for (let x = 0; x < PANO_W; x++) out[row + (x + shift) % PANO_W] = snap[row + x];
    }
    return out;
  }

  diagnostics(): Diagnostics | null {
    this.finished();
    const r = this.scanner.report(), ins = this.scanner.inspect();
    const s = r.sensors, loop = r.loop, north = r.north, open = ins.openMs ?? 0, yaw = worldYaw(ins.worldYawDeg ?? 0);
    return {
      version: 1, scanner: 'pano', sensor_only: r.scanner.sensorOnly,
      begin_ms: r.timeline.beginMs, finish_ms: r.timeline.finishMs,
      predictor_mode: s.modeAtBegin,
      mode_changes: s.modeChanges.map(c => ({ t_ms: c.tMs, from: c.from, to: c.to })),
      axis_mapping: s.axis && { perm: [...s.axis.perm], sign: [...s.axis.sign], unit: s.axis.unit, fit: s.axis.fit, confirmed: s.axis.confirmed },
      tau_ms: s.latency.tauMs, tau_sigma_ms: s.latency.sigmaMs, tau_pairs: s.latency.pairs, tau_applied: s.latency.applied,
      focal: { state: r.focal.state, f_norm: r.focal.fNorm, sd_pct: r.focal.sdPct, ratios: r.focal.ratios, short_fov_deg: r.focal.shortFovDeg },
      loop: {
        closed: loop.closed, method: loop.method, pre_deg: loop.preDeg ? [...loop.preDeg] : null, post_deg: loop.postDeg,
        match: loop.match ? { early_kf: loop.match.early, late_kf: loop.match.late } : null, unwrapped_deg: loop.unwrappedDeg,
      },
      north: north && {
        offset_deg: north.offsetDeg, sigma_deg: north.sigmaDeg, spread_deg: north.spreadDeg, samples: north.samples,
        n_eff: north.nEff, stable: north.stable, source: north.source,
      },
      declination_applied: r.declinationApplied,
      // The final world pose of each keyframe: its scan-frame pose with the Finish yaw composed, camera to world.
      keyframes: ins.tracker.keyframes.map(kf => {
        const q = qmul(yaw, kf.pose);
        return {
          id: kf.id, frame_id: this.idOf(kf.frameId), t_ms: kf.t - open, q: [q[0], q[1], q[2], q[3]] as [number, number, number, number],
          cls: kf.cls, sigma_deg: kf.sigmaDeg,
        };
      }),
      keyframe_ms: r.keyframes.ms, readback_ms: r.keyframes.readbackMs, stale_refusals: s.staleRefusals,
      extractor: 'tracer',
    };
  }

  /** After a frame that committed a keyframe (the replay asks only then): the newest keyframe, as of that frame. */
  liveSnapshot(): LiveSnapshot | null {
    const ins = this.scanner.inspect(), st = this.scanner.status;
    const kfs = ins.tracker.keyframes;
    if (kfs.length === 0) return null;
    const log = ins.tracker.log;
    let at = kfs[kfs.length - 1].t;
    for (let i = log.length - 1; i >= 0; i--) if (log[i].outcome === 'accepted') { at = log[i].at; break; }
    const loop = st.loop;
    return {
      t_ms: at - (ins.openMs ?? 0), kf: kfs.length - 1, painted_fraction: st.coveredDeg / 360,
      closure_state: !loop.closed ? 'open' : loop.method === 'image' ? 'closed-image' : 'closed-gyro',
      focal_state: st.focal.state,
    };
  }

  /** The report's capture log, its times already ms since the camera opened, in the snake_case of captures.jsonl.
   *  A field that is absent or not a finite number (a rate that was never known) is not written. */
  captureLog(): Record<string, unknown>[] {
    return this.scanner.report().captureLog.map(record => {
      const source = record as unknown as Record<string, unknown>;
      const row: Record<string, unknown> = { at: record.at, frame_id: this.idOf(record.frameId), outcome: record.outcome };
      for (const [from, to] of CAPTURE_FIELDS) {
        const v = source[from];
        if (typeof v === 'string' || (typeof v === 'number' && Number.isFinite(v))) row[to] = v;
      }
      return row;
    });
  }

  stop(): void { this.scanner.stop(); }

  private finished(): ScanResult {
    if (this.result === null) this.finishScan();
    return this.result as ScanResult;
  }

  /** The case's id for frame n; the delivery-count form when the replay never noted one. */
  private idOf(frameNo: number): string {
    return this.ids[frameNo - 1] ?? `f${String(frameNo).padStart(6, '0')}`;
  }
}

export const panoFactory: ScannerFactory = { create: o => new PanoScanner(o) };
