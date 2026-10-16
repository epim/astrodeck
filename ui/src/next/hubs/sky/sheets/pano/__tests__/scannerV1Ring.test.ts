// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T30: whole rings through the panorama scanner (SPEC-v2 7.2, the scanner v1 row): the closure states in the status, the report and
// the result, the S42 gyro closure at Finish, and the S46 rule that the latency estimator is fed from gyro rates only (issue 1030).
// Split from scannerV1.test.ts for the runner's per-file time: a ring is a second or two (synth/scannerRig.ts).
//
// Mutants this file must catch (each against the case named; applied from a byte backup and run under the normal command):
//   * the closure skipped (`closeByImage` not called from `tryClose`): 'the closure states' finds a ring still open at the seam;
//   * the S42 closure at Finish removed (`work()` not calling `tracker.closeAtFinish()`): 'a ring that stopped short of the seam'
//     finds the result open and `partial`;
//   * the latency feed reading chord rates (the `omegaGyro` test of `feedLatency` dropped, tracker.ts): 'the latency estimator is
//     fed from gyro rates only' finds a pair counted on a mapping that never confirmed.
import assert from 'node:assert/strict';
import { tracer } from '../horizon/horizonTrace';
import { BinState, ColState, PANO_W } from '../types';
import { ring } from './synth/scannerRig';

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>) { await fn(); passed++; console.log(`PASS ${name}`); }

await test('the closure states: open while turning, closed by image at the seam, in the status, the report and the result', async () => {
  const states: { frameNo: number; closed: boolean; method: string | null; cue: string; covered: number }[] = [];
  const r = await ring({ turnDeg: 385, confirmAxis: true, onFrame: (s, n) => {
    const st = s.status;
    if (n % 20 === 0) states.push({ frameNo: n, closed: st.loop.closed, method: st.loop.method, cue: st.cueKey, covered: st.coveredDeg });
  } });
  try {
    const first = states[3], last = states[states.length - 1];
    assert.equal(first.closed, false);
    assert.equal(first.method, null);
    assert.ok(['turning', 'gap', 'too-fast'].includes(first.cue), `early cue ${first.cue}`);
    assert.ok(states.findIndex(s => s.closed) > 5, 'the ring closed late, not at the start');
    assert.ok(states.every((s, i) => i === 0 || !states[i - 1].closed || s.closed), 'closed stays closed');
    const st = r.scanner.status;
    assert.equal(st.loop.closed, true);
    assert.equal(st.loop.method, 'image');
    assert.ok(st.loop.match && st.loop.match.early < st.loop.match.late, `match ${JSON.stringify(st.loop.match)}`);
    assert.ok(st.loop.postDeg !== null && st.loop.postDeg < 1e-3, `the newest keyframe sits on the match: ${st.loop.postDeg}`);
    assert.ok(st.loop.preDeg !== null && st.loop.preDeg.every(Number.isFinite));
    assert.equal(st.focal.state, 'closed');
    assert.ok(['done', 'done-tall'].includes(last.cue), `the cue at the end: ${last.cue}`);
    const rep = r.scanner.report();
    assert.deepEqual(rep.loop, st.loop, 'the report carries the loop as the status does');
    assert.equal(rep.focal.state, 'closed');
    assert.ok(rep.focal.closurePct !== null && Number.isFinite(rep.focal.closurePct), `closurePct ${rep.focal.closurePct}`);
    assert.ok(rep.focal.gyroScale !== null && Math.abs(rep.focal.gyroScale - 0.02) < 0.02, `gyroScale ${rep.focal.gyroScale} against the pan's 0.02`);
    assert.ok(rep.captureLog.some(c => c.detail === 'closure'), 'the capture log names the closing keyframe');
    const res = r.scanner.finish(null);
    assert.equal(res.partial, false);
    assert.equal(res.loop.method, 'image');
    assert.equal(r.scanner.report().loop.closed, true);
  } finally { r.scanner.stop(); r.harness.dispose(); }
});

await test('a ring that stopped short of the seam is closed by gyro at Finish and is not partial (S42)', async () => {
  // The pan turns 343 degrees (the chain reads 347 of them): the keyframes stop short of 350, so no commit can run a closure or
  // the over-limit rule, and the status says open. The live axis then turns the last 3 degrees to 350 (the test says so by
  // moving the tracker's live heading, which a phone does on its own in the last second of a scan); Finish closes the ring by
  // gyro, with no correction, and the focal is the lock's, never a closure's.
  const r = await ring({ turnDeg: 343, confirmAxis: true });
  try {
    const open = r.scanner.status.loop;
    assert.ok(Math.abs(open.unwrappedDeg) < 350 && !open.closed, `the keyframes turned ${open.unwrappedDeg}, closed ${open.closed}`);
    const tracker = r.scanner.inspect().tracker as unknown as { unwrapped: number; unwrappedAt: number[] };
    tracker.unwrapped = tracker.unwrappedAt[0] + 350.5;
    const res = r.scanner.finish(null);
    assert.equal(res.loop.closed, true, 'Finish closes a ring that turned a full circle');
    assert.equal(res.loop.method, 'gyro');
    assert.equal(res.partial, false);
    assert.equal(res.loop.match, null);
    assert.notEqual(res.focal.state, 'closed', 'a gyro closure measures no focal');
    assert.equal(r.scanner.report().focal.closurePct, null);
    assert.equal(r.scanner.report().focal.gyroScale, null);
  } finally { r.scanner.stop(); r.harness.dispose(); }

  // A scan that turned 120 degrees is partial, and Finish leaves it open.
  const p = await ring({ turnDeg: 120, confirmAxis: true });
  try {
    const res = p.scanner.finish(null);
    assert.equal(res.partial, true);
    assert.equal(res.loop.closed, false);
    assert.equal(res.loop.method, null);
  } finally { p.scanner.stop(); p.harness.dispose(); }
});

await test('the latency estimator is fed from gyro rates only: a chord rate feeds nothing (S46, issue 1030)', async () => {
  // A ring on a confirmed mapping gives the estimator its pair (the start of the turn, where the rate changes by more than
  // 2 deg/s between two aligned keyframes). The same ring on a mapping that has not confirmed (a steady pan never confirms it,
  // trackerAlign.test.ts) takes its rates from the orientation chord, whose pump-age jitter is shared with the residual r and
  // would be read as latency: none of it reaches the estimator.
  const c = await ring({ turnDeg: 120, confirmAxis: true });
  try {
    assert.ok(c.scanner.inspect().track.latency.pairs >= 1, 'a gyro rate feeds the estimator');
  } finally { c.scanner.stop(); c.harness.dispose(); }
  const u = await ring({ turnDeg: 120, confirmAxis: false });
  try {
    assert.equal(u.scanner.inspect().track.axis?.confirmed ?? false, false, 'the mapping never confirmed');
    const lat = u.scanner.inspect().track.latency;
    assert.equal(lat.pairs, 0, 'no pair from a chord rate');
    assert.equal(lat.applied, false);
    assert.equal(lat.tauMs, 50, 'the prior stands');
    assert.ok(u.scanner.inspect().tracker.keyframes.length > 15, 'the scan itself went on');
  } finally { u.scanner.stop(); u.harness.dispose(); }
});

await test('Finish guards a partial scan whose majority is an obstruction: a column the walk found nothing in is not published as open sky (S15 residue)', async () => {
  // 100 degrees scanned. The tracer is handed to Finish as the tracer of a scan whose ring sky is an obstruction: every column of
  // the observed arc is open to the ground and Measured (alt 0, no boundary, no contrast), as it reads a wall that fills the photo.
  // The scanner's guard makes those columns Tall: no bin of the arc is Measured and the arc is Tall. (The pure function is
  // scannerV1Tracer.test.ts's; this is the scanner calling it.)
  const w = await ring({ turnDeg: 100, confirmAxis: true });
  const real = tracer.extract;
  try {
    (tracer as { extract: typeof tracer.extract }).extract = (p, o) => {
      const h = real(p, o);
      for (let x = 0; x < PANO_W; x++) {
        if (h.state[x] === ColState.UnknownUnseen) continue;
        h.state[x] = ColState.Measured; h.alt[x] = 0; h.contrastSigma[x] = NaN; h.sigmaDeg[x] = 0.1;
      }
      return h;
    };
    const res = w.scanner.finish(null);
    assert.equal(res.partial, true);
    const states = new Map<number, number>();
    for (const s of res.draft.state) states.set(s, (states.get(s) ?? 0) + 1);
    assert.equal(states.get(BinState.Measured) ?? 0, 0, `no bin of the arc is Measured: ${JSON.stringify([...states])}`);
    assert.ok((states.get(BinState.Tall) ?? 0) >= 150, `the observed arc is Tall: ${JSON.stringify([...states])}`);
  } finally {
    (tracer as { extract: typeof tracer.extract }).extract = real;
    w.scanner.stop(); w.harness.dispose();
  }
});

console.log(`scannerV1Ring.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
