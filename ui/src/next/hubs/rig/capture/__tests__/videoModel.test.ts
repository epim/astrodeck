// videoModel.test.ts - the arithmetic and the sentences behind VIDEO mode.
//
//   Run directly:  npx tsx src/next/hubs/rig/capture/__tests__/videoModel.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by tsc.
//
// WHY THESE FOUR THINGS AND NOT THE WHOLE FILE. Each one is a place where a
// plausible-looking answer is a wrong one and nothing on screen would say so:
//
//  1. `alignRoi` mirrors the server's `align_roi`. A client that rounded to
//     `% 8` alone would agree with the server at bin 1 and disagree at bin 3 -
//     the picker would show a width the recording never used, and the only
//     symptom is a rectangle that changes by itself after the press.
//  2. A subframe smaller than one step must come back as ONE STEP, never 0. A
//     zero-width ROI is a file with no pixels in it, and the operator asked for
//     a small window, not for nothing.
//  3. `clampNote` must carry the server's sentence UNTOUCHED. `video.py:169-181`
//     composes it out of the camera's real numbers ("about 11.4 fps at 8 ms");
//     a paraphrase drops the measurement that makes it actionable.
//  4. `progress` must be NULL while arming. A 0 percent bar is a claim that the
//     file has started and no frames have landed - a different and more
//     alarming fact than "the camera is being set up".
//
// Pure: no jsdom, no store, no fetch. `videoModel.ts` imports only types.

const {
  DEFAULT_ROI_ALIGN, SER_HEADER_BYTES, SER_FRAME_TRAILER_BYTES,
  alignRoi, centredRoi, clampNote, diskShortfallSentence, estimateBytes,
  isTerminalVideoState, laneBusy, lcm, mergeVideoState, noVideoPathReason,
  plannedFrames, progress, progressLine, recordingLine, roiAlignNote, roiLine,
  videoCapability, videoStateFromEvent,
} = await import("../video/videoModel");

type VideoState = import("../../../../../types").VideoState;
type VideoEvent = import("../../../../../types").VideoEvent;
type VideoRecording = import("../../../../../types").VideoRecording;
type RigStatus = import("../../../../../types").RigStatus;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

const state = (over: Partial<VideoState> = {}): VideoState => ({
  active: true, id: "2026-09-10T2214-ser01", state: "recording",
  frames: 250, target_frames: 1000, elapsed_s: 8.3, fps: 30.1, dropped: 0,
  bytes: 4_000_000, roi: { x: 0, y: 0, w: 640, h: 480, bin: 1 },
  requested_fps: 60, actual_fps: 30, clamped: false, clamp_reason: null,
  started_ts: 1_757_500_000, finished_ts: null, error: null,
  ...over,
});

// ------------------------------------------------------------------ alignRoi
test("lcm satisfies both constraints, which is the whole reason align_roi uses it", () => {
  eq(lcm(8, 2), 8, "lcm(8,2)");
  eq(lcm(8, 3), 24, "lcm(8,3) - a bin of 3 is why % 8 alone is not enough");
  eq(lcm(2, 2), 2, "lcm(2,2)");
  eq(lcm(0, 5), 5, "a zero alignment must not divide by zero");
});

test("alignRoi rounds 1023 x 769 at bin 2 to the server's own grid", () => {
  const r = alignRoi({ x: 0, y: 0, w: 1023, h: 769, bin: 2 }, 4000, 3000);
  // lcm(8,2) = 8 and lcm(2,2) = 2, so 1023 -> 1016 and 769 -> 768.
  eq(r.w, 1016, "width did not land on lcm(roi_align.x, bin)");
  eq(r.h, 768, "height did not land on lcm(roi_align.y, bin)");
  eq(r.w % 2, 0, "the width is not a whole number of bins - the row length is one nobody agrees on");
  eq(r.bin, 2, "the binning was dropped");
});

test("alignRoi at bin 3 needs the LCM, which is the sabotage row", () => {
  // The step is lcm(8, 3) = 24. Rounding with `% 8` alone would return 1016,
  // which is not a whole number of 3-pixel bins.
  const r = alignRoi({ x: 0, y: 0, w: 1023, h: 769, bin: 3 }, 4000, 3000);
  eq(r.w, 1008, "a `% 8`-only rounding survived - 1008 is the first multiple of 24 below 1023");
  eq(r.w % 3, 0, "the width is not a whole number of bins");
  eq(r.h % 3, 0, "the height is not a whole number of bins");
});

test("a subframe smaller than one step comes back as one step, never 0", () => {
  const r = alignRoi({ x: 0, y: 0, w: 3, h: 1, bin: 1 }, 4000, 3000);
  eq(r.w, 8, "a 3 px width rounded down to nothing - that is a file with no pixels in it");
  eq(r.h, 2, "a 1 px height rounded down to nothing");
  const b = alignRoi({ x: 0, y: 0, w: 1, h: 1, bin: 4 }, 4000, 3000);
  eq(b.w, 8, "at bin 4 the step is lcm(8,4)=8 and the floor must be that step");
  eq(b.h, 4, "at bin 4 the height step is lcm(2,4)=4");
});

test("alignRoi clamps inside the sensor rather than trusting the caller", () => {
  const past = alignRoi({ x: 3990, y: 2990, w: 4000, h: 4000, bin: 1 }, 4000, 3000);
  assert(past.x + past.w <= 4000, "the subframe ran off the right edge of the sensor");
  assert(past.y + past.h <= 3000, "the subframe ran off the bottom of the sensor");
  const neg = alignRoi({ x: -50, y: -50, w: 640, h: 480, bin: 1 }, 4000, 3000);
  eq(neg.x, 0, "a negative origin was sent to the camera");
  eq(neg.y, 0, "a negative origin was sent to the camera");
  const junk = alignRoi(
    { x: Number.NaN, y: Number.NaN, w: Number.NaN, h: Number.NaN, bin: Number.NaN },
    4000, 3000,
  );
  eq(junk.bin, 1, "NaN became the binning");
  assert(Number.isFinite(junk.w) && junk.w > 0, "NaN reached the width");
});

test("alignRoi honours a camera whose grid is not ZWO's", () => {
  eq(DEFAULT_ROI_ALIGN[0], 8, "the fallback grid drifted from the ZWO default");
  const r = alignRoi({ x: 0, y: 0, w: 1000, h: 1000, bin: 1 }, 4000, 3000, [32, 32]);
  eq(r.w, 992, "a published roi_align of 32 was ignored");
  eq(r.h, 992, "a published roi_align of 32 was ignored");
});

test("centredRoi centres, and FULL is the absence of a subframe", () => {
  const c = centredRoi(640, 4000, 3000, 1);
  eq(c.w, 640, "the preset width");
  eq(c.x, Math.floor((4000 - 640) / 2), "the window is not centred horizontally");
  eq(c.y, Math.floor((3000 - 640) / 2), "the window is not centred vertically");
  const full = centredRoi(0, 4000, 3000, 1);
  eq(full.x, 0, "FULL is not at the origin");
  eq(full.w, 4000, "FULL is not the whole sensor");
  // A preset bigger than the sensor is the sensor, not an error.
  const big = centredRoi(9999, 800, 600, 1);
  assert(big.w <= 800 && big.h <= 600, "a preset larger than the sensor escaped it");
});

// ------------------------------------------------------------------ the file
test("plannedFrames and estimateBytes are the server's own sums", () => {
  eq(plannedFrames(10, 30), 300, "10 s at 30 fps");
  eq(plannedFrames(0.001, 1), 1, "a duration too short for one frame still records one");
  // video.py:322-324 - RAW16, two bytes a pixel, plus an 8-byte trailer.
  const bytes = estimateBytes({ w: 640, h: 480, bin: 1, targetFrames: 300 });
  eq(bytes, SER_HEADER_BYTES + 300 * (640 * 480 * 2 + SER_FRAME_TRAILER_BYTES),
    "the size forecast is not the sum the disk check will use");
  // Binning divides the DOWNLOAD, so it divides the file.
  const binned = estimateBytes({ w: 640, h: 480, bin: 2, targetFrames: 300 });
  assert(binned < bytes / 3, "binning did not shrink the download");
});

// ----------------------------------------------------------------- the clamp
test("clampNote carries the server's sentence untouched, prefixed with the rate", () => {
  const reason = "this camera has no burst path, so every frame is a full "
    + "start/poll/read cycle and the exposure engine polls no faster than 100 ms "
    + "- about 9.3 fps at 8 ms";
  const note = clampNote(state({ clamped: true, clamp_reason: reason, actual_fps: 9.3 }));
  assert(note != null, "a clamped recording said nothing about it");
  assert((note as string).includes(reason),
    "the server's sentence was re-worded - the measurement in it is what makes it actionable");
  assert((note as string).startsWith("Recording at 9.3 fps: "),
    "the clamp note does not lead with the rate that will actually be delivered");
  eq(clampNote(state()), null, "an unclamped recording invented a clamp note");
  eq(clampNote(null), null, "no recording produced a clamp note");
  eq(clampNote(state({ clamped: true, clamp_reason: null })), null,
    "a clamp with no reason produced a sentence out of nothing");
});

// -------------------------------------------------------------- the progress
test("progress is NULL while arming - a 0 percent bar is a claim, not a blank", () => {
  const arming = progress(state({ state: "arming", frames: 0 }));
  assert(arming != null, "arming produced no progress object at all");
  eq((arming as { fraction: number | null }).fraction, null,
    "arming drew a 0 percent bar, which says the file is being written and nothing has landed");
  const running = progress(state());
  eq((running as { fraction: number | null }).fraction, 0.25, "250 of 1000 is not a quarter");
  eq(progress(null), null, "a null state produced progress");
  eq(progress(state({ id: null })), null, "a recorder that has never run produced progress");
});

test("progress bounds the fraction and computes the remainder from the MEASURED rate", () => {
  const over = progress(state({ frames: 1200 }));
  eq((over as { fraction: number | null }).fraction, 1,
    "more frames than planned drew a bar past its own end");
  const p = progress(state({ frames: 250, target_frames: 1000, fps: 25 }));
  eq((p as { remainingS: number | null }).remainingS, 30,
    "the remainder used the planned rate, which keeps promising a finish the file will miss");
  const none = progress(state({ fps: 0 }));
  eq((none as { remainingS: number | null }).remainingS, null,
    "a remainder was invented before anything had been measured");
});

test("the progress line names dropped frames only when there are some", () => {
  const clean = progressLine(progress(state()), state());
  assert(clean != null && !/dropped/.test(clean),
    "'0 dropped' on every recording trains the eye to skip the field that matters");
  const lossy = state({ dropped: 12 });
  const line = progressLine(progress(lossy), lossy);
  assert(line != null && /12 dropped/.test(line), "dropped frames were not reported");
  const arming = state({ state: "arming", frames: 0 });
  eq(progressLine(progress(arming), arming), "arming the camera - no frames yet",
    "arming was narrated as progress");
});

// ------------------------------------------------------- the two channels
test("mergeVideoState keeps the fetched description and takes the live counters", () => {
  const ev: VideoEvent = {
    id: "2026-09-10T2214-ser01", state: "recording", frames: 700,
    target_frames: 1000, elapsed_s: 23.2, fps: 30.2, dropped: 1,
    bytes: 11_000_000, path: null,
  };
  const merged = mergeVideoState(state(), ev);
  assert(merged != null, "the merge lost both sources");
  eq((merged as VideoState).frames, 700, "the live frame count did not win");
  eq((merged as VideoState).actual_fps, 30,
    "the plan's rate was lost - the event does not carry it and nothing else does");
  assert((merged as VideoState).roi != null,
    "the subframe was lost - the event does not carry it either");
  eq((merged as VideoState).active, true, "a recording state read as inactive");
});

test("a terminal event ends the recording, whichever channel it arrives on", () => {
  const done: VideoEvent = {
    id: "2026-09-10T2214-ser01", state: "done", frames: 1000, target_frames: 1000,
    elapsed_s: 33.3, fps: 30, dropped: 0, bytes: 15_000_000,
    path: "video/2026-09-10T2214-ser01.ser",
  };
  eq((mergeVideoState(state(), done) as VideoState).active, false,
    "a finished recording still claimed the camera");
  assert(isTerminalVideoState("cancelled") && isTerminalVideoState("failed"),
    "a cancelled or failed recording read as still running");
  assert(!isTerminalVideoState("finalising"),
    "finalising is still writing the file and must not read as finished");
});

test("an event for a DIFFERENT recording replaces rather than blending", () => {
  const other: VideoEvent = {
    id: "2026-09-10T2301-ser02", state: "recording", frames: 5, target_frames: 900,
    elapsed_s: 0.2, fps: 25, dropped: 0, bytes: 90_000, path: null,
  };
  const merged = mergeVideoState(state(), other) as VideoState;
  eq(merged.id, "2026-09-10T2301-ser02", "the new recording did not win");
  eq(merged.roi, null,
    "the OLD recording's subframe was carried onto a new file - a description of the "
    + "wrong rectangle is worse than none");
  eq(merged.target_frames, 900, "the old plan survived onto a new recording");
});

test("videoStateFromEvent leaves absent fields absent rather than zeroing them", () => {
  const s = videoStateFromEvent({
    id: "a", state: "recording", frames: 1, target_frames: 2, elapsed_s: 1,
    fps: 2, dropped: 0, bytes: 3, path: null,
  });
  eq(s.roi, null, "a subframe was invented");
  eq(s.actual_fps, undefined, "a planned rate of 0 was invented, which is a number under a label");
  eq(s.clamp_reason, undefined, "a clamp reason was invented");
  eq(mergeVideoState(null, null), null, "two empty channels produced a state");
  eq(mergeVideoState(state(), null)?.id, "2026-09-10T2214-ser01",
    "a silent bus dropped the fetched state");
});

// --------------------------------------------------------- the capability
test("videoCapability reads the wire's three answers and keeps them three", () => {
  const st = (camera: Record<string, unknown>): RigStatus =>
    ({ camera, connected: { camera: { name: "ASI662MC" } } } as unknown as RigStatus);
  // hub.py:6968 publishes a STRING, and types.ts now declares that same union.
  eq(videoCapability(st({ video_path: "none" })).path, "none", "the wire's 'none' was not read");
  eq(videoCapability(st({ video_path: "native" })).path, "native", "the wire's 'native' was not read");
  // ABSENT is the third answer and NOT a falsy "none": an engine older than S7c
  // has never been asked, and locking the control on it would refuse a camera
  // that records perfectly well.
  eq(videoCapability(st({})).path, "unknown",
    "an engine that does not publish the field was treated as a verdict");
  eq(videoCapability(null).path, "unknown", "no status at all was treated as a verdict");
});

test("videoCapability falls back to the ZWO grid and never to a fake ceiling", () => {
  const st = (camera: Record<string, unknown>): RigStatus =>
    ({ camera } as unknown as RigStatus);
  eq(videoCapability(st({})).roiAlign[0], 8, "the default grid drifted");
  eq(videoCapability(st({ roi_align: [32, 8] })).roiAlign[1], 8, "a published grid was ignored");
  // JSON has no tuples: `hub.py:6963` sends `list(caps.roi_align)`, so a length
  // the wire never promised must not be destructured on faith.
  eq(videoCapability(st({ roi_align: [16] })).roiAlign[0], 8,
    "a one-element list was read as a grid, which would make one step NaN");
  eq(videoCapability(st({ roi_align: [0, 0] })).roiAlign[0], 8,
    "a zero grid would make every step 0 and every subframe empty");
  eq(videoCapability(st({ max_fps: null })).maxFps, null,
    "'the driver did not say' became a number");
  eq(videoCapability(st({ max_fps: 120 })).maxFps, 120, "a declared ceiling was dropped");
  eq(videoCapability(st({ burst_supported: false })).burst, false, "burst false");
  eq(videoCapability(st({})).burst, null, "an unpublished burst flag became a verdict");
});

// ------------------------------------------------------------------- copy
test("every sentence this mode can print is hyphenated and says something new", () => {
  const strings = [
    noVideoPathReason("ASI294MM"),
    noVideoPathReason(null),
    roiAlignNote([8, 2], true),
    roiAlignNote([8, 2], false),
    diskShortfallSentence(2_100_000_000, 5_800_000_000, (n) => `${n} B`),
    roiLine({ x: 1, y: 2, w: 3, h: 4, bin: 2 }),
    roiLine(null),
  ];
  for (const s of strings) {
    assert(!/[—–]/.test(s), `an em-dash or en-dash reached: ${s}`);
    assert(s.length > 0, "an empty sentence");
  }
  assert(noVideoPathReason("ASI294MM").startsWith("ASI294MM"),
    "the refusal does not name the camera it is about");
  assert(noVideoPathReason(null).startsWith("This camera"),
    "a nameless camera produced a sentence starting with nothing");
  const disk = diskShortfallSentence(2_100_000_000, 5_800_000_000, (n) => `${n} B`);
  assert(disk.includes("5800000000 B") && disk.includes("2100000000 B"),
    "the 507's two byte figures are the whole message and one of them is missing");
});

test("roiAlignNote says whether the grid came off the camera or out of a default", () => {
  assert(roiAlignNote([8, 2], true).includes("reports"),
    "a published grid was described as a default");
  assert(roiAlignNote([8, 2], false).includes("default"),
    "a defaulted grid was described as the camera's own");
});

// --------------------------------------------------------------- the list
test("a recording row reports the MEASURED rate and never a planned one", () => {
  const rec: VideoRecording = {
    id: "2026-09-10T2214-ser01", ts: 1_757_500_000, bytes: 15_000_000,
    frames: 1000, fps: 28.4, roi: { x: 0, y: 0, w: 640, h: 480, bin: 1 },
    camera: "ASI662MC", has_stack: false,
  };
  const line = recordingLine(rec, (n) => `${n} B`);
  assert(/28.4 fps measured/.test(line),
    "the row's rate is not labelled measured - it is the achieved rate from the sidecar");
  assert(/1,000 frames/.test(line), "the frame count is not grouped");
  assert(/640 x 480/.test(line), "the subframe is missing from the row");
  // fps 0 means "unknown", so it must not be printed as a rate of zero.
  const unknown = recordingLine({ ...rec, fps: 0 }, (n) => `${n} B`);
  assert(!/fps/.test(unknown), "an unknown rate was printed as 0 fps");
});

test("laneBusy tells the two video lanes apart and survives an old server", () => {
  const st = (lanes?: string[]): RigStatus => ({ busy_lanes: lanes } as unknown as RigStatus);
  assert(laneBusy(st(["video"]), "video"), "the recording lane was not seen");
  assert(!laneBusy(st(["video_stack"]), "video"),
    "a stack read as the camera being busy - it touches no device at all");
  assert(laneBusy(st(["video_stack"]), "video_stack"), "the stack lane was not seen");
  assert(!laneBusy(st(undefined), "video"),
    "a server too old to publish busy_lanes blocked the control forever");
  assert(!laneBusy(null, "video"), "no status read as busy");
});

const total = passed + failed;
console.log(`videoModel.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export default { passed, failed, total };
export { passed, failed, total };
