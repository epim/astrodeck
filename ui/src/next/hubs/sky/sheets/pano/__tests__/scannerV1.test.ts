// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T30: the panorama scanner v1 on synthetic input, driven by hand (SPEC-v2 7.2, the scanner v1 row): the watchdog cue for an image
// change under a still pose, a camera stall through `tick`, the stale cue, portrait suspension, and tau applied after estimation.
// The closure states in the status and the report, the S42 gyro closure at Finish and the S46 rule that the latency estimator is
// fed from gyro rates only are scannerV1Ring.test.ts's, which plays whole rings and has the time of them. The gate proof of 7.6 is
// not a test: it is the replay and `python -m sim score` of every case, and its tables are in the T30 report.
//
// The scanner runs in the replay's browser (`__sim__/harness.ts`) over a `CameraSourceLike` stub, with the real PoseTrack, tracker,
// raster and tracer, driven sensor event by sensor event as scannerV0.test.ts does (the rig is synth/scannerRig.ts).
//
// Mutants this file must catch (each against the case named; applied from a byte backup of scanner.ts and run under the normal
// command):
//   * `staleInLastSecond` not wired into cueFor's input (`computeStatus` passing 0): 'the stale cue' finds the status still saying
//     what it said while the tracker has refused five frames in a second;
//   * the watchdog's pose test dropped (`stalled` from the image alone): 'the watchdog' finds a turning phone called stalled;
//   * tau read from the prior and not the estimate (`predictAt(t - priorMs)`): 'tau is applied only after estimation' finds the
//     live heading moved by the whole turn of the frame interval and none of the change of tau.
import assert from 'node:assert/strict';
import type { ScanStatus } from '../types';
import { closeHand, openHand, runHand, tinyPicture } from './synth/scannerRig';

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }

// ---- 1. The watchdog (3.5 step 10, 2.6) -------------------------------------------------------------------------------------

await test('the watchdog: an image that changes under a pose that did not is the stalled-sensor cue, and a turn explains the change', async () => {
  // The compass is constant, so the live pose does not move. The tiny readback is read once a second (WATCH_EVERY_MS): the same
  // picture twice is nothing, a different one (ZNCC under 0.6) under a pose that moved under 0.5 degrees is a motion sensor that
  // has stalled, and the cue says so until the next second's pair agrees again.
  const h = await openHand();
  try {
    const cueAt = new Map<number, ScanStatus['cueKey']>();
    h.camera.tiny = tinyPicture(1);
    runHand(h, 4200, { compass: () => 200, after: t => {
      if (t >= 2000) h.camera.tiny = tinyPicture(2);
      cueAt.set(t, h.scanner.status.cueKey);
    } });
    assert.ok(h.begunAt <= 200, `the scan began at ${h.begunAt}`);
    const during = [...cueAt].filter(([t]) => t >= 2200 && t < 2900).map(([, c]) => c);
    assert.ok(during.length > 30 && during.every(c => c === 'stalled-sensor'), `during the change: ${[...new Set(during)].join(',')}`);
    const before = [...cueAt].filter(([t]) => t >= 400 && t < 1900).map(([, c]) => c);
    assert.ok(before.every(c => c !== 'stalled-sensor'), 'an unchanged image is not a stalled sensor');
    const after = [...cueAt].filter(([t]) => t >= 3200).map(([, c]) => c);
    assert.ok(after.every(c => c !== 'stalled-sensor'), `the next pair agrees, and the cue goes: ${[...new Set(after)].join(',')}`);
  } finally { closeHand(h); }

  // The same change under a pose that turns 30 degrees a second is a phone being turned, and no cue.
  const g = await openHand();
  try {
    const cues: string[] = [];
    g.camera.tiny = tinyPicture(1);
    runHand(g, 4200, { compass: t => 200 + 0.03 * t, after: t => {
      if (t >= 1000 && t % 1000 < 16) g.camera.tiny = tinyPicture(1 + t / 1000);
      cues.push(g.scanner.status.cueKey);
    } });
    assert.ok(!cues.includes('stalled-sensor'), `a turning phone is not stalled: ${[...new Set(cues)].join(',')}`);
  } finally { closeHand(g); }
});

// ---- 2. The stalled camera through tick (3.5, 2.6) --------------------------------------------------------------------------

await test('a camera that stops delivering frames reads stalled through tick, and a frame clears it', async () => {
  const h = await openHand();
  try {
    runHand(h, 1200, { compass: () => 200 });
    const last = h.camera.lastFrameAt as number;
    assert.ok(Number.isFinite(last) && h.scanner.status.cueKey !== 'stalled-camera');
    h.scanner.tick(last + 1400);
    assert.notEqual(h.scanner.status.cueKey, 'stalled-camera', 'under 1500 ms is a gap, not a stall');
    h.scanner.tick(last + 1600);
    assert.equal(h.scanner.status.cueKey, 'stalled-camera');
    assert.equal(h.scanner.status.cueKind, 'warn');
    h.t = Math.ceil((last + 1700) / 64) * 64;
    runHand(h, 128, { compass: () => 200 });
    assert.notEqual(h.scanner.status.cueKey, 'stalled-camera', 'the next frame clears it');
  } finally { closeHand(h); }
});

// ---- 3. The stale cue (2.6, issue 1031) -------------------------------------------------------------------------------------

await test('the stale cue: five refused frames in a second after the phone has turned, and never on a phone that has not', async () => {
  // The phone turns at 20 degrees a second (the relative stream, so the pose is a relative one), then every sensor goes silent
  // while the camera keeps delivering: the predictor refuses each frame (STALE_LIMIT), the tracker counts them, and the cue says
  // the pose is lagging. The count is `staleInLastSecond`, handed to cueFor by the status.
  const h = await openHand();
  try {
    let yaw = 0, spoke = true;
    const cues: string[] = [];
    let staleAt = NaN;
    for (; h.t < 6000; h.t += 16) {
      const t = h.t;
      h.harness.setClock(t);
      if (spoke) {
        if (t >= 2000) yaw = 20 * (t - 2000) / 1000;
        h.harness.dispatchOrientation({ timeStamp: t, alpha: 300 - yaw, beta: 70, gamma: 0, absolute: false, event: 'deviceorientation' });
        h.harness.dispatchOrientation({ timeStamp: t, alpha: 300 - yaw, beta: 70, gamma: 0, absolute: true, event: 'deviceorientationabsolute' });
        h.harness.dispatchMotion({ timeStamp: t, rate: { alpha: 0, beta: 0, gamma: 0 } });
      }
      if (t % 33 < 16) h.camera.deliver(t);
      if (Number.isNaN(h.begunAt) && h.scanner.canBegin) { h.scanner.begin(); h.begunAt = t; }
      if (t === 4000) spoke = false;
      cues.push(h.scanner.status.cueKey);
      if (t > 4000 && Number.isNaN(staleAt) && h.scanner.status.cueKey === 'stale') staleAt = t;
    }
    assert.ok(Number.isFinite(staleAt), `the cue never said stale: ${[...new Set(cues)].join(',')}`);
    assert.ok(staleAt - 4000 < 800, `stale after ${staleAt - 4000} ms of silence`);
    const ins = h.scanner.inspect();
    assert.ok(ins.tracker.staleInLastSecond >= 5, `${ins.tracker.staleInLastSecond} refusals in the last second`);
    assert.ok(h.scanner.status.cueKind === 'warn');
    const early = cues.slice(0, Math.floor(4000 / 16));
    assert.ok(!early.includes('stale'), 'a phone whose sensors are speaking is never stale');
  } finally { closeHand(h); }

  // A phone that has not turned since Begin has nothing to lag behind: five refusals are silence, not a lagging pose (1031).
  const s = await openHand();
  try {
    const cues: string[] = [];
    for (; s.t < 5000; s.t += 16) {
      const t = s.t;
      s.harness.setClock(t);
      if (t < 2000) {
        s.harness.dispatchOrientation({ timeStamp: t, alpha: 300, beta: 70, gamma: 0, absolute: true, event: 'deviceorientationabsolute' });
        s.harness.dispatchMotion({ timeStamp: t, rate: { alpha: 0, beta: 0, gamma: 0 } });
      }
      if (t % 33 < 16) s.camera.deliver(t);
      if (Number.isNaN(s.begunAt) && s.scanner.canBegin) { s.scanner.begin(); s.begunAt = t; }
      cues.push(s.scanner.status.cueKey);
    }
    assert.ok(!cues.includes('stale'), `never turned, never lagging: ${[...new Set(cues)].join(',')}`);
  } finally { closeHand(s); }
});

// ---- 4. Portrait suspension (2.6, D-ruling: a rotation suspends keyframes) --------------------------------------------------

await test('a phone on its side suspends keyframes and says so; turned upright it carries on where it was', async () => {
  const h = await openHand();
  try {
    const compass = (t: number) => 200 + 0.02 * t;   // 20 degrees a second, the whole time
    const count = () => h.scanner.inspect().tracker.keyframes.length;
    runHand(h, 3000, { compass });
    const upright = count();
    assert.ok(upright >= 2, `${upright} keyframes while upright`);
    runHand(h, 2000, { compass, landscape: true });
    assert.equal(h.scanner.status.portrait, false);
    assert.equal(h.scanner.status.cueKey, 'portrait');
    assert.equal(h.scanner.status.cueKind, 'block');
    assert.equal(count(), upright, 'no keyframe is taken on its side');
    assert.ok(h.scanner.inspect().tracker.log.some(r => r.outcome === 'inactive'), 'the log says the tracker was inactive');
    runHand(h, 2000, { compass });
    assert.equal(h.scanner.status.portrait, true);
    assert.notEqual(h.scanner.status.cueKey, 'portrait');
    assert.ok(count() > upright, `${count()} keyframes after it is upright again`);
  } finally { closeHand(h); }
});

// ---- 5. Tau (4.5) -----------------------------------------------------------------------------------------------------------

await test('tau is applied only after estimation, and the frame path predicts at t - tau (4.5)', async () => {
  // A phone turning 30 degrees a second under a compass that reads it exactly (absolute-only, so G is the compass). The live
  // heading of a frame at t is G(t - tau). The prior is 50 ms and stands until the estimator has 10 pairs at under 15 ms of
  // sigma; then the frame path predicts at t - tau of the estimate, and the heading, at the same frame time, is behind by
  // 30 deg/s times the change of tau (alpha grows as the heading falls).
  const h = await openHand();
  try {
    const compass = (t: number) => 200 + 0.03 * t;
    const lat = h.scanner.inspect().track.latency;
    const headingAt = (t: number): number => {
      h.t = t;
      runHand(h, 1, { compass });
      return h.scanner.status.headingDeg as number;
    };
    runHand(h, 3000, { compass });
    assert.equal(lat.tauMs, 50, 'the prior');
    assert.equal(lat.applied, false);
    const frameT = Math.ceil(3000 / 64) * 64;
    const before = headingAt(frameT);
    // Nine pairs are not enough, whatever they say.
    const pairs = [3, -4, 5, -6, 7, -8, 4, -5, 6, -7, 8, -3];
    for (const d of pairs.slice(0, 9)) lat.addPair((50 - 100) * d / 1000, d, 50);
    assert.equal(lat.applied, false, 'under 10 pairs');
    assert.equal(lat.tauMs, 50);
    for (const d of pairs.slice(9)) lat.addPair((50 - 100) * d / 1000, d, 50);
    assert.equal(lat.applied, true, 'applied at the tenth pair');
    assert.ok(Math.abs(lat.tauMs - 100) < 1e-6, `tau ${lat.tauMs}`);
    const after = headingAt(frameT + 64);
    // Alpha grows as the heading falls: 64 ms of turn is 1.92 degrees back, less the 50 ms the prediction now looks further back
    // (1.5 degrees the other way).
    assert.ok(Math.abs((after - before) + (0.03 * 64 - 0.03 * 50)) < 0.3,
      `the heading moved ${(after - before).toFixed(2)} degrees (expected -0.42; -1.92 if tau were ignored)`);
    assert.equal(h.scanner.report().sensors.latency.tauMs, lat.tauMs);
    assert.equal(h.scanner.report().sensors.latency.applied, true);
  } finally { closeHand(h); }
});

console.log(`scannerV1.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
