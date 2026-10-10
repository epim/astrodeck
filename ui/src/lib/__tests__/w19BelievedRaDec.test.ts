// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// WP-160 / #913: `believedRaDec`, the one gate every reader of
// `status.mount.ra_hours` / `.dec_deg` / `.ra_str` / `.dec_str` goes through.
//
// #791 (WP-151) put `believedPointing` in front of every reader of alt/az and
// left the equatorial reading alone. After a power cycle the AM5 reports its
// HOME position, the pole, wherever the tube is, and the server latches
// `status.mount.position_known` false (#144). The RA it reports there follows
// the site's sidereal clock (#883, #166), so a header, a lock screen, an atlas
// footprint or a guided arrival check that read it showed a precise position
// nothing backs. The surfaces each have a mounted test (the `w19*` files beside
// them); this file is the selector's own contract.
//
//     npx tsx src/lib/__tests__/w19BelievedRaDec.test.ts
//
// Named mutants (one-statement source changes in slewController.ts):
//   w19b1_ignores_the_flag -- `|| !positionKnown(mount)` deleted from the first
//     guard. "FAIL position_known false withholds a perfectly good RA/Dec".
//   w19b2_flag_inverted -- `!positionKnown(mount)` becomes `positionKnown(mount)`.
//     "FAIL a known position returns its RA, Dec and their text".
//   w19b3_zero_is_falsy -- the finite checks become truthiness checks. "FAIL zero
//     is a real reading, not an absent one".
//   w19b4_dec_unchecked -- the `dec_deg` guard line deleted. "FAIL an absent dec
//     is not a pointing".

import { believedRaDec } from "../slewController";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`FAIL ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function same(a: unknown, b: unknown, msg: string): void {
  const x = JSON.stringify(a);
  const y = JSON.stringify(b);
  if (x !== y) throw new Error(`${msg}: expected ${y}, got ${x}`);
}

const MOUNT = { ra_hours: 3.5, dec_deg: 40, ra_str: "03:30:00", dec_str: "+40:00:00" };

test("no mount block is no pointing", () => {
  assert(believedRaDec(undefined) === null, "undefined");
  assert(believedRaDec(null) === null, "null");
});

test("a known position returns its RA, Dec and their text", () => {
  same(believedRaDec({ ...MOUNT, position_known: true }), MOUNT, "flag true");
});

test("an ABSENT flag (an engine older than #144) reads as known", () => {
  same(believedRaDec({ ...MOUNT }), MOUNT, "no flag");
});

test("position_known false withholds a perfectly good RA/Dec", () => {
  assert(believedRaDec({ ...MOUNT, position_known: false }) === null,
    "the mount's home reading came back as a pointing");
});

test("zero is a real reading, not an absent one", () => {
  same(
    believedRaDec({ ra_hours: 0, dec_deg: 0, ra_str: "00h00m", dec_str: "+00" }),
    { ra_hours: 0, dec_deg: 0, ra_str: "00h00m", dec_str: "+00" },
    "ra 0 dec 0",
  );
});

test("an absent RA is not a pointing", () => {
  assert(believedRaDec({ dec_deg: 40, position_known: true }) === null, "no ra");
  assert(believedRaDec({ position_known: true }) === null, "no ra, no dec");
});

test("an absent dec is not a pointing", () => {
  assert(believedRaDec({ ra_hours: 3.5 }) === null, "ra without dec");
});

test("a non-finite or non-numeric angle is not a pointing", () => {
  assert(believedRaDec({ ...MOUNT, ra_hours: Number.NaN }) === null, "NaN ra");
  assert(believedRaDec({ ...MOUNT, dec_deg: Number.POSITIVE_INFINITY }) === null, "Infinity dec");
  assert(believedRaDec({ ...MOUNT, ra_hours: "3.5" as unknown as number }) === null, "string ra");
  assert(believedRaDec({ ...MOUNT, dec_deg: null as unknown as number }) === null, "null dec");
});

test("the text is empty, not undefined, when the wire carries none", () => {
  same(believedRaDec({ ra_hours: 3.5, dec_deg: 40 }),
    { ra_hours: 3.5, dec_deg: 40, ra_str: "", dec_str: "" }, "no strings");
});

test("only the four reading fields come back: nothing else of the mount block rides along", () => {
  const out = believedRaDec({ ...MOUNT, position_known: true, alt: 45, az: 120 } as never);
  same(Object.keys(out ?? {}).sort(), ["dec_deg", "dec_str", "ra_hours", "ra_str"], "keys of the result");
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw19BelievedRaDec.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
