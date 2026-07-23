// autofocus.test.ts — normalizeAutofocusResult (terminal-only gate, defensive
// point/fit parsing) + filterNameFromStatus + afResultAgeLabel (F5: R2-FOC-01).
// Run with:  npx tsx src/lib/__tests__/autofocus.test.ts   (from ui/)

import {
  afResultAgeLabel, filterNameFromStatus, normalizeAutofocusResult,
  deriveAutofocusParams, plainFocusVerdict, focusButtonState,
  AF_DEFAULT_STEP, AF_STEP_MAX,
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
    liveExposureS: 3, liveGain: 100, liveStars: 40, liveHfr: 2.2 });
  assert(p.exposure_s === 3, `exposure ${p.exposure_s}`);
  assert(p.binning === 2, "bin2");
  assert(p.gain === 100, "reuse live gain");
  assert(p.step === AF_DEFAULT_STEP, `step ${p.step}`); // 9.3% in-band
  assert(p.steps_each_side === 4, "each side");
});
test("derive: no usable live frame -> fallback 2s; long sub clamps to 6s", () => {
  const a = deriveAutofocusParams({ focuserMax: null, maxBin: null, maxGain: null,
    liveExposureS: 300, liveGain: null, liveStars: 2, liveHfr: null });
  assert(a.exposure_s === 2, `few stars -> fallback 2s, got ${a.exposure_s}`);
  const b = deriveAutofocusParams({ focuserMax: null, maxBin: null, maxGain: null,
    liveExposureS: 300, liveGain: null, liveStars: 40, liveHfr: 2.2 });
  assert(b.exposure_s === 6, `300s sub clamps to 6s, got ${b.exposure_s}`);
});
test("derive: bin-1-only sensor and gain ceiling are respected", () => {
  const p = deriveAutofocusParams({ focuserMax: null, maxBin: 1, maxGain: 100,
    liveExposureS: null, liveGain: 200, liveStars: null, liveHfr: null });
  assert(p.binning === 1, "bin clamped to 1");
  assert(p.gain === 100, `gain clamped to max, got ${p.gain}`);
});
test("derive: step rescales only at focuser-range extremes", () => {
  const nul = deriveAutofocusParams({ focuserMax: null, maxBin: 4, maxGain: 300,
    liveExposureS: 2, liveGain: 120, liveStars: 40, liveHfr: 2 });
  assert(nul.step === AF_DEFAULT_STEP, "null focuser -> default");
  const tiny = deriveAutofocusParams({ focuserMax: 5000, maxBin: 4, maxGain: 300,
    liveExposureS: 2, liveGain: 120, liveStars: 40, liveHfr: 2 });
  assert(tiny.step === 75, `5000 -> 75, got ${tiny.step}`); // 56% > 30%
  const huge = deriveAutofocusParams({ focuserMax: 100000, maxBin: 4, maxGain: 300,
    liveExposureS: 2, liveGain: 120, liveStars: 40, liveHfr: 2 });
  assert(huge.step === AF_STEP_MAX, `100000 -> clamp ${AF_STEP_MAX}, got ${huge.step}`); // 2.8% < 4%
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

console.log(`autofocus.test: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
