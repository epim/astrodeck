// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The panorama scanner's camera: opening it, delivering frames, reading them back, and the facts about the camera the
// scan report carries (SPEC-v2 2.3, 2.11, 3.5, 4.2).
//
// COPIED, not moved (D24, RI M28). The open sequence is `PhotosphereSweep.start()` (photosphere.ts:2085-2160) with its
// generation guard; `preferredRearCamera`, `percentile` and `readbackSummary` are the originals at photosphere.ts:1229,
// 251 and 97; the error texts are horizon.tsx:352-364. None of those files is edited, and nothing here imports them, so
// deleting the old scanner (T32) leaves this module standing. What is new is everything about the frame path: a fixed
// analysis canvas, the A/B readback benchmark, VideoFrame pairing, the lag distributions, the rAF fallback, the tiny
// watchdog readback, the live source, and the capability probes.
//
// Two rules hold for every canvas call below. Only the 5-argument `drawImage` is used, because that is the form the
// replay harness models. And a canvas is sized once, when it is made, because resizing resets its state and, on a GPU
// canvas, its backing store.
import { pixelLuminance } from '../photosphereGeometry';
import { checkPanoSupport, farblingProbe } from './support';
import type { AnalysisFrame, CameraBench, CameraSettingsReport, CameraSourceLike, FrameMeta, Stats } from './types';

export const ANALYSIS_LONG_PX = 320, LIVE_SOURCE_W = 90, LIVE_SOURCE_H = 160, TINY_W = 45, TINY_H = 80;

/** Frames the readback benchmark spends, alternating pipeline A and pipeline B (4.2). */
const BENCH_FRAMES = 10;
/** Samples kept per lag series: the newest, about two minutes at 30 fps. A scan is shorter, so the report sees all. */
const LAG_SAMPLES = 4096;
/** Frame intervals kept for the median that decides whether a VideoFrame is the frame the callback announced. */
const INTERVAL_SAMPLES = 31;
const DEFAULT_INTERVAL_US = 1e6 / 30;
const EXPOSURE_PROBE_MS = 1000;
/** 9:16 until a camera says otherwise. */
const DEFAULT_ANALYSIS_W = 180;

const EMPTY_SETTINGS: CameraSettingsReport = {
  width: null, height: null, frameRate: null, resizeMode: null, zoom: null, focusMode: null, focusDistance: null,
  facingMode: null, label: null,
};

/** Labels are vendor-dependent. Never infer lens type from device order. Copied from photosphere.ts:1229; it takes the
 *  browser's own device records and returns the record, where the original took `{ deviceId, label }` and returned the
 *  id. */
export function preferredRearCamera(devices: readonly MediaDeviceInfo[]): MediaDeviceInfo | undefined {
  const rear = devices.filter(c => /back|rear|environment/i.test(c.label)
    && !/ultra|telephoto|front|\b0[.,][56]\b/i.test(c.label));
  return rear.find(c => /main|wide|standard|\b1x\b/i.test(c.label))
    ?? (rear.length === 1 ? rear[0] : undefined);
}

/** What the person is told when the camera will not open: today's texts for the two failures that have a remedy
 *  (horizon.tsx:360-363), the browser's own message for anything else. Duck-typed on `name` and `message` rather than
 *  `instanceof Error`, because a DOMException from another realm is not an instance of this realm's Error. */
export function cameraErrorText(e: unknown): string {
  const name = typeof (e as { name?: unknown } | null)?.name === 'string' ? (e as { name: string }).name : '';
  if (name === 'NotAllowedError') return 'Camera access was blocked. Allow it in your browser\u2019s site settings, then try again.';
  if (name === 'NotReadableError') return 'The camera is busy. Close other camera apps, then try again.';
  const message = (e as { message?: unknown } | null)?.message;
  return typeof message === 'string' && message !== '' ? message : 'Could not open the camera. Try again or draw the horizon by hand.';
}

/** A frame older than this when its callback runs, or stamped later than the callback, is not believed (the old scanner's
 *  `STALE_FRAME_MS`, photosphere.ts:2252; 3.2, S14). */
const STALE_FRAME_MS = 1000;

/** A frame's time on the `performance.now()` timeline (3.2): the capture time when the browser gives one, else the
 *  presentation time, else the moment the callback ran. A value is believed only when it is a finite number, no later
 *  than the callback and no more than `STALE_FRAME_MS` before it; otherwise it is absent, and the next source is tried. */
export function frameTime(meta: { captureTime?: number; presentationTime?: number }, callbackNow: number): number {
  const believed = (v: number | undefined): v is number =>
    typeof v === 'number' && Number.isFinite(v) && v <= callbackNow && v >= callbackNow - STALE_FRAME_MS;
  if (believed(meta.captureTime)) return meta.captureTime;
  if (believed(meta.presentationTime)) return meta.presentationTime;
  return callbackNow;
}

/** count / p50 / p95 / max of a readback timing, in ms. Null before the first sample, so "never read" cannot be
 *  mistaken for "read in 0 ms". Copied from photosphere.ts:97. */
export function readbackSummary(ms: readonly number[]):
    {count:number;p50:number;p95:number;max:number}|null {
  if (!ms.length) return null;
  const sorted = [...ms].sort((a, b) => a - b);
  const at = (q: number) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
  return { count: sorted.length, p50: at(.5), p95: at(.95), max: sorted[sorted.length - 1] };
}

/** The value at percentile `p` (0..1), by index into the sorted values. Empty input is 0. Copied from
 *  photosphere.ts:251. Used here for the median frame interval; the report's `Stats` are nearest-rank (`stats` below). */
export function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor(p * (sorted.length - 1))));
  return sorted[idx];
}

/** `Stats` (types.ts): nearest-rank p50 and p95, every field but `n` null when there is nothing. */
function stats(values: readonly number[]): Stats {
  if (values.length === 0) return { n: 0, p50: null, p95: null, max: null };
  const sorted = [...values].sort((a, b) => a - b);
  const rank = (q: number) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(q * sorted.length) - 1))];
  return { n: sorted.length, p50: rank(.5), p95: rank(.95), max: sorted[sorted.length - 1] };
}

const num = (v: unknown): number | null => typeof v === 'number' && Number.isFinite(v) ? v : null;
const str = (v: unknown): string | null => typeof v === 'string' && v !== '' ? v : null;

function pushRing(values: number[], v: number, cap = LAG_SAMPLES): void {
  values.push(v);
  if (values.length > cap) values.shift();
}

const stopStream = (stream: MediaStream): void => stream.getTracks().forEach(t => t.stop());

/** Today's rule: a named camera is asked for exactly, anything else by facing mode. The shape of the request is 2.3:
 *  1280 x 720 at 30 fps as ideals, and `resizeMode: none` so Chrome does not crop and scale a native mode to reach them,
 *  which would change the field of view without telling anyone (RD finding 19). */
function videoConstraints(id?: string): MediaTrackConstraints {
  // `resizeMode` is in the specification and in Chromium but not yet in lib.dom's MediaTrackConstraints
  const constraints: MediaTrackConstraints & { resizeMode: { ideal: string } } = {
    ...(id ? { deviceId: { exact: id } } : { facingMode: { ideal: 'environment' } }),
    width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 },
    resizeMode: { ideal: 'none' },
  };
  return constraints;
}

/** The analysis canvas for a video of this size (4.2): 320 on the long edge, portrait, the short edge in proportion.
 *  9:16 gives 180 x 320 and 3:4 gives 240 x 320. A landscape video gets the same portrait canvas, because the scanner
 *  reads frames only in portrait and the canvas must not change size when the phone is turned. */
function analysisSize(width: number, height: number): { w: number; h: number } {
  const short = Math.min(width, height), long = Math.max(width, height);
  if (!(short > 0 && long > 0)) return { w: DEFAULT_ANALYSIS_W, h: ANALYSIS_LONG_PX };
  return { w: Math.max(1, Math.round(ANALYSIS_LONG_PX * short / long)), h: ANALYSIS_LONG_PX };
}

/** A canvas sized once. The 2d context is asked for after the size is set. Every context asks for `'medium'` smoothing,
 *  since Chrome's default `drawImage` filter aliases a 4x downscale (RD finding 4); `read` marks a canvas that is read
 *  back, which wants a software canvas. */
function makeCanvas(w: number, h: number, read: boolean): { canvas: HTMLCanvasElement; ctx: CanvasRenderingContext2D | null } {
  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext('2d', read ? { willReadFrequently: true } : undefined);
  if (ctx) { ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'medium'; }
  return { canvas, ctx };
}

type Pipeline = 'A' | 'B';

export class CameraSource implements CameraSourceLike {
  private readonly benchFrames: number;
  /** Bumped by every `close()`. An `open()` that finds it changed has been superseded, and releases what it holds. */
  private generation = 0;
  private video: HTMLVideoElement | null = null;
  private stream: MediaStream | null = null;
  private track: MediaStreamTrack | null = null;
  private callback: ((meta: FrameMeta) => void) | null = null;
  private handle: number | null = null;
  private viaRvfc = false;
  private frameCount = 0;
  private lastFrameTime: number | null = null;
  // rAF fallback: the clocks the last poll saw
  private polledTime: number | null = null;
  private polledFrames: number | null = null;

  private w = DEFAULT_ANALYSIS_W;
  private h = ANALYSIS_LONG_PX;
  private analysis: HTMLCanvasElement | null = null;
  private analysisCtx: CanvasRenderingContext2D | null = null;
  private gpu: HTMLCanvasElement | null = null;
  private gpuCtx: CanvasRenderingContext2D | null = null;
  private live: HTMLCanvasElement | null = null;
  private liveCtx: CanvasRenderingContext2D | null = null;
  private tinyCtx: CanvasRenderingContext2D | null = null;

  private settingsReport: CameraSettingsReport = EMPTY_SETTINGS;
  private benchResult: CameraBench | null = null;
  private farbledResult: boolean | null = null;
  private exposureResult: boolean | null = null;
  private deviceChoices: { deviceId: string; label: string }[] = [];
  private activeDevice: string | undefined;

  private benchA: number[] = [];
  private benchB: number[] = [];
  private benchDone = 0;
  private chosen: Pipeline = 'A';
  /** The readback taken for the frame in flight, so a bench frame the scanner also wants is not read twice. */
  private held: { frameNo: number; frame: AnalysisFrame } | null = null;

  private slipPairs = 0;
  private slipCount = 0;
  private intervalsUs: number[] = [];
  private lastMediaUs: number | null = null;
  private lagNow: number[] = [];
  private lagPresent: number[] = [];
  private lagExpected: number[] = [];

  private exposureTimer: ReturnType<typeof setInterval> | null = null;
  private exposureSeen: number[] = [];
  private imageCapture: { getPhotoCapabilities(): Promise<unknown> } | null = null;

  constructor(o?: { benchFrames?: number /* 10 */ }) {
    this.benchFrames = Math.max(0, Math.floor(o?.benchFrames ?? BENCH_FRAMES));
  }

  get settings(): CameraSettingsReport { return this.settingsReport; }
  get bench(): CameraBench | null { return this.benchResult; }
  get farbled(): boolean | null { return this.farbledResult; }
  /** True once the track's `exposureTime` has been seen to take two different values, false when it never appeared in a
   *  probe, null before the first probe and while only one value has been seen. Report only (RD finding 18). */
  get exposureReadable(): boolean | null { return this.exposureResult; }
  get slips(): { pairs: number; slips: number } { return { pairs: this.slipPairs, slips: this.slipCount }; }
  get lag(): { nowMinusCapture: Stats; presentMinusCapture: Stats; expectedMinusNow: Stats } {
    return { nowMinusCapture: stats(this.lagNow), presentMinusCapture: stats(this.lagPresent), expectedMinusNow: stats(this.lagExpected) };
  }
  get choices(): readonly { deviceId: string; label: string }[] { return this.deviceChoices; }
  get activeId(): string | undefined { return this.activeDevice; }
  get analysisW(): number { return this.w; }
  get analysisH(): number { return this.h; }
  /** The element is showing frames and the track behind it is live. */
  get playing(): boolean {
    const v = this.video, t = this.track;
    return !!v && !v.paused && !v.ended && v.readyState >= 2 && (!t || (t.readyState === 'live' && !t.muted));
  }
  /** When the newest frame was delivered, on the `performance.now()` timeline. Null until the first. */
  get lastFrameAt(): number | null { return this.lastFrameTime; }

  /** Open the camera and start delivering frames. The diagnostics of an earlier session are cleared; the ones of this
   *  one survive `close()`, because the report is built after the camera is released.
   *
   *  A superseded call (a `close()` or another `open()` while this one awaits) releases whatever it holds and returns
   *  without error, whatever fails later; it never touches the session that replaced it. The motion permission is NOT
   *  asked here: iOS wants it from the tap before any await, so the caller asks `requestIosMotionPermission()` first. */
  async open(video: HTMLVideoElement, deviceId?: string): Promise<void> {
    this.close();
    const generation = this.generation;
    const stale = () => generation !== this.generation;
    this.resetSession();
    const support = checkPanoSupport();
    if (!support.supported) throw new Error(support.reason ?? 'The camera is not available here.');
    this.video = video;
    try {
      const md = navigator.mediaDevices;
      const request = (id?: string) => md.getUserMedia({ audio: false, video: videoConstraints(id) });
      const cameras = async (): Promise<MediaDeviceInfo[]> => {
        try { return (await md.enumerateDevices()).filter(d => d.kind === 'videoinput'); }
        catch { return []; }
      };
      // Labels are empty until the first grant, so the list is read twice: once to pick, once to check the pick.
      const before = await cameras();
      if (stale()) return;
      let stream = await request(deviceId ?? preferredRearCamera(before)?.deviceId);
      // Closing the sheet while the browser's permission prompt is open must also release a camera granted after it
      // has gone.
      if (stale()) { stopStream(stream); return; }
      this.stream = stream;
      const after = await cameras();
      if (stale()) return;
      this.deviceChoices = after.map((d, i) => ({ deviceId: d.deviceId, label: d.label || `Camera ${i + 1}` }));
      const preferred = deviceId ?? preferredRearCamera(after)?.deviceId;
      const current = stream.getVideoTracks?.()[0]?.getSettings?.().deviceId;
      if (preferred && current && preferred !== current) {
        stopStream(stream);
        stream = await request(preferred);
        if (stale()) { stopStream(stream); return; }
        this.stream = stream;
      }
      const track = stream.getVideoTracks?.()[0] ?? null;
      this.track = track;
      this.activeDevice = track?.getSettings?.().deviceId ?? preferred;
      video.srcObject = stream;
      try { await video.play(); }
      catch {
        // A play() that fails because a newer open replaced the stream is that open's business, not an error here. The
        // release is the outer catch's: closing here would bump the generation and make this failure look superseded.
        if (stale()) return;
        throw new Error('The camera opened but its preview could not play. Try opening the camera again.');
      }
      if (stale()) return;
      if (track) {
        await this.applyFarFocus(track);
        if (stale()) return;
      }
      this.settingsReport = this.readSettings(track);
      const size = analysisSize(video.videoWidth || (this.settingsReport.width ?? 0), video.videoHeight || (this.settingsReport.height ?? 0));
      this.buildCanvases(size.w, size.h);
      this.farbledResult = farblingProbe(document.createElement('canvas'));
      this.startExposureProbe(generation);
      this.startFrames(video, generation);
    } catch (e) {
      // A superseded call resolves quietly whatever fails later (S14): a late NotAllowedError from a replaced request must
      // neither close the session that replaced it nor reach the caller as the error of a camera that is working.
      if (stale()) return;
      this.close();
      throw e;
    }
  }

  /** One call per new frame. The registration survives the next frame by itself, and survives `close()` and a later
   *  `open()`; null detaches it. */
  onFrame(cb: ((meta: FrameMeta) => void) | null): void {
    this.callback = cb;
  }

  /** The current frame into the fixed analysis canvas, through the pipeline the benchmark chose (4.2). Null when
   *  nothing is open, when the video has no picture, or when the canvas refuses. */
  readback(meta: FrameMeta): AnalysisFrame | null {
    if (this.held && this.held.frameNo === meta.frameNo) return this.held.frame;
    const got = this.grab(this.chosen, meta);
    return got ? this.analysisFrame(meta, got) : null;
  }

  /** 45 x 80 luma of the current frame, for the watchdog (3.5 step 10). Rec. 601 through the panorama's own
   *  `pixelLuminance`, rounded. */
  readbackTiny(): Uint8Array | null {
    const video = this.video, ctx = this.tinyCtx;
    if (!video || !ctx || !this.hasPicture(video)) return null;
    try {
      ctx.drawImage(video, 0, 0, TINY_W, TINY_H);
      const d = ctx.getImageData(0, 0, TINY_W, TINY_H).data;
      const out = new Uint8Array(TINY_W * TINY_H);
      for (let p = 0; p < out.length; p++) out[p] = Math.round(pixelLuminance(d[p * 4], d[p * 4 + 1], d[p * 4 + 2]));
      return out;
    } catch {
      return null;
    }
  }

  /** The GPU draw of the current frame into the live source. Never read back. */
  refreshLiveSource(): void {
    const video = this.video, ctx = this.liveCtx, live = this.live;
    if (!video || !ctx || !live || !this.hasPicture(video)) return;
    try { ctx.drawImage(video, 0, 0, live.width, live.height); }
    catch { /* a lost context leaves the last picture on the canvas */ }
  }

  /** The live source: the L1 size of the analysis frame, 90 x 160 at 9:16 and 120 x 160 at 3:4. */
  liveSource(): HTMLCanvasElement | null { return this.live; }

  /** Release the camera and stop delivering. What the report reads (`settings`, `bench`, `farbled`, `exposureReadable`,
   *  `slips`, `lag`, `choices`, `activeId`, the analysis size) stays; the next `open()` replaces it. */
  close(): void {
    this.generation++;
    if (this.handle !== null) {
      if (this.viaRvfc) this.video?.cancelVideoFrameCallback?.(this.handle);
      else if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(this.handle);
      this.handle = null;
    }
    if (this.exposureTimer !== null) { clearInterval(this.exposureTimer); this.exposureTimer = null; }
    this.stream?.getTracks().forEach(t => t.stop());
    this.stream = null; this.track = null;
    if (this.video) this.video.srcObject = null;
    this.video = null;
    this.analysis = this.analysisCtx = this.gpu = this.gpuCtx = this.live = this.liveCtx = this.tinyCtx = null;
    this.held = null; this.imageCapture = null;
  }

  // ---- open -----------------------------------------------------------------------------------------------------

  private resetSession(): void {
    this.frameCount = 0; this.lastFrameTime = null; this.polledTime = null; this.polledFrames = null;
    this.settingsReport = EMPTY_SETTINGS; this.benchResult = null; this.farbledResult = null; this.exposureResult = null;
    this.deviceChoices = []; this.activeDevice = undefined;
    this.benchA = []; this.benchB = []; this.benchDone = 0; this.chosen = 'A'; this.held = null;
    this.slipPairs = 0; this.slipCount = 0; this.intervalsUs = []; this.lastMediaUs = null;
    this.lagNow = []; this.lagPresent = []; this.lagExpected = [];
    this.exposureSeen = [];
  }

  /** With a camera that has a manual focus mode, focus far (RD finding 17): a hunting autofocus blurs frames and moves
   *  the field of view a little. Needs both the mode and a distance range, since manual focus with no distance would
   *  lock at whatever the lens happened to be at. A camera that refuses keeps its own focus. */
  private async applyFarFocus(track: MediaStreamTrack): Promise<void> {
    let caps: (MediaTrackCapabilities & { focusMode?: string[]; focusDistance?: { max?: number } }) | undefined;
    try { caps = track.getCapabilities?.() as typeof caps; } catch { return; }
    const far = caps?.focusDistance?.max;
    if (!Array.isArray(caps?.focusMode) || !caps.focusMode.includes('manual') || typeof far !== 'number' || !Number.isFinite(far)) return;
    try { await track.applyConstraints({ advanced: [{ focusMode: 'manual', focusDistance: far } as MediaTrackConstraintSet] }); }
    catch { /* keeps its own focus */ }
  }

  private readSettings(track: MediaStreamTrack | null): CameraSettingsReport {
    if (!track) return EMPTY_SETTINGS;
    const s = (track.getSettings?.() ?? {}) as MediaTrackSettings & { resizeMode?: string; zoom?: number; focusMode?: string; focusDistance?: number };
    return {
      width: num(s.width), height: num(s.height), frameRate: num(s.frameRate), resizeMode: str(s.resizeMode), zoom: num(s.zoom),
      focusMode: str(s.focusMode), focusDistance: num(s.focusDistance), facingMode: str(s.facingMode),
      label: str(track.label) ?? str(this.deviceChoices.find(c => c.deviceId === this.activeDevice)?.label),
    };
  }

  private buildCanvases(w: number, h: number): void {
    this.w = w; this.h = h;
    const a = makeCanvas(w, h, true);
    this.analysis = a.canvas; this.analysisCtx = a.ctx;
    const g = makeCanvas(w, h, false);
    this.gpu = g.canvas; this.gpuCtx = g.ctx;
    const l = makeCanvas(Math.max(1, Math.round(LIVE_SOURCE_H * w / h)), LIVE_SOURCE_H, false);
    this.live = l.canvas; this.liveCtx = l.ctx;
    this.tinyCtx = makeCanvas(TINY_W, TINY_H, true).ctx;
  }

  // ---- frames ---------------------------------------------------------------------------------------------------

  /** Frames come from `requestVideoFrameCallback`, which re-registers itself here BEFORE the callback runs so that a
   *  callback that closes the camera cancels a live registration, and one that throws cannot end the chain. Without it
   *  (the fallback, also what a browser without rVFC gets) a rAF loop polls the media clock and the playback quality
   *  counter for a new frame, and a frame's time is the moment the poll saw it (2.11). */
  private startFrames(video: HTMLVideoElement, generation: number): void {
    const stale = () => generation !== this.generation;
    if (typeof video.requestVideoFrameCallback === 'function') {
      this.viaRvfc = true;
      const frame: VideoFrameRequestCallback = (now, metadata) => {
        if (stale()) return;
        this.handle = video.requestVideoFrameCallback(frame);
        this.deliver(now, metadata, video, null);
      };
      this.handle = video.requestVideoFrameCallback(frame);
      return;
    }
    this.viaRvfc = false;
    this.pollFresh(video);   // the baseline: one reading is a number, not a delivery
    const poll = () => {
      if (stale()) return;
      this.handle = requestAnimationFrame(poll);
      const fresh = this.pollFresh(video);
      if (fresh) this.deliver(performance.now(), null, video, fresh.presented);
    };
    this.handle = requestAnimationFrame(poll);
  }

  /** Has the element shown a new frame since the last poll? The playback quality counter when the browser has one, else
   *  the media clock; either must move. A paused, ended or empty element, or a track that is not live, shows none. */
  private pollFresh(video: HTMLVideoElement): { presented: number | null } | null {
    if (video.paused || video.ended || video.readyState < 2) return null;
    const track = this.track;
    if (track && (track.readyState !== 'live' || track.muted)) return null;
    const total = num(video.getVideoPlaybackQuality?.().totalVideoFrames);
    if (total !== null) {
      const fresh = this.polledFrames !== null && total > this.polledFrames;
      this.polledFrames = total;
      return fresh ? { presented: total } : null;
    }
    const t = num(video.currentTime);
    if (t === null) return null;
    const fresh = this.polledTime !== null && t > this.polledTime;
    this.polledTime = t;
    return fresh ? { presented: null } : null;
  }

  private deliver(now: number, md: VideoFrameCallbackMetadata | null, video: HTMLVideoElement, polledFrames: number | null): void {
    this.held = null;
    const captureTime = num(md?.captureTime), presentationTime = num(md?.presentationTime);
    const expectedDisplayTime = num(md?.expectedDisplayTime), mediaTime = num(md?.mediaTime);
    const meta: FrameMeta = {
      t: frameTime({ captureTime: captureTime ?? undefined, presentationTime: presentationTime ?? undefined }, now),
      frameNo: ++this.frameCount,
      captureTime, presentationTime, expectedDisplayTime, mediaTime,
      width: num(md?.width) ?? video.videoWidth, height: num(md?.height) ?? video.videoHeight,
      presentedFrames: md ? num(md.presentedFrames) : polledFrames,
      viaRvfc: md !== null,
    };
    this.lastFrameTime = now;
    if (captureTime !== null) pushRing(this.lagNow, now - captureTime);
    if (captureTime !== null && presentationTime !== null) pushRing(this.lagPresent, presentationTime - captureTime);
    if (expectedDisplayTime !== null) pushRing(this.lagExpected, expectedDisplayTime - now);
    if (mediaTime !== null) {
      const us = mediaTime * 1e6;
      if (this.lastMediaUs !== null) {
        const step = us - this.lastMediaUs;
        if (step > 0 && step < 1e6) pushRing(this.intervalsUs, step, INTERVAL_SAMPLES);
      }
      this.lastMediaUs = us;
    }
    this.benchStep(meta);
    this.callback?.(meta);
  }

  // ---- readback -------------------------------------------------------------------------------------------------

  private hasPicture(video: HTMLVideoElement): boolean {
    return video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0;
  }

  /** One timed readback through pipeline A or B (4.2).
   *  A: draw the video into the `willReadFrequently` canvas, then `getImageData`.
   *  B: draw the video into a GPU canvas of the analysis size that is never read back, draw that into the
   *     `willReadFrequently` canvas, then `getImageData`; it moves 230 KB instead of converting the whole frame on the CPU.
   *  The time is `drawImage` plus `getImageData` and nothing else. With `VideoFrame` and a media time, the frame is drawn
   *  from `new VideoFrame(video)`, closed afterwards, and its timestamp is checked against the frame the callback
   *  announced; the replay has no VideoFrame and draws the element. */
  private grab(pipeline: Pipeline, meta: FrameMeta): { rgba: Uint8ClampedArray; ms: number } | null {
    const video = this.video, a = this.analysisCtx;
    if (!video || !a || !this.analysis || !this.hasPicture(video)) return null;
    const { w, h } = this;
    let vf: VideoFrame | null = null;
    if (meta.mediaTime !== null && typeof VideoFrame === 'function') {
      try { vf = new VideoFrame(video); } catch { vf = null; }
    }
    try {
      const source: CanvasImageSource = vf ?? video;
      const started = performance.now();
      if (pipeline === 'B' && this.gpu && this.gpuCtx) {
        this.gpuCtx.drawImage(source, 0, 0, w, h);
        a.drawImage(this.gpu, 0, 0, w, h);
      } else {
        a.drawImage(source, 0, 0, w, h);
      }
      const rgba = a.getImageData(0, 0, w, h).data;
      const ms = performance.now() - started;
      if (vf && meta.mediaTime !== null) this.pair(vf.timestamp, meta.mediaTime);
      return { rgba, ms };
    } catch {
      return null;
    } finally {
      vf?.close();
    }
  }

  /** A pair is a slip when the VideoFrame is more than half a median frame interval away from the callback's frame
   *  (RD finding 14): the element had already moved on to another picture by the time it was read. */
  private pair(timestampUs: number, mediaTimeS: number): void {
    this.slipPairs++;
    const interval = this.intervalsUs.length >= 3 ? percentile(this.intervalsUs, .5) : DEFAULT_INTERVAL_US;
    if (Math.abs(timestampUs - mediaTimeS * 1e6) > interval / 2) this.slipCount++;
  }

  private analysisFrame(meta: FrameMeta, got: { rgba: Uint8ClampedArray; ms: number }): AnalysisFrame {
    return { frameId: meta.frameNo, t: meta.t, w: this.w, h: this.h, rgba: got.rgba, readbackMs: got.ms };
  }

  /** The first `benchFrames` delivered frames alternate A and B, A first, each read back and timed by the source itself
   *  so the benchmark does not depend on which frames the tracker wants. The lower p50 wins and a tie goes to A: under
   *  the replay's frozen clock both read 0, so A runs and a replay stays deterministic. B wins only when it has
   *  samples and A has none or a strictly higher p50. */
  private benchStep(meta: FrameMeta): void {
    if (this.benchDone >= this.benchFrames) return;
    const pipeline: Pipeline = this.benchDone % 2 === 0 ? 'A' : 'B';
    this.benchDone++;
    const got = this.grab(pipeline, meta);
    if (got) {
      (pipeline === 'A' ? this.benchA : this.benchB).push(got.ms);
      this.held = { frameNo: meta.frameNo, frame: this.analysisFrame(meta, got) };
    }
    if (this.benchDone < this.benchFrames) return;
    const aMs = stats(this.benchA), bMs = stats(this.benchB);
    this.chosen = bMs.n > 0 && (aMs.n === 0 || bMs.p50! < aMs.p50!) ? 'B' : 'A';
    this.benchResult = { aMs, bMs, chosen: this.chosen, smoothing: 'medium' };
  }

  // ---- exposure -------------------------------------------------------------------------------------------------

  /** Once a second, outside the frame path, note what the track says its exposure time is (RD finding 18). Chromium
   *  refreshes that setting when `ImageCapture.getPhotoCapabilities()` resolves, so where ImageCapture exists it is
   *  asked first. The first probe runs at open, which makes the answer for a browser that never reports an exposure time
   *  a fact of the open and not of how long the scan ran. Nothing reads the answer but the report. */
  private startExposureProbe(generation: number): void {
    this.probeExposure(generation);
    this.exposureTimer = setInterval(() => this.probeExposure(generation), EXPOSURE_PROBE_MS);
  }

  private probeExposure(generation: number): void {
    const track = this.track;
    if (!track || generation !== this.generation) return;
    const read = () => {
      if (generation !== this.generation) return;
      const s = track.getSettings?.() as (MediaTrackSettings & { exposureTime?: number }) | undefined;
      const v = num(s?.exposureTime);
      if (v !== null && v > 0 && !this.exposureSeen.includes(v) && this.exposureSeen.length < 2) this.exposureSeen.push(v);
      this.exposureResult = this.exposureSeen.length >= 2 ? true : this.exposureSeen.length === 0 ? false : null;
    };
    const IC = (globalThis as { ImageCapture?: new (t: MediaStreamTrack) => { getPhotoCapabilities(): Promise<unknown> } }).ImageCapture;
    if (typeof IC === 'function') {
      try {
        this.imageCapture ??= new IC(track);
        void this.imageCapture.getPhotoCapabilities().then(read, read);
        return;
      } catch { /* read what the track already says */ }
    }
    read();
  }
}
