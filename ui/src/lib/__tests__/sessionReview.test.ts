// sessionReview.test.ts — pure tests for lib/sessionReview.ts (review drawer
// filtering / selection / local override). Inline-assert harness via `npx tsx`.
import {
  effectiveAccepted, filterFrames, pruneSelection, toggleSel, verdictOf, withOverride,
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

test("pruneSelection keeps only ids in the visible set", () => {
  const fs = [frame(), frame()];
  const both = [fs[0].id, fs[1].id];
  assert(pruneSelection(both, fs).length === 2, "kept when visible");
  const pruned = pruneSelection([fs[0].id, fs[1].id], [fs[0]]);
  assert(pruned.length === 1 && pruned[0] === fs[0].id, "dropped when hidden");
  assert(pruneSelection([], fs).length === 0, "empty selection stays empty");
});

test("withOverride touches only the matching frame", () => {
  const fs = [frame(), frame()];
  const out = withOverride(fs, fs[0].id, "reject");
  assert(out[0].override === "reject", "target frame updated");
  assert(out[1] === fs[1], "other frame reference-equal");
  assert(fs[0].override === null, "input not mutated");
});

// ---------------------------------------------- filter band (UX #37)
test("filterFrames: filters by filter band via the step resolver", () => {
  const fs = [frame({ step_id: "ha" }), frame({ step_id: "oiii" }), frame({ step_id: "ha" })];
  const bandOf = (id: string) => (id === "ha" ? "Ha" : "OIII");
  const out = filterFrames(fs, { filter: "Ha" }, bandOf);
  assert(out.length === 2, `2 Ha frames, got ${out.length}`);
  assert(filterFrames(fs, { filter: "OIII" }, bandOf).length === 1, "1 OIII frame");
  assert(filterFrames(fs, { filter: "SII" }, bandOf).length === 0, "no SII frames");
});
test("filterFrames: band filter combines with the existing keys", () => {
  const fs = [frame({ step_id: "ha", night: "n1", auto_accepted: false }),
              frame({ step_id: "ha", night: "n2" }),
              frame({ step_id: "oiii", night: "n1" })];
  const bandOf = (id: string) => (id === "ha" ? "Ha" : "OIII");
  const out = filterFrames(fs, { filter: "Ha", night: "n1" }, bandOf);
  assert(out.length === 1 && out[0].night === "n1", "band AND night");
  assert(filterFrames(fs, { filter: "Ha", verdict: "accepted" }, bandOf).length === 1,
    "band AND verdict");
});
test("filterFrames: band filter is inert without a resolver (never hides all)", () => {
  const fs = [frame({ step_id: "ha" }), frame({ step_id: "oiii" })];
  assert(filterFrames(fs, { filter: "Ha" }).length === 2, "inert, not empty");
});
test("filterFrames: no band filter → resolver never narrows anything", () => {
  const fs = [frame({ step_id: "ha" }), frame({ step_id: "oiii" })];
  assert(filterFrames(fs, {}, () => "Ha").length === 2, "unfiltered");
});

console.log(`sessionReview.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
