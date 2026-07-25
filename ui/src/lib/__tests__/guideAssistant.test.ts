// guideAssistant.test.ts — pure tests for lib/guideAssistant.ts (design §5 #6/#7):
// summarize copy bands, buildApplyBody maps a report onto a valid CLAMPED PUT
// body (reusing validateGuideSettings), selective-apply honors selectedKeys, and
// an out-of-range recommendation is clamped by the shared validator (pins that
// buildApplyBody does not bypass guideSettings clamps).
//
// Pure module (no React), runs under plain tsx like the repo verify command
// `npx tsx src/lib/__tests__/*.test.ts`. Copies the header/test/assert idiom
// from guideSettings.test.ts verbatim.
import {
  summarize,
  buildApplyBody,
  applyChangesAlgorithm,
  formatRecommendations,
  toggleRecommendationKey,
  backlashResultSentence,
  type AssistantReport,
  type AssistantRecommendation,
} from "../guideAssistant";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const DEFAULT_RECS: AssistantRecommendation[] = [
  { key: "min_move", field: "min_move", current: 0.2, recommended: 0.19, unit: "px", rationale: "calm", confidence: "high", advanced: false },
  { key: "aggression", field: "aggression", current: 0.7, recommended: 0.7, unit: "", rationale: "default", confidence: "high", advanced: false },
  { key: "hysteresis", field: "hysteresis", current: 0.1, recommended: 0.1, unit: "", rationale: "default", confidence: "high", advanced: false },
  { key: "ra_algorithm", field: "ra_algorithm", current: "hysteresis", recommended: "hysteresis", unit: "", rationale: "default", confidence: "high", advanced: false },
  { key: "dec_algorithm", field: "dec_algorithm", current: "resist_switch", recommended: "resist_switch", unit: "", rationale: "robust", confidence: "high", advanced: false },
  { key: "blc_pulse_ms", field: "blc_pulse_ms", current: 0, recommended: 430, unit: "ms", rationale: "measured", confidence: "high", advanced: false },
];

function makeReport(over: Partial<AssistantReport> = {}): AssistantReport {
  return {
    measurements: {
      n: 60, rms_ra_px: 0.4, rms_dec_px: 0.3, rms_total_px: 0.5,
      rms_ra_arcsec: 0.8, rms_dec_arcsec: 0.6, rms_total_arcsec: 1.0,
      drift_per_min_px: 6.0, drift_per_min_arcsec: 72.0,
      pe_amplitude_px: 1.0, pe_period_s: 200.0, jitter_px: 0.05,
      image_scale_arcsec: 2.0, image_scale_known: true,
      backlash: { bl_px: 3.4, bl_ms: 430, sigma_ms: 40, north_rate: 0.008, result_code: "VALID", y_rate_source: "declared", halted: false, measured: true },
      ...(over.measurements ?? {}),
    },
    recommendations: over.recommendations ?? DEFAULT_RECS,
    current: over.current ?? {
      ra_algorithm: "hysteresis", dec_algorithm: "resist_switch",
      dec_guide_mode: "auto", blc_pulse_ms: 0, ra_params: {}, dec_params: {},
    },
    polar: over.polar ?? { verdict: "good", tone: "good", drift_per_min_arcsec: 72.0 },
    samples: over.samples ?? [{ t: 0, ra: 0, dec: 0 }],
  };
}

// (a) summarize copy bands — parametrized over polar tone + backlash presence.
const SUMMARY_CASES: {
  name: string; tone: "good" | "warn" | "bad"; verdict: string;
  bl_ms: number; code: string; measured: boolean; wants: string;
}[] = [
  { name: "good polar + backlash", tone: "good", verdict: "good", bl_ms: 430, code: "VALID", measured: true, wants: "430 ms" },
  { name: "bad polar", tone: "bad", verdict: "consider re-doing polar alignment", bl_ms: 0, code: "VALID", measured: true, wants: "negligible" },
  { name: "unmeasured backlash", tone: "warn", verdict: "fair", bl_ms: 0, code: "SANITY", measured: false, wants: "couldn't measure" },
  // SAFETY: bl_ms 0 + TOO_FEW_NORTH is what a run that never measured returns.
  // It must NOT read as "negligible" (that copy is why Apply zeroed a tuned
  // blc_pulse_ms) — it must say the current setting is left alone.
  { name: "phase B never ran", tone: "good", verdict: "good", bl_ms: 0, code: "TOO_FEW_NORTH", measured: false, wants: "current setting is left alone" },
];
for (const c of SUMMARY_CASES) {
  test(`summarize: ${c.name}`, () => {
    const r = makeReport({
      polar: { verdict: c.verdict, tone: c.tone, drift_per_min_arcsec: 72.0 },
      measurements: {
        ...makeReport().measurements,
        backlash: { bl_px: 1, bl_ms: c.bl_ms, sigma_ms: 5, north_rate: 0.008, result_code: c.code, y_rate_source: "declared", halted: false, measured: c.measured },
      },
    });
    const s = summarize(r);
    assert(s.tone === c.tone, `tone: ${s.tone}`);
    assert(s.polarVerdict === c.verdict, `verdict: ${s.polarVerdict}`);
    assert(s.headline.includes(c.verdict), `headline missing verdict: ${s.headline}`);
    assert(s.headline.includes(c.wants), `headline missing "${c.wants}": ${s.headline}`);
  });
}

// (b) buildApplyBody maps a report onto a valid, clamped PUT body.
test("buildApplyBody applies all non-advanced recs (novice one-tap)", () => {
  const body = buildApplyBody(makeReport());
  assert(body.blc_pulse_ms === 430, `blc: ${body.blc_pulse_ms}`);
  assert(body.ra_params.min_move === 0.19, `ra min_move: ${body.ra_params.min_move}`);
  assert(body.dec_params.min_move === 0.19, `dec min_move: ${body.dec_params.min_move}`);
  assert(body.ra_algorithm === "hysteresis", `ra alg: ${body.ra_algorithm}`);
  assert(body.dec_algorithm === "resist_switch", `dec alg: ${body.dec_algorithm}`);
  assert(body.dec_guide_mode === "auto", `dec mode: ${body.dec_guide_mode}`);
});

// (c) selective apply honors selectedKeys — only the chosen recs land.
test("buildApplyBody with selectedKeys applies only those", () => {
  const body = buildApplyBody(makeReport(), ["blc_pulse_ms"]);
  assert(body.blc_pulse_ms === 430, `blc: ${body.blc_pulse_ms}`);
  // min_move NOT selected -> stays the current algorithm default (0.2), not 0.19.
  assert(body.ra_params.min_move === 0.2, `ra min_move should be default: ${body.ra_params.min_move}`);
});

// advanced PPEC opt-in only lands when its key is explicitly selected.
test("advanced PPEC never applied by default, applies when selected", () => {
  const recs: AssistantRecommendation[] = [
    ...DEFAULT_RECS,
    { key: "ra_algorithm_ppec", field: "ra_algorithm", current: "hysteresis", recommended: "ppec", unit: "", rationale: "PE", confidence: "medium", advanced: true },
  ];
  const rep = makeReport({ recommendations: recs });
  assert(buildApplyBody(rep).ra_algorithm === "hysteresis", "default must stay hysteresis");
  const opt = buildApplyBody(rep, ["ra_algorithm_ppec", "blc_pulse_ms"]);
  assert(opt.ra_algorithm === "ppec", `opt-in ra alg: ${opt.ra_algorithm}`);
});

// (d) an out-of-range recommendation is CLAMPED by the shared validator —
// buildApplyBody must not bypass guideSettings clamps.
test("out-of-range aggression is clamped by validateGuideSettings", () => {
  const recs: AssistantRecommendation[] = DEFAULT_RECS.map((r) =>
    r.field === "aggression" ? { ...r, recommended: 9 } : r);
  const body = buildApplyBody(makeReport({ recommendations: recs }));
  assert(body.ra_params.aggression === 2.0, `aggression clamped: ${body.ra_params.aggression}`);
});

test("garbage blc pulse clamps to the [0,10000] ceiling", () => {
  const recs: AssistantRecommendation[] = DEFAULT_RECS.map((r) =>
    r.field === "blc_pulse_ms" ? { ...r, recommended: 999999 } : r);
  const body = buildApplyBody(makeReport({ recommendations: recs }));
  assert(body.blc_pulse_ms === 10000, `blc clamped: ${body.blc_pulse_ms}`);
});

// (e) applyChangesAlgorithm drives the D4 clear-calibration decision.
test("applyChangesAlgorithm: false when only params change, true on algo swap", () => {
  assert(applyChangesAlgorithm(makeReport()) === false, "param-only must not change algo");
  const recs: AssistantRecommendation[] = DEFAULT_RECS.map((r) =>
    r.field === "dec_algorithm" ? { ...r, recommended: "lowpass2" } : r);
  assert(applyChangesAlgorithm(makeReport({ recommendations: recs })) === true, "dec algo swap");
});

// (f) formatRecommendations gives labelled before/after rows for the advanced table.
test("formatRecommendations labels each row", () => {
  const rows = formatRecommendations(makeReport());
  assert(rows.length === DEFAULT_RECS.length, `rows: ${rows.length}`);
  const blc = rows.find((r) => r.field === "blc_pulse_ms");
  assert(!!blc && blc.label === "Dec backlash pulse", `blc label: ${blc?.label}`);
  assert(blc!.recommended === 430, `blc recommended: ${blc!.recommended}`);
});

// (g) two recommendations can target the SAME field (default vs advanced opt-in).
// They must not render with an identical label, and ticking one must untick the
// other — apply order used to decide the winner with no signal to the user.
const PPEC_REPORT = makeReport({
  recommendations: [
    ...DEFAULT_RECS,
    { key: "ra_algorithm_ppec", field: "ra_algorithm", current: "hysteresis", recommended: "ppec", unit: "", rationale: "PE", confidence: "medium", advanced: true },
  ],
});

test("same-field recommendations get distinct labels + conflict lists", () => {
  const rows = formatRecommendations(PPEC_REPORT);
  const a = rows.find((r) => r.key === "ra_algorithm")!;
  const b = rows.find((r) => r.key === "ra_algorithm_ppec")!;
  assert(a.label !== b.label, `labels must differ: ${a.label} / ${b.label}`);
  assert(a.conflicts.includes("ra_algorithm_ppec"), `conflicts: ${a.conflicts}`);
  assert(b.conflicts.includes("ra_algorithm"), `conflicts: ${b.conflicts}`);
  const mm = rows.find((r) => r.key === "min_move")!;
  assert(mm.conflicts.length === 0, "single-row fields have no conflicts");
});

test("toggleRecommendationKey enforces the same-field either/or", () => {
  const base = new Set(["ra_algorithm", "min_move"]);
  const on = toggleRecommendationKey(PPEC_REPORT, base, "ra_algorithm_ppec");
  assert(on.has("ra_algorithm_ppec"), "ppec selected");
  assert(!on.has("ra_algorithm"), "the other RA-algorithm row must be unticked");
  assert(on.has("min_move"), "unrelated fields untouched");
  // buildApplyBody then has exactly one ra_algorithm rec to apply.
  assert(buildApplyBody(PPEC_REPORT, [...on]).ra_algorithm === "ppec", "ppec applied");
  // toggling off is a plain removal.
  const off = toggleRecommendationKey(PPEC_REPORT, on, "ra_algorithm_ppec");
  assert(!off.has("ra_algorithm_ppec") && !off.has("ra_algorithm"), "both off");
});

// The advanced panel must never print a raw BL_* enum at the user (and an
// unknown code must not leak the identifier either).
for (const code of ["VALID", "TOO_FEW_NORTH", "TOO_FEW_SOUTH", "BL_NOT_CLEARED",
                    "SANITY", "WAT_IS_THIS"]) {
  test(`backlashResultSentence(${code}) is a sentence, not the enum`, () => {
    const s = backlashResultSentence(code);
    assert(s.length > 8, `too short to be a sentence: ${s}`);
    assert(!s.includes(code), `leaks the code itself: ${s}`);
    assert(!/[A-Z_]{3,}/.test(s), `leaks an identifier: ${s}`);
  });
}

console.log(`guideAssistant.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
