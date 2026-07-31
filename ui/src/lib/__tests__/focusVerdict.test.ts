// focusVerdict.test.ts — the Focus panel must not endorse a donut field.
import { DEFOCUS_R80_LIE_PX, defocusMessage, focusState } from "../focusVerdict";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void) { try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = "") { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

test("a donut field is reported as defocused, not FAIR", () => {
  // The real 2026-07-31 frame: 1393 "stars", HFR 4.5, blob r80 572.
  const s = focusState({ hfr: 4.5, stars: 1393, defocus_r80: 572 });
  eq(s.kind, "defocused", "HFR 4.5 with a 572px blob must not read as measured");
});

test("defocus outranks a healthy-looking star count AND a healthy HFR", () => {
  // Both inputs look fine in isolation; only the blob size reveals the truth.
  eq(focusState({ hfr: 2.1, stars: 500, defocus_r80: 300 }).kind, "defocused");
});

test("a genuinely focused frame still reports its HFR", () => {
  const s = focusState({ hfr: 2.4, stars: 120, defocus_r80: 3.1 });
  eq(s.kind, "measured");
  eq(s.kind === "measured" && s.hfr, 2.4);
});

test("a frame just inside the bar is still measured, not cried wolf over", () => {
  eq(focusState({ hfr: 3.0, stars: 50, defocus_r80: DEFOCUS_R80_LIE_PX }).kind,
     "measured");
});

test("an old server with no defocus field behaves exactly as before", () => {
  // Backwards compatibility: absent measurement must not become "defocused".
  eq(focusState({ hfr: 2.2, stars: 40 }).kind, "measured");
  eq(focusState({ hfr: null, stars: 40 }).kind, "few-stars");
  eq(focusState({ hfr: 2.2, stars: 1 }).kind, "few-stars");
});

test("no frame at all is its own state", () => {
  eq(focusState(null).kind, "no-frame");
  eq(focusState(undefined).kind, "no-frame");
});

test("the message states the SIZE, not just that it is bad", () => {
  // "very defocused" gives no idea whether you are one turn out or twenty.
  const m = defocusMessage(572);
  eq(m.includes("1144"), true, `must give the diameter in px: ${m}`);
  eq(/coarse focus/i.test(m), true, `must name the action: ${m}`);
});

console.log(`focusVerdict.test.ts: ${passed} passed, ${failed} failed`);
if (failed) { failures.forEach((f) => console.error(f)); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
