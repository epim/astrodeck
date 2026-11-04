// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w2_6Rig.ts - a phone for the WP 2.6 cases (#904, #903, #902, #1117): a jsdom
// browser whose camera looks at a WORLD and whose compass says where the phone
// points, driving the real `PhotosphereSweep` the way `photosphereCaptureDom`
// does, without the React sheet.
//
// The camera renders `world(az, alt)` through the same 60 degree pinhole the
// scanner assumes, from the pose the test names. That pose is normally the one
// the sensor reports; `aimed` lets a case make them differ, which is how a
// sensor that lies about where the phone points is staged (#47: a magnetometer
// spike worn for a whole hold). Not a test file: the runner only walks `*.test.*`.
import { JSDOM } from 'jsdom';

export type World = (az: number, alt: number) => { r: number; g: number; b: number };

export interface Rig {
  /** The sweep module's exports, imported after the browser existed. */
  sweepModule: typeof import('../photosphere');
  geometry: typeof import('../photosphereGeometry');
  /** Point the compass: heading `az`, camera tilted to `alt` degrees up. */
  aim(az: number, alt: number): void;
  /** Make the camera see a different direction from the one the compass reports, for the next frames; null puts them back in step. */
  seeing(az: number | null, alt?: number): void;
  /** 600 ms of sensor and camera, then one capture attempt. */
  tick(): void;
  /** A new sweep started on a fresh camera session, ready to `begin()`. */
  newSweep(deviceId?: string): Promise<InstanceType<typeof import('../photosphere').PhotosphereSweep>>;
  window: any;
}

export async function createRig(world: World): Promise<Rig> {
  const dom = new JSDOM('<html><body></body></html>', { url: 'https://localhost/', pretendToBeVisual: true });
  const w = dom.window as any, g = globalThis as any;
  for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLVideoElement', 'HTMLCanvasElement', 'Element', 'Node',
    'Event', 'localStorage', 'requestAnimationFrame', 'cancelAnimationFrame']) {
    Object.defineProperty(g, k, { value: k === 'window' ? w : w[k], writable: true, configurable: true });
  }
  w.DeviceOrientationEvent = class {};
  Object.defineProperty(w, 'isSecureContext', { value: true });
  Object.defineProperty(w.HTMLVideoElement.prototype, 'videoWidth', { get: () => 640 });
  Object.defineProperty(w.HTMLVideoElement.prototype, 'videoHeight', { get: () => 480 });
  w.HTMLVideoElement.prototype.play = async function () {};
  let mediaTime = 0;
  Object.defineProperty(w.HTMLVideoElement.prototype, 'currentTime', { get: () => mediaTime, configurable: true });
  Object.defineProperty(w.HTMLVideoElement.prototype, 'paused', { get: () => false, configurable: true });
  Object.defineProperty(w.HTMLVideoElement.prototype, 'ended', { get: () => false, configurable: true });
  Object.defineProperty(w.HTMLVideoElement.prototype, 'readyState', { get: () => 2, configurable: true });
  const intervals = new Map<number, () => void>();
  let intervalId = 0;
  g.setInterval = (fn: () => void) => { intervals.set(++intervalId, fn); return intervalId; };
  g.clearInterval = (id: number) => intervals.delete(id);
  const track = { stop() {}, getSettings: () => ({ deviceId: 'cam' }), addEventListener() {}, readyState: 'live', muted: false };
  Object.defineProperty(w.navigator, 'mediaDevices', { value: {
    enumerateDevices: async () => [{ kind: 'videoinput', deviceId: 'cam', label: 'Back main wide camera' }],
    getUserMedia: async () => ({ getTracks: () => [track], getVideoTracks: () => [track] }),
  }, configurable: true });
  let sensorNow = 10000;
  Object.defineProperty(performance, 'now', { value: () => sensorNow, configurable: true });

  const geometry = await import('../photosphereGeometry');
  const sweepModule = await import('../photosphere');
  const { orientationBasis } = geometry;

  let sensor: { type: string; alpha: number; beta: number; gamma: number; absolute: boolean } | null = null;
  for (const type of ['deviceorientation', 'deviceorientationabsolute']) {
    w.addEventListener(type, (e: any) => { sensor = { type, alpha: e.alpha, beta: e.beta, gamma: e.gamma, absolute: e.absolute }; });
  }
  let seen: { az: number; alt: number } | null = null;

  w.HTMLCanvasElement.prototype.getContext = () => ({
    drawImage() {},
    getImageData: (_x: number, _y: number, width: number, height: number) => {
      const data = new Uint8ClampedArray(width * height * 4);
      const d = Math.PI / 180;
      const basis = seen
        ? orientationBasis((360 - seen.az) % 360, 90 + seen.alt, 0, 0)
        : sensor ? orientationBasis(sensor.alpha, sensor.beta, sensor.gamma, 0) : null;
      const tanY = Math.tan(30 * d), tanX = tanY * width / height;
      for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
        const i = (y * width + x) * 4;
        let px = { r: 120, g: 120, b: 120 };
        if (basis) {
          const sx = (x + .5) / width, sy = (y + .5) / height;
          const ray = [0, 1, 2].map(k => basis.forward[k] + basis.right[k] * (sx * 2 - 1) * tanX + basis.up[k] * (1 - sy * 2) * tanY);
          const norm = Math.hypot(ray[0], ray[1], ray[2]);
          px = world((Math.atan2(ray[0], ray[1]) / d + 360) % 360, Math.asin(ray[2] / norm) / d);
        }
        data[i] = px.r; data[i + 1] = px.g; data[i + 2] = px.b; data[i + 3] = 255;
      }
      return { data };
    },
    createImageData: (width: number, height: number) => ({ data: new Uint8ClampedArray(width * height * 4) }), putImageData() {},
  });
  w.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,';

  const aim = (az: number, alt: number) => {
    const ev = new w.Event('deviceorientationabsolute');
    sensorNow += 50;
    Object.defineProperty(ev, 'timeStamp', { value: sensorNow });
    Object.assign(ev, { alpha: (360 - az) % 360, beta: 90 + alt, gamma: 0, absolute: true });
    w.dispatchEvent(ev);
  };
  const tick = () => {
    // A camera frame after 600 ms of sensor observations, as photosphereCaptureDom does it.
    for (let i = 0; i < 6; i++) {
      sensorNow += 100;
      if (sensor) {
        const { type, ...pose } = sensor;
        const event = new w.Event(type);
        Object.assign(event, pose);
        Object.defineProperty(event, 'timeStamp', { value: sensorNow });
        w.dispatchEvent(event);
      }
    }
    mediaTime += 0.6;
    for (const fn of [...intervals.values()]) fn();
  };
  const newSweep = async (deviceId = 'cam') => {
    for (let i = w.localStorage.length - 1; i >= 0; i--) w.localStorage.removeItem(w.localStorage.key(i) as string);
    const sweep = new sweepModule.PhotosphereSweep();
    await sweep.start(w.document.createElement('video'), w.document.createElement('canvas'), deviceId);
    return sweep;
  };
  return { sweepModule, geometry, aim, tick, newSweep, window: w,
    seeing: (az, alt = 0) => { seen = az === null ? null : { az, alt }; } };
}
