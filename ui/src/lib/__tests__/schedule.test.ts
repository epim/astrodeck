// schedule.test.ts — collapsed-chip summary for the per-target autorun schedule.
// Run with:  npx tsx src/lib/__tests__/schedule.test.ts   (from ui/)

import { scheduleSummary } from "../schedule";

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

test("all defaults -> Runs immediately", () => {
  assert(scheduleSummary({ start_mode: "now", start_offset_min: 0, start_time: null,
    min_altitude_deg: 0, stop_mode: "none", stop_offset_min: 0, stop_time: null,
    max_run_min: 0, on_missed: "wait" }) === "Runs immediately", "defaults");
});
test("undefined -> Runs immediately", () => {
  assert(scheduleSummary(undefined) === "Runs immediately", "undefined");
});
test("dusk +30 to dawn", () => {
  const s = scheduleSummary({ start_mode: "dusk", start_offset_min: 30, start_time: null,
    min_altitude_deg: 0, stop_mode: "dawn", stop_offset_min: 0, stop_time: null,
    max_run_min: 0, on_missed: "wait" });
  assert(s === "Dusk +30m → dawn", `got "${s}"`);
});
test("time start, cap, skip", () => {
  const s = scheduleSummary({ start_mode: "time", start_offset_min: 0, start_time: "22:00",
    min_altitude_deg: 0, stop_mode: "none", stop_offset_min: 0, stop_time: null,
    max_run_min: 90, on_missed: "skip" });
  assert(s === "22:00 → max 90m · skip if missed", `got "${s}"`);
});
test("altitude gate only", () => {
  const s = scheduleSummary({ start_mode: "now", start_offset_min: 0, start_time: null,
    min_altitude_deg: 35, stop_mode: "none", stop_offset_min: 0, stop_time: null,
    max_run_min: 0, on_missed: "wait" });
  assert(s === "Alt ≥ 35°", `got "${s}"`);
});
test("negative dawn offset on stop", () => {
  const s = scheduleSummary({ start_mode: "dusk", start_offset_min: 0, start_time: null,
    min_altitude_deg: 0, stop_mode: "dawn", stop_offset_min: -20, stop_time: null,
    max_run_min: 0, on_missed: "wait" });
  assert(s === "Dusk → dawn −20m", `got "${s}"`);
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nschedule.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}
export const result = { passed, failed, total };
