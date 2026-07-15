// safety.test.ts — normalizeSafety: the flat server SafetyReading dict (hello /
// status / event) and the null-monitor case collapse to a trustworthy
// SafetyState.connected (the no-safety-monitor confirm reads this).
// Run with:  npx tsx src/lib/__tests__/safety.test.ts   (from ui/)

import type { SafetyReading } from "../../types";
import { normalizeSafety } from "../safety";

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

const healthy: SafetyReading = {
  is_safe: true, reason: "", source: "cloud sensor", stale: false, ts: 1000,
};
const staleR: SafetyReading = {
  is_safe: false, reason: "safety read stale", source: "cloud sensor",
  stale: true, ts: 1000,
};

// This is the exact bug: the flat hello dict was assigned straight into
// store.safety, leaving `connected` undefined → the no-monitor confirm cried
// wolf on a healthy rig. Normalizing must yield connected === true.
test("flat hello/status dict (healthy) -> connected true", () => {
  const s = normalizeSafety(healthy);
  assert(s.connected === true, `connected=${s.connected}`);
  assert(s.reading === healthy, "reading passthrough");
  assert(s.streak === 0, "streak default 0");
});

// poll_status sends null when no monitor is present (or after a mid-session
// disconnect: the poller clears its cache to None).
test("absent monitor (null) -> connected false", () => {
  const s = normalizeSafety(null);
  assert(s.connected === false, `connected=${s.connected}`);
  assert(s.reading === null, "reading null");
});

test("undefined -> connected false", () => {
  const s = normalizeSafety(undefined);
  assert(s.connected === false, `connected=${s.connected}`);
  assert(s.reading === null, "reading null");
});

// A stale reading = the read timed out / device dropped → fail-closed: NOT
// connected (matches the pre-existing `safety` event handler semantics).
test("stale reading -> connected false", () => {
  const s = normalizeSafety(staleR);
  assert(s.connected === false, `connected=${s.connected}`);
  assert(s.reading === staleR, "reading passthrough");
});

// The UI-accumulated streak is not on any raw payload, so it must be carried
// across the wholesale status/hello replace.
test("prevStreak preserved through a status replace", () => {
  assert(normalizeSafety(healthy, 3).streak === 3, "healthy keeps streak");
  assert(normalizeSafety(null, 5).streak === 5, "null keeps streak");
  assert(normalizeSafety(staleR, 2).streak === 2, "stale keeps streak");
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nsafety.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}
export const result = { passed, failed, total };
