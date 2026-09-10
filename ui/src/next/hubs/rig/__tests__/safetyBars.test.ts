// safetyBars.test.ts - one bar per key the monitor ACTUALLY sent.
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/safetyBars.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS GUARDS (plan deviation E11). The design asks for five bars - rain,
// wind, cloud, power, humidity - with red limit ticks. `SafetyReading.detail` is
// an OPTIONAL `Record<string, number>` that a given monitor may not populate at
// all, and `SafetyConfig` carries no per-input thresholds. So the two things
// that must never happen are a bar with no reading behind it and a tick with no
// limit behind it, and both have an assertion here.
//
// Pure module, printed tally + the counts export (shell-and-tests.md section 4).

import {
  NO_LIMIT_SUB, readingAgeS, safetyBars, safetyLimitsFromConfig, safetyLive,
  streakLine,
} from "../lib/safetyBars";
import type { SafetyReading } from "../../../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
function near(got: number, want: number, msg: string, tol = 1e-6): void {
  if (Math.abs(got - want) > tol) throw new Error(`${msg} (expected ${want}, got ${got})`);
}

function reading(over: Partial<SafetyReading> = {}): SafetyReading {
  return { is_safe: true, reason: "", source: "Boltwood II", stale: false, ts: 1000, ...over };
}

// --------------------------------------------------- nothing in, nothing out

test("no detail produces NO bars at all", () => {
  eq(safetyBars(undefined).length, 0, "undefined detail invented rows");
  eq(safetyBars(null).length, 0, "null detail invented rows");
  eq(safetyBars({}).length, 0, "an empty detail dict invented rows");
});

test("the design's five bars are not manufactured from an empty reading", () => {
  // The whole point of the module. A row list built from a fixture would carry
  // these labels; a row list built from the device carries none of them.
  const labels = safetyBars({}).map((r) => r.label);
  for (const fabricated of ["RAIN", "WIND", "CLOUD", "POWER", "HUMIDITY"]) {
    assert(!labels.includes(fabricated),
      `${fabricated} appeared with no reading behind it - the fixture is being drawn`);
  }
});

// ---------------------------------------------------------- one key, one bar

test("one key in, one bar out, in the device's own order", () => {
  const rows = safetyBars({ wind_kmh: 12, humidity: 64 });
  eq(rows.length, 2, "the row count does not match the keys the device sent");
  eq(rows[0].key, "wind_kmh", "the rows are not in the order the device sent them");
  eq(rows[0].label, "WIND", "wind_kmh did not map to the WIND label");
  eq(rows[1].label, "HUMIDITY", "humidity did not map to the HUMIDITY label");
});

test("the value text carries the unit, because the bar cannot", () => {
  eq(safetyBars({ humidity: 64 })[0].text, "64%", "the humidity row lost its unit");
  eq(safetyBars({ wind_kmh: 12.5 })[0].text, "12.5 km/h", "the wind row lost its unit");
  eq(safetyBars({ sky_temp: -21.4 })[0].text, "-21.4 C", "a negative sky temperature did not survive");
});

test("a bounded key fills its track in proportion", () => {
  near(safetyBars({ humidity: 64 })[0].pct, 0.64, "the humidity fill is not the reading");
  near(safetyBars({ wind_kmh: 50 })[0].pct, 0.5, "the wind fill is not the reading");
  // Signed scales: sky_temp runs -40..10, so -15 sits half way.
  near(safetyBars({ sky_temp: -15 })[0].pct, 0.5, "a signed scale did not map through its minimum");
});

test("a value past the end of the scale clamps rather than overflowing", () => {
  near(safetyBars({ humidity: 140 })[0].pct, 1, "an over-range value ran past the end of the track");
  near(safetyBars({ humidity: -5 })[0].pct, 0, "an under-range value ran off the front of the track");
});

// ------------------------------------------------------------- unknown keys

test("an unknown key is rendered, never dropped", () => {
  const rows = safetyBars({ sky_quality_mpsas: 20.9 });
  eq(rows.length, 1, "an unrecognised reading was thrown away");
  eq(rows[0].label, "SKY QUALITY MPSAS", "an unknown key did not fall back to its own name");
  eq(rows[0].known, false, "an unknown key is being reported as one we understand");
  near(rows[0].pct, 0.5, "an unknown key was given a scale it does not have");
  assert(/the number is the reading/.test(rows[0].sub),
    `an unscaled bar does not say the number is the truth: ${rows[0].sub}`);
});

test("a key with no conventional scale parks at the half mark and says so", () => {
  const v = safetyBars({ voltage: 12.1 })[0];
  eq(v.text, "12.1 V", "the voltage row lost its unit");
  near(v.pct, 0.5, "volts were given a full-scale reading nobody can justify");
});

test("a non-numeric or infinite value is skipped, not drawn as zero", () => {
  const rows = safetyBars({ humidity: 64, broken: Number.NaN, wild: Number.POSITIVE_INFINITY } as never);
  eq(rows.length, 1, "a NaN or Infinity reading was drawn as a bar");
  eq(rows[0].key, "humidity", "the wrong row survived");
});

// ---------------------------------------------------------------- the ticks

test("no limit from config means NO tick and a sub that says why", () => {
  const rows = safetyBars({ humidity: 64, wind_kmh: 12 }, safetyLimitsFromConfig());
  for (const r of rows) {
    eq(r.limPct, null, `${r.label} drew a red tick with no limit behind it`);
    eq(r.sub, NO_LIMIT_SUB, `${r.label}'s sub does not say who owns the limit`);
  }
});

test("SafetyConfig supplies no per-input limits today, and says so by returning none", () => {
  eq(Object.keys(safetyLimitsFromConfig()).length, 0,
    "a limit appeared from a config block that has no per-input threshold in it");
});

test("a SOURCED limit draws its tick at the right place and names the number", () => {
  const r = safetyBars({ humidity: 64 }, { humidity: 80 })[0];
  near(r.limPct ?? -1, 0.8, "the tick is not at the limit");
  eq(r.sub, "limit 80%", "a real limit is not printed beside its tick");
  eq(r.tone, "accent", "a reading under its limit was coloured as a breach");
});

test("a reading at or past its limit is toned bad - the only colour claim we can make", () => {
  eq(safetyBars({ humidity: 85 }, { humidity: 80 })[0].tone, "bad",
    "a reading past its limit is not flagged");
  eq(safetyBars({ humidity: 80 }, { humidity: 80 })[0].tone, "bad",
    "a reading exactly at its limit is not flagged");
});

// -------------------------------------------------------- streak + live line

test("the streak line counts toward the configured hysteresis", () => {
  eq(streakLine(2, 3), "2 of 3 unsafe reads", "the streak line does not show the counter");
  eq(streakLine(0, 3), null, "a zero streak printed a line about unsafe reads");
});

test("the live line says which of the four states the monitor is in", () => {
  // `ts` is EPOCH SECONDS and `now` is milliseconds, so a ts of 1000 is 59 s
  // old at 1_059_000 ms. Getting that conversion wrong is the whole reason the
  // age is asserted as a string rather than a number.
  const now = 1_059_000;
  eq(safetyLive(false, null, now).text, "no monitor assigned", "a missing monitor is not named");
  eq(safetyLive(false, reading({ stale: true }), now).text,
    "stale - the monitor stopped answering", "a stale read is not named");
  eq(safetyLive(true, reading({ is_safe: false, reason: "rain detected" }), now).text,
    "UNSAFE - rain detected", "an unsafe verdict does not carry the rig's own reason");
  eq(safetyLive(true, reading(), now).text, "safe · Boltwood II · last read 59s ago",
    "a safe verdict does not carry the source and the age");
});

test("the live line's tone is never good unless the rig says safe", () => {
  eq(safetyLive(true, reading(), 1_059_000).tone, "good", "a safe read is not toned good");
  eq(safetyLive(true, reading({ is_safe: false }), 1_059_000).tone, "bad", "an unsafe read is not toned bad");
  eq(safetyLive(false, reading({ stale: true }), 1_059_000).tone, "warn", "a stale read is not toned warn");
  eq(safetyLive(false, null, 1_059_000).tone, "warn", "a missing monitor is not toned warn");
});

test("a reading from the future reads as 0s old, not as a negative age", () => {
  eq(readingAgeS(reading({ ts: 5000 }), 1000), 0, "clock skew produced a negative age");
  eq(readingAgeS(null, 1000), null, "an absent reading produced an age");
});

const total = passed + failed;
console.log(`safetyBars.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
