// Unit tests for lane-2A's Monitor ETA/format math (monitor spec §10).
//
// There is no vitest/jest wired into this UI (build is `tsc -b && vite build`),
// so these use the same tiny inline-assert harness as foundation.test.ts /
// store.test.ts. They compile under `tsc -b` and run directly with a TS-aware
// runner, e.g.  npx tsx src/lib/__tests__/eta.test.ts
// Each `test(...)` maps 1:1 to an `it(...)` if a real runner lands later.

import {
  fmtDuration,
  fmtClock,
  fmtCountdown,
  deriveFinish,
  LIVE_WINDOW_S,
  COOLER_AT_TARGET_C,
  ETA_MIN_FRAMES,
} from "../eta";

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

// ---------------------------------------------------------------- constants
test("constants: shared timing values match spec §9", () => {
  eq(LIVE_WINDOW_S, 8, "LIVE_WINDOW_S");
  eq(COOLER_AT_TARGET_C, 1.0, "COOLER_AT_TARGET_C");
  eq(ETA_MIN_FRAMES, 3, "ETA_MIN_FRAMES");
});

// ---------------------------------------------------------------- fmtDuration
test("fmtDuration: hours mode drops seconds (2h 2m)", () => {
  eq(fmtDuration(7340), "2h 2m", "7340s");
});
test("fmtDuration: minutes mode shows m+s (1m 35s)", () => {
  eq(fmtDuration(95), "1m 35s", "95s");
});
test("fmtDuration: under a minute is seconds only", () => {
  eq(fmtDuration(9), "9s", "9s");
});
test("fmtDuration: exactly one hour", () => {
  eq(fmtDuration(3600), "1h 0m", "3600s");
});
test("fmtDuration: exactly one minute", () => {
  eq(fmtDuration(60), "1m 0s", "60s");
});
test("fmtDuration: negative clamps to 0s", () => {
  eq(fmtDuration(-5), "0s", "negative");
});

// ---------------------------------------------------------------- fmtClock
test("fmtClock: same-day pads HH:MM", () => {
  const now = new Date(2026, 5, 15, 3, 5, 0).getTime();
  eq(fmtClock(new Date(2026, 5, 15, 3, 41, 0).getTime(), now), "03:41", "same day");
});
test("fmtClock: next-day instant appends (+1d)", () => {
  // 23:50 "now" → finish at 00:30 the next calendar day.
  const now = new Date(2026, 5, 15, 23, 50, 0).getTime();
  const finish = new Date(2026, 5, 16, 0, 30, 0).getTime();
  eq(fmtClock(finish, now), "00:30 (+1d)", "midnight rollover");
});
test("fmtClock: a few minutes before midnight is still same-day", () => {
  const now = new Date(2026, 5, 15, 23, 40, 0).getTime();
  const finish = new Date(2026, 5, 15, 23, 55, 0).getTime();
  eq(fmtClock(finish, now), "23:55", "no rollover");
});

// ---------------------------------------------------------------- fmtCountdown
test("fmtCountdown: below an hour is MM:SS", () => {
  eq(fmtCountdown(95), "01:35", "95s");
});
test("fmtCountdown: boundary 3599 stays MM:SS", () => {
  eq(fmtCountdown(3599), "59:59", "3599s");
});
test("fmtCountdown: boundary 3600 flips to H:MM", () => {
  eq(fmtCountdown(3600), "1:00", "3600s");
});
test("fmtCountdown: above an hour is H:MM", () => {
  eq(fmtCountdown(7325), "2:02", "7325s");
});
test("fmtCountdown: negative clamps to 00:00", () => {
  eq(fmtCountdown(-10), "00:00", "negative");
});

// ---------------------------------------------------------------- deriveFinish
test("deriveFinish: remaining counts down monotonically from one clock", () => {
  const received = 1_000_000;
  const eta = 600; // 10 min
  const a = deriveFinish(eta, received, received); // at emit
  const b = deriveFinish(eta, received, received + 60_000); // +60s
  const c = deriveFinish(eta, received, received + 120_000); // +120s
  near(a.remainingS, 600, 1e-6, "at emit");
  near(b.remainingS, 540, 1e-6, "+60s");
  near(c.remainingS, 480, 1e-6, "+120s");
  assert(a.remainingS > b.remainingS && b.remainingS > c.remainingS, "monotonic decrease");
});
test("deriveFinish: finishAtMs is stable as the clock advances", () => {
  const received = 2_000_000;
  const eta = 300;
  const a = deriveFinish(eta, received, received);
  const b = deriveFinish(eta, received, received + 90_000);
  // finishAtMs = nowMs + remaining*1000 — invariant while not overdue.
  near(a.finishAtMs, b.finishAtMs, 1e-6, "finish instant stable");
  near(a.finishAtMs, received + eta * 1000, 1e-6, "finish == emit + eta");
});
test("deriveFinish: overdue run clamps remaining to 0 (no negative time)", () => {
  const received = 3_000_000;
  const r = deriveFinish(120, received, received + 200_000); // 80s past finish
  eq(r.remainingS, 0, "clamped remaining");
  near(r.finishAtMs, received + 200_000, 1e-6, "finish == now when overdue");
});

// ----------------------------------------------------- in-flight non-double-count
// Mirror of the engine's off-by-one guard (monitor spec §5.3 / §10): the
// remaining-capture estimate must NEVER include the in-flight frame, which is
// accounted once as the in-flight residual. We model the same arithmetic the
// engine emits and assert eta == remaining_capture + in_flight + overhead.
test("eta assembly: in-flight frame counted exactly once", () => {
  const exposure = 120;
  const framesRemainingAfterCurrent = 4; // frames with index > current step's in-flight
  const remainingCaptureS = framesRemainingAfterCurrent * exposure; // EXCLUDES in-flight
  const elapsedThisFrame = 30;
  const inFlight = Math.max(0, exposure - elapsedThisFrame); // 90s residual, counted once
  const overhead = 12 * (framesRemainingAfterCurrent + 1); // per remaining frame incl. in-flight
  const etaS = remainingCaptureS + inFlight + overhead;
  // The in-flight 120s frame contributes ONLY its 90s residual, not a full 120s.
  eq(remainingCaptureS, 480, "remaining excludes in-flight full frame");
  eq(inFlight, 90, "in-flight residual");
  eq(etaS, 480 + 90 + 60, "assembled eta");
  // A naive double-count would treat the in-flight frame as a full extra frame in
  // remaining_capture (overstating by one exposure minus the residual we already
  // counted). The honest assembly must be exactly that much smaller.
  const naiveRemaining = (framesRemainingAfterCurrent + 1) * exposure; // wrong: includes in-flight
  const naiveEta = naiveRemaining + inFlight + overhead;
  eq(naiveEta - etaS, exposure, "double-count overstates by exactly one exposure");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\neta.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
