// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T30: the scanner rig of scannerV1.test.ts and scannerV1Ring.test.ts (SPEC-v2 7.2, the scanner v1 row).
//
// Test helper, never imported by production code, and not a test: the name does not end in `.test.ts`, so `run-tests.mjs` does not
// run it. A `CameraSourceLike` stub, a scanner opened over it in the replay's browser (`__sim__/harness.ts`) that a test drives
// by hand, sensor event by sensor event, and `ring`, which plays a synthetic pan (synth.ts, T17) into a scanner as the replay
// plays it, with the frames of the baked scene of closureRig.ts seen from the truth pose of each frame under the simulator's
// sensor noise, so that a full ring is a second or so.
import assert from 'node:assert/strict';
import { createHarness, type ReplayHarness } from '../../../__sim__/harness';
import { mergeObservations, type FrameObservation, type Observation } from '../../../__sim__/replay';
import { frameTime } from '../../cameraSource';
import { PanoramaScanner } from '../../scanner';
import type { AnalysisFrame, AxisMapping, CameraSettingsReport, CameraSourceLike, FrameMeta, Stats } from '../../types';
import { SCENE, rng, viewOf } from './closureRig';
import { DEFAULT_PAN, synthPan, type SynthPan, type SynthScene } from './synth';

const NO_STATS: Stats = { n: 0, p50: null, p95: null, max: null };
const FAKE_SETTINGS: CameraSettingsReport = {
  width: 720, height: 1280, frameRate: 30, resizeMode: 'none', zoom: null, focusMode: null, focusDistance: null,
  facingMode: 'environment', label: 'stub camera',
};
export const CONFIRMED_AXIS: AxisMapping = { perm: [0, 1, 2], sign: [1, 1, 1], unit: 'deg', fit: 1, confirmed: true, samples: 100 };

/** A CameraSourceLike whose frames, tiny readback, shape and playing state the test sets. */
export class StubCamera implements CameraSourceLike {
  playing = true;
  lastFrameAt: number | null = null;
  /** What `readback` returns for a frame number; the default is a grey frame. */
  picture: ((frameNo: number) => Uint8ClampedArray) | null = null;
  /** What `readbackTiny` returns now (45 x 80 luma), or null. */
  tiny: Uint8Array | null = null;
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
    const rgba = this.picture ? this.picture(meta.frameNo) : new Uint8ClampedArray(180 * 320 * 4).fill(128);
    return { frameId: meta.frameNo, t: meta.t, w: 180, h: 320, rgba, readbackMs: 1 };
  }
  readbackTiny(): Uint8Array | null { return this.tiny; }
  refreshLiveSource(): void { }
  liveSource(): HTMLCanvasElement | null { return null; }
  close(): void { }
  /** Deliver a frame as CameraSource would. `landscape` swaps the delivered size (a phone turned on its side). */
  deliver(t: number, landscape = false): void {
    assert.ok(this.cb, 'the scanner listens to the stub camera');
    this.lastFrameAt = t;
    this.cb({
      t: frameTime({ captureTime: t, presentationTime: t }, t), frameNo: ++this.frameNo, captureTime: t, presentationTime: t,
      expectedDisplayTime: t, mediaTime: t / 1000, width: landscape ? 320 : 180, height: landscape ? 180 : 320, presentedFrames: this.frameNo,
      viaRvfc: true,
    });
  }
}

/** Seeded luma noise, one 45 x 80 picture per seed: two seeds are two unrelated images. */
export function tinyPicture(seed: number): Uint8Array {
  const r = rng(seed), px = new Uint8Array(45 * 80);
  for (let i = 0; i < px.length; i++) px[i] = Math.floor(r() * 256);
  return px;
}

export interface Hand { scanner: PanoramaScanner; harness: ReplayHarness; camera: StubCamera; t: number; frames: number; begunAt: number }

/** A scanner started over a stub camera and a harness, with no events yet. */
export async function openHand(): Promise<Hand> {
  const harness = createHarness({ videoWidth: 45, videoHeight: 80 });
  const camera = new StubCamera();
  const scanner = new PanoramaScanner({ camera });
  await scanner.start(harness.video);
  return { scanner, harness, camera, t: 0, frames: 0, begunAt: NaN };
}
export function closeHand(h: Hand): void { h.scanner.stop(); h.harness.dispose(); }

/** Advance by `ms` in 16 ms steps. Every step the compass reads `compass(t)` (absolute stream, tilt 70), the gyro reads zeros, and
 *  every fourth step (64 ms) a frame is delivered unless `frames` says not. The scan begins at the first step it can. */
export function runHand(h: Hand, ms: number, o: { compass: (t: number) => number; frames?: boolean; landscape?: boolean; after?: (t: number) => void }): void {
  const end = h.t + ms;
  for (; h.t < end; h.t += 16) {
    const t = h.t;
    h.harness.setClock(t);
    h.harness.dispatchOrientation({ timeStamp: t, alpha: o.compass(t), beta: 70, gamma: 0, absolute: true, event: 'deviceorientationabsolute' });
    h.harness.dispatchMotion({ timeStamp: t, rate: { alpha: 0, beta: 0, gamma: 0 } });
    if ((o.frames ?? true) && t % 64 === 0) { h.frames++; h.camera.deliver(t, o.landscape ?? false); }
    if (Number.isNaN(h.begunAt) && h.scanner.canBegin) { h.scanner.begin(); h.begunAt = t; }
    o.after?.(t);
  }
}

// ---- A ring through the scanner ---------------------------------------------------------------------------------------------

/** A pan played into a scanner as the replay plays it: readings and frames merged by delivery time, the frame's picture the baked
 *  scene seen from the truth pose of that frame under the simulator's sensor noise, the scan begun at the first moment it can. */
export async function ring(o: { turnDeg: number; confirmAxis: boolean; scene?: SynthScene; onFrame?: (scanner: PanoramaScanner, frameNo: number) => void }): Promise<
  { scanner: PanoramaScanner; harness: ReplayHarness; pan: SynthPan }> {
  const scene = o.scene ?? SCENE;
  const pan = synthPan({ ...DEFAULT_PAN, scene, fps: 30, turnDeg: o.turnDeg });
  const harness = createHarness({ videoWidth: 180, videoHeight: 320 });
  const camera = new StubCamera();
  const noise = rng(7);
  camera.picture = frameNo => {
    const rgba = viewOf(scene, pan.frames[frameNo - 1].truth, pan.k0);
    for (let i = 0; i < rgba.length; i += 4) { const n = 2 * ((noise() + noise() + noise() + noise() - 2) * 1.73); rgba[i] += n; rgba[i + 1] += n; rgba[i + 2] += n; }
    return rgba;
  };
  const scanner = new PanoramaScanner({ camera, focalPriorScale: 1.1 });
  await scanner.start(harness.video);
  if (o.confirmAxis) (scanner.inspect().track as unknown as { axisMap: AxisMapping | null }).axisMap = CONFIRMED_AXIS;
  const frames: FrameObservation[] = pan.frames.map(f => ({
    kind: 'frame', frame_id: f.frameId, t_capture_ms: f.tCaptureMs, t_present_ms: f.tPresentMs, width: pan.k0.w, height: pan.k0.h, file: '',
  }));
  let begun = false, frameNo = 0;
  for (const item of mergeObservations([...frames, ...(pan.observations as unknown as Observation[])])) {
    const at = item.kind === 'frame' ? item.t_present_ms : item.t_receive_ms;
    harness.setClock(at);
    if (item.kind === 'motion') harness.dispatchMotion({ timeStamp: item.t_event_ms, rate: item.rate });
    else if (item.kind === 'orientation') {
      harness.dispatchOrientation({ timeStamp: item.t_event_ms, alpha: item.alpha, beta: item.beta, gamma: item.gamma, absolute: item.absolute ?? null, event: item.event });
    } else if (item.kind === 'frame') {
      camera.deliver(item.t_capture_ms ?? item.t_present_ms);
      frameNo++;
      o.onFrame?.(scanner, frameNo);
    }
    if (!begun && scanner.canBegin) { scanner.begin(); begun = true; }
  }
  assert.ok(begun, 'the scan began');
  return { scanner, harness, pan };
}

