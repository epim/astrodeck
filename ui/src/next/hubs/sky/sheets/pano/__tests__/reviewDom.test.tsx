// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T29: the flagged review in the horizon sheet (SPEC-v2 7.2, the flagged review DOM row), mounted in jsdom. A fake
// scanner sits behind PanoramaScanner's prototype (as panoCaptureDom.test.tsx does), the sheet's scan button and Review
// button are tapped, and the sheet adopts a hand-built ScanResult: so the shipped wiring (adoption, the strip, the notes,
// the north card, the editor handlers, Save) is under test with nothing added to the production surface.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/sheets/pano/__tests__/reviewDom.test.tsx   (from ui/)
//
// Mutant this file must catch (SPEC-v2 7.2, T29): tap-to-delete restored in the flagged path (a still tap on a point
// removes it instead of selecting it). 'a still tap selects a point ...' sees the point count fall and no readout.
//
// Further mutants, each caught by the case named (the full list, with the line each one fails on, is in the T29 report):
//   * the grid dropped from insert or from drag: 'the 0.5 grid ...';
//   * the draft not updated by an edit (editDraft skipped): 'an edit turns the bins it changed into Edited ...';
//   * the nudge turning the raster but not the draft, or not the line: 'the north nudge shifts the raster, the draft and the line';
//   * the no-compass lock removed, or not released by the tap: 'the no-compass lock ...';
//   * the non-Measured segments not red dotted, or the vertices not hollow: 'line styles over non-Measured bins ...';
//   * the accept action not reaching the line: 'the accept-traced action ...'.
import assert from 'node:assert/strict';
import { createManualRaf } from '../../../../../../testing/rafPolyfill';
import { BinState, ColState, PANO_H, PANO_W, PROFILE_BINS } from '../types';
import type { HorizonDraft, HorizonPoint, NorthEstimate, ScanReport, ScanResult, ScanStatus, ScannerLike } from '../types';
import type { PanoScannerControl } from '../PanoCapture';

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import('jsdom');
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://localhost/', pretendToBeVisual: true });
const w = dom.window as any;
const g = globalThis as any;

// ---- Fakes installed before React and the sheet are imported ---------------------------------------------------

const raf = createManualRaf();
function flushRaf(): void {
  const upTo = raf.pending.length;
  for (let i = 0; i < upTo; i++) {
    const cb = raf.pending[i];
    if (!cb) continue;
    delete raf.pending[i];
    cb(performance.now());
  }
}
/** setInterval as a record: nothing ticks behind the tests. */
g.setInterval = () => 1;
g.clearInterval = () => {};

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
Object.defineProperty(w.navigator, 'mediaDevices', { value: { enumerateDevices: async () => [], getUserMedia: async () => ({ getTracks: () => [], getVideoTracks: () => [] }) }, configurable: true });

// The strip's real layout: jsdom measures every element as zero, which would put every tap on the first point. The strip
// is 340 x 150 CSS pixels here, so a client coordinate is a viewBox coordinate.
w.Element.prototype.getBoundingClientRect = function (this: any) {
  const strip = this.getAttribute?.('data-testid') === 'horizon-strip';
  return { left: 0, top: 0, right: strip ? 340 : 0, bottom: strip ? 150 : 0, width: strip ? 340 : 0, height: strip ? 150 : 0, x: 0, y: 0, toJSON() {} };
};

// A canvas that records the raster `toPNG` puts on it, and answers a distinct PNG for each call.
const putData: Uint8ClampedArray[] = [];
w.HTMLCanvasElement.prototype.getContext = function (this: any) {
  if (this.width !== 1080) return null;   // toPNG sizes its canvas to PANO_W first; ScanView's own canvases draw nothing here
  return {
  createImageData: (cw: number, ch: number) => ({ data: new Uint8ClampedArray(cw * ch * 4) }),
  putImageData: (image: { data: Uint8ClampedArray }) => { putData.push(image.data.slice()); },
  };
};
w.HTMLCanvasElement.prototype.toDataURL = () => `data:image/png;base64,nudge${putData.length}`;

const idbPuts: { key: string; image: string }[] = [];
g.indexedDB = {
  open() {
    const req: any = { result: null, error: null, onupgradeneeded: null, onsuccess: null, onerror: null };
    req.result = {
      createObjectStore() {}, close() {},
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

// The network. Synthetic coordinates only (SPEC-v2 8.1, rule 6).
const SITE = { name: 'Test field', is_default: false, horizon_min_deg: 15, horizon_points: [[0, 5], [180, 8]] as [number, number][],
  latitude: 44.25, longitude: -121.75, elevation_m: 250 };
const posts: { url: string; body: any }[] = [];
const ok = (data: unknown) => ({ ok: true, status: 200, statusText: 'OK', json: async () => data });
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const u = String(url), method = (init?.method ?? 'GET').toUpperCase();
  if (method !== 'GET') posts.push({ url: u, body: init?.body ? JSON.parse(init.body) : null });
  if (u.includes('/api/locations') && method === 'GET') return ok([]);
  if (u.includes('/api/site') && method === 'GET') return ok({ site: SITE, version: 1 });
  if (u.includes('/api/config')) return ok({ version: 2, site: { is_default: true, horizon_min_deg: 15 }, safety: { horizon: [] } });
  return ok({});
};

const { createElement, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { useStore } = await import('../../../../../../store');
const { HorizonSheet } = await import('../../horizon');
const { draftFromPoints, draftToPoints, acceptSuggested, shiftDraft } = await import('../../horizonDraft');
const { HX, HY } = await import('../../horizonStrip');
const { PanoramaScanner } = await import('../scanner');
const { CUE_TEXT, REVIEW_TEXT, fill } = await import('../copy');

// ---- Harness ---------------------------------------------------------------------------------------------------

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }
const settle = async () => { for (let i = 0; i < 4; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)); }); };
const paint = async () => { await act(async () => { flushRaf(); await new Promise(r => setTimeout(r, 0)); }); };
const byTest = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
const all = (id: string) => [...document.querySelectorAll<SVGElement & HTMLElement>(`[data-testid="${id}"]`)];
const must = (id: string): HTMLElement => { const el = byTest(id); assert.ok(el, `no element with data-testid="${id}"`); return el!; };
const absent = (id: string, msg?: string) => assert.ok(byTest(id) === null, msg ?? `${id} should not be in the document`);
const click = async (el: HTMLElement) => { await act(async () => { el.click(); }); await settle(); };
const locked = (id: string) => must(id).getAttribute('aria-disabled') === 'true';
const text = (id: string) => must(id).textContent ?? '';

function ptr(type: string, x: number, y: number): any {
  const ev = new w.Event(type, { bubbles: true, cancelable: true });
  Object.assign(ev, { pointerId: 1, pointerType: 'touch', button: 0, buttons: 1, isPrimary: true, clientX: x, clientY: y });
  return ev;
}
const strip = () => must('horizon-strip');
const fire = async (type: string, az: number, alt: number) => { await act(async () => { strip().dispatchEvent(ptr(type, HX(az), HY(alt))); }); };
const tap = async (az: number, alt: number) => { await fire('pointerdown', az, alt); await fire('pointerup', az, alt); await settle(); };
const drag = async (from: HorizonPoint, to: HorizonPoint) => {
  await fire('pointerdown', from.az, from.alt); await fire('pointermove', to.az, to.alt); await fire('pointerup', to.az, to.alt); await settle();
};
const circles = () => all('horizon-edit-point');
const savePress = async () => {
  const save = [...document.querySelectorAll('button')].find(b => /Save horizon/.test(b.textContent ?? ''))!;
  assert.ok(save, 'Save horizon is offered');
  posts.length = 0;
  await click(save as HTMLElement);
  return posts.find(p => p.url.includes('/api/config'))?.body?.safety?.horizon as [number, number][] | undefined;
};
const saveButton = () => [...document.querySelectorAll('button')].find(b => /Save horizon/.test(b.textContent ?? '')) as HTMLElement;

function seed() {
  useStore.setState({
    principal: { role: 'admin', email: 'a@example.test', caps: ['view.status', 'config.site_optics', 'config.safety', 'view.site_precise'] },
    wsPhase: 'up', equipConnected: true,
    status: { connected: {}, looping: false, mode: 'sim', busy: null, busy_lanes: [] },
    config: { version: 1, site: { is_default: true, horizon_min_deg: 15 }, safety: { horizon: [] } },
  } as never);
}
function resetPage() {
  w.localStorage.clear();
  w.history.replaceState(null, '', '/#/');
  Object.defineProperty(w.document, 'visibilityState', { value: 'visible', configurable: true });
}

const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=';
const NORTH: NorthEstimate = { offsetDeg: 0, sigmaDeg: 2.4, spreadDeg: 1.2, samples: 120, nEff: 4, stable: true, source: 'scan' };
const LOOP = { closed: true, method: 'image' as const, preDeg: null, postDeg: 0.4, match: { early: 0, late: 9 }, unwrappedDeg: 360 };
function resultOf(points: HorizonPoint[], over: Partial<ScanResult> = {}): ScanResult {
  return {
    png: PNG, rgba: new Uint8ClampedArray(0), draft: draftFromPoints(points), points, tau: 0.5, north: NORTH, declinationApplied: false,
    focal: { state: 'locked', sdPct: 1, shortFovDeg: 43 }, loop: LOOP,
    extractor: 'tracer', partial: false, endedBy: 'user', error: null, ...over,
  };
}

/** A draft whose bins are, in degrees: 0-100 Measured at 10; 100-120 Low (traced 20); 120-140 Unknown unseen; 140-150
 *  Unknown dark; 150-170 Tall (top 40); 170-180 Kept at 15; 180-360 Measured at 12. */
function mixedDraft(): HorizonDraft {
  const d = draftFromPoints([{ az: 0, alt: 12 }]);
  const set = (from: number, to: number, state: number, alt: number, reason: number = ColState.Measured, suggested = NaN, top = NaN) => {
    for (let b = from * 2; b < to * 2; b++) { d.state[b] = state; d.alt[b] = alt; d.reason[b] = reason; d.suggested[b] = suggested; d.top[b] = top; }
  };
  set(0, 100, BinState.Measured, 10);
  set(100, 120, BinState.Low, 90, ColState.Low, 20);
  set(120, 140, BinState.Unknown, 90, ColState.UnknownUnseen);
  set(140, 150, BinState.Unknown, 90, ColState.UnknownDark);
  set(150, 170, BinState.Tall, 90, ColState.Tall, NaN, 40);
  set(170, 180, BinState.Kept, 15);
  set(180, 360, BinState.Measured, 12);
  return d;
}
function mixedResult(over: Partial<ScanResult> = {}): ScanResult {
  const draft = mixedDraft();
  return resultOf(draftToPoints(draft).points, { draft, ...over });
}

const BAND0 = { green: [12, 23.36] as [number, number], amber: [5, 25.36] as [number, number] };
function baseStatus(): ScanStatus {
  return {
    phase: 'scanning', cue: CUE_TEXT.ready, cueKey: 'ready', cueKind: 'info', error: null,
    frameNo: 0, headingDeg: null, northOffsetDeg: null, elevationDeg: 20, rollDeg: 0, rateDegS: 0,
    targetPitchDeg: 20, pitchBand: BAND0, pace: 'idle', rateMaxDegS: 40,
    coverage: new Uint8Array(720), coveredDeg: 0, gapDeg: null, tallSpans: [],
    mode: 'absolute-only', north: null, focal: { state: 'prior', shortFovDeg: 43 },
    loop: { closed: false, method: null, preDeg: null, postDeg: null, match: null, unwrappedDeg: 0 },
    keyframes: 5, portrait: true, canBegin: true, recording: false, farbled: null,
  };
}
class FakeScanner implements ScannerLike, PanoScannerControl {
  status: ScanStatus = baseStatus();
  canBegin = true;
  cameraChoices: readonly { deviceId: string; label: string }[] = [];
  activeCameraId: string | undefined = undefined;
  result: ScanResult;
  readonly ribbon = { pixels: () => new Uint8ClampedArray(PANO_W * PANO_H * 4), takeDirty: () => [], liveMesh: () => null, liveSource: () => null };
  constructor(result: ScanResult) { this.result = result; }
  subscribe() { return () => {}; }
  async start() {}
  begin() {} pause() {} resume() {} tick() {} stop() {} setDeclination() {} setRecording() {} noteMeshDraw() {}
  finish() { return this.result; }
  report() { return { format: 'astrodeck-pano-report', version: 2 } as unknown as ScanReport; }
  recording() { return null; }
}
const FAKED_METHODS = ['start', 'begin', 'pause', 'resume', 'tick', 'finish', 'stop', 'setDeclination', 'setRecording', 'report', 'recording', 'subscribe', 'noteMeshDraw'];
const FAKED_GETTERS = ['status', 'canBegin', 'cameraChoices', 'activeCameraId'];
let installed: Map<string, PropertyDescriptor | undefined> | null = null;
function installFake(fake: FakeScanner) {
  const proto = PanoramaScanner.prototype as any;
  installed = new Map();
  for (const name of FAKED_METHODS) {
    installed.set(name, Object.getOwnPropertyDescriptor(proto, name));
    Object.defineProperty(proto, name, { configurable: true, writable: true, value(...args: unknown[]) { return (fake as any)[name](...args); } });
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

/** Open the sheet with the flag as `hash` says, and with a fake scanner that finishes into `result` (none: no scan is run). */
async function review(result: ScanResult | null, hash = '#/sky/horizon?scanner=pano') {
  resetPage(); seed(); posts.length = 0; idbPuts.length = 0; putData.length = 0;
  w.history.replaceState(null, '', `/${hash}`);
  if (result) installFake(new FakeScanner(result));
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(createElement(HorizonSheet, { params: { site: 'current' }, depth: 0 } as any)); });
  await settle();
  if (result) {
    await click(must('capture-photosphere'));
    await act(async () => { must('pano-review').click(); });
    await paint(); await settle();
  }
  return { unmount: async () => { await act(async () => { root.unmount(); }); host.remove(); if (result) uninstallFake(); } };
}

// ---- Cases -----------------------------------------------------------------------------------------------------

await test('line styles over non-Measured bins: solid gold over certain bins, red dotted over Low, Unknown and Tall, the suggestion dashed, uncertain vertices hollow amber', async () => {
  const result = mixedResult();
  const s = await review(result);
  const runs = all('horizon-line-run');
  assert.deepEqual(runs.map(r => r.getAttribute('data-kind')), ['certain', 'blocked', 'certain'], 'the line is three runs: 0-100, 100-170, 170-360');
  const [a, b, c] = runs;
  assert.equal(a.getAttribute('stroke'), '#ffdb8b');
  assert.equal(a.getAttribute('stroke-dasharray'), null, 'a certain run is solid');
  assert.equal(b.getAttribute('stroke'), '#ff5470', 'a blocked run is red');
  assert.ok(b.getAttribute('stroke-dasharray'), 'and dotted');
  assert.equal(c.getAttribute('stroke-dasharray'), null, 'Kept and Measured are solid again');
  // The blocked run is the line at 90: its path stays on y = HY(90).
  const ys = [...b.getAttribute('d')!.matchAll(/[ML][\d.]+,([\d.]+)/g)].map(m => Number(m[1]));
  assert.ok(ys.length >= 2 && ys.every(y => Math.abs(y - HY(90)) < 0.06), `the blocked run is drawn at 90: ${ys.join(' ')}`);
  const sug = all('horizon-suggested');
  assert.equal(sug.length, 1, 'one dashed suggestion, over the Low run');
  assert.ok(sug[0].getAttribute('stroke-dasharray'));
  assert.ok(Number(sug[0].getAttribute('stroke-width')) < Number(a.getAttribute('stroke-width')), 'thin');
  const ys2 = [...sug[0].getAttribute('d')!.matchAll(/[ML][\d.]+,([\d.]+)/g)].map(m => Number(m[1]));
  assert.ok(ys2.every(y => Math.abs(y - HY(20)) < 0.06), 'at the traced altitude');
  const dots = circles();
  assert.equal(dots.length, result.points.length);
  result.points.forEach((p, i) => {
    const hollow = p.az >= 100 && p.az <= 170;   // a vertex on an edge belongs to both bins beside it
    assert.equal(dots[i].getAttribute('data-uncertain') === 'true', hollow, `vertex at ${p.az} deg: ${hollow ? 'hollow' : 'solid'}`);
    assert.equal(dots[i].getAttribute('fill'), hollow ? 'none' : 'rgba(6,7,11,.85)');
    if (hollow) assert.equal(dots[i].getAttribute('stroke'), '#ffb454', 'amber');
  });
  assert.ok(dots.some(d => d.getAttribute('data-uncertain') === 'true') && dots.some(d => d.getAttribute('data-uncertain') !== 'true'));
  await s.unmount();
});

await test('the notes under the strip name each blocked span with its degrees, from the draft', async () => {
  const s = await review(mixedResult());
  assert.equal(text('review-note-low'), fill(REVIEW_TEXT.lowSpans, { n: 1, d: 20 }));
  assert.equal(text('review-note-low'), 'Traced but not certain: 1 places, 20 deg in all. Saved as blocked unless you accept the traced line.');
  assert.equal(text('review-note-unseen'), 'Not photographed: 120-140 deg. Saved as blocked.');
  assert.equal(text('review-note-dark'), 'Too dark to read: 140-150 deg. Saved as blocked.');
  assert.equal(text('review-note-tall'), 'Taller than the photo: 150-170 deg (at least 40 deg). Saved as blocked.');
  assert.equal(text('review-note-kept'), 'Not photographed: 170-180 deg. Your earlier line is kept there.');
  absent('review-note-lowlight'); absent('review-note-hidden'); absent('review-note-partial');
  await s.unmount();
});

await test('the accept-traced action takes the traced line over the uncertain parts, and Save publishes it', async () => {
  const result = mixedResult();
  const s = await review(result);
  const before = text('review-notes');
  assert.match(before, /Use the traced line for the uncertain parts/);
  await click(must('accept-traced'));
  absent('review-note-low', 'the Low bins are Edited now, so the note goes');
  absent('accept-traced');
  assert.equal(all('horizon-suggested').length, 0, 'and so does the dashed suggestion');
  assert.equal(all('horizon-line-run').filter(r => r.getAttribute('data-kind') === 'blocked').length, 1, 'only the Unknown and Tall run stays red');
  const want = draftToPoints(acceptSuggested(result.draft)).points.map(p => [p.az, p.alt]);
  assert.notDeepEqual(want, result.points.map(p => [p.az, p.alt]), 'the action changes the line');
  assert.deepEqual(await savePress(), want, 'Save publishes the draft\'s conservative line');
  assert.ok(want.some(([az, alt]) => az >= 100 && az <= 120 && alt === 20), 'with the traced 20 deg over the Low span');
  await s.unmount();
});

await test('the north nudge shifts the raster, the draft and the line, and writes the turned picture; it is offered only while the raster is in memory', async () => {
  const rgba = new Uint8ClampedArray(PANO_W * PANO_H * 4);
  const mark = (150 * PANO_W + 100) * 4;
  rgba.set([255, 0, 0, 255], mark);
  const result = mixedResult({ rgba });
  const s = await review(result);
  assert.deepEqual([...document.querySelectorAll('[data-testid^="north-nudge-"]')].map(b => b.textContent), ['-5', '-0.5', '+0.5', '+5']);
  await click(must('north-nudge-5'));
  assert.equal(putData.length, 1, 'the turned raster went to the picture');
  const out = putData[0];
  assert.equal(out[(150 * PANO_W + 115) * 4 + 3], 255, '+5 deg is 15 columns: the mark moved from column 100 to 115');
  assert.equal(out[(150 * PANO_W + 100) * 4 + 3], 0, 'and is gone from 100');
  assert.equal(must('horizon-panorama').getAttribute('href'), 'data:image/png;base64,nudge1', 'the strip shows the turned picture');
  assert.equal(idbPuts[idbPuts.length - 1].image, 'data:image/png;base64,nudge1', 'which the browser store holds too');
  const turned = shiftDraft(result.draft, 5);
  const want = draftToPoints(turned).points.map(p => [p.az, p.alt]);
  assert.deepEqual(circles().map(c => [Number(c.getAttribute('cx')), Number(c.getAttribute('cy'))]),
    want.map(([az, alt]) => [HX(az), HY(alt)]), 'the line is re-derived from the shifted draft');
  assert.match(text('review-note-unseen'), /125-145 deg/, 'and the notes follow the draft: 120-140 became 125-145');
  await click(must('north-nudge--5'));
  assert.equal(putData[1][(150 * PANO_W + 100) * 4 + 3], 255, 'a second nudge turns the turned raster: -5 deg brings the mark back to column 100');
  assert.match(text('review-note-unseen'), /120-140 deg/);
  assert.deepEqual(await savePress(), draftToPoints(shiftDraft(turned, -5)).points.map(p => [p.az, p.alt]));
  await s.unmount();

  const gone = await review(mixedResult());   // rgba of length 0: the raster is no longer in memory
  absent('north-nudge', 'no buttons without the raster');
  assert.ok(byTest('north-card'), 'the card itself is still there');
  await gone.unmount();
});

await test('the north card names the source, the uncertainty, and true or magnetic', async () => {
  let s = await review(mixedResult());
  assert.equal(text('north-source'), 'North: compass averaged over the scan, about ±2 deg. Metal near a mount can bias it by a fixed amount.');
  assert.equal(text('north-frame'), 'North is magnetic.');
  absent('north-is-right', 'a compass reading was taken: nothing to confirm');
  assert.equal(locked('capture-photosphere') === false && saveButton().getAttribute('aria-disabled') !== 'true', true, 'Save is open');
  await s.unmount();
  s = await review(mixedResult({ north: { ...NORTH, stable: false, sigmaDeg: 6.2 }, declinationApplied: true }));
  assert.equal(text('north-source'), 'Unstable compass: about ±6 deg. Line N up with something you know.');
  assert.equal(text('north-frame'), 'Corrected to true north.');
  await s.unmount();
});

await test('the no-compass lock: Save stays locked, with the reason, until North is right is tapped', async () => {
  const s = await review(mixedResult({ north: null }));
  assert.equal(text('north-source'), REVIEW_TEXT.noCompass);
  assert.equal(REVIEW_TEXT.noCompass, 'No compass reading. Line up N with something you know is north, then tap North is right.');
  assert.equal(saveButton().getAttribute('aria-disabled'), 'true', 'Save is locked');
  assert.equal(saveButton().getAttribute('title'), REVIEW_TEXT.noCompass, 'and says why');
  posts.length = 0;
  await click(saveButton());
  assert.equal(posts.length, 0, 'a tap on the locked Save writes nothing');
  assert.equal(text('north-is-right'), 'North is right');
  await click(must('north-is-right'));
  assert.notEqual(saveButton().getAttribute('aria-disabled'), 'true', 'Save is open after the tap');
  absent('north-is-right');
  assert.ok(await savePress(), 'and writes');
  await s.unmount();
});

await test('the low-light note, the hidden-end note and the partial and gyro-closure notes', async () => {
  const dark = { ...mixedDraft(), lowLight: true };
  let s = await review(mixedResult({ draft: dark, endedBy: 'hidden', partial: true, loop: { ...LOOP, closed: false } }));
  assert.equal(text('review-note-lowlight'), 'Too dark to find the horizon automatically here. Draw it over the photo, or scan again before dark.');
  assert.equal(text('review-note-hidden'), 'The scan stopped when the screen was switched away.');
  assert.equal(text('review-note-partial'), 'The ring did not close: north and lens are not refined.');
  absent('review-note-gyro');
  await s.unmount();
  s = await review(mixedResult({ loop: { ...LOOP, method: 'gyro' } }));
  assert.equal(text('review-note-gyro'), 'The loop closed without a picture match.');
  absent('review-note-lowlight'); absent('review-note-hidden');
  await s.unmount();
});

const LINE: HorizonPoint[] = [{ az: 10, alt: 12 }, { az: 100, alt: 20.5 }, { az: 200, alt: 30 }];

await test('a still tap selects a point without deleting it, shows its azimuth and altitude, and Delete point deletes it', async () => {
  const s = await review(resultOf(LINE));
  assert.equal(circles().length, 3);
  absent('selected-point');
  await tap(100, 20.5);
  assert.equal(circles().length, 3, 'the tap did not delete');
  assert.equal(text('selected-point-readout'), 'Azimuth 100 deg, altitude 20.5 deg');
  assert.equal(circles()[1].getAttribute('data-selected'), 'true', 'the point is marked');
  assert.equal(text('delete-point'), 'Delete point');
  assert.match(must('horizon-sheet').textContent!, /tap a point to select it/);
  assert.doesNotMatch(must('horizon-sheet').textContent!, /tap a point to delete/);
  assert.equal(must('horizon-sheet').textContent!.includes('from photosphere'), true, 'a tap that selects is not a hand edit');
  await tap(200, 30);
  assert.equal(text('selected-point-readout'), 'Azimuth 200 deg, altitude 30 deg', 'a second tap moves the selection');
  await click(must('delete-point'));
  assert.equal(circles().length, 2, 'the button deleted the selected point');
  absent('selected-point');
  assert.deepEqual(await savePress(), [[10, 12], [100, 20.5]]);
  await s.unmount();
});

await test('the 0.5 grid: an inserted point and a dragged point land on half degrees, and a drag stays 0.5 clear of its neighbours', async () => {
  const s = await review(resultOf(LINE));
  await tap(301.37, 60.3);
  assert.equal(circles().length, 4, 'a tap on empty strip inserts');
  const after = await savePress();
  assert.deepEqual(after, [[10, 12], [100, 20.5], [200, 30], [301.5, 60.5]], '301.37 and 60.3 rounded to the grid');
  await drag({ az: 100, alt: 20.5 }, { az: 250, alt: 22.3 });
  assert.deepEqual(await savePress(), [[10, 12], [199.5, 22.5], [200, 30], [301.5, 60.5]], 'clamped to the neighbour minus 0.5, altitude on the grid');
  await s.unmount();
});

await test('an edit turns the bins it changed into Edited: the notes and the red dots follow the line the user drew', async () => {
  const s = await review(mixedResult());
  assert.ok(byTest('review-note-low'));
  const blocked = () => all('horizon-line-run').filter(r => r.getAttribute('data-kind') === 'blocked').map(r => r.getAttribute('d')!);
  assert.ok(blocked()[0].startsWith(`M${HX(100).toFixed(1)},`), 'the blocked run starts at 100, where the Low span does');
  await tap(110, 50);   // insert in the Low span, on empty strip: the line there drops from 90 to meet the new point
  absent('review-note-low', 'every bin of the Low span moved, so each is Edited and the note goes');
  assert.ok(blocked().every(pd => !pd.startsWith(`M${HX(100).toFixed(1)},`)), 'and no red dotted run starts at 100 any more');
  assert.ok(all('horizon-line-run').some(r => r.getAttribute('data-kind') === 'certain' && r.getAttribute('d')!.includes(`${HX(110).toFixed(1)},${HY(50).toFixed(1)}`)),
    'the edited bins are drawn solid, through the new vertex');
  const inserted = circles().find(c => Math.abs(Number(c.getAttribute('cx')) - HX(110)) < 0.01);
  assert.ok(inserted, 'the new vertex is on the strip');
  assert.notEqual(inserted!.getAttribute('data-uncertain'), 'true', 'a vertex the user placed is not drawn as uncertain');
  await s.unmount();
});

await test('with the flag off a tap still deletes, and none of the review is there', async () => {
  const s = await review(null, '#/sky/horizon?scanner=legacy');
  assert.equal(circles().length, 2, 'the saved line, no panorama: the old hit test');
  await tap(0, 5);
  assert.equal(posts.find(p => p.url.includes('/api/config'))?.body.safety.horizon.length, 1, 'tap-to-delete wrote the line without the point');
  absent('selected-point'); absent('review-notes'); absent('north-card');
  assert.match(must('horizon-sheet').textContent!, /tap a point to delete/);
  assert.ok(byTest('editable-horizon-line'), 'the old single line');
  absent('horizon-line-run');
  await s.unmount();
});

await test('with the flag on and no scan result, the editor selects and snaps but shows no review', async () => {
  const s = await review(null);
  assert.equal(circles().length, 2);
  absent('review-notes'); absent('north-card');
  await tap(90.3, 40.2);
  const body = posts.find(p => p.url.includes('/api/config'))?.body.safety.horizon;
  assert.ok(body.some((p: number[]) => p[0] === 90.5 && p[1] === 40), `inserted on the grid: ${JSON.stringify(body)}`);
  await s.unmount();
});

console.log(`${passed} passed`);
void PROFILE_BINS; void PANO_H;
