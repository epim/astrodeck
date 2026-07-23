// Unit tests for the PRO-10 stacking-bundle view helpers (Task 5). Same tiny
// inline-assert harness as eta.test.ts (no vitest/jest wired in):
//   npx tsx src/lib/__tests__/bundleView.test.ts
import { masterChips, bundleDisabledReason } from "../bundleView";
import type { BundlePreview } from "../../types";

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

// ---------------------------------------------------------------- masterChips
test("masterChips fixed order dark/flat/bias with ok flags", () => {
  const c = masterChips({ dark: true, flat: false, bias: true });
  eq(c.map((x) => x.kind).join(","), "dark,flat,bias");
  eq(c[0].ok, true);
  eq(c[1].ok, false);
  eq(c[2].ok, true);
});
test("masterChips treats missing keys as not-ok (no undefined)", () => {
  const c = masterChips({});
  eq(c.length, 3);
  eq(c.every((x) => x.ok === false), true);
});

// ---------------------------------------------------------- bundleDisabledReason
const empty: BundlePreview = {
  report_id: "r",
  plan_name: "p",
  layout: "grouped",
  weight_altitude: false,
  groups: [],
  warnings: [],
};
const ok: BundlePreview = {
  ...empty,
  groups: [
    {
      dir: "d",
      target: "M42",
      filter: "Ha",
      exposure_s: 300,
      gain: 100,
      binning: 1,
      light_count: 2,
      accepted_count: 2,
      masters: { dark: true, flat: false, bias: false },
    },
  ],
};

test("disabled when no frames captured, even before a preview loads", () => {
  assert(bundleDisabledReason(0, null) !== null, "0 frames disabled");
  assert(bundleDisabledReason(0, ok) !== null, "0 frames disabled even with groups");
});
test("disabled when frames exist but the bundle has no groups", () => {
  assert(bundleDisabledReason(5, empty) !== null, "no groups disabled");
});
test("enabled when frames exist and at least one group is present", () => {
  eq(bundleDisabledReason(5, ok), null);
});
test("null preview with frames is not disabled (preview still loading)", () => {
  eq(bundleDisabledReason(5, null), null);
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nbundleView: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
