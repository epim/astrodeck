// Run with:  npx tsx src/components/preview/__tests__/linearReason.test.ts
import { linearUnavailableReason } from "../linearReason";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(n: string, f: () => void): void {
  try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); }
}
function assert(c: boolean, m: string): void { if (!c) throw new Error(m); }

const T = "The magnifier";

test("no frame at all does not blame NINA", () => {
  const r = linearUnavailableReason({ hasFrame: false, isNina: false, isLinear: true }, T);
  assert(r !== null, "should be unavailable");
  assert(!/nina/i.test(r!), `blames NINA on a rig with no frame and no NINA: ${r}`);
  assert(/no frame|none has been taken/i.test(r!), r!);
});

test("a NINA frame still says NINA", () => {
  const r = linearUnavailableReason({ hasFrame: true, isNina: true, isLinear: false }, T);
  assert(/nina/i.test(r!), r!);
});

test("a stretched non-NINA frame says stretched, not NINA", () => {
  const r = linearUnavailableReason({ hasFrame: true, isNina: false, isLinear: false }, T);
  assert(!/nina/i.test(r!), `blames NINA for a native stretched frame: ${r}`);
  assert(/stretched/i.test(r!), r!);
});

test("available means no reason", () => {
  assert(linearUnavailableReason({ hasFrame: true, isNina: false, isLinear: true }, T) === null,
    "invented a reason for a usable frame");
});

test("the tool names itself, so one helper serves magnifier and export", () => {
  const r = linearUnavailableReason({ hasFrame: false, isNina: false, isLinear: true },
                                    "Full-res export");
  assert(r!.startsWith("Full-res export"), r!);
});

console.log(`linearReason.test: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log(f);
if (failed > 0) process.exit(1);
