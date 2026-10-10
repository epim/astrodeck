// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T25: the flagged capture in the horizon sheet (SPEC-v2 7.2, the flagged capture DOM row), mounted in jsdom. Three
// groups: the flag (flag.ts); PanoCapture over a fake scanner, and over the real PanoramaScanner for the Brave cue; and
// HorizonSheet itself with the flag on and off. The sheet creates its scanner with `new PanoramaScanner`, so the sheet
// group puts a fake behind that class's prototype, which keeps the shipped wiring (props, the tap handler, adoption)
// under test with nothing added to the production surface to let a test in.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/sheets/pano/__tests__/panoCaptureDom.test.tsx   (from ui/)
//
// Mutant this file must catch (SPEC-v2 7.2, T25): the flag ignored, the sheet always taking the legacy path
// (`const panoOn = false` in horizon.tsx). 'flag on: the scan button opens PanoCapture ...' finds no `pano-scan-line`
// (and would find the legacy panel and no `pano-capture`), and every later sheet case fails with it.
//
// Further mutants, each caught by the case named (the full list, with the line each one fails on, is in the T25 report):
//   * the role check dropped from `toTrue` (built for any role): 'toTrue is built only for a role that may see precise
//     site coordinates';
//   * the declination's sign lost, or its closure not given to the scanner: the same case, and 'PanoCapture renders ...';
//   * a hidden tab finishing as 'user', or finishing before Start: the two 'a hidden tab ...' cases;
//   * `stop()` missing from the unmount cleanup: 'a 500 ms tick interval runs scanner.tick; unmount stops the scanner ...';
//   * the report taken after the scanner stops, or dropped from Cancel: 'Cancel hands over the report ...';
//   * the iOS permission asked after an await: 'the tap asks for iOS motion permission synchronously ...';
//   * Start not locked before canBegin: 'Start is locked until canBegin, with the reason shown ...';
//   * the picture not written to the browser store, or the result's points not adopted: 'Finish adopts the result's points ...'.
import assert from 'node:assert/strict';
import { createManualRaf } from '../../../../../../testing/rafPolyfill';
import { PANO_H, PANO_W } from '../types';
import type {
  HorizonPoint, ScanReport, ScanResult, ScanStatus, ScannerLike,
} from '../types';
import type { PanoCaptureProps, PanoScannerControl } from '../PanoCapture';

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import('jsdom');
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://localhost/', pretendToBeVisual: true });
const w = dom.window as any;
const g = globalThis as any;

// ---- Fakes installed before React and the sheet are imported ---------------------------------------------------

/** requestAnimationFrame as the shared manual queue (testing/rafPolyfill.ts): nothing runs until `flushRaf`. Slots are
 *  emptied and never reused, so an id stays unique for the life of the file as a browser's does; a callback queued while
 *  flushing waits for the next flush. */
const raf = createManualRaf();
function flushRaf(): number {
  const upTo = raf.pending.length;
  let ran = 0;
  for (let i = 0; i < upTo; i++) {
    const cb = raf.pending[i];
    if (!cb) continue;
    delete raf.pending[i];
    cb(performance.now());
    ran++;
  }
  return ran;
}

/** setInterval as a record: the tests run a callback by hand, so nothing ticks behind them. */
interface IntervalRec { id: number; fn: () => void; ms: number; cleared: boolean }
const intervals: IntervalRec[] = [];
g.setInterval = (fn: () => void, ms: number) => { const rec = { id: intervals.length + 1, fn, ms, cleared: false }; intervals.push(rec); return rec.id; };
g.clearInterval = (id: number) => { const rec = intervals.find(r => r.id === id); if (rec) rec.cleared = true; };
const liveTicks = () => intervals.filter(r => r.ms === 500 && !r.cleared);

/** A clock the tests may freeze; null is the real one. */
let fakeNow: number | null = null;
const realNow = performance.now.bind(performance);
Object.defineProperty(performance, 'now', { value: () => fakeNow ?? realNow(), configurable: true });

for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLVideoElement', 'HTMLCanvasElement', 'HTMLInputElement', 'Element', 'Node',
  'Event', 'CustomEvent', 'MouseEvent', 'KeyboardEvent', 'localStorage', 'sessionStorage', 'getComputedStyle']) {
  Object.defineProperty(g, k, { value: k === 'window' ? w : w[k], writable: true, configurable: true });
}
for (const [k, v] of Object.entries({ requestAnimationFrame: raf.request, cancelAnimationFrame: raf.cancel })) {
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
  w[k] = v;
}
g.IS_REACT_ACT_ENVIRONMENT = true;
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
g.matchMedia = w.matchMedia;
g.WebSocket = w.WebSocket = class { close() {} addEventListener() {} send() {} };
w.DeviceOrientationEvent = class {};
w.DeviceMotionEvent = class {};
Object.defineProperty(w, 'isSecureContext', { value: true });
Object.defineProperty(w.HTMLVideoElement.prototype, 'videoWidth', { get: () => 640 });
Object.defineProperty(w.HTMLVideoElement.prototype, 'videoHeight', { get: () => 480 });
w.HTMLVideoElement.prototype.play = async function () {};
w.HTMLCanvasElement.prototype.getContext = () => null;
w.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=';

// A camera that opens, for the legacy path of the sheet (flag off) and for nothing else.
Object.defineProperty(w.navigator, 'mediaDevices', { value: {
  enumerateDevices: async () => [{ kind: 'videoinput', deviceId: 'main', label: 'Back main wide camera' }],
  getUserMedia: async (constraints: any) => {
    const track = { stop() {}, getSettings: () => ({ deviceId: constraints.video.deviceId?.exact ?? 'main' }), addEventListener() {}, readyState: 'live', muted: false };
    return { getTracks: () => [track], getVideoTracks: () => [track] };
  },
}, configurable: true });
let mediaTime = 0;
Object.defineProperty(w.HTMLVideoElement.prototype, 'currentTime', { get: () => mediaTime, configurable: true });
Object.defineProperty(w.HTMLVideoElement.prototype, 'paused', { get: () => false, configurable: true });
Object.defineProperty(w.HTMLVideoElement.prototype, 'ended', { get: () => false, configurable: true });
Object.defineProperty(w.HTMLVideoElement.prototype, 'readyState', { get: () => 2, configurable: true });

// IndexedDB as a recorder: `writePanorama` stores through it and `readPanorama` finds nothing, so the sheet's picture store
// can be asserted without a browser. Every put is kept in `idbPuts`.
const idbPuts: { key: string; image: string }[] = [];
g.indexedDB = {
  open() {
    const req: any = { result: null, error: null, onupgradeneeded: null, onsuccess: null, onerror: null };
    req.result = {
      createObjectStore() {},
      close() {},
      transaction() {
        const tx: any = { oncomplete: null, onerror: null, onabort: null, error: null };
        tx.objectStore = () => ({
          put(image: string, key: string) { idbPuts.push({ key, image }); setTimeout(() => tx.oncomplete?.(), 0); },
          get() { const r: any = { result: undefined, onsuccess: null, onerror: null }; setTimeout(() => r.onsuccess?.(), 0); return r; },
        });
        return tx;
      },
    };
    setTimeout(() => { req.onupgradeneeded?.(); req.onsuccess?.(); }, 0);
    return req;
  },
};

// The network the sheet talks to. Synthetic coordinates only (SPEC-v2 8.1, rule 6).
const SITE = { name: 'Test field', is_default: false, horizon_min_deg: 15, horizon_points: [[0, 5], [180, 8]] as [number, number][],
  latitude: 44.25, longitude: -121.75, elevation_m: 250 };
const LOCATION = { id: 'loc1', name: 'Back Lawn', latitude: 33.75, longitude: -112.5, elevation_m: 400, horizon_min_deg: 15,
  horizon_points: [[0, 5], [180, 8]] as [number, number][], created_ts: 0, updated_ts: 0 };
let siteBody: Record<string, unknown> = SITE;
const posts: { url: string; body: any }[] = [];
const ok = (data: unknown) => ({ ok: true, status: 200, statusText: 'OK', json: async () => data });
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const u = String(url), method = (init?.method ?? 'GET').toUpperCase();
  if (method !== 'GET') posts.push({ url: u, body: init?.body ? JSON.parse(init.body) : null });
  if (u.includes('/api/locations') && method === 'GET') return ok([LOCATION]);
  if (u.includes('/api/site') && method === 'GET') return ok({ site: siteBody, version: 1 });
  if (u.includes('/api/config')) return ok({ version: 2, site: { is_default: true, horizon_min_deg: 15 }, safety: { horizon: [] } });
  return ok({});
};

const { createElement, act, StrictMode } = await import('react');
const { createRoot } = await import('react-dom/client');
const { useStore } = await import('../../../../../../store');
const { HorizonSheet } = await import('../../horizon');
const { draftFromPoints } = await import('../../horizonDraft');
const { panoFlag, PANO_FLAG_KEY } = await import('../flag');
const { PanoCapture } = await import('../PanoCapture');
const { PanoramaScanner } = await import('../scanner');
const { ERROR_TEXT, CUE_TEXT } = await import('../copy');
const { declinationDeg, decimalYear } = await import('../wmm');

// ---- Harness ---------------------------------------------------------------------------------------------------

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }

const settle = async () => { for (let i = 0; i < 6; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)); }); };
/** One animation frame and the task after it: ScanView's draw, and PanoCapture's "after the paint". */
const paint = async () => { await act(async () => { flushRaf(); await new Promise(r => setTimeout(r, 0)); }); };
const byTest = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
const must = (id: string): HTMLElement => { const el = byTest(id); assert.ok(el, `no element with data-testid="${id}"`); return el!; };
const click = async (el: HTMLElement) => { await act(async () => { el.click(); }); await settle(); };
/** Absence, asserted on a boolean: a failed `assert.equal(element, null)` would print the whole jsdom element. */
const absent = (id: string, msg?: string) => assert.ok(byTest(id) === null, msg ?? `${id} should not be in the document`);
const locked = (id: string) => must(id).getAttribute('aria-disabled') === 'true';
const text = (id: string) => must(id).textContent ?? '';
const decode = (href: string) => decodeURIComponent(href.slice(href.indexOf(',') + 1));

function resetPage() {
  w.localStorage.clear();
  w.history.replaceState(null, '', '/#/');
  Object.defineProperty(w.document, 'visibilityState', { value: 'visible', configurable: true });
}

const BAND0 = { green: [12, 23.36] as [number, number], amber: [5, 25.36] as [number, number] };
function baseStatus(): ScanStatus {
  return {
    phase: 'ready', cue: CUE_TEXT.ready, cueKey: 'ready', cueKind: 'info', error: null,
    frameNo: 0, headingDeg: null, northOffsetDeg: null, elevationDeg: 20, rollDeg: 0, rateDegS: 0,
    targetPitchDeg: 20, pitchBand: BAND0, pace: 'idle', rateMaxDegS: 40,
    coverage: new Uint8Array(720), coveredDeg: 0, gapDeg: null, tallSpans: [],
    mode: 'absolute-only', north: null, focal: { state: 'prior', shortFovDeg: 43 },
    loop: { closed: false, method: null, preDeg: null, postDeg: null, match: null, unwrappedDeg: 0 },
    keyframes: 0, portrait: true, canBegin: true, recording: false, farbled: null,
  };
}

const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=';
function resultOf(points: HorizonPoint[], over: Partial<ScanResult> = {}): ScanResult {
  return {
    png: PNG, rgba: new Uint8ClampedArray(0), draft: draftFromPoints(points), points, tau: 0.5, north: null, declinationApplied: false,
    focal: { state: 'prior', sdPct: 20, shortFovDeg: 43 },
    loop: { closed: false, method: null, preDeg: null, postDeg: null, match: null, unwrappedDeg: 0 },
    extractor: 'tracer', partial: true, endedBy: 'user', error: null, ...over,
  };
}

const REPORT = { format: 'astrodeck-pano-report', version: 2, marker: 'fake-report' } as unknown as ScanReport;

/** A scanner whose status the test sets and whose every call it records, in order, in `log`. */
class FakeScanner implements ScannerLike, PanoScannerControl {
  status: ScanStatus = baseStatus();
  canBegin = true;
  cameraChoices: readonly { deviceId: string; label: string }[] = [];
  activeCameraId: string | undefined = undefined;
  readonly log: string[] = [];
  readonly listeners = new Set<() => void>();
  readonly startArgs: [HTMLVideoElement, string | undefined][] = [];
  readonly finishArgs: { previous: readonly HorizonPoint[] | null; endedBy: string | undefined }[] = [];
  readonly ticks: number[] = [];
  readonly recordingCalls: boolean[] = [];
  declination: unknown = 'unset';
  startImpl: () => Promise<void> = async () => {};
  result: ScanResult = resultOf([{ az: 10, alt: 12 }]);
  recordingText: string | null = null;
  readonly pix = new Uint8ClampedArray(PANO_W * PANO_H * 4);
  readonly ribbon = { pixels: () => this.pix, takeDirty: () => [], liveMesh: () => null, liveSource: () => null };
  subscribe(cb: () => void) { this.listeners.add(cb); return () => { this.listeners.delete(cb); }; }
  emit() { for (const cb of [...this.listeners]) cb(); }
  set(over: Partial<ScanStatus>, canBegin?: boolean) {
    this.status = { ...this.status, ...over };
    if (canBegin !== undefined) this.canBegin = canBegin;
    this.emit();
  }
  start(video: HTMLVideoElement, deviceId?: string) { this.log.push('start'); this.startArgs.push([video, deviceId]); return this.startImpl(); }
  begin() { this.log.push('begin'); }
  pause() { this.log.push('pause'); }
  resume() { this.log.push('resume'); }
  tick(now: number) { this.ticks.push(now); }
  finish(previous: readonly HorizonPoint[] | null, endedBy?: 'user' | 'hidden' | 'error') {
    this.log.push('finish'); this.finishArgs.push({ previous, endedBy }); return this.result;
  }
  stop() { this.log.push('stop'); }
  setDeclination(toTrue: ((az: number) => number) | null) { this.log.push('setDeclination'); this.declination = toTrue; }
  setRecording(on: boolean) { this.log.push(`setRecording:${on}`); this.recordingCalls.push(on); }
  report() { this.log.push('report'); return REPORT; }
  recording() { this.log.push('recording'); return this.recordingText; }
  noteMeshDraw() {}
}

/** Mount PanoCapture over `fake` in its own container. */
function mountPano(fake: FakeScanner, over: Partial<PanoCaptureProps> = {}, strict = false) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);
  const finishes: { result: ScanResult; links: { report: string; recording: string | null } }[] = [];
  const cancels: { report: string | null; recording: string | null }[] = [];
  const made: { sensorOnly?: boolean }[] = [];
  const props: PanoCaptureProps = {
    previous: [{ az: 0, alt: 5 }], toTrue: null, sensorOnly: false,
    onFinish: (result, links) => { fake.log.push('onFinish'); finishes.push({ result, links }); },
    onCancel: links => { fake.log.push('onCancel'); cancels.push(links); },
    createScanner: o => { made.push(o); return fake; },
    ...over,
  };
  return {
    host, root, finishes, cancels, made, props,
    render: async () => {
      await act(async () => { root.render(strict ? createElement(StrictMode, null, createElement(PanoCapture, props)) : createElement(PanoCapture, props)); });
      await settle();
    },
    rerender: async (next: Partial<PanoCaptureProps>) => { Object.assign(props, next); await act(async () => { root.render(createElement(PanoCapture, { ...props })); }); await settle(); },
    unmount: async () => { await act(async () => { root.unmount(); }); host.remove(); },
  };
}

// ---- 1. The flag -----------------------------------------------------------------------------------------------

await test('flag: off by default; the hash parameter sets pano and pano-sensor and persists; legacy clears it', () => {
  resetPage();
  assert.equal(panoFlag(''), 'off');
  assert.equal(panoFlag('#/sky/horizon'), 'off');
  assert.equal(panoFlag('#/sky?scanner=pano'), 'pano');
  assert.equal(w.localStorage.getItem(PANO_FLAG_KEY), 'pano', 'persists to localStorage[astrodeck.pano.scanner]');
  assert.equal(PANO_FLAG_KEY, 'astrodeck.pano.scanner');
  assert.equal(panoFlag('#/sky'), 'pano', 'a later visit without the parameter keeps it');
  assert.equal(panoFlag('#/sky?site=a&scanner=pano-sensor&az=3'), 'pano-sensor');
  assert.equal(w.localStorage.getItem(PANO_FLAG_KEY), 'pano-sensor');
  assert.equal(panoFlag('#/sky'), 'pano-sensor');
  assert.equal(panoFlag('#/sky?scanner=legacy'), 'off');
  assert.equal(w.localStorage.getItem(PANO_FLAG_KEY), null, 'legacy clears the stored flag');
  assert.equal(panoFlag('#/sky'), 'off');
});

await test('flag: the default reads window.location.hash; an unknown value is no instruction; a bad stored value is off', () => {
  resetPage();
  w.history.replaceState(null, '', '/#/sky/horizon?scanner=pano');
  assert.equal(panoFlag(), 'pano');
  w.history.replaceState(null, '', '/#/sky/horizon?scanner=bogus');
  assert.equal(panoFlag(), 'pano', 'the stored value stands against a value it does not know');
  w.localStorage.setItem(PANO_FLAG_KEY, 'maybe');
  assert.equal(panoFlag('#/sky'), 'off');
  w.localStorage.setItem(PANO_FLAG_KEY, 'off');
  assert.equal(panoFlag('#/sky'), 'off');
  resetPage();
});

await test('flag: storage that throws or is missing never throws out of panoFlag', () => {
  resetPage();
  const real = Object.getOwnPropertyDescriptor(g, 'localStorage')!;
  Object.defineProperty(g, 'localStorage', { get() { throw new Error('blocked'); }, configurable: true });
  try {
    assert.equal(panoFlag('#/sky?scanner=pano'), 'pano', 'the hash still decides');
    assert.equal(panoFlag('#/sky'), 'off', 'nothing to read');
    assert.equal(panoFlag('#/sky?scanner=legacy'), 'off');
  } finally {
    Object.defineProperty(g, 'localStorage', real);
  }
  resetPage();
});

// ---- 2. PanoCapture over a fake scanner -----------------------------------------------------------------------

await test('PanoCapture renders ScanView with the <video> visible in its slot, starts the scanner on mount, gives it the declination', async () => {
  resetPage();
  const fake = new FakeScanner();
  const toTrue = (az: number) => az + 4.2468;
  const m = mountPano(fake, { toTrue });
  await m.render();
  assert.ok(byTest('pano-view'), 'ScanView renders');
  const video = must('pano-video') as HTMLVideoElement;
  assert.ok(must('pano-preview').contains(video), 'the video sits in ScanView\'s preview slot');
  assert.equal(video.hidden, false);
  assert.equal(video.style.display, '');
  assert.equal(video.getAttribute('aria-label'), 'Live surroundings camera');
  assert.deepEqual(fake.log.slice(0, 2).sort(), ['setDeclination', 'start']);
  assert.equal(fake.startArgs.length, 1);
  assert.equal(fake.startArgs[0][0], video, 'start() is given the rendered element');
  assert.equal(fake.startArgs[0][1], undefined, 'no stored camera: the default rear camera');
  assert.equal(fake.declination, toTrue, 'the closure the sheet built goes to the scanner untouched');
  assert.deepEqual(m.made, [{ sensorOnly: false }]);
  assert.ok(!document.body.innerHTML.includes('4.2468') && !document.body.innerHTML.includes('az + '), 'neither the closure nor its value is rendered');
  await m.unmount();
});

await test('PanoCapture passes sensorOnly to the scanner it creates, and a new toTrue to the same scanner', async () => {
  resetPage();
  const fake = new FakeScanner();
  const m = mountPano(fake, { sensorOnly: true });
  await m.render();
  assert.deepEqual(m.made, [{ sensorOnly: true }]);
  const next = (az: number) => az + 1;
  await m.rerender({ toTrue: next });
  assert.equal(fake.declination, next);
  assert.equal(m.made.length, 1, 'one scanner for the life of the mount');
  await m.rerender({ toTrue: null });
  assert.equal(fake.declination, null);
  await m.unmount();
});

await test('the stored camera choice is used, remembered once the camera is open, and cleared on OverconstrainedError and NotFoundError', async () => {
  resetPage();
  w.localStorage.setItem('astrodeck.photosphere.camera', 'cam-b');
  const fake = new FakeScanner();
  fake.startImpl = async () => { fake.activeCameraId = 'cam-a'; };
  let m = mountPano(fake);
  await m.render();
  assert.equal(fake.startArgs[0][1], 'cam-b', 'today\'s stored choice');
  assert.equal(w.localStorage.getItem('astrodeck.photosphere.camera'), 'cam-a', 'the camera that opened is remembered');
  await m.unmount();

  for (const name of ['OverconstrainedError', 'NotFoundError']) {
    w.localStorage.setItem('astrodeck.photosphere.camera', 'gone');
    const f = new FakeScanner();
    f.startImpl = () => Promise.reject(Object.assign(new Error('no such device'), { name }));
    m = mountPano(f);
    await m.render();
    assert.equal(w.localStorage.getItem('astrodeck.photosphere.camera'), null, `${name} clears the stored choice`);
    assert.equal(text('pano-open-error'), 'no such device', 'and the browser\'s own message is shown when the scanner has none');
    await m.unmount();
  }

  w.localStorage.setItem('astrodeck.photosphere.camera', 'kept');
  const denied = new FakeScanner();
  denied.startImpl = () => Promise.reject(Object.assign(new Error('x'), { name: 'NotAllowedError' }));
  m = mountPano(denied);
  await m.render();
  assert.equal(w.localStorage.getItem('astrodeck.photosphere.camera'), 'kept', 'a refusal of permission is no reason to forget the lens');
  assert.match(text('pano-open-error'), /Camera access was blocked/);
  await m.unmount();
});

await test('the camera select lists the lenses, restarts the scanner on a choice, and is a locked chip while a stream opens', async () => {
  resetPage();
  const fake = new FakeScanner();
  fake.cameraChoices = [{ deviceId: 'cam-a', label: 'Back main' }, { deviceId: 'cam-b', label: 'Back ultra' }];
  fake.activeCameraId = 'cam-a';
  const m = mountPano(fake);
  await m.render();
  const select = must('pano-camera') as HTMLSelectElement;
  assert.deepEqual([...select.options].map(o => o.value), ['cam-a', 'cam-b']);
  assert.equal(select.value, 'cam-a');
  await act(async () => { select.value = 'cam-b'; select.dispatchEvent(new w.Event('change', { bubbles: true })); });
  await settle();
  assert.equal(fake.startArgs.length, 2);
  assert.equal(fake.startArgs[1][1], 'cam-b');
  await act(async () => { fake.set({ phase: 'opening' }, false); });
  absent('pano-camera');
  assert.equal(must('pano-camera-locked').getAttribute('aria-disabled'), 'true');
  assert.match(text('pano-opening'), /^Opening camera$/);
  await act(async () => { fake.set({ phase: 'scanning', keyframes: 1 }, true); });
  absent('pano-camera', 'no lens change once the scan has begun');
  await m.unmount();
});

await test('Start is locked until canBegin, with the reason shown, and is enabled without a compass', async () => {
  resetPage();
  const fake = new FakeScanner();
  fake.set({ phase: 'opening' }, false);
  const m = mountPano(fake);
  await m.render();
  assert.equal(locked('pano-start'), true);
  assert.equal(text('pano-start-reason'), 'Opening camera');
  assert.equal(must('pano-start').getAttribute('title'), 'Opening camera');
  await act(async () => { must('pano-start').click(); });
  assert.ok(!fake.log.includes('begin'), 'a locked Start does not begin');

  await act(async () => { fake.set({ phase: 'ready' }, false); });
  assert.match(text('pano-start-reason'), /Waiting for the camera and the motion sensor/);
  assert.equal(locked('pano-start'), true);

  // The ready state of a phone with no compass: any orientation sample has arrived, the north estimate has not.
  await act(async () => { fake.set({ phase: 'ready', north: null, mode: 'absolute-only', northOffsetDeg: null }, true); });
  assert.equal(locked('pano-start'), false, 'Start is enabled without a compass');
  absent('pano-start-reason');
  assert.equal(text('pano-sensors'), 'Compass: no reading yet.');
  await act(async () => { fake.set({ north: { offsetDeg: 3, sigmaDeg: 2, spreadDeg: 1, samples: 40, nEff: 4, stable: true, source: 'scan' } }); });
  assert.equal(text('pano-sensors'), 'Compass: ready.');
  await act(async () => { must('pano-start').click(); });
  assert.deepEqual(fake.log.filter(e => e === 'begin' || e.startsWith('setRecording')), ['setRecording:false', 'begin']);
  await m.unmount();
});

await test('an error blocks Start with a pointer to the message, and the message is the scanner\'s cue', async () => {
  resetPage();
  const fake = new FakeScanner();
  fake.set({ phase: 'failed', error: ERROR_TEXT.noOrientation, cue: ERROR_TEXT.noOrientation, cueKey: 'error', cueKind: 'block' }, false);
  const m = mountPano(fake);
  await m.render();
  await paint();
  assert.equal(text('pano-cue'), ERROR_TEXT.noOrientation);
  assert.equal(locked('pano-start'), true);
  assert.match(text('pano-start-reason'), /see the message above/);
  absent('pano-open-error', 'the same error is not shown twice');
  await m.unmount();
});

await test('the Brave blocked cue: a blocked phone that sends nothing shows the blocked text through tick, and the report says blocked', async () => {
  resetPage();
  const camera = stubCamera();
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);
  const cancels: { report: string | null; recording: string | null }[] = [];
  await act(async () => {
    root.render(createElement(PanoCapture, {
      previous: [], toTrue: null, sensorOnly: true, onFinish: () => {}, onCancel: l => { cancels.push(l); },
      createScanner: o => new PanoramaScanner({ ...o, camera }),
    }));
  });
  await settle();
  assert.equal(byTest('pano-capture')!.getAttribute('data-phase'), 'ready', 'the camera is open');
  // Brave's blocked shape: one deviceorientation with every angle null, then silence.
  fakeNow = 100;
  const ev = new w.Event('deviceorientation');
  Object.assign(ev, { alpha: null, beta: null, gamma: null, absolute: null });
  Object.defineProperty(ev, 'timeStamp', { value: 100 });
  w.dispatchEvent(ev);
  fakeNow = 600;
  await act(async () => { liveTicks().at(-1)!.fn(); });
  await paint();
  assert.notEqual(byTest('pano-cue')!.getAttribute('data-key'), 'error', 'not yet: blocked needs a second of nothing');
  fakeNow = 1150;
  await act(async () => { liveTicks().at(-1)!.fn(); });
  await paint();
  fakeNow = null;
  assert.equal(must('pano-cue').getAttribute('data-key'), 'error');
  assert.equal(text('pano-cue'), ERROR_TEXT.motionBlocked, 'the cue is exactly the blocked text of 2.11');
  assert.equal(must('pano-cue').getAttribute('data-kind'), 'block');
  assert.equal(locked('pano-start'), true, 'a blocked phone cannot begin');
  await click(must('pano-cancel'));
  assert.equal(cancels.length, 1);
  const report = JSON.parse(decode(cancels[0].report!));
  assert.equal(report.sensors.blocked, true, 'the report records blocked: true');
  assert.equal(report.scanner.sensorOnly, true, 'sensorOnly reached the real scanner');
  await act(async () => { root.unmount(); });
  host.remove();
});

await test('Start is enabled without a compass: the real scanner begins on a tilt-only sample, and Start moves it to scanning', async () => {
  resetPage();
  const camera = stubCamera();
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(createElement(PanoCapture, {
      previous: [], toTrue: null, sensorOnly: false, onFinish: () => {}, onCancel: () => {},
      createScanner: o => new PanoramaScanner({ ...o, camera }),
    }));
  });
  await settle();
  assert.equal(must('pano-capture').getAttribute('data-phase'), 'ready');
  assert.equal(locked('pano-start'), true, 'no orientation sample yet');
  assert.match(text('pano-start-reason'), /Waiting for the camera and the motion sensor/);
  assert.equal(text('pano-sensors'), 'Compass: no reading yet.');
  // A phone with no compass: one relative orientation event with tilt and no heading, no absolute stream, no motion event.
  const ev = new w.Event('deviceorientation');
  Object.assign(ev, { alpha: null, beta: 75, gamma: 2, absolute: false });
  await act(async () => { w.dispatchEvent(ev); });
  await settle();
  assert.equal(locked('pano-start'), false, 'tilt is all Start needs');
  absent('pano-start-reason');
  assert.equal(text('pano-sensors'), 'Compass: no reading yet.', 'a tilt-only phone says so, and Start is still open');
  await click(must('pano-start'));
  assert.equal(must('pano-capture').getAttribute('data-phase'), 'scanning');
  absent('pano-start');
  absent('pano-sensors', 'the readiness line is for the ready screen');
  await act(async () => { root.unmount(); });
  host.remove();
});

await test('the ultra-wide note shows once a locked focal implies a short axis above 60 degrees, and not before', async () => {
  resetPage();
  const fake = new FakeScanner();
  const m = mountPano(fake);
  await m.render();
  absent('pano-ultrawide');
  await act(async () => { fake.set({ phase: 'scanning', focal: { state: 'prior', shortFovDeg: 64 } }); });
  absent('pano-ultrawide', 'the prior is not a measurement');
  await act(async () => { fake.set({ focal: { state: 'locked', shortFovDeg: 43 } }); });
  absent('pano-ultrawide');
  await act(async () => { fake.set({ focal: { state: 'locked', shortFovDeg: 64 } }); });
  assert.equal(text('pano-ultrawide'), 'This looks like the ultra-wide camera. Choose the main camera.');
  await m.unmount();
});

await test('the farbling note sits under Start and is never a blocking cue', async () => {
  resetPage();
  const fake = new FakeScanner();
  const m = mountPano(fake);
  await m.render();
  absent('pano-farbled');
  await act(async () => { fake.set({ farbled: false }); });
  absent('pano-farbled');
  await act(async () => { fake.set({ farbled: true }); });
  assert.equal(text('pano-farbled'), ERROR_TEXT.farbled);
  assert.equal(must('pano-farbled').getAttribute('role'), 'status');
  const nodes = [...must('pano-capture').querySelectorAll('*')];
  assert.ok(nodes.indexOf(must('pano-farbled')) > nodes.indexOf(must('pano-start')), 'under Start');
  assert.equal(locked('pano-start'), false, 'the scan can still begin');
  await paint();
  assert.notEqual(must('pano-cue').getAttribute('data-key'), 'error', 'the cue is not an error');
  await m.unmount();
});

await test('the record box sets recording before Begin and goes away once the scan has begun', async () => {
  resetPage();
  const fake = new FakeScanner();
  const m = mountPano(fake);
  await m.render();
  const box = must('pano-record');
  assert.equal(box.getAttribute('aria-checked'), 'false', 'off by default');
  assert.match(box.textContent!, /Record this scan for diagnosis/);
  assert.match(document.body.textContent!, /Saves every camera picture and sensor reading of this scan in a file on this phone, to help fix the scanner\. It includes photos of your surroundings\. Use it for the S25 check\./);
  await click(box);
  assert.equal(must('pano-record').getAttribute('aria-checked'), 'true');
  assert.deepEqual(fake.recordingCalls, [true]);
  await click(must('pano-start'));
  assert.deepEqual(fake.log.filter(e => e === 'begin' || e.startsWith('setRecording')), ['setRecording:true', 'setRecording:true', 'begin'], 'the box is read again at Start, before begin()');
  await act(async () => { fake.set({ phase: 'scanning' }); });
  absent('pano-record');
  await m.unmount();
});

await test('after Start: Review waits for the first keyframe and reads partial until the ring closes; Pause and Resume; Cancel', async () => {
  resetPage();
  const fake = new FakeScanner();
  const m = mountPano(fake);
  await m.render();
  absent('pano-review', 'before Start there is no Review');
  absent('pano-pause');
  assert.ok(byTest('pano-cancel'), 'Cancel is there from the start');
  await act(async () => { fake.set({ phase: 'scanning', keyframes: 0 }); });
  absent('pano-start');
  assert.equal(text('pano-review'), 'Review partial scan');
  assert.equal(locked('pano-review'), true);
  assert.match(must('pano-review').getAttribute('title')!, /first picture is kept/);
  await act(async () => { must('pano-review').click(); });
  await paint();
  assert.ok(!fake.log.includes('finish'), 'a locked Review does not finish');
  await act(async () => { fake.set({ keyframes: 1 }); });
  assert.equal(locked('pano-review'), false, 'enabled after the first keyframe');
  assert.equal(text('pano-review'), 'Review partial scan');
  await act(async () => { fake.set({ keyframes: 80, loop: { ...fake.status.loop, closed: true } }); });
  assert.equal(text('pano-review'), 'Review', 'the word partial goes once the ring has closed');
  assert.equal(text('pano-pause'), 'Pause');
  await click(must('pano-pause'));
  assert.deepEqual(fake.log.slice(-1), ['pause']);
  await act(async () => { fake.set({ phase: 'paused' }); });
  assert.equal(text('pano-pause'), 'Resume');
  await click(must('pano-pause'));
  assert.deepEqual(fake.log.slice(-1), ['resume']);
  await m.unmount();
});

await test('Review shows "Working out the horizon" first, then finishes as the user with the previous line and hands over both links', async () => {
  resetPage();
  const fake = new FakeScanner();
  fake.set({ phase: 'scanning', keyframes: 3 });
  fake.recordingText = '{"kind":"header"}\n{"kind":"report"}';
  const previous: HorizonPoint[] = [{ az: 0, alt: 5 }, { az: 180, alt: 8 }];
  const m = mountPano(fake, { previous });
  await m.render();
  await act(async () => { must('pano-review').click(); });
  assert.equal(text('pano-working'), 'Working out the horizon', 'on screen before the synchronous work');
  assert.ok(!fake.log.includes('finish'), 'finish waits for the paint');
  assert.equal(locked('pano-review'), true, 'a second tap does nothing');
  await paint();
  assert.equal(fake.finishArgs.length, 1);
  assert.equal(fake.finishArgs[0].previous, previous, 'the previous line goes in as it is');
  assert.equal(fake.finishArgs[0].endedBy, 'user');
  assert.equal(m.finishes.length, 1);
  assert.equal(m.finishes[0].result, fake.result);
  const { report, recording } = m.finishes[0].links;
  assert.ok(report.startsWith('data:application/json;charset=utf-8,'));
  assert.deepEqual(JSON.parse(decode(report)), REPORT);
  assert.ok(recording!.startsWith('data:application/x-ndjson;charset=utf-8,'));
  assert.equal(decode(recording!), fake.recordingText);
  assert.ok(fake.log.indexOf('finish') < fake.log.indexOf('report') && fake.log.lastIndexOf('report') < fake.log.indexOf('onFinish'));
  assert.ok(!fake.log.includes('stop'), 'the scanner is stopped by the unmount, after the links are taken');
  await m.unmount();
  assert.deepEqual(fake.log.slice(-1), ['stop']);
  assert.equal(fake.finishArgs.length, 1, 'finish ran once');
});

await test('Review with no recording hands over a null recording link', async () => {
  resetPage();
  const fake = new FakeScanner();
  fake.set({ phase: 'scanning', keyframes: 1 });
  const m = mountPano(fake);
  await m.render();
  await act(async () => { must('pano-review').click(); });
  await paint();
  assert.equal(m.finishes[0].links.recording, null);
  await m.unmount();
});

await test('Cancel hands over the report and the recording BEFORE the scanner stops, and keeps the report even with nothing scanned', async () => {
  resetPage();
  const fake = new FakeScanner();
  fake.recordingText = 'recorded';
  const m = mountPano(fake);
  await m.render();
  await click(must('pano-cancel'));
  assert.equal(m.cancels.length, 1);
  assert.deepEqual(JSON.parse(decode(m.cancels[0].report!)), REPORT);
  assert.equal(decode(m.cancels[0].recording!), 'recorded');
  assert.ok(!fake.log.includes('finish'), 'Cancel discards the scan');
  assert.ok(fake.log.indexOf('report') >= 0 && fake.log.indexOf('report') < fake.log.indexOf('onCancel'), 'the report is read before the sheet is told');
  assert.ok(!fake.log.includes('stop'), 'the scanner is still running when the links are taken');
  await m.unmount();
  assert.deepEqual(fake.log.slice(-1), ['stop']);
});

await test('Cancel while "Working out the horizon" is pending cancels the pending finish', async () => {
  resetPage();
  const fake = new FakeScanner();
  fake.set({ phase: 'scanning', keyframes: 2 });
  const m = mountPano(fake);
  await m.render();
  await act(async () => { must('pano-review').click(); });
  await click(must('pano-cancel'));
  await paint(); await paint();
  assert.ok(!fake.log.includes('finish'));
  assert.equal(m.finishes.length, 0);
  assert.equal(m.cancels.length, 1);
  await m.unmount();
});

await test('a report that cannot be built does not block the way out', async () => {
  resetPage();
  const fake = new FakeScanner();
  fake.report = () => { throw new Error('no report today'); };
  fake.set({ phase: 'scanning', keyframes: 2 });
  const m = mountPano(fake);
  await m.render();
  await act(async () => { must('pano-review').click(); });
  await paint();
  assert.equal(m.finishes.length, 1);
  assert.match(decode(m.finishes[0].links.report), /report unavailable: no report today/);
  await m.unmount();
});

await test('a finish that throws keeps the scan open with its message; Review tries again with the newest previous line', async () => {
  resetPage();
  const fake = new FakeScanner();
  fake.set({ phase: 'scanning', keyframes: 3 });
  const good = fake.result;
  let calls = 0;
  fake.finish = (previous, endedBy) => {
    fake.log.push('finish'); fake.finishArgs.push({ previous, endedBy });
    if (++calls === 1) throw new Error('out of memory');
    return good;
  };
  const m = mountPano(fake, { previous: [{ az: 0, alt: 5 }] });
  await m.render();
  await act(async () => { must('pano-review').click(); });
  await paint();
  assert.equal(m.finishes.length, 0, 'nothing is handed over');
  assert.equal(text('pano-open-error'), 'out of memory');
  absent('pano-working');
  assert.equal(locked('pano-review'), false, 'the user can tap Review again');
  const newer: HorizonPoint[] = [{ az: 0, alt: 5 }, { az: 90, alt: 9 }];
  await m.rerender({ previous: newer });
  await act(async () => { must('pano-review').click(); });
  await paint();
  assert.equal(m.finishes.length, 1);
  assert.equal(fake.finishArgs[1].previous, newer, 'the line the sheet holds now, not the one it held at mount');
  await m.unmount();
});

await test('a hidden tab ends a begun scan with finish(previous, "hidden") and hands over the links', async () => {
  resetPage();
  const fake = new FakeScanner();
  const previous: HorizonPoint[] = [{ az: 90, alt: 11 }];
  const m = mountPano(fake, { previous });
  await m.render();
  const hide = async () => {
    Object.defineProperty(w.document, 'visibilityState', { value: 'hidden', configurable: true });
    await act(async () => { w.document.dispatchEvent(new w.Event('visibilitychange')); });
  };
  // Before Start there is nothing to keep, and a tab that comes back to visible changes nothing.
  await hide();
  assert.ok(!fake.log.includes('finish'), 'a hidden tab before Start does not finish');
  Object.defineProperty(w.document, 'visibilityState', { value: 'visible', configurable: true });
  await act(async () => { fake.set({ phase: 'scanning', keyframes: 4 }); });
  await act(async () => { w.document.dispatchEvent(new w.Event('visibilitychange')); });
  assert.ok(!fake.log.includes('finish'), 'visible is not hidden');
  await hide();
  assert.equal(fake.finishArgs.length, 1);
  assert.equal(fake.finishArgs[0].endedBy, 'hidden');
  assert.equal(fake.finishArgs[0].previous, previous);
  assert.equal(m.finishes.length, 1, 'the sheet is handed the result at once');
  assert.deepEqual(JSON.parse(decode(m.finishes[0].links.report)), REPORT);
  await hide();
  assert.equal(fake.finishArgs.length, 1, 'a second hide finishes nothing more');
  await m.unmount();
  resetPage();
});

await test('a hidden tab during a pause also ends the scan, and one that finished by Review is not finished twice', async () => {
  resetPage();
  const fake = new FakeScanner();
  fake.set({ phase: 'paused', keyframes: 2 });
  const m = mountPano(fake);
  await m.render();
  await act(async () => { must('pano-review').click(); });
  await paint();
  Object.defineProperty(w.document, 'visibilityState', { value: 'hidden', configurable: true });
  await act(async () => { w.document.dispatchEvent(new w.Event('visibilitychange')); });
  assert.equal(fake.finishArgs.length, 1);
  assert.equal(fake.finishArgs[0].endedBy, 'user');
  await m.unmount();
  resetPage();
  const fake2 = new FakeScanner();
  fake2.set({ phase: 'paused', keyframes: 2 });
  const m2 = mountPano(fake2);
  await m2.render();
  Object.defineProperty(w.document, 'visibilityState', { value: 'hidden', configurable: true });
  await act(async () => { w.document.dispatchEvent(new w.Event('visibilitychange')); });
  assert.deepEqual(fake2.finishArgs.map(a => a.endedBy), ['hidden']);
  await m2.unmount();
  resetPage();
});

await test('a 500 ms tick interval runs scanner.tick; unmount stops the scanner, clears the interval and drops the visibility listener', async () => {
  resetPage();
  const fake = new FakeScanner();
  const before = intervals.length;
  const m = mountPano(fake);
  await m.render();
  const mine = intervals.slice(before).filter(r => r.ms === 500);
  assert.equal(mine.length, 1, 'one tick interval, every 500 ms');
  fakeNow = 4321;
  mine[0].fn();
  fakeNow = null;
  assert.deepEqual(fake.ticks, [4321], 'tick is given performance.now()');
  await act(async () => { fake.set({ phase: 'scanning', keyframes: 1 }); });
  await m.unmount();
  assert.ok(fake.log.includes('stop'), 'unmount stops the scanner');
  assert.equal(mine[0].cleared, true, 'and clears the tick interval');
  assert.equal(fake.listeners.size, 0, 'and unsubscribes');
  Object.defineProperty(w.document, 'visibilityState', { value: 'hidden', configurable: true });
  w.document.dispatchEvent(new w.Event('visibilitychange'));
  assert.ok(!fake.log.includes('finish'), 'a hidden tab after unmount finishes nothing');
  resetPage();
});

await test('under StrictMode (the dev build) the scanner ends up started and not stopped, with one live tick interval', async () => {
  resetPage();
  const fake = new FakeScanner();
  fake.startImpl = async () => { fake.activeCameraId = 'cam-a'; };
  const before = intervals.length;
  const m = mountPano(fake, {}, true);
  await m.render();
  const lifecycle = fake.log.filter(e => e === 'start' || e === 'stop');
  assert.equal(lifecycle[lifecycle.length - 1], 'start', 'the last word is start: a scan that was started, stopped and started again');
  assert.ok(lifecycle.includes('stop'), 'the double mount really happened');
  assert.equal(intervals.slice(before).filter(r => r.ms === 500 && !r.cleared).length, 1, 'one live tick interval');
  assert.equal(w.localStorage.getItem('astrodeck.photosphere.camera'), 'cam-a', 'the camera of the second start is remembered');
  await m.unmount();
  assert.equal(fake.log[fake.log.length - 1], 'stop');
});

await test('PanoCapture is store-free and renders a scanner that is replaced on no render: one scanner per mount', async () => {
  resetPage();
  const fake = new FakeScanner();
  const m = mountPano(fake);
  await m.render();
  for (let i = 0; i < 3; i++) await m.rerender({ previous: [{ az: i, alt: 1 }] });
  assert.equal(m.made.length, 1);
  assert.equal(fake.startArgs.length, 1, 'start runs once, on mount');
  await m.unmount();
});

// ---- 3. HorizonSheet, flag off and on --------------------------------------------------------------------------

const PRECISE = { role: 'admin', email: 'a@example.test', caps: ['view.status', 'config.site_optics', 'config.safety', 'view.site_precise'] };
const NO_PRECISE = { role: 'operator', email: 'o@example.test', caps: ['view.status', 'config.safety', 'config.site_optics'] };
function seed(principal: unknown = PRECISE) {
  useStore.setState({
    principal, wsPhase: 'up', equipConnected: true,
    status: { connected: {}, looping: false, mode: 'sim', busy: null, busy_lanes: [] },
    config: { version: 1, site: { is_default: true, horizon_min_deg: 15 }, safety: { horizon: [] } },
  } as never);
}

/** Put `fake` behind PanoramaScanner's prototype: every instance the sheet creates behaves as `fake`. */
const FAKED_METHODS = ['start', 'begin', 'pause', 'resume', 'tick', 'finish', 'stop', 'setDeclination', 'setRecording', 'report', 'recording', 'subscribe', 'noteMeshDraw'];
const FAKED_GETTERS = ['status', 'canBegin', 'cameraChoices', 'activeCameraId'];
const created: unknown[] = [];
let installed: Map<string, PropertyDescriptor | undefined> | null = null;
function installFake(fake: FakeScanner) {
  const proto = PanoramaScanner.prototype as any;
  installed = new Map();
  created.length = 0;
  for (const name of FAKED_METHODS) {
    installed.set(name, Object.getOwnPropertyDescriptor(proto, name));
    Object.defineProperty(proto, name, { configurable: true, writable: true, value(this: unknown, ...args: unknown[]) { if (!created.includes(this)) created.push(this); return (fake as any)[name](...args); } });
  }
  for (const name of FAKED_GETTERS) {
    installed.set(name, Object.getOwnPropertyDescriptor(proto, name));
    Object.defineProperty(proto, name, { configurable: true, get() { return (fake as any)[name]; } });
  }
}
function uninstallFake() {
  const proto = PanoramaScanner.prototype as any;
  for (const [name, d] of installed ?? []) { if (d) Object.defineProperty(proto, name, d); }
  installed = null;
}

async function mountSheet(opts: { hash?: string; params?: Record<string, string> } = {}) {
  resetPage();
  if (opts.hash) w.history.replaceState(null, '', `/${opts.hash}`);
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(createElement(HorizonSheet, { params: opts.params ?? { site: 'current' }, depth: 0 } as any)); });
  await settle();
  return { host, root, unmount: async () => { await act(async () => { root.unmount(); }); host.remove(); } };
}

/** `new Date()` with no argument answers a fixed day while the clock is pinned. WMM2025 holds for 2025.0-2030.0 only and the
 *  sheet leaves the panorama magnetic outside it, so a case that read the real clock would turn red on 2030-01-01 with
 *  nothing wrong in the code under test. Returns the function that puts the real clock back. */
const PINNED_NOW = Date.UTC(2026, 9, 10, 12);
function pinClock(at: number = PINNED_NOW): () => void {
  const RealDate = Date;
  class PinnedDate extends RealDate {
    constructor(...args: unknown[]) {
      if (args.length === 0) super(at);
      else super(...(args as [number]));
    }
  }
  g.Date = PinnedDate;
  return () => { g.Date = RealDate; };
}

/** Every console line, kept, while the spy is on: the declination must reach no log (SPEC-v2 4.12). Returns the function
 *  that puts the real console back. */
const logged: string[] = [];
function spyConsole(): () => void {
  const real = { log: console.log, info: console.info, warn: console.warn, error: console.error, debug: console.debug };
  logged.length = 0;
  for (const k of Object.keys(real) as (keyof typeof real)[]) console[k] = (...args: unknown[]) => { logged.push(args.map(String).join(' ')); };
  return () => { Object.assign(console, real); };
}

const permissionCalls: string[] = [];
function grantPermission(answer: 'granted' | 'denied') {
  permissionCalls.length = 0;
  w.DeviceOrientationEvent.requestPermission = () => { permissionCalls.push('orientation'); return Promise.resolve(answer); };
  w.DeviceMotionEvent.requestPermission = () => { permissionCalls.push('motion'); return Promise.resolve(answer); };
}
function noPermissionApi() {
  delete w.DeviceOrientationEvent.requestPermission;
  delete w.DeviceMotionEvent.requestPermission;
}

await test('flag off: the legacy panel renders, with no PanoCapture, no scan line and no iOS permission asked by the sheet', async () => {
  seed();
  siteBody = SITE;
  grantPermission('granted');
  const s = await mountSheet();
  absent('pano-scan-line', 'the 2.2 line is flagged');
  assert.ok(byTest('photosphere-capturing'), 'the legacy panel is in the document');
  assert.equal(must('photosphere-capturing').hidden, true, 'and hidden until the scan button is tapped');
  await act(async () => { must('capture-photosphere').click(); });
  assert.equal(must('photosphere-capturing').hidden, false, 'the legacy panel opens');
  assert.ok(byTest('photosphere-video'));
  absent('pano-capture');
  absent('pano-view');
  await s.unmount();
  noPermissionApi();
});

await test('flag on: the scan button opens PanoCapture in place of the legacy panel, under the copy line of 2.2', async () => {
  seed();
  siteBody = SITE;
  noPermissionApi();
  const fake = new FakeScanner();
  installFake(fake);
  try {
    const s = await mountSheet({ hash: '#/sky/horizon?scanner=pano&cameraFov=70' });
    assert.equal(text('pano-scan-line'), 'Stand where the telescope stands. One slow turn takes about 20 seconds.');
    absent('pano-capture', 'closed until the scan button is tapped');
    await click(must('capture-photosphere'));
    assert.ok(byTest('pano-capture'), 'PanoCapture opens');
    assert.ok(byTest('pano-view'), 'with ScanView');
    absent('photosphere-capturing', 'in place of the legacy capture panel');
    absent('photosphere-video');
    absent('capture-photosphere', 'the scan button gives way while the scan is open');
    absent('pano-scan-line');
    assert.ok(!/Camera alignment|view angle|Try scan angle/i.test(document.body.textContent!), 'no view-angle controls, no cameraFov trial');
    assert.equal(fake.startArgs.length, 1, 'the scanner is started');
    assert.equal((created[0] as any).sensorOnly, false);
    await s.unmount();
    assert.ok(fake.log.includes('stop'), 'closing the sheet stops the scanner');
  } finally { uninstallFake(); }
});

await test('flag on through localStorage alone, and pano-sensor builds a sensor-only scanner', async () => {
  seed();
  const fake = new FakeScanner();
  installFake(fake);
  try {
    resetPage();
    w.localStorage.setItem(PANO_FLAG_KEY, 'pano-sensor');
    const host = document.createElement('div'); document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => { root.render(createElement(HorizonSheet, { params: { site: 'current' }, depth: 0 } as any)); });
    await settle();
    assert.ok(byTest('pano-scan-line'));
    await click(must('capture-photosphere'));
    assert.ok(byTest('pano-capture'));
    assert.equal((created[0] as any).sensorOnly, true, 'the control: alignment disabled');
    await act(async () => { root.unmount(); });
    host.remove();
  } finally { uninstallFake(); resetPage(); }
});

await test('flag on: a browser with no orientation API gets the scan button locked with the 2.11 text; flag off is not locked by it', async () => {
  seed(); siteBody = SITE; noPermissionApi();
  const fake = new FakeScanner();
  installFake(fake);
  const had = w.DeviceOrientationEvent;
  try {
    delete w.DeviceOrientationEvent;
    let s = await mountSheet({ hash: '#/sky/horizon?scanner=pano' });
    assert.equal(locked('capture-photosphere'), true);
    assert.equal(ERROR_TEXT.noOrientation, "This browser does not report the phone's tilt. Draw the horizon by hand.");
    assert.equal(must('capture-photosphere').getAttribute('title'), ERROR_TEXT.noOrientation);
    await click(must('capture-photosphere'));
    absent('pano-capture', 'a locked button opens nothing');
    assert.equal(fake.startArgs.length, 0);
    await s.unmount();

    s = await mountSheet({ hash: '#/sky/horizon?scanner=legacy' });
    assert.equal(locked('capture-photosphere'), false, 'the old scanner never had this lock');
    await s.unmount();
  } finally { w.DeviceOrientationEvent = had; uninstallFake(); resetPage(); }
});

await test('the tap asks for iOS motion permission synchronously, before any state update or await', async () => {
  seed();
  siteBody = SITE;
  grantPermission('granted');
  const fake = new FakeScanner();
  installFake(fake);
  try {
    const s = await mountSheet({ hash: '#/sky/horizon?scanner=pano' });
    // A synchronous act: nothing but the click handler runs before the assertions.
    act(() => { must('capture-photosphere').click(); });
    assert.deepEqual(permissionCalls, ['orientation', 'motion'], 'both prompts were requested inside the tap');
    absent('pano-capture', 'PanoCapture waits for the answer');
    assert.ok(byTest('capture-photosphere'), 'the scan button has not been replaced yet');
    await settle();
    assert.ok(byTest('pano-capture'), 'a yes opens the scan');
    await s.unmount();
  } finally { uninstallFake(); noPermissionApi(); }
});

await test('a denied motion permission shows the 2.11 text instead of starting', async () => {
  seed();
  siteBody = SITE;
  grantPermission('denied');
  const fake = new FakeScanner();
  installFake(fake);
  try {
    const s = await mountSheet({ hash: '#/sky/horizon?scanner=pano' });
    await click(must('capture-photosphere'));
    absent('pano-capture', 'no scan opens');
    assert.equal(fake.startArgs.length, 0, 'no scanner starts');
    assert.equal(document.querySelector('[role="alert"]')!.textContent, ERROR_TEXT.motionDenied);
    assert.equal(ERROR_TEXT.motionDenied, 'Motion access was denied. Allow motion sensors to scan, or draw the horizon by hand.');
    assert.ok(byTest('capture-photosphere'), 'the user can try again');
    grantPermission('granted');
    await click(must('capture-photosphere'));
    assert.ok(byTest('pano-capture'), 'and a later yes opens it');
    assert.ok(document.querySelector('[role="alert"]') === null, 'the old message is gone');
    await s.unmount();
  } finally { uninstallFake(); noPermissionApi(); }
});

await test('toTrue is built only for a role that may see precise site coordinates, from the site the sheet loaded, and never renders', async () => {
  const fake = new FakeScanner();
  installFake(fake);
  const unpin = pinClock();
  const unspy = spyConsole();
  const hash = '#/sky/horizon?scanner=pano';
  try {
    console.warn('spy probe');
    assert.deepEqual(logged, ['spy probe'], 'the spy sees the console');
    // An owner (view.site_precise), the active site: a function that adds the WMM2025 declination at that position.
    seed(PRECISE); siteBody = SITE; noPermissionApi();
    let s = await mountSheet({ hash });
    await click(must('capture-photosphere'));
    assert.equal(typeof fake.declination, 'function');
    const now = new Date();
    const want = declinationDeg(SITE.latitude, SITE.longitude, SITE.elevation_m / 1000, decimalYear(now));
    const got = (fake.declination as (az: number) => number)(100) - 100;
    assert.ok(Math.abs(got - want) < 1e-6, 'the declination at the loaded site and today');
    assert.ok(Math.abs(got) > 0.5, 'a real value, so the privacy assertions below have something to find');
    for (const needle of [got.toFixed(3), got.toFixed(2), String(got), want.toFixed(3), String(SITE.latitude), String(SITE.longitude)]) {
      assert.ok(!document.body.innerHTML.includes(needle), `${needle.length} chars of the value or the position appear in the page`);
    }
    assert.ok(!document.body.innerHTML.includes('azMagDeg') && !/=>/.test(document.body.innerHTML), 'the closure is not rendered');
    assert.ok(!logged.some(l => l.includes(got.toFixed(2)) || l.includes(String(got).slice(0, 7))), 'the declination reaches no console line');
    await s.unmount();

    // A saved location (the other load branch).
    fake.declination = 'unset';
    seed(PRECISE); noPermissionApi();
    s = await mountSheet({ hash, params: { site: 'loc1' } });
    await click(must('capture-photosphere'));
    assert.equal(typeof fake.declination, 'function');
    const wantLoc = declinationDeg(LOCATION.latitude, LOCATION.longitude, LOCATION.elevation_m / 1000, decimalYear(new Date()));
    assert.ok(Math.abs((fake.declination as (az: number) => number)(0) - wantLoc) < 1e-6);
    await s.unmount();

    // A role without view.site_precise gets no declination, even when the position is in hand.
    fake.declination = 'unset';
    seed(NO_PRECISE); siteBody = SITE;
    s = await mountSheet({ hash });
    await click(must('capture-photosphere'));
    assert.equal(fake.declination, null, 'magnetic');
    await s.unmount();

    // The server strips the position for such a role: nothing to build from either way.
    fake.declination = 'unset';
    seed(PRECISE); siteBody = { name: 'Stripped', is_default: false, horizon_min_deg: 15, horizon_points: [] };
    s = await mountSheet({ hash });
    await click(must('capture-photosphere'));
    assert.equal(fake.declination, null, 'no position, no declination');
    await s.unmount();
  } finally { unspy(); unpin(); uninstallFake(); siteBody = SITE; }
});

await test('toTrue is null when the model has no answer for today (WMM2025 ends in 2030): the scan opens and the panorama stays magnetic', async () => {
  seed(PRECISE); siteBody = SITE; noPermissionApi();
  const fake = new FakeScanner();
  installFake(fake);
  const unpin = pinClock(Date.UTC(2031, 5, 1));
  try {
    const s = await mountSheet({ hash: '#/sky/horizon?scanner=pano' });
    await click(must('capture-photosphere'));
    assert.ok(byTest('pano-capture'), 'the scan still opens');
    assert.equal(fake.declination, null);
    await s.unmount();
  } finally { unpin(); uninstallFake(); }
});

await test('Finish adopts the result\'s points; nothing is written until Save horizon, which persists exactly those points', async () => {
  seed(); siteBody = SITE; noPermissionApi(); posts.length = 0; idbPuts.length = 0;
  const fake = new FakeScanner();
  const line: HorizonPoint[] = [{ az: 10, alt: 12 }, { az: 100, alt: 20.5 }, { az: 200, alt: 30 }];
  fake.result = resultOf(line);
  fake.set({ phase: 'scanning', keyframes: 5 });
  installFake(fake);
  try {
    const s = await mountSheet({ hash: '#/sky/horizon?scanner=pano' });
    assert.equal(document.querySelectorAll('[data-testid="horizon-edit-point"]').length, 2, 'the saved line is on the strip');
    await click(must('capture-photosphere'));
    await act(async () => { must('pano-review').click(); });
    await paint(); await settle();
    // The previous line went into finish: the saved line, as the sheet held it.
    assert.deepEqual(fake.finishArgs[0].previous, [{ az: 0, alt: 5 }, { az: 180, alt: 8 }]);
    assert.equal(fake.finishArgs[0].endedBy, 'user');
    absent('pano-capture', 'the capture closes');
    assert.ok(fake.log.includes('stop'));
    const points = document.querySelectorAll('[data-testid="horizon-edit-point"]');
    assert.equal(points.length, 3, 'the result\'s points are the editor\'s points');
    assert.equal(must('horizon-panorama').getAttribute('href'), PNG, 'and its picture is under them');
    assert.ok(byTest('photosphere-card'), 'the trace card');
    assert.match(must('horizon-sheet').textContent!, /from photosphere/, 'the sub line says where the line came from');
    assert.match(must('horizon-sheet').textContent!, /Your changes haven't been saved yet/, 'dirty');
    assert.equal(posts.length, 0, 'adopting the result writes nothing to the server');
    assert.equal(idbPuts.length, 1, 'the picture goes to the browser store, once');
    assert.equal(idbPuts[0].image, PNG);
    assert.match(idbPuts[0].key, /^active:/, 'under the active site key');
    assert.match(must('horizon-sheet').textContent!, /This photo is saved in this browser for this site/, 'and the editor says so once the write is done');
    const save = [...document.querySelectorAll('button')].find(b => /Save horizon/.test(b.textContent ?? ''))!;
    assert.ok(save, 'Save horizon is offered');
    await click(save as HTMLElement);
    const write = posts.find(p => p.url.includes('/api/config'));
    assert.ok(write, 'Save writes the safety config');
    assert.deepEqual(write!.body.safety.horizon, [[10, 12], [100, 20.5], [200, 30]], 'with exactly the scan\'s points');
    assert.match(must('horizon-sheet').textContent!, /Horizon saved/);
    await s.unmount();
  } finally { uninstallFake(); }
});

await test('a scan that ended in an error shows the error; a result without a picture leaves the strip bare', async () => {
  seed(); siteBody = SITE; noPermissionApi();
  const fake = new FakeScanner();
  idbPuts.length = 0;
  fake.result = resultOf([{ az: 5, alt: 90 }], { png: '', endedBy: 'error', error: 'the tracer ran out of memory' });
  fake.set({ phase: 'scanning', keyframes: 5 });
  installFake(fake);
  try {
    const s = await mountSheet({ hash: '#/sky/horizon?scanner=pano' });
    await click(must('capture-photosphere'));
    await act(async () => { must('pano-review').click(); });
    await paint(); await settle();
    assert.equal(document.querySelector('[role="alert"]')!.textContent, 'the tracer ran out of memory');
    absent('horizon-panorama');
    assert.equal(document.querySelectorAll('[data-testid="horizon-edit-point"]').length, 1);
    assert.equal(idbPuts.length, 0, 'an empty picture is not stored');
    await s.unmount();
  } finally { uninstallFake(); }
});

await test('Cancel keeps the report link, and the recording link when there is one; the editor is as it was', async () => {
  seed(); siteBody = SITE; noPermissionApi();
  const fake = new FakeScanner();
  fake.recordingText = '{"kind":"header"}';
  installFake(fake);
  try {
    const s = await mountSheet({ hash: '#/sky/horizon?scanner=pano' });
    absent('photosphere-report-card');
    await click(must('capture-photosphere'));
    await click(must('pano-cancel'));
    absent('pano-capture');
    assert.ok(fake.log.includes('stop'));
    const card = must('photosphere-report-card');
    const reportLink = card.querySelector<HTMLAnchorElement>('a[download="astrodeck-pano-report.json"]');
    assert.ok(reportLink, 'the report is downloadable after Cancel (#66)');
    assert.deepEqual(JSON.parse(decode(reportLink!.getAttribute('href')!)), REPORT);
    const recording = must('pano-recording-link') as HTMLAnchorElement;
    assert.equal(recording.getAttribute('download'), 'astrodeck-pano-recording.jsonl');
    assert.equal(decode(recording.getAttribute('href')!), '{"kind":"header"}');
    assert.equal(document.querySelectorAll('[data-testid="horizon-edit-point"]').length, 2, 'the line is untouched');
    absent('horizon-panorama');
    assert.ok(!/Your changes haven't been saved/.test(must('horizon-sheet').textContent!), 'a cancelled scan does not dirty the sheet');
    assert.ok(byTest('capture-photosphere'), 'and the scan can be opened again');
    // A scan with no recording drops the old recording link.
    fake.recordingText = null;
    await click(must('capture-photosphere'));
    await click(must('pano-cancel'));
    absent('pano-recording-link');
    await s.unmount();
  } finally { uninstallFake(); }
});

await test('a hidden tab ends a begun scan with finish(previous, "hidden"), and the sheet adopts the partial result', async () => {
  seed(); siteBody = SITE; noPermissionApi();
  const fake = new FakeScanner();
  fake.result = resultOf([{ az: 20, alt: 15 }], { endedBy: 'hidden' });
  fake.set({ phase: 'scanning', keyframes: 6 });
  installFake(fake);
  try {
    const s = await mountSheet({ hash: '#/sky/horizon?scanner=pano' });
    await click(must('capture-photosphere'));
    Object.defineProperty(w.document, 'visibilityState', { value: 'hidden', configurable: true });
    await act(async () => { w.document.dispatchEvent(new w.Event('visibilitychange')); });
    await settle();
    assert.equal(fake.finishArgs.length, 1);
    assert.equal(fake.finishArgs[0].endedBy, 'hidden');
    assert.deepEqual(fake.finishArgs[0].previous, [{ az: 0, alt: 5 }, { az: 180, alt: 8 }]);
    absent('pano-capture');
    assert.equal(document.querySelectorAll('[data-testid="horizon-edit-point"]').length, 1);
    await s.unmount();
  } finally { uninstallFake(); resetPage(); }
});

await test('unmounting the sheet during a scan stops the scanner and finishes nothing', async () => {
  seed(); siteBody = SITE; noPermissionApi();
  const fake = new FakeScanner();
  fake.set({ phase: 'scanning', keyframes: 6 });
  installFake(fake);
  try {
    const s = await mountSheet({ hash: '#/sky/horizon?scanner=pano' });
    await click(must('capture-photosphere'));
    await s.unmount();
    assert.deepEqual(fake.log.filter(e => e === 'stop' || e === 'finish'), ['stop']);
  } finally { uninstallFake(); resetPage(); }
});

await test('flag back off (scanner=legacy): the legacy panel again, and the pano line is gone', async () => {
  seed(); siteBody = SITE; noPermissionApi();
  const fake = new FakeScanner();
  installFake(fake);
  try {
    w.localStorage.setItem(PANO_FLAG_KEY, 'pano');
    const s = await mountSheet({ hash: '#/sky/horizon?scanner=legacy' });
    assert.equal(w.localStorage.getItem(PANO_FLAG_KEY), null);
    absent('pano-scan-line');
    await act(async () => { must('capture-photosphere').click(); });
    absent('pano-capture');
    assert.equal(must('photosphere-capturing').hidden, false);
    assert.equal(fake.startArgs.length, 0);
    await s.unmount();
  } finally { uninstallFake(); resetPage(); }
});

// ---- Stubs used above ------------------------------------------------------------------------------------------

function stubCamera(): any {
  const none = { n: 0, p50: null, p95: null, max: null };
  return {
    playing: true, lastFrameAt: null, choices: [], activeId: undefined, analysisW: 180, analysisH: 320,
    settings: { width: 720, height: 1280, frameRate: 30, resizeMode: 'none', zoom: null, focusMode: null, focusDistance: null, facingMode: 'environment', label: 'stub camera' },
    bench: null, farbled: null, exposureReadable: null, slips: { pairs: 0, slips: 0 },
    lag: { nowMinusCapture: none, presentMinusCapture: none, expectedMinusNow: none },
    async open() {}, onFrame() {}, readback: () => null, readbackTiny: () => null, refreshLiveSource() {}, liveSource: () => null, close() {},
  };
}

console.log(`panoCaptureDom.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
