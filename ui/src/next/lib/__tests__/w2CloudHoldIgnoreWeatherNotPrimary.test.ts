// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w2CloudHoldIgnoreWeatherNotPrimary.test.ts - WP-17 (c), the lib half (#260).
// Pure: literal inputs, no DOM.
//
//   Run directly:  node --import tsx src/next/lib/__tests__/w2CloudHoldIgnoreWeatherNotPrimary.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// IGNORE WEATHER TONIGHT lifts only `WeatherService.veto_reason`'s forecast
// rain veto (weather.py: "RAIN VETOES. CLOUD DOES NOT."); it never touches the
// in-run cloud hold this card is about. Presenting it as the card's primary
// (highlighted) action, under its old name, told an operator at 2am that the
// highlighted button would clear the hold. It will not.
//
// NAMED MUTANT, run from a byte copy of incidents.ts and restored
// byte-identical afterwards (sha256 checked). The observed failure is quoted
// at the test it turns red.
//   C1 "restore the false primary"   `ignore_weather`'s `primary: true` and its
//                                     old "IGNORE WEATHER TONIGHT" label put back

import { deriveIncidents, type IncidentInputs } from "../incidents";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function assert(cond: boolean, m: string): void { if (!cond) throw new Error(m); }

const NOW = 1_700_000_000_000;

function baseInputs(): IncidentInputs {
  return {
    sequence: { state: "idle" },
    safety: { is_safe: true },
    wsPhase: "up",
    telemetryStale: false,
    wsLastEvent: NOW,
    mountOp: null,
    focus: null,
    lastAutofocusResult: null,
    guide: null,
    disk: { free_gb: 50, low: false, critical: false },
    cooler: null,
    logs: [],
    lastCaptureAtMs: null,
    expectedFrameS: null,
  };
}

function cloudHoldCard() {
  const inp = baseInputs();
  inp.sequence = { state: "holding", hold: { reason: "clouds", since: NOW - 60000 } };
  const list = deriveIncidents(inp, NOW);
  const c = list.find((i) => i.kind === "cloud");
  if (!c) throw new Error("precondition: no cloud card - the fixture is wrong, not the lib");
  return c;
}

test("precondition: the cloud-hold card carries both actions", () => {
  const c = cloudHoldCard();
  const ids = c.actions.map((a) => a.id).sort().join(",");
  assert(ids === "ignore_weather,wait", `actions: ${ids}`);
});

test("ignore_weather is not the cloud-hold card's primary action (#260)", () => {
  // C1 "restore the false primary", observed:
  //   x ignore_weather is not the cloud-hold card's primary action (#260):
  //   ignore_weather is marked primary on a hold it cannot touch
  const c = cloudHoldCard();
  const ignore = c.actions.find((a) => a.id === "ignore_weather")!;
  assert(!ignore.primary, "ignore_weather is marked primary on a hold it cannot touch");
});

test("WAIT is the recommended action on a cloud hold - it is what actually clears it", () => {
  const c = cloudHoldCard();
  const wait = c.actions.find((a) => a.id === "wait")!;
  assert(!!wait.primary, "no action on the cloud-hold card is marked primary");
});

test("ignore_weather's label names the forecast rain veto, not the in-run hold (#260)", () => {
  const c = cloudHoldCard();
  const ignore = c.actions.find((a) => a.id === "ignore_weather")!;
  assert(!/^IGNORE WEATHER TONIGHT$/i.test(ignore.label),
    `the old false-affordance label is back: "${ignore.label}"`);
  assert(/forecast/i.test(ignore.label) && /rain/i.test(ignore.label),
    `the label does not name the forecast rain veto it actually lifts: "${ignore.label}"`);
});

test("control: the DEFERRED variant still offers WAIT only - it never had ignore_weather", () => {
  const inp = baseInputs();
  inp.sequence = {
    state: "running",
    detail: "waiting for M31 to rise",
    sky: {
      cloudy: true, text: "cloudy, from a reading 40s old", holding: false,
      hold_deferred: "the frames say the sky has closed in, with no target set "
        + "up to judge it from, so no cloud hold is open",
    },
  };
  const c = deriveIncidents(inp, NOW).find((i) => i.kind === "cloud")!;
  assert(c != null, "precondition: no deferred card");
  const ids = c.actions.map((a) => a.id).join(",");
  assert(ids === "wait", `the deferred card grew ignore_weather: ${ids}`);
});

console.log(`${passed} passed, ${failed} failed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total: passed + failed };
