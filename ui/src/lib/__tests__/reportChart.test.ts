// Unit tests for the report-viewer pure shaping lib (report viewer spec §3
// Task 1). Same tiny inline-assert harness as eta.test.ts / healthStrip.test.ts —
// no vitest/jest wired into this UI. Compiles under `tsc -b`; run directly with a
// TS-aware runner:  npx tsx src/lib/__tests__/reportChart.test.ts

import { trendGeom, pushLiveSample, pickSeries, endReasonMeta, type LiveSample } from "../reportChart";

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
function deepEq<T>(a: T, b: T, msg = ""): void {
  const as = JSON.stringify(a), bs = JSON.stringify(b);
  if (as !== bs) throw new Error(`${msg} expected ${bs}, got ${as}`);
}

// ---------------------------------------------------------------- trendGeom
test("trendGeom: empty values -> null", () => {
  eq(trendGeom([], 100, 50), null, "empty");
});

test("trendGeom: single value -> non-null, path starts M, padded so 5 sits inside band", () => {
  const g = trendGeom([5], 100, 50);
  assert(g !== null, "non-null");
  assert(g!.d.startsWith("M"), "path starts with M");
  assert(g!.yMin < 5 && g!.yMax > 5, "flat series padded around the value");
});

test("trendGeom: 3 ascending values -> one M + two L, x spans 0..w, higher value -> smaller y", () => {
  const g = trendGeom([1, 2, 3], 100, 50);
  assert(g !== null, "non-null");
  const d = g!.d;
  const mCount = (d.match(/M/g) ?? []).length;
  const lCount = (d.match(/L/g) ?? []).length;
  eq(mCount, 1, "exactly one M");
  eq(lCount, 2, "exactly two L");
  // parse the three points' x,y in order
  const pts = d.split(" ").reduce<{ x: number; y: number }[]>((acc, tok, i, arr) => {
    if (i % 2 === 0) {
      const x = Number(tok.slice(1));
      const y = Number(arr[i + 1]);
      acc.push({ x, y });
    }
    return acc;
  }, []);
  eq(pts.length, 3, "three points parsed");
  eq(pts[0].x, 0, "first x == 0");
  eq(pts[1].x, 50, "middle x == 50");
  eq(pts[2].x, 100, "last x == 100");
  assert(pts[2].y < pts[0].y, "higher value renders at smaller y (SVG y grows downward)");
  assert(g!.yMax > g!.yMin, "yMax > yMin");
});

test("trendGeom: negatives handled (sensor temp) -> yMin below the most-negative value", () => {
  const g = trendGeom([-10, -5, 0], 100, 50);
  assert(g !== null, "non-null");
  assert(g!.yMin < -10, "yMin padded below the most negative input");
});

// ---------------------------------------------------------------- pushLiveSample
function sample(t: number, hfr: number | null = null): LiveSample {
  return { t, hfr, stars: null, rms: null, temp: null };
}

test("pushLiveSample: within cap just appends", () => {
  const r = pushLiveSample([sample(1), sample(2)], sample(3), 5);
  eq(r.length, 3, "length");
  deepEq(r.map((s) => s.t), [1, 2, 3], "order preserved");
});

test("pushLiveSample: past cap keeps the LAST cap entries, most-recent last", () => {
  let ring: LiveSample[] = [];
  for (let i = 1; i <= 7; i++) ring = pushLiveSample(ring, sample(i), 5);
  eq(ring.length, 5, "capped length");
  deepEq(ring.map((s) => s.t), [3, 4, 5, 6, 7], "keeps last 5, newest last");
});

// ---------------------------------------------------------------- pickSeries
test('pickSeries: drops nulls for the requested key', () => {
  const ring: LiveSample[] = [
    { t: 1, hfr: 1, stars: null, rms: null, temp: null },
    { t: 2, hfr: null, stars: null, rms: null, temp: null },
    { t: 3, hfr: 3, stars: null, rms: null, temp: null },
  ];
  deepEq(pickSeries(ring, "hfr"), [1, 3], "hfr series");
});

// ---------------------------------------------------------------- endReasonMeta
test("endReasonMeta: complete -> COMPLETE/good", () => {
  deepEq(endReasonMeta("complete"), { word: "COMPLETE", tone: "good" });
});
test("endReasonMeta: dawn_cutoff -> DAWN CUTOFF/good", () => {
  deepEq(endReasonMeta("dawn_cutoff"), { word: "DAWN CUTOFF", tone: "good" });
});
test("endReasonMeta: aborted -> ABORTED/warn", () => {
  deepEq(endReasonMeta("aborted"), { word: "ABORTED", tone: "warn" });
});
test("endReasonMeta: quality -> QUALITY STOP/warn", () => {
  deepEq(endReasonMeta("quality"), { word: "QUALITY STOP", tone: "warn" });
});
test("endReasonMeta: cooling_skip -> COOLING SKIP/warn", () => {
  deepEq(endReasonMeta("cooling_skip"), { word: "COOLING SKIP", tone: "warn" });
});
test("endReasonMeta: unsafe -> UNSAFE — STOPPED/bad", () => {
  deepEq(endReasonMeta("unsafe"), { word: "UNSAFE — STOPPED", tone: "bad" });
});
test("endReasonMeta: error -> ERROR/bad", () => {
  deepEq(endReasonMeta("error"), { word: "ERROR", tone: "bad" });
});
test("endReasonMeta: null -> IN PROGRESS/warn", () => {
  deepEq(endReasonMeta(null), { word: "IN PROGRESS", tone: "warn" });
});
test('endReasonMeta: unknown "weird" -> uppercased word/warn', () => {
  deepEq(endReasonMeta("weird"), { word: "WEIRD", tone: "warn" });
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nreportChart.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
