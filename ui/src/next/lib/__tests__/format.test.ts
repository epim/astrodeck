// Pure-lib test for format.ts. Sabotage check: reusing eta.ts's fmtDuration
// verbatim (no space before a lone "s") turns the "45 s" worked example red;
// dropping the RA wrap-to-[0,24) turns the negative-hours fmtRA test red;
// forgetting the unicode minus in fmtDec turns the negative-declination test red;
// rounding the minutes separately from the hours/degrees (`Math.round((h - hh)
// * 60) % 60`, which is what shipped) turns both carry tests red - 22.9999 h
// prints "22h 00m" and 62.996 degrees prints "+62° 00′"; applying the 24 h wrap
// BEFORE the rounding instead of after turns "23h 59.7m carries round to 00h"
// red.
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

test("fmtRA: 60 minutes CARRIES into the hour, it does not fold back to :00", () => {
  // The review's own example. Rounding the minutes on their own gave "22h 00m"
  // here - the same string 22.0 prints, an hour from the truth, on the readout
  // an operator uses to check the mount is where the plan says.
  eq(fmtRA(22.9999), "23h 00m");
  eq(fmtRA(2.9999), "03h 00m");
  eq(fmtRA(0.9999), "01h 00m");
});

test("fmtRA: 23h 59.7m carries round to 00h, not to 24h", () => {
  eq(fmtRA(23.9999), "00h 00m", "the wrap has to happen after the carry:");
  eq(fmtRA(23.99), "23h 59m", "and half a minute short of it does not wrap:");
});

test("fmtDec: sign, zero-padded degrees and minutes", () => {
  eq(fmtDec(62.6167), "+62° 37′");
  eq(fmtDec(-5.5), "−05° 30′");
});

test("fmtDec: 60 minutes CARRIES into the degree, on both signs", () => {
  eq(fmtDec(62.996), "+63° 00′");
  eq(fmtDec(-62.996), "−63° 00′");
  eq(fmtDec(-0.999), "−01° 00′", "a target just south of the equator:");
});

test("fmtDec: the pole carries to +90, which is a place and not an overflow", () => {
  // RA wraps at 24 h because 24 h IS 0 h; declination must not, because
  // folding 90 back to 0 would put Polaris on the celestial equator.
  eq(fmtDec(89.9999), "+90° 00′");
  eq(fmtDec(-89.9999), "−90° 00′");
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export { passed, failed };
export const total = passed + failed;
