// videoModel.ts - every number and sentence VIDEO mode needs, as pure functions
// over the wire's own shapes (D-RIG-1).
//
// WHY THE ARITHMETIC IS MIRRORED RATHER THAN ASKED FOR. The server rounds the
// subframe (`imaging/video.py:133-160 align_roi`), plans the frame count
// (`video.py:316`) and sizes the file (`video.py:322-324`) - but it only says
// so in the 202, which arrives AFTER the camera has been claimed for the whole
// recording. A picker that showed one rectangle and a size, then handed back a
// different rectangle and a 507, would teach the operator to distrust both. So
// the same three sums run here, before the press, and the RESPONSE'S numbers
// replace them the moment a recording starts - `VideoStarted.roi` and
// `actual_fps` are authoritative and these are a forecast.
//
// THE ONE THING THIS FILE CANNOT KNOW EXACTLY. `caps.roi_align` reached the
// status bus with S7c (`hub.py:6963`, published as a two-element list), so a
// rig running that engine hands us its real grid. An OLDER engine does not, and
// there the default below is ZWO's (8, 2) - which is right for the two native
// adapters in this tree and wrong for nothing we ship. Either way the server
// rounds again and the readout corrects itself once, visibly, rather than
// silently recording a different rectangle than the one on screen.
//
// `fps` AND `actual_fps` ARE DIFFERENT NUMBERS (types.ts says so at length):
// `fps` is measured, `actual_fps` is the plan. Nothing here lets them share a
// label.

import type { RigStatus, VideoEvent, VideoRecording, VideoRoi, VideoState } from "../../../../../types";

// ------------------------------------------------------------------ constants

/** ZWO's subframe grid, the fallback when the engine does not publish
 *  `camera.roi_align`: width on a multiple of 8, height on a multiple of 2
 *  (`devices/cameras/adapter.py:72`). */
export const DEFAULT_ROI_ALIGN: readonly [number, number] = [8, 2];

/** `imaging/ser.py:69`. The SER container's fixed header, counted here so the
 *  size forecast and the server's `est_bytes` are the same sum and not two
 *  numbers that nearly agree. */
export const SER_HEADER_BYTES = 178;

/** Per-frame trailer the writer appends beside the pixels (`video.py:324`). */
export const SER_FRAME_TRAILER_BYTES = 8;

/** `video.py:311`. A duration past this is clamped server-side, so the picker
 *  refuses it here rather than letting the operator plan a file that will be
 *  cut short. */
export const MAX_DURATION_S = 1800;

/** The states a recording ends on (`types.ts VideoRecordingState`). Terminal
 *  means the camera is free again, which is why `active` is derived from it. */
const TERMINAL: ReadonlySet<string> = new Set(["done", "cancelled", "failed", "idle"]);

export function isTerminalVideoState(state: string): boolean {
  return TERMINAL.has(state);
}

// -------------------------------------------------------------- the subframe

function gcd(a: number, b: number): number {
  let x = Math.abs(Math.trunc(a));
  let y = Math.abs(Math.trunc(b));
  while (y) { const t = x % y; x = y; y = t; }
  return x || 1;
}

/** Least common multiple, the same rule `align_roi` uses to satisfy the brand's
 *  grid and the binning factor at once. */
export function lcm(a: number, b: number): number {
  const x = Math.max(1, Math.trunc(a) || 1);
  const y = Math.max(1, Math.trunc(b) || 1);
  return (x / gcd(x, y)) * y;
}

const int = (v: unknown, fallback = 0): number => {
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
};

/** The client-side mirror of `imaging/video.py:133-160 align_roi`.
 *
 *  Line for line, including the part that looks like an omission: x and y are
 *  CLAMPED but not rounded onto the grid. The sensor's constraint is on the
 *  width and the height (that is what a row length is made of); an origin one
 *  pixel across only moves which photons land in the frame. Rounding it here
 *  would make the picker disagree with the server about a number the server
 *  never touches.
 *
 *  A width smaller than one step comes back as one step, never 0: a zero-width
 *  subframe is a file with no pixels in it, and the operator asked for a small
 *  one, not for nothing. */
export function alignRoi(
  roi: Partial<VideoRoi>,
  sensorW: number,
  sensorH: number,
  align: readonly [number, number] = DEFAULT_ROI_ALIGN,
): VideoRoi {
  const sw = Math.max(1, int(sensorW, 1));
  const sh = Math.max(1, int(sensorH, 1));
  const bin = Math.max(1, int(roi.bin, 1));
  const stepW = lcm(Math.max(1, int(align[0], 1)), bin);
  const stepH = lcm(Math.max(1, int(align[1], 1)), bin);

  let x = Math.max(0, Math.min(int(roi.x, 0), Math.max(0, sw - stepW)));
  let y = Math.max(0, Math.min(int(roi.y, 0), Math.max(0, sh - stepH)));
  x = Math.max(0, x);
  y = Math.max(0, y);

  let w = Math.max(stepW, Math.min(int(roi.w, sw) || sw, sw - x));
  let h = Math.max(stepH, Math.min(int(roi.h, sh) || sh, sh - y));
  w -= w % stepW;
  h -= h % stepH;
  return { x, y, w: Math.max(stepW, w), h: Math.max(stepH, h), bin };
}

/** A centred subframe `side` pixels across, aligned. `side <= 0` means the
 *  whole sensor - the FULL preset, which is not a 0-sized special case but the
 *  absence of a subframe. */
export function centredRoi(
  side: number,
  sensorW: number,
  sensorH: number,
  bin: number,
  align: readonly [number, number] = DEFAULT_ROI_ALIGN,
): VideoRoi {
  const sw = Math.max(1, int(sensorW, 1));
  const sh = Math.max(1, int(sensorH, 1));
  if (!(side > 0)) return alignRoi({ x: 0, y: 0, w: sw, h: sh, bin }, sw, sh, align);
  const w = Math.min(side, sw);
  const h = Math.min(side, sh);
  return alignRoi(
    { x: Math.floor((sw - w) / 2), y: Math.floor((sh - h) / 2), w, h, bin },
    sw, sh, align,
  );
}

/** `320 x 240 at (852, 636), bin 2` - the whole rectangle in one line, because
 *  four numbers in four tiles is four things to compare and one sentence is
 *  one. */
export function roiLine(roi: VideoRoi | null | undefined): string {
  if (!roi) return "whole sensor";
  const bin = roi.bin > 1 ? `, bin ${roi.bin}` : "";
  return `${roi.w} x ${roi.h} at (${roi.x}, ${roi.y})${bin}`;
}

// ------------------------------------------------------------------ the file

/** `max(1, floor(duration * fps))` - `video.py:316`, exactly. A duration too
 *  short for one frame still records one: the recorder never writes an empty
 *  container. */
export function plannedFrames(durationS: number, fps: number): number {
  const d = Number.isFinite(durationS) ? durationS : 0;
  const f = Number.isFinite(fps) ? fps : 0;
  return Math.max(1, Math.floor(d * f));
}

/** `video.py:322-324`. RAW16 on every adapter in this tree, so two bytes a
 *  pixel even for an 8-bit sensor - narrowing it here would be a guess about
 *  data nobody measured. */
export function estimateBytes(opts: {
  w: number; h: number; bin: number; targetFrames: number;
}): number {
  const bin = Math.max(1, int(opts.bin, 1));
  const bw = Math.floor(Math.max(0, int(opts.w, 0)) / bin);
  const bh = Math.floor(Math.max(0, int(opts.h, 0)) / bin);
  const frames = Math.max(0, int(opts.targetFrames, 0));
  const frameBytes = bw * bh * 2;
  return SER_HEADER_BYTES + frames * (frameBytes + SER_FRAME_TRAILER_BYTES);
}

// ---------------------------------------------------------------- the clamp

/** The server's own sentence for a rate it could not deliver, prefixed with the
 *  rate it WILL deliver.
 *
 *  Never re-worded: `video.py:169-181` composes it from the camera's real
 *  numbers ("this camera has no burst path ... about 11.4 fps at 8 ms"), and a
 *  paraphrase would drop the measurement that makes it actionable. */
export function clampNote(state: VideoState | null | undefined): string | null {
  if (!state || !state.clamped) return null;
  const reason = state.clamp_reason;
  if (!reason) return null;
  const fps = state.actual_fps;
  const rate = fps != null && Number.isFinite(fps) ? `${Number(fps.toFixed(2))}` : "a lower rate";
  return `Recording at ${rate} fps: ${reason}`;
}

// -------------------------------------------------------------- the progress

export interface VideoProgress {
  /** `frames / target_frames`, or **null while arming**: a 0 % bar during
   *  arming is a claim that the file has started and no frames have landed,
   *  which is a different (and alarming) fact from "the camera is being set
   *  up". Null renders as an indeterminate state, never as zero. */
  fraction: number | null;
  frames: number;
  targetFrames: number;
  elapsedS: number;
  /** Seconds left, from the MEASURED rate where there is one - the plan's rate
   *  would keep promising a finish time the file is not going to meet. Null
   *  when nothing has been measured yet. */
  remainingS: number | null;
  dropped: number;
  bytes: number;
}

export function progress(state: VideoState | null | undefined): VideoProgress | null {
  if (!state || !state.id) return null;
  const frames = Math.max(0, int(state.frames, 0));
  const target = Math.max(0, int(state.target_frames, 0));
  const arming = state.state === "arming";
  const fraction = arming || target <= 0
    ? null
    : Math.max(0, Math.min(1, frames / target));
  const fps = Number(state.fps);
  const remainingS = !arming && fps > 0 && target > frames
    ? (target - frames) / fps
    : null;
  return {
    fraction,
    frames,
    targetFrames: target,
    elapsedS: Number.isFinite(state.elapsed_s) ? state.elapsed_s : 0,
    remainingS,
    dropped: Math.max(0, int(state.dropped, 0)),
    bytes: Math.max(0, int(state.bytes, 0)),
  };
}

/** The line beside the bar. `dropped` is only mentioned when there are some:
 *  "0 dropped" on every recording trains the eye to skip the field that
 *  matters. */
export function progressLine(p: VideoProgress | null, state: VideoState | null): string | null {
  if (!p || !state) return null;
  if (state.state === "arming") return "arming the camera - no frames yet";
  const bits = [`${p.frames} / ${p.targetFrames} frames`];
  if (state.fps > 0) bits.push(`${Number(state.fps.toFixed(1))} fps measured`);
  if (p.remainingS != null) bits.push(`${Math.ceil(p.remainingS)} s left`);
  if (p.dropped > 0) bits.push(`${p.dropped} dropped`);
  return bits.join(" - ");
}

// ----------------------------------------------- the bus event and the GET

/** Widen a bus event into the status shape. The five fields the event does not
 *  carry stay ABSENT (or null), never zeroed: `roi`, `actual_fps` and the rest
 *  describe the file being written and only the GET and the 202 know them, so
 *  inventing a 0 fps plan here would put a number under a label that had no
 *  measurement behind it. */
export function videoStateFromEvent(ev: VideoEvent): VideoState {
  return {
    active: !isTerminalVideoState(ev.state),
    id: ev.id,
    state: ev.state,
    frames: ev.frames,
    target_frames: ev.target_frames,
    elapsed_s: ev.elapsed_s,
    fps: ev.fps,
    dropped: ev.dropped,
    bytes: ev.bytes,
    roi: null,
    started_ts: null,
    finished_ts: null,
    error: null,
  };
}

/** One state out of the two channels that carry it.
 *
 *  The bus is the fast one (about 2 Hz) and the GET is the complete one, so
 *  neither can be dropped: the event has no `roi`, no `actual_fps` and no
 *  `clamp_reason`, and the GET only arrives on mount and on the watchdog. When
 *  they describe the SAME recording the event's live counters win over the
 *  fetched ones and the fetched description survives; when they describe
 *  different recordings the event wins whole, because an event is something
 *  happening now and a fetch is something that was true when it was asked. */
export function mergeVideoState(
  fetched: VideoState | null,
  ev: VideoEvent | null,
): VideoState | null {
  if (!ev) return fetched;
  if (!fetched || fetched.id !== ev.id) return videoStateFromEvent(ev);
  return {
    ...fetched,
    ...ev,
    active: !isTerminalVideoState(ev.state),
  };
}

// ---------------------------------------------------------- camera capability

export interface VideoCapability {
  /** `"native"` records, `"none"` cannot, `"unknown"` is an engine older than
   *  S7c that does not publish the field (`hub.py:6968`) - and the three are
   *  not two: "unknown" must render the controls live and let the server refuse
   *  with its own sentence, while "none" is knowable before the press. */
  path: "native" | "none" | "unknown";
  roiAlign: readonly [number, number];
  /** The rate the driver says it will sustain, or null when it does not say -
   *  which is NOT the same as slow. */
  maxFps: number | null;
  burst: boolean | null;
  sensorW: number;
  sensorH: number;
  name: string | null;
}

/** Read the four S7c capability fields off the status, tolerating both the
 *  string the engine publishes and the boolean `ui/src/types.ts` currently
 *  declares.
 *
 *  THE TYPE AND THE WIRE DISAGREE TODAY. `hub.py:6968` publishes
 *  `"native" | "none"`; `types.ts:231` declares `video_path?: boolean`. That
 *  file belongs to another task in this wave, so the mismatch is read through
 *  `unknown` here and reported rather than patched from inside a hub. Both
 *  shapes are handled so neither an older client type nor a newer engine can
 *  turn "cannot record" into "can". */
export function videoCapability(status: RigStatus | null | undefined): VideoCapability {
  const cam = status?.camera as ({
    video_path?: unknown; roi_align?: unknown; max_fps?: unknown;
    burst_supported?: unknown; width?: unknown; height?: unknown;
  } | undefined);
  const raw = cam?.video_path;
  const path: VideoCapability["path"] =
    raw === "native" || raw === true ? "native"
      : raw === "none" || raw === false ? "none"
        : "unknown";
  const alignRaw = cam?.roi_align;
  const align: readonly [number, number] = Array.isArray(alignRaw)
    && Number(alignRaw[0]) > 0 && Number(alignRaw[1]) > 0
    ? [Math.trunc(Number(alignRaw[0])), Math.trunc(Number(alignRaw[1]))]
    : DEFAULT_ROI_ALIGN;
  const maxFpsRaw = Number(cam?.max_fps);
  const burstRaw = cam?.burst_supported;
  return {
    path,
    roiAlign: align,
    maxFps: Number.isFinite(maxFpsRaw) && maxFpsRaw > 0 ? maxFpsRaw : null,
    burst: typeof burstRaw === "boolean" ? burstRaw : null,
    sensorW: Math.max(1, int(cam?.width, 1)),
    sensorH: Math.max(1, int(cam?.height, 1)),
    name: status?.connected?.camera?.name ?? null,
  };
}

// -------------------------------------------------------------------- copy

/** What the operator can be told BEFORE the press, from the published
 *  capability alone.
 *
 *  It deliberately stops short of the server's own sentence, which names the
 *  brand AND the backend (`video_routes.py:120-127`); the status bus carries
 *  neither, and inventing a backend name would be the guess the brief forbids.
 *  When the first press does happen on an older engine, that fuller sentence
 *  arrives on the 409 and replaces this one. */
export function noVideoPathReason(name: string | null): string {
  const who = name ? name : "This camera";
  return `${who} cannot record video through AstroDeck: it has no subframe or burst `
    + "path. Video recording needs a natively-driven camera.";
}

/** The still path is untouched by any of this, and saying so is the difference
 *  between "video is unavailable" and "the camera is broken". */
export const STILL_STILL_WORKS_NOTE =
  "Single frames, LOOP and Live View still work on this camera - none of them "
  + "needs a subframe or a burst path.";

/** The 507. OURS, not the server's, because the server sends two numbers and no
 *  sentence (`video_routes.py:147-151`) - and the two numbers ARE the message:
 *  "shorten the duration" is only actionable next to how far short it fell. */
export function diskShortfallSentence(
  freeBytes: number, requiredBytes: number, fmt: (n: number) => string,
): string {
  return `Not enough disk for this recording: it needs ${fmt(requiredBytes)} and there `
    + `is ${fmt(freeBytes)} free. Shorten the duration, or free space in the gallery.`;
}

/** The ROI picker's own line. The server rounds AGAIN
 *  (`video.py:133-160`), and a picker that did not say so would look wrong the
 *  first time a camera with a different grid handed back a different width. */
export function roiAlignNote(align: readonly [number, number], published: boolean): string {
  const source = published
    ? "the grid this camera reports"
    : "the default grid, because this engine does not report the camera's";
  return `Width goes to a multiple of ${align[0]} and height to a multiple of ${align[1]} `
    + `- ${source} - and to the binning on top of that. The camera rounds it again, and `
    + "the ROI on the recording below is the one that was actually used.";
}

// ------------------------------------------------------------ recordings list

/** One row's sentence: what was recorded, how fast it really went, and how big
 *  it is. `fps` here is the ACHIEVED rate out of the sidecar (`video.py:580-592`),
 *  which is why it is labelled measured and not planned. */
export function recordingLine(
  rec: VideoRecording, fmtBytes: (n: number) => string,
): string {
  const bits = [`${rec.frames.toLocaleString("en")} frames`];
  if (rec.fps > 0) bits.push(`${Number(rec.fps.toFixed(1))} fps measured`);
  bits.push(roiLine(rec.roi));
  bits.push(fmtBytes(rec.bytes));
  return bits.join(" - ");
}

/** The row's own clock. A recording id already carries the date it was made
 *  (`video.py:118-130` names the file from the local stamp), so this adds what
 *  the id cannot: the reader's own locale and time zone, which is what tells
 *  them whether "2026-09-10T2214" was last night or the night before. */
export function recordingStamp(unixSeconds: number): string {
  if (!Number.isFinite(unixSeconds)) return "no timestamp";
  return new Date(unixSeconds * 1000).toLocaleString();
}

/** Is a lane live on the rig right now? `busy_lanes` is unreduced
 *  (`hub.py:2101`), so `video` and `video_stack` are separately visible - which
 *  matters, because one owns the camera and the other owns nothing at all. */
export function laneBusy(status: RigStatus | null | undefined, lane: string): boolean {
  const lanes = status?.busy_lanes;
  return Array.isArray(lanes) && lanes.includes(lane);
}
