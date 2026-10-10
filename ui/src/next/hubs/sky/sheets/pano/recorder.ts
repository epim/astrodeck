// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The opt-in recording of a scan (SPEC-v2 13.9, D25): the camera frames and the
// sensor events the scanner saw, as JSON Lines the simulator can import
// (`python -m sim import-recording`). A recording is how a real phone's night
// becomes a test case, so it is the same observation lines the replay reads
// (13.1) with the frame's pixels inline in place of a file name.
//
// Synchronous and passive: every method appends a line and returns, and the
// frame path never waits on it. The pixel encoder is injected (FrameEncoder),
// JPEG on the phone and lossless PNG in the replay and the tests, so this file
// touches no canvas and no codec. The recording carries photos of the
// surroundings; it stays on the phone unless the owner shares it (O4).
//
// Every line is written from a list of named fields, never from the object it
// was handed, so a caller that passes a whole event or a settings object cannot
// put an extra key in the file. Times are written exactly as given (ms since
// the camera opened, 13.9) and nothing here reads a clock.
import { buildReport } from './report';
import type { CameraSettingsReport, FrameEncoder, RecordingHeader, ScanReport } from './types';

export class Recorder {
  private readonly encoder: FrameEncoder;
  private readonly lines: string[] = [];
  private everyN: 1 | 2 | 3 = 1;
  private delivered = 0;                 // frame() calls so far, kept or not
  private lastKept = -Infinity;          // the call number of the last frame kept
  private framesKept = 0;
  private eventCount = 0;
  private size = 0;                      // characters written so far, a newline per line included

  constructor(o: { encoder: FrameEncoder; header: Omit<RecordingHeader, 'kind' | 'format' | 'version' | 'encoder'> }) {
    this.encoder = o.encoder;
    const s = o.header.settings;
    const settings: CameraSettingsReport = {
      width: s.width ?? null, height: s.height ?? null, frameRate: s.frameRate ?? null,
      resizeMode: s.resizeMode ?? null, zoom: s.zoom ?? null, focusMode: s.focusMode ?? null,
      focusDistance: s.focusDistance ?? null, facingMode: s.facingMode ?? null, label: s.label ?? null,
    };
    const header: RecordingHeader = {
      kind: 'header', format: 'astrodeck-pano-recording', version: 1, encoder: o.encoder.mime,
      video: { width: o.header.video.width, height: o.header.video.height },
      analysis: { width: o.header.analysis.width, height: o.header.analysis.height },
      settings,
      commit: o.header.commit ?? null,
    };
    this.push(header);
  }

  /** Frames kept per frame delivered: 1 keeps all, 2 every second, 3 every third. */
  get every(): number { return this.everyN; }
  /** Frames written. */
  get frames(): number { return this.framesKept; }
  /** Orientation and motion lines written. Frames and actions are not events. */
  get events(): number { return this.eventCount; }
  /** Characters written so far, header included and the final report not. */
  get chars(): number { return this.size; }
  /**
   * Whether the next frame() call will be kept: the rule frame() applies,
   * asked before the call. A caller that has to read the pixels back reads
   * them only when this is true and hands frame() an empty array otherwise,
   * which a skipped call never reads (3.5 step 4, S24). It looks at the call
   * count and the last kept call, not at the count alone, so it follows a
   * change of `every` made by setEvery.
   */
  get keepsNext(): boolean { return this.delivered + 1 - this.lastKept >= this.everyN; }

  /**
   * One call per delivered frame. The first call is kept and then every n-th,
   * counted from the last frame kept, so a change of `every` takes effect at
   * once and no stretch is longer than `every` frames. A frame that is not
   * kept is not encoded and its `rgba` is not read (`keepsNext` says in
   * advance which calls are kept). `frameId` is written as given: the caller
   * numbers frames by delivery (f000001, f000002, ...), so a decimated
   * recording shows its gaps in the ids.
   */
  frame(f: { frameId: string; tCaptureMs: number | null; tPresentMs: number; w: number; h: number; rgba: Uint8ClampedArray }): void {
    this.delivered++;
    if (this.delivered - this.lastKept < this.everyN) return;
    this.lastKept = this.delivered;
    this.framesKept++;
    this.push({
      kind: 'frame', frame_id: f.frameId, t_capture_ms: f.tCaptureMs, t_present_ms: f.tPresentMs,
      width: f.w, height: f.h, mime: this.encoder.mime, data: this.encoder.encode(f.rgba, f.w, f.h),
    });
  }

  orientation(o: { event: 'deviceorientation' | 'deviceorientationabsolute'; tEventMs: number; tReceiveMs: number;
    alpha: number | null; beta: number | null; gamma: number | null; absolute: boolean | null }): void {
    this.eventCount++;
    this.push({
      kind: 'orientation', event: o.event, t_event_ms: o.tEventMs, t_receive_ms: o.tReceiveMs,
      alpha: o.alpha, beta: o.beta, gamma: o.gamma, absolute: o.absolute,
    });
  }

  motion(m: { tEventMs: number; tReceiveMs: number; rate: { alpha: number | null; beta: number | null; gamma: number | null } | null }): void {
    this.eventCount++;
    this.push({
      kind: 'motion', t_event_ms: m.tEventMs, t_receive_ms: m.tReceiveMs,
      rate: m.rate ? { alpha: m.rate.alpha, beta: m.rate.beta, gamma: m.rate.gamma } : null,
    });
  }

  action(tMs: number, action: 'begin' | 'finish'): void {
    this.push({ kind: 'action', t_ms: tMs, action });
  }

  /** Keep every n-th frame from now on. Anything but 1, 2 or 3 is refused: a silent 0 or NaN would drop the whole recording. */
  setEvery(n: 1 | 2 | 3): void {
    if (n !== 1 && n !== 2 && n !== 3) throw new RangeError(`Recorder.setEvery takes 1, 2 or 3, not ${String(n)}`);
    this.everyN = n;
  }

  /**
   * The JSON Lines text of 13.9: the header, the lines in the order they
   * arrived, then the report when there is one. Every line ends in a newline.
   * The report goes through buildReport again, so a report object that grew a
   * stray key still writes only what ScanReport declares. Calling it does not
   * close the recording.
   */
  finish(report: ScanReport | null): string {
    const tail = report ? JSON.stringify({ kind: 'report', report: buildReport(report) }) + '\n' : '';
    return this.lines.join('') + tail;
  }

  private push(line: object): void {
    const text = JSON.stringify(line) + '\n';
    this.lines.push(text);
    this.size += text.length;
  }
}

/**
 * The phone's frame encoder: the frame goes onto `canvas` with putImageData and
 * comes back out of toDataURL as a JPEG, with the `data:image/jpeg;base64,`
 * prefix removed. All synchronous. The canvas is resized to the frame and the
 * ImageData is built once per frame size and reused, so a long recording
 * allocates nothing per frame. A browser that cannot encode JPEG answers
 * toDataURL with a PNG; that is refused, since a recording whose frames say
 * `image/jpeg` over PNG bytes would hide the fault until the import.
 */
export function jpegEncoder(canvas: HTMLCanvasElement, quality = 0.85): FrameEncoder {
  const prefix = 'data:image/jpeg;base64,';
  let scratch: ImageData | null = null;
  return {
    mime: 'image/jpeg',
    encode(rgba: Uint8ClampedArray, w: number, h: number): string {
      // A short buffer would leave the previous frame's pixels in the rest of
      // the scratch image and encode a blend of two frames without a sound.
      if (rgba.length !== w * h * 4) throw new Error(`jpegEncoder: ${rgba.length} bytes is not a ${w} x ${h} RGBA frame`);
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('jpegEncoder: the canvas has no 2d context');
      if (canvas.width !== w) canvas.width = w;
      if (canvas.height !== h) canvas.height = h;
      if (!scratch || scratch.width !== w || scratch.height !== h) scratch = ctx.createImageData(w, h);
      scratch.data.set(rgba);
      ctx.putImageData(scratch, 0, 0);
      const url = canvas.toDataURL('image/jpeg', quality);
      if (!url.startsWith(prefix)) throw new Error('jpegEncoder: the browser did not encode a JPEG');
      return url.slice(prefix.length);
    },
  };
}
