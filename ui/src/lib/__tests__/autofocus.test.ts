// autofocus.test.ts — normalizeAutofocusResult (terminal-only gate, defensive
// point/fit parsing) + filterNameFromStatus + afResultAgeLabel (F5: R2-FOC-01).
// Run with:  npx tsx src/lib/__tests__/autofocus.test.ts   (from ui/)

import {
  afResultAgeLabel, filterNameFromStatus, normalizeAutofocusResult,
  deriveAutofocusParams, plainFocusVerdict, focusButtonState, readFocusFailure,
  AF_DEFAULT_STEP, AF_STEP_MAX, AF_FALLBACK_BIN,
} from "../autofocus";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

const CTX = { provider: { kind: "astrodeck", label: "AstroDeck native" }, filter: "Luminance", tsMs: 1_700_000_000_000 };

test("normalizeAutofocusResult: 'running' and garbage states yield null (terminal-only gate)", () => {
  assert(normalizeAutofocusResult({ state: "running", points: [], best: null }, CTX) === null, "running -> null");
  assert(normalizeAutofocusResult({ state: "idle" }, CTX) === null, "unknown state -> null");
  assert(normalizeAutofocusResult(null, CTX) === null, "null raw -> null");
  assert(normalizeAutofocusResult("nope", CTX) === null, "non-object raw -> null");
});

test("normalizeAutofocusResult: 'done' carries points/best/fit + snapshots provider/filter/ts", () => {
  const r = normalizeAutofocusResult({
    state: "done",
    points: [{ position: 100, hfr: 3.2 }, { position: 450, hfr: 1.8, sigma: 0.1 }],
    best: { position: 450, hfr: 1.79 },
    fit: { method: "hyperbolic", r2: 0.987, curve: [[100, 3.2], [450, 1.79]] },
  }, CTX)!;
  assert(r.state === "done", "state");
  assert(r.points.length === 2, `points.length=${r.points.length}`);
  assert(r.points[1].sigma === 0.1, "sigma carried");
  assert(r.best?.position === 450 && r.best.hfr === 1.79, "best");
  assert(r.fit?.method === "hyperbolic" && r.fit.r2 === 0.987, "fit method/r2");
  assert(r.fit?.curve?.length === 2, "fit.curve");
  assert(r.message === null, "no message -> null");
  assert(r.provider?.label === "AstroDeck native", "provider snapshot");
  assert(r.filter === "Luminance", "filter snapshot");
  assert(r.ts === CTX.tsMs, "ts stamped from ctx, not Date.now()");
});

test("normalizeAutofocusResult: 'failed' carries message, tolerates absent best/fit", () => {
  const r = normalizeAutofocusResult({
    state: "failed", points: [{ position: 1, hfr: 2 }], best: null,
    message: "no V-curve minimum found (flat or inverted fit)",
  }, CTX)!;
  assert(r.state === "failed", "state");
  assert(r.best === null, "best null");
  assert(r.fit === null, "fit absent -> null");
  assert(r.message === "no V-curve minimum found (flat or inverted fit)", "message carried");
});

test("normalizeAutofocusResult: malformed points/best entries are dropped, not fabricated", () => {
  const r = normalizeAutofocusResult({
    state: "done",
    points: [{ position: 1, hfr: 2 }, { position: "bad", hfr: 2 }, { hfr: 2 }, null, "x"],
    best: { hfr: 2 }, // missing position -> best must be null, not {position:0,...}
  }, CTX)!;
  assert(r.points.length === 1, `only the one valid point kept, got ${r.points.length}`);
  assert(r.best === null, "best with no position -> null, no fabricated 0");
});

test("normalizeAutofocusResult: fit.curve drops malformed tuples but keeps valid ones", () => {
  const r = normalizeAutofocusResult({
    state: "done", points: [], best: null,
    fit: { curve: [[1, 2], [1, 2, 3], "x", [1, "y"], [3, 4]] },
  }, CTX)!;
  assert(r.fit?.curve?.length === 2, `expected 2 valid tuples, got ${r.fit?.curve?.length}`);
});

test("normalizeAutofocusResult: no provider/filter in ctx -> both null (never fabricated)", () => {
  const r = normalizeAutofocusResult(
    { state: "done", points: [], best: null },
    { provider: null, filter: null, tsMs: 5 },
  )!;
  assert(r.provider === null, "provider null");
  assert(r.filter === null, "filter null");
});

test("filterNameFromStatus: valid index, missing wheel, out-of-range position all handled", () => {
  assert(filterNameFromStatus({ position: 1, names: ["L", "R", "G", "B"] }) === "R", "valid index");
  assert(filterNameFromStatus(null) === null, "no wheel -> null");
  assert(filterNameFromStatus(undefined) === null, "undefined wheel -> null");
  assert(filterNameFromStatus({ position: 9, names: ["L"] }) === null, "out-of-range -> null (not undefined-as-string)");
});

test("afResultAgeLabel: just-now / minutes / hours / days bands", () => {
  const now = 1_700_000_000_000;
  assert(afResultAgeLabel(now - 5_000, now) === "just now", "5s -> just now");
  assert(afResultAgeLabel(now - 3 * 60_000, now) === "3m ago", "3m");
  assert(afResultAgeLabel(now - 2 * 3600_000, now) === "2.0h ago", "2h");
  assert(afResultAgeLabel(now - 3 * 86_400_000, now) === "3d ago", "3d");
});

// ------------------------------------------------------------ one-tap focus (NOV-6)
const TH = { hfrGood: 2.0, hfrWarn: 3.5 };

test("derive: reuses a star-bearing live frame's exposure, clamps to band", () => {
  const p = deriveAutofocusParams({ focuserMax: 30000, maxBin: 4, maxGain: 300,
    liveExposureS: 3, liveGain: 100, liveBinning: 2, liveStars: 40, liveHfr: 2.2 });
  assert(p.exposure_s === 3, `exposure ${p.exposure_s}`);
  assert(p.binning === 2, "bin2");
  assert(p.gain === 100, "reuse live gain");
  assert(p.step === AF_DEFAULT_STEP, `step ${p.step}`); // 9.3% in-band
  assert(p.steps_each_side === 4, "each side");
});
test("derive: no usable live frame -> fallback 2s; long sub clamps to 6s", () => {
  const a = deriveAutofocusParams({ focuserMax: null, maxBin: null, maxGain: null,
    liveExposureS: 300, liveGain: null, liveBinning: null, liveStars: 2, liveHfr: null });
  assert(a.exposure_s === 2, `few stars -> fallback 2s, got ${a.exposure_s}`);
  const b = deriveAutofocusParams({ focuserMax: null, maxBin: null, maxGain: null,
    liveExposureS: 300, liveGain: null, liveBinning: null, liveStars: 40, liveHfr: 2.2 });
  assert(b.exposure_s === 6, `300s sub clamps to 6s, got ${b.exposure_s}`);
});
test("derive: bin-1-only sensor and gain ceiling are respected", () => {
  const p = deriveAutofocusParams({ focuserMax: null, maxBin: 1, maxGain: 100,
    liveExposureS: null, liveGain: 200, liveBinning: 4, liveStars: null, liveHfr: null });
  assert(p.binning === 1, "bin clamped to 1");
  assert(p.gain === 100, `gain clamped to max, got ${p.gain}`);
});
test("derive: step rescales only at focuser-range extremes", () => {
  const nul = deriveAutofocusParams({ focuserMax: null, maxBin: 4, maxGain: 300,
    liveExposureS: 2, liveGain: 120, liveBinning: 2, liveStars: 40, liveHfr: 2 });
  assert(nul.step === AF_DEFAULT_STEP, "null focuser -> default");
  const tiny = deriveAutofocusParams({ focuserMax: 5000, maxBin: 4, maxGain: 300,
    liveExposureS: 2, liveGain: 120, liveBinning: 2, liveStars: 40, liveHfr: 2 });
  assert(tiny.step === 75, `5000 -> 75, got ${tiny.step}`); // 56% > 30%
  const huge = deriveAutofocusParams({ focuserMax: 100000, maxBin: 4, maxGain: 300,
    liveExposureS: 2, liveGain: 120, liveBinning: 2, liveStars: 40, liveHfr: 2 });
  assert(huge.step === AF_STEP_MAX, `100000 -> clamp ${AF_STEP_MAX}, got ${huge.step}`); // 2.8% < 4%
});
test("derive: a MEASURED span from the server beats every rule in this file", () => {
  // The sweep's width is no longer something a browser can work out. It is
  // sized from the defocus slope a completed sweep measured (server:
  // focus/span.py) — 0.078 px/step on this rig, which comes out at ±300 rather
  // than the shipped ±1400. If this file kept deriving its own number, the line
  // printed under the button would describe a sweep that does not happen, which
  // is the exact defect the `source`/`basis` split was added to end.
  const measured = "75 steps (±300) - sized from a defocus slope of 0.0780 px/step";
  const p = deriveAutofocusParams({ focuserMax: 30000, maxBin: 4, maxGain: 300,
    liveExposureS: 2, liveGain: 120, liveBinning: 1, liveStars: 40, liveHfr: 2,
    serverSweep: { step: 75, basis: measured } });
  assert(p.step === 75, `server span ignored: ${p.step}`);
  assert(p.basis.step === measured, `basis rebuilt locally: ${p.basis.step}`);
  // And it wins even where the local travel rule would have intervened, which
  // is where the two would otherwise most visibly disagree.
  const tiny = deriveAutofocusParams({ focuserMax: 5000, maxBin: 4, maxGain: 300,
    liveExposureS: 2, liveGain: 120, liveBinning: 1, liveStars: 40, liveHfr: 2,
    serverSweep: { step: 75, basis: measured } });
  assert(tiny.step === 75, `travel rule overrode a measurement: ${tiny.step}`);
});
test("derive: no server span (old server, no focuser) keeps the shipped rule", () => {
  // The fallback has to stay exactly what shipped: a rig whose focuser has
  // never completed a sweep gets the geometry every successful focus run in
  // this project's history used.
  for (const sweep of [null, undefined, { step: 0, basis: "nonsense" }]) {
    const p = deriveAutofocusParams({ focuserMax: 30000, maxBin: 4, maxGain: 300,
      liveExposureS: 2, liveGain: 120, liveBinning: 1, liveStars: 40, liveHfr: 2,
      serverSweep: sweep as never });
    assert(p.step === AF_DEFAULT_STEP, `fallback broke for ${JSON.stringify(sweep)}: ${p.step}`);
  }
});
test("derive: the sweep BINS LIKE THE FRAME — the third parameter, and the one that cost the night", () => {
  // The reviewed defect: binning was min(2, maxBin) unconditionally while three
  // sentences on the Focus screen said it had been copied from the live frame.
  // A user who shot the configuration that works — 4s / gain 220 / bin 1 — was
  // told bin 1 was copied and swept at bin 2, the setting focus/native.py
  // measured turning 24 stars into 8.
  const one = deriveAutofocusParams({ focuserMax: 30000, maxBin: 4, maxGain: 570,
    liveExposureS: 4, liveGain: 220, liveBinning: 1, liveStars: 2100, liveHfr: 2.4 });
  assert(one.binning === 1, `bin 1 frame -> bin 1 sweep, got ${one.binning}`);
  assert(/copied/i.test(one.basis.binning), `and says so: ${one.basis.binning}`);
  const two = deriveAutofocusParams({ focuserMax: 30000, maxBin: 4, maxGain: 570,
    liveExposureS: 4, liveGain: 220, liveBinning: 2, liveStars: 2100, liveHfr: 2.4 });
  assert(two.binning === 2, "a bin-2 frame is copied just as faithfully");
  // A star-poor frame still hands over its binning: like gain, it is a setting
  // the user chose, not a measurement the frame failed to make.
  const sparse = deriveAutofocusParams({ focuserMax: 30000, maxBin: 4, maxGain: 570,
    liveExposureS: 1, liveGain: 220, liveBinning: 1, liveStars: 2, liveHfr: 3.0 });
  assert(sparse.source === "sparse" && sparse.binning === 1,
         `sparse frame keeps its binning, got ${sparse.binning}`);
});
test("derive: with NO frame the binning is a guess, is called one, and is not the star-losing one", () => {
  const blind = deriveAutofocusParams({ focuserMax: 30000, maxBin: 4, maxGain: 570,
    liveExposureS: null, liveGain: null, liveBinning: null, liveStars: null, liveHfr: null });
  assert(blind.binning === AF_FALLBACK_BIN, `blind bin, got ${blind.binning}`);
  assert(blind.binning === 1, "the blind guess is the setting that keeps the most stars");
  assert(/guess/i.test(blind.basis.binning), `must not read as a decision: ${blind.basis.binning}`);
  assert(!/copied/i.test(blind.basis.binning), "must not claim a copy that never happened");
});
test("derive: a frame binned past the sensor's ceiling is clamped and the clamp is stated", () => {
  const p = deriveAutofocusParams({ focuserMax: null, maxBin: 2, maxGain: 300,
    liveExposureS: 3, liveGain: 100, liveBinning: 4, liveStars: 40, liveHfr: 2.2 });
  assert(p.binning === 2, `clamped to the ceiling, got ${p.binning}`);
  assert(/clamped/i.test(p.basis.binning), `and says it clamped: ${p.basis.binning}`);
});
test("plainFocusVerdict: sharp / soft(action) / failed(action) / running", () => {
  assert(plainFocusVerdict({ state: "done", hfr: 1.8, r2: 0.997, ...TH }).headline === "Sharp!", "sharp");
  const soft = plainFocusVerdict({ state: "done", hfr: 4.0, r2: 0.99, ...TH });
  assert(soft.tone === "warn" && /try again/.test(soft.detail), "soft carries action");
  const fail = plainFocusVerdict({ state: "failed", hfr: null, r2: null, ...TH });
  assert(fail.tone === "bad" && /try again/.test(fail.detail), "failed carries action");
  assert(plainFocusVerdict({ state: "running", hfr: null, r2: null, ...TH }).headline === "Focusing…", "running");
});
test("focusButtonState: viewer locked > no-focuser > running > enabled", () => {
  assert(focusButtonState({ canFocus: false, hasFocuser: true, running: false }).locked === true, "viewer locked");
  assert(focusButtonState({ canFocus: true, hasFocuser: false, running: false }).reason != null, "no focuser reason");
  assert(focusButtonState({ canFocus: true, hasFocuser: true, running: true }).label === "Focusing…", "running label");
  const ok = focusButtonState({ canFocus: true, hasFocuser: true, running: false });
  assert(ok.disabled === false && ok.reason === null, "enabled");
});
test("focusButtonState: an unmeasured sweep is blocked, and the hero carries the reason", () => {
  // The 2026-07-31 failure: a rig with permission, a focuser and an idle sweep
  // still cannot honestly start one when nothing has been measured to copy.
  const b = focusButtonState({
    canFocus: true, hasFocuser: true, running: false,
    sweepBlock: "Take a frame first — autofocus copies the live frame",
  });
  assert(b.disabled === true, "must not start");
  assert(b.reason!.includes("Take a frame first"), "must carry the sentence, not a bare boolean");
  // A bigger fact about the rig still outranks it.
  assert(/read-only/i.test(focusButtonState({
    canFocus: false, hasFocuser: true, running: false, sweepBlock: "no frame",
  }).reason!), "permission outranks the parameter complaint");
});

// ---------------------------------------------------- #114 failure explainer
test("readFocusFailure: the engine's tokens are explained in the SAME terms they mean", () => {
  // The bug: "Not enough stars to lock onto — check the sky is clear" printed
  // directly above "r_squared_below_threshold", which is a fit-SHAPE failure
  // that happens perfectly well with two thousand stars. The user chased the
  // star count all night because the sentence, not the token, was in English.
  const r = readFocusFailure("r_squared_below_threshold");
  assert(r.code === "r_squared_below_threshold", "code kept for the technical chip");
  assert(r.explain != null, "must be explained");
  assert(/shape|curve/i.test(r.explain!), `must be about the curve, not the star count: ${r.explain}`);
  assert(!/not enough stars/i.test(r.explain!), "must not blame star count");
});
test("readFocusFailure: every engine reason is covered, and each names an action", () => {
  // Mirrors native/crates/astrodeck-native/src/lib.rs fail_reason_label.
  for (const code of ["not_enough_spread", "r_squared_below_threshold", "out_of_bounds",
                      "hfr_worse_than_start", "fit_unavailable"]) {
    const r = readFocusFailure(code);
    assert(r.code === code, `${code} recognised`);
    assert(r.explain != null && r.explain.length > 20, `${code} explained`);
  }
});
test("readFocusFailure: a decorated or unknown message never fabricates an explanation", () => {
  assert(readFocusFailure("native engine: out_of_bounds").code === "out_of_bounds", "prefix tolerated");
  assert(readFocusFailure("only 0 stars at the current focus").explain === null, "server prose -> no client guess");
  assert(readFocusFailure(null).code === null, "null -> nothing");
  assert(readFocusFailure("   ").explain === null, "blank -> nothing");
});
test("plainFocusVerdict: the server's own advice outranks every canned sentence", () => {
  const advice = "Every point measured 0 stars at 2s/gain 120 — try 4s at gain 220.";
  const failed = plainFocusVerdict({ state: "failed", hfr: null, r2: null, ...TH,
                                     message: "fit_unavailable", advice });
  assert(failed.detail === advice, "advice wins over the token explanation");
  const soft = plainFocusVerdict({ state: "done", hfr: 4.0, r2: 0.99, ...TH, advice });
  assert(soft.detail === advice, "a soft SUCCESS can also be worth explaining");
});
test("plainFocusVerdict: a SUCCESSFUL run's advice is rendered, not replaced by 'Sharp!'", () => {
  // focus/native.py calls _advice(ok=True) explicitly to stop "letting a
  // confident R² stand on four five-star samples", and the sentence it produces
  // fires ONLY on a run good enough to reach `excellent` (R² >= 0.98). The
  // panel used to print "Stars are tight — you're focused." over it, deleting
  // the one caveat the server took the trouble to compute.
  const thin = "3 of 9 points came from fewer than 10 stars and carry little weight "
    + "in the fit, so this is thinner evidence than the R² suggests.";
  const excellent = plainFocusVerdict({ state: "done", hfr: 1.8, r2: 0.997, ...TH, advice: thin });
  assert(excellent.level === "excellent", "still an excellent run");
  assert(excellent.headline === "Sharp!", "the headline still reports the measurement");
  assert(excellent.detail === thin, `the caveat must survive: ${excellent.detail}`);
  const good = plainFocusVerdict({ state: "done", hfr: 2.5, r2: 0.9, ...TH, advice: thin });
  assert(good.detail === thin, "same for a merely good run");
  // And with nothing to add, the canned reassurance is still there.
  assert(plainFocusVerdict({ state: "done", hfr: 1.8, r2: 0.997, ...TH }).detail
         === "Stars are tight - you're focused.", "no advice -> the plain sentence");
  // A run that finished with no HFR at all is still a run that happened.
  const noHfr = plainFocusVerdict({ state: "done", hfr: null, r2: null, ...TH, advice: thin });
  assert(noHfr.detail === thin, `a done-but-unmeasured run keeps its advice: ${noHfr.detail}`);
  assert(plainFocusVerdict({ ...TH }).detail === "Tap Focus my scope to start.",
         "before any run, the canned invitation stands");
});
test("plainFocusVerdict: without advice it explains the token; with neither it invents no cause", () => {
  const explained = plainFocusVerdict({ state: "failed", hfr: null, r2: null, ...TH,
                                        message: "r_squared_below_threshold" });
  assert(/curve|shape/i.test(explained.detail), `must explain the token: ${explained.detail}`);
  const bare = plainFocusVerdict({ state: "failed", hfr: null, r2: null, ...TH });
  assert(!/not enough stars/i.test(bare.detail), "must not guess a cause it was never told");
  assert(/try again/.test(bare.detail), "but must still carry an action");
  const prose = plainFocusVerdict({ state: "failed", hfr: null, r2: null, ...TH,
                                    message: "only 0 stars at the current focus" });
  assert(prose.detail.includes("only 0 stars"), "an unexplained server sentence is passed through, not dropped");
});
test("normalizeAutofocusResult: advice rides on the event; blank is not advice", () => {
  const withAdvice = normalizeAutofocusResult(
    { state: "failed", points: [], best: null, message: "fit_unavailable",
      advice: "Try 4s at gain 220." }, CTX)!;
  assert(withAdvice.advice === "Try 4s at gain 220.", "carried");
  assert(normalizeAutofocusResult({ state: "done", points: [], best: null }, CTX)!.advice === null,
         "an older server that cannot say -> null, never a fabricated sentence");
  assert(normalizeAutofocusResult({ state: "done", points: [], best: null, advice: "  " }, CTX)!.advice === null,
         "blank advice must not hide the fallback that does have something to say");
});

console.log(`autofocus.test: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
