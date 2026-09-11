// wheelRing.test.ts - the filter carousel's geometry, without a DOM.
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/wheelRing.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Three things are worth asserting here and they are all things a screenshot
// cannot show:
//
//   1. THE CHOSEN SLOT REACHES THE LIGHT PATH. The whole rotation exists so the
//      slot the wheel is on sits under the fixed marker at the top. If the sign
//      of `rot` or the -PI/2 phase is wrong the ring still looks like a ring -
//      seven circles, evenly spaced, one highlighted - and it is pointing at
//      the wrong glass.
//   2. THE CIRCLES DO NOT OVERLAP on a wheel that is not the design's seven.
//      The design draws 7; real wheels report 5, 8, 9, 12. A radius fixed at 22
//      makes a 12-slot ring a chain of intersecting circles.
//   3. PAST TWELVE THE RING IS ABANDONED. A 13-slot ring at 236 px cannot carry
//      a readable label, so `mode` flips to `list` and the sheet draws the
//      table alone.

import {
  MARKER_POINT, RING_C, RING_R, RING_MAX_SLOTS, SLOT_HIT_PX, SLOT_R_MAX,
  SLOT_R_MIN, filterColor, slotRadius, slotType, wheelRing,
} from "../lib/wheelRing";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
function near(got: number, want: number, tol: number, msg: string): void {
  if (!(Math.abs(got - want) <= tol)) {
    throw new Error(`${msg} (expected ${want} +-${tol}, got ${got})`);
  }
}

// ------------------------------------------------------ the precondition
test("a 7-slot wheel builds a ring of 7 slots - the fixture, before anything else", () => {
  const r = wheelRing(7, 0);
  eq(r.mode, "ring", "a 7-slot wheel did not draw a ring");
  eq(r.n, 7, "wrong slot count");
  eq(r.slots.length, 7, "the ring has the wrong number of slot models");
  eq(r.slots.filter((s) => s.current).length, 1,
    "exactly one slot must be the current one");
});

// ------------------------------------------- 1. the chosen slot reaches the marker
test("the group turns by -(current/n)*360 on a 7-slot wheel", () => {
  for (let i = 0; i < 7; i++) {
    near(wheelRing(7, i).rot, -(i / 7) * 360, 1e-9,
      `slot ${i} does not rotate the group by -(i/n)*360`);
  }
});

test("whichever slot is current lands on the light-path marker, within 0.01", () => {
  for (const n of [5, 7, 8, 9, 12]) {
    for (let i = 0; i < n; i++) {
      const chosen = wheelRing(n, i).slots[i];
      // The hit button is placed by its top-left corner, so its CENTRE is the
      // corner plus half the button. That centre is what has to sit on the
      // marker: the button is what the finger presses and the circle under it
      // is what the light goes through.
      near(chosen.bx + SLOT_HIT_PX / 2, MARKER_POINT.x, 0.01,
        `${n} slots, slot ${i}: the chosen slot is not on the marker in x`);
      near(chosen.by + SLOT_HIT_PX / 2, MARKER_POINT.y, 0.01,
        `${n} slots, slot ${i}: the chosen slot is not on the marker in y`);
      near(((chosen.rotatedDeg % 360) + 360) % 360, 0, 0.01,
        `${n} slots, slot ${i}: the chosen slot's rotated angle is not 12 o'clock`);
    }
  }
});

test("the UNROTATED centres are a circle 12 o'clock first - the group's own frame", () => {
  const r = wheelRing(4, 2);
  near(r.slots[0].cx, RING_C, 1e-9, "slot 0 is not on the vertical axis");
  near(r.slots[0].cy, RING_C - RING_R, 1e-9, "slot 0 is not at the top of the orbit");
  near(r.slots[1].cx, RING_C + RING_R, 1e-9, "slot 1 is not at 3 o'clock");
  near(r.slots[1].cy, RING_C, 1e-9, "slot 1 is not at 3 o'clock");
  // …and the rotation has NOT been applied to them: they live inside the <g>.
  assert(r.rot !== 0, "precondition: this fixture must be rotated");
});

// ---------------------------------------------- 2. circles that do not overlap
test("slot circles never overlap, on every wheel size the ring is drawn for", () => {
  for (let n = 2; n <= RING_MAX_SLOTS; n++) {
    const r = wheelRing(n, 0);
    const a = r.slots[0];
    const b = r.slots[1];
    const gap = Math.hypot(a.cx - b.cx, a.cy - b.cy);
    assert(2 * r.radius < gap,
      `${n} slots: circles of r=${r.radius} overlap - neighbours are ${gap.toFixed(1)} apart`);
    assert(r.radius >= SLOT_R_MIN && r.radius <= SLOT_R_MAX,
      `${n} slots: radius ${r.radius} is outside the legible range`);
  }
});

test("5, 9 and 12 slots are each sized from their own gap", () => {
  const r5 = slotRadius(5);
  const r7 = slotRadius(7);
  const r9 = slotRadius(9);
  eq(r7, SLOT_R_MAX, "a 7-slot wheel must still draw the design's 22 px circles");
  eq(r5, SLOT_R_MAX, "a 5-slot wheel has more room than 7, so it keeps the maximum");
  // 74*sin(PI/9) = 25.3, so a 9-slot wheel still has room for the full 22 and
  // the clamp - not the formula - is what decides. Asserted as the formula so
  // the two cannot drift apart.
  eq(r9, Math.max(SLOT_R_MIN, Math.min(SLOT_R_MAX, Math.floor(74 * Math.sin(Math.PI / 9)) - 3)),
    "the 9-slot radius is not the documented formula");
  // 10 is the first size where the gap, not the ceiling, sets the radius.
  assert(slotRadius(10) < r7,
    `a 10-slot wheel must draw smaller circles than a 7 (got ${slotRadius(10)} vs ${r7})`);
  eq(slotRadius(12), Math.floor(74 * Math.sin(Math.PI / 12)) - 3,
    "a 12-slot wheel is where the clamp stops mattering and the formula shows");
});

// ------------------------------------------------- 3. the list fallback
test("13 slots is too many for the ring, and says so", () => {
  const r = wheelRing(13, 3);
  eq(r.mode, "list", "a 13-slot wheel still tried to draw a ring");
  eq(r.n, 13, "the slot count is lost when the ring is abandoned");
  eq(wheelRing(RING_MAX_SLOTS, 0).mode, "ring",
    "12 slots is the last size that still draws a ring");
});

test("an empty wheel draws nothing rather than a ring of nothing", () => {
  const r = wheelRing(0, 0);
  eq(r.mode, "list", "a wheel with no slots must not claim a ring");
  eq(r.slots.length, 0, "a wheel with no slots produced slot models");
});

test("a position past the end, or a mid-move -1, still draws a wheel", () => {
  // ASCOM reports -1 while turning and the store clamps it to 0; a names array
  // shorter than the reported position is a driver disagreeing with itself.
  // Neither is a reason to render a blank card.
  const low = wheelRing(7, -1);
  eq(low.slots[0].current, true, "a negative position must clamp to the first slot");
  const high = wheelRing(7, 99);
  eq(high.slots[6].current, true, "a position past the end must clamp to the last slot");
});

// ---------------------------------------------------- derived type + colour
test("the slot type is derived, blackout first", () => {
  eq(slotType(false, false), "broadband", "a plain slot is broadband");
  eq(slotType(false, true), "narrowband", "the narrowband flag names the type");
  eq(slotType(true, false), "blackout", "the opaque flag names the type");
  // A slot can carry both flags (a stale narrowband tick under a new blackout).
  // No light path outranks the passband, because there is no passband.
  eq(slotType(true, true), "blackout",
    "a blackout slot that still carries a narrowband flag is a blackout");
});

test("a filter with no colour token gets the body text colour, not a made-up hue", () => {
  eq(filterColor("Ha"), "var(--nx-filter-Ha)", "Ha has a token and must use it");
  eq(filterColor("ha"), "var(--nx-filter-Ha)", "the token match is case-insensitive");
  eq(filterColor("L"), "var(--nx-filter-L)", "L has a token and must use it");
  eq(filterColor("Astrodon 5nm"), "var(--text)",
    "an unknown filter name must fall back to the text colour");
  eq(filterColor(""), "var(--text)", "an unnamed slot must fall back to the text colour");
  eq(filterColor(null), "var(--text)", "a null name must not throw");
});

const total = passed + failed;
console.log(`wheelRing.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
