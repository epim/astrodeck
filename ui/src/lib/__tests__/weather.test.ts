// weather.test.ts — normalizeWeather (stale fail-closed, clamping, missing
// series) + breachSpans golden cases + fmtHm/agoLabel (weather spec §9/§14).
// Run with:  npx tsx src/lib/__tests__/weather.test.ts   (from ui/)

import type { WeatherState } from "../../types";
import {
  agoLabel, breachSpans, fmtHm, groupEndLabels, normalizeWeather, OPEN_METEO_STALE_S,
  weatherSourceLabel,
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

test("groupEndLabels: coincident series (all-zero) merge into one row", () => {
  const rows = groupEndLabels(
    [{ name: "total", y: 100 }, { name: "low", y: 100 }, { name: "mid", y: 100 }, { name: "high", y: 100 }],
    3,
  );
  assert(rows.length === 1, `expected 1 row, got ${rows.length}`);
  assert(rows[0].label === "total+low+mid+high", `label=${rows[0].label}`);
  assert(rows[0].y === 100, `mean y=${rows[0].y}`);
});

test("groupEndLabels: well-separated series stay on their own rows", () => {
  const rows = groupEndLabels(
    [{ name: "total", y: 10 }, { name: "low", y: 40 }, { name: "mid", y: 90 }, { name: "high", y: 130 }],
    6,
  );
  assert(rows.length === 4, `expected 4 rows, got ${rows.length}`);
  assert(rows.map((r) => r.label).join(",") === "total,low,mid,high", JSON.stringify(rows));
});

test("groupEndLabels: partial collision — only the near pair merges", () => {
  // sorted by y: high(10), low(40), total(100), mid(103) — total/mid are 3px
  // apart (<= minGap 6) and merge; low sits 60px from total, stays separate.
  const rows = groupEndLabels(
    [{ name: "total", y: 100 }, { name: "low", y: 40 }, { name: "mid", y: 103 }, { name: "high", y: 10 }],
    6,
  );
  assert(rows.length === 3, `expected 3 rows, got ${rows.length}: ${JSON.stringify(rows)}`);
  assert(rows[0].label === "high" && rows[1].label === "low", JSON.stringify(rows));
  assert(rows[2].label === "total+mid", `label=${rows[2].label}`);
  assert(rows[2].y === 101.5, `mean y=${rows[2].y}`);
});

test("groupEndLabels: transitive chain merge — adjacent gaps within minGap join even when the extremes exceed it", () => {
  // a-b and b-c are each exactly minGap apart; a-c (10) exceeds it. The
  // documented chain rule merges all three into one row.
  const rows = groupEndLabels(
    [{ name: "a", y: 0 }, { name: "b", y: 5 }, { name: "c", y: 10 }],
    5,
  );
  assert(rows.length === 1, `expected 1 chained row, got ${rows.length}: ${JSON.stringify(rows)}`);
  assert(rows[0].label === "a+b+c", `label=${rows[0].label}`);
  assert(rows[0].y === 5, `mean y=${rows[0].y}`);
});

test("groupEndLabels: gap exactly at minGap merges; empty input -> []", () => {
  const rows = groupEndLabels([{ name: "a", y: 0 }, { name: "b", y: 5 }], 5);
  assert(rows.length === 1, `boundary gap should merge, got ${rows.length}`);
  const justOver = groupEndLabels([{ name: "a", y: 0 }, { name: "b", y: 5.01 }], 5);
  assert(justOver.length === 2, `gap just over minGap should NOT merge, got ${justOver.length}`);
  assert(groupEndLabels([], 5).length === 0, "empty input");
});

test("weatherSourceLabel: mechanically truthful — Astrospheric only when it's actually flowing", () => {
  assert(weatherSourceLabel(false) === "Open-Meteo", "Open-Meteo alone");
  assert(weatherSourceLabel(true) === "Open-Meteo + Astrospheric", "both sources named");
});

console.log(`weather.test: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
