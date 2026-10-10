// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T22: the panorama scanner v0 end to end (SPEC-v2 7.2, the scanner v0 row), on synthetic input.
//
// The scanner runs in the replay's browser (`__sim__/harness.ts`): the real CameraSource over a harness video, the
// real PoseTrack fed by dispatched events, the real tracker, raster, tracer and recorder. A synthetic pan (T17) gives
// the frames and the sensor streams. Frames are 45 x 80, which the harness's camera scales to the 180 x 320 analysis
// frame, so a pan renders in milliseconds. Where a case needs a camera the harness cannot fake (a slow readback, a
// track that stops), a CameraSourceLike stub stands in for CameraSource and the rest stays real.
//
// Mutants this file must catch (SPEC-v2 7.2 and the T22 brief), each against the case named:
//   * `stop()` keeping the listeners: 'stop() removes every listener it added' finds them still attached, and a
//     reading dispatched after stop() still reaching the pose track;
//   * `firstSeen` exported unshifted: 'first_seen.bin lines up with panorama.png under a non-zero world yaw' finds the
//     two masks a world yaw apart (S30);
//   * the declination written into the report: 'privacy: ...' finds a number within 0.001 of 12.3456 in the report, or
//     a report that differs from the same scan's without a declination in more than `horizon` and `declinationApplied`;
//   * motion events dropped from the recording: 'the recording holds every frame and event in order' finds the lines
//     it expects missing.
// Rows 9 and 10 of `cueFor` are cueFor.test.ts's.
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHarness, resample, type ReplayHarness } from '../../__sim__/harness';
import { decodePng } from '../../__sim__/png';
import { pngEncoder } from '../../__sim__/pngEncoder';
import { mergeObservations, replayCase, type FrameObservation, type Observation } from '../../__sim__/replay';
import { frameTime } from '../cameraSource';
import { ERROR_TEXT } from '../copy';
import { PRIOR_SD_PCT } from '../focal';
import { tracer } from '../horizon/horizonTrace';
import { STALE_LIMIT_MIN_MS } from '../poseTrack';
import { elevationDeg, headingDeg, quatFromDeviceOrientation } from '../rotation';
import { PanoramaScanner } from '../scanner';
import { BinState, ColState, PANO_H, PANO_W } from '../types';
import type {
  AnalysisFrame, CameraSettingsReport, CameraSourceLike, FrameEncoder, FrameMeta, HorizonPoint, Keyframe, ScanReport,
  ScanResult, Stats,
} from '../types';
import { DEFAULT_PAN, makeSynthScene, synthPan, writeReplayInput, type SynthPan } from './synth/synth';

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }

type Row = Record<string, unknown>;
const DEG = Math.PI / 180;
const wrap180 = (d: number) => ((((d + 180) % 360) + 360) % 360) - 180;
const fid = (n: number) => `f${String(n).padStart(6, '0')}`;
const lines = (text: string): Row[] => text.split(/\r?\n/).filter(l => l.length > 0).map(l => JSON.parse(l) as Row);

// ---- The synthetic pan and the harness driver ---------------------------------------------------

const scene = makeSynthScene({ seed: 3 });
/** 100 degrees at 12 deg/s (inside the 15 deg/s limit of 15 fps), both orientation streams and the gyro. */
const pan = synthPan({ ...DEFAULT_PAN, scene, w: 45, h: 80, fps: 15, speedDegS: 12, turnDeg: 100, startHoldS: 0.5, endHoldS: 0.5 });
const FINISH_AT = pan.actions[1].t_ms;
const DECLINATION = 12.3456;
const PREVIOUS: HorizonPoint[] = [{ az: 0, alt: 12 }, { az: 180, alt: 12 }];

const renders = new Map<SynthPan, Uint8ClampedArray[]>();
function imagesOf(p: SynthPan): Uint8ClampedArray[] {
  let images = renders.get(p);
  if (!images) { images = p.frames.map(f => f.render()); renders.set(p, images); }
  return images;
}

/** The pan as the page receives it: frames and readings merged by delivery time, as replay.ts merges them. */
function timelineOf(p: SynthPan): Observation[] {
  const frames: FrameObservation[] = p.frames.map(f => ({
    kind: 'frame', frame_id: f.frameId, t_capture_ms: f.tCaptureMs, t_present_ms: f.tPresentMs, width: p.k0.w, height: p.k0.h, file: '',
  }));
  return mergeObservations([...frames, ...(p.observations as unknown as Observation[])]);
}

const NO_STATS: Stats = { n: 0, p50: null, p95: null, max: null };
const FAKE_SETTINGS: CameraSettingsReport = {
  width: 720, height: 1280, frameRate: 30, resizeMode: 'none', zoom: null, focusMode: null, focusDistance: null,
  facingMode: 'environment', label: 'stub camera',
};

/** One picture per frame number: red and green carry the number, so a recorded frame says which frame it is. */
function patternOf(frameNo: number): Uint8ClampedArray {
  const px = new Uint8ClampedArray(180 * 320 * 4);
  for (let i = 0, p = 0; i < px.length; i += 4, p++) {
    px[i] = frameNo & 255; px[i + 1] = (frameNo >> 8) & 255; px[i + 2] = (p * 37) % 251; px[i + 3] = 255;
  }
  return px;
}

/** A CameraSourceLike whose readback time, playing state and frames the test sets. Every readback is logged. */
class StubCamera implements CameraSourceLike {
  readbackMs = 1;
  playing = true;
  lastFrameAt: number | null = null;
  closes = 0;
  readonly reads: number[] = [];
  /** How many of the next readbacks fail (return null), as a lost canvas would. */
  failReads = 0;
  private cb: ((meta: FrameMeta) => void) | null = null;
  private frameNo = 0;
  readonly settings = FAKE_SETTINGS;
  readonly bench = null;
  readonly farbled = null;
  readonly exposureReadable = null;
  readonly slips = { pairs: 0, slips: 0 };
  readonly lag = { nowMinusCapture: NO_STATS, presentMinusCapture: NO_STATS, expectedMinusNow: NO_STATS };
  readonly choices: readonly { deviceId: string; label: string }[] = [];
  readonly activeId = undefined;
  readonly analysisW = 180;
  readonly analysisH = 320;
  async open(): Promise<void> { }
  onFrame(cb: ((meta: FrameMeta) => void) | null): void { this.cb = cb; }
  readback(meta: FrameMeta): AnalysisFrame | null {
    this.reads.push(meta.frameNo);
    if (this.failReads > 0) { this.failReads--; return null; }
    return { frameId: meta.frameNo, t: meta.t, w: 180, h: 320, rgba: patternOf(meta.frameNo), readbackMs: this.readbackMs };
  }
  readbackTiny(): Uint8Array | null { return null; }
  refreshLiveSource(): void { }
  liveSource(): HTMLCanvasElement | null { return null; }
  close(): void { this.closes++; }
  /** Deliver a frame as CameraSource would; false when nothing is registered. */
  deliver(now: number, md: { captureTime: number | null; presentationTime: number; presentedFrames: number }): boolean {
    if (!this.cb) return false;
    this.lastFrameAt = now;
    this.cb({
      t: frameTime({ captureTime: md.captureTime ?? undefined, presentationTime: md.presentationTime }, now),
      frameNo: ++this.frameNo, captureTime: md.captureTime, presentationTime: md.presentationTime,
      expectedDisplayTime: md.presentationTime, mediaTime: (md.captureTime ?? md.presentationTime) / 1000,
      width: 180, height: 320, presentedFrames: md.presentedFrames, viaRvfc: true,
    });
    return true;
  }
}

interface Run {
  scanner: PanoramaScanner; harness: ReplayHarness;
  /** Everything delivered after begin(), in order; a frame with the number it was delivered as. */
  after: { item: Observation; frameNo: number | null }[];
  beganAt: number;
  /** Frame number to the frame's capture time. */
  captureAt: Map<number, number | null>;
}

/** Start a scanner in a fresh harness, play `p` into it, and begin() at the first delivery where it can, as the replay
 *  does. The caller finishes it and calls `end`. With `offset`, the camera opens at that clock time and every time in
 *  the pan is played that much later; `after`, `beganAt` and `captureAt` stay in the pan's own times. */
async function play(p: SynthPan, o: {
  camera?: StubCamera; encoder?: FrameEncoder; recording?: boolean; declination?: number | null; offset?: number;
  /** Called after each frame the scanner has seen once the scan has begun, with the frame's time in the pan's own clock. */
  onFrame?: (scanner: PanoramaScanner, at: number) => void;
} = {}): Promise<Run> {
  const harness = createHarness({ videoWidth: p.k0.w, videoHeight: p.k0.h });
  const scanner = new PanoramaScanner({ camera: o.camera, encoder: o.encoder });
  const off = o.offset ?? 0;
  const later = (t: number | null) => (t === null ? null : t + off);
  try {
    if (o.declination !== undefined && o.declination !== null) {
      const d = o.declination;
      scanner.setDeclination(az => az + d);
    }
    scanner.setRecording(o.recording ?? false);
    harness.setClock(off);
    await scanner.start(harness.video);
    const images = o.camera ? null : imagesOf(p);
    const index = new Map(p.frames.map((f, i) => [f.frameId, i]));
    const after: Run['after'] = [];
    const captureAt = new Map<number, number | null>();
    let begun = false, beganAt = NaN, frames = 0;
    for (const item of timelineOf(p)) {
      const at = item.kind === 'frame' ? item.t_present_ms : item.t_receive_ms;
      harness.setClock(at + off);
      let frameNo: number | null = null;
      if (item.kind === 'motion') {
        harness.dispatchMotion({ timeStamp: item.t_event_ms + off, rate: item.rate });
      } else if (item.kind === 'orientation') {
        harness.dispatchOrientation({
          timeStamp: item.t_event_ms + off, alpha: item.alpha, beta: item.beta, gamma: item.gamma, absolute: item.absolute ?? null, event: item.event,
        });
      } else if (item.kind === 'frame') {
        frameNo = ++frames;
        captureAt.set(frameNo, item.t_capture_ms);
        const md = { captureTime: later(item.t_capture_ms), presentationTime: item.t_present_ms + off, presentedFrames: frames };
        if (o.camera) {
          assert.ok(o.camera.deliver(at + off, md), 'the scanner listens to the stub camera');
        } else {
          harness.setFrame({ width: item.width, height: item.height, pixels: images![index.get(item.frame_id)!] }, (item.t_capture_ms ?? item.t_present_ms) + off);
          assert.ok(harness.deliverFrame({
            ...md, mediaTime: ((item.t_capture_ms ?? item.t_present_ms) + off) / 1000, expectedDisplayTime: item.t_present_ms + off,
            width: item.width, height: item.height,
          }), 'the camera took the frame');
        }
      }
      if (begun) { after.push({ item, frameNo }); if (item.kind === 'frame') o.onFrame?.(scanner, at); }
      else if (scanner.canBegin) { scanner.begin(); begun = true; beganAt = at; }
    }
    assert.ok(begun, 'the scan began');
    return { scanner, harness, after, beganAt, captureAt };
  } catch (e) {
    scanner.stop();
    harness.dispose();
    throw e;
  }
}

function end(r: Run): void {
  r.scanner.stop();
  r.harness.dispose();
}

/** The numbers and the keys of a parsed JSON value, at every depth. */
function walk(value: unknown, numbers: number[], keys: string[]): void {
  if (typeof value === 'number') numbers.push(value);
  else if (Array.isArray(value)) for (const v of value) walk(v, numbers, keys);
  else if (value !== null && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) { keys.push(k); walk(v, numbers, keys); }
  }
}

const isStats = (s: unknown) => s !== null && typeof s === 'object' && Object.keys(s as object).join() === 'n,p50,p95,max'
  && typeof (s as Stats).n === 'number';

/** Intersection over union of two masks of the raster's size. */
function iou(a: Uint8Array, b: Uint8Array): number {
  let both = 0, either = 0;
  for (let i = 0; i < a.length; i++) { if (a[i] && b[i]) both++; if (a[i] || b[i]) either++; }
  return either ? both / either : 1;
}

// ---- 1. The replay through --scanner pano (7.4, 13.7) --------------------------------------

const replayDir = mkdtempSync(join(tmpdir(), 'pano-v0-replay-'));
writeReplayInput(replayDir, pan);
writeFileSync(join(replayDir, 'input', 'scanner.json'), JSON.stringify({ declination_deg: DECLINATION }), 'utf8');
const replaySummary = await replayCase(replayDir, { scanner: 'pano' });
const resultFile = (name: string) => readFileSync(join(replayDir, 'result', name));
const replayed = {
  panorama: decodePng(resultFile('panorama.png')),
  horizon: JSON.parse(resultFile('horizon.json').toString('utf8')) as Row,
  events: lines(resultFile('events.jsonl').toString('utf8')),
  captures: lines(resultFile('captures.jsonl').toString('utf8')),
  summary: JSON.parse(resultFile('summary.json').toString('utf8')) as Row,
  firstSeen: resultFile('first_seen.bin'),
  diagnostics: JSON.parse(resultFile('diagnostics.json').toString('utf8')) as Row,
  live: lines(resultFile('live.jsonl').toString('utf8')),
};
rmSync(replayDir, { recursive: true, force: true });
/** The same pan through the control (`sensor_only: true`, 7.7): no alignment, so the focal stays the prior and every keyframe is
 *  placed by its prediction, in the live view and at Finish alike. The S30 case grades first_seen.bin against its panorama. */
const controlDir = mkdtempSync(join(tmpdir(), 'pano-v0-control-'));
writeReplayInput(controlDir, pan);
writeFileSync(join(controlDir, 'input', 'scanner.json'), JSON.stringify({ declination_deg: DECLINATION, sensor_only: true }), 'utf8');
await replayCase(controlDir, { scanner: 'pano' });
const controlFile = (name: string) => readFileSync(join(controlDir, 'result', name));
const control = {
  panorama: decodePng(controlFile('panorama.png')),
  firstSeen: controlFile('first_seen.bin'),
  diagnostics: JSON.parse(controlFile('diagnostics.json').toString('utf8')) as Row,
};
rmSync(controlDir, { recursive: true, force: true });
const frameIds = new Set(pan.frames.map(f => f.frameId));
/** S32 at the start of this pan. The phone is still for its first 0.5 s, its change-driven relative stream is silent and, the axis
 *  mapping being unconfirmed, no quiet gyro vouches for it (S41), so the pose falls to the compass and the scan's first keyframe is
 *  taken from it; when the pan starts the relative stream returns, that keyframe is withdrawn with its capture record and the
 *  first keyframe is taken again (the tracker's S32 re-anchor). The keyframe event and the live snapshot the replay wrote at the
 *  first commit stay in events.jsonl and live.jsonl: `firstKeptAt` is the event of the first keyframe that survived (its capture is
 *  the first accepted one, read and committed on one frame) and `withdrawn` the keyframe events before it. */
const firstKeptAt = replayed.events.findIndex(e => e.frame_id === replayed.captures.find(c => c.outcome === 'accepted')?.frame_id);
const withdrawn = replayed.events.slice(0, firstKeptAt).filter(e => e.keyframe).length;

await test('replay --scanner pano: panorama.png and horizon.json (v2) follow 13.7', () => {
  const { panorama, horizon } = replayed;
  assert.deepEqual([panorama.width, panorama.height], [PANO_W, PANO_H]);
  let painted = 0;
  for (let i = 3; i < panorama.pixels.length; i += 4) {
    assert.ok(panorama.pixels[i] === 0 || panorama.pixels[i] === 255, 'alpha is 0 or 255');
    if (panorama.pixels[i]) painted++;
  }
  assert.ok(painted > 10000, `the pan painted the raster (${painted} pixels)`);
  assert.deepEqual(Object.keys(horizon),
    ['version', 'interpolation', 'profile_bins', 'profile', 'profile_traced', 'profile_state', 'points', 'tau', 'bins', 'uncertain_bins']);
  assert.equal(horizon.version, 2); assert.equal(horizon.interpolation, 'linear-wrap');
  assert.equal(horizon.profile_bins, 720); assert.equal(horizon.bins, 720);
  const profile = horizon.profile as number[], traced = horizon.profile_traced as (number | null)[], state = horizon.profile_state as number[];
  assert.equal(profile.length, 720); assert.equal(traced.length, 720); assert.equal(state.length, 720);
  const states = new Set<number>(Object.values(BinState));
  for (let i = 0; i < 720; i++) {
    assert.ok(states.has(state[i]), `bin ${i}: state ${state[i]}`);
    assert.ok(traced[i] === null || Number.isFinite(traced[i]), `bin ${i}: traced`);
    const publishes = state[i] === BinState.Measured || state[i] === BinState.Edited || state[i] === BinState.Kept;
    if (!publishes) assert.equal(profile[i], 90, `bin ${i} (state ${state[i]}) publishes 90`);
  }
  const uncertain = [...state.keys()].filter(i => state[i] === BinState.Low || state[i] === BinState.Unknown || state[i] === BinState.Tall);
  assert.deepEqual(horizon.uncertain_bins, uncertain);
  const points = horizon.points as { az: number; alt: number }[];
  assert.ok(points.length >= 2 && points.length <= 180, `${points.length} points`);
  for (let i = 0; i < points.length; i++) {
    assert.deepEqual(Object.keys(points[i]), ['az', 'alt']);
    assert.ok(points[i].az >= 0 && points[i].az < 360 && points[i].alt >= -10 && points[i].alt <= 90);
    if (i) assert.ok(points[i].az > points[i - 1].az, 'azimuths ascend');
  }
  assert.equal(typeof horizon.tau, 'number');
});

await test('replay --scanner pano: events.jsonl and captures.jsonl follow 13.7, with the case\'s frame ids', () => {
  const { events, captures } = replayed;
  assert.equal(events.length, pan.frames.length);
  events.forEach((e, k) => {
    assert.deepEqual(Object.keys(e), ['t_ms', 'frame_id', 'compass_ready', 'tilt_ready', 'aim', 'basis', 'frame_count', 'cue', 'keyframe', 'cls']);
    assert.equal(e.frame_id, pan.frames[k].frameId);
    assert.equal(e.frame_count, k + 1);
    assert.equal(typeof e.cue, 'string');
    assert.equal(typeof e.keyframe, 'boolean');
    assert.ok(e.keyframe ? ['aligned', 'blurred', 'sensor'].includes(e.cls as string) : e.cls === null, `cls ${String(e.cls)}`);
  });
  // A live pose whenever the relative stream is speaking. This steady pan never confirms its axis mapping (the fit stays
  // at 0.2 to 0.4), so a gyro sample is no rate and a pause is held only on a chord that reads quiet (S33, S41): the
  // holds either side of the pan may have no pose, the pan itself may not.
  const relativeAt = pan.observations
    .filter(o => o.kind === 'orientation' && o.event === 'deviceorientation' && !o.absolute).map(o => o.t_receive_ms as number);
  const speaking = events.map((_, k) => {
    const t = pan.frames[k].tCaptureMs ?? pan.frames[k].tPresentMs;
    return relativeAt.some(r => r <= t && t - r <= STALE_LIMIT_MIN_MS);
  });
  assert.ok(speaking.filter(Boolean).length > 100, `the relative stream speaks at ${speaking.filter(Boolean).length} of ${events.length} frames`);
  assert.deepEqual(events.map((e, k) => (k >= 5 && speaking[k] && e.basis === null ? k : -1)).filter(k => k >= 0), [],
    'a live pose at every frame after the fifth where the relative stream is speaking');
  const allowed = ['at', 'frame_id', 'outcome', 'detail', 'kf', 'step_deg', 'rate_deg_s', 'psr', 'zncc', 'innovation_deg', 'w_yaw', 'extrapolated_ms'];
  const outcomes = ['accepted', 'revisit', 'waiting-sharper', 'inactive', 'stale-pose', 'read-failed', 'cap'];
  let accepted = 0;
  for (const c of captures) {
    const keys = Object.keys(c);
    assert.deepEqual(keys.slice(0, 3), ['at', 'frame_id', 'outcome']);
    for (const k of keys) assert.ok(allowed.includes(k), `captures.jsonl key ${k}`);
    assert.deepEqual(keys, allowed.filter(k => keys.includes(k)), 'the 13.7 order');
    assert.ok(frameIds.has(c.frame_id as string), `frame_id ${String(c.frame_id)} is the case's`);
    assert.ok(outcomes.includes(c.outcome as string));
    for (const k of keys.filter(k => k !== 'frame_id' && k !== 'outcome' && k !== 'detail')) assert.ok(Number.isFinite(c[k]), `${k} is a number`);
    if (c.outcome === 'accepted') { assert.equal(c.kf, accepted); accepted++; }
  }
  assert.ok(accepted >= 15, `${accepted} keyframes over 100 degrees`);
  assert.ok(firstKeptAt >= 0, 'the first accepted capture names a frame of the pan');
  assert.equal(events.slice(firstKeptAt).filter(e => e.keyframe).length, accepted, 'a keyframe event per accepted capture');
  assert.ok(withdrawn <= 1, `${withdrawn} keyframe events before the first that survived: only the first keyframe is ever withdrawn (S32)`);
});

await test('replay --scanner pano: summary.json, first_seen.bin, diagnostics.json and live.jsonl follow 13.7', () => {
  const { summary, firstSeen, diagnostics: d, live, captures } = replayed;
  assert.equal(summary.cells_total, null); assert.equal(summary.cells_covered, null);
  assert.equal(summary.frames_delivered, pan.frames.length);
  assert.equal(summary.frames_accepted, captures.filter(c => c.outcome === 'accepted').length);
  assert.equal(replaySummary.frames_accepted, summary.frames_accepted);
  const cost = summary.cost_ms as Row;
  assert.deepEqual(Object.keys(cost), ['callback', 'callback_ref', 'ref_unit_ms']);
  assert.ok(isStats(cost.callback) && isStats(cost.callback_ref) && (cost.ref_unit_ms as number) > 0);
  assert.equal(firstSeen.length, 2 * PANO_W * PANO_H, 'little-endian Uint16, 1080 x 300');

  assert.deepEqual(Object.keys(d), [
    'version', 'scanner', 'sensor_only', 'begin_ms', 'finish_ms', 'predictor_mode', 'mode_changes', 'axis_mapping',
    'tau_ms', 'tau_sigma_ms', 'tau_pairs', 'tau_applied', 'focal', 'loop', 'north', 'declination_applied', 'keyframes',
    'keyframe_ms', 'readback_ms', 'stale_refusals', 'extractor',
  ]);
  assert.equal(d.version, 1); assert.equal(d.scanner, 'pano'); assert.equal(d.sensor_only, false); assert.equal(d.extractor, 'tracer');
  assert.equal(d.predictor_mode, 'relative');
  assert.ok(typeof d.begin_ms === 'number' && typeof d.finish_ms === 'number' && (d.finish_ms as number) > (d.begin_ms as number));
  assert.equal(d.finish_ms, FINISH_AT, 'ms since the camera opened, which on a replay is the case clock');
  for (const c of d.mode_changes as Row[]) assert.deepEqual(Object.keys(c), ['t_ms', 'from', 'to']);
  assert.deepEqual(Object.keys(d.focal as Row), ['state', 'f_norm', 'sd_pct', 'ratios', 'short_fov_deg']);
  assert.deepEqual(Object.keys(d.loop as Row), ['closed', 'method', 'pre_deg', 'post_deg', 'match', 'unwrapped_deg']);
  assert.deepEqual(Object.keys(d.north as Row), ['offset_deg', 'sigma_deg', 'spread_deg', 'samples', 'n_eff', 'stable', 'source']);
  assert.equal(d.declination_applied, true, 'scanner.json set a declination and the compass gave a north');
  assert.ok(isStats(d.keyframe_ms) && isStats(d.readback_ms) && typeof d.stale_refusals === 'number');
  const kfs = d.keyframes as Row[];
  assert.equal(kfs.length, summary.frames_accepted);
  kfs.forEach((k, i) => {
    assert.deepEqual(Object.keys(k), ['id', 'frame_id', 't_ms', 'q', 'cls', 'sigma_deg']);
    assert.equal(k.id, i);
    assert.ok(frameIds.has(k.frame_id as string));
    const q = k.q as number[];
    assert.equal(q.length, 4);
    assert.ok(Math.abs(Math.hypot(...q) - 1) < 1e-9, 'a unit quaternion');
  });
  // The keyframe's frame is the one its accepted capture names, and its time that frame's capture time.
  const accepted = captures.filter(c => c.outcome === 'accepted');
  const capture = new Map(pan.frames.map(f => [f.frameId, f.tCaptureMs]));
  kfs.forEach((k, i) => {
    assert.equal(k.frame_id, accepted[i].frame_id);
    assert.equal(k.t_ms, capture.get(k.frame_id as string));
  });
  // One live snapshot per keyframe that survived, after the snapshot a withdrawn first commit left (keyframe 0, S32).
  assert.equal(live.length, withdrawn + kfs.length, 'one live snapshot per keyframe');
  live.slice(0, withdrawn).forEach(s => assert.equal(s.kf, 0, 'the withdrawn commit was keyframe 0'));
  const current = live.slice(withdrawn);
  current.forEach((s, i) => {
    assert.deepEqual(Object.keys(s), ['t_ms', 'kf', 'painted_fraction', 'closure_state', 'focal_state']);
    assert.equal(s.kf, i);
    assert.equal(s.closure_state, 'open');
    assert.ok((s.painted_fraction as number) > 0 && (s.painted_fraction as number) <= 1);
    if (i) assert.ok((s.painted_fraction as number) >= (current[i - 1].painted_fraction as number), 'the painted fraction grows');
  });
});

await test('first_seen.bin lines up with panorama.png under a non-zero world yaw (S30)', () => {
  // Graded on two replays of the pan. In the control the two masks overlap by 0.9994 and a single column of error costs 0.005
  // (0.9928-0.9940), so the floor reads one column. With alignment on the live paints before the focal locks were made at the prior
  // focal and the Finish render at the locked one, and first_seen.bin is never reset by clear() (S30): the vertical edge of each
  // slice moves a row, about 0.6 % of the union and nearly all of it on the one row at each edge of the band, so the masks overlap
  // by 0.994 and the floor reads a world yaw that is three columns out (0.02), not one.
  for (const [what, r, floor] of [['control', control, 0.995], ['aligned', replayed, 0.99]] as const) {
    const { panorama, firstSeen, diagnostics: d } = r;
    assert.equal(d.sensor_only, what === 'control', `${what}: the replay is the one asked for`);
    const north = d.north as { offset_deg: number };
    // The world yaw Finish composed: the magnetic north offset plus the declination scanner.json gave, in raster columns.
    const shift = ((Math.round((north.offset_deg + DECLINATION) * 3) % PANO_W) + PANO_W) % PANO_W;
    assert.ok(Math.min(shift, PANO_W - shift) >= 15, `${what}: the world yaw is ${shift} columns: big enough to tell the frames apart`);
    const painted = new Uint8Array(PANO_W * PANO_H), seen = new Uint8Array(PANO_W * PANO_H), unshifted = new Uint8Array(PANO_W * PANO_H);
    const begin = d.begin_ms as number;
    let latest = 0;
    for (let i = 0; i < painted.length; i++) {
      painted[i] = panorama.pixels[i * 4 + 3] ? 1 : 0;
      const v = firstSeen.readUInt16LE(i * 2);
      seen[i] = v > 0 ? 1 : 0;
      latest = Math.max(latest, v);
      // The same snapshot moved back by the yaw: what an exporter that forgot the shift would have written.
      const y = Math.floor(i / PANO_W), x = i % PANO_W;
      unshifted[y * PANO_W + ((x - shift + PANO_W) % PANO_W)] = seen[i];
    }
    // Deciseconds since Begin, so begin_ms + first_seen x 100 lands inside the scan.
    assert.ok(latest > 0 && begin + latest * 100 <= FINISH_AT, `${what}: latest stamp ${latest}`);
    const aligned = iou(painted, seen), apart = iou(painted, unshifted);
    assert.ok(aligned >= floor, `${what}: first_seen.bin and panorama.png overlap by ${aligned.toFixed(4)}`);
    assert.ok(apart < 0.9, `${what}: the unshifted snapshot would overlap by ${apart.toFixed(4)}, so the case tells them apart`);
  }
});

// ---- 2. canBegin, blocked, stop ------------------------------------------------------------------

await test('canBegin is true on a still absolute-only input (RS M4)', async () => {
  const harness = createHarness({ videoWidth: 45, videoHeight: 80 });
  const scanner = new PanoramaScanner();
  try {
    await scanner.start(harness.video);
    assert.equal(scanner.canBegin, false, 'no orientation sample yet');
    assert.equal(scanner.status.phase, 'ready');
    assert.equal(scanner.status.cueKey, 'ready');
    // Chromium with no relative sensor: each reading twice, as deviceorientationabsolute and as a deviceorientation that
    // says it is absolute. Held still, a change-driven stream sends this one reading and nothing more; no gyro.
    harness.setClock(40);
    harness.dispatchOrientation({ timeStamp: 35, alpha: 101.4, beta: 67, gamma: -1.2, absolute: true, event: 'deviceorientationabsolute' });
    harness.dispatchOrientation({ timeStamp: 35, alpha: 101.4, beta: 67, gamma: -1.2, absolute: true, event: 'deviceorientation' });
    assert.equal(scanner.canBegin, true);
    assert.equal(scanner.status.canBegin, true, 'the status says so before any frame arrives');
    assert.equal(scanner.inspect().track.mode, 'absolute-only');
    scanner.begin();
    assert.equal(scanner.status.phase, 'scanning');
    assert.equal(scanner.inspect().beginMs, 40);
  } finally {
    scanner.stop();
    harness.dispose();
  }
});

await test('a blocked phone that sends nothing reads blocked through tick (S11)', async () => {
  const harness = createHarness({ videoWidth: 45, videoHeight: 80 });
  const scanner = new PanoramaScanner();
  try {
    await scanner.start(harness.video);
    // Brave's blocked shape: one deviceorientation with every angle null, and then silence.
    harness.setClock(100);
    harness.dispatchOrientation({ timeStamp: 100, alpha: null, beta: null, gamma: null, absolute: null, event: 'deviceorientation' });
    harness.setClock(600);
    scanner.tick(600);
    assert.equal(scanner.status.error, null, 'not yet: blocked needs a second of nothing');
    harness.setClock(1150);
    scanner.tick(1150);
    assert.equal(scanner.status.error, ERROR_TEXT.motionBlocked);
    assert.equal(scanner.status.cueKey, 'error');
    assert.equal(scanner.status.cue, ERROR_TEXT.motionBlocked);
    assert.equal(scanner.canBegin, false);
    assert.equal(scanner.report().sensors.blocked, true);
  } finally {
    scanner.stop();
    harness.dispose();
  }
});

await test('a gyroscope permission query that answers denied is the blocked error too, and the report says blocked (2.11)', async () => {
  const harness = createHarness({ videoWidth: 45, videoHeight: 80 });
  Object.defineProperty(navigator, 'permissions', { configurable: true, value: { query: async () => ({ state: 'denied' }) } });
  const scanner = new PanoramaScanner({ camera: new StubCamera() });
  try {
    await scanner.start(harness.video);
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(scanner.status.error, ERROR_TEXT.motionBlocked);
    assert.equal(scanner.report().sensors.blocked, true);
    assert.equal(scanner.inspect().track.blocked, false, 'the pose track itself saw nothing blocked');
  } finally {
    scanner.stop();
    harness.dispose();
  }
});

await test('stop() removes every listener it added, and releases the camera', async () => {
  const harness = createHarness({ videoWidth: 45, videoHeight: 80 });
  const camera = new StubCamera();
  const scanner = new PanoramaScanner({ camera });
  const added: [EventTarget, string, unknown][] = [], removed: [EventTarget, string, unknown][] = [];
  for (const target of [window, document, window.screen.orientation] as EventTarget[]) {
    const add = target.addEventListener.bind(target), remove = target.removeEventListener.bind(target);
    Object.defineProperty(target, 'addEventListener', {
      configurable: true, value: (type: string, fn: EventListener) => { added.push([target, type, fn]); add(type, fn); },
    });
    Object.defineProperty(target, 'removeEventListener', {
      configurable: true, value: (type: string, fn: EventListener) => { removed.push([target, type, fn]); remove(type, fn); },
    });
  }
  try {
    await scanner.start(harness.video);
    assert.deepEqual(added.map(a => a[1]).sort(),
      ['change', 'devicemotion', 'deviceorientation', 'deviceorientationabsolute', 'visibilitychange']);
    const relative = () => scanner.inspect().track.facts().events.find(e => e.type === 'deviceorientation')!.total;
    const motion = () => scanner.inspect().track.facts().events.find(e => e.type === 'devicemotion')!.total;
    harness.setClock(20);
    harness.dispatchOrientation({ timeStamp: 20, alpha: 10, beta: 70, gamma: 0, absolute: false, event: 'deviceorientation' });
    harness.dispatchMotion({ timeStamp: 20, rate: { alpha: 0, beta: 5, gamma: 1 } });
    assert.equal(relative(), 1);
    assert.equal(motion(), 1);

    scanner.stop();
    for (const [target, type, fn] of added) {
      assert.ok(removed.some(r => r[0] === target && r[1] === type && r[2] === fn), `the ${type} listener is removed`);
    }
    harness.setClock(40);
    harness.dispatchOrientation({ timeStamp: 40, alpha: 12, beta: 70, gamma: 0, absolute: false, event: 'deviceorientation' });
    harness.dispatchMotion({ timeStamp: 40, rate: { alpha: 0, beta: 5, gamma: 1 } });
    harness.setScreenAngle(90);
    document.dispatchEvent(new window.Event('visibilitychange'));
    assert.equal(relative(), 1, 'a reading after stop() does not reach the pose track');
    assert.equal(motion(), 1);
    assert.equal(camera.deliver(60, { captureTime: 10, presentationTime: 60, presentedFrames: 1 }), false, 'the frame callback is detached');
    assert.ok(camera.closes >= 1, 'the camera is closed');
    scanner.stop();
    assert.equal(removed.length, added.length, 'a second stop() removes nothing twice');
  } finally {
    scanner.stop();
    harness.dispose();
  }
});

await test('two overlapping start() calls (a camera switch while one opens) leave one set of listeners', async () => {
  const harness = createHarness({ videoWidth: 45, videoHeight: 80 });
  const camera = new StubCamera();
  const gates: (() => void)[] = [];
  camera.open = () => new Promise<void>(resolve => { gates.push(resolve); });
  const scanner = new PanoramaScanner({ camera });
  const attached = new Map<string, number>();
  const add = window.addEventListener.bind(window), remove = window.removeEventListener.bind(window);
  Object.defineProperty(window, 'addEventListener', {
    configurable: true, value: (type: string, fn: EventListener) => { attached.set(type, (attached.get(type) ?? 0) + 1); add(type, fn); },
  });
  Object.defineProperty(window, 'removeEventListener', {
    configurable: true, value: (type: string, fn: EventListener) => { attached.set(type, (attached.get(type) ?? 0) - 1); remove(type, fn); },
  });
  try {
    const first = scanner.start(harness.video), second = scanner.start(harness.video, 'other');
    for (const open of gates) open();
    await Promise.all([first, second]);
    assert.equal(scanner.status.phase, 'ready');
    assert.deepEqual([...attached.entries()].sort(), [['devicemotion', 1], ['deviceorientation', 1], ['deviceorientationabsolute', 1]]);
    harness.setClock(20);
    harness.dispatchOrientation({ timeStamp: 20, alpha: 10, beta: 70, gamma: 0, absolute: false, event: 'deviceorientation' });
    assert.equal(scanner.inspect().track.facts().events.find(e => e.type === 'deviceorientation')!.total, 1, 'a reading is seen once');
  } finally {
    scanner.stop();
    harness.dispose();
  }
});

// ---- 3. A recorded scan, with and without a declination ---------------------------------------

interface Recorded {
  result: ScanResult; report: ScanReport; recording: Row[]; keyframes: readonly Keyframe[];
  log: readonly { at: number; frameId: number; outcome: string; kf?: number }[];
  after: Run['after']; beganAt: number; meshSrcMax: [number, number]; meshCount: number;
}

/** The middle of the pan, where the relative stream speaks; the end hold may have no pose (S41). */
const MID_PAN_MS = pan.frames[pan.frames.length >> 1].tPresentMs;

async function recordedScan(declination: number | null, offset = 0): Promise<Recorded> {
  // The live mesh is read at the first frame at or after the middle of the pan.
  const meshSrcMax: [number, number] = [0, 0];
  let meshCount = 0, meshRead = false;
  const readMesh = (scanner: PanoramaScanner, at: number) => {
    if (meshRead || at < MID_PAN_MS) return;
    meshRead = true;
    const live = scanner.status;
    const tris = scanner.ribbon.liveMesh({ centreAz: live.headingDeg ?? 0, altTop: 60, altBottom: -10, pxPerDeg: 2.6, widthPx: 390 }) ?? [];
    meshCount = tris.length;
    for (const t of tris) for (let i = 0; i < 6; i += 2) {
      meshSrcMax[0] = Math.max(meshSrcMax[0], t.src[i]); meshSrcMax[1] = Math.max(meshSrcMax[1], t.src[i + 1]);
    }
  };
  const r = await play(pan, { recording: true, encoder: pngEncoder, declination, offset, onFrame: readMesh });
  try {
    assert.ok(meshRead, 'the pan reached its middle');
    r.harness.setClock(FINISH_AT + offset);
    const result = r.scanner.finish(PREVIOUS, 'user');
    const report = r.scanner.report();
    const text = r.scanner.recording();
    assert.ok(text !== null, 'recording was on at begin()');
    const ins = r.scanner.inspect();
    return {
      result, report, recording: lines(text), keyframes: ins.tracker.keyframes, log: ins.tracker.log.map(x => ({ ...x })),
      after: r.after, beganAt: r.beganAt, meshSrcMax, meshCount,
    };
  } finally {
    end(r);
  }
}

const withDecl = await recordedScan(DECLINATION);
const withoutDecl = await recordedScan(null);
/** The same scan with the camera opened 1000 ms into the page's clock. */
const SHIFT_MS = 1000;
const shifted = await recordedScan(null, SHIFT_MS);

await test('the recording holds every frame and event in order, and pngEncoder frames decode to the readback', () => {
  const rec = withDecl.recording;
  assert.deepEqual(rec[0], {
    kind: 'header', format: 'astrodeck-pano-recording', version: 1, encoder: 'image/png',
    video: { width: 45, height: 80 }, analysis: { width: 180, height: 320 },
    settings: rec[0].settings, commit: null,
  });
  assert.deepEqual(rec[1], { kind: 'action', t_ms: withDecl.beganAt, action: 'begin' }, 'times are ms since the camera opened');
  // What the scanner was handed after begin(), line for line. The camera opened at the harness clock's zero.
  const expected: Row[] = withDecl.after.map(({ item, frameNo }) => {
    if (item.kind === 'orientation') {
      return { kind: 'orientation', event: item.event, t_event_ms: item.t_event_ms, t_receive_ms: item.t_receive_ms,
        alpha: item.alpha, beta: item.beta, gamma: item.gamma, absolute: item.absolute };
    }
    if (item.kind === 'motion') return { kind: 'motion', t_event_ms: item.t_event_ms, t_receive_ms: item.t_receive_ms, rate: item.rate };
    if (item.kind === 'frame') {
      return { kind: 'frame', frame_id: fid(frameNo!), t_capture_ms: item.t_capture_ms, t_present_ms: item.t_present_ms,
        width: 180, height: 320, mime: 'image/png' };
    }
    throw new Error(`no ${item.kind} in a synthetic pan`);
  });
  const body = rec.slice(2, rec.length - 2);
  assert.equal(body.length, expected.length, `${body.length} recorded lines for ${expected.length} deliveries`);
  const images = imagesOf(pan);
  let frames = 0, motions = 0;
  body.forEach((line, i) => {
    if (line.kind !== 'frame') {
      if (line.kind === 'motion') motions++;
      assert.deepEqual(line, expected[i], `line ${i + 2}`);
      return;
    }
    const { data, ...rest } = line;
    assert.deepEqual(rest, expected[i], `line ${i + 2}`);
    // The analysis frame the camera read back: the 45 x 80 frame scaled to 180 x 320 by the harness's camera.
    const decoded = decodePng(Buffer.from(data as string, 'base64'));
    const frameNo = Number((line.frame_id as string).slice(1));
    const want = resample(images[frameNo - 1], 45, 80, 180, 320);
    assert.deepEqual([decoded.width, decoded.height], [180, 320]);
    assert.ok(Buffer.from(decoded.pixels.buffer, decoded.pixels.byteOffset, decoded.pixels.length)
      .equals(Buffer.from(want.buffer, want.byteOffset, want.length)), `frame ${line.frame_id as string} is lossless`);
    frames++;
  });
  assert.equal(frames, pan.frames.length, 'every frame');
  assert.ok(motions > 300, `every motion event (${motions})`);
  assert.deepEqual(rec[rec.length - 2], { kind: 'action', t_ms: FINISH_AT, action: 'finish' });
  assert.equal(rec[rec.length - 1].kind, 'report');
  assert.deepEqual(rec[rec.length - 1].report, JSON.parse(JSON.stringify(withDecl.report)), 'the report line is the report');
  assert.deepEqual(withDecl.report.recording, { on: true, every: 1, frames: pan.frames.length });
});

await test('captureLog frame ids are the recording\'s f%06d numbers (S24)', () => {
  const frameAt = new Map<number, Row>();
  for (const line of withDecl.recording) if (line.kind === 'frame') frameAt.set(Number((line.frame_id as string).slice(1)), line);
  assert.deepEqual([...frameAt.keys()], pan.frames.map((_, i) => i + 1), 'f000001, f000002, ... by delivery');
  let checked = 0;
  for (const r of withDecl.log) {
    const line = frameAt.get(r.frameId);
    assert.ok(line, `record ${r.outcome} names frame ${r.frameId}, which the recording holds`);
    // A record is made on the frame it names, except an accepted one, whose frame is the candidate read earlier.
    const want = r.outcome === 'accepted' ? withDecl.keyframes[r.kf!].t : r.at;
    assert.equal(line.t_capture_ms, want, `record ${r.outcome} of frame ${r.frameId}`);
    checked++;
  }
  for (const kf of withDecl.keyframes) assert.equal(frameAt.get(kf.frameId)!.t_capture_ms, kf.t, `keyframe ${kf.id}`);
  assert.ok(checked > 20 && withDecl.keyframes.length >= 15, `${checked} records, ${withDecl.keyframes.length} keyframes`);
  // The report's capture log is the same log, its times in ms since the camera opened (here the clock's zero).
  assert.deepEqual(withDecl.report.captureLog.map(r => [r.frameId, r.at, r.outcome]), withDecl.log.map(r => [r.frameId, r.at, r.outcome]));
});

await test('privacy: with setDeclination(az => az + 12.3456) no number within 0.001 of it is in the report or the recording\'s header and report lines', () => {
  assert.equal(withDecl.report.declinationApplied, true, 'the declination was applied');
  assert.equal(withDecl.result.declinationApplied, true);
  const rec = withDecl.recording;
  for (const [what, value] of [['report', withDecl.report], ['header', rec[0]], ['report line', rec[rec.length - 1]]] as const) {
    const numbers: number[] = [], keys: string[] = [];
    walk(JSON.parse(JSON.stringify(value)), numbers, keys);
    const near = numbers.filter(n => Math.abs(n - DECLINATION) <= 0.001);
    assert.deepEqual(near, [], `${what}: a number within 0.001 of the declination`);
    const named = keys.filter(k => /declin/i.test(k));
    assert.ok(named.every(k => k === 'declinationApplied'), `${what}: keys naming a declination: ${named.join(', ')}`);
  }
  // Stronger than the numbers: the same scan without a declination reports exactly the same, apart from the horizon
  // (whose bins are counted in the true-north frame) and the flag itself. Nothing else depends on the closure.
  const strip = (r: ScanReport) => ({ ...JSON.parse(JSON.stringify(r)) as Row, horizon: null, declinationApplied: null });
  assert.equal(withoutDecl.report.declinationApplied, false);
  assert.deepEqual(strip(withDecl.report), strip(withoutDecl.report));
  assert.deepEqual(withDecl.recording.slice(0, -1), withoutDecl.recording.slice(0, -1), 'every line but the report');
});

await test('finish(previous) keeps the previous line where nothing was photographed, as Kept bins (O1, 5.5)', () => {
  const { draft, points } = withDecl.result;
  const kept = [...draft.state.keys()].filter(i => draft.state[i] === BinState.Kept);
  assert.ok(kept.length > 400, `${kept.length} Kept bins after a 100-degree pan`);
  for (const i of kept) {
    assert.equal(draft.alt[i], 12, `bin ${i} takes the previous line`);
    assert.equal(draft.reason[i], ColState.UnknownUnseen);
    assert.ok(Number.isNaN(draft.top[i]), `bin ${i} has no photographed column`);
  }
  assert.ok([...draft.state.keys()].every(i => Number.isNaN(draft.top[i]) || draft.state[i] !== BinState.Kept), 'a photographed bin is never Kept');
  assert.equal(withDecl.report.horizon!.kept, kept.length, 'the report counts state === Kept');
  const h = withDecl.report.horizon!;
  assert.equal(h.measured + h.low + h.unknown + h.tall + h.kept, 720);
  assert.equal(h.points, points.length);
  assert.equal(h.tau, withDecl.result.tau);
  assert.equal(withDecl.result.partial, true);
  assert.equal(withDecl.result.endedBy, 'user');
  // The same scan finished without a declination keeps the same previous line, to within the bins its footprint moves: the two
  // Finishes compose world yaws a fractional number of columns apart, and each of the two ends of the swept range falls into its
  // bin or the next (503 against 502 with alignment on; equal under v0's predictor-only poses).
  const keptWithout = withoutDecl.result.draft.state.filter(s => s === BinState.Kept).length;
  assert.ok(Math.abs(keptWithout - kept.length) <= 2, `${keptWithout} Kept bins without a declination, ${kept.length} with`);
});

await test('the live mesh is drawn from the live source at L1 intrinsics, 90 x 160 at 9:16', () => {
  assert.ok(withDecl.meshCount > 0, 'triangles once a pose exists');
  assert.ok(withDecl.meshSrcMax[0] <= 90 + 1e-9 && withDecl.meshSrcMax[1] <= 160 + 1e-9, `source corner ${withDecl.meshSrcMax.join(' x ')}`);
  assert.ok(withDecl.meshSrcMax[1] > 140, 'the mesh spans the live source vertically (0.9 of its height)');
});

await test('every time in the report and the recording is ms since the camera opened (3.2, S11)', () => {
  // The pan played 1000 ms later on the page's clock, with the camera opened 1000 ms later: what leaves the scanner
  // is the same to the byte. A time copied out raw (a capture record's `at`, a mode change's tMs, a recorded event)
  // would be 1000 larger here.
  assert.ok(shifted.report.sensors.modeChanges.length > 0, 'the pan has mode changes to convert');
  assert.ok(shifted.report.timeline.beginMs !== null && shifted.report.timeline.beginMs < SHIFT_MS);
  assert.deepEqual(shifted.report, withoutDecl.report);
  assert.deepEqual(shifted.recording, withoutDecl.recording);
});

// ---- 4. The recording when the readback is slow, and when the encoder fails (S24) --------------

await test('a decimated recording keeps its frame ids, and reads back only what it keeps or the tracker asks for (S24)', async () => {
  const camera = new StubCamera();
  camera.readbackMs = 10;   // over the 8 ms p95, so the recording halves after 30 frames (3.5 step 4)
  const r = await play(pan, { camera, recording: true, encoder: pngEncoder });
  try {
    r.harness.setClock(FINISH_AT);
    r.scanner.finish(null, 'user');
    const report = r.scanner.report();
    const rec = lines(r.scanner.recording()!);
    const recorded = rec.filter(l => l.kind === 'frame');
    const ids = recorded.map(l => Number((l.frame_id as string).slice(1)));
    assert.equal(report.recording.every, 2, 'the report records the change');
    assert.equal(report.recording.frames, recorded.length);
    assert.deepEqual(ids.slice(0, 30), Array.from({ length: 30 }, (_, i) => i + 1), 'all of the first 30');
    for (let i = 30; i < ids.length; i++) assert.equal(ids[i] - ids[i - 1], 2, `then every second frame (${ids[i - 1]} -> ${ids[i]})`);
    // Each kept frame is the frame its id names, and the id is the tracker's number for it.
    for (const line of recorded) {
      const n = Number((line.frame_id as string).slice(1));
      const px = decodePng(Buffer.from(line.data as string, 'base64')).pixels;
      assert.equal(px[0] | (px[1] << 8), n, `${line.frame_id as string} holds frame ${n}`);
      assert.equal(line.t_capture_ms, r.captureAt.get(n));
    }
    const ins = r.scanner.inspect();
    let keyframesRecorded = 0;
    for (const kf of ins.tracker.keyframes) {
      if (!ids.includes(kf.frameId)) continue;
      keyframesRecorded++;
      assert.equal(recorded[ids.indexOf(kf.frameId)].t_capture_ms, kf.t, `keyframe ${kf.id}`);
    }
    assert.ok(keyframesRecorded >= 5, `${keyframesRecorded} keyframes fall on kept frames`);
    // A frame is read back once at most, and only when the recorder keeps it or the tracker reads it.
    const trackerReads = new Set(ins.tracker.log.filter(x => x.outcome === 'accepted' || x.outcome === 'waiting-sharper').map(x => x.frameId));
    const wanted = new Set([...ids, ...trackerReads]);
    assert.equal(camera.reads.length, new Set(camera.reads).size, 'no frame read twice');
    assert.deepEqual([...camera.reads].sort((a, b) => a - b), [...wanted].sort((a, b) => a - b));
    assert.ok(camera.reads.length < pan.frames.length, `${camera.reads.length} readbacks for ${pan.frames.length} frames`);
  } finally {
    end(r);
  }
});

await test('an encoder exception stops recording frames, not the scan (S24)', async () => {
  const camera = new StubCamera();
  let calls = 0;
  const encoder: FrameEncoder = {
    mime: 'image/png',
    encode(rgba, w, h) {
      if (++calls === 3) throw new Error('codec gave up');
      return pngEncoder.encode(rgba, w, h);
    },
  };
  const r = await play(pan, { camera, recording: true, encoder });
  try {
    assert.equal(r.scanner.status.phase, 'scanning');
    assert.equal(r.scanner.status.recording, false, 'the status says the recording stopped');
    const ins = r.scanner.inspect();
    assert.ok(ins.tracker.keyframes.filter(k => k.frameId > 3).length >= 15, 'keyframes kept coming after the failure');
    r.harness.setClock(FINISH_AT);
    const result = r.scanner.finish(null, 'user');
    assert.equal(result.endedBy, 'user');
    assert.equal(result.error, null, 'a recording failure is not a scan failure');
    const report = r.scanner.report();
    assert.equal(report.error, 'recording: codec gave up');
    const rec = lines(r.scanner.recording()!);
    assert.deepEqual(rec.filter(l => l.kind === 'frame').map(l => l.frame_id), ['f000001', 'f000002']);
    const failedAt = r.captureAt.size ? pan.frames[2].tPresentMs : 0;
    assert.ok(rec.some(l => l.kind === 'motion' && (l.t_receive_ms as number) > failedAt + 1000), 'events are still recorded');
    assert.deepEqual(rec.slice(-2).map(l => l.kind), ['action', 'report']);
    // After the failure, only the tracker reads back. A readback the log does not name is the first commit that the S32 re-anchor
    // withdrew with its capture record (this pan starts with a pose from the compass): at most one, before the first keyframe kept.
    const trackerReads = new Set(ins.tracker.log.filter(x => x.outcome === 'accepted' || x.outcome === 'waiting-sharper').map(x => x.frameId));
    const unlogged = camera.reads.filter(n => n > 3 && !trackerReads.has(n));
    assert.ok(unlogged.length <= 1 && unlogged.every(n => n < ins.tracker.keyframes[0].frameId), `readbacks the tracker did not log: ${unlogged.join(', ')}`);
  } finally {
    end(r);
  }
});

await test('a camera that stops playing shows the track-ended error through tick, and a frame clears it', async () => {
  const harness = createHarness({ videoWidth: 45, videoHeight: 80 });
  const camera = new StubCamera();
  const scanner = new PanoramaScanner({ camera });
  try {
    await scanner.start(harness.video);
    camera.playing = false;
    scanner.tick(200);
    assert.equal(scanner.status.error, ERROR_TEXT.cameraStopped);
    assert.equal(scanner.status.cueKey, 'error');
    camera.playing = true;
    camera.deliver(260, { captureTime: 220, presentationTime: 260, presentedFrames: 1 });
    assert.equal(scanner.status.error, null);
  } finally {
    scanner.stop();
    harness.dispose();
  }
});

await test('a null readback logs read-failed and ends the step; the next frame reads again (S28)', async () => {
  const camera = new StubCamera();
  camera.failReads = 2;   // the first two readbacks the tracker asks for, which are of the first keyframe's candidates
  const r = await play(pan, { camera });
  try {
    const log = r.scanner.inspect().tracker.log;
    const [first, second] = camera.reads;
    assert.equal(second, first + 1, 'the frame after a failed readback reads again');
    // Until the axis mapping confirms, the start hold has no pose (S41) and its frames log stale-pose with no readback.
    const failedAt = log.findIndex(x => x.outcome === 'read-failed');
    assert.ok(failedAt >= 0, 'a readback failed');
    assert.ok(log.slice(0, failedAt).every(x => x.outcome === 'stale-pose' && x.frameId < first), 'before it, only frames that read nothing');
    assert.deepEqual(log.slice(failedAt, failedAt + 3).map(x => [x.outcome, x.frameId]), [['read-failed', first], ['read-failed', second], ['accepted', second + 1]],
      'the first keyframe is the first frame whose readback worked');
    assert.equal(r.scanner.inspect().tracker.keyframes[0].frameId, second + 1);
    assert.ok(r.scanner.inspect().tracker.keyframes.length >= 15, 'the scan carried on');
    assert.equal(r.scanner.status.phase, 'scanning');
  } finally {
    end(r);
  }
});

// ---- 5. tallSpans (S26) --------------------------------------------------------------------------

/** The relative stream's heading when the phone faces north at the start of a pan of this seed: the scan frame's
 *  azimuth of true north, with no gyro scale error and no drift. */
function yawZeroOf(seed: number): number {
  const probe = synthPan({ ...DEFAULT_PAN, scene, seed, w: 9, h: 16, fps: 2, turnDeg: 2, startHoldS: 0.1, endHoldS: 0.1, gyroScaleErr: 0, driftDegMin: 0 });
  const first = probe.observations.find(o => o.kind === 'orientation' && o.event === 'deviceorientation') as { alpha: number; beta: number; gamma: number };
  return headingDeg(quatFromDeviceOrientation(first.alpha, first.beta, first.gamma));
}

await test('tallSpans match the Tall columns of the raster they describe, in its scan frame, across 0 (S26)', async () => {
  // A seed whose scan frame puts a tower that stands at true azimuth A0 at scan azimuth 0, with A0 far enough into a
  // pan from north that the pan covers the tower and 30 degrees either side.
  let seed = 0, towerAz = 0;
  for (let s = 1; s <= 200 && seed === 0; s++) {
    const a0 = (((-yawZeroOf(s)) % 360) + 360) % 360;
    if (a0 >= 40 && a0 <= 70) { seed = s; towerAz = a0; }
  }
  assert.ok(seed > 0, 'a seed puts the tower in range');
  const towerScene = makeSynthScene({
    seed: 5, skyline: az => (Math.abs(wrap180(az - towerAz)) <= 3 ? 75 : 5 + 2 * Math.sin(az * 7 * DEG)),
  });
  const towerPan = synthPan({
    ...DEFAULT_PAN, scene: towerScene, seed, w: 45, h: 80, fps: 15, speedDegS: 12, turnDeg: towerAz + 35,
    startHoldS: 0.5, endHoldS: 1.5, gyroScaleErr: 0, driftDegMin: 0,
  });
  const r = await play(towerPan);
  try {
    const st = r.scanner.status, ins = r.scanner.inspect();
    // The raster the spans describe is the live one, in the scan frame: trace all of it as it stands now.
    const alts = ins.tracker.keyframes.map(kf => elevationDeg(kf.pose)).sort((a, b) => a - b);
    const mid = alts.length >> 1;
    const axisAltDeg = alts.length % 2 ? alts[mid] : (alts[mid - 1] + alts[mid]) / 2;
    const traced = tracer.extract(ins.pano, { focalSdPct: PRIOR_SD_PCT, axisAltDeg });
    const tall = [...traced.state.keys()].filter(x => traced.state[x] === ColState.Tall);
    const inSpan = (az: number) => st.tallSpans.some(s => (s.from <= s.to ? az >= s.from && az < s.to : az >= s.from || az < s.to));
    const spanned = Array.from({ length: PANO_W }, (_, x) => x).filter(x => inSpan((x + 0.5) * 360 / PANO_W));
    assert.ok(tall.length >= 6, `the tower is Tall (${tall.length} columns)`);
    assert.deepEqual(spanned, tall, 'the spans cover exactly the Tall columns');
    for (const s of st.tallSpans) assert.ok(s.from >= 0 && s.from < 360 && s.to >= 0 && s.to < 360, `span ${s.from}-${s.to} in [0, 360)`);
    const across = st.tallSpans.find(s => s.to < s.from);
    assert.ok(across, `a span runs across 0 (to < from): ${JSON.stringify(st.tallSpans)}`);
    const width = across.to + 360 - across.from;
    assert.ok(width >= 3 && width <= 12, `the tower span is ${width.toFixed(2)} degrees wide`);
    assert.ok(Math.abs(wrap180((across.from + width / 2))) < 3, 'centred on scan azimuth 0, where the tower stands in the scan frame');
  } finally {
    end(r);
  }
});

console.log(`scannerV0.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
