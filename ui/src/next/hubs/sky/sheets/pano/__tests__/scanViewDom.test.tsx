// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T18: ScanView, mounted in jsdom over a fake ScannerLike (SPEC-v2 7.2, the ScanView row). Canvas is stubbed with a
// recording context, requestAnimationFrame with a queue the test flushes by hand, so a frame happens exactly when the
// test says so. jsdom's setup follows horizonDom.test.tsx.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/sheets/pano/__tests__/scanViewDom.test.tsx   (from ui/)
//
// Mutant this file must catch (SPEC-v2 7.2): the live mesh redrawn on every animation frame instead of only when
// `status.frameNo` has changed. 'the live mesh is redrawn only when frameNo changes' counts `drawImage` calls on the
// overlay canvas across notifications that do not change `frameNo`; with the gate gone it counts extra draws.
//
// Further mutants named in the T18 report, each caught by the test written beside it: the ribbon scroll's sign, the
// wrap of a dirty rectangle, the sign of the north offset on the labels, a band edge made exclusive, the cue line
// without its role.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { PANO_H, PANO_W, PROFILE_BINS, PixClass } from '../types';
import type { DirtyRect, LiveTriangle, RibbonView, ScanStatus, ScannerLike } from '../types';

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import('jsdom');
const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', { url: 'http://local/', pretendToBeVisual: true });
const win = dom.window as any;
const g = globalThis as any;

// ---- Fakes installed before React is imported ------------------------------

/** requestAnimationFrame as a queue: nothing runs until `flushRaf`. A callback queued while flushing waits for the next flush. */
let rafSeq = 0;
const rafQueue = new Map<number, (t: number) => void>();
const fakeRaf = (cb: (t: number) => void) => { const id = ++rafSeq; rafQueue.set(id, cb); return id; };
const fakeCancelRaf = (id: number) => { rafQueue.delete(id); };
function flushRaf(): number {
  const batch = [...rafQueue.values()];
  rafQueue.clear();
  for (const cb of batch) cb(performance.now());
  return batch.length;
}

/** ImageData as the browser has it, including the size check; jsdom has none. */
class FakeImageData {
  readonly data: Uint8ClampedArray;
  readonly width: number;
  readonly height: number;
  constructor(data: Uint8ClampedArray, width: number, height: number) {
    if (data.length !== width * height * 4) throw new RangeError('ImageData: the data length does not match the size');
    this.data = data; this.width = width; this.height = height;
  }
}

interface Call { name: string; args: any[] }
interface Rec { calls: Call[]; props: Record<string, unknown>; ctx: any }
const recs = new Map<unknown, Rec>();
/** A 2d context that records every method call and every property set (as 'set:<name>'), in order. */
function recFor(canvas: unknown): Rec {
  let r = recs.get(canvas);
  if (r) return r;
  const calls: Call[] = [];
  const props: Record<string, unknown> = {};
  const ctx = new Proxy({}, {
    get: (_t, name: string) => (name === 'canvas' ? canvas : name in props ? props[name] : (...args: any[]) => { calls.push({ name, args }); }),
    set: (_t, name: string, value) => { props[name] = value; calls.push({ name: `set:${name}`, args: [value] }); return true; },
  });
  r = { calls, props, ctx };
  recs.set(canvas, r);
  return r;
}
win.HTMLCanvasElement.prototype.getContext = function (this: unknown, kind: string) { return kind === '2d' ? recFor(this).ctx : null; };

for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLCanvasElement', 'Element', 'Node', 'Event', 'MouseEvent']) {
  Object.defineProperty(g, k, { value: k === 'window' ? win : win[k], writable: true, configurable: true });
}
for (const [k, v] of Object.entries({
  requestAnimationFrame: fakeRaf, cancelAnimationFrame: fakeCancelRaf, ImageData: FakeImageData,
  getComputedStyle: win.getComputedStyle.bind(win),
})) {
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
  win[k] = v;
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { ScanView, ribbonViewFor, railColour, RIBBON_ALT_BOTTOM, RIBBON_ALT_TOP, RIBBON_SPAN_DEG } = await import('../ScanView');

// The real stylesheet, so computed style is the shipped one.
const SHEETS_CSS = readFileSync(new URL('../../sheets.css', import.meta.url), 'utf8');
const sheet = win.document.createElement('style');
sheet.textContent = SHEETS_CSS;
win.document.head.append(sheet);

// ---- Harness ---------------------------------------------------------------

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const near = (a: number, b: number, tol: number, what = '') => assert.ok(Math.abs(a - b) < tol, `${what} ${a} != ${b} (tol ${tol})`);
const mod = (a: number, n: number) => ((a % n) + n) % n;

const BAND0 = { green: [12, 23.36] as [number, number], amber: [5, 25.36] as [number, number] };

function baseStatus(): ScanStatus {
  return {
    phase: 'scanning', cue: 'Keep turning. 210 deg to go.', cueKey: 'turning', cueKind: 'info', error: null,
    frameNo: 0, headingDeg: 0, northOffsetDeg: null, elevationDeg: 20, rollDeg: 0, rateDegS: 12,
    targetPitchDeg: 20, pitchBand: BAND0, pace: 'good', rateMaxDegS: 40,
    coverage: new Uint8Array(PROFILE_BINS), coveredDeg: 150, gapDeg: null, tallSpans: [],
    mode: 'relative', north: null, focal: { state: 'prior', shortFovDeg: 43 },
    loop: { closed: false, method: null, preDeg: null, postDeg: null, match: null, unwrappedDeg: 0 },
    keyframes: 10, portrait: true, canBegin: true, recording: false, farbled: null,
  };
}

/** A 2 x 1 grid of cells split into 4 triangles, the way the real mesh is: vertices shared exactly between
 *  neighbours, the source in 90 x 160 pixels and the destination skewed one pixel per column so it is not a plain rectangle. */
function gridMesh(): LiveTriangle[] {
  const vertex = (col: number, row: number): [number, number, number, number] => [35 + 10 * col, 50 + 60 * row, 185 + 12 * col, 60 + 30 * row + col];
  const tri = (a: number[], b: number[], c: number[]): LiveTriangle => ({
    src: [a[0], a[1], b[0], b[1], c[0], c[1]], dst: [a[2], a[3], b[2], b[3], c[2], c[3]],
  });
  const tris: LiveTriangle[] = [];
  for (let c = 0; c < 2; c++) {
    const tl = vertex(c, 0), tr = vertex(c + 1, 0), bl = vertex(c, 1), br = vertex(c + 1, 1);
    tris.push(tri(tl, tr, bl));    // top-left, top-right, bottom-left
    tris.push(tri(br, bl, tr));    // bottom-right, bottom-left, top-right
  }
  return tris;
}

class FakeScanner implements ScannerLike {
  status: ScanStatus = baseStatus();
  readonly listeners = new Set<() => void>();
  readonly pix = new Uint8ClampedArray(PANO_W * PANO_H * 4);
  dirty: DirtyRect[] = [];
  takeDirtyCalls = 0;
  views: RibbonView[] = [];
  meshDraws: number[] = [];
  mesh: LiveTriangle[] | null = gridMesh();
  readonly source = { tag: 'live-source' } as unknown as CanvasImageSource;
  readonly canBegin = true;
  readonly cameraChoices: readonly { deviceId: string; label: string }[] = [];
  readonly activeCameraId: string | undefined = undefined;
  begin(): void {}
  pause(): void {}
  resume(): void {}
  tick(_nowMs: number): void {}
  subscribe(cb: () => void): () => void { this.listeners.add(cb); return () => { this.listeners.delete(cb); }; }
  readonly ribbon = {
    pixels: () => this.pix,
    takeDirty: () => { this.takeDirtyCalls++; const d = this.dirty; this.dirty = []; return d; },
    liveMesh: (view: RibbonView) => { this.views.push(view); return this.mesh; },
    liveSource: () => this.source,
  };
  noteMeshDraw(ms: number): void { this.meshDraws.push(ms); }
  emit(): void { for (const l of [...this.listeners]) l(); }
  set(patch: Partial<ScanStatus>): void { this.status = { ...this.status, ...patch }; this.emit(); }
}

interface Mounted { host: HTMLElement; scanner: FakeScanner; unmount(): void }
function mount(scanner = new FakeScanner(), props: { widthPx?: number; withVideo?: boolean } = {}): Mounted {
  rafQueue.clear();
  const host = win.document.createElement('div') as HTMLElement;
  win.document.body.append(host);
  const root = createRoot(host);
  const video = props.withVideo === false ? undefined : createElement('video', { id: 'slot-video' });
  act(() => { root.render(createElement(ScanView, { scanner, videoSlot: video, widthPx: props.widthPx })); });
  let gone = false;
  return { host, scanner, unmount() { if (gone) return; gone = true; act(() => { root.unmount(); }); host.remove(); } };
}
/** Run one animation frame's worth of queued callbacks inside act, so React state set by the frame is flushed. */
function frame(): number { let n = 0; act(() => { n = flushRaf(); }); return n; }
/** Mount, run the first frame, then the body, and always unmount. */
function withView(fn: (m: Mounted) => void, scanner?: FakeScanner, props?: { widthPx?: number; withVideo?: boolean }) {
  const m = mount(scanner, props);
  try { frame(); fn(m); } finally { m.unmount(); }
}

const q = (host: Element, id: string): HTMLElement => {
  const el = host.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  assert.ok(el, `no element with data-testid ${id}`);
  return el;
};
const qa = (host: Element, sel: string) => [...host.querySelectorAll<HTMLElement>(sel)];
const callsOf = (el: Element, name?: string) => (recs.get(el)?.calls ?? []).filter(c => !name || c.name === name);
const drawCount = (m: Mounted) => callsOf(q(m.host, 'pano-live-canvas'), 'drawImage').length;
const num = (css: string) => parseFloat(css);

// ---- Pure helpers ----------------------------------------------------------

test('railColour: green inside the green band, amber inside the amber band, red outside, none without a reading', () => {
  assert.equal(railColour(null, BAND0), 'none');
  assert.equal(railColour(NaN, BAND0), 'none');
  assert.equal(railColour(18, BAND0), 'green');
  assert.equal(railColour(24, BAND0), 'amber');
  assert.equal(railColour(8, BAND0), 'amber');
  assert.equal(railColour(40, BAND0), 'red');
  assert.equal(railColour(-3, BAND0), 'red');
  // The band edges belong to the band (SPEC-v2 2.4: green [12, 23.36], amber [5, 25.36]).
  assert.equal(railColour(12, BAND0), 'green');
  assert.equal(railColour(11.99, BAND0), 'amber');
  assert.equal(railColour(23.36, BAND0), 'green');
  assert.equal(railColour(23.37, BAND0), 'amber');
  assert.equal(railColour(5, BAND0), 'amber');
  assert.equal(railColour(4.99, BAND0), 'red');
  assert.equal(railColour(25.36, BAND0), 'amber');
  assert.equal(railColour(25.37, BAND0), 'red');
  // After the focal lock on the S25 (p1 = 22.98, h = 30.98): green [14.98, 26.98], amber [7.98, 30.98].
  const locked = { green: [14.98, 26.98] as const, amber: [7.98, 30.98] as const };
  assert.equal(railColour(26.98, locked), 'green');
  assert.equal(railColour(30.98, locked), 'amber');
  assert.equal(railColour(31, locked), 'red');
});

test('ribbonViewFor: 150 degrees across the width, altitude 60 down to -10, heading wrapped into [0, 360)', () => {
  assert.equal(RIBBON_SPAN_DEG, 150); assert.equal(RIBBON_ALT_TOP, 60); assert.equal(RIBBON_ALT_BOTTOM, -10);
  assert.deepEqual(ribbonViewFor(10, 390), { centreAz: 10, altTop: 60, altBottom: -10, pxPerDeg: 2.6, widthPx: 390 });
  near(ribbonViewFor(10, 360).pxPerDeg, 2.4, 1e-12);
  assert.equal(ribbonViewFor(370, 390).centreAz, 10);
  assert.equal(ribbonViewFor(-20, 390).centreAz, 340);
  // Altitude 60 to -10 over 2.6 px/deg is the 182 px of SPEC-v2 2.5.
  near((60 - -10) * ribbonViewFor(0, 390).pxPerDeg, 182, 1e-9);
});

// ---- Structure and the rail ------------------------------------------------

test('the preview box holds the video slot, two slit lines and a roll tick, and the view has no buttons', () => {
  withView(m => {
    const box = q(m.host, 'pano-preview');
    assert.ok(box.querySelector('#slot-video'), 'the video slot is inside the preview box');
    assert.equal(qa(box, '.pano-slit').length, 2);
    assert.ok(box.querySelector('[data-testid="pano-roll"]'));
    assert.equal(qa(m.host, 'button, a, input, select, textarea, [role="button"], [tabindex]').length, 0, 'ScanView renders nothing interactive');
  });
});

test('the slit lines mark the central 6 degrees: narrower on a wider lens, symmetric about the middle', () => {
  const lines = (m: Mounted) => qa(q(m.host, 'pano-preview'), '.pano-slit').map(e => num(e.style.left));
  const half = (fov: number) => Math.tan(3 * Math.PI / 180) / Math.tan(fov / 2 * Math.PI / 180) / 2 * 100;
  for (const fov of [43, 55.4]) {
    const sc = new FakeScanner();
    sc.status = { ...sc.status, focal: { state: 'prior', shortFovDeg: fov } };
    withView(m => {
      const [l, r] = lines(m);
      near(l, 50 - half(fov), 0.01, `left at ${fov}`);
      near(r, 50 + half(fov), 0.01, `right at ${fov}`);
    }, sc);
  }
});

test('rail colour follows the band, and the marker sits at its altitude on the rail scale', () => {
  const sc = new FakeScanner();
  withView(m => {
    const rail = q(m.host, 'pano-rail');
    const marker = () => q(m.host, 'pano-rail-marker');
    const expect = (elev: number | null, colour: string) => {
      sc.set({ elevationDeg: elev }); frame();
      assert.equal(rail.dataset.colour, colour, `elevation ${elev}`);
    };
    expect(18, 'green');
    near(num(marker().style.bottom), (18 - -10) / 60 * 100, 0.01, 'marker at 18');
    expect(24, 'amber');
    expect(40, 'red');
    expect(2, 'red');
    // A reading rounded for display must not change the class: 23.36 is green, 23.364 would round to 23.4.
    expect(23.36, 'green');
    expect(23.37, 'amber');
    expect(null, 'none');
    assert.equal(rail.querySelector('[data-testid="pano-rail-marker"]'), null, 'no marker without a reading');
    // After the lock the band moves, and the rail follows the new band.
    sc.set({ elevationDeg: 26, targetPitchDeg: 22.98, pitchBand: { green: [14.98, 26.98], amber: [7.98, 30.98] } }); frame();
    assert.equal(rail.dataset.colour, 'green');
    assert.equal(num(getComputedStyle(rail).width), 44, 'the rail is 44 px wide');
  }, sc);
});

test('the roll tick rotates with the roll and turns red above 15 degrees, either way', () => {
  const sc = new FakeScanner();
  withView(m => {
    const tick = q(m.host, 'pano-roll');
    const check = (roll: number | null, alert: boolean) => {
      sc.set({ rollDeg: roll }); frame();
      assert.equal(tick.dataset.alert, String(alert), `roll ${roll}`);
    };
    check(0, false); check(15, false); check(15.2, true); check(-16, true); check(-14, false);
    // The tick shows a rounded roll, but the alert is decided on the reading: 15.04 rounds to 15.0 and is still over.
    check(15.04, true); check(-15.04, true); check(14.96, false);
    sc.set({ rollDeg: 12 }); frame();
    assert.match(tick.style.transform, /^rotate\(12deg\)$/);
    sc.set({ rollDeg: null }); frame();
    assert.equal(tick.hidden, true, 'no reading, no tick');
  }, sc);
});

// ---- The cue line ----------------------------------------------------------

test('the cue line is a status region with a fixed height that its text never changes', () => {
  const sc = new FakeScanner();
  withView(m => {
    const cue = q(m.host, 'pano-cue');
    assert.equal(cue.getAttribute('role'), 'status');
    assert.equal(cue.textContent, sc.status.cue);
    const heightOf = () => getComputedStyle(cue).height;
    const h0 = heightOf();
    assert.match(h0, /^\d+(\.\d+)?px$/, `the cue line has a fixed pixel height, got ${h0}`);
    assert.ok(num(h0) >= 60, `room for four lines, got ${h0}`);
    const texts = [
      ['', 'ready', 'info'],
      ['Go back 7 degrees to fill the gap.', 'gap', 'warn'],
      ['Motion sensors are blocked for this site. In Brave or Chrome, open Site settings > Motion sensors and allow this site, then reopen the scan.', 'error', 'block'],
    ] as const;
    for (const [text, key, kind] of texts) {
      sc.set({ cue: text, cueKey: key, cueKind: kind }); frame();
      assert.equal(cue.textContent, text);
      assert.equal(cue.dataset.key, key);
      assert.equal(cue.dataset.kind, kind);
      assert.equal(heightOf(), h0, `the height stays ${h0} for cue ${key}`);
    }
  }, sc);
});

// ---- The 720 cells and the labels ------------------------------------------

test('720 cells carry their classes, and a coverage array changed in place is picked up', () => {
  const sc = new FakeScanner();
  const names = ['none', 'sensor', 'blurred', 'aligned'];
  const cov = new Uint8Array(PROFILE_BINS);
  for (let i = 0; i < PROFILE_BINS; i++) cov[i] = (i * 7 + (i >> 3)) % 4;
  sc.status = { ...sc.status, coverage: cov };
  withView(m => {
    const check = (what: string) => {
      const cells = qa(m.host, '.pano-cell');
      assert.equal(cells.length, 720, `${what}: 720 cells`);
      for (let i = 0; i < PROFILE_BINS; i++) {
        assert.ok(cells[i].classList.contains(`pano-cell-${names[cov[i]]}`), `${what}: cell ${i} is ${names[cov[i]]}, has ${cells[i].className}`);
      }
    };
    check('first');
    assert.equal(PixClass.Aligned, 3);
    // The scanner may well mutate one array in place, round after round; a snapshot that kept a reference to the
    // scanner's array, or compared by identity, would miss the second round.
    for (const round of ['first', 'second', 'third']) {
      for (let i = 0; i < PROFILE_BINS; i++) cov[i] = (cov[i] + 1 + (i % 3)) % 4;
      sc.emit(); frame();
      check(`after the ${round} in-place change`);
      assert.equal(qa(m.host, '.pano-cell-aligned').length, cov.filter(v => v === 3).length);
    }
  }, sc);
});

test('N/E/S/W labels appear only once a north estimate exists, at the scan azimuth of each magnetic bearing', () => {
  const sc = new FakeScanner();
  withView(m => {
    const labels = () => qa(m.host, '.pano-compass');
    assert.equal(labels().length, 0, 'no north, no labels');
    sc.set({ northOffsetDeg: 30 }); frame();
    assert.deepEqual(labels().map(e => e.textContent), ['N', 'E', 'S', 'W']);
    // magnetic = scan + offset, so a scan azimuth is the magnetic bearing minus the offset.
    const at = (dir: string) => num(labels().find(e => e.dataset.dir === dir)!.style.left);
    near(at('N'), mod(0 - 30, 360) / 360 * 100, 0.01, 'N');
    near(at('E'), mod(90 - 30, 360) / 360 * 100, 0.01, 'E');
    near(at('S'), mod(180 - 30, 360) / 360 * 100, 0.01, 'S');
    near(at('W'), mod(270 - 30, 360) / 360 * 100, 0.01, 'W');
    sc.set({ northOffsetDeg: 0 }); frame();
    near(at('N'), 0, 0.01, 'N at offset 0'); near(at('E'), 25, 0.01, 'E at offset 0');
    sc.set({ northOffsetDeg: null }); frame();
    assert.equal(labels().length, 0, 'north lost, labels gone');
  }, sc);
});

// ---- The pace gauge --------------------------------------------------------

test('pace classes: the gauge takes the scanner\'s class, reads "Keep turning" when idle, and the marker moves with the rate', () => {
  const sc = new FakeScanner();
  withView(m => {
    const gauge = q(m.host, 'pano-pace');
    const marker = q(m.host, 'pano-pace-marker');
    const seen: number[] = [];
    for (const [pace, rate] of [['idle', 2], ['good', 20], ['fast', 32], ['too-fast', 48]] as const) {
      sc.set({ pace, rateDegS: rate, rateMaxDegS: 40 }); frame();
      assert.equal(gauge.dataset.pace, pace);
      assert.equal(qa(gauge, '.pano-pace-label')[0].textContent, pace === 'idle' ? 'Keep turning' : `${rate}°/s`);
      seen.push(num(marker.style.left));
    }
    assert.ok(seen.every((v, i) => v >= 0 && v <= 100 && (i === 0 || v > seen[i - 1])), `the marker rises with the rate: ${seen}`);
    const zones = qa(gauge, '.pano-pace-zone');
    assert.deepEqual(zones.map(z => z.dataset.zone), ['idle', 'good', 'fast', 'too-fast']);
    near(zones.reduce((s, z) => s + num(z.style.flexBasis), 0), 100, 0.05, 'zones fill the gauge');
    // 5 deg/s, 0.7 x rateMax and rateMax are the zone edges (SPEC-v2 2.5): 5, 28 and 40 of a 50 deg/s scale.
    near(num(zones[0].style.flexBasis), 10, 0.01, 'idle zone'); near(num(zones[1].style.flexBasis), 46, 0.01, 'good zone');
    near(num(zones[2].style.flexBasis), 24, 0.01, 'fast zone'); near(num(zones[3].style.flexBasis), 20, 0.01, 'too-fast zone');
    // A slower frame rate lowers rateMax, and the zones move with it.
    sc.set({ rateMaxDegS: 15, pace: 'fast', rateDegS: 12 }); frame();
    near(num(qa(gauge, '.pano-pace-zone')[3].style.flexBasis), 20, 0.01, 'red zone is a fifth of the scale at any rateMax');
  }, sc);
});

// ---- The live mesh: the proof of the named mutant ---------------------------

test('the live mesh is redrawn only when frameNo changes, counted as drawImage calls on the overlay context', () => {
  const sc = new FakeScanner();
  withView(m => {
    const base = drawCount(m);
    const per = gridMesh().length;
    assert.equal(per, 4);
    // Drawing happens in the animation frame, not in the notification.
    sc.set({ frameNo: 1, headingDeg: 40 });
    assert.equal(drawCount(m), base, 'nothing is drawn by the notification itself');
    assert.equal(rafQueue.size, 1, 'the notification scheduled exactly one frame');
    frame();
    assert.equal(drawCount(m), base + per, 'a new camera frame redraws every triangle once');
    const afterNewFrame = drawCount(m);
    // Notifications that carry no new camera frame: a cue change (tick), a plain re-emit, a pitch change, a repeated frameNo.
    sc.set({ cue: 'The camera image stopped.', cueKey: 'stalled-camera', cueKind: 'warn' }); frame();
    sc.emit(); frame();
    sc.set({ elevationDeg: 21 }); frame();
    sc.set({ frameNo: 1 }); frame();
    frame(); frame();
    assert.equal(drawCount(m), afterNewFrame, 'no new frameNo, no redraw');
    // The next camera frame draws again; several in one animation frame coalesce into one redraw.
    sc.set({ frameNo: 2 }); frame();
    assert.equal(drawCount(m), afterNewFrame + per);
    sc.set({ frameNo: 3, headingDeg: 41 }); sc.set({ frameNo: 4, headingDeg: 42 }); sc.set({ frameNo: 5, headingDeg: 43 });
    assert.equal(rafQueue.size, 1, 'three notifications, one scheduled frame');
    frame();
    assert.equal(drawCount(m), afterNewFrame + 2 * per);
    near(sc.views[sc.views.length - 1].centreAz, 43, 1e-9, 'the mesh is projected at the heading of the status it is drawn for');
    // Every redraw was timed and reported; a number of milliseconds, never negative.
    assert.equal(sc.meshDraws.length, 4, 'the first frame, frameNo 1, 2 and 5');
    assert.ok(sc.meshDraws.every(ms => Number.isFinite(ms) && ms >= 0), `timings ${sc.meshDraws}`);
  }, sc);
});

test('the mesh is drawn as clipped triangles at 50 percent, each mapped from the live source onto the ribbon', () => {
  const sc = new FakeScanner();
  withView(m => {
    sc.set({ frameNo: 7 }); frame();
    const canvas = q(m.host, 'pano-live-canvas');
    const calls = callsOf(canvas);
    const lastClear = calls.map(c => c.name).lastIndexOf('clearRect');
    const drawn = calls.slice(lastClear);
    assert.deepEqual(drawn[0].args, [0, 0, 390, 182], 'the overlay is cleared first, at the ribbon size');
    const names = drawn.map(c => c.name);
    const alphas = drawn.filter(c => c.name === 'set:globalAlpha').map(c => c.args[0]);
    assert.deepEqual(alphas, [0.5, 1], 'half alpha for the triangles, back to full for the outline');
    assert.ok(names.indexOf('set:globalAlpha') < names.indexOf('save') && names.lastIndexOf('set:globalAlpha') > names.lastIndexOf('restore'),
      'the alpha is set before the first triangle and reset after the last');
    assert.equal(names.filter(n => n === 'clip').length, 4);
    assert.equal(names.filter(n => n === 'save').length, 4);
    assert.equal(names.filter(n => n === 'restore').length, 4);
    assert.ok(names.indexOf('clip') < names.indexOf('setTransform') && names.indexOf('setTransform') < names.indexOf('drawImage'), 'clip, then transform, then draw');
    const tris = gridMesh();
    const transforms = drawn.filter(c => c.name === 'setTransform');
    const draws = drawn.filter(c => c.name === 'drawImage');
    assert.equal(transforms.length, 4);
    transforms.forEach((t, i) => {
      const [a, b, c, d, e, f] = t.args as number[];
      for (let k = 0; k < 3; k++) {
        const sx = tris[i].src[2 * k], sy = tris[i].src[2 * k + 1];
        near(a * sx + c * sy + e, tris[i].dst[2 * k], 1e-9, `triangle ${i} vertex ${k} x`);
        near(b * sx + d * sy + f, tris[i].dst[2 * k + 1], 1e-9, `triangle ${i} vertex ${k} y`);
      }
      assert.deepEqual(draws[i].args, [sc.source, 0, 0], 'the 3-argument drawImage of the live source');
    });
    // The outline is the mesh boundary: 2 x 1 cells have 6 outer edges, drawn once, white.
    const outline = drawn.slice(names.lastIndexOf('restore') + 1);
    assert.equal(outline.filter(c => c.name === 'lineTo').length, 6, 'six outer edges');
    assert.equal(outline.filter(c => c.name === 'stroke').length, 1);
    assert.equal(recs.get(canvas)!.props.strokeStyle, '#ffffff');
  }, sc);
});

test('with no pose the overlay is cleared and nothing is drawn or timed', () => {
  const sc = new FakeScanner();
  withView(m => {
    const before = drawCount(m), timed = sc.meshDraws.length;
    const clears = callsOf(q(m.host, 'pano-live-canvas'), 'clearRect').length;
    sc.mesh = null;
    sc.set({ frameNo: 9 }); frame();
    assert.equal(drawCount(m), before);
    assert.equal(sc.meshDraws.length, timed, 'an empty redraw is not a mesh timing');
    assert.equal(callsOf(q(m.host, 'pano-live-canvas'), 'clearRect').length, clears + 1, 'the old frame is wiped');
  }, sc);
});

// ---- The ribbon ------------------------------------------------------------

test('dirty rectangles are copied into both halves of the 2160 x 300 canvas by putImageData in the animation frame', () => {
  const sc = new FakeScanner();
  const m = mount(sc);
  try {
    const canvas = q(m.host, 'pano-ribbon-canvas') as HTMLCanvasElement;
    assert.equal(canvas.width, 2160); assert.equal(canvas.height, 300);
    assert.equal(sc.takeDirtyCalls, 0, 'nothing is taken before the first frame');
    frame();
    let puts = callsOf(canvas, 'putImageData');
    assert.equal(puts.length, 2, 'the first frame paints the whole raster once per half');
    assert.deepEqual(puts.map(p => p.args.slice(1)), [[0, 0, 0, 0, 1080, 300], [1080, 0, 0, 0, 1080, 300]]);
    assert.equal(puts[0].args[0].data, sc.pix, 'the ImageData wraps the scanner\'s own buffer, nothing is copied');
    assert.equal(puts[1].args[0], puts[0].args[0]);

    let mark = puts.length;
    sc.dirty = [{ x: 100, y: 50, w: 20, h: 30 }];
    sc.emit();
    assert.equal(sc.takeDirtyCalls, 1, 'the notification does not take the dirty list');
    assert.equal(callsOf(canvas, 'putImageData').length, mark, 'and copies nothing');
    frame();
    puts = callsOf(canvas, 'putImageData').slice(mark);
    assert.deepEqual(puts.map(p => p.args.slice(1)), [[0, 0, 100, 50, 20, 30], [1080, 0, 100, 50, 20, 30]]);

    // A rectangle past column 1080 wraps to column 0: two pieces, each in both halves.
    mark = callsOf(canvas, 'putImageData').length;
    sc.dirty = [{ x: 1070, y: 10, w: 30, h: 5 }];
    sc.emit(); frame();
    puts = callsOf(canvas, 'putImageData').slice(mark);
    assert.deepEqual(puts.map(p => p.args.slice(1)), [
      [0, 0, 1070, 10, 10, 5], [1080, 0, 1070, 10, 10, 5],
      [0, 0, 0, 10, 20, 5], [1080, 0, 0, 10, 20, 5],
    ]);

    // Several rectangles in one frame, and a frame with none paints nothing.
    mark = callsOf(canvas, 'putImageData').length;
    sc.dirty = [{ x: 0, y: 0, w: 4, h: 4 }, { x: 500, y: 200, w: 6, h: 9 }];
    sc.emit(); frame();
    assert.equal(callsOf(canvas, 'putImageData').length - mark, 4);
    mark = callsOf(canvas, 'putImageData').length;
    sc.emit(); frame();
    assert.equal(callsOf(canvas, 'putImageData').length, mark);
  } finally { m.unmount(); }
});

test('the ribbon crops to 150 x 70 degrees at 2.6 px/deg (390 x 182) and scrolls so the heading is in the middle', () => {
  const sc = new FakeScanner();
  withView(m => {
    const box = q(m.host, 'pano-ribbon'), track = q(m.host, 'pano-ribbon-track'), canvas = q(m.host, 'pano-ribbon-canvas');
    const W = 390, ppd = W / 150;
    assert.equal(box.style.width, '390px'); assert.equal(box.style.height, '182px');
    near(num(canvas.style.width), 2160 / 3 * ppd, 0.01, 'canvas width: 3 columns per degree'); near(num(canvas.style.height), 300 / 2.99 * ppd, 0.01, 'canvas height: 2.99 rows per degree');
    // Altitude 60 is the top edge of the window: the centre of row r with altitude 90 - r / 2.99 lands at (60 - alt) * ppd.
    for (const r of [90, 150, 269, 299]) {
      const alt = 90 - r / 2.99;
      near(num(track.style.top) + (r + 0.5) * num(canvas.style.height) / 300, (60 - alt) * ppd, 0.02, `row ${r}`);
    }
    for (const heading of [0, 10, 74.9, 75, 200, 359.9, 400, -20]) {
      sc.set({ headingDeg: heading }); frame();
      const m2 = /^translate3d\((-?[\d.]+)px, 0, 0\)$/.exec(track.style.transform);
      assert.ok(m2, `transform ${track.style.transform}`);
      const tx = parseFloat(m2[1]);
      // The azimuth under the middle of the window, on a canvas that holds 720 degrees, is the heading.
      const centreAz = (W / 2 - tx) / ppd;
      near(mod(centreAz - heading + 180, 360) - 180, 0, 0.01, `heading ${heading}`);
      assert.ok(-tx >= -0.01 && -tx + W <= num(canvas.style.width) + 0.01, `the window stays on the canvas at heading ${heading}`);
    }
  }, sc);
});

test('the ribbon is laid out for the width the page gives it: 360 px, and again after a resize', () => {
  const proto = win.Element.prototype;
  const original = proto.getBoundingClientRect;
  let width = 360;
  proto.getBoundingClientRect = function (this: Element) {
    return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, height: 0, width: this.classList.contains('pano-view') ? width : 0, toJSON() {} };
  };
  try {
    const sc = new FakeScanner();
    withView(m => {
      const box = q(m.host, 'pano-ribbon');
      assert.equal(box.style.width, '360px'); assert.equal(box.style.height, '168px');
      assert.equal(q(m.host, 'pano-live-canvas').getAttribute('width'), '360');
      sc.set({ frameNo: 1, headingDeg: 20 }); frame();
      const v = sc.views[sc.views.length - 1];
      assert.deepEqual(v, ribbonViewFor(20, 360));
      near(v.pxPerDeg, 2.4, 1e-12);
      // A resize clears the overlay canvas, so the mesh is drawn again for the new size without a new camera frame.
      const before = drawCount(m);
      width = 320;
      act(() => { win.dispatchEvent(new win.Event('resize')); });
      frame();
      assert.equal(box.style.width, '320px'); assert.equal(box.style.height, '149.33px');
      assert.equal(drawCount(m), before + 4, 'redrawn once at the new size');
      assert.equal(sc.views[sc.views.length - 1].widthPx, 320);
    }, sc);
  } finally { proto.getBoundingClientRect = original; }
});

test('without a measured width the nominal widthPx is used, 390 by default', () => {
  withView(m => { assert.equal(q(m.host, 'pano-ribbon').style.width, '390px'); });
  withView(m => { assert.equal(q(m.host, 'pano-ribbon').style.width, '300px'); assert.equal(q(m.host, 'pano-ribbon').style.height, '140px'); }, undefined, { widthPx: 300 });
});

test('Tall spans show as red ticks on the ribbon, one per copy of the 720 degrees, a wrapping span included', () => {
  const sc = new FakeScanner();
  withView(m => {
    assert.equal(qa(m.host, '.pano-tall').length, 0);
    sc.set({ tallSpans: [{ from: 100, to: 130 }] }); frame();
    let ticks = qa(m.host, '.pano-tall');
    assert.equal(ticks.length, 3);
    [(100 - 360) * 2.6, 100 * 2.6, (100 + 360) * 2.6].forEach((left, i) => near(num(ticks[i].style.left), left, 0.01, `tick ${i}`));
    ticks.forEach(t => near(num(t.style.width), 78, 0.01, 'the 30 degree span is 78 px wide'));
    sc.set({ tallSpans: [{ from: 350, to: 10 }] }); frame();
    ticks = qa(m.host, '.pano-tall');
    assert.equal(ticks.length, 3);
    ticks.forEach(t => near(num(t.style.width), 52, 0.01, 'a 20 degree wrapping span'));
    [(350 - 360) * 2.6, 350 * 2.6, (350 + 360) * 2.6].forEach((left, i) => near(num(ticks[i].style.left), left, 0.01, `wrapping tick ${i}`));
    sc.set({ tallSpans: [] }); frame();
    assert.equal(qa(m.host, '.pano-tall').length, 0);
  }, sc);
});

test('sensor-placed and blurred columns carry an amber tint, one pixel per coverage cell, in both halves', () => {
  const sc = new FakeScanner();
  const cov = new Uint8Array(PROFILE_BINS);
  for (let i = 100; i < 110; i++) cov[i] = PixClass.Sensor;
  cov[200] = PixClass.Blurred; cov[300] = PixClass.Aligned;
  sc.status = { ...sc.status, coverage: cov };
  withView(m => {
    const tint = q(m.host, 'pano-tint') as HTMLCanvasElement;
    assert.equal(tint.width, 1440); assert.equal(tint.height, 1);
    const puts = callsOf(tint, 'putImageData');
    const data = (puts[puts.length - 1].args[0] as FakeImageData).data;
    const alpha = (px: number) => data[px * 4 + 3];
    for (const px of [100, 105, 109, 200, 720 + 100, 720 + 109, 720 + 200]) assert.ok(alpha(px) > 0, `pixel ${px} tinted`);
    for (const px of [0, 99, 110, 199, 201, 300, 720 + 99, 720 + 110, 720 + 300]) assert.equal(alpha(px), 0, `pixel ${px} clear`);
    assert.deepEqual([...data.subarray(100 * 4, 100 * 4 + 3)], [237, 189, 128], 'amber');
    // It follows the coverage: a cell that becomes aligned loses its tint.
    cov[105] = PixClass.Aligned; sc.emit(); frame();
    const again = callsOf(tint, 'putImageData');
    assert.equal((again[again.length - 1].args[0] as FakeImageData).data[105 * 4 + 3], 0);
  }, sc);
});

test('a dotted line marks the top of the painted run at altitude 0, in the columns that were painted', () => {
  const sc = new FakeScanner();
  const col = (x: number, y0: number, y1: number) => { for (let y = y0; y <= y1; y++) sc.pix[(y * PANO_W + x) * 4 + 3] = 255; };
  col(96, 150, 269);    // a run containing altitude 0 (row 269) up to row 150
  col(99, 150, 269);    // a dash-free column (the line is one degree on, one degree off)
  col(98, 10, 50);      // painted, but the run does not contain altitude 0
  withView(m => {
    const top = q(m.host, 'pano-photo-top');
    const mark = callsOf(top).length;
    sc.dirty = [{ x: 96, y: 150, w: 4, h: 120 }];
    sc.emit(); frame();
    const calls = callsOf(top).slice(mark);
    assert.deepEqual(calls.filter(c => c.name === 'clearRect').map(c => c.args), [[96, 0, 4, 300], [1176, 0, 4, 300]]);
    assert.deepEqual(calls.filter(c => c.name === 'fillRect').map(c => c.args), [[96, 149, 1, 3], [1176, 149, 1, 3]],
      'only column 96: 97 is unpainted, 98 has no run at altitude 0, 99 falls in a gap of the dots');
  }, sc);
});

// ---- Phases, lifetime ------------------------------------------------------

test('the ribbon, bar and pace gauge show while scanning and stay out of the way before it; the rail and cue always show', () => {
  const sc = new FakeScanner();
  withView(m => {
    const group = q(m.host, 'pano-live-group');
    for (const [phase, shown] of [['idle', false], ['opening', false], ['ready', false], ['failed', false], ['scanning', true], ['paused', true], ['finishing', true], ['done', true]] as const) {
      sc.set({ phase }); frame();
      assert.equal(group.hidden, !shown, `phase ${phase}`);
      assert.equal(q(m.host, 'pano-view').dataset.phase, phase);
      assert.equal(q(m.host, 'pano-cue').getAttribute('role'), 'status');
    }
    // While hidden the canvases still take their dirty rectangles, so the raster is complete when the view appears.
    sc.set({ phase: 'ready' }); frame();
    const canvas = q(m.host, 'pano-ribbon-canvas');
    const mark = callsOf(canvas, 'putImageData').length;
    sc.dirty = [{ x: 10, y: 10, w: 5, h: 5 }];
    sc.emit(); frame();
    assert.equal(callsOf(canvas, 'putImageData').length, mark + 2);
  }, sc);
});

test('unmount unsubscribes, cancels a pending frame and draws nothing afterwards', () => {
  const sc = new FakeScanner();
  const m = mount(sc);
  frame();
  assert.equal(sc.listeners.size, 1);
  const live = q(m.host, 'pano-live-canvas'), ribbon = q(m.host, 'pano-ribbon-canvas');
  sc.set({ frameNo: 3 });
  assert.equal(rafQueue.size, 1);
  m.unmount();
  assert.equal(sc.listeners.size, 0, 'unsubscribed');
  assert.equal(rafQueue.size, 0, 'the pending frame is cancelled');
  const total = callsOf(live).length + callsOf(ribbon).length, timed = sc.meshDraws.length;
  sc.dirty = [{ x: 0, y: 0, w: 9, h: 9 }];
  sc.set({ frameNo: 4 }); flushRaf();
  assert.equal(callsOf(live).length + callsOf(ribbon).length, total);
  assert.equal(sc.meshDraws.length, timed);
});

test('a new scanner replaces the old one: the old subscription goes and the new one draws', () => {
  const a = new FakeScanner(), b = new FakeScanner();
  const host = win.document.createElement('div') as HTMLElement;
  win.document.body.append(host);
  const root = createRoot(host);
  rafQueue.clear();
  try {
    act(() => { root.render(createElement(ScanView, { scanner: a })); });
    frame();
    act(() => { root.render(createElement(ScanView, { scanner: b })); });
    assert.equal(a.listeners.size, 0); assert.equal(b.listeners.size, 1);
    frame();
    assert.ok(b.meshDraws.length >= 1, 'the new scanner\'s mesh was drawn');
    b.set({ cue: 'Paused. Tap Resume to carry on.', cueKey: 'paused' }); frame();
    assert.equal(q(host, 'pano-cue').textContent, 'Paused. Tap Resume to carry on.');
  } finally { act(() => { root.unmount(); }); host.remove(); }
});

// ---- The stylesheet --------------------------------------------------------

test('every pano class the view renders has a rule in sheets.css, and the cue line and rail are fixed in CSS', () => {
  const sc = new FakeScanner();
  sc.status = {
    ...sc.status, northOffsetDeg: 10, tallSpans: [{ from: 10, to: 20 }], elevationDeg: 18, rollDeg: 20,
    coverage: Uint8Array.from({ length: PROFILE_BINS }, (_, i) => i % 4), cueKind: 'warn',
  };
  withView(m => {
    const classes = new Set<string>();
    for (const el of qa(m.host, '*')) for (const c of el.classList) if (c.startsWith('pano-')) classes.add(c);
    assert.ok(classes.size >= 25, `only ${classes.size} pano classes found, is the walk working? ${[...classes]}`);
    const rules = new Set([...SHEETS_CSS.matchAll(/\.(pano-[a-z-]+)/g)].map(r => r[1]));
    const missing = [...classes].filter(c => !rules.has(c));
    assert.deepEqual(missing, [], `pano classes without a rule: ${missing}`);
    const style = (id: string) => getComputedStyle(q(m.host, id));
    assert.equal(style('pano-cue').overflowY, 'auto', 'a long cue scrolls inside its box instead of growing it');
    assert.ok(style('pano-view').display === 'flex');
  }, sc);
  // Nothing of the old panel's rules was removed: the legacy classes are all still there.
  for (const c of ['photosphere-capture', 'photosphere-preview', 'photosphere-coverage', 'photosphere-error', 'photosphere-actions']) {
    assert.ok(SHEETS_CSS.includes(`.${c}`), `.${c} is still defined`);
  }
});

console.log(`scanViewDom.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
