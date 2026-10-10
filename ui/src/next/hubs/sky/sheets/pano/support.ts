// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// What this browser can do for the panorama scanner, asked before and while the camera opens (SPEC-v2 2.3, 2.11).
//
// `checkPanoSupport` is COPIED from `checkPhotosphereSupport` (photosphere.ts:229), not moved: the old scanner keeps its
// own copy and is not edited (D24). The other three are new. None of them keeps state, and none of them logs.

/** Secure context + `getUserMedia` - what the scanner needs before it can even ask for a camera. Same reason string as
 *  the old scanner and the AR camera/gyro fallback: one sentence for "this needs HTTPS", not three. */
export function checkPanoSupport(): { supported: boolean; reason: string | null } {
  const insecure = typeof window === 'undefined' || !window.isSecureContext;
  const md = typeof navigator === 'undefined'
    ? undefined
    : (navigator as Navigator & { mediaDevices?: MediaDevices }).mediaDevices;
  if (insecure || !md || typeof md.getUserMedia !== 'function') {
    return {
      supported: false,
      reason: 'Photosphere capture needs a secure connection - set one up in Connection.',
    };
  }
  return { supported: true, reason: null };
}

/** `navigator.permissions.query({ name: 'gyroscope' })` (2.11). A browser that blocks motion sensors for the site
 *  answers `denied` here before a single event has fired. `unknown` covers every other outcome: no Permissions API,
 *  a browser that does not know the name `gyroscope` (Firefox and Safari reject it), a query that throws, and a state
 *  string this code has not heard of. It is never an error, because the answer only ever adds a cue. */
export async function queryMotionPermission(): Promise<'granted' | 'denied' | 'prompt' | 'unknown'> {
  try {
    const permissions = typeof navigator === 'undefined' ? undefined : navigator.permissions;
    if (!permissions || typeof permissions.query !== 'function') return 'unknown';
    const status = await permissions.query({ name: 'gyroscope' as PermissionName });
    const state: string = status.state;
    return state === 'granted' || state === 'denied' || state === 'prompt' ? state : 'unknown';
  } catch {
    return 'unknown';
  }
}

type Requestable = { requestPermission?: () => Promise<string> };

/** iOS gates orientation and motion events behind two separate prompts, `DeviceOrientationEvent.requestPermission`
 *  and `DeviceMotionEvent.requestPermission` (RD finding 22). Safari only shows a prompt from inside a tap, and the
 *  tap's allowance is spent by the first `await`, so BOTH are called here, synchronously, before the function returns
 *  anything to wait on. The caller must call this from the tap handler, ahead of every `await` of its own - including
 *  `CameraSource.open` - and wait on the result afterwards.
 *
 *  `not-needed` is every browser that has neither method (Android, desktop, iOS before 13). A rejected or thrown
 *  request counts as `denied`, which is what the old scanner did, and so does anything other than `granted`. */
export function requestIosMotionPermission(): Promise<'granted' | 'denied' | 'not-needed'> {
  const win = typeof window === 'undefined' ? undefined : (window as unknown as Record<string, Requestable | undefined>);
  const asks: Promise<string>[] = [];
  for (const name of ['DeviceOrientationEvent', 'DeviceMotionEvent']) {
    const ctor = win?.[name];
    // Called as a method: on iOS `requestPermission` is static and refuses to run detached from its constructor.
    if (typeof ctor?.requestPermission !== 'function') continue;
    try { asks.push(Promise.resolve(ctor.requestPermission()).catch(() => 'denied')); }
    catch { asks.push(Promise.resolve('denied')); }
  }
  if (asks.length === 0) return Promise.resolve('not-needed');
  return Promise.all(asks).then(answers => answers.every(a => a === 'granted') ? 'granted' : 'denied');
}

const FARBLE_SIZE = 64;
/** More than this share of the channels must move for the canvas to count as farbled (2.11). */
const FARBLE_SHARE = 0.001;
/** A channel moves when it differs by more than this. Rounding in a colour-managed canvas can cost one or two. */
const FARBLE_STEP = 2;

/** Is `getImageData` handing back pixels that were not put in (Brave Shields, privacy extensions)? Writes a seeded
 *  64 x 64 opaque pattern with `putImageData` and reads it back. Opaque, because a canvas premultiplies colour by alpha
 *  and an honest browser would otherwise lose low bits on its own. True when more than 0.1 % of the colour channels
 *  differ by more than 2. Null when the readback is all zeros, which is what a canvas that cannot hold pixels returns
 *  (no real 2d context, as under the replay harness), and when the context refuses: neither says anything about
 *  farbling. The canvas is resized to 64 x 64, so hand it a scratch canvas and not one that is in use. */
export function farblingProbe(canvas: HTMLCanvasElement): boolean | null {
  try {
    if (canvas.width !== FARBLE_SIZE) canvas.width = FARBLE_SIZE;
    if (canvas.height !== FARBLE_SIZE) canvas.height = FARBLE_SIZE;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) return null;
    const put = ctx.createImageData(FARBLE_SIZE, FARBLE_SIZE);
    let s = 0x9E3779B9;   // mulberry32: the same pattern on every call
    const next = () => {
      s = (s + 0x6D2B79F5) >>> 0;
      let t = s;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return (t ^ (t >>> 14)) >>> 0;
    };
    for (let i = 0; i < put.data.length; i += 4) {
      const v = next();
      put.data[i] = v & 255; put.data[i + 1] = (v >>> 8) & 255; put.data[i + 2] = (v >>> 16) & 255; put.data[i + 3] = 255;
    }
    ctx.putImageData(put, 0, 0);
    const got = ctx.getImageData(0, 0, FARBLE_SIZE, FARBLE_SIZE).data;
    let any = false, moved = 0, channels = 0;
    for (let i = 0; i < got.length; i++) {
      if (got[i] !== 0) any = true;
      if ((i & 3) === 3) continue;
      channels++;
      if (Math.abs(got[i] - put.data[i]) > FARBLE_STEP) moved++;
    }
    if (!any) return null;
    return moved > FARBLE_SHARE * channels;
  } catch {
    return null;
  }
}
