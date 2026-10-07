// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// WP-126 / #144 (UI half): what the slew controller does while the mount does
// not know where it points.
//
// After a power cycle the AM5 reports its home position, which is the pole,
// wherever the tube is. Every number the client computes from "where the mount
// points" is then a precise answer about nothing, and the controller's own
// instance of that is the ALTITUDE GUARD: it reads `status.mount.alt`, and an
// altitude read at the pole is the site latitude, which is nowhere near the
// tube's. The guard is a safety net that can only be wrong in one of two ways
// at that moment: it can refuse a perfectly good hold (the tube really is high,
// the mount's idea is not), or it can wave through a slew toward the horizon
// because the believed altitude is comfortable. Neither is a guard. So while
// `getPositionKnown()` says false the controller does not consult it, at the
// gate or on the keepalive, and `positionKnown()` is the one definition of
// "false" both UIs use (only an explicit `false` locks anything: an engine older
// than #144 sends no flag and reads as known).
//
// Same inline-assert harness as slewController.test.ts. Run directly with
//     npx tsx src/lib/__tests__/w16SlewControllerPositionUnknown.test.ts
//
// Named mutants (one-statement source changes in slewController.ts; each run
// from a byte backup inside the worktree, restored byte-identically, mutant text
// grepped out). The first failing assertion of each is quoted verbatim:
//   w16c1_guard_ignores_the_flag -- `guardAlt()`'s `if (this.o.getPositionKnown
//     && ...) return null;` line deleted. "FAIL a hold below the horizon limit
//     starts when the mount does not know where it points: the hold did not
//     start, so the pad cannot drive a reset mount home: expected 1, got 0".
//   w16c2_keepalive_reads_the_raw_alt -- `keepaliveTick` calls `this.o.getAlt()`
//     instead of `this.guardAlt()`. "FAIL the keepalive does not trip on an
//     altitude the mount cannot vouch for: the keepalive stopped a hold on a
//     believed altitude the mount cannot vouch for: expected holding, got
//     belowHorizon".
//   w16c3_flag_inverted -- `positionKnown` returns `mount?.position_known ===
//     true`. "FAIL only an explicit false is 'unknown': absent, null and true
//     all read known: no mount block must not lock the pad".
//   w16c4_gate_reads_the_raw_alt -- `startHold` calls `this.o.getAlt()`
//     instead of `this.guardAlt()`. "FAIL a hold below the horizon limit starts
//     when the mount does not know where it points: the hold did not start, so
//     the pad cannot drive a reset mount home: expected 1, got 0".
//   w16c5_ceiling_rung_is_the_first -- `fastestRungIndex` returns 0. "FAIL the
//     ceiling rung is the last one on whatever ladder the pad has: the shipped
//     ladder's fastest rung is 0.5 deg/s: expected 2, got 0".

import {
  SlewController,
  SLEW_RATES,
  positionKnown,
  fastestRungIndex,
  slewRatesWithCeiling,
  type SlewControllerOpts,
  type SlewState,
  type Axis,
} from "../slewController";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`FAIL ${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

interface H {
  moves: { axis: Axis; rate: number }[];
  states: SlewState[];
  tick: () => void;
  ctrl: SlewController;
  set: { alt: number | null; known: boolean };
}

/** A controller on a continuous rate, with the altitude and the flag held in a
 *  mutable box so a case can change them between the press and the keepalive,
 *  the way a status frame does. */
function make(over: Partial<SlewControllerOpts> = {}, withFlag = true): H {
  const moves: H["moves"] = [];
  const states: SlewState[] = [];
  let registered: (() => void) | null = null;
  const set = { alt: 60 as number | null, known: true };
  const opts: SlewControllerOpts = {
    getRate: () => SLEW_RATES[2],
    reverseRa: () => false,
    reverseDec: () => false,
    getAlt: () => set.alt,
    isNina: () => false,
    postMove: async (axis, r) => { moves.push({ axis, rate: r }); },
    postNudge: async () => {},
    onStateChange: (s) => states.push(s),
    setTimer: (fn) => { registered = fn; return 1; },
    clearTimer: () => { registered = null; },
    ...(withFlag ? { getPositionKnown: () => set.known } : {}),
    ...over,
  };
  const ctrl = new SlewController(opts);
  return { moves, states, tick: () => registered?.(), ctrl, set };
}

// ------------------------------------------------------------------ 1. the flag

test("only an explicit false is 'unknown': absent, null and true all read known", () => {
  assert(positionKnown(undefined) === true, "no mount block must not lock the pad");
  assert(positionKnown(null) === true, "a null mount must not lock the pad");
  assert(positionKnown({}) === true,
    "an engine older than #144 sends no flag and must read as known");
  assert(positionKnown({ position_known: true }) === true, "an explicit true");
  assert(positionKnown({ position_known: false }) === false,
    "an explicit false is the one thing that locks");
});

// ------------------------------------------------------- 2. the guard at the gate

test("a hold below the horizon limit starts when the mount does not know where it points", () => {
  const h = make();
  h.set.alt = 3;             // the believed altitude: meaningless after a reset
  h.set.known = false;
  h.ctrl.startHold("ra", 1);
  eq(h.moves.length, 1, "the hold did not start, so the pad cannot drive a reset mount home:");
  assert(h.moves[0].rate !== 0, "the first post was a stop, not the move");
  eq(h.ctrl.getState().mode, "holding", "the controller tripped a guard that reads a wrong number:");
  h.ctrl.forceStop();
});

test("the same press with a KNOWN position still trips the guard (the control)", () => {
  const h = make();
  h.set.alt = 3;
  h.ctrl.startHold("ra", 1);
  eq(h.ctrl.getState().mode, "belowHorizon", "a known low altitude must still stop the pad:");
  assert(h.moves.every((m) => m.rate === 0), "a move was posted below the limit");
});

test("a caller that passes no flag keeps the guard exactly as it was", () => {
  const h = make({}, false);
  h.set.alt = 3;
  h.ctrl.startHold("ra", 1);
  eq(h.ctrl.getState().mode, "belowHorizon",
    "an older caller (no getPositionKnown) lost its altitude guard:");
});

// -------------------------------------------------------- 4. the guard on the tick

test("the keepalive does not trip on an altitude the mount cannot vouch for", () => {
  const h = make();
  h.ctrl.startHold("dec", 1);           // known, comfortably high: starts
  eq(h.ctrl.getState().mode, "holding", "precondition: the hold started");
  const before = h.moves.length;

  h.set.known = false;                  // a reset lands mid-hold
  h.set.alt = 2;                        // ...and the believed altitude is nonsense
  h.tick();
  eq(h.ctrl.getState().mode, "holding",
    "the keepalive stopped a hold on a believed altitude the mount cannot vouch for:");
  eq(h.moves.length, before + 1, "the keepalive did not re-assert the rate:");
  assert(h.moves[h.moves.length - 1].rate !== 0, "the re-assert was a stop");

  // The flag clearing puts the guard back: a sync landed, the altitude is real.
  h.set.known = true;
  h.tick();
  eq(h.ctrl.getState().mode, "belowHorizon",
    "once the position is known again a low altitude must stop the hold:");
});

// ------------------------------------------------------------ 5. the ceiling rung

test("the ceiling rung is the last one on whatever ladder the pad has", () => {
  eq(fastestRungIndex(SLEW_RATES), 2, "the shipped ladder's fastest rung is 0.5 deg/s:");
  const withCeiling = slewRatesWithCeiling(1.44);
  eq(fastestRungIndex(withCeiling), 3, "a 1.44 deg/s mount's ceiling rung is the fourth:");
  eq(withCeiling[fastestRungIndex(withCeiling)].id, "ceiling", "and it is the ceiling rung");
  eq(fastestRungIndex([]), 0, "an empty ladder must not answer -1:");
});

// ---------------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw16SlewControllerPositionUnknown.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
