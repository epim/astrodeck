// filterSlots.test.ts — the blackout checkbox names the slot. Inline harness.
import { DARK_SLOT_NAME, isPlaceholderName, nameForOpaqueToggle } from "../filterSlots";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void) { try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = "") { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

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

console.log(`filterSlots.test.ts: ${passed} passed, ${failed} failed`);
if (failed) { failures.forEach((f) => console.error(f)); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
