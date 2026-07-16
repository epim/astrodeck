// site.test.ts — pure geo helpers (spec §6). Inline assert harness like
// caps.test.ts. Run: npx tsx src/lib/__tests__/site.test.ts
import {
  toSigned,
  fromSigned,
  validateLat,
  validateLon,
  validateElevation,
  formatCoord,
  locationEquals,
  activeSiteName,
  activeSiteSource,
  type Hemisphere,
  type SiteDraft,
} from "../site";
import type { SavedLocation, Site } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

// ---------------------------------------------------------- toSigned
test("toSigned negates S/W, keeps N/E", () => {
  eq(toSigned(40.5, "N"), 40.5, "N");
  eq(toSigned(40.5, "S"), -40.5, "S");
  eq(toSigned(74.25, "E"), 74.25, "E");
  eq(toSigned(74.25, "W"), -74.25, "W");
  eq(toSigned(0, "S"), -0, "0 magnitude");   // -0 === 0 in JS
  assert(toSigned(0, "S") === 0, "toSigned(0,S) is 0");
});

// ---------------------------------------------------------- fromSigned
test("fromSigned splits magnitude + hemisphere by axis", () => {
  eq(fromSigned(40.5, "lat").magnitude, 40.5, "lat mag");
  eq(fromSigned(40.5, "lat").hemisphere, "N", "lat +");
  eq(fromSigned(-40.5, "lat").hemisphere, "S", "lat -");
  eq(fromSigned(74.25, "lon").hemisphere, "E", "lon +");
  eq(fromSigned(-74.25, "lon").hemisphere, "W", "lon -");
  eq(fromSigned(0, "lat").hemisphere, "N", "0 lat -> N");
  eq(fromSigned(0, "lon").hemisphere, "E", "0 lon -> E");
});

// ---------------------------------------------------------- round-trips
test("signed <-> magnitude round-trips (incl 0 and boundaries)", () => {
  const cases: Array<{ v: number; axis: "lat" | "lon" }> = [
    { v: 0, axis: "lat" }, { v: 0, axis: "lon" },
    { v: 40.123456, axis: "lat" }, { v: -74.654321, axis: "lon" },
    { v: 90, axis: "lat" }, { v: -90, axis: "lat" },
    { v: 180, axis: "lon" }, { v: -180, axis: "lon" },
  ];
  for (const { v, axis } of cases) {
    const { magnitude, hemisphere } = fromSigned(v, axis);
    eq(toSigned(magnitude, hemisphere as Hemisphere), v, `roundtrip ${v}/${axis}`);
  }
});

// ---------------------------------------------------------- validation
test("validateLat range 0..90 + NaN rejected", () => {
  assert(validateLat(0) === null, "0 ok");
  assert(validateLat(90) === null, "90 ok");
  assert(validateLat(-1) !== null, "-1 rejected");
  assert(validateLat(90.1) !== null, "90.1 rejected");
  assert(validateLat(NaN) !== null, "NaN rejected");
});

test("validateLon range 0..180 + NaN rejected", () => {
  assert(validateLon(0) === null, "0 ok");
  assert(validateLon(180) === null, "180 ok");
  assert(validateLon(180.1) !== null, "180.1 rejected");
  assert(validateLon(NaN) !== null, "NaN rejected");
});

test("validateElevation range -430..9000 + NaN rejected", () => {
  assert(validateElevation(0) === null, "0 ok");
  assert(validateElevation(-430) === null, "-430 ok");
  assert(validateElevation(9000) === null, "9000 ok");
  assert(validateElevation(-431) !== null, "-431 rejected");
  assert(validateElevation(9001) !== null, "9001 rejected");
  assert(validateElevation(NaN) !== null, "NaN rejected");
});

// empty-string inputs parse to NaN via Number("") === 0? No: Number("") === 0,
// but the panel passes Number(str); an EMPTY field yields "" -> Number("") = 0
// which is a valid coord. The reject-empty guard belongs to the panel (it
// treats "" as unset); here we prove NaN (non-numeric text) is rejected.
test("non-numeric text -> NaN is rejected by every validator", () => {
  assert(validateLat(Number("abc")) !== null, "lat NaN");
  assert(validateLon(Number("abc")) !== null, "lon NaN");
  assert(validateElevation(Number("abc")) !== null, "elev NaN");
});

// ---------------------------------------------------------- formatCoord
test("formatCoord is 6 dp", () => {
  eq(formatCoord(40.123456789), "40.123457", "rounds to 6dp");
  eq(formatCoord(-74.65), "-74.650000", "pads to 6dp");
  eq(formatCoord(0), "0.000000", "zero");
});

// ---------------------------------------------------------- locationEquals
const loc: SavedLocation = {
  id: "abc", name: "Backyard", latitude: 40.123456, longitude: -74.654321,
  elevation_m: 12, horizon_min_deg: 15, created_ts: 0, updated_ts: 0,
};
test("locationEquals true for a matching signed draft", () => {
  const draft: SiteDraft = {
    name: " Backyard ", latitude: 40.123456, longitude: -74.654321,
    elevation_m: 12,
  };
  assert(locationEquals(draft, loc), "trimmed name + 6dp coords match");
});
test("locationEquals false when any field differs", () => {
  const base: SiteDraft = {
    name: "Backyard", latitude: 40.123456, longitude: -74.654321,
    elevation_m: 12,
  };
  assert(!locationEquals({ ...base, name: "Other" }, loc), "name differs");
  assert(!locationEquals({ ...base, latitude: 40.2 }, loc), "lat differs");
  assert(!locationEquals({ ...base, longitude: -74.6 }, loc), "lon differs");
  assert(!locationEquals({ ...base, elevation_m: 13 }, loc), "elev differs");
});

// ---------------------------------------------------------- activeSiteName
test("activeSiteName: default -> Not set, stripped -> Hidden, else the name", () => {
  eq(activeSiteName(undefined), "Not set", "no config yet");
  eq(activeSiteName({ is_default: true, name: undefined }), "Not set", "default");
  eq(activeSiteName({ is_default: false, name: undefined }), "Hidden", "stripped by RBAC");
  eq(activeSiteName({ is_default: false, name: "Backyard" }), "Backyard", "named");
  eq(activeSiteName({ is_default: false, name: "  " }), "Unnamed", "blank after trim");
});

// ---------------------------------------------------------- activeSiteSource
const presetA: SavedLocation = {
  id: "a", name: "Backyard", latitude: 40.123456, longitude: -74.654321,
  elevation_m: 12, horizon_min_deg: 15, created_ts: 0, updated_ts: 0,
};
test("activeSiteSource: default site", () => {
  eq(activeSiteSource(undefined, []), "default", "no config yet");
  eq(activeSiteSource({ is_default: true }, [presetA]), "default", "is_default flag");
});
test("activeSiteSource: coordinates stripped by RBAC -> hidden", () => {
  eq(activeSiteSource({ is_default: false, name: undefined }, [presetA]), "hidden");
});
test("activeSiteSource: byte-for-byte match against a saved preset", () => {
  const site: Site = {
    is_default: false, horizon_min_deg: 15, name: "Backyard",
    latitude: 40.123456, longitude: -74.654321, elevation_m: 12,
  };
  eq(activeSiteSource(site, [presetA]), "saved preset");
});
test("activeSiteSource: coordinates present but no matching preset -> manual", () => {
  const site: Site = {
    is_default: false, horizon_min_deg: 15, name: "Somewhere Else",
    latitude: 10, longitude: 20, elevation_m: 30,
  };
  eq(activeSiteSource(site, [presetA]), "manual");
  eq(activeSiteSource(site, []), "manual", "empty library still manual, not a crash");
});

// ---------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nsite.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}
export const result = { passed, failed, total };
