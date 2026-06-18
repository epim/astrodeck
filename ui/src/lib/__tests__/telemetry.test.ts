// telemetry.test.ts — staleness-gate logic (the "stale only when connected" fix).
// The server's 2s status poller only runs while a rig is connected, so with
// NOTHING connected the /ws socket is quiet by design; the banner must not alarm
// "TELEMETRY CATCHING UP" in that case.
//
// computeTelemetryStale is pure (no store / no browser globals), so unlike the
// other lane-1 tests this needs no localStorage/document stubs. Run with:
//     npx tsx src/lib/__tests__/telemetry.test.ts
// It also compiles under `tsc -b`, so the build gate covers it.

import { computeTelemetryStale } from "../telemetry";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

const STALE_MS = 20000;

// ============================================================ disconnected ⇒ never stale
test("nothing connected ⇒ NEVER stale, even past the age threshold", () => {
  assert(
    computeTelemetryStale({ connected: false, busy: null, ageMs: 999999, staleMs: STALE_MS }) === false,
    "disconnected + ancient age must stay fresh (no telemetry expected)",
  );
});

test("nothing connected ⇒ never stale even at age 0", () => {
  assert(
    computeTelemetryStale({ connected: false, busy: null, ageMs: 0, staleMs: STALE_MS }) === false,
    "disconnected + age 0 is fresh",
  );
});

// ============================================================ connected ⇒ original behavior
test("connected + fresh frame ⇒ not stale", () => {
  assert(
    computeTelemetryStale({ connected: true, busy: null, ageMs: 1000, staleMs: STALE_MS }) === false,
    "recent frame under threshold is fresh",
  );
});

test("connected + frames stopped past threshold ⇒ stale (the genuine 20s WS stall)", () => {
  assert(
    computeTelemetryStale({ connected: true, busy: null, ageMs: STALE_MS + 1, staleMs: STALE_MS }) === true,
    "connected with a real stall must still alarm",
  );
});

test("connected + busy long-op ⇒ not stale even past threshold", () => {
  for (const busy of ["slewing", "solving", "focusing", "capturing"]) {
    assert(
      computeTelemetryStale({ connected: true, busy, ageMs: STALE_MS + 5000, staleMs: STALE_MS }) === false,
      `busy=${busy} legitimately blocks the poll, so not stale`,
    );
  }
});

test("threshold is exclusive: exactly staleMs is NOT yet stale", () => {
  assert(
    computeTelemetryStale({ connected: true, busy: null, ageMs: STALE_MS, staleMs: STALE_MS }) === false,
    "age == staleMs is the boundary, not over it",
  );
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ntelemetry.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
