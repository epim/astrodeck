// backfillLabel: the one line the Session stack panel shows about the pass that
// folds in the subs a run had already shot before the stack was switched on.
//
//   npx tsx src/api/__tests__/sessionStackBackfill.test.ts
//
// Four states, and the distinction that matters is between the last two: a pass
// that finished and a pass that gave up look identical from a frame count
// alone, and only one of them means "there is still most of the night missing
// from this picture".

/* eslint-disable @typescript-eslint/no-explicit-any */

// `../sessionStack` pulls in lib/base.ts, which resolves the API base path
// off `window.location` at module scope, so a bare import takes the file down
// before an assertion runs. Stub first, then import dynamically (the
// coolingConfigPayload idiom).
(globalThis as unknown as { window: unknown }).window = {
  location: { pathname: "/", origin: "http://local" },
} as unknown as Window;

const { backfillLabel } = await import("../sessionStack");
type SessionStackBackfill =
  import("../sessionStack").SessionStackBackfill;

let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, m = ""): void {
  if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`);
}
function assert(c: boolean, m: string): void { if (!c) throw new Error(m); }

const bf = (o: Partial<SessionStackBackfill> = {}): SessionStackBackfill => ({
  running: false, total: 0, done: 0, added: 0, skipped: 0, failed: 0,
  channel: "", error: "", started_ts: null, finished_ts: null, available: 0,
  ...o,
});

test("nothing to say before any pass has run", () => {
  eq(backfillLabel(undefined), null, "absent block (an older server)");
  eq(backfillLabel(bf()), null, "a stack that has only ever stacked live");
});

test("a reading pass is a COUNT, not a spinner", () => {
  // A pass over ninety 26-megapixel subs is minutes long. A spinner cannot be
  // told from a hang; a number that stops moving can.
  eq(backfillLabel(bf({ running: true, total: 90, done: 34 })),
    "Stacking earlier subs: 34 of 90");
});

test("a pass whose total is not known yet still counts", () => {
  eq(backfillLabel(bf({ running: true, total: 0, done: 3 })),
    "Stacking earlier subs: 3");
});

test("a finished pass reports what it did with every frame", () => {
  eq(backfillLabel(bf({ total: 90, done: 90, added: 90 })),
    "90 earlier subs stacked");
  // 86 of 90 with no explanation reads as a bug. It is not one: two were
  // already in the stack and two could not be registered.
  eq(backfillLabel(bf({ total: 90, done: 90, added: 86, skipped: 2, failed: 2 })),
    "86 earlier subs stacked · 2 already in · 2 unusable");
  eq(backfillLabel(bf({ total: 1, done: 1, added: 1 })),
    "1 earlier sub stacked");
});

test("a pass over frames that were all already in says so", () => {
  eq(backfillLabel(bf({ total: 12, done: 12, skipped: 12 })),
    "0 earlier subs stacked · 12 already in");
});

test("a pass that gave up does not read as a finished one", () => {
  // The plain case: switched off, or reset. The sentence says it once.
  eq(backfillLabel(bf({ total: 90, done: 12, added: 12, error: "stopped" })),
    "Earlier subs: stopped at 12 of 90");
  // Anything else is a reason worth printing next to the count.
  const moved = backfillLabel(bf({ total: 90, done: 12, added: 12,
                                   error: "the stack moved to another target" }));
  assert(moved !== null && moved.includes("another target"),
    `the reason it gave up was dropped: ${String(moved)}`);
  assert(moved!.includes("12 of 90"),
    `no sign of how much of the night is still missing: ${moved}`);
});

console.log(`sessionStackBackfill: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
