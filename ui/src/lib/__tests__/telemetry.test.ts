// telemetry.test.ts — staleness-gate logic (the "stale only when connected" fix).
// The server's 2s status poller only runs while a rig is connected, so with
// NOTHING connected the /ws socket is quiet by design; the banner must not alarm
// "TELEMETRY CATCHING UP" in that case.
//
// computeTelemetryStale is pure (no store / no browser globals), so unlike the
// other lane-1 tests this needs no localStorage/document stubs. Run with:
//     npx tsx src/lib/__tests__/telemetry.test.ts
// It also compiles under `tsc -b`, so the build gate covers it.

import {
  HARD_STALE_MS, computeTelemetryStale, formatAge, telemetryStaleNotice,
} from "../telemetry";

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

// ------------------------------------------------- #224: busy cannot veto forever
//
// Reported 2026-08-10 03:40 — "any idea why the last capture was 2 hours ago".
// It hadn't been. Frame 0125 had landed 30 seconds earlier and the rig was
// guiding at RMS 1.11; the operator's VIEW was two hours old and said nothing.
//
// `busy` is read from the client's own last status message. An outage that
// begins mid-capture — which, during a sequence, is nearly always — freezes it
// at "capture", and the message that would clear it is the message that stopped
// arriving. So the veto never lifted and the watchdog stayed disarmed by the
// very outage it existed to catch.

test("THE BUG: a two-hour gap that began mid-capture is STALE", () => {
  assert(
    computeTelemetryStale({
      connected: true, busy: "capture", ageMs: 2 * 60 * 60 * 1000, staleMs: STALE_MS,
    }) === true,
    "two hours of silence was reported as fine because busy was frozen at 'capture'",
  );
});

test("a long op still buys its normal grace", () => {
  // The whole point of the veto is preserved below the ceiling: an exposure
  // legitimately blocks the poll and must not raise an alarm.
  assert(
    computeTelemetryStale({
      connected: true, busy: "capture", ageMs: HARD_STALE_MS - 1000, staleMs: STALE_MS,
    }) === false,
    "a gap inside the ceiling is still excused by a long op",
  );
});

test("the ceiling is past the worst legitimate gap on this rig", () => {
  // 180 s Ha sub + dither + settle + filter move + download is the longest
  // honest quiet; the ceiling must clear it with room, and must be nowhere near
  // the two hours that went unreported.
  assert(HARD_STALE_MS > 240_000, "ceiling is under a real Ha sub plus its tail");
  assert(HARD_STALE_MS < 30 * 60 * 1000, "ceiling is so high it would miss a real outage");
});

test("every busy value loses to the ceiling", () => {
  for (const busy of ["slewing", "solving", "focusing", "capturing", "capture"]) {
    assert(
      computeTelemetryStale({
        connected: true, busy, ageMs: HARD_STALE_MS + 1, staleMs: STALE_MS,
      }) === true,
      `busy=${busy} still vetoed past the ceiling`,
    );
  }
});

test("disconnected is still never stale, even past the ceiling", () => {
  // With no rig there is no telemetry to be behind on, and this gate fails in
  // the honest direction: a client stuck on "nothing connected" under-claims.
  assert(
    computeTelemetryStale({
      connected: false, busy: null, ageMs: 5 * 60 * 60 * 1000, staleMs: STALE_MS,
    }) === false,
    "alarmed about telemetry from a rig that is not connected",
  );
});

// ------------------------------------------------- the words the operator reads

test("a short gap reads as catching up, not as an alarm", () => {
  const n = telemetryStaleNotice(25_000);
  assert(n !== null && n.level === "warn", "a 25s gap should be a warning");
  assert(n!.detail.includes("25 seconds"), `age missing from: ${n!.detail}`);
});

test("a long gap STATES THE AGE and escalates", () => {
  const n = telemetryStaleNotice(2 * 60 * 60 * 1000);
  assert(n !== null && n.level === "error", "two hours should not be a mere warning");
  assert(n!.title.includes("2 HOURS"), `age missing from title: ${n!.title}`);
});

test("the severe copy RULES OUT the wrong conclusion", () => {
  // The false conclusion that made this expensive at 3am: a frozen screen read
  // as a rig that had stopped imaging. The banner reports what the BROWSER
  // knows — that its own view is old — and has to say out loud that this is
  // not a statement about the rig, which was imaging perfectly the whole time.
  //
  // Asserting the ABSENCE of "rig has stopped" is not the test: the denial
  // itself contains that phrase. What matters is that the sentence is a denial
  // and that it says whose fault the silence is not.
  const n = telemetryStaleNotice(2 * 60 * 60 * 1000)!;
  const words = (n.title + " " + n.detail).toLowerCase();
  assert(words.includes("does not mean the rig has stopped"),
         `banner does not rule out the wrong conclusion: ${n.detail}`);
  assert(!words.includes("capture failed"), "banner asserts a capture failure");
  assert(!words.includes("sequence stopped"), "banner asserts the sequence stopped");
  // And it must say what IS true: the numbers on screen are that old.
  assert(words.includes("on screen is that old") || words.includes("that old"),
         `banner does not say the screen is stale: ${n.detail}`);
});

test("age reads in units a human uses", () => {
  assert(formatAge(1000) === "1 second", formatAge(1000));
  assert(formatAge(45_000) === "45 seconds", formatAge(45_000));
  assert(formatAge(6 * 60_000) === "6 minutes", formatAge(6 * 60_000));
  assert(formatAge(2 * 3600_000) === "2 hours", formatAge(2 * 3600_000));
  assert(formatAge(2 * 3600_000 + 5 * 60_000) === "2 hours 5 minutes",
         formatAge(2 * 3600_000 + 5 * 60_000));
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
