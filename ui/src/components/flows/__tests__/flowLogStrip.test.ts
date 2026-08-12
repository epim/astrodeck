// flowLogStrip.test.ts — the two rules the log strip cannot get wrong quietly.
//   Run:  npx tsx src/components/flows/__tests__/flowLogStrip.test.ts   (from ui/)
//
// The strip itself is a render; there is nothing to test in a <button>. These
// two derivations are different:
//
//   * NEWEST FIRST. `logs.slice(-60).reverse()` is what makes a 170px panel
//     usable mid-run — the line you want is the one that just landed. Drop the
//     reverse and the panel still renders, still scrolls, still shows sixty
//     lines, and puts the interesting one out of sight below the fold. Nothing
//     about that looks broken in a screenshot.
//   * The TONE LADDER differs from the toast ladder by one rung (§C.12: the
//     log's default is --text-dim, a toast's is --accent). A file that reached
//     for the toast map would be wrong in exactly one of four cases.
/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- globals first
// FlowLogStrip imports the store, which reaches lib/base.ts — and base.ts reads
// `window.location.pathname` AT MODULE SCOPE to derive the relay mount. So the
// import has to happen after a window exists, which means a dynamic import.
// Same stub as flowsSlice.test.ts; nothing here fakes behaviour under test.
(globalThis as any).window = {
  location: { pathname: "/", origin: "http://local" },
  addEventListener() {}, removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
(globalThis as any).localStorage = {
  getItem: () => null, setItem() {}, removeItem() {},
};
// store.ts also runs three DOM writes at module scope (night class, touch
// sizing, brightness vars), so a window alone is not enough to import it.
(globalThis as any).document = {
  documentElement: {
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    style: { setProperty() {}, removeProperty() {} },
  },
};

const { logTail, logTime, LOG_TAIL, LOG_TONE_CLASS, IDLE_LOG_TEXT } =
  await import("../FlowLogStrip");
const { LOG_RING } = await import("../flowsSlice");
type FlowLogLine = import("../flowsTypes").FlowLogLine;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const line = (id: number): FlowLogLine =>
  ({ id, ts: 0, msg: `line ${id}`, tone: "info" });

test("the panel shows the newest line FIRST", () => {
  const out = logTail([line(1), line(2), line(3)]);
  assert(out[0].id === 3, `newest first — got id ${out[0]?.id}, which is the oldest`);
  assert(out[out.length - 1].id === 1, "…and the oldest last");
});

test("a full 120-entry ring yields exactly the newest 60", () => {
  const logs = Array.from({ length: LOG_RING }, (_, i) => line(i + 1));
  const out = logTail(logs);
  assert(out.length === LOG_TAIL, `showed ${out.length} of a ${LOG_RING} ring, want ${LOG_TAIL}`);
  assert(out[0].id === LOG_RING, "the first row is the newest line in the ring");
  assert(out[LOG_TAIL - 1].id === LOG_RING - LOG_TAIL + 1,
    "the last row is 60 back — not the oldest of the 120");
});

test("logTail does not mutate the store's array", () => {
  // `reverse()` is in-place. Reversing the ring itself would reorder every
  // other reader of `flows.logs` — and zustand hands out the live array.
  const logs = [line(1), line(2), line(3)];
  logTail(logs);
  assert(logs[0].id === 1, "the caller's array survived the reverse()");
});

test("an empty ring yields nothing, and the bar says so in words", () => {
  assert(logTail([]).length === 0, "no lines, no rows");
  assert(IDLE_LOG_TEXT === "Idle — no events yet",
    "the idle sentence is the one the design specifies (and the only honest one "
    + "while no flow.log event exists)");
});

test("the log's default rung is dim, NOT the toast's accent", () => {
  assert(LOG_TONE_CLASS.info === "text-dim",
    `info reads ${LOG_TONE_CLASS.info}; accent here would make every routine `
    + "line shout as loudly as a warning");
  assert(LOG_TONE_CLASS.good === "text-good", "good");
  assert(LOG_TONE_CLASS.warn === "text-warn", "warn");
  assert(LOG_TONE_CLASS.bad === "text-bad", "bad");
});

test("timestamps are 24-hour and zero-padded whatever the viewer's locale", () => {
  // 2026-08-12T04:07:09Z read back in the runtime's own zone — the point is the
  // SHAPE, not the hour, so the assertion is on the pattern.
  const t = logTime(Date.UTC(2026, 7, 12, 4, 7, 9));
  assert(/^\d{2}:\d{2}:\d{2}$/.test(t),
    `got "${t}" — a log that reads "4:07:09 am" cannot be lined up against `
    + "captures/logs/<night>.jsonl, which is where a failed night is actually read");
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failures.length) { failures.forEach((f) => console.log(f)); process.exit(1); }
export default { passed, failed, total: passed + failed };
