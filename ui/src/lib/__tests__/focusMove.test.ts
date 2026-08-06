// focusMove.test.ts — pressing Go must never look identical to not pressing Go.
import {
  ARRIVAL_TOLERANCE_STEPS, ARRIVED_LINGER_MS, MOVE_IN_FLIGHT_REASON,
  STALL_GRACE_MS, STALL_LINGER_MS, anchorBlocker, moveProgress, retireAfterMs,
  type FocuserCommand,
} from "../focusMove";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void) { try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = "") { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function ok(c: boolean, m: string) { if (!c) throw new Error(m); }

const T0 = 1_000_000;
const cmd = (over: Partial<FocuserCommand> = {}): FocuserCommand =>
  ({ target: 22000, startedAt: T0, from: 360, ...over });

test("nothing commanded says nothing", () => {
  eq(moveProgress(null, 12000, false, T0, T0), null);
});

test("a move under way is visible the moment it is commanded", () => {
  // The whole bug: at t=0 the panel looked exactly as it did before the tap.
  const p = moveProgress(cmd(), 360, true, T0, T0);
  ok(p != null, "must say something immediately");
  eq(p!.tone, "info");
  ok(p!.text.includes("22000"), `must name the target: ${p!.text}`);
});

test("the line carries where it has got to, not just where it is going", () => {
  const p = moveProgress(cmd(), 14475, true, T0 + 3000, T0 + 3000);
  ok(p!.text.includes("14475"), `must show live position: ${p!.text}`);
  ok(p!.text.includes("22000"), `must still show target: ${p!.text}`);
});

test("arriving is reported, and outranks a stale moving flag", () => {
  const p = moveProgress(cmd(), 22000, true, T0 + 60_000, T0 + 60_000);
  eq(p!.tone, "good");
  eq(p!.settled, true);
});

test("arrival tolerates the driver's own slop", () => {
  // zwo_usb.py calls this arrived; a stricter UI would wait forever.
  const p = moveProgress(cmd(), 22000 - ARRIVAL_TOLERANCE_STEPS, false,
                         T0 + 60_000, T0 + 60_000);
  eq(p!.tone, "good");
});

test("a refused move is called out — the 2026-07-31 failure", () => {
  // Commanded 22000, firmware clamped at 360, no motion, no error for minutes.
  const p = moveProgress(cmd(), 360, false, T0 + STALL_GRACE_MS, T0);
  eq(p!.tone, "warn");
  eq(p!.settled, true);
  ok(p!.text.includes("360"), `must say where it actually is: ${p!.text}`);
  ok(p!.text.includes("22000"), `must say what was asked: ${p!.text}`);
});

test("a slow motor start is not called a stall", () => {
  const p = moveProgress(cmd(), 360, false, T0 + STALL_GRACE_MS - 1, T0);
  eq(p!.tone, "info", "one poll of silence must not cry wolf");
});

test("visible progress keeps the stall clock reset on a long move", () => {
  // A 20000-step EAF move takes minutes. Position advancing is proof of motion
  // even from a backend whose is_moving() cannot answer.
  const now = T0 + 120_000;
  const p = moveProgress(cmd(), 17627, undefined, now, now - 1000);
  eq(p!.tone, "info", "a move that is visibly progressing is not stalled");
});

test("a backend that cannot report motion still gets a stall verdict", () => {
  // moving===undefined must not become "assume it's fine forever".
  const p = moveProgress(cmd(), 360, undefined, T0 + STALL_GRACE_MS + 1, T0);
  eq(p!.tone, "warn");
});

test("no position yet means no claim either way", () => {
  eq(moveProgress(cmd(), null, true, T0, T0), null);
  eq(moveProgress(cmd(), undefined, true, T0, T0), null);
});

test("position 0 is a real position, not a missing one", () => {
  const p = moveProgress(cmd({ target: 5000 }), 0, false, T0 + 60_000, T0);
  ok(p != null, "0 must not be treated as absent");
  eq(p!.tone, "warn");
});

// ------------------------------------------------------ retiring the command
// A command held forever is why the NEXT motion of the focuser — a sweep, a
// coarse walk, the sequencer — was narrated as the user's last Go and ended in
// an orange "not moving, asked for 22000" about a move that finished minutes
// earlier. The Focus page's own version of this is asserted end-to-end in
// views/__tests__/focusHonesty.test.tsx; the DELAYS are here, because a test
// cannot sit through 90 seconds of them.

const retire = (over: Partial<Parameters<typeof retireAfterMs>[0]> = {}) =>
  retireAfterMs({ settled: false, tone: "info", sweepOwnsFocuser: false,
                  sequenceRunning: false, ...over });

test("a move still under way is not retired", () => {
  eq(retire(), null);
});

test("an arrived move lingers long enough to read, then goes", () => {
  eq(retire({ settled: true, tone: "good" }), ARRIVED_LINGER_MS);
  ok(ARRIVED_LINGER_MS >= 5000, "too short to read after looking at the image");
});

test("a REFUSED move outstays a confirmation — it is the fault report", () => {
  const warn = retire({ settled: true, tone: "warn" });
  eq(warn, STALL_LINGER_MS);
  ok(warn! > ARRIVED_LINGER_MS,
     "the 'stopped at 360, asked for 22000' line must outlive an 'at 22000'");
});

test("a sweep taking the focuser retires it immediately, mid-move or not", () => {
  // The numbers are the sweep's now — every frame we keep the old target is a
  // frame that reports somebody else's motion as ours.
  eq(retire({ sweepOwnsFocuser: true }), 0);
  eq(retire({ settled: true, tone: "warn", sweepOwnsFocuser: true }), 0);
});

test("a running sequence retires a FINISHED command but never a live one", () => {
  // Nudging during a run is allowed, and a nudge with no narration is the
  // failure this whole file exists to fix. What must not survive is the
  // finished target, which the run's own next refocus would be reported as.
  eq(retire({ sequenceRunning: true }), null, "an in-flight nudge lost its narration");
  eq(retire({ settled: true, tone: "good", sequenceRunning: true }), 0);
});

test("a REFUSED move keeps its fault report even under a running sequence", () => {
  // The case the first cut of the sequence gate erased. A run does not take the
  // focuser away from the operator — the docstring above says nudging during
  // one is allowed — so the nudge that the firmware REFUSES happens under a
  // sequence as readily as without one. Retiring at 0 there clears `cmd` in the
  // same commit that first renders "not moving — stopped at 360, asked for
  // 22000": the one line this module exists to print, deleted on the frame it
  // appears, leaving the tap looking exactly like not having tapped.
  //
  // Only the ARRIVED confirmation is expendable to a run — "at 22000" has
  // already been believed by then, and what it costs to keep is the run's next
  // refocus being narrated as the user's Go.
  const warn = retire({ settled: true, tone: "warn", sequenceRunning: true });
  ok(warn !== 0, "the refusal was erased on the frame it appeared");
  eq(warn, STALL_LINGER_MS, "a refusal under a run gets the same linger as one without");
});

test("the in-flight refusal names the way out", () => {
  // A second move is a 409 off the `focuser` lane; Halt is the only way to end
  // the first one early, and it is the button directly beside the refusal.
  ok(/halt/i.test(MOVE_IN_FLIGHT_REASON), MOVE_IN_FLIGHT_REASON);
});

// ------------------------------------------------------- re-anchoring guard

const anchor = (over: Partial<Parameters<typeof anchorBlocker>[0]> = {}) =>
  anchorBlocker({ canFocus: true, hasFocuser: true, supported: true,
                  moving: false, raw: "22000", max: 40000, ...over });

test("a valid re-anchor is allowed", () => {
  eq(anchor(), null);
});

test("every block states its own reason rather than sitting greyed out", () => {
  // House rule: a disabled control the user cannot interrogate is a dead end.
  for (const [label, over] of [
    ["read-only", { canFocus: false }],
    ["no focuser", { hasFocuser: false }],
    ["unsupported", { supported: false }],
    ["moving", { moving: true }],
    ["blank", { raw: "  " }],
    ["not a number", { raw: "abc" }],
    ["negative", { raw: "-5" }],
    ["past max", { raw: "99999" }],
  ] as const) {
    const r = anchor(over as object);
    ok(typeof r === "string" && r.length > 0, `${label} must give a reason`);
  }
});

test("re-anchoring is refused mid-move — the number would land stale", () => {
  ok(/moving/i.test(anchor({ moving: true })!), "must say why");
});

test("the past-max reason names the limit, not just 'too big'", () => {
  ok(anchor({ raw: "99999" })!.includes("40000"), "must name the max");
});

test("position 0 is a legitimate anchor", () => {
  eq(anchor({ raw: "0" }), null);
});

console.log(`focusMove.test.ts: ${passed} passed, ${failed} failed`);
if (failed) { failures.forEach((f) => console.error(f)); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
