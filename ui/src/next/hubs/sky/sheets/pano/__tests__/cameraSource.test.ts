// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T09: the panorama scanner's camera source and capability probes, under the replay harness (SPEC-v2 7.2, cameraSource row).
//
// Mutant this file must catch (SPEC-v2 7.2): the generation guard removed from `CameraSource.open`. `a late grant is
// released` closes the source while the permission prompt is open, grants the stream afterwards, and asserts the stream
// was stopped; without the guard the grant is kept and the camera stays on behind a sheet that has gone.
//
// Mutants the review rounds of this file's own cases are written to catch, each named by the test that carries it:
//   the guard after the reopen removed (`a late grant on the reopen is released too`); a tie going to B (`pipeline A
//   wins a tie`); a luma average in place of Rec. 601 (`readbackTiny`); a slip threshold of a whole frame interval
//   (`VideoFrame pairing`); the exposure probe flagging a single value (`exposure probe`); the second permission request
//   made after the first resolves (`iOS motion permission`); a farbling threshold of `>= 2` (`farblingProbe`).
//
// The harness is today's `createHarness` (SHEETS/__sim__/harness.ts), read only. Its canvas context is a stub that draws
// nothing and answers `getImageData` from the current frame at the size last drawn, so every pixel assertion here is an
// assertion about the SIZE and ORDER of the draws; the canvases are wrapped to log them.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHarness, resample, type FrameMetadata, type ReplayHarness } from '../../__sim__/harness';
import type { Raster } from '../../__sim__/png';
import { pixelLuminance } from '../../photosphereGeometry';
import {
  ANALYSIS_LONG_PX, CameraSource, LIVE_SOURCE_H, LIVE_SOURCE_W, TINY_H, TINY_W, cameraErrorText, frameTime, percentile,
  preferredRearCamera, readbackSummary,
} from '../cameraSource';
import { checkPanoSupport, farblingProbe, queryMotionPermission, requestIosMotionPermission } from '../support';
import type { FrameMeta } from '../types';

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }

// The stubs below reach into globals and into jsdom, so they are loosely typed on purpose.
/* eslint-disable @typescript-eslint/no-explicit-any */
const g = globalThis as any;

// ---- fixtures ------------------------------------------------------------------------------------------------------

/** What a rVFC callback receives for frame i. Frame times are on the clock the test sets. */
const fm = (i: number, over: Partial<FrameMetadata> = {}): FrameMetadata => ({
  captureTime: 1000 + i * 33, mediaTime: i / 30, presentationTime: 1000 + i * 33 + 40, expectedDisplayTime: 1000 + i * 33 + 56,
  width: 720, height: 1280, presentedFrames: i, ...over,
});

function deliver(h: ReplayHarness, i: number, over: Partial<FrameMetadata> = {}) {
  assert.ok(h.deliverFrame(fm(i, over)), `frame ${i}: the source has a frame callback registered`);
}

/** A picture with structure in both directions, so a wrong size or a transposed draw cannot match. */
function pattern(width: number, height: number): Raster {
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const k = (y * width + x) * 4;
    pixels[k] = (x * 3) & 255; pixels[k + 1] = (y * 5) & 255; pixels[k + 2] = ((x + y) * 7) & 255; pixels[k + 3] = 255;
  }
  return { width, height, pixels };
}
function solid(width: number, height: number, r: number, gr: number, b: number): Raster {
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let k = 0; k < pixels.length; k += 4) { pixels[k] = r; pixels[k + 1] = gr; pixels[k + 2] = b; pixels[k + 3] = 255; }
  return { width, height, pixels };
}
const sameBytes = (a: Uint8ClampedArray, b: Uint8ClampedArray) =>
  Buffer.compare(Buffer.from(a.buffer, a.byteOffset, a.byteLength), Buffer.from(b.buffer, b.byteOffset, b.byteLength)) === 0;

function fakeTrack(extra: Record<string, unknown> = {}) {
  const t: any = {
    stopped: 0, readyState: 'live', muted: false, label: '', getSettings: () => ({ deviceId: 'rear' }), addEventListener() { },
    stop() { t.stopped++; }, ...extra,
  };
  return t;
}
const fakeStream = (track: any) => ({ getTracks: () => [track], getVideoTracks: () => [track] });
const device = (deviceId: string, label: string) => ({ kind: 'videoinput', deviceId, label, groupId: '' });

/** Script the page's media devices: what is listed, and the track each `getUserMedia` returns. */
function scriptMedia(o: { devices?: (call: number) => any[]; track?: (n: number, constraints: any) => any }) {
  const md = g.navigator.mediaDevices;
  const script = { requests: [] as any[], tracks: [] as any[], listings: 0 };
  if (o.devices) md.enumerateDevices = async () => o.devices!(++script.listings);
  md.getUserMedia = async (constraints: any) => {
    script.requests.push(constraints);
    const track = o.track ? o.track(script.requests.length, constraints) : fakeTrack();
    script.tracks.push(track);
    return fakeStream(track);
  };
  return script;
}

/** Collect every frame the source delivers. */
function collect(cam: CameraSource): FrameMeta[] {
  const metas: FrameMeta[] = [];
  cam.onFrame(m => { metas.push(m); });
  return metas;
}

type Role = 'analysis' | 'gpu' | 'live' | 'tiny' | 'probe';
interface CanvasLog {
  canvas: HTMLCanvasElement; role: Role; w: number; h: number; opts: unknown;
  draws: { argc: number; source: unknown }[]; reads: number; ctx: any;
}
/** Wrap every canvas context the page makes: log each draw (with its argument count and source) and each read, name the
 *  canvas by what it is for, count assignments to `width` and `height`, and optionally charge a cost to the harness
 *  clock per draw. A canvas is named by how it was made: the `willReadFrequently` ones are the analysis, tiny and probe
 *  canvases, told apart by size; the others are the GPU canvas and the live source, told apart by height. */
function instrument(h: ReplayHarness, cost?: (role: Role, fromCanvas: boolean) => number) {
  const proto = g.window.HTMLCanvasElement.prototype;
  const original = proto.getContext;
  const logs: CanvasLog[] = [];
  const sizeSets = new WeakMap<object, { w: number; h: number }>();
  const state = { throwOn: null as Role | null };
  for (const key of ['width', 'height'] as const) {
    const d = Object.getOwnPropertyDescriptor(proto, key);
    assert.ok(d && d.get && d.set, `HTMLCanvasElement.prototype.${key} is an accessor to wrap`);
    Object.defineProperty(proto, key, {
      configurable: true,
      get(this: object) { return d!.get!.call(this); },
      set(this: object, v: number) {
        const s = sizeSets.get(this) ?? { w: 0, h: 0 };
        if (key === 'width') s.w++; else s.h++;
        sizeSets.set(this, s);
        d!.set!.call(this, v);
      },
    });
  }
  const wrappers = new WeakMap<object, any>();
  proto.getContext = function (this: HTMLCanvasElement, type: string, opts?: unknown) {
    let wrapped = wrappers.get(this);
    if (wrapped) return wrapped;
    const inner = original.call(this, type, opts);
    const reads = !!(opts && (opts as any).willReadFrequently);
    const role: Role = reads ? (this.width === TINY_W && this.height === TINY_H ? 'tiny' : this.width === 64 ? 'probe' : 'analysis')
      : (this.height === LIVE_SOURCE_H ? 'live' : 'gpu');
    const log: CanvasLog = { canvas: this, role, w: this.width, h: this.height, opts, draws: [], reads: 0, ctx: null };
    wrapped = {
      drawImage: (...args: unknown[]) => {
        log.draws.push({ argc: args.length, source: args[0] });
        if (state.throwOn === role) throw new Error('draw refused');
        if (cost) h.setClock(h.now() + cost(role, (args[0] as any)?.tagName === 'CANVAS'));
        return inner.drawImage(...args);
      },
      getImageData: (...a: unknown[]) => { log.reads++; return inner.getImageData(...a); },
      createImageData: (...a: unknown[]) => inner.createImageData(...a),
      putImageData: (...a: unknown[]) => inner.putImageData(...a),
    };
    log.ctx = wrapped;
    logs.push(log);
    wrappers.set(this, wrapped);
    return wrapped;
  };
  return {
    logs, sizeSets,
    /** The newest canvas made for this role. */
    of(role: Role): CanvasLog {
      for (let i = logs.length - 1; i >= 0; i--) if (logs[i].role === role) return logs[i];
      throw new Error(`no ${role} canvas was made`);
    },
    throwOn(role: Role | null) { state.throwOn = role; },
  };
}

async function withCamera(
  video: { videoWidth: number; videoHeight: number },
  fn: (h: ReplayHarness, cam: CameraSource, ins: ReturnType<typeof instrument>) => Promise<void>,
  o: { benchFrames?: number; cost?: (role: Role, fromCanvas: boolean) => number; open?: boolean } = {},
) {
  const h = createHarness(video);
  const ins = instrument(h, o.cost);
  const cam = new CameraSource(o.benchFrames === undefined ? undefined : { benchFrames: o.benchFrames });
  try {
    if (o.open !== false) await cam.open(h.video);
    await fn(h, cam, ins);
  } finally {
    cam.close();
    h.dispose();
  }
}

const PORTRAIT = { videoWidth: 720, videoHeight: 1280 };

// ---- the readback benchmark ----------------------------------------------------------------------------------------

await test('pipeline A wins a tie; the benchmark alternates A then B over the first ten frames', async () => {
  await withCamera(PORTRAIT, async (h, cam, ins) => {
    h.setClock(1000);
    const metas = collect(cam);
    assert.equal(cam.bench, null, 'no verdict before the frames');
    for (let i = 1; i <= 9; i++) deliver(h, i);
    assert.equal(cam.bench, null, 'nine frames are not ten');
    deliver(h, 10);
    const bench = cam.bench!;
    // Under the frozen clock both read 0: a tie, and a tie goes to A. (Mutant: a tie goes to B.)
    assert.deepEqual(bench.aMs, { n: 5, p50: 0, p95: 0, max: 0 });
    assert.deepEqual(bench.bMs, { n: 5, p50: 0, p95: 0, max: 0 });
    assert.equal(bench.chosen, 'A');
    assert.equal(bench.smoothing, 'medium');
    const analysis = ins.of('analysis'), gpu = ins.of('gpu');
    assert.equal(analysis.draws.filter(d => d.source === h.video).length, 5, 'pipeline A draws the video straight in');
    assert.equal(analysis.draws.filter(d => d.source === gpu.canvas).length, 5, 'pipeline B draws the GPU canvas in');
    assert.equal(gpu.draws.length, 5, 'pipeline B draws the video into the GPU canvas');
    assert.ok(gpu.draws.every(d => d.source === h.video));
    assert.equal(gpu.reads, 0, 'the GPU canvas is never read back');
    // after the benchmark the winner is used and nothing else
    deliver(h, 11);
    cam.readback(metas[10])!;
    assert.equal(analysis.draws.filter(d => d.source === h.video).length, 6);
    assert.equal(gpu.draws.length, 5);
  });
});

await test('the benchmark chooses B when B is faster, and the later readbacks go through B', async () => {
  // A: one draw of the video, 5 ms. B: the video into the GPU canvas, 1 ms, then that canvas into the analysis one, 0.5 ms.
  const cost = (role: Role, fromCanvas: boolean) => role === 'gpu' ? 1 : role === 'analysis' ? (fromCanvas ? 0.5 : 5) : 0;
  await withCamera(PORTRAIT, async (h, cam, ins) => {
    const metas = collect(cam);
    for (let i = 1; i <= 10; i++) deliver(h, i);
    const bench = cam.bench!;
    assert.deepEqual(bench.aMs, { n: 5, p50: 5, p95: 5, max: 5 });
    assert.deepEqual(bench.bMs, { n: 5, p50: 1.5, p95: 1.5, max: 1.5 });
    assert.equal(bench.chosen, 'B');
    const gpu = ins.of('gpu');
    const before = gpu.draws.length;
    deliver(h, 11);
    const frame = cam.readback(metas[10])!;
    assert.equal(gpu.draws.length, before + 1, 'the readback went through the GPU canvas');
    assert.equal(frame.readbackMs, 1.5, 'readbackMs is drawImage plus getImageData, from the clock');
  }, { cost });
});

await test('the benchmark chooses A when A is faster', async () => {
  const cost = (role: Role, fromCanvas: boolean) => role === 'gpu' ? 4 : role === 'analysis' ? (fromCanvas ? 0.5 : 1) : 0;
  await withCamera(PORTRAIT, async (h, cam, ins) => {
    for (let i = 1; i <= 10; i++) deliver(h, i);
    assert.equal(cam.bench!.chosen, 'A');
    assert.equal(cam.bench!.bMs.p50, 4.5);
    assert.equal(ins.of('gpu').draws.length, 5, 'and B is not used again');
  }, { cost });
});

await test('a frame the benchmark read is not read twice when the scanner asks for it', async () => {
  await withCamera(PORTRAIT, async (h, cam, ins) => {
    const got: (ReturnType<CameraSource['readback']>)[] = [];
    cam.onFrame(m => { got.push(cam.readback(m)); got.push(cam.readback(m)); });
    deliver(h, 1);
    assert.ok(got[0], 'a frame came back');
    assert.equal(got[0], got[1], 'the same object, not a second read');
    assert.equal(ins.of('analysis').draws.length, 1, 'one draw for the benchmark and none for the scanner');
  });
});

await test('benchFrames 0 skips the benchmark, and readbacks use A', async () => {
  await withCamera(PORTRAIT, async (h, cam, ins) => {
    const metas = collect(cam);
    for (let i = 1; i <= 12; i++) deliver(h, i);
    assert.equal(cam.bench, null);
    assert.ok(cam.readback(metas[11]));
    assert.equal(ins.of('gpu').draws.length, 0);
  }, { benchFrames: 0 });
});

await test('readback returns the current frame at the analysis size, and null when it cannot', async () => {
  await withCamera(PORTRAIT, async (h, cam, ins) => {
    const raster = pattern(720, 1280);
    h.setFrame(raster, 5000);
    const metas = collect(cam);
    for (let i = 1; i <= 10; i++) deliver(h, i);
    deliver(h, 11);
    const f = cam.readback(metas[10])!;
    assert.equal(f.frameId, 11);
    assert.equal(f.t, metas[10].t);
    assert.equal(f.w, 180); assert.equal(f.h, 320);
    assert.equal(f.rgba.length, 180 * 320 * 4);
    assert.ok(sameBytes(f.rgba, resample(raster.pixels, 720, 1280, 180, 320)), 'the pixels are the frame at 180 x 320');
    assert.equal(f.readbackMs, 0, 'a frozen clock reads 0');
    ins.throwOn('analysis');
    assert.equal(cam.readback(metas[10]), null, 'a canvas that refuses is a null, not an exception');
    ins.throwOn(null);
    assert.ok(cam.readback(metas[10]));
  });
  await withCamera(PORTRAIT, async (h, cam) => {
    const before = new CameraSource();
    assert.equal(before.readback({} as FrameMeta), null, 'nothing is open');
    assert.equal(before.readbackTiny(), null);
    cam.close();
    assert.equal(cam.readback({} as FrameMeta), null, 'nothing is open after close');
    assert.equal(h.deliverFrame(fm(1)), false);
  });
});

// ---- canvases ------------------------------------------------------------------------------------------------------

await test('only the 5-argument drawImage is used, in every path', async () => {
  await withCamera(PORTRAIT, async (h, cam, ins) => {
    const metas = collect(cam);
    h.setFrame(pattern(720, 1280), 100);
    for (let i = 1; i <= 12; i++) { deliver(h, i); cam.refreshLiveSource(); if (i === 6) cam.readbackTiny(); }
    cam.readback(metas[11]);
    cam.readbackTiny();
    for (const role of ['analysis', 'gpu', 'live', 'tiny'] as const) {
      const log = ins.of(role);
      assert.ok(log.draws.length > 0, `the ${role} canvas was drawn to`);
      assert.ok(log.draws.every(d => d.argc === 5), `${role}: every drawImage has five arguments`);
    }
  });
  // and the source says the same, so a path this run does not reach cannot use another form
  for (const file of ['../cameraSource.ts', '../support.ts']) {
    const src = readFileSync(new URL(file, import.meta.url), 'utf8');
    for (const m of src.matchAll(/\.drawImage\(([^)]*)\)/g)) assert.equal(m[1].split(',').length, 5, `${file}: ${m[0]}`);
  }
});

await test('the analysis canvas size is set once, and follows the video aspect', async () => {
  await withCamera(PORTRAIT, async (h, cam, ins) => {
    assert.equal(cam.analysisW, 180); assert.equal(cam.analysisH, ANALYSIS_LONG_PX);
    const metas = collect(cam);
    for (let i = 1; i <= 14; i++) { deliver(h, i); cam.refreshLiveSource(); cam.readbackTiny(); cam.readback(metas[i - 1]); }
    for (const log of ins.logs) {
      const s = ins.sizeSets.get(log.canvas)!;
      assert.deepEqual(s, { w: 1, h: 1 }, `${log.role}: width and height are each assigned exactly once`);
    }
    const a = ins.of('analysis'), gp = ins.of('gpu'), lv = ins.of('live'), ty = ins.of('tiny');
    assert.deepEqual([a.w, a.h], [180, 320]);
    assert.deepEqual([gp.w, gp.h], [180, 320]);
    assert.deepEqual([lv.w, lv.h], [LIVE_SOURCE_W, LIVE_SOURCE_H]);
    assert.deepEqual([ty.w, ty.h], [TINY_W, TINY_H]);
    assert.equal(cam.liveSource(), lv.canvas);
    assert.equal(a.opts && (a.opts as any).willReadFrequently, true, 'the readback canvas is a software canvas');
    assert.ok(!(gp.opts && (gp.opts as any).willReadFrequently), 'the GPU canvas is not');
  });
  await withCamera({ videoWidth: 960, videoHeight: 1280 }, async (h, cam, ins) => {
    assert.deepEqual([cam.analysisW, cam.analysisH], [240, 320], '3:4');
    assert.deepEqual([ins.of('live').w, ins.of('live').h], [120, 160], 'the live source is the L1 size of a 3:4 frame');
    assert.deepEqual([ins.of('tiny').w, ins.of('tiny').h], [TINY_W, TINY_H]);
    assert.equal(h.deliverFrame(fm(1, { width: 960, height: 1280 })), true);
  });
  await withCamera({ videoWidth: 1280, videoHeight: 720 }, async (_h, cam) => {
    assert.deepEqual([cam.analysisW, cam.analysisH], [180, 320], 'a landscape video gets the same portrait canvas');
  });
});

await test('every canvas that downsamples asks for medium smoothing', async () => {
  await withCamera(PORTRAIT, async (_h, _cam, ins) => {
    for (const role of ['analysis', 'gpu', 'live', 'tiny'] as const) {
      const ctx = ins.of(role).ctx;
      assert.equal(ctx.imageSmoothingQuality, 'medium', role);
      assert.equal(ctx.imageSmoothingEnabled, true, role);
    }
  });
});

await test('the live source and the GPU canvas are never read back', async () => {
  await withCamera(PORTRAIT, async (h, cam, ins) => {
    const metas = collect(cam);
    for (let i = 1; i <= 12; i++) { deliver(h, i); cam.refreshLiveSource(); }
    cam.readback(metas[11]);
    const live = ins.of('live');
    assert.equal(live.draws.length, 12, 'drawn once per refresh');
    assert.ok(live.draws.every(d => d.source === h.video && d.argc === 5));
    assert.equal(live.reads, 0, 'never read back');
    assert.equal(ins.of('gpu').reads, 0);
    assert.ok(ins.of('analysis').reads > 0);
    assert.equal(ins.of('tiny').reads, 0, 'the tiny readback happens only when asked');
    cam.readbackTiny();
    assert.equal(ins.of('tiny').reads, 1);
  });
});

await test('readbackTiny is 45 x 80 Rec. 601 luma of the current frame', async () => {
  await withCamera(PORTRAIT, async (h, cam) => {
    h.setFrame(solid(720, 1280, 200, 100, 50), 10);
    const t = cam.readbackTiny()!;
    assert.equal(t.length, TINY_W * TINY_H);
    assert.ok(t instanceof Uint8Array);
    const want = Math.round(pixelLuminance(200, 100, 50));   // 124.2
    assert.equal(want, 124);
    assert.ok(t.every(v => v === want), 'a colour is read as luma, not as its average (116.7)');   // mutant: the mean
    // left half dark, right half bright
    const half = new Uint8ClampedArray(720 * 1280 * 4);
    for (let y = 0; y < 1280; y++) for (let x = 0; x < 720; x++) {
      const k = (y * 720 + x) * 4, v = x < 360 ? 40 : 200;
      half[k] = half[k + 1] = half[k + 2] = v; half[k + 3] = 255;
    }
    h.setFrame({ width: 720, height: 1280, pixels: half }, 20);
    const u = cam.readbackTiny()!;
    assert.equal(u[0], 40); assert.equal(u[TINY_W - 1], 200);
    assert.equal(u[(TINY_H - 1) * TINY_W], 40);
    assert.notEqual(u, t, 'a fresh array each call');
  });
});

// ---- frames: rVFC, the rAF fallback and the metadata -----------------------------------------------------------------

await test('a frame callback is delivered once per frame with its metadata, and null detaches it', async () => {
  await withCamera(PORTRAIT, async (h, cam) => {
    h.setClock(2000);
    assert.equal(cam.lastFrameAt, null);
    const metas = collect(cam);
    deliver(h, 1);
    deliver(h, 2, { captureTime: 1500, presentationTime: 1530, expectedDisplayTime: 2016, width: 720, height: 1280 });
    assert.equal(metas.length, 2);
    const [a, b] = metas;
    assert.equal(a.frameNo, 1); assert.equal(b.frameNo, 2);
    assert.equal(a.viaRvfc, true);
    assert.equal(a.t, fm(1).captureTime, 'the capture time is the frame time');
    assert.deepEqual(
      [b.captureTime, b.presentationTime, b.expectedDisplayTime, b.mediaTime, b.width, b.height, b.presentedFrames],
      [1500, 1530, 2016, 2 / 30, 720, 1280, 2],
    );
    assert.equal(cam.lastFrameAt, 2000);
    cam.onFrame(null);
    deliver(h, 3);
    assert.equal(metas.length, 2, 'detached: nothing more arrives');
    assert.equal(cam.lastFrameAt, 2000, 'the source still sees the frame');
    // missing and non-finite times are absent, and the frame time falls through to the next one
    cam.onFrame(m => { metas.push(m); });
    deliver(h, 4, { captureTime: Number.NaN, presentationTime: 1700 });
    deliver(h, 5, { captureTime: Number.NaN, presentationTime: Number.POSITIVE_INFINITY });
    assert.equal(metas[2].captureTime, null); assert.equal(metas[2].t, 1700);
    assert.equal(metas[3].presentationTime, null); assert.equal(metas[3].t, 2000, 'the callback time');
  });
});

await test('a callback that throws does not end the frame chain', async () => {
  await withCamera(PORTRAIT, async (h, cam) => {
    let calls = 0;
    cam.onFrame(() => { calls++; if (calls === 1) throw new Error('scanner fault'); });
    assert.throws(() => deliver(h, 1), /scanner fault/);
    deliver(h, 2);
    assert.equal(calls, 2, 'the registration was renewed before the callback ran');
  });
});

await test('frameTime follows 3.2: capture, then presentation, then the callback', () => {
  assert.equal(frameTime({ captureTime: 10, presentationTime: 20 }, 30), 10);
  assert.equal(frameTime({ presentationTime: 20 }, 30), 20);
  assert.equal(frameTime({}, 30), 30);
  assert.equal(frameTime({ captureTime: 0 }, 30), 0, 'zero is a time');
  assert.equal(frameTime({ captureTime: Number.NaN, presentationTime: 5 }, 30), 5);
  assert.equal(frameTime({ captureTime: Number.POSITIVE_INFINITY }, 30), 30);
});

/** A rAF the test steps by hand. */
function manualRaf() {
  const queue = new Map<number, (t: number) => void>();
  let next = 1;
  g.requestAnimationFrame = (fn: (t: number) => void) => { queue.set(next, fn); return next++; };
  g.cancelAnimationFrame = (id: number) => { queue.delete(id); };
  return {
    queue,
    step() { const q = [...queue.values()]; queue.clear(); for (const fn of q) fn(0); },
  };
}

await test('rAF fallback when rVFC is stubbed away: a frame is a moved media clock', async () => {
  const h = createHarness(PORTRAIT);
  const raf = manualRaf();
  const proto = g.window.HTMLVideoElement.prototype;
  proto.requestVideoFrameCallback = undefined;
  const cam = new CameraSource();
  try {
    h.setClock(2000); h.setFrame(pattern(720, 1280), 0);
    await cam.open(h.video);
    assert.equal(h.deliverFrame(fm(1)), false, 'no rVFC callback was registered');
    assert.equal(raf.queue.size, 1, 'a rAF poll is');
    const metas = collect(cam);
    raf.step();
    assert.equal(metas.length, 0, 'the clock has not moved: no frame');
    h.setClock(2033); h.setFrame(pattern(720, 1280), 33);
    raf.step();
    assert.equal(metas.length, 1);
    const m = metas[0];
    assert.equal(m.viaRvfc, false);
    assert.deepEqual([m.captureTime, m.presentationTime, m.expectedDisplayTime, m.mediaTime], [null, null, null, null]);
    assert.equal(m.t, 2033, 'the frame time is the callback time');
    assert.equal(m.frameNo, 1);
    assert.deepEqual([m.width, m.height, m.presentedFrames], [720, 1280, null]);
    raf.step();
    assert.equal(metas.length, 1, 'the same clock is the same frame');
    h.setClock(2066); h.setFrame(pattern(720, 1280), 66);
    raf.step();
    assert.equal(metas.length, 2);
    assert.equal(cam.lastFrameAt, 2066);
    assert.deepEqual(cam.lag.nowMinusCapture, { n: 0, p50: null, p95: null, max: null }, 'no metadata, no lag');
    // the benchmark runs on polled frames as well
    for (let i = 0; i < 10; i++) { h.setClock(2100 + i); h.setFrame(pattern(720, 1280), 100 + i); raf.step(); }
    assert.ok(cam.bench, 'the benchmark finished on the fallback path');
    assert.equal(cam.bench!.chosen, 'A');
    cam.close();
    assert.equal(raf.queue.size, 0, 'close cancels the poll');
  } finally {
    cam.close();
    h.dispose();
  }
});

await test('rAF fallback with getVideoPlaybackQuality: a frame is a larger totalVideoFrames', async () => {
  const h = createHarness(PORTRAIT);
  const raf = manualRaf();
  const proto = g.window.HTMLVideoElement.prototype;
  proto.requestVideoFrameCallback = undefined;
  let total = 0;
  proto.getVideoPlaybackQuality = () => ({ totalVideoFrames: total });
  const cam = new CameraSource();
  try {
    await cam.open(h.video);
    const metas = collect(cam);
    h.setFrame(pattern(720, 1280), 50);
    raf.step();
    assert.equal(metas.length, 0, 'the media clock moved but the counter did not');
    total = 3;
    raf.step();
    assert.equal(metas.length, 1);
    assert.equal(metas[0].presentedFrames, 3);
    raf.step();
    assert.equal(metas.length, 1);
  } finally {
    cam.close();
    h.dispose();
  }
});

await test('the lag distributions come from the frame metadata', async () => {
  await withCamera(PORTRAIT, async (h, cam) => {
    h.setClock(5000);
    // now - capture = i, present - capture = 2i, expected - now = 100 - i, for i in 1..20
    for (let i = 1; i <= 20; i++) {
      deliver(h, i, { captureTime: 5000 - i, presentationTime: 5000 - i + 2 * i, expectedDisplayTime: 5000 + 100 - i });
    }
    const lag = cam.lag;
    assert.deepEqual(lag.nowMinusCapture, { n: 20, p50: 10, p95: 19, max: 20 }, 'nearest rank');
    assert.deepEqual(lag.presentMinusCapture, { n: 20, p50: 20, p95: 38, max: 40 });
    assert.deepEqual(lag.expectedMinusNow, { n: 20, p50: 89, p95: 98, max: 99 });   // 80..99
    // a frame without a capture time adds to neither of the two that need it
    deliver(h, 21, { captureTime: Number.NaN });
    assert.equal(cam.lag.nowMinusCapture.n, 20);
    assert.equal(cam.lag.presentMinusCapture.n, 20);
    assert.equal(cam.lag.expectedMinusNow.n, 21);
  });
});

await test('the lag Stats are nearest-rank, which differs from an index into n - 1 at small n', async () => {
  for (const [n, p50, p95] of [[1, 1, 1], [2, 1, 2], [4, 2, 4], [5, 3, 5], [10, 5, 10], [20, 10, 19]] as const) {
    await withCamera(PORTRAIT, async (h, cam) => {
      h.setClock(5000);
      for (let i = 1; i <= n; i++) deliver(h, i, { captureTime: 5000 - i });   // now - capture = 1..n
      assert.deepEqual(cam.lag.nowMinusCapture, { n, p50, p95, max: n }, `n = ${n}`);
    }, { benchFrames: 0 });
  }
});

// ---- VideoFrame pairing ---------------------------------------------------------------------------------------------

await test('VideoFrame pairing counts slips against half the median frame interval, and closes every frame', async () => {
  const created: any[] = [];
  let stamp = 0;
  g.VideoFrame = class { timestamp = stamp; closed = false; constructor(public source: unknown) { created.push(this); } close() { this.closed = true; } };
  try {
    await withCamera(PORTRAIT, async (h, cam, ins) => {
      h.setClock(1000);
      h.setFrame(pattern(720, 1280), 1);
      const metas = collect(cam);
      for (let i = 1; i <= 6; i++) deliver(h, i);
      assert.equal(created.length, 0, 'no readback has been asked for');
      assert.deepEqual(cam.slips, { pairs: 0, slips: 0 });
      // 30 fps: the median interval is 33,333 us, half of it 16,667 us
      const us = (i: number) => (i / 30) * 1e6;
      stamp = us(6); assert.ok(cam.readback(metas[5]));
      stamp = us(6) + 10000; assert.ok(cam.readback(metas[5]));
      assert.deepEqual(cam.slips, { pairs: 2, slips: 0 }, '10 ms is inside half an interval');
      stamp = us(6) + 20000; assert.ok(cam.readback(metas[5]));   // mutant: a whole interval as the threshold
      assert.deepEqual(cam.slips, { pairs: 3, slips: 1 }, '20 ms is a slip');
      stamp = us(6) - 20000; assert.ok(cam.readback(metas[5]));
      assert.deepEqual(cam.slips, { pairs: 4, slips: 2 }, 'a frame that is early is a slip too');
      assert.equal(created.length, 4);
      assert.ok(created.every(vf => vf.closed), 'every VideoFrame is closed');
      assert.ok(ins.of('analysis').draws.every(d => d.source !== h.video || d.argc === 5));
      assert.ok(ins.of('analysis').draws.some(d => created.includes(d.source)), 'the readback drew from the VideoFrame');
      // a draw that throws still closes the frame, and is not a pair
      ins.throwOn('analysis');
      assert.equal(cam.readback(metas[5]), null);
      ins.throwOn(null);
      assert.equal(created.length, 5);
      assert.ok(created[4].closed, 'closed on the error path');
      assert.equal(cam.slips.pairs, 4);
      // a frame with no media time cannot be paired and does not construct a VideoFrame
      const noTime = { ...metas[5], mediaTime: null };
      assert.ok(cam.readback(noTime));
      assert.equal(created.length, 5);
      // a constructor that throws falls back to the element
      g.VideoFrame = class { constructor() { throw new Error('InvalidStateError'); } };
      assert.ok(cam.readback(metas[5]));
      assert.equal(cam.slips.pairs, 4);
    }, { benchFrames: 0 });
  } finally {
    delete g.VideoFrame;
  }
  // and without VideoFrame the element is drawn and nothing is counted
  await withCamera(PORTRAIT, async (h, cam) => {
    const metas = collect(cam);
    for (let i = 1; i <= 6; i++) deliver(h, i);
    assert.ok(cam.readback(metas[5]));
    assert.deepEqual(cam.slips, { pairs: 0, slips: 0 });
  }, { benchFrames: 0 });
});

// ---- opening: constraints, permissions, focus, settings ----------------------------------------------------------------

await test('constraints carry resizeMode, 1280 x 720 at 30 fps, and today\'s facing-mode or device-id rule', async () => {
  const h = createHarness(PORTRAIT);
  const script = scriptMedia({ devices: () => [device('a', ''), device('b', '')] });   // labels are empty before the grant
  const cam = new CameraSource();
  try {
    await cam.open(h.video);
    const first = script.requests[0];
    assert.equal(first.audio, false);
    assert.deepEqual(first.video.resizeMode, { ideal: 'none' });
    assert.deepEqual(first.video.width, { ideal: 1280 });
    assert.deepEqual(first.video.height, { ideal: 720 });
    assert.deepEqual(first.video.frameRate, { ideal: 30 });
    assert.deepEqual(first.video.facingMode, { ideal: 'environment' }, 'no label, no preference: the facing mode');
    assert.equal(first.video.deviceId, undefined);
    cam.close();
    await cam.open(h.video, 'chosen');
    const second = script.requests[1];
    assert.deepEqual(second.video.deviceId, { exact: 'chosen' }, 'a named camera is asked for exactly');
    assert.equal(second.video.facingMode, undefined);
    assert.deepEqual(second.video.resizeMode, { ideal: 'none' });
  } finally {
    cam.close();
    h.dispose();
  }
});

await test('enumerateDevices runs twice, and the camera is reopened when the labelled list names another', async () => {
  const h = createHarness(PORTRAIT);
  const script = scriptMedia({
    devices: call => call === 1
      ? [device('ultra', ''), device('main', '')]
      : [device('ultra', 'Back ultra wide camera'), device('main', 'Back main wide camera')],
    track: (_n, c) => fakeTrack({ getSettings: () => ({ deviceId: c.video.deviceId?.exact ?? 'ultra' }) }),
  });
  const cam = new CameraSource();
  try {
    await cam.open(h.video);
    assert.equal(script.listings, 2);
    assert.equal(script.requests.length, 2);
    assert.deepEqual(script.requests[0].video.facingMode, { ideal: 'environment' });
    assert.deepEqual(script.requests[1].video.deviceId, { exact: 'main' }, 'the labelled list picks the main camera');
    assert.equal(script.tracks[0].stopped, 1, 'the first stream is released');
    assert.equal(script.tracks[1].stopped, 0);
    assert.equal(cam.activeId, 'main');
    assert.deepEqual(cam.choices, [
      { deviceId: 'ultra', label: 'Back ultra wide camera' }, { deviceId: 'main', label: 'Back main wide camera' },
    ]);
    assert.equal(cam.settings.label, 'Back main wide camera', 'the label falls back to the device list');
    // a named camera is never second-guessed
    cam.close();
    await cam.open(h.video, 'ultra');
    assert.equal(script.requests.length, 3);
    assert.equal(cam.activeId, 'ultra');
  } finally {
    cam.close();
    h.dispose();
  }
});

await test('choices label an unnamed camera by its position', async () => {
  const h = createHarness(PORTRAIT);
  scriptMedia({ devices: () => [device('a', 'Back main wide camera'), device('b', ''), { kind: 'audioinput', deviceId: 'mic', label: 'Mic' }] });
  const cam = new CameraSource();
  try {
    await cam.open(h.video);
    assert.deepEqual(cam.choices, [{ deviceId: 'a', label: 'Back main wide camera' }, { deviceId: 'b', label: 'Camera 2' }]);
  } finally {
    cam.close();
    h.dispose();
  }
});

await test('a late grant is released (generation guard)', async () => {
  const h = createHarness(PORTRAIT);
  const md = g.navigator.mediaDevices;
  let grant!: (s: unknown) => void;
  let asked = 0;
  md.getUserMedia = () => { asked++; return new Promise(resolve => { grant = resolve; }); };
  const cam = new CameraSource();
  try {
    const pending = cam.open(h.video);
    while (!asked) await Promise.resolve();
    cam.close();   // the sheet is closed while the browser's permission prompt is open
    const track = fakeTrack();
    grant(fakeStream(track));
    await pending;
    assert.equal(track.stopped, 1, 'the stream granted after close() is stopped');   // mutant: the guard removed
    assert.equal(cam.playing, false);
    assert.equal(h.video.srcObject ?? null, null, 'the element never received it');
    assert.equal(h.deliverFrame(fm(1)), false, 'no frame loop was started');
    assert.equal(asked, 1);
    assert.equal(cam.activeId, undefined);
  } finally {
    cam.close();
    h.dispose();
  }
});

await test('a late grant on the reopen is released too', async () => {
  const h = createHarness(PORTRAIT);
  const md = g.navigator.mediaDevices;
  let listings = 0, asked = 0;
  md.enumerateDevices = async () => ++listings === 1
    ? [device('ultra', ''), device('main', '')]
    : [device('ultra', 'Back ultra wide camera'), device('main', 'Back main wide camera')];
  const tracks: any[] = [];
  let grant!: (s: unknown) => void;
  md.getUserMedia = () => {
    asked++;
    if (asked === 1) { const t = fakeTrack({ getSettings: () => ({ deviceId: 'ultra' }) }); tracks.push(t); return Promise.resolve(fakeStream(t)); }
    return new Promise(resolve => { grant = resolve; });
  };
  const cam = new CameraSource();
  try {
    const pending = cam.open(h.video);
    while (asked < 2) await Promise.resolve();
    cam.close();
    const second = fakeTrack({ getSettings: () => ({ deviceId: 'main' }) });
    grant(fakeStream(second));
    await pending;
    assert.equal(second.stopped, 1, 'the reopened stream, granted late, is stopped');   // mutant: the second guard removed
    assert.ok(tracks[0].stopped >= 1);
    assert.equal(cam.playing, false);
    assert.equal(h.deliverFrame(fm(1)), false);
  } finally {
    cam.close();
    h.dispose();
  }
});

await test('a newer open supersedes an older one without being closed by it', async () => {
  const h = createHarness(PORTRAIT);
  const md = g.navigator.mediaDevices;
  const grants: ((s: unknown) => void)[] = [];
  md.getUserMedia = () => new Promise(resolve => { grants.push(resolve); });
  const cam = new CameraSource();
  try {
    const first = cam.open(h.video);
    while (grants.length < 1) await Promise.resolve();
    const second = cam.open(h.video);   // closes the first
    while (grants.length < 2) await Promise.resolve();
    const t1 = fakeTrack(), t2 = fakeTrack();
    grants[0](fakeStream(t1));
    grants[1](fakeStream(t2));
    await first; await second;
    assert.equal(t1.stopped, 1, 'the superseded open released its stream');
    assert.equal(t2.stopped, 0, 'the live one is untouched');
    assert.equal(cam.playing, true);
    assert.equal(h.deliverFrame(fm(1)), true);
  } finally {
    cam.close();
    h.dispose();
  }
});

await test('a play() that rejects after a newer open replaced the stream leaves the newer open alone', async () => {
  const h = createHarness(PORTRAIT);
  scriptMedia({});
  let rejectFirst!: (e: Error) => void;
  let plays = 0;
  h.video.play = () => ++plays === 1 ? new Promise<void>((_ok, reject) => { rejectFirst = reject; }) : Promise.resolve();
  const cam = new CameraSource();
  try {
    const first = cam.open(h.video);
    while (plays < 1) await Promise.resolve();
    await cam.open(h.video);   // closes the first, and plays
    rejectFirst(new Error('AbortError: the play() request was interrupted by a new load request'));
    await first;               // quietly: it was superseded
    assert.equal(cam.playing, true, 'the newer session survived');   // mutant: the superseded catch closes the source
    assert.equal(h.deliverFrame(fm(1)), true);
  } finally {
    cam.close();
    h.dispose();
  }
});

await test('a play() that fails is an error with the old text, and releases the camera', async () => {
  const h = createHarness(PORTRAIT);
  const script = scriptMedia({});
  h.video.play = async () => { throw new Error('NotAllowedError'); };
  const cam = new CameraSource();
  try {
    await assert.rejects(cam.open(h.video), /The camera opened but its preview could not play\. Try opening the camera again\./);
    assert.equal(script.tracks[0].stopped, 1);
    assert.equal(cam.playing, false);
  } finally {
    cam.close();
    h.dispose();
  }
});

await test('far focus is applied when a fake track offers manual, and not otherwise', async () => {
  const applied: any[] = [];
  const offering = (modes: string[], distance: unknown) => fakeTrack({
    getCapabilities: () => ({ focusMode: modes, focusDistance: distance }),
    applyConstraints: async (c: unknown) => { applied.push(c); },
    getSettings: () => ({ deviceId: 'rear', focusMode: 'manual', focusDistance: 4.2 }),
  });
  for (const [label, track, expectApplied] of [
    ['manual offered', offering(['continuous', 'manual'], { min: 0.1, max: 4.2, step: 0.1 }), true],
    ['manual not offered', offering(['continuous', 'single-shot'], { min: 0.1, max: 4.2 }), false],
    ['manual but no distance range', offering(['manual'], undefined), false],
    ['no capabilities at all', fakeTrack(), false],
  ] as const) {
    const h = createHarness(PORTRAIT);
    scriptMedia({ track: () => track });
    applied.length = 0;
    const cam = new CameraSource();
    try {
      await cam.open(h.video);
      if (expectApplied) {
        assert.deepEqual(applied, [{ advanced: [{ focusMode: 'manual', focusDistance: 4.2 }] }], `${label}: the maximum distance`);
      } else {
        assert.deepEqual(applied, [], `${label}: nothing applied`);
      }
    } finally {
      cam.close();
      h.dispose();
    }
  }
  // a camera that refuses keeps its own focus and the open goes on
  const h = createHarness(PORTRAIT);
  scriptMedia({ track: () => fakeTrack({
    getCapabilities: () => ({ focusMode: ['manual'], focusDistance: { max: 5 } }),
    applyConstraints: async () => { throw new Error('OverconstrainedError'); },
  }) });
  const cam = new CameraSource();
  try {
    await cam.open(h.video);
    assert.equal(cam.playing, true);
  } finally {
    cam.close();
    h.dispose();
  }
});

await test('the settings report carries width, height, frame rate, resize mode, zoom, focus and facing mode', async () => {
  const h = createHarness(PORTRAIT);
  scriptMedia({ track: () => fakeTrack({
    label: 'camera2 0, facing back',
    getSettings: () => ({ deviceId: 'rear', width: 720, height: 1280, frameRate: 29.97, resizeMode: 'none', zoom: 1,
      focusMode: 'manual', focusDistance: 4.2, facingMode: 'environment', exposureTime: 100 }),
  }) });
  const cam = new CameraSource();
  try {
    assert.deepEqual(cam.settings, {
      width: null, height: null, frameRate: null, resizeMode: null, zoom: null, focusMode: null, focusDistance: null,
      facingMode: null, label: null,
    }, 'null before the camera opens');
    await cam.open(h.video);
    assert.deepEqual(cam.settings, {
      width: 720, height: 1280, frameRate: 29.97, resizeMode: 'none', zoom: 1, focusMode: 'manual', focusDistance: 4.2,
      facingMode: 'environment', label: 'camera2 0, facing back',
    });
    assert.ok(!('deviceId' in cam.settings) && !('exposureTime' in cam.settings), 'only the declared fields');
  } finally {
    cam.close();
    h.dispose();
  }
  // a browser that reports less gets nulls, never zeros
  await withCamera(PORTRAIT, async (_h, cam2) => {
    assert.equal(cam2.settings.width, null);
    assert.equal(cam2.settings.zoom, null);
    assert.equal(cam2.settings.label, 'Back main wide camera');
  });
});

await test('close releases the camera but keeps what the report reads; the next open starts over', async () => {
  const h = createHarness(PORTRAIT);
  const script = scriptMedia({});
  const cam = new CameraSource();
  try {
    h.setClock(1000);
    await cam.open(h.video);
    const metas = collect(cam);
    for (let i = 1; i <= 10; i++) deliver(h, i);
    assert.equal(cam.playing, true);
    assert.equal(cam.lastFrameAt, 1000);
    cam.close();
    assert.equal(script.tracks[0].stopped, 1);
    assert.equal(cam.playing, false);
    assert.equal(h.video.srcObject, null);
    assert.equal(h.deliverFrame(fm(11)), false, 'the frame chain is cancelled');
    assert.equal(cam.liveSource(), null);
    assert.ok(cam.bench, 'the benchmark outlives the camera');
    assert.equal(cam.lag.nowMinusCapture.n, 10);
    assert.equal(cam.choices.length, 1);
    assert.equal(cam.activeId, 'rear');
    assert.equal(cam.analysisW, 180);
    assert.equal(cam.lastFrameAt, 1000);
    cam.close();   // idempotent
    assert.equal(script.tracks[0].stopped, 1, 'and does not stop twice');
    await cam.open(h.video);
    assert.equal(cam.bench, null, 'a new session starts over');
    assert.equal(cam.lag.nowMinusCapture.n, 0);
    assert.equal(cam.lastFrameAt, null);
    deliver(h, 1);
    assert.equal(metas.at(-1)!.frameNo, 1, 'frame numbers start again at 1 and the registered callback survived');
  } finally {
    cam.close();
    h.dispose();
  }
});

await test('open refuses with the old reason when the page is not a secure context', async () => {
  const h = createHarness(PORTRAIT);
  Object.defineProperty(g.window, 'isSecureContext', { value: false, configurable: true });
  const cam = new CameraSource();
  try {
    await assert.rejects(cam.open(h.video), /needs a secure connection/);
  } finally {
    cam.close();
    h.dispose();
  }
});

// ---- the 1 Hz exposure probe ----------------------------------------------------------------------------------------------

await test('exposure probe: report only, at 1 Hz, outside the frame path', async () => {
  const realSet = g.setInterval, realClear = g.clearInterval;
  const timers = new Map<number, { fn: () => void; ms: number }>();
  let id = 0;
  g.setInterval = (fn: () => void, ms: number) => { timers.set(++id, { fn, ms }); return id; };
  g.clearInterval = (i: number) => { timers.delete(i); };
  const settings: Record<string, unknown> = { deviceId: 'rear' };
  const h = createHarness(PORTRAIT);
  scriptMedia({ track: () => fakeTrack({ getSettings: () => ({ ...settings }) }) });
  const cam = new CameraSource();
  try {
    assert.equal(cam.exposureReadable, null, 'unknown before the first probe');
    await cam.open(h.video);
    assert.equal(timers.size, 1);
    const timer = [...timers.values()][0];
    assert.equal(timer.ms, 1000);
    assert.equal(cam.exposureReadable, false, 'the first probe ran at open and the track reports no exposure time');
    const tick = () => timer.fn();
    settings.exposureTime = 50; tick();
    assert.equal(cam.exposureReadable, null, 'one value is not a change');   // mutant: one value counts
    tick();
    assert.equal(cam.exposureReadable, null, 'the same value again is not a change');
    settings.exposureTime = 80; tick();
    assert.equal(cam.exposureReadable, true, 'two values: it is live');
    settings.exposureTime = 50; tick();
    assert.equal(cam.exposureReadable, true);
    // frames do not touch it
    const before = cam.exposureReadable;
    for (let i = 1; i <= 3; i++) deliver(h, i);
    assert.equal(cam.exposureReadable, before);
    cam.close();
    assert.equal(timers.size, 0, 'close clears the timer');
    tick();   // a stale tick from the cleared timer changes nothing
    assert.equal(cam.exposureReadable, true);
  } finally {
    g.setInterval = realSet; g.clearInterval = realClear;
    cam.close();
    h.dispose();
  }
});

await test('exposure probe: asks ImageCapture to refresh the setting before it reads it', async () => {
  const realSet = g.setInterval, realClear = g.clearInterval;
  const timers = new Map<number, () => void>();
  let id = 0;
  g.setInterval = (fn: () => void) => { timers.set(++id, fn); return id; };
  g.clearInterval = (i: number) => { timers.delete(i); };
  const settings: Record<string, unknown> = { deviceId: 'rear' };
  let constructed = 0, refreshed = 0;
  g.ImageCapture = class {
    constructor(public track: unknown) { constructed++; }
    getPhotoCapabilities() { refreshed++; settings.exposureTime = 60 + 10 * refreshed; return Promise.resolve({}); }
  };
  const h = createHarness(PORTRAIT);
  scriptMedia({ track: () => fakeTrack({ getSettings: () => ({ ...settings }) }) });
  const cam = new CameraSource();
  try {
    await cam.open(h.video);
    await Promise.resolve(); await Promise.resolve();
    assert.equal(refreshed, 1);
    assert.equal(cam.exposureReadable, null, 'the refreshed value was read: one value');
    [...timers.values()][0]();
    await Promise.resolve(); await Promise.resolve();
    assert.equal(refreshed, 2);
    assert.equal(constructed, 1, 'one ImageCapture for the session');
    assert.equal(cam.exposureReadable, true, 'the second refresh read a different value');
  } finally {
    delete g.ImageCapture;
    g.setInterval = realSet; g.clearInterval = realClear;
    cam.close();
    h.dispose();
  }
});

// ---- farbling -------------------------------------------------------------------------------------------------------------

/** A canvas whose context stores what is put into it and hands back `alter(data)`. */
function fakeCanvas(alter: ((put: Uint8ClampedArray) => Uint8ClampedArray) | 'zeros' | 'throws') {
  let stored = new Uint8ClampedArray(0);
  const ctx = {
    createImageData: (w: number, hh: number) => ({ width: w, height: hh, data: new Uint8ClampedArray(w * hh * 4) }),
    putImageData: (img: { data: Uint8ClampedArray }) => { stored = img.data.slice(); },
    getImageData: (_x: number, _y: number, w: number, hh: number) => {
      if (alter === 'throws') throw new Error('SecurityError');
      return { data: alter === 'zeros' ? new Uint8ClampedArray(w * hh * 4) : alter(stored) };
    },
  };
  return { width: 300, height: 150, getContext: () => ctx } as unknown as HTMLCanvasElement;
}
/** Change `count` colour channels (never alpha) by `delta`, from the front. */
const shift = (count: number, delta: number) => (put: Uint8ClampedArray) => {
  const out = put.slice();
  let done = 0;
  for (let i = 0; i < out.length && done < count; i++) {
    if ((i & 3) === 3) continue;
    out[i] = out[i] >= 128 ? out[i] - delta : out[i] + delta;
    done++;
  }
  return out;
};

await test('farblingProbe: null on an all-zero readback, false on an honest canvas, true on a perturbing one', async () => {
  assert.equal(farblingProbe(fakeCanvas('zeros')), null, 'no real canvas');
  assert.equal(farblingProbe(fakeCanvas('throws')), null, 'a refused readback says nothing');
  assert.equal(farblingProbe(fakeCanvas(put => put.slice())), false, 'what was put in comes back');
  assert.equal(farblingProbe(fakeCanvas(shift(12288, 40))), true, 'every channel moved');
  // the boundaries: more than 2 per channel, more than 0.1 % of 12,288 channels (12.288)
  assert.equal(farblingProbe(fakeCanvas(shift(12288, 2))), false, 'a difference of 2 is not more than 2');   // mutant: >= 2
  assert.equal(farblingProbe(fakeCanvas(shift(12288, 3))), true, 'a difference of 3 is');
  assert.equal(farblingProbe(fakeCanvas(shift(12, 40))), false, '12 channels is 0.098 %');
  assert.equal(farblingProbe(fakeCanvas(shift(13, 40))), true, '13 channels is 0.106 %');
  // alpha is not a colour channel: a canvas that only changes alpha is not farbled by this test
  assert.equal(farblingProbe(fakeCanvas(put => { const o = put.slice(); for (let i = 3; i < o.length; i += 4) o[i] = 200; return o; })), false);
  // the pattern is seeded: two probes put the same bytes
  const seen: Uint8ClampedArray[] = [];
  farblingProbe(fakeCanvas(put => { seen.push(put.slice()); return put.slice(); }));
  farblingProbe(fakeCanvas(put => { seen.push(put.slice()); return put.slice(); }));
  assert.ok(sameBytes(seen[0], seen[1]));
  assert.equal(seen[0].length, 64 * 64 * 4);
  assert.ok(seen[0].some(v => v !== seen[0][0]), 'a pattern, not a fill');
  // the canvas is resized to 64 x 64
  const c = fakeCanvas(put => put.slice()); farblingProbe(c);
  assert.deepEqual([c.width, c.height], [64, 64]);
});

await test('the source reports farbled as null under the replay harness, where no canvas holds pixels', async () => {
  await withCamera(PORTRAIT, async (_h, cam, ins) => {
    assert.equal(cam.farbled, null);
    assert.equal(ins.of('probe').draws.length, 0, 'the probe used its own scratch canvas');
    assert.deepEqual([ins.of('probe').w, ins.of('probe').h], [64, 64]);
  });
});

// ---- support -----------------------------------------------------------------------------------------------------------------

await test('checkPanoSupport: secure context and getUserMedia, with the old reason', () => {
  const h = createHarness(PORTRAIT);
  try {
    assert.deepEqual(checkPanoSupport(), { supported: true, reason: null });
    const reason = 'Photosphere capture needs a secure connection - set one up in Connection.';
    Object.defineProperty(g.window, 'isSecureContext', { value: false, configurable: true });
    assert.deepEqual(checkPanoSupport(), { supported: false, reason });
    Object.defineProperty(g.window, 'isSecureContext', { value: true, configurable: true });
    const md = g.navigator.mediaDevices;
    Object.defineProperty(g.navigator, 'mediaDevices', { value: undefined, configurable: true });
    assert.deepEqual(checkPanoSupport(), { supported: false, reason });
    Object.defineProperty(g.navigator, 'mediaDevices', { value: {}, configurable: true });
    assert.deepEqual(checkPanoSupport(), { supported: false, reason }, 'no getUserMedia');
    Object.defineProperty(g.navigator, 'mediaDevices', { value: md, configurable: true });
    assert.equal(checkPanoSupport().supported, true);
  } finally {
    h.dispose();
  }
});

await test('queryMotionPermission maps the gyroscope permission and is never an error', async () => {
  const h = createHarness(PORTRAIT);
  try {
    assert.equal(await queryMotionPermission(), 'unknown', 'no Permissions API');
    const asked: unknown[] = [];
    const answer = (impl: (d: unknown) => Promise<unknown>) => Object.defineProperty(g.navigator, 'permissions', {
      value: { query: (d: unknown) => { asked.push(d); return impl(d); } }, configurable: true,
    });
    for (const state of ['granted', 'denied', 'prompt'] as const) {
      answer(async () => ({ state }));
      assert.equal(await queryMotionPermission(), state);
    }
    assert.deepEqual(asked[0], { name: 'gyroscope' });
    answer(async () => ({ state: 'something-new' }));
    assert.equal(await queryMotionPermission(), 'unknown');
    answer(async () => { throw new TypeError('not a valid permission name'); });
    assert.equal(await queryMotionPermission(), 'unknown', 'a rejection');
    answer(() => { throw new TypeError('sync'); });
    assert.equal(await queryMotionPermission(), 'unknown', 'a synchronous throw');
    Object.defineProperty(g.navigator, 'permissions', { value: {}, configurable: true });
    assert.equal(await queryMotionPermission(), 'unknown', 'no query');
  } finally {
    h.dispose();
  }
});

await test('iOS motion permission: both requests are made before the function returns', async () => {
  const h = createHarness(PORTRAIT);
  const w = g.window;
  const order: string[] = [];
  const install = (name: 'DeviceOrientationEvent' | 'DeviceMotionEvent', impl: (() => Promise<string>) | null) => {
    w[name] = class { };
    if (impl) w[name].requestPermission = impl;
  };
  try {
    install('DeviceOrientationEvent', null); install('DeviceMotionEvent', null);
    assert.equal(await requestIosMotionPermission(), 'not-needed');

    install('DeviceOrientationEvent', () => { order.push('orientation'); return Promise.resolve('granted'); });
    install('DeviceMotionEvent', () => { order.push('motion'); return Promise.resolve('granted'); });
    const pending = requestIosMotionPermission();
    // No await has happened: Safari's allowance for the tap is still unspent, and both prompts are already asked.
    assert.deepEqual(order, ['orientation', 'motion']);   // mutant: the second request waits for the first
    assert.equal(await pending, 'granted');

    install('DeviceMotionEvent', () => Promise.resolve('denied'));
    assert.equal(await requestIosMotionPermission(), 'denied', 'one refusal is a refusal');

    install('DeviceMotionEvent', () => Promise.reject(new Error('NotAllowedError')));
    assert.equal(await requestIosMotionPermission(), 'denied', 'a rejection');

    install('DeviceMotionEvent', () => { throw new Error('sync'); });
    assert.equal(await requestIosMotionPermission(), 'denied', 'a throw');

    install('DeviceMotionEvent', null);
    assert.equal(await requestIosMotionPermission(), 'granted', 'only the orientation request exists');

    // called as a method: a detached static would lose its constructor
    let thisArg: unknown = null;
    install('DeviceOrientationEvent', null);
    w.DeviceOrientationEvent.requestPermission = function (this: unknown) { thisArg = this; return Promise.resolve('granted'); };
    install('DeviceMotionEvent', null);
    await requestIosMotionPermission();
    assert.equal(thisArg, w.DeviceOrientationEvent);
  } finally {
    h.dispose();
  }
});

// ---- copied helpers ----------------------------------------------------------------------------------------------------------

const dev = (deviceId: string, label: string) => ({ deviceId, label, kind: 'videoinput', groupId: '' }) as MediaDeviceInfo;

await test('preferredRearCamera is the old rule, returning the device', () => {
  const ultra = dev('u', 'Back ultra wide camera'), main = dev('m', 'Back main wide camera'), other = dev('o', 'Back camera 2');
  assert.equal(preferredRearCamera([ultra, main, other]), main);
  assert.equal(preferredRearCamera([other, main]), main);
  assert.equal(preferredRearCamera([dev('x', 'camera2 0, facing back'), dev('y', 'Rear Standard camera')])?.deviceId, 'y');
  assert.equal(preferredRearCamera([dev('x', 'Back 1x')])?.deviceId, 'x');
  assert.equal(preferredRearCamera([dev('x', 'Back camera')])?.deviceId, 'x', 'a single rear camera without a hint');
  assert.equal(preferredRearCamera([dev('x', 'Back camera'), dev('y', 'Back camera 2')]), undefined, 'two and no hint: no guess');
  assert.equal(preferredRearCamera([dev('x', 'Front camera'), dev('y', 'Back telephoto camera')]), undefined);
  assert.equal(preferredRearCamera([dev('x', 'Back camera 0.5'), dev('y', 'Back ultra wide')]), undefined);
  assert.equal(preferredRearCamera([dev('x', 'Back camera 0,6')]), undefined);
  assert.equal(preferredRearCamera([dev('x', ''), dev('y', '')]), undefined, 'no labels before the grant');
  assert.equal(preferredRearCamera([]), undefined);
});

await test('cameraErrorText keeps today\'s texts for the two failures with a remedy', () => {
  const h = createHarness(PORTRAIT);
  try {
    const apostrophe = String.fromCharCode(0x2019);
    const blocked = `Camera access was blocked. Allow it in your browser${apostrophe}s site settings, then try again.`;
    const busy = 'The camera is busy. Close other camera apps, then try again.';
    const generic = 'Could not open the camera. Try again or draw the horizon by hand.';
    const DOMEx = g.window.DOMException;
    assert.equal(cameraErrorText(new DOMEx('Permission denied', 'NotAllowedError')), blocked);
    assert.equal(cameraErrorText(new DOMEx('Device in use', 'NotReadableError')), busy);
    assert.equal(cameraErrorText(Object.assign(new Error('x'), { name: 'NotAllowedError' })), blocked);
    assert.equal(cameraErrorText({ name: 'NotReadableError' }), busy, 'duck-typed: another realm\'s exception');
    assert.equal(cameraErrorText(new Error('The camera opened but its preview could not play.')), 'The camera opened but its preview could not play.');
    assert.equal(cameraErrorText(new DOMEx('No camera', 'NotFoundError')), 'No camera', 'any other error says what it says');
    assert.equal(cameraErrorText(new Error('')), generic);
    assert.equal(cameraErrorText('boom'), generic);
    assert.equal(cameraErrorText(null), generic);
    assert.equal(cameraErrorText(undefined), generic);
    assert.equal(cameraErrorText({ name: 'NotAllowedError' }).includes(apostrophe), true);
  } finally {
    h.dispose();
  }
});

await test('readbackSummary and percentile are the old helpers, copied', () => {
  assert.equal(readbackSummary([]), null);
  assert.deepEqual(readbackSummary([5, 1, 3, 2, 4, 10, 9, 8, 7, 6]), { count: 10, p50: 6, p95: 10, max: 10 });
  assert.deepEqual(readbackSummary([2]), { count: 1, p50: 2, p95: 2, max: 2 });
  assert.equal(percentile([], 0.5), 0);
  assert.equal(percentile([4, 1, 3, 2], 0.5), 2);
  assert.equal(percentile([4, 1, 3, 2], 0), 1);
  assert.equal(percentile([4, 1, 3, 2], 1), 4);
  const input = [3, 1, 2];
  percentile(input, 0.5); readbackSummary(input);
  assert.deepEqual(input, [3, 1, 2], 'the input is not sorted in place');
});

// ---- the files ------------------------------------------------------------------------------------------------------------------

await test('the constants are the block\'s, and the two sources are plain ASCII with no BOM', () => {
  assert.deepEqual([ANALYSIS_LONG_PX, LIVE_SOURCE_W, LIVE_SOURCE_H, TINY_W, TINY_H], [320, 90, 160, 45, 80]);
  for (const file of ['../cameraSource.ts', '../support.ts']) {
    const bytes = readFileSync(new URL(file, import.meta.url));
    assert.notEqual(bytes[0], 0xef, `${file}: no BOM`);
    for (let i = 0; i < bytes.length; i++) assert.ok(bytes[i] < 128 && (bytes[i] >= 32 || bytes[i] === 9 || bytes[i] === 10 || bytes[i] === 13), `${file}: byte ${i} is plain ASCII`);
    const src = bytes.toString('utf8');
    assert.ok(/^\/\/ Copyright \(c\) 2026 James Penick\r?\n\/\/ SPDX-License-Identifier: Apache-2\.0\r?\n/.test(src), `${file}: header`);
  }
  // production code imports nothing from the harness or from tests, and nothing from the old scanner it copies
  for (const file of ['../cameraSource.ts', '../support.ts']) {
    const src = readFileSync(new URL(file, import.meta.url), 'utf8');
    assert.ok(!/from '[^']*(__sim__|__tests__|\/photosphere')/.test(src), `${file}: imports`);
    assert.ok(!/from '[^']*horizon'/.test(src), `${file}: imports`);
  }
});

console.log(`cameraSource.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
