// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// WP-151 / #791: `believedPointing`, the one gate every reader of
// `status.mount.alt` / `.az` goes through.
//
// After a power cycle the AM5 reports its HOME position, the pole, wherever the
// tube is, and the server latches `status.mount.position_known` false (#144). At
// the pole the altitude IS the site latitude (#140), so a reader that plots,
// prints or measures `mount.alt` while the flag is false is wrong about the tube
// and leaks the latitude. WP-126 gated the slew pad, the mount sheet and the
// classic Mount view; #791 found six more readers and the classic Monitor made a
// seventh, so the gate is one function and these are its contracts. Each surface
// has its own mounted test (the `w18*PositionUnknown` files beside it).
//
//     npx tsx src/lib/__tests__/w18BelievedPointing.test.ts
//
// Named mutants (one-statement source changes in slewController.ts):
//   w18b1_ignores_the_flag -- delete `|| !positionKnown(mount)` from the first
//     guard. "FAIL position_known false withholds a perfectly good alt/az".
//   w18b2_flag_inverted -- `!positionKnown(mount)` becomes `positionKnown(mount)`.
//     "FAIL a known position returns its alt and az".
//   w18b3_zero_is_falsy -- the finite checks become truthiness checks. "FAIL zero
//     is a real reading, not an absent one".
//   w18b4_alt_only -- the `az` guard line deleted. "FAIL an absent az is not a
//     pointing".

import { believedPointing } from "../slewController";

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

test("no mount block is no pointing", () => {
  assert(believedPointing(undefined) === null, "undefined");
  assert(believedPointing(null) === null, "null");
});

test("a known position returns its alt and az", () => {
  same(believedPointing({ alt: 45, az: 120, position_known: true }), { alt: 45, az: 120 }, "flag true");
});

test("an ABSENT flag (an engine older than #144) reads as known", () => {
  same(believedPointing({ alt: 45, az: 120 }), { alt: 45, az: 120 }, "no flag");
});

test("position_known false withholds a perfectly good alt/az", () => {
  assert(believedPointing({ alt: 45, az: 120, position_known: false }) === null,
    "the mount's home reading came back as a pointing");
});

test("below the horizon is still a pointing: clipping it is each reader's own call", () => {
  same(believedPointing({ alt: -12, az: 300, position_known: true }), { alt: -12, az: 300 }, "alt -12");
});

test("zero is a real reading, not an absent one", () => {
  same(believedPointing({ alt: 0, az: 0 }), { alt: 0, az: 0 }, "alt 0 az 0");
});

test("an absent alt is not a pointing (a viewer's frame has neither angle)", () => {
  assert(believedPointing({ position_known: true }) === null, "no alt, no az");
  assert(believedPointing({ az: 120 }) === null, "no alt");
});

test("an absent az is not a pointing", () => {
  assert(believedPointing({ alt: 45 }) === null, "alt without az");
});

test("a non-finite or non-numeric angle is not a pointing", () => {
  assert(believedPointing({ alt: Number.NaN, az: 120 }) === null, "NaN alt");
  assert(believedPointing({ alt: 45, az: Number.POSITIVE_INFINITY }) === null, "Infinity az");
  assert(believedPointing({ alt: "45" as unknown as number, az: 120 }) === null, "string alt");
  assert(believedPointing({ alt: null as unknown as number, az: 120 }) === null, "null alt");
});

test("only alt and az come back: nothing else of the mount block rides along", () => {
  const out = believedPointing({ alt: 45, az: 120, position_known: true, ra_hours: 3.5 } as never);
  same(Object.keys(out ?? {}).sort(), ["alt", "az"], "keys of the result");
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw18BelievedPointing.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
