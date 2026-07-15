// sessions.test.ts — pure tests for lib/sessions.ts (session cards math).
// Inline-assert harness; runs via `npx tsx`.
import { mergePreview, targetProgress } from "../sessions";
import type { SequencePlan, Session, SessionFrame, Target } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const target = (id: string, stepIds: string[], count = 4): Target => ({
  id, name: `T-${id}`, ra_hours: 0, dec_deg: 0, center: true,
  autofocus_first: true, calibration: false,
  steps: stepIds.map((sid) => ({
    id: sid, filter: "L", exposure_s: 60, gain: 100, offset: 30, binning: 1,
    count, frame_type: "Light",
  })),
});

const plan = (targets: Target[]): SequencePlan => ({
  name: "P", targets, guide: true, dither_every: 3, dither_pixels: 3,
  autofocus_every: 0, cool_to: null, cool_timeout_s: 600,
  apply_filter_offsets: true, refocus_on_temp_delta_c: 0, meridian_flip: true,
  recover_guiding: true, hfr_reject_factor: 0, park_when_done: false,
  warm_cooler_when_done: false,
});

const frame = (stepId: string, over: Partial<SessionFrame> = {}): SessionFrame => ({
  id: `f-${Math.random()}`, ts: 0, night: "n1", target_id: "t1", step_id: stepId,
  thumb: null, metrics: {}, auto_accepted: true, override: null, ...over,
});

test("targetProgress counts EFFECTIVE accepted per target, capped at count", () => {
  const p = plan([target("t1", ["s1"], 3)]);
  const frames = [
    frame("s1"),                                        // accepted
    frame("s1", { auto_accepted: false }),              // rejected
    frame("s1", { auto_accepted: false, override: "accept" }),  // regraded in
    frame("s1", { override: "reject" }),                // regraded out
    frame("s1"), frame("s1"),                           // 2 more accepted (4 total)
  ];
  const [tp] = targetProgress(p, frames);
  assert(tp.accepted === 3, `capped at count: ${tp.accepted}`);
  assert(tp.total === 3, "total = step count");
  assert(tp.name === "T-t1", "carries the target name");
});

test("mergePreview: kept / added / dropped-with-frames", () => {
  const s: Session = {
    id: "sess", schema_version: 1, name: "P", created_ts: 0, updated_ts: 0,
    status: "dormant", plan: plan([target("t1", ["s1", "s2"])]),
    nights: ["n1"], frames: [frame("s1")], auto_resume: false,
  };
  // next plan keeps s1, drops s2 (no frames -> not counted), adds s3
  const next = plan([target("t1", ["s1", "s3"])]);
  const d = mergePreview(s, next);
  assert(d.kept === 1, `kept: ${d.kept}`);
  assert(d.added === 1, `added: ${d.added}`);
  assert(d.dropped === 0, "s2 had no frames -> not dropped-with-progress");
  // dropping the frame-bearing step reports it
  const d2 = mergePreview(s, plan([target("t1", ["s2"])]));
  assert(d2.dropped === 1, `dropped: ${d2.dropped}`);
});

console.log(`sessions.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
