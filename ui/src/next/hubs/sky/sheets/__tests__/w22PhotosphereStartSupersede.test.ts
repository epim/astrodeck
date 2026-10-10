// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w22PhotosphereStartSupersede.test.ts - WP-208 / #1003: a `video.play()` that
// rejects after a newer `PhotosphereSweep.start()` (or a `stop()`) replaced the
// call that issued it leaves the newer call alone.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/w22PhotosphereStartSupersede.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. Every await in `start()` re-checks `generation !== this.generation`
// and returns quietly when something newer has taken over. The `play()` catch
// did not. A second `start()` on the same sweep bumps the generation (its first
// act is `this.stop()`) and later assigns `video.srcObject`, which makes the
// first call's pending `play()` reject (AbortError: "The play() request was
// interrupted by a new load request"). The first call's catch then ran
// `this.stop()` - which stops the SECOND call's stream and bumps the generation
// again, so the second call returned without setting `ready` - and threw "the
// camera opened but its preview could not play" at the first call's caller.
// Intermittent on a device (it needs the browser to reject the pending promise
// at the wrong moment), so these cases reject the promise BY HAND, where the
// test says, rather than repeating taps and hoping.
//
// THE FAKE. A video element whose `play()` hands back a promise the test
// settles, and whose `srcObject` is a plain property (jsdom implements none),
// so "the browser rejected the first play() after the second start assigned
// srcObject" is two ordinary statements in the order the issue describes. The
// camera is a stream per getUserMedia call whose track records its stop, so
// "stopped the newer start's camera" is a fact the test reads off the track.
//
// THE SHARED ELEMENT. `stop()` used to null `video.srcObject` whatever it held.
// The sheet keeps one <video> for every scan, so a stop() on a sweep that no
// longer owns the element - the caller's own, on return from a start() it was
// superseded in - blanked the picture of the sweep that does. `stop()` now
// clears the element only while it still shows this sweep's stream. Case 3 holds
// that, with the caller's stop() in it;
// w22PhotosphereSharedVideoDom.test.tsx holds it through the real sheet.
//
// WHAT EACH CASE HOLDS THE SWEEP TO:
//   1. the issue's case: first play() rejected after a second start() on the
//      same sweep. The first call returns without throwing, the second call's
//      camera is live and not stopped, and the preview is ready.
//   2. the same, through a STOP: a play() rejected after the editor closed the
//      sweep does not throw a "could not play" error over a closed editor, and
//      the camera is stopped once.
//   3. two sweeps on one shared video element, as horizon.tsx builds them (a NEW
//      sweep per open, the old one stopped): the old sweep's play() rejects late,
//      after the new sweep took the element. Its start() returns quietly, and
//      then the case does what the caller does on return from a start() it no
//      longer owns - `sweep.stop()` - which must not null `video.srcObject`
//      under the newer sweep's picture. That step is in the case because it is
//      the one that blanks the picture: a case that stops at the start() promise
//      grades the sweep alone and passes on the old stop().
//   4. control: a play() that rejects with nothing newer in the way is still a
//      failed preview - the camera is released and the error says so.
//   5. a motion-permission verdict that arrives after a newer start() began is
//      dropped: the superseded start() must not write "Motion access was
//      denied" onto a sweep whose own prompt was granted.
//
// Named mutants (edits to photosphere.ts, run from a byte backup and restored
// byte-identically). The first failing assertion of each case is quoted:
//   w22m5_catch_unguarded -- the `if (generation !== this.generation) return;`
//     line in the `play()` catch deleted. 2/5 pass: cases 1, 2 and 3 fail, the
//     control and case 5 stay green. "x a play() rejected after a second start()
//     leaves the newer start's camera alone: the superseded start() stopped the
//     NEWER start's camera".
//   w22m6_stop_clears_unconditionally -- stop() back to
//     `if (this.video) this.video.srcObject = null;`. 4/5 pass: only case 3
//     fails. "x a stopped sweep's stale play() rejection does not blank the newer
//     sweep's picture: the stopped sweep's late stop() cleared video.srcObject
//     under the new sweep's picture".
//   w22m7_motion_verdict_before_check -- the motion verdict written before the
//     generation check again. 4/5 pass: only case 5 fails. "x a motion verdict
//     that arrives after a newer start() began is not written onto it: the
//     superseded start()'s denial was written onto the newer start".

import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<html><body></body></html>", { url: "https://localhost/", pretendToBeVisual: true });
const w = dom.window as any;
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLVideoElement", "HTMLCanvasElement",
  "Element", "Node", "Event", "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  Object.defineProperty(g, k, { value: k === "window" ? w : w[k], writable: true, configurable: true });
}
w.DeviceOrientationEvent = class {};
Object.defineProperty(w, "isSecureContext", { value: true });
// The preview timer is not under test and must not tick in the background: a
// started sweep schedules one, and `stop()` clears it.
g.setInterval = () => 1;
g.clearInterval = () => undefined;

// ------------------------------------------------------------------- camera
type Track = { stops: number; stop(): void; getSettings(): { deviceId: string }; addEventListener(): void };
type Stream = { tracks: Track[]; getTracks(): Track[]; getVideoTracks(): Track[] };
const streams: Stream[] = [];
function makeStream(): Stream {
  const track: Track = {
    stops: 0, stop() { this.stops++; }, getSettings: () => ({ deviceId: "cam" }), addEventListener() {},
  };
  const stream: Stream = { tracks: [track], getTracks: () => [track], getVideoTracks: () => [track] };
  streams.push(stream);
  return stream;
}
Object.defineProperty(w.navigator, "mediaDevices", { value: {
  enumerateDevices: async () => [{ kind: "videoinput", deviceId: "cam", label: "Back camera" }],
  getUserMedia: async () => makeStream(),
}, configurable: true });

type Play = { resolve(): void; reject(e: unknown): void };
type FakeVideo = HTMLVideoElement & { plays: Play[] };
/** A video element whose play() promises are the test's to settle. */
function makeVideo(): FakeVideo {
  const video = w.document.createElement("video") as FakeVideo;
  video.plays = [];
  (video as any).play = () => new Promise<void>((resolve, reject) => { video.plays.push({ resolve, reject }); });
  return video;
}
const aborted = () => new w.DOMException("The play() request was interrupted by a new load request.", "AbortError");

const { PhotosphereSweep } = await import("../photosphere");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
/** Let the sweep's microtask chain and any queued task run. */
const flush = () => new Promise<void>((r) => setTimeout(r, 0));
async function until(cond: () => boolean, what: string): Promise<void> {
  for (let i = 0; i < 200; i++) { if (cond()) return; await flush(); }
  throw new Error(`never happened: ${what}`);
}
/** How a start() ended, without letting a throw escape the test. */
type Outcome = { threw: false } | { threw: true; message: string };
const outcome = (p: Promise<void>): Promise<Outcome> =>
  p.then(() => ({ threw: false } as Outcome), (e: unknown) => ({ threw: true, message: (e as Error).message }));

// ============================================ 1. a second start() on one sweep
await test("a play() rejected after a second start() leaves the newer start's camera alone", async () => {
  streams.length = 0;
  const sweep = new PhotosphereSweep();
  const video = makeVideo();
  const canvas = w.document.createElement("canvas");

  const first = outcome(sweep.start(video, canvas));
  await until(() => video.plays.length === 1, "the first start() reached play()");
  const second = outcome(sweep.start(video, canvas));
  await until(() => video.plays.length === 2, "the second start() reached play()");
  assert.equal(streams.length, 2, "each start() opened its own camera");
  assert.equal(streams[0].tracks[0].stops, 1, "the second start() releases the first start's camera");

  // The browser, having had `video.srcObject` reassigned by the second call,
  // rejects the first call's pending play(). The second call is still waiting
  // on its own.
  video.plays[0].reject(aborted());
  await flush();
  video.plays[1].resolve();

  const firstOutcome = await first;
  const secondOutcome = await second;
  // The harm first (the newer camera), then what the caller was told.
  assert.equal(streams[1].tracks[0].stops, 0,
    "the superseded start() stopped the NEWER start's camera");
  assert.equal(sweep.previewReady, true, "the newer start() never became ready");
  assert.equal((video as any).srcObject, streams[1], "the video is no longer showing the newer start's camera");
  assert.deepEqual(secondOutcome, { threw: false }, `the newer start() failed: ${JSON.stringify(secondOutcome)}`);
  assert.deepEqual(firstOutcome, { threw: false },
    `the superseded start() reported a failed preview to its caller: ${JSON.stringify(firstOutcome)}`);
  sweep.stop();
  assert.equal(streams[1].tracks[0].stops, 1, "an explicit stop() releases the camera exactly once");
});

// ================================================= 2. a stop() while it pends
await test("a play() rejected after stop() does not throw 'could not play' over a closed editor", async () => {
  streams.length = 0;
  const sweep = new PhotosphereSweep();
  const video = makeVideo();
  const started = outcome(sweep.start(video, w.document.createElement("canvas")));
  await until(() => video.plays.length === 1, "start() reached play()");

  sweep.stop();
  assert.equal(streams[0].tracks[0].stops, 1, "stop() releases the camera");
  video.plays[0].reject(aborted());

  const result = await started;
  assert.deepEqual(result, { threw: false },
    `a start() that was stopped reported a failed preview afterwards: ${JSON.stringify(result)}`);
  assert.equal(sweep.previewReady, false, "a stopped sweep must not read as ready");
  assert.equal(streams[0].tracks[0].stops, 1, "the camera was released more than once");
});

// ============================ 3. two sweeps, one video element (the caller's shape)
await test("a stopped sweep's stale play() rejection does not blank the newer sweep's picture", async () => {
  streams.length = 0;
  const video = makeVideo();
  const canvas = w.document.createElement("canvas");
  const old = new PhotosphereSweep();
  const oldStart = outcome(old.start(video, canvas));
  await until(() => video.plays.length === 1, "the old sweep reached play()");
  old.stop();

  const next = new PhotosphereSweep();
  const nextStart = outcome(next.start(video, canvas));
  await until(() => video.plays.length === 2, "the new sweep reached play()");
  assert.equal((video as any).srcObject, streams[1], "the new sweep did not take the element");

  // The old sweep's rejection arrives late, after the new sweep owns the element.
  video.plays[0].reject(aborted());
  await flush();
  video.plays[1].resolve();

  const oldResult = await oldStart;
  const nextResult = await nextStart;
  // horizon.tsx startCapture, back from a start() it no longer owns:
  //   if (sweepRef.current !== sweep) { sweep.stop(); return; }
  // (a start() that THREW goes to the caller's catch instead, which does not
  // stop). Left out, this case grades the sweep alone, and the stop() the caller
  // runs here is the one that blanked the picture.
  if (!oldResult.threw) old.stop();
  assert.equal((video as any).srcObject, streams[1],
    "the stopped sweep's late stop() cleared video.srcObject under the new sweep's picture");
  assert.equal(next.previewReady, true, "the new sweep is not ready");
  assert.equal(streams[1].tracks[0].stops, 0, "the new sweep's camera was stopped");
  assert.equal(streams[0].tracks[0].stops, 1, "the old sweep's camera was not released exactly once");
  assert.deepEqual(nextResult, { threw: false }, `the new sweep threw: ${JSON.stringify(nextResult)}`);
  assert.deepEqual(oldResult, { threw: false }, `the stopped sweep threw: ${JSON.stringify(oldResult)}`);
  next.stop();
});

// ================================================= 4. control: a real failure
await test("control: a play() that fails with nothing newer in the way releases the camera and says so", async () => {
  streams.length = 0;
  const sweep = new PhotosphereSweep();
  const video = makeVideo();
  const started = outcome(sweep.start(video, w.document.createElement("canvas")));
  await until(() => video.plays.length === 1, "start() reached play()");
  video.plays[0].reject(new w.DOMException("The play method is not allowed", "NotAllowedError"));

  const result = await started;
  assert.equal(result.threw, true, "a failed preview did not reach the caller");
  assert.match((result as { message: string }).message, /preview could not play/, "the error does not say what failed");
  assert.equal(streams[0].tracks[0].stops, 1, "the camera was left open after a failed preview");
  assert.equal(sweep.previewReady, false, "a failed preview must not read as ready");
});

// ================================== 5. a motion verdict that outlives its start()
await test("a motion verdict that arrives after a newer start() began is not written onto it", async () => {
  streams.length = 0;
  const asks: Array<(answer: string) => void> = [];
  w.DeviceOrientationEvent.requestPermission = () => new Promise<string>((resolve) => { asks.push(resolve); });
  try {
    const sweep = new PhotosphereSweep();
    const video = makeVideo();
    const canvas = w.document.createElement("canvas");

    const first = outcome(sweep.start(video, canvas));
    await until(() => video.plays.length === 1, "the first start() reached play()");
    video.plays[0].resolve();
    // Past play() the first start() has nothing left to wait for but the prompt.
    await until(() => sweep.previewReady, "the first start() got past play()");

    const second = outcome(sweep.start(video, canvas));
    await until(() => video.plays.length === 2, "the second start() reached play()");
    assert.equal(asks.length, 2, "each start() asked for motion access");

    // The first prompt is answered "denied" after the second start() began.
    asks[0]("denied");
    await flush();
    assert.doesNotMatch(sweep.error ?? "", /Motion access was denied/,
      "the superseded start()'s denial was written onto the newer start");

    asks[1]("granted");
    video.plays[1].resolve();
    assert.deepEqual(await first, { threw: false }, "the superseded start() threw");
    assert.deepEqual(await second, { threw: false }, "the newer start() threw");
    assert.equal(sweep.previewReady, true, "the newer start() never became ready");
    assert.doesNotMatch(sweep.error ?? "", /Motion access was denied/,
      "the newer start() was granted motion access and reads as denied");
    sweep.stop();
  } finally { delete w.DeviceOrientationEvent.requestPermission; }
});

console.log(`w22PhotosphereStartSupersede: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
