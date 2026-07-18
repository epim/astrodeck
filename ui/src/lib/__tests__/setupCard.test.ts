// setupCard.test.ts — pure tests for lib/setupCard.ts (I4 guided "Secure this
// server" card, 2026-07-17 decisions wave). No browser/store deps — the
// module under test is pure, so no stubs needed. Inline-assert harness via
// `npx tsx`, matching the house idiom (see caps.test.ts / drivers.test.ts).
//     npx tsx src/lib/__tests__/setupCard.test.ts

import { setupCardState } from "../setupCard";

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

// ---------------------------------------------------------------- visibility
test("visible only while auth is open (methodsEnabled=false) and not dismissed", () => {
  assert(setupCardState(false, false, false).visible, "open + not dismissed -> visible");
  assert(!setupCardState(true, false, false).visible, "methods enabled -> never visible");
  assert(!setupCardState(true, true, false).visible, "methods enabled + admin done -> still never visible");
  assert(!setupCardState(false, false, true).visible, "dismissed -> hidden even while open");
  assert(!setupCardState(false, true, true).visible, "dismissed -> hidden even with admin done");
});

test("reappears once auth returns to open, as long as it was never dismissed", () => {
  // closed (methods enabled) -> hidden regardless of dismissed history
  assert(!setupCardState(true, true, false).visible, "closed stays hidden");
  // auth re-opens (methods dropped back to []): visible again UNLESS dismissed
  assert(setupCardState(false, true, false).visible, "re-opened + never dismissed -> visible");
  assert(!setupCardState(false, true, true).visible, "re-opened but dismissed -> stays hidden (needs 'Setup guide' un-dismiss)");
});

// ---------------------------------------------------------------- step1Done
test("step1Done mirrors hasEnabledAdmin exactly, independent of methodsEnabled/dismissed", () => {
  assert(setupCardState(false, true, false).step1Done, "admin exists -> step1 done");
  assert(!setupCardState(false, false, false).step1Done, "no admin -> step1 not done");
  assert(setupCardState(true, true, true).step1Done, "step1Done still reported true even when card not visible");
  assert(!setupCardState(true, false, true).step1Done, "step1Done still reported false even when card not visible");
});

// -------------------------------------------------------- step2Enabled (ORDER)
// This is the rule the campaign's external reviewers tripped on twice:
// creating a user must NOT unlock step 2 by itself — only an ENABLED
// admin-role local user (hasEnabledAdmin=true) does.
test("step2Enabled is LOCKED until step1 (an enabled admin) exists", () => {
  assert(!setupCardState(false, false, false).step2Enabled, "no enabled admin -> step2 locked");
  assert(setupCardState(false, true, false).step2Enabled, "enabled admin exists -> step2 unlocked");
});

test("step2Enabled tracks hasEnabledAdmin regardless of methodsEnabled/dismissed", () => {
  assert(setupCardState(true, true, true).step2Enabled, "still reports unlocked even when card hidden");
  assert(!setupCardState(true, false, true).step2Enabled, "still reports locked even when card hidden");
});

// ------------------------------------------------------------------ full matrix
test("full 2x2x2 matrix matches the documented truth table", () => {
  for (const methodsEnabled of [false, true]) {
    for (const hasEnabledAdmin of [false, true]) {
      for (const dismissed of [false, true]) {
        const s = setupCardState(methodsEnabled, hasEnabledAdmin, dismissed);
        const expectVisible = !methodsEnabled && !dismissed;
        assert(
          s.visible === expectVisible,
          `visible mismatch for (${methodsEnabled},${hasEnabledAdmin},${dismissed})`,
        );
        assert(s.step1Done === hasEnabledAdmin, `step1Done mismatch for (${methodsEnabled},${hasEnabledAdmin},${dismissed})`);
        assert(s.step2Enabled === hasEnabledAdmin, `step2Enabled mismatch for (${methodsEnabled},${hasEnabledAdmin},${dismissed})`);
      }
    }
  }
});

// ---------------------------------------------------------------- report
console.log(`setupCard.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  // No @types/node in this Vite/browser project; reach process via globalThis so
  // `tsc -b` (browser lib, no Node types) still compiles this file cleanly
  // (same workaround as src/lib/__tests__/drivers.test.ts / src/__tests__/ws.test.ts).
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
