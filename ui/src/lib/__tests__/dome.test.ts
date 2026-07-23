// Unit tests for the pure dome/roof formatters (PRO-4). Same inline-assert
// harness as eta.test.ts (no vitest/jest wired into this UI):
//   npx tsx src/lib/__tests__/dome.test.ts

import { domeStatusLabel, domeIsClosed, type DomeShutter } from "../dome";

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

// ---------------------------------------------------------------- domeStatusLabel
test("domeStatusLabel: every state maps to a human line", () => {
  eq(domeStatusLabel("open"), "Roof open", "open");
  eq(domeStatusLabel("closed"), "Roof closed", "closed");
  eq(domeStatusLabel("opening"), "Roof opening…", "opening");
  eq(domeStatusLabel("closing"), "Roof closing…", "closing");
  eq(domeStatusLabel("error"), "Roof error", "error");
  eq(domeStatusLabel("unknown"), "Roof status unknown", "unknown");
});

test("domeStatusLabel: an unexpected value falls back to unknown", () => {
  eq(domeStatusLabel("garbage" as DomeShutter), "Roof status unknown", "fallback");
});

// ---------------------------------------------------------------- domeIsClosed
test("domeIsClosed: only 'closed' is closed", () => {
  eq(domeIsClosed("closed"), true, "closed");
  for (const s of ["open", "opening", "closing", "unknown", "error"] as const) {
    eq(domeIsClosed(s), false, s);
  }
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ndome.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
