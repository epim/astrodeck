// Pure-lib test for format.ts. Sabotage check: reusing eta.ts's fmtDuration
// verbatim (no space before a lone "s") turns the "45 s" worked example red;
// dropping the RA wrap-to-[0,24) turns the negative-hours fmtRA test red;
// forgetting the unicode minus in fmtDec turns the negative-declination test red.
import { fmtBytes, fmtClock, fmtDec, fmtDeg, fmtDuration, fmtPct, fmtRA } from "../format";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = ""): void { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

// T0.3 brief worked examples: fmtDuration(s) -> "1h 30m", "45 s".
test("fmtDuration: 5400s -> \"1h 30m\"", () => eq(fmtDuration(5400), "1h 30m"));
test("fmtDuration: 45s -> \"45 s\"", () => eq(fmtDuration(45), "45 s"));
test("fmtDuration: 754s -> \"12m 34s\" (minutes tier keeps seconds)", () => eq(fmtDuration(754), "12m 34s"));
test("fmtDuration: negative clamps to 0s", () => eq(fmtDuration(-5), "0 s"));

test("fmtClock: same local calendar day, no suffix", () => {
  const now = new Date(2026, 0, 15, 9, 0, 0).getTime();
  const at = new Date(2026, 0, 15, 10, 0, 0).getTime();
  eq(fmtClock(at, now), "10:00");
});

test("fmtClock: next local calendar day gets a (+1d) suffix", () => {
  const now = new Date(2026, 0, 15, 9, 0, 0).getTime();
  const at = new Date(2026, 0, 16, 10, 0, 0).getTime();
  eq(fmtClock(at, now), "10:00 (+1d)");
});

// gallery.ts's own pinned worked example, reused via re-export.
test("fmtBytes: 41017345024 bytes -> \"38.2 GB\" (1024-based, reused from lib/gallery.ts)", () => {
  eq(fmtBytes(41017345024), "38.2 GB");
});

test("fmtDeg: whole and fractional digits", () => {
  eq(fmtDeg(58.3, 0), "58°");
  eq(fmtDeg(2.5401, 2), "2.54°");
});

test("fmtPct: default 0 digits", () => {
  eq(fmtPct(82), "82%");
  eq(fmtPct(65.44, 1), "65.4%");
});

test("fmtRA: hours -> HHh MMm, wraps negative hours into [0,24)", () => {
  eq(fmtRA(13.5), "13h 30m");
  eq(fmtRA(-1), "23h 00m");
});

test("fmtDec: sign, zero-padded degrees and minutes", () => {
  eq(fmtDec(62.6167), "+62° 37′");
  eq(fmtDec(-5.5), "−05° 30′");
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export { passed, failed };
export const total = passed + failed;
