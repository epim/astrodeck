// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The browser the recorded case is replayed in.
//
// Everything the scanner reaches for outside itself is stubbed here, and every
// stub is driven by the case rather than by a timer: the clock is a variable,
// the camera is whichever frame the driver has loaded, and a video frame
// arrives only when the driver hands one over. Nothing in this file decides
// anything about the scan - it is a browser, not a test.
//
// It is modelled on the DOM harness in __tests__/photosphereStillnessDom (not
// imported from it: that file drives its own scenario at import time, and its
// scene is a function rather than a picture). The three things worth repeating
// from it are why they are the way they are:
//
//   * BOTH clocks are the same variable. `compassReady` reads `performance.now`
//     and `justCaptured` reads `Date.now`; leaving one of them on the wall
//     clock would let a replay of a two-minute night run in fifteen seconds of
//     real time and read as fifteen seconds of holding.
//   * The canvas is a real resampler, not a fabricated gradient. The scanner
//     judges stillness, overlap and horizon from the pixels it draws, so a stub
//     that invented them would be grading the stub.
//   * `localStorage` is empty, so the scanner uses its default lens estimate
//     rather than a calibration some earlier run left behind.
import { JSDOM } from 'jsdom';
import type { Raster } from './png';

interface Span { start: number; weights: Float64Array; total: number }

const spanCache = new Map<string, Span[]>();

/** Which source pixels an output pixel covers, and by how much of each. */
function spans(source: number, target: number): Span[] {
  const key = `${source}:${target}`;
  const cached = spanCache.get(key);
  if (cached) return cached;
  const scale = source / target;
  const built = Array.from({ length: target }, (_, i) => {
    const from = i * scale, to = (i + 1) * scale;
    const start = Math.min(source - 1, Math.floor(from));
    const end = Math.max(start + 1, Math.min(source, Math.ceil(to)));
    const weights = new Float64Array(end - start);
    let total = 0;
    for (let s = start; s < end; s++) {
      const weight = Math.max(0, Math.min(s + 1, to) - Math.max(s, from));
      weights[s - start] = weight;
      total += weight;
    }
    return { start, weights, total: total || 1 };
  });
  spanCache.set(key, built);
  return built;
}

/** Box-filter `rgba` (RGBA, `W` x `H`) down to `w` x `h` by exact area
 *  averaging: an output pixel is the mean of the source rectangle it covers,
 *  weighted by how much of each source pixel falls inside it.
 *
 *  This is the idealisation the simulator's contract asks for, NOT a claim
 *  about any browser. What a real `drawImage` into a smaller canvas does is
 *  implementation-defined - the filter, whether it is separable, whether it
 *  happens on the GPU and in what precision, all vary by engine and by scale
 *  factor - so a replay is a measurement against a stated downscale, and the
 *  same scanner on a phone will see slightly different pixels. What matters
 *  for the scan is only that the downscale averages rather than picks: a
 *  nearest-neighbour choice of one source pixel in 15 would make a still view
 *  flicker between neighbouring samples, and a 32 x 24 stillness grid built
 *  from single pixels would read sensor noise as movement. */
export function resample(rgba: Uint8ClampedArray, W: number, H: number, w: number, h: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(w * h * 4);
  const xs = spans(W, w), ys = spans(H, h);
  for (let j = 0; j < h; j++) {
    const ry = ys[j];
    for (let i = 0; i < w; i++) {
      const rx = xs[i];
      let r = 0, g = 0, b = 0, a = 0;
      for (let sy = 0; sy < ry.weights.length; sy++) {
        const wy = ry.weights[sy];
        if (!wy) continue;
        const row = (ry.start + sy) * W;
        for (let sx = 0; sx < rx.weights.length; sx++) {
          const weight = wy * rx.weights[sx];
          if (!weight) continue;
          const k = (row + rx.start + sx) * 4;
          r += rgba[k] * weight; g += rgba[k + 1] * weight; b += rgba[k + 2] * weight; a += rgba[k + 3] * weight;
        }
      }
      const total = rx.total * ry.total, o = (j * w + i) * 4;
      out[o] = r / total; out[o + 1] = g / total; out[o + 2] = b / total; out[o + 3] = a / total;
    }
  }
  return out;
}

/** The metadata a `requestVideoFrameCallback` receives, as the case describes
 *  the frame. The scanner reads `captureTime` and nothing else, but the whole
 *  object is handed over so a later reader of the callback finds what a browser
 *  would have given it.
 *
 *  `captureTime: null` is a frame whose metadata carries none, which some
 *  browsers deliver. The key is then LEFT OFF what the callback receives
 *  rather than set to null or undefined:
 *  `'captureTime' in metadata` is the test a page can make, and a scanner that
 *  guards with `!== undefined` and one that guards with `!= null` must both see
 *  a frame without one. */
export interface FrameMetadata {
  captureTime: number | null;
  mediaTime: number;
  presentationTime: number;
  expectedDisplayTime: number;
  width: number;
  height: number;
  presentedFrames: number;
}

/** What the video-frame callback is actually handed: `FrameMetadata` with
 *  `captureTime` present only when the frame has one. */
export type DeliveredMetadata = Omit<FrameMetadata, 'captureTime'> & { captureTime?: number };

/** A `devicemotion` reading: the rate of turn about the device's own axes, in
 *  degrees per second. `MotionStability` reads `hypot(alpha, beta, gamma)` and
 *  nothing else. `rate` is null for an event that carries no `rotationRate`,
 *  and a component is null where the browser delivered null for it. */
export interface MotionReading {
  timeStamp: number;
  rate: { alpha: number | null; beta: number | null; gamma: number | null } | null;
}

/** The two events a phone can raise for its attitude. The recorded cases of
 *  the legacy scanner carry only `deviceorientationabsolute`, so that is what
 *  an unlabelled reading is. */
export type OrientationEventType = 'deviceorientation' | 'deviceorientationabsolute';

export interface OrientationReading {
  timeStamp: number;
  /** Null where the browser blocks the sensor but still fires the event. */
  alpha: number | null;
  beta: number | null;
  gamma: number | null;
  /** The event's own flag. Null leaves the property off the event entirely,
   *  which is an event that has none. */
  absolute: boolean | null;
  /** Which event to fire. Absent means `deviceorientationabsolute`. */
  event?: OrientationEventType;
}

export interface ReplayHarness {
  video: HTMLVideoElement;
  canvas: HTMLCanvasElement;
  /** Move the one virtual clock that `performance.now` and `Date.now` read. */
  setClock(ms: number): void;
  now(): number;
  /** The picture the camera is showing from now on. */
  setFrame(frame: Raster, captureMs: number): void;
  /** Fire the reading as `reading.event`, `deviceorientationabsolute` when it
   *  names none. */
  dispatchOrientation(reading: OrientationReading): void;
  /** Turn the screen: `screen.orientation.angle` reads `deg` from now on and
   *  the stub's `change` event fires, as a browser does when the phone is
   *  rotated with auto-rotate on. It starts at 0 (portrait). */
  setScreenAngle(deg: number): void;
  /** The SECOND witness (issue #105). `deviceorientation` is change-driven,
   *  so a phone holding still goes silent; `devicemotion` fires at a fixed
   *  rate whether or not anything moved. Until the recordings carried this
   *  stream, `MotionStability` collected nothing in any replay and its
   *  witness read `stale` from end to end. */
  dispatchMotion(reading: MotionReading): void;
  /** Run the scanner's stored video-frame callback, if it has registered one.
   *  Returns false when it has not - which is itself a finding, not a silence
   *  to swallow: the scanner would then be on its interval fallback. A
   *  `captureTime` of null is left off what the callback receives. */
  deliverFrame(metadata: FrameMetadata): boolean;
  dispose(): void;
}

/** The 2D context a scanner draws through. `drawImage`, `getImageData` and
 *  `createImageData` do real work (below); the rest of the surface exists so a
 *  scanner that paints a canvas - a ribbon, a live frame - runs under the
 *  replay without a TypeError, and does nothing: no path, clip or transform is
 *  modelled, so nothing a scanner reads back depends on one. The properties
 *  are plain and settable, which is all a scanner asks of them. */
interface StubContext {
  imageSmoothingEnabled: boolean;
  imageSmoothingQuality: string;
  globalAlpha: number;
  globalCompositeOperation: string;
  fillStyle: unknown;
  strokeStyle: unknown;
  lineWidth: number;
  drawImage(image: unknown, x: number, y: number, width: number, height: number): void;
  getImageData(x: number, y: number, width: number, height: number): { data: Uint8ClampedArray };
  createImageData(width: number, height: number): { data: Uint8ClampedArray };
  putImageData(): void;
  save(): void;
  restore(): void;
  clearRect(x: number, y: number, width: number, height: number): void;
  fillRect(x: number, y: number, width: number, height: number): void;
  setTransform(a: number, b: number, c: number, d: number, e: number, f: number): void;
  beginPath(): void;
  moveTo(x: number, y: number): void;
  lineTo(x: number, y: number): void;
  closePath(): void;
  clip(): void;
  stroke(): void;
}

export function createHarness(options: { videoWidth: number; videoHeight: number }): ReplayHarness {
  const { videoWidth, videoHeight } = options;
  const dom = new JSDOM('<html><body></body></html>', { url: 'https://localhost/', pretendToBeVisual: true });
  const w = dom.window as unknown as Record<string, any>;
  const g = globalThis as unknown as Record<string, any>;

  // Everything this harness puts on a shared object is put back by dispose().
  // The process outlives the replay - the test file replays twice and then goes
  // on to assert other things - and a frozen `Date.now` left behind in it is a
  // trap for whatever runs next, which would fail somewhere far from here.
  const restores: (() => void)[] = [];
  const replace = (target: object, key: string, value: unknown) => {
    const original = Object.getOwnPropertyDescriptor(target, key);
    restores.push(original
      ? () => Object.defineProperty(target, key, original)
      : () => { delete (target as Record<string, unknown>)[key]; });
    Object.defineProperty(target, key, { value, writable: true, configurable: true });
  };

  for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLVideoElement', 'HTMLCanvasElement',
    'Element', 'Node', 'Event', 'localStorage', 'requestAnimationFrame', 'cancelAnimationFrame', 'screen'])
    replace(g, key, key === 'window' ? w : w[key]);
  w.DeviceOrientationEvent = class { };
  Object.defineProperty(w, 'isSecureContext', { value: true, configurable: true });

  // `screen.orientation`: jsdom has none, and a scanner reads `.angle` from it
  // on every orientation event (the legacy one through
  // `window.screen?.orientation?.angle ?? 0`). It is an EventTarget so a
  // scanner can listen for `change`, and the angle moves only when the driver
  // says so (`setScreenAngle`): a replay does not rotate a phone by itself.
  let screenAngle = 0;
  const screenOrientation = new w.EventTarget();
  Object.defineProperty(screenOrientation, 'angle', { get: () => screenAngle, configurable: true });
  Object.defineProperty(screenOrientation, 'type', {
    get: () => (screenAngle % 180 === 0 ? 'portrait-primary' : 'landscape-primary'), configurable: true,
  });
  Object.defineProperty(w.screen, 'orientation', { value: screenOrientation, configurable: true });

  let clock = 0;
  replace(performance, 'now', () => clock);
  replace(Date, 'now', () => clock);

  // The picture the camera is showing, and the moment it was taken. Both are
  // replaced whole when the driver loads the next frame; nothing here keeps a
  // history, because the scanner is the only thing allowed to remember frames.
  let current: Raster = { width: videoWidth, height: videoHeight, pixels: new Uint8ClampedArray(videoWidth * videoHeight * 4) };
  let captureSeconds = 0;
  // One resample per (frame, size) pair. The scanner asks for the same frame at
  // 32 x 24 for stillness and at 240 x 320 for capture, and may ask twice; the
  // arithmetic is identical every time, so it is done once.
  let resampled = new Map<string, Uint8ClampedArray>();

  Object.defineProperty(w.HTMLVideoElement.prototype, 'videoWidth', { get: () => videoWidth, configurable: true });
  Object.defineProperty(w.HTMLVideoElement.prototype, 'videoHeight', { get: () => videoHeight, configurable: true });
  Object.defineProperty(w.HTMLVideoElement.prototype, 'currentTime', { get: () => captureSeconds, configurable: true });
  Object.defineProperty(w.HTMLVideoElement.prototype, 'paused', { get: () => false, configurable: true });
  Object.defineProperty(w.HTMLVideoElement.prototype, 'ended', { get: () => false, configurable: true });
  Object.defineProperty(w.HTMLVideoElement.prototype, 'readyState', { get: () => 2, configurable: true });
  w.HTMLVideoElement.prototype.play = async function () { };
  // Frames the page has been handed, which is what `totalVideoFrames` counts in
  // a browser: a scanner without `requestVideoFrameCallback` polls it. Nothing
  // is ever dropped or corrupt here - the case recorded the frames that arrived.
  let presented = 0;
  w.HTMLVideoElement.prototype.getVideoPlaybackQuality = function () {
    return {
      creationTime: clock, totalVideoFrames: presented, droppedVideoFrames: 0, corruptedVideoFrames: 0, totalFrameDelay: 0,
    };
  };

  let frameCallback: ((now: number, metadata: DeliveredMetadata) => void) | null = null;
  w.HTMLVideoElement.prototype.requestVideoFrameCallback = function (fn: (now: number, metadata: DeliveredMetadata) => void) {
    frameCallback = fn;
    return 1;
  };
  w.HTMLVideoElement.prototype.cancelVideoFrameCallback = function () { frameCallback = null; };

  // One context per canvas, each remembering the size it was last drawn at, so
  // the 32 x 24 stillness canvas and the 240 x 320 capture canvas cannot read
  // each other's pixels.
  const contexts = new WeakMap<object, StubContext>();
  function makeContext(): StubContext {
    let drawnWidth = 0, drawnHeight = 0;
    return {
      imageSmoothingEnabled: true, imageSmoothingQuality: 'low', globalAlpha: 1, globalCompositeOperation: 'source-over',
      fillStyle: '#000000', strokeStyle: '#000000', lineWidth: 1,
      drawImage(_image, _x, _y, width, height) { drawnWidth = width; drawnHeight = height; },
      getImageData(_x, _y, width, height) {
        // A canvas nothing has been drawn to is transparent black, and reading
        // the camera out of it would be the stub answering a question the
        // caller never asked the camera.
        if (!drawnWidth || !drawnHeight) return { data: new Uint8ClampedArray(width * height * 4) };
        const outW = drawnWidth, outH = drawnHeight;
        const key = `${outW}x${outH}`;
        let data = resampled.get(key);
        if (!data) {
          data = resample(current.pixels, current.width, current.height, outW, outH);
          resampled.set(key, data);
        }
        return { data };
      },
      createImageData(width, height) { return { data: new Uint8ClampedArray(width * height * 4) }; },
      putImageData() { },
      save() { }, restore() { }, clearRect() { }, fillRect() { }, setTransform() { },
      beginPath() { }, moveTo() { }, lineTo() { }, closePath() { }, clip() { }, stroke() { },
    };
  }
  w.HTMLCanvasElement.prototype.getContext = function (this: object) {
    let ctx = contexts.get(this);
    if (!ctx) { ctx = makeContext(); contexts.set(this, ctx); }
    return ctx;
  };
  w.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,';

  Object.defineProperty(w.document, 'visibilityState', { get: () => 'visible', configurable: true });

  const track = {
    stop() { }, getSettings: () => ({ deviceId: 'rear' }), addEventListener() { },
    readyState: 'live', muted: false,
  };
  Object.defineProperty(w.navigator, 'mediaDevices', {
    value: {
      enumerateDevices: async () => [{ kind: 'videoinput', deviceId: 'rear', label: 'Back main wide camera' }],
      getUserMedia: async () => ({ getTracks: () => [track], getVideoTracks: () => [track] }),
    },
    configurable: true,
  });

  const video = w.document.createElement('video') as HTMLVideoElement;
  const canvas = w.document.createElement('canvas') as HTMLCanvasElement;

  return {
    video,
    canvas,
    setClock(ms: number) { clock = ms; },
    now() { return clock; },
    setFrame(frame: Raster, captureMs: number) {
      current = frame;
      captureSeconds = captureMs / 1000;
      resampled = new Map();
    },
    dispatchMotion(reading: MotionReading) {
      const event = new w.Event('devicemotion');
      Object.defineProperty(event, 'timeStamp', { value: reading.timeStamp, configurable: true });
      // `rotationRate` is the only member the scanner reads. The others a real
      // DeviceMotionEvent carries - acceleration, interval - are left off
      // rather than filled with zeros, so a reader of this harness cannot
      // mistake an unmodelled field for a measured one.
      Object.assign(event, { rotationRate: reading.rate });
      w.dispatchEvent(event);
    },
    dispatchOrientation(reading: OrientationReading) {
      const event = new w.Event(reading.event ?? 'deviceorientationabsolute');
      Object.defineProperty(event, 'timeStamp', { value: reading.timeStamp, configurable: true });
      Object.assign(event, { alpha: reading.alpha, beta: reading.beta, gamma: reading.gamma });
      if (reading.absolute !== null) Object.assign(event, { absolute: reading.absolute });
      w.dispatchEvent(event);
    },
    setScreenAngle(deg: number) {
      screenAngle = deg;
      screenOrientation.dispatchEvent(new w.Event('change'));
    },
    deliverFrame(metadata: FrameMetadata) {
      presented++;
      const fn = frameCallback;
      if (!fn) return false;
      frameCallback = null;
      const { captureTime, ...rest } = metadata;
      fn(clock, captureTime === null ? rest : { captureTime, ...rest });
      return true;
    },
    dispose() {
      frameCallback = null;
      dom.window.close();
      for (const restore of restores.reverse()) restore();
    },
  };
}

/** How long a fixed loop of floating-point work takes on this machine, in ms:
 *  the median of five runs. A replay's cost numbers are wall time, and wall time
 *  moves with the machine, the load and the power plan; dividing by this gives
 *  cost in units of "that loop", which a slow laptop and a fast one agree on
 *  far better than they agree on milliseconds. The loop is pinned (SPEC-v2
 *  7.4): changing its body or its count changes what every recorded unit means.
 *  The `s < 0` test is never true and exists so the loop's result is used. */
export function referenceLoopMs(): number {
  const runs: number[] = [];
  for (let r = 0; r < 5; r++) {
    const t0 = process.hrtime.bigint();
    let s = 0;
    for (let i = 0; i < 10_000_000; i++) s += (i & 1023) * 1.0000001;
    const t1 = process.hrtime.bigint();
    if (s < 0) throw new Error('unreachable');
    runs.push(Number(t1 - t0) / 1e6);
  }
  runs.sort((a, b) => a - b);
  return runs[2];
}
