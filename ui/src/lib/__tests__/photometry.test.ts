// Unit tests for the F-A photometry/SNR core (spec docs/superpowers/specs/2026-07-23-photometry-snr-design.md §3).
//
// There is no vitest/jest wired into this UI (build is `tsc -b && vite build`),
// so these use the same tiny inline-assert harness as eta.test.ts / foundation.test.ts.
// They compile under `tsc -b` and run directly with a TS-aware runner, e.g.
//   npx tsx src/lib/__tests__/photometry.test.ts

import {
  subNoise, skyElectronsPerSub, skyRateEPerSec, skyLimitedSubSeconds,
  subLengthVerdict, subSnr, stackedSnr, subsForStackedSnr, moreSubsForSnrMultiple,
  projectedIntegrationSeconds, integrationByFilter, suggestSubLength,
  perSubSnrFromFlux, starSnrWord,
  SKY_LIMIT_FACTOR, SUB_LONG_MULT,
} from "../photometry";

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
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function near(a: number, b: number, tol: number, msg = ""): void {
  if (Math.abs(a - b) > tol) throw new Error(`${msg} expected ~${b}, got ${a}`);
}

// ---------------------------------------------------------------- constants
test("constants: sky-limit factor and long-sub multiple", () => {
  eq(SKY_LIMIT_FACTOR, 10, "SKY_LIMIT_FACTOR");
  eq(SUB_LONG_MULT, 4, "SUB_LONG_MULT");
});

// ---------------------------------------------------------------- subNoise
test("subNoise: read dominates a short sub", () => {
  const n = subNoise(4, 2);            // sky=4 e-, read=2 e- => var=8, read²=4
  near(n.totalNoiseE, Math.sqrt(8), 1e-9, "total");
  near(n.readFraction, 0.5, 1e-9, "read fraction");
});
test("subNoise: sky-limited sub => read fraction small", () => {
  const n = subNoise(400, 2);         // var=404, read²=4
  assert(n.readFraction < 0.02, "read fraction tiny when sky-limited");
});
test("subNoise: clamps negatives", () => {
  const n = subNoise(-5, -3);
  eq(n.skyE, 0, "sky clamp"); eq(n.readE, 0, "read clamp"); eq(n.readFraction, 1, "0/0 => 1");
});

// ---------------------------------------------------------- skyElectronsPerSub / rate
test("skyElectronsPerSub: (median-bias)*egain, clamped", () => {
  near(skyElectronsPerSub(1200, 1000, 0.25), 50, 1e-9, "50 e-");
  eq(skyElectronsPerSub(900, 1000, 0.25), 0, "below bias => 0");
});
test("skyRateEPerSec: divide guard", () => {
  near(skyRateEPerSec(50, 10), 5, 1e-9, "5 e-/s");
  eq(skyRateEPerSec(50, 0), 0, "zero exposure => 0");
});

// ---------------------------------------------------------------- sky-limited length
test("skyLimitedSubSeconds: factor*RN^2/rate", () => {
  near(skyLimitedSubSeconds(2, 5)!, (10 * 4) / 5, 1e-9, "8s");   // default factor 10
  near(skyLimitedSubSeconds(2, 5, 3)!, (3 * 4) / 5, 1e-9, "custom factor");
  eq(skyLimitedSubSeconds(2, 0), null, "no sky => null");
});

// ---------------------------------------------------------------- verdict bands
test("subLengthVerdict: bands around sky-limited", () => {
  eq(subLengthVerdict(30, 60), "too_short", "under");
  eq(subLengthVerdict(90, 60), "good", "within long-mult");
  eq(subLengthVerdict(60 * SUB_LONG_MULT + 1, 60), "long", "beyond");
  eq(subLengthVerdict(120, null), "unknown", "no estimate");
});

// ---------------------------------------------------------------- SNR + stacking
test("subSnr + stackedSnr: √N growth", () => {
  const n = subNoise(96, 2);          // total = sqrt(100) = 10
  near(subSnr(50, n), 5, 1e-9, "per-sub 5");
  near(stackedSnr(5, 4), 10, 1e-9, "×2 after 4 subs");
});
test("subsForStackedSnr: (target/perSub)^2 ceil", () => {
  eq(subsForStackedSnr(5, 20), 16, "16 subs for 20 from 5");
  eq(subsForStackedSnr(0, 20), null, "no signal => null");
});
test("moreSubsForSnrMultiple: exact √N law", () => {
  eq(moreSubsForSnrMultiple(30, 2), 90, "×2 => 4× total => +90");
  eq(moreSubsForSnrMultiple(30, 1), 0, "×1 => none");
});

// ---------------------------------------------------------------- integration
test("projectedIntegrationSeconds + integrationByFilter", () => {
  const steps = [
    { filter: "L", exposure_s: 120, count: 60 },
    { filter: "R", exposure_s: 180, count: 10 },
    { filter: "L", exposure_s: 60, count: 5 },
    { filter: null, exposure_s: 30, count: 2 },
  ];
  eq(projectedIntegrationSeconds(steps), 120*60 + 180*10 + 60*5 + 30*2, "sum");
  const by = integrationByFilter(steps);
  eq(by[0].filter, "L", "L first (insertion order)");
  eq(by[0].seconds, 120*60 + 60*5, "L merged");
  eq(by[2].filter, "—", "null bucket");
});

// ---------------------------------------------------------------- suggest assembler
test("suggestSubLength: happy path clamps + rounds", () => {
  const s = suggestSubLength({ medianAdu: 1200, biasAdu: 1000, egain: 0.25,
    readNoiseE: 2, exposureS: 10 });
  // skyE=50 => rate=5 e-/s => sky-limited=8s => clamped to min 5..600 => 8
  eq(s.ok, true, "ok"); eq(s.suggestedS, 8, "8s");
});
test("suggestSubLength: missing profile => honest not-ok", () => {
  const s = suggestSubLength({ medianAdu: 1200, biasAdu: 1000, egain: 0,
    readNoiseE: 2, exposureS: 10 });
  eq(s.ok, false, "not ok"); assert(s.reason.includes("gain"), "reason mentions gain");
});

// ------------------------------------------------ perSubSnrFromFlux (grab-bag b)
// Absolute per-SUB SNR from the server's background-subtracted star flux. The
// number must be reproducible by hand, and must ABSTAIN (never guess) when the
// photometry profile is unset.
test("perSubSnrFromFlux: known flux/gain/sky/read reproduces the hand-computed SNR", () => {
  // flux 1000 ADU x 2 e-/ADU = 2000 e- signal.
  // sky = (medianAdu 600 - bias 100) x 2 = 1000 e-;  read 5 e-
  // noise = sqrt(1000 + 25) = 32.0156...  -> snr = 2000 / 32.0156 = 62.47
  const r = perSubSnrFromFlux({ fluxAdu: 1000, egain: 2, biasAdu: 100, medianAdu: 600, readNoiseE: 5 });
  eq(r.ok, true, "ok");
  eq(r.signalE, 2000, "signal e-");
  near(r.noise!.skyE, 1000, 1e-9, "sky e-");
  near(r.noise!.totalNoiseE, Math.sqrt(1025), 1e-9, "total noise e-");
  near(r.snr, 2000 / Math.sqrt(1025), 1e-9, "per-sub snr");
  // sanity: this is PER SUB — stacking 25 of them is 5x better, not the same.
  near(stackedSnr(r.snr, 25), r.snr * 5, 1e-9, "stacked is sqrt(N) better");
});

test("perSubSnrFromFlux: abstains with an honest reason when the profile/flux is missing", () => {
  const cases: [string, Parameters<typeof perSubSnrFromFlux>[0], string][] = [
    ["no egain", { fluxAdu: 1000, egain: 0, biasAdu: 0, medianAdu: 600, readNoiseE: 5 }, "gain"],
    ["no read noise", { fluxAdu: 1000, egain: 2, biasAdu: 0, medianAdu: 600, readNoiseE: 0 }, "read noise"],
    ["no flux", { fluxAdu: 0, egain: 2, biasAdu: 0, medianAdu: 600, readNoiseE: 5 }, "flux"],
  ];
  for (const [name, inp, word] of cases) {
    const r = perSubSnrFromFlux(inp);
    eq(r.ok, false, name);
    eq(r.snr, 0, `${name}: no number`);
    eq(r.noise, null, `${name}: no decomposition`);
    assert(!!r.reason && r.reason.toLowerCase().includes(word), `${name}: reason mentions ${word}`);
  }
});

// The chip leads with a WORD so "~38" is never read as a score out of 100.
const SNR_WORDS: Array<[number, string]> = [
  [0, "faint"], [9.9, "faint"], [10, "usable"], [29.9, "usable"],
  [30, "strong"], [120, "strong"],
];
for (const [snr, word] of SNR_WORDS) {
  test(`starSnrWord(${snr}) is ${word}`, () => eq(starSnrWord(snr), word));
}

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nphotometry.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
