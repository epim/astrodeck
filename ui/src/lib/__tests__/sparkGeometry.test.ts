// sparkGeometry.test.ts — mini altitude-chart geometry for the Plan tab's
// per-target cards (wave-3 §3). Run with:
//   npx tsx src/lib/__tests__/sparkGeometry.test.ts   (from ui/)

import { buildSparkGeometry, ALT_MAX } from "../visibility";
import type { VisibilityNight, VisibilitySample, MoonInfo } from "../../types";

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
function near(a: number, b: number, tol: number, msg: string): void {
  assert(Math.abs(a - b) <= tol, `${msg} (got ${a}, want ${b} ±${tol})`);
}

const W = 120;
const H = 28;
const SPAN = 4 * 3600; // 4h

const moon: MoonInfo = {
  illumination: 0.3,
  phase_name: "Waxing Crescent",
  alt: 10,
  az: 90,
  separation_deg: 60,
  rise_unix: null,
  set_unix: null,
};

function sample(t_unix: number, alt: number): VisibilitySample {
  return { t_unix, alt, moon_alt: 5, sun_alt: -20 };
}

// 5 samples over 4h, alt rising 10 -> 60 deg.
const samples: VisibilitySample[] = [
  sample(0, 10),
  sample(SPAN * 0.25, 22.5),
  sample(SPAN * 0.5, 35),
  sample(SPAN * 0.75, 47.5),
  sample(SPAN, 60),
];

function baseNight(overrides: Partial<VisibilityNight> = {}): VisibilityNight {
  return {
    date: "2026-07-13",
    transit_unix: SPAN * 0.5,
    transit_alt: 60,
    transit_in_daylight: false,
    dark_start_unix: SPAN * 0.25, // dark window covers the middle half
    dark_end_unix: SPAN * 0.75,
    darkness_kind: "astronomical",
    samples,
    moon,
    best_window: null,
    alt_limit_deg: 30,
    never_rises_above_limit: false,
    ...overrides,
  };
}

test("altPath starts at M0.0 and has 5 segments", () => {
  const geo = buildSparkGeometry(baseNight(), W, H);
  assert(geo.altPath !== null, "altPath should not be null");
  assert(geo.altPath!.startsWith("M0.0"), `got "${geo.altPath}"`);
  const segCount = (geo.altPath!.match(/[ML]/g) || []).length;
  assert(segCount === 5, `expected 5 segments, got ${segCount} ("${geo.altPath}")`);
});

test("altLimitY matches h - limit/ALT_MAX*h", () => {
  const geo = buildSparkGeometry(baseNight(), W, H);
  const want = H - (30 / ALT_MAX) * H;
  assert(geo.altLimitY === want, `got ${geo.altLimitY}, want ${want}`);
});

test("dark band spans the middle half within 0.5px", () => {
  const geo = buildSparkGeometry(baseNight(), W, H);
  assert(geo.darkBand !== null, "darkBand should not be null");
  near(geo.darkBand!.x, W * 0.25, 0.5, "darkBand.x");
  near(geo.darkBand!.w, W * 0.5, 0.5, "darkBand.w");
});

test("never_rises_above_limit -> altPath null", () => {
  const geo = buildSparkGeometry(baseNight({ never_rises_above_limit: true }), W, H);
  assert(geo.altPath === null, `got "${geo.altPath}"`);
  assert(geo.darkBand === null, "darkBand should be null");
});

test("empty samples -> null path, no throw", () => {
  const geo = buildSparkGeometry(baseNight({ samples: [] }), W, H);
  assert(geo.altPath === null, `got "${geo.altPath}"`);
  assert(geo.darkBand === null, "darkBand should be null with no samples");
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nsparkGeometry.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}
export const result = { passed, failed, total };
