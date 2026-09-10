// focusCapture.test.ts — the Focus screen must be able to take a frame, and the
// sweep must never pretend it copied one that does not exist.
//
// Run with:  npx tsx src/lib/__tests__/focusCapture.test.ts   (from ui/)
import {
  FOCUS_EXPOSURE_PRESETS, FRAME_READOUT_GRACE_MS, focusCaptureBlocker,
  focusCaptureBody, frameWaitNote, presetAction, sweepPreviewNote, sweepReadiness,
} from "../focusCapture";
import { deriveAutofocusParams } from "../autofocus";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void) { try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = "") { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function ok(c: boolean, m: string) { if (!c) throw new Error(m); }

// ------------------------------------------------------------------ presets
test("the presets are the five the user asked for at the telescope", () => {
  eq(FOCUS_EXPOSURE_PRESETS.join(","), "1,2,3,5,10");
});

// --------------------------------------------------------------- the request
test("a focus frame is never written to disk — the loop could not save it anyway", () => {
  // /api/capture/loop drops `save`: hub.start_loop has no save parameter. A
  // save toggle here would be lit for something the server never does.
  const b = focusCaptureBody({ exposureS: 4, gain: 220, binning: 1 });
  eq(b.save, false);
  eq(b.exposure_s, 4);
  eq(b.gain, 220);
  eq(b.binning, 1);
  eq(b.offset, 30, "offset defaults rather than posting undefined");
});

test("the body is integral where the server expects integers", () => {
  const b = focusCaptureBody({ exposureS: 2.5, gain: 219.6, binning: 2.2, offset: 30.4 });
  eq(b.gain, 220);
  eq(b.binning, 2);
  eq(b.offset, 30);
  eq(b.exposure_s, 2.5, "exposure stays fractional — 2.5s is a real exposure");
});

// ------------------------------------------------------------------ blockers
const blocker = (over: Partial<Parameters<typeof focusCaptureBlocker>[0]> = {}) =>
  focusCaptureBlocker({
    readOnlyReason: null, hasCamera: true, polarBusy: false,
    sequenceOwnsCamera: false, autofocusRunning: false,
    exposureInvalid: false, gainInvalid: false, gainMax: 570, ...over,
  });

test("a ready rig is not blocked", () => {
  eq(blocker(), null);
});

test("every blocked capture states its own reason rather than sitting greyed out", () => {
  for (const [label, over] of [
    ["read-only", { readOnlyReason: "Read-only session — operator access required" }],
    ["no camera", { hasCamera: false }],
    ["polar", { polarBusy: true }],
    ["sequence", { sequenceOwnsCamera: true }],
    ["autofocus", { autofocusRunning: true }],
    ["bad exposure", { exposureInvalid: true }],
    ["bad gain", { gainInvalid: true }],
  ] as const) {
    const r = blocker(over as object);
    ok(typeof r === "string" && r.length > 0, `${label} must give a reason`);
  }
});

test("the gain complaint names the ceiling instead of saying 'fix it'", () => {
  ok(blocker({ gainInvalid: true })!.includes("570"), "must name the camera's max");
  ok(!blocker({ gainInvalid: true, gainMax: null })!.includes("570"),
     "with no known ceiling it must not invent one");
});

test("a running sweep owns the camera — the reason says so, not just 'busy'", () => {
  ok(/autofocus/i.test(blocker({ autofocusRunning: true })!), "must name the owner");
});

// ---------------------------------------------------------------- preset tap
test("changing the preset mid-loop restarts the loop — a highlight is not an effect", () => {
  // hub.start_loop closes over the exposure it was given, so a running loop
  // keeps the OLD one forever. A preset that only moved a highlight would be a
  // control that looks applied and is ignored.
  const a = presetAction(true, 5);
  eq(a.restartLoop, true);
  ok(a.hint.includes("5"), `the hint must name the new exposure: ${a.hint}`);
  ok(/restart/i.test(a.hint), `and must say the loop restarts: ${a.hint}`);
  eq(presetAction(false, 5).restartLoop, false);
});

// ------------------------------------------------------ what the sweep uses
const PARAMS = { exposure_s: 4, gain: 220, binning: 1, step: 350 };
const ready = (over: Partial<Parameters<typeof sweepReadiness>[0]> = {}) =>
  sweepReadiness({
    manual: false, hasLiveFrame: true, liveStars: 2100, captureBlocked: null,
    params: PARAMS, source: "measured", ...over,
  });

test("with a measured frame the sweep says exactly what it will use, and starts", () => {
  const r = ready();
  eq(r.block, null, "a measured sweep must not be blocked");
  eq(r.basis, "measured");
  ok(r.summary.includes("4s") && r.summary.includes("220") && r.summary.includes("bin 1"),
     `the summary must carry the numbers: ${r.summary}`);
  ok(r.provenance.includes("2100"), `and where they came from: ${r.provenance}`);
  eq(r.warn, null);
});

test("with NO frame at all the sweep refuses — the 2026-07-31 failure", () => {
  // Two four-minute runs swept at 2s/gain 120/bin 2 because the preview had
  // never taken a frame, and nothing on screen said the numbers were invented.
  const r = ready({ hasLiveFrame: false, liveStars: null, source: "no-frame" });
  ok(r.block != null, "must refuse rather than sweep blind");
  ok(/frame/i.test(r.block!), `the refusal must name what is missing: ${r.block}`);
  ok(/preset|single/i.test(r.block!), `and the one-tap fix: ${r.block}`);
  // The blind numbers themselves ride on the summary line beside the refusal,
  // so they are on screen exactly once.
  ok(r.summary.includes("4s") && r.summary.includes("gain 220"), r.summary);
  ok(/nothing measured/i.test(r.provenance), r.provenance);
});

test("the refusal is not a dead end: it names a control on this same screen", () => {
  // Refusing was only defensible once the camera controls existed here. If the
  // sentence stops naming them, the refusal becomes the trap it replaced.
  const r = ready({ hasLiveFrame: false, source: "no-frame" });
  ok(/tap a preset/i.test(r.block!), `must point at the presets: ${r.block}`);
});

test("the refusal defers to the reason the frame cannot be taken, and stops naming Single", () => {
  // The reviewed defect: with no camera connected the hero said the sweep was
  // blocked because no frame had been taken and told the user to tap Single —
  // while Single, two inches above, read "No camera is connected". A blocked
  // control resolving to a reason that is not merely incomplete but WRONG, and
  // pointing at another blocked control.
  // Sourced from the real function rather than a hardcoded copy, so a future
  // reword of the sentence cannot leave this test asserting stale text.
  const noCam = blocker({ hasCamera: false })!;
  const r = ready({ hasLiveFrame: false, liveStars: null, source: "no-frame",
                    captureBlocked: noCam });
  ok(r.block != null, "still refuses");
  ok(r.block!.includes(noCam), `must state the real cause: ${r.block}`);
  ok(!/tap a preset|tap single/i.test(r.block!),
     `must not send the user to a control that is itself locked: ${r.block}`);
});

test("with the camera free the refusal still names the one-tap fix", () => {
  // The deferral must not swallow the ordinary case: when Single WOULD work,
  // "tap a preset, then Single" is the whole justification for refusing at all.
  const r = ready({ hasLiveFrame: false, liveStars: null, source: "no-frame",
                    captureBlocked: null });
  ok(/tap a preset/i.test(r.block!), `${r.block}`);
});

test("a star-poor frame WARNS and proceeds — the server re-probes with better information", () => {
  // Mirrors focus/native.py's own split: MIN_STARS_TO_SWEEP refuses,
  // SPARSE_FIELD_WARN warns and continues. The client counted stars in a frame
  // taken at different settings from the ones the sweep will use.
  const r = ready({ liveStars: 3, source: "sparse" });
  eq(r.block, null, "doubtful is not hopeless — must not block a sweep that might work");
  eq(r.basis, "sparse");
  ok(r.warn != null && r.warn.includes("3"), `the warning must carry the count: ${r.warn}`);
  ok(/fewer/i.test(r.warn!), "must say defocusing finds fewer stars, not more");
  // It used to say "exposure and binning fell back to defaults". Binning does
  // NOT fall back — it is copied from any frame, star-poor or not — so that
  // sentence described a mechanism the code does not have.
  ok(/gain and binning/i.test(r.provenance) && /exposure/i.test(r.provenance),
     `the provenance must split what is copied from what falls back: ${r.provenance}`);
});

test("manual settings are never blocked, but an unmeasured rig is still called unmeasured", () => {
  const withFrame = ready({ manual: true });
  eq(withFrame.block, null);
  eq(withFrame.basis, "manual");
  eq(withFrame.warn, null);
  const blind = ready({ manual: true, hasLiveFrame: false, source: "no-frame" });
  eq(blind.block, null, "the user steering explicitly is never refused");
  ok(blind.warn != null, "but must still be told nothing has been measured");
});

test("a backend sweep is never blocked and never claims our numbers were used", () => {
  // focus/autofocus.py routes kind=="backend" to the backend's OWN autofocus and
  // drops exposure/gain/binning on the way. Refusing there would block a sweep
  // that was never going to read them — and the settings panel has been lying
  // to NINA users the whole time.
  const r = ready({ hasLiveFrame: false, source: "no-frame", paramsSent: false });
  eq(r.block, null, "a backend sweep must not be refused for OUR missing frame");
  eq(r.basis, "backend");
  ok(/backend/i.test(r.summary), `must say who chooses: ${r.summary}`);
  ok(/not sent/i.test(r.provenance), `must say ours are not used: ${r.provenance}`);
  eq(r.warn, null, "no advice to give about numbers nobody reads");
});

test("the summary is the numbers actually posted, not a description of them", () => {
  const r = sweepReadiness({
    manual: false, hasLiveFrame: true, liveStars: 40, captureBlocked: null,
    params: { exposure_s: 2, gain: 120, binning: 2, step: 75 }, source: "measured",
  });
  ok(r.summary.includes("2s"), r.summary);
  ok(r.summary.includes("gain 120"), r.summary);
  ok(r.summary.includes("bin 2"), r.summary);
  ok(r.summary.includes("75"), r.summary);
});

// ------------------------------------------- the derivation feeding readiness
test("derive + readiness agree: no live frame is 'no-frame', not 'sparse'", () => {
  // The join between the two files. deriveAutofocusParams cannot see the
  // preview object, so if this drifts the hero silently unblocks itself.
  const d = deriveAutofocusParams({
    focuserMax: 40000, maxBin: 4, maxGain: 570,
    liveExposureS: null, liveGain: null, liveBinning: null, liveStars: null, liveHfr: null,
    hasLiveFrame: false,
  });
  eq(d.source, "no-frame");
  eq(d.exposure_s, 2, "the historical blind fallback");
  eq(d.gain, 120, "the gain that found 8 stars");
  ok(sweepReadiness({ manual: false, hasLiveFrame: false, liveStars: null,
                      captureBlocked: null, params: d, source: d.source }).block != null,
     "a no-frame derivation must block");
});

test("derive + readiness agree about BINNING: what the panel says was copied, was copied", () => {
  // The three sentences on this screen that claim the frame's binning is copied
  // (the Camera panel note, the refusal, and the measured provenance) are only
  // true if the derivation actually reads it. It did not.
  const d = deriveAutofocusParams({
    focuserMax: 40000, maxBin: 4, maxGain: 570,
    liveExposureS: 4, liveGain: 220, liveBinning: 1, liveStars: 2100, liveHfr: 2.4,
    hasLiveFrame: true,
  });
  eq(d.binning, 1, "the working configuration is bin 1, and the sweep must use it");
  const r = sweepReadiness({ manual: false, hasLiveFrame: true, liveStars: 2100,
                             captureBlocked: null, params: d, source: d.source });
  ok(r.summary.includes("bin 1"),
     `the summary the user reads must be the binning that goes out: ${r.summary}`);
  ok(/copied/i.test(r.provenance), r.provenance);
});

test("derive marks a star-poor frame 'sparse', and a good one 'measured'", () => {
  const sparse = deriveAutofocusParams({
    focuserMax: 40000, maxBin: 4, maxGain: 570,
    liveExposureS: 1, liveGain: 200, liveBinning: 1, liveStars: 2, liveHfr: 3.2,
    hasLiveFrame: true,
  });
  eq(sparse.source, "sparse");
  eq(sparse.gain, 200, "a chosen gain survives a frame whose star count does not");
  eq(sparse.binning, 1, "and so does a chosen binning");
  const good = deriveAutofocusParams({
    focuserMax: 40000, maxBin: 4, maxGain: 570,
    liveExposureS: 4, liveGain: 220, liveBinning: 1, liveStars: 2100, liveHfr: 2.4,
    hasLiveFrame: true,
  });
  eq(good.source, "measured");
  eq(good.exposure_s, 4);
  eq(good.gain, 220);
});

test("the basis strings say GUESS out loud when nothing was measured", () => {
  const d = deriveAutofocusParams({
    focuserMax: null, maxBin: 4, maxGain: null,
    liveExposureS: null, liveGain: null, liveBinning: null, liveStars: null, liveHfr: null,
    hasLiveFrame: false,
  });
  ok(/guess/i.test(d.basis.exposure), `exposure basis: ${d.basis.exposure}`);
  ok(/guess/i.test(d.basis.gain), `gain basis: ${d.basis.gain}`);
  ok(/guess/i.test(d.basis.binning), `binning basis: ${d.basis.binning}`);
});

// -------------------------------------------------------- a frame in flight
const T0 = 2_000_000;
test("pressing Single never looks identical to not pressing it", () => {
  // POST /api/capture returns before the shutter opens — the same shape of bug
  // the Go button had, on the control next to it.
  const n = frameWaitNote({ startedAt: T0, exposureS: 10, now: T0 + 1000 });
  ok(n != null, "must say something immediately");
  eq(n!.tone, "info");
  ok(n!.text.includes("9"), `must count down the exposure: ${n!.text}`);
});

test("past the exposure it says readout, not a stuck countdown", () => {
  const n = frameWaitNote({ startedAt: T0, exposureS: 4, now: T0 + 5000 });
  eq(n!.tone, "info");
  ok(/reading/i.test(n!.text), n!.text);
});

test("a frame that never arrives is called out with both numbers", () => {
  const n = frameWaitNote({
    startedAt: T0, exposureS: 4, now: T0 + 4000 + FRAME_READOUT_GRACE_MS + 1,
  });
  eq(n!.tone, "warn");
  ok(n!.text.includes("4s"), `must say what was asked: ${n!.text}`);
  ok(/\d+s/.test(n!.text), `must say how long it has been: ${n!.text}`);
});

test("nothing in flight says nothing", () => {
  eq(frameWaitNote({ startedAt: null, exposureS: 4, now: T0 }), null);
});

// ------------------------------------------------- the sweep's own frames
test("a sweep with no frames on screen says so instead of showing an empty stage", () => {
  // Observed: four minutes of continuous exposures under a preview that read
  // "No capture yet" the whole time.
  const n = sweepPreviewNote({ running: true, pointsMeasured: 3, framesSinceStart: 0 });
  ok(n != null && n.includes("3"), `must say how far it has got: ${n}`);
  ok(/v-curve/i.test(n!), "must point at the evidence that IS live");
});

test("the first exposure of a sweep is not reported as a failure to deliver", () => {
  const n = sweepPreviewNote({ running: true, pointsMeasured: 0, framesSinceStart: 0 });
  ok(n != null && /first/i.test(n), `must distinguish 'not yet' from 'never': ${n}`);
});

test("once the sweep's frames DO arrive the note gets out of the way", () => {
  eq(sweepPreviewNote({ running: true, pointsMeasured: 3, framesSinceStart: 2 }), null);
  eq(sweepPreviewNote({ running: false, pointsMeasured: 0, framesSinceStart: 0 }), null);
});

test("a capture loop's frames are not counted as the sweep's — they belong to the loop", () => {
  // /api/capture/loop refuses only for polar and sequences (api/app.py), NOT for
  // a running sweep, so a loop started from the Capture screen keeps publishing
  // previews right through autofocus. Silencing the note on those frames would
  // let someone else's pictures stand in as proof the sweep is delivering — the
  // exact substitution the note exists to prevent.
  const n = sweepPreviewNote({
    running: true, pointsMeasured: 3, framesSinceStart: 5, loopRunning: true,
  });
  ok(n != null, "must still speak while another source owns the stage");
  ok(/loop/i.test(n!), `and must name whose frames these are: ${n}`);
  ok(!/no frame .* has reached this screen/i.test(n!),
     `must not claim an empty stage the user can see is full: ${n}`);
  // Before the first point, same rule.
  const first = sweepPreviewNote({
    running: true, pointsMeasured: 0, framesSinceStart: 2, loopRunning: true,
  });
  ok(first != null && /loop/i.test(first), `${first}`);
  ok(!/nothing has come back/i.test(first!),
     "'nothing has come back yet' is false with loop frames landing");
  // A loop that is running but has published nothing since the sweep began is
  // not evidence of anything, so the plain note stands.
  const quiet = sweepPreviewNote({
    running: true, pointsMeasured: 3, framesSinceStart: 0, loopRunning: true,
  });
  ok(quiet != null && !/loop/i.test(quiet), `no loop frames -> no loop claim: ${quiet}`);
});

console.log(`focusCapture.test.ts: ${passed} passed, ${failed} failed`);
if (failed) { failures.forEach((f) => console.error(f)); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
