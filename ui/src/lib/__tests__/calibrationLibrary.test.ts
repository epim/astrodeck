// Unit tests for PRO-1's pure calibration-library helpers (calibration-library
// spec §6). Same tiny inline-assert harness as eta.test.ts — compiles under
// `tsc -b`, runs with: npx tsx src/lib/__tests__/calibrationLibrary.test.ts
//
// planCalibrationGaps is the CLIENT MIRROR of matcher.py; the tolerance cases
// below parallel test_calibration_matcher.py so the two sides can't silently
// drift (Open Decision D3).

import {
  masterRowSummary,
  groupMasters,
  planCalibrationGaps,
  type MasterRow,
  type CoverageTol,
} from "../calibrationLibrary";
import type { FrameType } from "../calibration";
import type { SequencePlan } from "../../types";

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

// ---------------------------------------------------------------- builders
const TOL: CoverageTol = { exposureTolPct: 5, tempTolC: 2 };

function master(over: Partial<MasterRow> & { frame_type: FrameType }): MasterRow {
  return {
    id: "m",
    exposure_s: 300,
    gain: 100,
    offset: 30,
    temp_c: -10,
    binning: 1,
    filter: "",
    frame_count: 20,
    built_ts: 0,
    ...over,
  };
}
const dark = (over: Partial<MasterRow> = {}) => master({ frame_type: "Dark", ...over });
const flat = (filter: string, over: Partial<MasterRow> = {}) =>
  master({ frame_type: "Flat", filter, exposure_s: 2, ...over });
const bias = (over: Partial<MasterRow> = {}) =>
  master({ frame_type: "Bias", exposure_s: 0, filter: "", ...over });

function plan(
  steps: { exposure_s: number; gain?: number; offset?: number; binning?: number; filter?: string | null }[],
  cool_to: number | null = -10,
): SequencePlan {
  return {
    cool_to,
    targets: [
      {
        name: "M31",
        calibration: false,
        steps: steps.map((s) => ({
          filter: s.filter ?? null,
          exposure_s: s.exposure_s,
          gain: s.gain ?? 100,
          offset: s.offset ?? 30,
          binning: s.binning ?? 1,
          count: 10,
          frame_type: "Light",
        })),
      },
    ],
  } as unknown as SequencePlan;
}

// ---------------------------------------------------------------- masterRowSummary
test("masterRowSummary: dark shows exposure, temp, bin, count", () => {
  eq(masterRowSummary(dark({ frame_count: 25 })), "300s · gain 100 · -10°C · bin1 · ×25");
});
test("masterRowSummary: bias DROPS the exposure", () => {
  const s = masterRowSummary(bias({ frame_count: 50 }));
  assert(!s.includes("0s"), `bias summary must not show exposure: ${s}`);
  eq(s, "gain 100 · -10°C · bin1 · ×50");
});
test("masterRowSummary: flat shows its filter, no temp when null", () => {
  eq(masterRowSummary(flat("Ha", { temp_c: null, frame_count: 30 })),
     "2s · gain 100 · bin1 · Ha · ×30");
});

// ---------------------------------------------------------------- groupMasters
test("groupMasters: stable Dark → Flat → Bias order, empties dropped", () => {
  const groups = groupMasters([flat("Ha"), dark(), bias(), dark({ id: "d2" })]);
  eq(groups.map((g) => g.type).join(","), "Dark,Flat,Bias", "order");
  eq(groups[0].rows.length, 2, "two darks");
  eq(groups[1].rows.length, 1, "one flat");
});
test("groupMasters: a type with no rows is omitted", () => {
  const groups = groupMasters([dark()]);
  eq(groups.length, 1, "only Dark group");
  eq(groups[0].type, "Dark", "Dark");
});

// ---------------------------------------------------------------- planCalibrationGaps
test("gaps: empty library never nags", () => {
  eq(planCalibrationGaps(plan([{ exposure_s: 300, filter: "Ha" }]), [], TOL).missing.length, 0,
     "no masters => no gaps");
});
test("gaps: fully covered => no missing", () => {
  const masters = [dark(), flat("Ha")];
  eq(planCalibrationGaps(plan([{ exposure_s: 300, filter: "Ha" }]), masters, TOL).missing.length, 0,
     "dark + matching flat covers it");
});
test("gaps: missing flat is reported with its filter", () => {
  const { missing } = planCalibrationGaps(
    plan([{ exposure_s: 300, filter: "OIII" }]), [dark(), flat("Ha")], TOL);
  eq(missing.length, 1, "one gap kind");
  assert(missing[0].startsWith("flats"), `expected flats gap, got ${missing[0]}`);
  assert(missing[0].includes("OIII"), `must name the filter: ${missing[0]}`);
});
test("gaps: dark within +2.3% exposure tolerance is covered", () => {
  // 307 is +2.33% of 300 => within 5% (mirrors matcher.py's dark(307) case).
  const { missing } = planCalibrationGaps(
    plan([{ exposure_s: 307, filter: "Ha" }]), [dark(), flat("Ha")], TOL);
  eq(missing.length, 0, "307s light covered by a 300s dark within 5%");
});
test("gaps: dark 33% off exposure is NOT covered", () => {
  // 400 vs 300 is +33% => out of tolerance => dark missing (matcher.py dark(400)).
  const { missing } = planCalibrationGaps(
    plan([{ exposure_s: 400, filter: "Ha" }]), [dark(), flat("Ha")], TOL);
  assert(missing.some((s) => s.startsWith("darks")), `expected darks gap: ${missing.join("; ")}`);
});
test("gaps: temp beyond ±2°C tolerance drops the dark match", () => {
  // light cooled to -15 vs a -10 dark => 5°C apart => out of the 2°C window.
  const { missing } = planCalibrationGaps(
    plan([{ exposure_s: 300, filter: "Ha" }], -15), [dark(), flat("Ha")], TOL);
  assert(missing.some((s) => s.startsWith("darks")), `expected darks gap: ${missing.join("; ")}`);
});
test("gaps: unknown light temp (cool_to null) is not a temp constraint", () => {
  const { missing } = planCalibrationGaps(
    plan([{ exposure_s: 300, filter: "Ha" }], null), [dark(), flat("Ha")], TOL);
  eq(missing.length, 0, "null cool_to => temp not compared => covered");
});
test("gaps: a no-filter light needs only a dark (no flat required)", () => {
  const { missing } = planCalibrationGaps(
    plan([{ exposure_s: 300, filter: null }]), [dark()], TOL);
  eq(missing.length, 0, "unfiltered light covered by a dark alone");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ncalibrationLibrary.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
