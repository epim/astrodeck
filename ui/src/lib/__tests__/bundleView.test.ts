// Unit tests for the PRO-10 stacking-bundle view helpers (Task 5). Same tiny
// inline-assert harness as eta.test.ts (no vitest/jest wired in):
//   npx tsx src/lib/__tests__/bundleView.test.ts
import {
  masterChips,
  bundleDisabledReason,
  bundleQuery,
  keptSummary,
  layoutOptions,
  lightsDir,
  materializeDisabledReason,
  materializeSummary,
  relayoutDirs,
} from "../bundleView";
import type { BundleMaterializeResult, BundlePreview } from "../../types";

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

// ------------------------------------------------- PRO-10 enrichments (b/d/a)
test("bundleQuery omits every default — the novice one-click URL is unchanged", () => {
  eq(bundleQuery({}), "");
  eq(bundleQuery({ layout: "grouped", weightAlt: false, keepThreshold: null }), "");
});
test("bundleQuery emits only the non-default options, weight_altitude first", () => {
  eq(bundleQuery({ weightAlt: true }), "?weight_altitude=1");
  eq(bundleQuery({ layout: "siril" }), "?layout=siril");
  eq(
    bundleQuery({ layout: "app", weightAlt: true, keepThreshold: 0.5 }),
    "?weight_altitude=1&layout=app&keep_threshold=0.5",
  );
  eq(bundleQuery({ keepThreshold: 0 }), "?keep_threshold=0"); // 0 is a real cutoff
  eq(bundleQuery({ keepThreshold: NaN }), ""); // ...but NaN is not
});
test("lightsDir mirrors the server's _lights_dir for every layout", () => {
  eq(layoutOptions().length, 3);
  eq(layoutOptions()[0].value, "grouped");
  eq(lightsDir("M42/Ha/300s", "grouped"), "M42/Ha/300s/lights");
  eq(lightsDir("M42/Ha/300s", "siril"), "M42/Ha/300s/lights");
  eq(lightsDir("M42/Ha/300s", "app"), "M42/Ha/300s/Light");
  eq(relayoutDirs(ok, "app").join(","), "d/Light");
  eq(relayoutDirs(null, "app").length, 0);
});
test("keptSummary is silent without a threshold and honest with one", () => {
  eq(keptSummary(ok), null); // no keep_threshold set -> nothing to say
  const flagged: BundlePreview = {
    ...ok,
    keep_threshold: 0.5,
    groups: [{ ...ok.groups[0], light_count: 42, kept_count: 38 }],
  };
  const s = keptSummary(flagged) ?? "";
  assert(s.includes("38 of 42"), `expected the kept count, got ${s}`);
  assert(s.includes("nothing is deleted"), "must say nothing is deleted");
  assert(s.includes("all 42 are in the download"),
    `must say every sub still ships, got ${s}`);
});
test("materializeSummary names failures instead of hiding a partial export", () => {
  const base: BundleMaterializeResult = {
    export_dir: "/c/exports/r1",
    layout: "grouped",
    linked: 12,
    copied: 3,
    bytes_copied: 99,
    failed: [],
    groups: [],
    hardlink_note: "",
  };
  // the OUTCOME, not link/copy telemetry — but a partial export still says so
  const s = materializeSummary(base);
  assert(s.includes("15 photos"), `expected the total, got ${s}`);
  assert(s.includes("3 had to be copied"), `expected the disk caveat, got ${s}`);
  assert(materializeSummary({ ...base, copied: 0 }).includes("no extra disk used"),
    "all-hardlinked says no extra disk");
  assert(materializeSummary({ ...base, failed: [{ src: "a", reason: "b" }] })
    .includes("1 couldn't be written"), "failures are named, never hidden");
});
test("materialize is honest-disabled off the capture box", () => {
  assert(materializeDisabledReason(0, ok) !== null, "no frames -> disabled");
  assert(materializeDisabledReason(5, null) !== null, "preview loading -> disabled");
  const r = materializeDisabledReason(5, empty) ?? "";
  assert(r.includes("build.sh"), `must point at the .zip path, got ${r}`);
  eq(materializeDisabledReason(5, ok), null);
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
