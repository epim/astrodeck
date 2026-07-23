// Unit tests for guideNarration (NOV-7 design doc §1.5/§3 Task 1).
//
// No vitest/jest wired into this UI (build is `tsc -b && vite build`), so
// this uses the same tiny inline-assert harness as eta.test.ts /
// healthStrip.test.ts. Run directly with a TS-aware runner:
//   npx tsx src/lib/__tests__/guideNarration.test.ts

import { guideNarration, type GuideNarrationInput } from "../guideNarration";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

// A fully-specified "good, guiding, arcsec" baseline so each test only
// overrides what it's exercising.
function baseInput(overrides: Partial<GuideNarrationInput> = {}): GuideNarrationInput {
  return {
    connected: true,
    phase: "guiding",
    guiding: true,
    rmsTotal: 1.1,
    isArcsec: true,
    imageScale: 1.5714,
    hasSamples: true,
    ...overrides,
  };
}

// ---------------------------------------------------------------- not connected
test("not connected -> No guider connected, verdict null", () => {
  const r = guideNarration(baseInput({ connected: false }));
  eq(r.phaseText, "No guider connected");
  eq(r.tone, "neutral");
  eq(r.verdict, null);
});

// ---------------------------------------------------------------- phase strings
test('phase "finding" -> Finding a guide star…', () => {
  const r = guideNarration(baseInput({ phase: "finding" }));
  eq(r.phaseText, "Finding a guide star…");
  eq(r.tone, "neutral");
  eq(r.verdict, null);
});

test('phase "calibrating" -> Calibrating the guider…', () => {
  const r = guideNarration(baseInput({ phase: "calibrating" }));
  eq(r.phaseText, "Calibrating the guider…");
  eq(r.tone, "neutral");
  eq(r.verdict, null);
});

test('phase "settling" -> tone warn', () => {
  const r = guideNarration(baseInput({ phase: "settling" }));
  eq(r.phaseText, "Settling after the move…");
  eq(r.tone, "warn");
  eq(r.verdict, null);
});

test('phase "lost" -> tone bad, verdict null', () => {
  const r = guideNarration(baseInput({ phase: "lost" }));
  eq(r.phaseText, "Lost the guide star — trying to recover");
  eq(r.tone, "bad");
  eq(r.verdict, null);
});

// ---------------------------------------------------------------- headline case
test("headline case: rms 1.1 / scale 1.5714 -> 0.7 px ≈ 1.1″ — good for 3-minute subs", () => {
  const r = guideNarration(baseInput());
  eq(r.verdict, "0.7 px ≈ 1.1″ — good for 3-minute subs");
  eq(r.tone, "good");
  eq(r.phaseText, "Guiding well — you can relax");
});

// ---------------------------------------------------------------- arcsec-only
test("arcsec-only (imageScale 0) -> verdict without a px prefix", () => {
  const r = guideNarration(baseInput({ imageScale: 0 }));
  eq(r.verdict, "1.1″ — good for 3-minute subs");
  eq(r.tone, "good");
});

// ---------------------------------------------------------------- px-only (UX-15)
test("isArcsec:false while guiding -> verdict null (UX-15 note owns it)", () => {
  const r = guideNarration(baseInput({ isArcsec: false }));
  eq(r.verdict, null);
  // still a guiding sentence (the neutral default text)
  eq(r.phaseText, "Guiding well — you can relax");
  eq(r.tone, "neutral");
});

// ---------------------------------------------------------------- measuring
test("rmsTotal:0 while guiding -> Guiding — measuring…, verdict null", () => {
  const r = guideNarration(baseInput({ rmsTotal: 0 }));
  eq(r.phaseText, "Guiding — measuring…");
  eq(r.tone, "neutral");
  eq(r.verdict, null);
});

test("hasSamples:false while guiding -> Guiding — measuring…, verdict null", () => {
  const r = guideNarration(baseInput({ hasSamples: false }));
  eq(r.phaseText, "Guiding — measuring…");
  eq(r.tone, "neutral");
  eq(r.verdict, null);
});

// ---------------------------------------------------------------- phase-absent fallback
test("phase-absent fallback: guiding:true with good rms -> guiding verdict", () => {
  const r = guideNarration(baseInput({ phase: undefined }));
  eq(r.verdict, "0.7 px ≈ 1.1″ — good for 3-minute subs");
  eq(r.tone, "good");
  eq(r.phaseText, "Guiding well — you can relax");
});

test("phase-absent fallback: guiding:false -> Ready to guide", () => {
  const r = guideNarration(baseInput({ phase: undefined, guiding: false }));
  eq(r.phaseText, "Ready to guide");
  eq(r.tone, "neutral");
  eq(r.verdict, null);
});

test('phase "" (empty string) treated as absent -> falls back to guiding bool', () => {
  const r = guideNarration(baseInput({ phase: "", guiding: true }));
  eq(r.verdict, "0.7 px ≈ 1.1″ — good for 3-minute subs");
  eq(r.tone, "good");
});

// ---------------------------------------------------------------- connected, not guiding
test("connected, not guiding (idle/unknown) -> Ready to guide", () => {
  const r = guideNarration(baseInput({ phase: "idle", guiding: false }));
  eq(r.phaseText, "Ready to guide");
  eq(r.tone, "neutral");
  eq(r.verdict, null);
});

// ---------------------------------------------------------------- band boundaries
test("band boundary: arc = 0.6 -> excellent (good tone)", () => {
  const r = guideNarration(baseInput({ rmsTotal: 0.6, imageScale: 0 }));
  eq(r.verdict, "0.6″ — excellent — long subs are fine");
  eq(r.tone, "good");
});

test("band boundary: arc = 1.2 -> good for 3-minute subs (good tone)", () => {
  const r = guideNarration(baseInput({ rmsTotal: 1.2, imageScale: 0 }));
  eq(r.verdict, "1.2″ — good for 3-minute subs");
  eq(r.tone, "good");
});

test("band boundary: arc = 2.0 -> OK for short subs (warn tone)", () => {
  const r = guideNarration(baseInput({ rmsTotal: 2.0, imageScale: 0 }));
  eq(r.verdict, "2.0″ — OK for short subs");
  eq(r.tone, "warn");
});

test("band boundary: arc = 2.01 -> high, expect trailing (bad tone)", () => {
  const r = guideNarration(baseInput({ rmsTotal: 2.01, imageScale: 0 }));
  eq(r.verdict, "2.0″ — high — expect some star trailing");
  eq(r.tone, "bad");
  eq(r.phaseText, "Guiding, but the error is high");
});

// ---------------------------------------------------------------- guiding* also covers phase==="guiding" with guiding:false
test('phase "guiding" with guiding:false still reads as guiding* (phase wins)', () => {
  const r = guideNarration(baseInput({ phase: "guiding", guiding: false }));
  eq(r.verdict, "0.7 px ≈ 1.1″ — good for 3-minute subs");
  eq(r.tone, "good");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nguideNarration.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
