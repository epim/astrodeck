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
 *  would have given it. */
export interface FrameMetadata {
  captureTime: number;
  mediaTime: number;
  presentationTime: number;
  expectedDisplayTime: number;
  width: number;
  height: number;
  presentedFrames: number;
}

export interface OrientationReading {
  timeStamp: number;
  alpha: number;
  beta: number;
  gamma: number;
  absolute: boolean;
}

export interface ReplayHarness {
  video: HTMLVideoElement;
  canvas: HTMLCanvasElement;
  /** Move the one virtual clock that `performance.now` and `Date.now` read. */
  setClock(ms: number): void;
  now(): number;
  /** The picture the camera is showing from now on. */
  setFrame(frame: Raster, captureMs: number): void;
  dispatchOrientation(reading: OrientationReading): void;
  /** Run the scanner's stored video-frame callback, if it has registered one.
   *  Returns false when it has not - which is itself a finding, not a silence
   *  to swallow: the scanner would then be on its interval fallback. */
  deliverFrame(metadata: FrameMetadata): boolean;
  dispose(): void;
}

interface StubContext {
  drawImage(image: unknown, x: number, y: number, width: number, height: number): void;
  getImageData(x: number, y: number, width: number, height: number): { data: Uint8ClampedArray };
  createImageData(width: number, height: number): { data: Uint8ClampedArray };
  putImageData(): void;
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
    'Element', 'Node', 'Event', 'localStorage', 'requestAnimationFrame', 'cancelAnimationFrame'])
    replace(g, key, key === 'window' ? w : w[key]);
  w.DeviceOrientationEvent = class { };
  Object.defineProperty(w, 'isSecureContext', { value: true, configurable: true });

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

  let frameCallback: ((now: number, metadata: FrameMetadata) => void) | null = null;
  w.HTMLVideoElement.prototype.requestVideoFrameCallback = function (fn: (now: number, metadata: FrameMetadata) => void) {
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
    dispatchOrientation(reading: OrientationReading) {
      const event = new w.Event('deviceorientationabsolute');
      Object.defineProperty(event, 'timeStamp', { value: reading.timeStamp, configurable: true });
      Object.assign(event, {
        alpha: reading.alpha, beta: reading.beta, gamma: reading.gamma, absolute: reading.absolute,
      });
      w.dispatchEvent(event);
    },
    deliverFrame(metadata: FrameMetadata) {
      const fn = frameCallback;
      if (!fn) return false;
      frameCallback = null;
      fn(clock, metadata);
      return true;
    },
    dispose() {
      frameCallback = null;
      dom.window.close();
      for (const restore of restores.reverse()) restore();
    },
  };
}
