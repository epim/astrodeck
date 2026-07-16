// weather.test.ts — normalizeWeather (stale fail-closed, clamping, missing
// series) + breachSpans golden cases + fmtHm/agoLabel (weather spec §9/§14).
// Run with:  npx tsx src/lib/__tests__/weather.test.ts   (from ui/)

import type { WeatherState } from "../../types";
import {
  agoLabel, breachSpans, fmtHm, normalizeWeather, OPEN_METEO_STALE_S,
} from "../weather";

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

const NOW = 1_700_000_000;

const base: WeatherState = {
  enabled: true,
  fetched_ts: NOW - 60,
  stale: false,
  ignore_tonight: false,
  threshold_pct: 50,
  sustain_minutes: 30,
  forecast: {
    times: ["2026-07-16T01:00:00Z", "2026-07-16T01:15:00Z"],
    cloud: [10, 120],
    cloud_low: [0, -5],
    cloud_mid: [0, 0],
    cloud_high: [10, 100],
  },
  astrospheric: null,
  alert: null,
};

test("fresh payload stays fresh; percentages clamp 0-100", () => {
  const w = normalizeWeather(base, NOW)!;
  assert(w.stale === false, `stale=${w.stale}`);
  assert(w.forecast!.cloud[1] === 100, `clamp high ${w.forecast!.cloud[1]}`);
  assert(w.forecast!.cloud_low[1] === 0, `clamp low ${w.forecast!.cloud_low[1]}`);
});

test("stale derived FAIL-CLOSED from fetched_ts age (> 45 min)", () => {
  const w = normalizeWeather(
    { ...base, stale: false, fetched_ts: NOW - OPEN_METEO_STALE_S - 1 }, NOW)!;
  assert(w.stale === true, "old fetched_ts must derive stale");
  const w2 = normalizeWeather({ ...base, fetched_ts: null, stale: false }, NOW)!;
  assert(w2.stale === true, "missing fetched_ts must derive stale");
});

test("missing series tolerated (padded to times length with 0)", () => {
  const raw = {
    ...base,
    forecast: { ...base.forecast!, cloud_mid: undefined },
  } as unknown as WeatherState;
  const w = normalizeWeather(raw, NOW)!;
  assert(w.forecast!.cloud_mid.length === 2, "padded to n");
  assert(w.forecast!.cloud_mid[0] === 0, "pad value 0");
});

test("null payload -> null; astrospheric staleness derived (> 12 h)", () => {
  assert(normalizeWeather(null, NOW) === null, "null passthrough");
  const w = normalizeWeather({
    ...base,
    astrospheric: {
      times: ["2026-07-16T00:00:00Z"], seeing: [2], transparency: [21],
      fetched_ts: NOW - 13 * 3600, stale: false, credits_used_today: 20,
    },
  }, NOW)!;
  assert(w.astrospheric!.stale === true, "13 h old astrospheric is stale");
});

test("breachSpans: consecutive-sample rule (sustain 30 -> 2 samples)", () => {
  assert(breachSpans([40, 60, 40, 40], 50, 30).length === 0,
    "single sample is not sustained");
  const spans = breachSpans([40, 60, 70, 40, 80, 90, 95, 10], 50, 30);
  assert(spans.length === 2, `spans=${JSON.stringify(spans)}`);
  assert(spans[0].start === 1 && spans[0].end === 2, "first span 1-2");
  assert(spans[1].start === 4 && spans[1].end === 6, "second span 4-6");
});

test("breachSpans: sustain 15 -> single sample; run reaching array end", () => {
  const spans = breachSpans([0, 0, 99], 50, 15);
  assert(spans.length === 1 && spans[0].start === 2 && spans[0].end === 2,
    "tail run counted");
});

test("fmtHm shape + garbage guard; agoLabel fresh vs stale copy", () => {
  assert(/^\d{2}:\d{2}$/.test(fmtHm("2026-07-16T04:05:00Z")), "hh:mm shape");
  assert(fmtHm("garbage") === "--:--", "garbage guarded");
  assert(agoLabel(NOW - 720, NOW) === "updated 12 min ago", "fresh copy");
  assert(agoLabel(NOW - 2 * 3600, NOW).startsWith("STALE — last fetch 2.0 h"),
    "stale copy");
  assert(agoLabel(null, NOW) === "STALE — never fetched", "never fetched");
});

console.log(`weather.test: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
