// incidentLinkGrace.test.ts - the LINK LOST card must not be the first thing a
// cold start says (review #16).
//
//   Run directly:  npx tsx src/next/__tests__/incidentLinkGrace.test.ts
//   Also run by `npm test` and type-checked by `tsc -b`.
//
// WHAT THIS GUARDS. `store.ts:1023` initialises `wsPhase: "connecting"` with
// `wsLastEvent: Date.now()`, and `lib/incidents.ts` returned a LINK incident for
// any phase that was not "up". So EVERY cold start painted
//
//     LINK LOST - This phone cannot reach the rig computer
//
// zero seconds old, plus the Session tab's pulsing dot and the amber banner,
// until the socket opened. The first thing the app ever said was that it was
// broken, on a rig that was fine.
//
// The suppression has to be NARROW or it hides the real thing. Two facts make
// it safe, and both are asserted below:
//   * "connecting" is the phase that means NEVER UP (`ws.ts:48` picks
//     `everConnected ? "reconnecting" : "connecting"`), and a socket that opened
//     and dropped goes to "down". So a real drop can never be suppressed.
//   * the grace is bounded. A socket that still has not opened after 5 s IS a
//     link the user needs to be told about, and staying quiet would be the
//     mirror-image defect.
//
// SABOTAGE CHECKS (each names the assertion that goes red):
//   * delete the grace -> "a cold start says nothing about the link".
//   * make the grace unbounded (drop the elapsed comparison) -> "a socket that
//     never opens is reported once the grace lapses".
//   * widen it to any phase instead of "connecting" -> "a RECONNECTING socket
//     is a real drop" and "a DOWN socket is a real drop".

import { deriveIncidents, FIRST_LINK_GRACE_MS, type IncidentInputs } from "../lib/incidents";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, m = ""): void {
  if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`);
}
function ok(cond: boolean, m: string): void { if (!cond) throw new Error(m); }

const NOW = 1_700_000_000_000;

/** The store's ACTUAL initial state, not a convenient fiction: this is what
 *  `store.ts:1023-1024` sets before the socket has ever been asked to open. */
function coldStart(): IncidentInputs {
  return {
    sequence: { state: "idle" },
    safety: null,
    wsPhase: "connecting",
    telemetryStale: false,
    wsLastEvent: NOW,
    mountOp: null,
    focus: null,
    lastAutofocusResult: null,
    guide: null,
    disk: null,
    cooler: null,
    logs: [],
    lastCaptureAtMs: null,
    expectedFrameS: null,
  };
}

const linkOf = (inp: IncidentInputs, nowMs: number) =>
  deriveIncidents(inp, nowMs).find((i) => i.kind === "link") ?? null;

test("a cold start says nothing about the link on the first frame", () => {
  eq(linkOf(coldStart(), NOW), null,
    "the very first paint announced LINK LOST over a rig nobody had tried to reach yet:");
});

test("and stays quiet for the whole grace", () => {
  const inp = coldStart();
  eq(linkOf(inp, NOW + FIRST_LINK_GRACE_MS - 1), null, "just inside the grace:");
  ok(FIRST_LINK_GRACE_MS === 5000, "the grace is the reviewed 5 s");
});

test("a socket that never opens IS reported once the grace lapses", () => {
  const inp = coldStart();
  const inc = linkOf(inp, NOW + FIRST_LINK_GRACE_MS + 1);
  ok(inc != null, "staying quiet forever is the mirror-image defect - the rig really is unreachable");
  eq(inc!.title, "LINK LOST");
});

test("a DOWN socket is a real drop and is reported immediately", () => {
  const inp = coldStart();
  inp.wsPhase = "down";
  ok(linkOf(inp, NOW) != null, "a socket that opened and dropped must never be suppressed");
});

test("a RECONNECTING socket is a real drop and is reported immediately", () => {
  // `ws.ts:48` only says "reconnecting" when the socket HAS been up, which is
  // exactly the case the grace must not cover.
  const inp = coldStart();
  inp.wsPhase = "reconnecting";
  ok(linkOf(inp, NOW) != null, "a reconnect after a real drop must still be announced");
});

test("stale telemetry on a healthy socket is still an incident", () => {
  const inp = coldStart();
  inp.wsPhase = "up";
  inp.telemetryStale = true;
  ok(linkOf(inp, NOW) != null, "an open socket with nothing coming down it is the STALE case");
});

test("a healthy link has no incident at all", () => {
  const inp = coldStart();
  inp.wsPhase = "up";
  eq(linkOf(inp, NOW + 60_000), null, "a working link must not be reported as anything");
});

const total = passed + failed;
console.log(`incidentLinkGrace.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
export { passed, failed, total };
