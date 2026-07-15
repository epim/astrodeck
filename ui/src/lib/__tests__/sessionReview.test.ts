// sessionReview.test.ts — pure tests for lib/sessionReview.ts (review drawer
// filtering / selection / local override). Inline-assert harness via `npx tsx`.
import {
  effectiveAccepted, filterFrames, toggleSel, verdictOf, withOverride,
} from "../sessionReview";
import type { SessionFrame } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

let n = 0;
const frame = (over: Partial<SessionFrame> = {}): SessionFrame => ({
  id: `f${n++}`, ts: 0, night: "n1", target_id: "t1", step_id: "s1",
  thumb: null, metrics: { hfr: 2 }, auto_accepted: true, override: null, ...over,
});

test("verdict + effective acceptance", () => {
  assert(verdictOf(frame()) === "accepted", "auto accepted");
  assert(verdictOf(frame({ auto_accepted: false })) === "rejected", "auto rejected");
  assert(verdictOf(frame({ override: "reject" })) === "overridden", "override badge wins");
  assert(effectiveAccepted(frame({ auto_accepted: false, override: "accept" })), "override accept counts");
  assert(!effectiveAccepted(frame({ override: "reject" })), "override reject discounts");
});

test("filterFrames: target / night / verdict semantics", () => {
  const fs = [
    frame({ target_id: "t1", night: "n1" }),                          // accepted
    frame({ target_id: "t2", night: "n2", auto_accepted: false }),    // rejected
    frame({ target_id: "t1", night: "n2", override: "reject" }),      // overridden (eff. rejected)
  ];
  assert(filterFrames(fs, { target_id: "t1" }).length === 2, "target filter");
  assert(filterFrames(fs, { night: "n2" }).length === 2, "night filter");
  assert(filterFrames(fs, { verdict: "accepted" }).length === 1, "accepted = EFFECTIVE");
  assert(filterFrames(fs, { verdict: "rejected" }).length === 2, "rejected = EFFECTIVE (incl. override)");
  assert(filterFrames(fs, { verdict: "overridden" }).length === 1, "overridden = has override");
  assert(filterFrames(fs, {}).length === 3, "no filters = all");
});

test("toggleSel is a pure toggle", () => {
  let sel: string[] = [];
  sel = toggleSel(sel, "a");
  assert(sel.includes("a"), "added");
  const before = sel;
  sel = toggleSel(sel, "a");
  assert(!sel.includes("a") && before.includes("a"), "removed, input untouched");
});

test("withOverride touches only the matching frame", () => {
  const fs = [frame(), frame()];
  const out = withOverride(fs, fs[0].id, "reject");
  assert(out[0].override === "reject", "target frame updated");
  assert(out[1] === fs[1], "other frame reference-equal");
  assert(fs[0].override === null, "input not mutated");
});

console.log(`sessionReview.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
