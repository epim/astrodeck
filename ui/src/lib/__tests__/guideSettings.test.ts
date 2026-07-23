// guideSettings.test.ts — pure tests for lib/guideSettings.ts's
// defaultGuideSettings()/validateGuideSettings(), the per-axis guide-algorithm
// selection the native guider's GuideView settings drawer edits. Asserts the
// dossier §15 PHD2-default parameter set (RA Hysteresis 0.7/0.1/0.2, Dec Resist
// Switch 1.0), the out-of-range clamps (aggression <= 2.0, hysteresis <= 0.99),
// and rejection of unknown per-axis algorithm names.
//
// Pure module (no React import), so it runs under plain tsx exactly like the
// repo verify command `npx tsx src/lib/__tests__/*.test.ts`. Copies the
// header/`test`/`assert`/`process.exit`-via-`globalThis` idiom from
// apiError.test.ts verbatim.
import {
  defaultGuideSettings,
  validateGuideSettings,
  RA_GUIDE_ALGORITHMS,
  DEC_GUIDE_ALGORITHMS,
  DEC_GUIDE_MODES,
  isValidDecGuideMode,
  type GuideSettings,
} from "../guideSettings";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// (a) defaults — the dossier §15 PHD2-default parameter set.
test("defaultGuideSettings yields RA Hysteresis (0.7/0.1/0.2)", () => {
  const s = defaultGuideSettings();
  assert(s.ra.algorithm === "hysteresis", `ra.algorithm: ${s.ra.algorithm}`);
  assert(s.ra.params.aggression === 0.7, `ra.aggression: ${s.ra.params.aggression}`);
  assert(s.ra.params.hysteresis === 0.1, `ra.hysteresis: ${s.ra.params.hysteresis}`);
  assert(s.ra.params.minMove === 0.2, `ra.minMove: ${s.ra.params.minMove}`);
});

test("defaultGuideSettings yields Dec Resist Switch (1.0)", () => {
  const s = defaultGuideSettings();
  assert(s.dec.algorithm === "resist_switch", `dec.algorithm: ${s.dec.algorithm}`);
  assert(s.dec.params.aggression === 1.0, `dec.aggression: ${s.dec.params.aggression}`);
  assert(s.dec.params.minMove === 0.2, `dec.minMove: ${s.dec.params.minMove}`);
});

test("defaultGuideSettings returns a fresh object each call (no shared mutation)", () => {
  const a = defaultGuideSettings();
  a.ra.params.aggression = 1.9;
  const b = defaultGuideSettings();
  assert(b.ra.params.aggression === 0.7, `b.ra.aggression leaked: ${b.ra.params.aggression}`);
});

test("defaultGuideSettings disables static BLC (blcPulseMs 0)", () => {
  const s = defaultGuideSettings();
  assert(s.blcPulseMs === 0, `blcPulseMs: ${s.blcPulseMs}`);
});

test("validateGuideSettings clamps the static BLC pulse (integer, >= 0, <= 10000)", () => {
  const neg = defaultGuideSettings();
  neg.blcPulseMs = -50;
  assert(validateGuideSettings(neg).blcPulseMs === 0, "negative BLC floors to 0");

  const frac = defaultGuideSettings();
  frac.blcPulseMs = 199.6;
  assert(validateGuideSettings(frac).blcPulseMs === 200, "BLC rounds to an integer ms");

  const huge = defaultGuideSettings();
  huge.blcPulseMs = 999999;
  assert(validateGuideSettings(huge).blcPulseMs === 10000, "BLC caps at 10000 ms");

  const ok = defaultGuideSettings();
  ok.blcPulseMs = 250;
  assert(validateGuideSettings(ok).blcPulseMs === 250, "in-range BLC unchanged");
});

// (b) validate — clamps out-of-range params.
test("validateGuideSettings clamps aggression to <= 2.0", () => {
  const s = defaultGuideSettings();
  s.ra.params.aggression = 5.0;
  s.dec.params.aggression = 3.3;
  const out = validateGuideSettings(s);
  assert(out.ra.params.aggression === 2.0, `ra.aggression: ${out.ra.params.aggression}`);
  assert(out.dec.params.aggression === 2.0, `dec.aggression: ${out.dec.params.aggression}`);
});

test("validateGuideSettings clamps hysteresis to <= 0.99", () => {
  const s = defaultGuideSettings();
  s.ra.params.hysteresis = 1.5;
  const out = validateGuideSettings(s);
  assert(out.ra.params.hysteresis === 0.99, `ra.hysteresis: ${out.ra.params.hysteresis}`);
});

test("validateGuideSettings floors negatives to 0", () => {
  const s = defaultGuideSettings();
  s.ra.params.aggression = -1.0;
  s.ra.params.minMove = -0.5;
  const out = validateGuideSettings(s);
  assert(out.ra.params.aggression === 0, `ra.aggression: ${out.ra.params.aggression}`);
  assert(out.ra.params.minMove === 0, `ra.minMove: ${out.ra.params.minMove}`);
});

test("validateGuideSettings leaves in-range values unchanged", () => {
  const out = validateGuideSettings(defaultGuideSettings());
  assert(out.ra.params.aggression === 0.7, `ra.aggression: ${out.ra.params.aggression}`);
  assert(out.ra.params.hysteresis === 0.1, `ra.hysteresis: ${out.ra.params.hysteresis}`);
  assert(out.dec.params.aggression === 1.0, `dec.aggression: ${out.dec.params.aggression}`);
});

// (c) validate — rejects unknown algorithm names.
test("validateGuideSettings rejects an unknown RA algorithm", () => {
  const s = defaultGuideSettings();
  (s.ra as { algorithm: string }).algorithm = "bogus";
  let threw = false;
  try { validateGuideSettings(s); } catch { threw = true; }
  assert(threw, "expected a throw for an unknown RA algorithm");
});

test("validateGuideSettings rejects an unknown Dec algorithm", () => {
  const s = defaultGuideSettings();
  (s.dec as { algorithm: string }).algorithm = "not_an_algo";
  let threw = false;
  try { validateGuideSettings(s); } catch { threw = true; }
  assert(threw, "expected a throw for an unknown Dec algorithm");
});

test("validateGuideSettings rejects PPEC on Dec (RA-only algorithm)", () => {
  // PPEC is present in RA_GUIDE_ALGORITHMS but absent from DEC_GUIDE_ALGORITHMS.
  assert(RA_GUIDE_ALGORITHMS.some((o) => o.value === "ppec"), "ppec must be an RA option");
  assert(!DEC_GUIDE_ALGORITHMS.some((o) => o.value === "ppec"), "ppec must NOT be a Dec option");
  const s = defaultGuideSettings();
  (s.dec as { algorithm: string }).algorithm = "ppec";
  let threw = false;
  try { validateGuideSettings(s); } catch { threw = true; }
  assert(threw, "expected a throw for PPEC on Dec");
});

test("validateGuideSettings accepts a valid non-default algorithm swap", () => {
  const s: GuideSettings = defaultGuideSettings();
  (s.ra as { algorithm: string }).algorithm = "lowpass2";
  (s.dec as { algorithm: string }).algorithm = "z_filter";
  const out = validateGuideSettings(s);
  assert(out.ra.algorithm === "lowpass2", `ra.algorithm: ${out.ra.algorithm}`);
  assert(out.dec.algorithm === "z_filter", `dec.algorithm: ${out.dec.algorithm}`);
});

// (d) Dec guide DIRECTION (PRO-12 Tier 1) — distinct from the Dec algorithm.
test("defaultGuideSettings seeds decGuideMode auto", () => {
  const s = defaultGuideSettings();
  assert(s.decGuideMode === "auto", `decGuideMode: ${s.decGuideMode}`);
});

test("DEC_GUIDE_MODES carries all four directions", () => {
  const values = DEC_GUIDE_MODES.map((o) => o.value).sort();
  assert(
    JSON.stringify(values) === JSON.stringify(["auto", "north", "off", "south"]),
    `DEC_GUIDE_MODES values: ${values.join(",")}`,
  );
});

test("isValidDecGuideMode accepts the four literals and rejects junk", () => {
  assert(isValidDecGuideMode("auto"), "auto should be valid");
  assert(isValidDecGuideMode("north"), "north should be valid");
  assert(isValidDecGuideMode("south"), "south should be valid");
  assert(isValidDecGuideMode("off"), "off should be valid");
  assert(!isValidDecGuideMode("bogus"), "bogus should be invalid");
});

test("validateGuideSettings coerces an unknown decGuideMode to auto", () => {
  const s = defaultGuideSettings();
  (s as { decGuideMode: string }).decGuideMode = "bogus";
  const out = validateGuideSettings(s);
  assert(out.decGuideMode === "auto", `decGuideMode: ${out.decGuideMode}`);
});

test("validateGuideSettings preserves a valid non-default decGuideMode", () => {
  const s = defaultGuideSettings();
  s.decGuideMode = "south";
  const out = validateGuideSettings(s);
  assert(out.decGuideMode === "south", `decGuideMode: ${out.decGuideMode}`);
});

console.log(`guideSettings.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
