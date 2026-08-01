// filterSlots.test.ts — the blackout checkbox names the slot, and picking a
// filter must never look identical to not picking one. Inline harness.
import {
  DARK_SLOT_NAME, FILTER_STUCK_AFTER_MS, filterMotion, isPlaceholderName,
  nameForOpaqueToggle, type FilterCommand,
} from "../filterSlots";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void) { try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = "") { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function ok(c: boolean, m: string) { if (!c) throw new Error(m); }

test("ticking blackout on an unnamed slot names it DARK", () => {
  eq(nameForOpaqueToggle("", 2, true, undefined), DARK_SLOT_NAME);
});

test("ticking blackout on a default 'Slot N' names it DARK", () => {
  eq(nameForOpaqueToggle("Slot 3", 2, true, undefined), DARK_SLOT_NAME);
});

test("ticking blackout NEVER overwrites a name somebody typed", () => {
  // The one variant to avoid: a user who called it "Blank" or "DARK 2" keeps it.
  eq(nameForOpaqueToggle("Blank", 2, true, undefined), "Blank");
  eq(nameForOpaqueToggle("DARK 2", 2, true, undefined), "DARK 2");
});

test("unticking restores the name blackout replaced", () => {
  eq(nameForOpaqueToggle(DARK_SLOT_NAME, 4, false, "Ha 3nm"), "Ha 3nm");
});

test("unticking with nothing to restore falls back to the slot default", () => {
  // Leaving DARK on a slot that now passes light is exactly the mislabel this
  // is meant to prevent — darks would file under a filter that transmits.
  eq(nameForOpaqueToggle(DARK_SLOT_NAME, 4, false, undefined), "Slot 5");
});

test("unticking leaves a real filter name alone", () => {
  eq(nameForOpaqueToggle("L", 0, false, undefined), "L");
});

test("placeholder detection is per-slot and case-insensitive", () => {
  eq(isPlaceholderName("Slot 1", 0), true);
  eq(isPlaceholderName("slot 1", 0), true);
  eq(isPlaceholderName("Slot 2", 0), false, "another slot's default is a real name here");
  eq(isPlaceholderName("   ", 0), true);
  eq(isPlaceholderName("L", 0), false);
});

// ---------------------------------------------------- picking a filter (#115)

const NAMES = ["L", "R", "G", "B", "Ha", "OIII", "SII", "DARK"];
const T0 = 1_000_000;
// The user is on R (slot 1) and picks L (slot 0) — the exact tap from the field.
const pick = (over: Partial<FilterCommand> = {}): FilterCommand =>
  ({ slot: 0, startedAt: T0, from: 1, ...over });

test("picking a filter is visible the instant it is picked", () => {
  // The whole bug: the pick spawns a background task and the slot NAME cannot
  // change until the carousel lands, so the screen looked exactly as it did
  // before the tap for several seconds.
  const m = filterMotion(pick(), 1, true, NAMES, T0);
  eq(m.pulsing, true);
  ok(m.summary.includes("L"), `must name the slot asked for: ${m.summary}`);
  eq(m.problem, null);
});

test("the pulse survives a status poll that has not caught up yet", () => {
  // The poll is 2s and the command was spawned, so `moving` is legitimately
  // still false on the first status after the tap. Accusing the wheel there
  // would cry wolf at every single filter change.
  const m = filterMotion(pick(), 1, false, NAMES, T0 + 1500);
  eq(m.pulsing, true);
  eq(m.problem, null);
});

test("a wheel still reporting motion keeps pulsing past the stuck clock", () => {
  // The device's own flag outranks any timer: a slow wheel that is genuinely
  // turning must never be called stuck.
  const m = filterMotion(pick(), 1, true, NAMES, T0 + FILTER_STUCK_AFTER_MS * 3);
  eq(m.pulsing, true);
  eq(m.problem, null);
});

test("arriving stops the pulse and just shows the filter name", () => {
  // Verbatim from the report: "Then show the filter name."
  const m = filterMotion(pick(), 0, false, NAMES, T0 + 4000);
  eq(m.pulsing, false);
  eq(m.summary, "L");
  eq(m.problem, null);
});

test("a wheel that never turned says so, and names BOTH slots", () => {
  // "it didn't move" is not actionable; "still on R, asked for L" points at the
  // wheel. This is the case a fixed-duration CSS animation would have hidden.
  const m = filterMotion(pick(), 1, false, NAMES, T0 + FILTER_STUCK_AFTER_MS + 1);
  eq(m.pulsing, false);
  ok(m.problem != null, "a wheel that ignored the command must not be silent");
  ok(/did not turn/.test(m.problem!), `never budged is its own fault: ${m.problem}`);
  ok(/\bR\b/.test(m.problem!), `must name where it is: ${m.problem}`);
  ok(/\bL\b/.test(m.problem!), `must name where it was asked for: ${m.problem}`);
});

test("a wheel that turned and mis-seated is a DIFFERENT sentence", () => {
  // Landed on G having been asked for L, from R. A seized motor and a carousel
  // that over-ran by a slot need different things done to them, so they must
  // not read the same.
  const m = filterMotion(pick(), 2, false, NAMES, T0 + FILTER_STUCK_AFTER_MS + 1);
  ok(!/did not turn/.test(m.problem!), `it DID turn: ${m.problem}`);
  ok(/\bG\b/.test(m.problem!) && /\bL\b/.test(m.problem!),
    `must name landed-on and asked-for: ${m.problem}`);
});

test("a backend that cannot report motion still gets a pulse and still gets caught", () => {
  // `moving` undefined = "cannot say" (the hub omits the key). It must behave
  // like the not-yet-caught-up case, not like a claim of stillness.
  eq(filterMotion(pick(), 1, undefined, NAMES, T0 + 500).pulsing, true);
  ok(filterMotion(pick(), 1, undefined, NAMES, T0 + FILTER_STUCK_AFTER_MS + 1).problem != null,
    "the timer is the only witness left when the backend has no flag");
});

test("motion outranks the position, because ASCOM's -1 is clamped to 0", () => {
  // A move TO slot 0 on an Alpaca wheel reads position 0 for the whole turn
  // (get_position clamps the -1 'moving' sentinel). Trusting position first
  // would call that arrived the instant it was commanded.
  const m = filterMotion(pick({ slot: 0 }), 0, true, NAMES, T0 + 100);
  eq(m.pulsing, true);
  ok(m.summary.includes("L"), `must still be going to L: ${m.summary}`);
});

test("a filter change nobody on this screen asked for still shows as motion", () => {
  // A sequence step turned the wheel. We do not know its target and the
  // position is mid-move garbage on at least one backend, so name neither.
  const m = filterMotion(null, 1, true, NAMES, T0);
  eq(m.pulsing, true);
  eq(m.problem, null);
  ok(!/\bR\b/.test(m.summary), `must not claim a slot it cannot know: ${m.summary}`);
});

test("an idle wheel is just its slot name", () => {
  const m = filterMotion(null, 4, false, NAMES, T0);
  eq(m.summary, "Ha");
  eq(m.pulsing, false);
  eq(m.problem, null);
});

test("an unnamed slot falls back to its number, never to a blank button", () => {
  eq(filterMotion(null, 2, false, ["L", "R", "  "], T0).summary, "#3");
  eq(filterMotion(null, 5, false, ["L"], T0).summary, "#6");
});

console.log(`filterSlots.test.ts: ${passed} passed, ${failed} failed`);
if (failed) { failures.forEach((f) => console.error(f)); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
