// backendLinkReason.test.ts — UX #52 defence in depth: the tri-state grid must
// never render a bare alarm word with no cause. The backend can (and did) send
// `{ok:true, connected:false, error:null}`, which the grid maps to an orange
// DEGRADED; with a null error the reason line rendered nothing at all.
// `linkReason` is the guarantee that an alarm ALWAYS carries a sentence.
// Run: npx tsx src/lib/__tests__/backendLinkReason.test.ts

import { linkReason, linkTriState } from "../../components/settings/BackendLinkGrid";
import type { BackendLink } from "../../types";

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
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

const link = (p: Partial<BackendLink>): BackendLink => ({
  role: "guider",
  ok: false,
  error: null,
  attempted: false,
  connected: false,
  ...p,
});

test("degraded with a null error still states a reason", () => {
  const l = link({ attempted: true, ok: true, connected: false, error: null });
  assert(linkTriState(l) === "degraded", "precondition: this is the DEGRADED cell");
  const r = linkReason(l);
  assert(!!r && r.length > 20, `degraded must carry a sentence, got ${String(r)}`);
});

test("failed with a null error still states a reason", () => {
  const r = linkReason(link({ attempted: true, ok: false, connected: false }));
  assert(!!r && r.length > 20, `failed must carry a sentence, got ${String(r)}`);
});

test("the backend's own error always wins over the fallback", () => {
  const r = linkReason(link({ attempted: true, ok: false, error: "no route to host" }));
  assert(r === "no route to host", `expected the backend reason, got ${String(r)}`);
});

test("calm states carry no reason line (connected / not requested)", () => {
  assert(linkReason(link({ attempted: true, ok: true, connected: true })) === null,
    "a connected role needs no explanation");
  assert(linkReason(link({ attempted: false })) === null,
    "a not-requested role needs no explanation");
});

console.log(`\n${passed} passed, ${failed} failed`);
failures.forEach((f) => console.log(f));
if (failed > 0) {
  // No @types/node in this project — same typed guard cropRoi.test.ts uses.
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
