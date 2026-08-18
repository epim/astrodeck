// #203: the Atlas card explains a geocentric position, and there are now TWO
// reasons for one. Only one of them is something the reader can act on.
//
// Before this, the card said "because no site is set" whenever `topocentric`
// came back false. That was the only reason there was. #203 added a second: a
// caller without `view.site_derived` gets the whole ephemeris computed from the
// centre of the Earth, so their row carries no parallax. Said to THAT reader on
// a fully-configured rig, "no site is set" is false twice over — the setting is
// correct, and it is not the setting that decided this.
//
// Run with:  npx tsx src/components/atlas/__tests__/objectCardEphemeris.test.ts

import { ephemerisAge } from "../ObjectCard";
import type { SkyRow } from "../../../lib/skyRegion";

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

const NOW = Date.now() / 1000;

function row(extra: Partial<SkyRow>): SkyRow {
  return {
    id: "Mars", label: "Mars", kind: "solar_system", type: "Planet",
    ra_hours: 6.3, dec_deg: 23.7, mag: 1.3, size_arcmin: 0.08,
    constellation: "Gemini", describe: "in Gemini", alias: null,
    ephemeris_unix: NOW, ...extra,
  } as SkyRow;
}

test("card: a role-limited position is not blamed on a missing setting", () => {
  const s = ephemerisAge(row({ topocentric: false, geocentric_reason: "not_permitted" }));
  assert(s != null, "no sentence at all");
  assert(!s!.includes("no site is set"),
    `told the reader to fix a setting that is already correct: ${s}`);
  assert(s!.includes("your role"), `the actual reason is missing: ${s}`);
});

test("card: an unset site still says so — the reader CAN fix that one", () => {
  const s = ephemerisAge(row({ topocentric: false, geocentric_reason: "site_unset" }));
  assert(s!.includes("no site is set"), `lost the actionable case: ${s}`);
});

test("card: an older server sends no reason, and the old copy still applies", () => {
  // `geocentric_reason` is additive. A row without it must not lose its
  // explanation — before #203, `topocentric: false` meant exactly one thing.
  const s = ephemerisAge(row({ topocentric: false }));
  assert(s!.includes("no site is set"), `absent field lost the sentence: ${s}`);
});

test("card: a real topocentric position explains nothing extra", () => {
  const s = ephemerisAge(row({ topocentric: true, geocentric_reason: null }));
  assert(!s!.includes("centre of the Earth"),
    `explained a degradation that did not happen: ${s}`);
  assert(s!.includes("Position computed"), s!);
});

test("card: a fixed object gets no ephemeris line at all", () => {
  assert(ephemerisAge(row({ kind: "dso", ephemeris_unix: undefined })) === null,
    "a galaxy was given a position-age caption");
});

console.log(`objectCardEphemeris.test: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log(f);
if (failed > 0) process.exit(1);
