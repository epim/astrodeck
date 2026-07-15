// ids.test.ts — pure tests for lib/ids.ts (sessions spec §1 client-side
// id generation + backfill). Inline-assert harness; runs via `npx tsx`.
import { ensurePlanIds, uid } from "../ids";
import type { SequencePlan, Target } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const target = (over: Partial<Target> = {}): Target => ({
  name: "M42", ra_hours: 5.6, dec_deg: -5.4, center: true, autofocus_first: true,
  calibration: false,
  steps: [{ filter: "L", exposure_s: 120, gain: 100, offset: 30, binning: 1, count: 10, frame_type: "Light" }],
  ...over,
});

const plan = (targets: Target[]): SequencePlan => ({
  name: "P", targets, guide: true, dither_every: 3, dither_pixels: 3,
  autofocus_every: 0, cool_to: null, cool_timeout_s: 600,
  apply_filter_offsets: true, refocus_on_temp_delta_c: 0, meridian_flip: true,
  recover_guiding: true, hfr_reject_factor: 0, park_when_done: false,
  warm_cooler_when_done: false,
});

test("uid() is 32 hex chars and unique", () => {
  const a = uid(); const b = uid();
  assert(/^[0-9a-f]{32}$/.test(a), `uid shape: ${a}`);
  assert(a !== b, "two uids differ");
});

test("backfills missing target AND step ids", () => {
  const out = ensurePlanIds(plan([target()]));
  assert(!!out.targets[0].id, "target id backfilled");
  assert(!!out.targets[0].steps[0].id, "step id backfilled");
});

test("preserves existing ids", () => {
  const t = target({ id: "tkeep" });
  t.steps[0].id = "skeep";
  const out = ensurePlanIds(plan([t]));
  assert(out.targets[0].id === "tkeep", "target id kept");
  assert(out.targets[0].steps[0].id === "skeep", "step id kept");
});

test("reference-preserving when nothing is missing", () => {
  const complete = ensurePlanIds(plan([target()]));
  const again = ensurePlanIds(complete);
  assert(again === complete, "same object back when all ids present");
});

test("only touched targets are re-created (React re-render scoping)", () => {
  const done = ensurePlanIds(plan([target()])).targets[0];
  const missing = target();
  const out = ensurePlanIds(plan([done, missing]));
  assert(out.targets[0] === done, "complete target reference-equal");
  assert(out.targets[1] !== missing && !!out.targets[1].id, "incomplete target re-created with id");
});

console.log(`ids.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
