// effective.test.ts — the layer resolver (#129).
//
// Run with:  npx tsx src/lib/__tests__/effective.test.ts   (from ui/)
//
// The property under test is not "does it read a field". It is: does the UI
// stop agreeing with a value that is not what the rig runs, AND does it still
// mark the case where the two layers happen to agree — because a profile pinned
// to `sim` and a global config pinned to `sim` are indistinguishable by value,
// and that indistinguishability is what hid the original bug for twelve days.
import type { AppConfig, EffectiveEntry } from "../../types";
import {
  describeCameraFill,
  describeOverride,
  effectiveOptics,
  effectiveProviders,
  entryOf,
  isProfileOverride,
  layerOf,
  opticsOverridden,
  opticsOverrideProfile,
  overrideChangesValue,
  overrideProfileName,
  overriddenCaps,
  profileOverrideSummary,
  showValue,
  valueOf,
} from "../effective";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq(a: unknown, b: unknown, msg = ""): void {
  if (!Object.is(a, b)) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

const entry = (p: Partial<EffectiveEntry>): EffectiveEntry => ({
  value: null,
  layer: "default",
  profile: null,
  config: null,
  default: null,
  profile_id: null,
  profile_name: null,
  reason: null,
  ...p,
});

/** A config carrying only the provenance keys a test needs. Cast because the
 *  real AppConfig has thirty required fields none of these functions read. */
const cfg = (effective: Record<string, EffectiveEntry>, rest: object = {}) =>
  ({ effective, ...rest }) as unknown as AppConfig;

// ------------------------------------------------------------ the core read

test("valueOf returns the WINNING value, not the global one", () => {
  const c = cfg({
    "providers.polar_align": entry({
      value: "sim",
      layer: "profile",
      profile: "sim",
      config: "astrodeck",
      profile_name: "Backyard",
    }),
  });
  eq(valueOf(c, "providers.polar_align", "astrodeck"), "sim");
  eq(layerOf(c, "providers.polar_align"), "profile");
});

test("a missing provenance block degrades to the caller's global value", () => {
  // The WS `hello` bootstrap carries no `effective`. Showing the old value is a
  // degradation; showing nothing would be a blank panel.
  const c = cfg({});
  eq(valueOf(c, "providers.solve", "astap"), "astap");
  eq(entryOf(c, "providers.solve"), null);
  eq(layerOf(c, "providers.solve"), null);
});

test("a null WINNING value is honoured, not treated as missing", () => {
  // guide_focal_length_mm is legitimately null. Falling back to the caller's
  // value here would report a guide scope the rig does not have.
  const c = cfg({
    "optics.guide_focal_length_mm": entry({ value: null, layer: "profile", config: 200 }),
  });
  eq(valueOf(c, "optics.guide_focal_length_mm", 200), null);
});

test("no provenance is NOT the same as no override", () => {
  // isProfileOverride(null) must be false, but callers can still tell the two
  // apart via entryOf() === null. Asserting the predicate alone would let a
  // bootstrap config quietly claim "nothing is overridden".
  assert(!isProfileOverride(null), "null entry is not an override");
  eq(entryOf(cfg({}), "providers.guide"), null);
});

// -------------------------------------------------- the indistinguishable case

test("a profile pin that MATCHES global is still an override", () => {
  const e = entry({
    value: "sim",
    layer: "profile",
    profile: "sim",
    config: "sim",
    profile_name: "Backyard",
  });
  assert(isProfileOverride(e), "layer profile ⇒ override");
  assert(!overrideChangesValue(e), "the values agree today");
  const text = describeOverride(e) ?? "";
  assert(text.includes("Backyard"), `names the profile: ${text}`);
  assert(
    text.includes("will not change what runs"),
    `warns that editing global is inert: ${text}`,
  );
});

test("an override that CHANGES the value prints both layers", () => {
  const e = entry({
    value: "sim",
    layer: "profile",
    profile: "sim",
    config: "astrodeck",
    profile_name: "Backyard",
  });
  assert(overrideChangesValue(e), "values differ");
  const text = describeOverride(e) ?? "";
  assert(text.includes("sim"), `names what runs: ${text}`);
  assert(text.includes("astrodeck"), `names what would run instead: ${text}`);
});

test("describeOverride is silent for config / default / camera layers", () => {
  eq(describeOverride(entry({ layer: "config", value: 1 })), null);
  eq(describeOverride(entry({ layer: "default", value: 1 })), null);
  eq(describeOverride(entry({ layer: "camera", value: 3.76 })), null);
});

test("the camera is named as a supplier, and only for its own layer", () => {
  const e = entry({ layer: "camera", value: 3.76, config: 0 });
  const text = describeCameraFill(e) ?? "";
  assert(text.includes("3.76"), `prints the running value: ${text}`);
  assert(text.includes("camera"), `names the camera: ${text}`);
  eq(describeCameraFill(entry({ layer: "profile", value: 3.76 })), null);
});

test("an unnamed profile still yields an answer", () => {
  const e = entry({ layer: "profile", value: "sim", profile_id: "3f2a", config: "auto" });
  eq(overrideProfileName(e), "3f2a");
});

// ---------------------------------------------------------------- formatting

test("0 / '' / null read as 'not set', never as a measurement of zero", () => {
  eq(showValue(0), "not set");
  eq(showValue(""), "not set");
  eq(showValue(null), "not set");
  eq(showValue(undefined), "not set");
});

test("false is a SETTING, not an empty value", () => {
  // false === 0 is falsy; auto_from_camera:false is deliberate.
  eq(showValue(false), "off");
  eq(showValue(true), "on");
});

test("a formatter adds units to real values only", () => {
  eq(showValue(250, (v) => `${v} mm`), "250 mm");
  eq(showValue(0, (v) => `${v} mm`), "not set");
});

// ----------------------------------------------------------------- providers

test("effectiveProviders spreads only the keys the server reported", () => {
  const c = cfg({
    "providers.solve": entry({ value: "astap", layer: "config" }),
    "providers.guide": entry({ value: "sim", layer: "profile" }),
  });
  const out = effectiveProviders(c);
  eq(out.solve, "astap");
  eq(out.guide, "sim");
  eq(out.autofocus, undefined, "unreported keys stay absent so a seed survives");
});

test("overriddenCaps lists exactly the profile-pinned capabilities", () => {
  const c = cfg({
    "providers.autofocus": entry({ value: "auto", layer: "default" }),
    "providers.polar_align": entry({ value: "sim", layer: "profile" }),
    "providers.solve": entry({ value: "astap", layer: "config" }),
    "providers.guide": entry({ value: "auto", layer: "profile" }),
  });
  eq(overriddenCaps(c).join(","), "polar_align,guide");
});

// -------------------------------------------------------------------- optics

test("effectiveOptics draws from the WINNING layer, not config.optics", () => {
  const global = {
    focal_length_mm: 530,
    pixel_size_um: 3.76,
    sensor_width_px: 6248,
    sensor_height_px: 4176,
    auto_from_camera: false,
    telescope_name: "",
  };
  const c = cfg({
    "optics.focal_length_mm": entry({ value: 250, layer: "profile", config: 530 }),
    "optics.pixel_size_um": entry({ value: 2.4, layer: "profile", config: 3.76 }),
    "optics.sensor_width_px": entry({ value: 4144, layer: "profile" }),
    "optics.sensor_height_px": entry({ value: 2822, layer: "profile" }),
  });
  const o = effectiveOptics(c, global, null)!;
  eq(o.focal_length_mm, 250);
  eq(o.pixel_size_um, 2.4);
  eq(o.sensor_width_px, 4144);
  eq(o.sensor_height_px, 2822);
});

test("without provenance, effectiveOptics reproduces the OLD camera merge", () => {
  // The bootstrap path. Pixel/sensor still fall back to the live camera readout,
  // which is exactly what the two hand-copied merges used to do.
  const global = {
    focal_length_mm: 530,
    pixel_size_um: 0,
    sensor_width_px: 0,
    sensor_height_px: 0,
    auto_from_camera: true,
    telescope_name: "",
  };
  const o = effectiveOptics(cfg({}), global, {
    pixel_size_um: 3.76,
    sensor_width_px: 6248,
    sensor_height_px: 4176,
  })!;
  eq(o.focal_length_mm, 530);
  eq(o.pixel_size_um, 3.76);
  eq(o.sensor_width_px, 6248);
});

test("effectiveOptics is null when there are no optics at all", () => {
  eq(effectiveOptics(null, null, null), null);
});

test("opticsOverridden reads the whole-block swap off one field", () => {
  const on = cfg({
    "optics.focal_length_mm": entry({
      value: 250,
      layer: "profile",
      profile_name: "Backyard",
    }),
  });
  assert(opticsOverridden(on), "profile layer ⇒ overridden");
  eq(opticsOverrideProfile(on), "Backyard");
  assert(
    !opticsOverridden(cfg({ "optics.focal_length_mm": entry({ layer: "config" }) })),
    "config layer ⇒ not overridden",
  );
  assert(!opticsOverridden(cfg({})), "no provenance ⇒ claims nothing");
});

// ------------------------------------------------------------- profile rows

test("a profile row's summary names the capability AND the provider", () => {
  const s = profileOverrideSummary({ providers: { polar_align: "sim" } }) ?? "";
  assert(s.includes("polar align"), `names the capability: ${s}`);
  assert(s.includes("built-in simulator"), `names the provider in words: ${s}`);
});

test("a profile pinned to 'auto' is REPORTED, not filtered away", () => {
  // "auto" from a profile still beats a global "astap" and changes which solver
  // runs. Hiding it would recreate the invisible override one layer up.
  const s = profileOverrideSummary({ providers: { solve: "auto" } }) ?? "";
  assert(s.includes("plate solve"), `still listed: ${s}`);
});

test("an unresolvable driver id is printed literally", () => {
  // A pin naming a deleted driver is silently discarded at resolve time; the
  // raw string is the only way a user can tell that from a pin never written.
  const s = profileOverrideSummary({ providers: { guide: "nina-1a2b" } }) ?? "";
  assert(s.includes("nina-1a2b"), `prints the id: ${s}`);
});

test("an optics block is summarised by the two facts that identify a scope", () => {
  const s =
    profileOverrideSummary({
      optics: {
        focal_length_mm: 250,
        pixel_size_um: 2.4,
        sensor_width_px: 0,
        sensor_height_px: 0,
        auto_from_camera: true,
        telescope_name: "Redcat 51",
      },
    }) ?? "";
  assert(s.includes("250 mm"), `focal length: ${s}`);
  assert(s.includes("Redcat 51"), `scope name: ${s}`);
});

test("a profile that overrides nothing summarises to null", () => {
  eq(profileOverrideSummary({}), null);
  eq(profileOverrideSummary({ providers: {}, optics: null }), null);
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\neffective.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
