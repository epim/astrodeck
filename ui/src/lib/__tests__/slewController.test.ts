// Unit tests for the manual-slew controller (touch spec §4.2 checklist).
//
// Same inline-assert harness as eta.test.ts / foundation.test.ts (no vitest wired
// into this UI). Compiles under `tsc -b`; run directly with e.g.
//     npx tsx src/lib/__tests__/slewController.test.ts
//
// Coverage (touch spec §14 "slewController" checklist):
//   - NO acceleration: hold posts the selected fixed rate ONCE; the keepalive only
//     re-asserts the SAME rate (never a larger one).
//   - tap vs hold: a quick press = one nudge; a real hold = a move then a 0.
//   - idempotent stop: forceStop twice posts 0 once-per-call, never throws.
//   - clamp: a rate above TOUCH_MAX_RATE_DEG_S is clamped at post time.
//   - alt-guard trip: dropping below MIN_SLEW_ALT_DEG mid-keepalive forceStops and
//     emits `belowHorizon`.
//   - NINA path: hold is disabled; a tap routes through postNudge (rel-GOTO).

import {
  SlewController,
  SLEW_RATES,
  TOUCH_MAX_RATE_DEG_S,
  MIN_SLEW_ALT_DEG,
  KEEPALIVE_MS,
  rateById,
  rateGlyph,
  type SlewControllerOpts,
  type SlewState,
  type Axis,
  type Dir,
} from "../slewController";

// Server deadman window the keepalive must keep fed (hub._move_watchdog).
const MOVE_DEADMAN_MS = 1200;

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
function near(a: number, b: number, tol: number, msg = ""): void {
  if (Math.abs(a - b) > tol) throw new Error(`${msg} expected ~${b}, got ${a}`);
}

// ---------------------------------------------------------------- fake clock/timer
// A controllable harness: `now` is a mutable number; `setTimer` registers one
// interval callback that we fire manually via `tick()`.
interface Harness {
  moves: { axis: Axis; rate: number }[];
  nudges: { axis: Axis; dir: Dir }[];
  states: SlewState[];
  errors: unknown[];
  setNow: (n: number) => void;
  tick: () => void; // fire the keepalive once
  opts: SlewControllerOpts;
}

function makeHarness(over: Partial<SlewControllerOpts> = {}): Harness {
  const moves: Harness["moves"] = [];
  const nudges: Harness["nudges"] = [];
  const states: SlewState[] = [];
  const errors: unknown[] = [];
  let clock = 1000;
  let registered: (() => void) | null = null;

  const opts: SlewControllerOpts = {
    // defaults: 0.5°/s rate, well above the horizon, alpaca (not NINA). Tests that
    // need other values pass them via `over`.
    getRate: () => SLEW_RATES[2],
    reverseRa: () => false,
    reverseDec: () => false,
    getAlt: () => 60,
    isNina: () => false,
    postMove: async (axis, r) => {
      moves.push({ axis, rate: r });
    },
    postNudge: async (axis, dir) => {
      nudges.push({ axis, dir });
    },
    onStateChange: (s) => states.push(s),
    onError: (e) => errors.push(e),
    now: () => clock,
    setTimer: (fn) => {
      registered = fn;
      return 1;
    },
    clearTimer: () => {
      registered = null;
    },
    ...over,
  };

  return {
    moves,
    nudges,
    states,
    errors,
    setNow: (n) => {
      clock = n;
    },
    tick: () => {
      if (registered) registered();
    },
    opts,
  };
}

// ---------------------------------------------------------------- constants
test("constants: rates computed from real sidereal; touch cap + alt guard set", () => {
  eq(SLEW_RATES[0].id, "pulse", "pulse first");
  eq(SLEW_RATES[0].rateDegS, 0, "pulse rate 0 (tap-only)");
  near(SLEW_RATES[1].rateDegS, 0.0334, 1e-9, "8x sidereal");
  eq(SLEW_RATES[2].rateDegS, 0.5, "set rate");
  eq(TOUCH_MAX_RATE_DEG_S, 0.6, "touch cap");
  eq(MIN_SLEW_ALT_DEG, 10, "min slew alt");
});

test("rateById / rateGlyph helpers", () => {
  eq(rateById("fine").id, "fine", "lookup fine");
  eq(rateById("nope" as never).id, "pulse", "unknown -> first");
  eq(rateGlyph("pulse"), "▰", "pulse glyph");
  eq(rateGlyph("fine"), "▰▰", "fine glyph");
  eq(rateGlyph("set"), "▰▰▰", "set glyph");
});

// ---------------------------------------------------------------- NO acceleration
test("hold posts the selected fixed rate ONCE; keepalive re-asserts the SAME rate", () => {
  const h = makeHarness();
  const c = new SlewController(h.opts);
  c.startHold("ra", 1);
  eq(h.moves.length, 1, "one post on start");
  eq(h.moves[0].rate, 0.5, "posts 0.5°/s");
  // three keepalive ticks: each re-asserts the SAME 0.5 — never larger (no ramp).
  h.tick();
  h.tick();
  h.tick();
  eq(h.moves.length, 4, "start + 3 keepalive posts");
  for (const m of h.moves) eq(m.rate, 0.5, "rate never accelerates");
  c.stopHold();
});

// ------------------------------------------------- F-A2 keepalive/deadman margin
test("F-A2: keepalive period is ~½ the 1200ms deadman so ≥3 stamps land per window", () => {
  // The widened margin (was 1000ms → 200ms slack) is what keeps a single jittered
  // foreground tick from tripping the server deadman early (false mid-slew STOP).
  eq(KEEPALIVE_MS, 600, "keepalive lowered to ~600ms (F-A2)");
  assert(KEEPALIVE_MS <= MOVE_DEADMAN_MS / 2, "≤ ½ the deadman window");
  // stamps within a window = the leading stamp (t=0) + one per keepalive interval.
  const stampsPerWindow = 1 + Math.floor(MOVE_DEADMAN_MS / KEEPALIVE_MS);
  assert(stampsPerWindow >= 3, `≥3 stamps per deadman window (got ${stampsPerWindow})`);
  // jitter tolerance = the slack between one keepalive and the deadman.
  const jitterToleranceMs = MOVE_DEADMAN_MS - KEEPALIVE_MS;
  assert(jitterToleranceMs >= 500, `≥500ms jitter tolerance (got ${jitterToleranceMs}ms)`);
});

test("F-A2: the keepalive is wired at exactly KEEPALIVE_MS, re-stamping the same rate", () => {
  let period = -1;
  const moves: number[] = [];
  let fn: (() => void) | null = null;
  const c = new SlewController({
    getRate: () => SLEW_RATES[2], // 0.5°/s
    reverseRa: () => false,
    reverseDec: () => false,
    getAlt: () => 60,
    isNina: () => false,
    postMove: async (_a, r) => {
      moves.push(r);
    },
    postNudge: async () => {},
    setTimer: (f, ms) => {
      fn = f;
      period = ms;
      return 1;
    },
    clearTimer: () => {
      fn = null;
    },
  });
  c.startHold("ra", 1);
  eq(period, KEEPALIVE_MS, "interval armed at KEEPALIVE_MS (~600ms)");
  // re-stamp at the real cadence three times within one deadman window.
  fn!();
  fn!();
  fn!();
  eq(moves.length, 4, "start + 3 re-stamps");
  for (const r of moves) eq(r, 0.5, "each stamp re-asserts the SAME rate (no ramp)");
  c.stopHold();
});

// ---------------------------------------------------------------- tap vs hold
test("tapNudge routes through postNudge (one nudge), no move posts", () => {
  const h = makeHarness();
  const c = new SlewController(h.opts);
  c.tapNudge("dec", 1);
  eq(h.nudges.length, 1, "one nudge");
  eq(h.nudges[0].axis, "dec", "axis");
  eq(h.nudges[0].dir, 1, "dir");
  eq(h.moves.length, 0, "no continuous move from a tap");
});

test("F-D4: quick press+release at a continuous rate is a brief slew, NOT a double-move", () => {
  const h = makeHarness();
  const c = new SlewController(h.opts);
  c.beginPress("ra", -1); // starts a hold (0.5°/s rate, dir -1)
  eq(h.moves[0].rate, -0.5, "hold started (west = -0.5°/s)");
  h.setNow(1100); // 100ms later (< TAP_MAX_MS 200) — still a quick stab
  c.endPress("ra", -1);
  // The old behavior fired BOTH a stop(0) AND a tapNudge (a brief slew + a pulse =
  // the "quick-tap double-move"). The single-model fix: a continuous-rate press is
  // a hold; release just stops. NO nudge is fired at a continuous rate.
  const last = h.moves[h.moves.length - 1];
  eq(last.rate, 0, "stop posted 0");
  eq(h.nudges.length, 0, "no extra nudge — not a double-move");
  eq(h.moves.length, 2, "exactly one move + one stop, never a third pulse command");
});

test("a real hold (beyond tap window) ends with a stop, NO extra nudge", () => {
  const h = makeHarness();
  const c = new SlewController(h.opts);
  c.beginPress("ra", 1);
  h.setNow(2000); // 1000ms later (> TAP_MAX_MS)
  c.endPress("ra", 1);
  eq(h.nudges.length, 0, "no nudge after a genuine hold");
  eq(h.moves[h.moves.length - 1].rate, 0, "stop posted 0");
});

// ------------------------------------------------- F-A4 multi-touch contract (S2)
test("F-A4: isHolding() is the gate SlewPad uses to reject a 2nd concurrent press", () => {
  // SlewPad rejects a second pointerdown while isHolding() — proving that contract:
  // a live hold reports true; once we honor the gate (don't start a 2nd hold), the
  // first axis is never overwritten and stays the commanded axis through release.
  const h = makeHarness();
  const c = new SlewController(h.opts);
  c.beginPress("ra", 1); // first finger: continuous-rate hold on RA
  eq(c.isHolding(), true, "holding after first press");
  eq(c.getState().axis, "ra", "RA is the held axis");

  // Honoring the gate, SlewPad would NOT call beginPress for a second finger here.
  // Releasing the FIRST (and only) press must stop RA cleanly — no stranded axis.
  c.endPress("ra", 1);
  eq(c.isHolding(), false, "released");
  eq(h.moves[h.moves.length - 1].rate, 0, "RA stopped (posted 0) — not stranded");
  eq(c.getState().axis, null, "no axis left commanded");
});

// ---------------------------------------------------------------- idempotent stop
test("forceStop is idempotent and posts 0 (never throws when already stopped)", () => {
  const h = makeHarness();
  const c = new SlewController(h.opts);
  c.startHold("dec", 1);
  const before = h.moves.length;
  c.forceStop();
  eq(h.moves[h.moves.length - 1].rate, 0, "forceStop posts 0");
  const afterFirst = h.moves.length;
  assert(afterFirst > before, "forceStop posted while holding");
  c.forceStop(); // second call — nothing held, must not post or throw
  eq(h.moves.length, afterFirst, "second forceStop is a no-op post-wise");
  eq(c.isHolding(), false, "not holding");
});

// ---------------------------------------------------------------- clamp
test("rate above the touch cap is clamped at post time", () => {
  const h = makeHarness({ getRate: () => ({ id: "set", label: "x", rateDegS: 4.0 }) });
  const c = new SlewController(h.opts);
  c.startHold("ra", 1);
  eq(h.moves[0].rate, TOUCH_MAX_RATE_DEG_S, "clamped to +0.6");
  c.stopHold();
});

test("reverse flips the commanded sign", () => {
  const h = makeHarness({ reverseRa: () => true });
  const c = new SlewController(h.opts);
  c.startHold("ra", 1); // dir +1, reversed -> negative
  eq(h.moves[0].rate, -0.5, "reverse-RA negates");
  c.stopHold();
});

// ---------------------------------------------------------------- alt-guard
test("alt-guard at the gate: won't start a hold below the horizon limit", () => {
  const h = makeHarness({ getAlt: () => 5 }); // below MIN_SLEW_ALT_DEG
  const c = new SlewController(h.opts);
  c.startHold("ra", 1);
  eq(h.moves.length, 0, "no move posted below horizon");
  eq(c.getState().mode, "belowHorizon", "latched belowHorizon");
});

test("alt-guard trip mid-hold: keepalive forceStops + emits belowHorizon", () => {
  let alt = 30;
  const h = makeHarness({ getAlt: () => alt });
  const c = new SlewController(h.opts);
  c.startHold("ra", 1);
  eq(h.moves.length, 1, "moving");
  alt = 8; // drift below the limit
  h.tick(); // keepalive checks alt -> trip
  eq(h.moves[h.moves.length - 1].rate, 0, "auto-stopped on trip");
  eq(c.getState().mode, "belowHorizon", "belowHorizon emitted");
  eq(c.isHolding(), false, "no longer holding");
});

// ---------------------------------------------------------------- NINA path
test("NINA mode: startHold is a no-op (no continuous move)", () => {
  const h = makeHarness({ isNina: () => true });
  const c = new SlewController(h.opts);
  c.startHold("ra", 1);
  eq(h.moves.length, 0, "NINA: no continuous slew");
  eq(c.isHolding(), false, "not holding in NINA");
});

test("NINA mode: a press becomes a tap nudge (relative GOTO via postNudge)", () => {
  const h = makeHarness({ isNina: () => true });
  const c = new SlewController(h.opts);
  c.beginPress("dec", 1);
  eq(h.moves.length, 0, "NINA press starts no hold");
  c.endPress("dec", 1);
  eq(h.nudges.length, 1, "NINA press resolves to a nudge");
});

test("pulse rate is tap-only: startHold no-ops even in alpaca mode", () => {
  const h = makeHarness({ getRate: () => SLEW_RATES[0] }); // pulse
  const c = new SlewController(h.opts);
  c.startHold("ra", 1);
  eq(h.moves.length, 0, "pulse rate never holds");
});

// ---------------------------------------------------------------- error handling
test("a failed move post escalates to a stop and surfaces onError", async () => {
  const moves: number[] = [];
  let first = true;
  const h = makeHarness({
    postMove: async (_axis, r) => {
      moves.push(r);
      if (first && r !== 0) {
        first = false;
        throw new Error("network");
      }
    },
  });
  const c = new SlewController(h.opts);
  c.startHold("ra", 1);
  // allow the async postMove rejection + recovery stop to settle.
  await Promise.resolve();
  await Promise.resolve();
  assert(h.errors.length >= 1, "onError surfaced");
  assert(moves.includes(0), "recovery stop posted 0");
  eq(c.isHolding(), false, "not holding after a failed command");
});

// A failed move must also REPAINT THE PAD, not only toast (audit #37).
//
// The escalation above was already tested; what was missing is the half the
// user looks at. Every other exit from a hold ends in `emit()` — stopHold,
// forceStop, tripBelowHorizon — and this one did not, so the arrow stayed lit
// and the feedback line kept reading "HOLD · 0.50°/s" over a mount that had
// stopped: the toast said one thing and the control said the opposite.
//
// Driven at module scope rather than inside `test(...)` because this harness's
// `test` does not await an async body: assertions made after an await land in a
// promise nobody inspects, and the file would score them as passes.
const emitH = makeHarness({
  postMove: async (_axis, r) => { if (r !== 0) throw new Error("network"); },
});
const emitC = new SlewController(emitH.opts);
emitC.startHold("ra", 1);
const emitsAtPress = emitH.states.map((s) => s.mode);
// three ticks: the failing postMove, its best-effort zero, and the catch.
await Promise.resolve();
await Promise.resolve();
await Promise.resolve();
const emitsAfterFailure = emitH.states.map((s) => s.mode);

test("a failed move post repaints the pad instead of leaving the arrow lit", () => {
  // PRECONDITION. Without this, "the last state is idle" is true from the very
  // first render of a pad that never held anything, and the assertion below
  // would pass with the fix reverted.
  eq(emitsAtPress.length, 1, "the press should have emitted exactly one state:");
  eq(emitsAtPress[0], "holding", "the press must actually start a hold:");

  assert(emitsAfterFailure.length > emitsAtPress.length,
    "the failure emitted NO state change — the controller went idle while the pad " +
    "still shows the lit arrow and the hold rate it is no longer slewing at");
  eq(emitsAfterFailure[emitsAfterFailure.length - 1], "idle",
    "the state the pad was last told:");
  const last = emitH.states[emitH.states.length - 1];
  eq(last.axis, null, "the arrow highlight is keyed on axis; it must be cleared:");
  eq(last.dir, null, "…and on dir:");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nslewController.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
