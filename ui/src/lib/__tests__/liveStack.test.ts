import { integratedLabel, formatLiveStack, alignmentState } from "../liveStack";
import type { LiveStackInfo } from "../../types";
let passed = 0, failed = 0; const failures: string[] = [];
function test(name: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`✗ ${name}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = "") { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

test("integratedLabel: seconds under a minute", () => eq(integratedLabel(48), "48 s"));
test("integratedLabel: minutes", () => eq(integratedLabel(1440), "24 min"));
test("integratedLabel: hours drops .0", () => eq(integratedLabel(3600), "1 h"));
test("integratedLabel: fractional hours", () => eq(integratedLabel(5400), "1.5 h"));
test("integratedLabel: negative clamps", () => eq(integratedLabel(-9), "0 s"));
test("formatLiveStack: singular frame", () => eq(formatLiveStack({ frames: 1, integrated_s: 120, rejected: 0, accepted: true }).frames, "1 frame"));
test("formatLiveStack: headline", () => eq(formatLiveStack({ frames: 12, integrated_s: 1440, rejected: 0, accepted: true }).headline, "12 frames · 24 min integrated"));
test("formatLiveStack: rejected suffix", () => eq(formatLiveStack({ frames: 12, integrated_s: 1440, rejected: 3, accepted: true }).rejected, "3 skipped"));
test("formatLiveStack: no rejected -> empty", () => eq(formatLiveStack({ frames: 5, integrated_s: 600, rejected: 0, accepted: true }).rejected, ""));

// ------------------------------------------------------------- alignmentState
// The frame count climbs whether the stack is well registered or riding on one
// star about to saturate. This mapping is the only thing that tells them apart.
const ls = (o: Partial<LiveStackInfo> = {}): LiveStackInfo =>
  ({ frames: 10, integrated_s: 300, rejected: 0, accepted: true, ...o });

function assert(c: boolean, m: string) { if (!c) throw new Error(m); }

test("alignmentState: silent before anything is stacked", () => {
  eq(alignmentState(null), null, "null");
  eq(alignmentState(undefined), null, "undefined");
  eq(alignmentState(ls({ frames: 0 })), null, "zero frames");
});

test("alignmentState: a clean match says nothing (a permanent badge is chrome)", () => {
  eq(alignmentState(ls({ reason: "", support: 7 })), null, "clean match");
  eq(alignmentState(ls({ support: 5 })), null, "older server, no reason field");
});

test("alignmentState: weak alignment is NEVER silent", () => {
  const a = alignmentState(ls({ reason: "weak_align", support: 1 }));
  assert(a !== null, "a one-star fallback must not pass unmentioned");
  eq(a!.tone, "warn", "tone");
  assert(a!.detail.toLowerCase().includes("single star"),
         "it has to say WHY it is weak, not just that it is");
});

test("alignmentState: a restart says the earlier subs are gone", () => {
  const a = alignmentState(ls({ reason: "reseed" }));
  eq(a!.label, "restarted", "label");
  eq(a!.tone, "warn", "tone");
  assert(a!.detail.includes("discarded"),
         "losing the accumulated stack is the consequence the user must see");
  eq(alignmentState(ls({ reason: "size" }))!.label, "restarted", "binning change");
});

test("alignmentState: every rejection is bad and names something to check", () => {
  for (const reason of ["drift", "no_match", "no_stars"] as const) {
    const a = alignmentState(ls({ reason }));
    eq(a!.tone, "bad", `${reason} tone`);
    assert(a!.detail.length > 20, `${reason} must suggest a cause, not just a code`);
  }
});

test("alignmentState: the labels are distinct, so the badge alone identifies it", () => {
  const reasons = ["weak_align", "reseed", "drift", "no_match", "no_stars"] as const;
  const labels = reasons.map((r) => alignmentState(ls({ reason: r }))!.label);
  eq(new Set(labels).size, labels.length, "two states share a label");
});

const total = passed + failed;
console.log(`\nliveStack.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));
export const result = { passed, failed, total };
