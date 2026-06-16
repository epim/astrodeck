// Unit tests for the additive haptics leaf (touch spec §7, R26 checklist).
//
// Same inline-assert harness as eta.test.ts (no vitest wired into this UI).
// Compiles under `tsc -b`; run directly with e.g.
//     npx tsx src/lib/__tests__/haptics.test.ts
//
// Coverage:
//   - no-op when unsupported (never throws, never calls vibrate).
//   - no-op when disabled even if supported.
//   - fires through to navigator.vibrate when enabled + supported.
//   - error() is debounced (>=1.5s) so a flaky night doesn't buzz constantly.
//   - a non-error pattern is NOT debounced.

import { haptics, __resetHapticsDebounce, type Pattern } from "../haptics";

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

// ---------------------------------------------------------------- vibrate spy
// `navigator.vibrate` may not exist in the test runtime; install a spy so we can
// assert call counts. We drive `haptics.supported` directly (its documented test
// seam) rather than depend on the host having the real API.
const calls: (number | number[])[] = [];
function installVibrate(): void {
  const nav = (globalThis as { navigator?: { vibrate?: (p: number | number[]) => boolean } }).navigator;
  const stub = (p: number | number[]) => {
    calls.push(p);
    return true;
  };
  if (nav) {
    (nav as { vibrate?: typeof stub }).vibrate = stub;
  } else {
    (globalThis as { navigator?: unknown }).navigator = { vibrate: stub };
  }
}

function reset(): void {
  calls.length = 0;
  __resetHapticsDebounce();
}

installVibrate();

// ---------------------------------------------------------------- unsupported
test("unsupported: no-op, never calls vibrate, never throws", () => {
  reset();
  haptics.supported = false;
  haptics.enabled = true;
  haptics.tap();
  haptics.error();
  eq(calls.length, 0, "no vibrate when unsupported");
});

// ---------------------------------------------------------------- disabled
test("disabled: no-op even when supported", () => {
  reset();
  haptics.supported = true;
  haptics.enabled = false;
  haptics.tap();
  eq(calls.length, 0, "no vibrate when disabled");
});

// ---------------------------------------------------------------- fires
test("enabled + supported: fires through to navigator.vibrate", () => {
  reset();
  haptics.supported = true;
  haptics.enabled = true;
  haptics.tap();
  haptics.start();
  haptics.stop();
  eq(calls.length, 3, "three patterns fired");
});

test("every named method maps to a defined pattern", () => {
  reset();
  haptics.supported = true;
  haptics.enabled = true;
  const methods: Pattern[] = ["tap", "start", "stop", "confirm", "warn", "error"];
  for (const m of methods) haptics.fire(m);
  eq(calls.length, methods.length, "all patterns fired");
  for (const c of calls) assert(typeof c === "number" || Array.isArray(c), "valid pattern payload");
});

// ---------------------------------------------------------------- error debounce
test("error() is debounced: a rapid second error is suppressed", () => {
  reset();
  haptics.supported = true;
  haptics.enabled = true;
  haptics.error();
  haptics.error(); // immediately again — within 1.5s, must be swallowed
  haptics.error();
  eq(calls.length, 1, "only the first error buzzes");
});

test("error() fires again after the debounce window is reset", () => {
  reset();
  haptics.supported = true;
  haptics.enabled = true;
  haptics.error();
  eq(calls.length, 1, "first error");
  __resetHapticsDebounce(); // simulate >1.5s elapsing
  haptics.error();
  eq(calls.length, 2, "second error after window");
});

test("non-error patterns are NOT debounced", () => {
  reset();
  haptics.supported = true;
  haptics.enabled = true;
  haptics.tap();
  haptics.tap();
  haptics.tap();
  eq(calls.length, 3, "taps never debounced");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nhaptics.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
