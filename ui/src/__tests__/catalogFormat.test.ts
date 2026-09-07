// catalogFormat.test.ts — fmtMag/fmtAlt/altTone (lib/catalogFormat.ts).
//
// These three exist because CatalogEntry.mag is `number | null` (no published
// magnitude for NGC 604, IC 1604) and .alt/.az are absent entirely for a
// caller without view.site_derived — and a bare `.toFixed()` on either used to
// crash the whole Mount view (see mountViewCatalogNulls.test.tsx). The cases
// below are exactly the three shapes a server payload can hand these helpers:
// a real number, an explicit null, and a field missing from the object.
//
// Run directly:  npx tsx src/__tests__/catalogFormat.test.ts
import { altTone, fmtAlt, fmtMag } from "../lib/catalogFormat";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg: string): void {
  if (a !== b) throw new Error(`${msg} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
}

// -------------------------------------------------------------------- fmtMag
test("fmtMag formats a real number to one decimal", () => {
  eq(fmtMag(4), "4.0", "whole number");
  eq(fmtMag(12.34), "12.3", "rounds to one decimal");
  eq(fmtMag(-1.5), "-1.5", "negative magnitude (bright objects go negative)");
});
test("fmtMag placeholders an explicit null", () => {
  eq(fmtMag(null), "—", "null");
});
test("fmtMag placeholders undefined the same way", () => {
  eq(fmtMag(undefined), "—", "undefined");
});

// -------------------------------------------------------------------- fmtAlt
test("fmtAlt formats a real number with a degree sign, no decimal", () => {
  eq(fmtAlt(54.6), "55°", "rounds to the nearest degree");
  eq(fmtAlt(0), "0°", "zero is a real altitude, not an absence");
});
test("fmtAlt placeholders a missing value (no view.site_derived)", () => {
  eq(fmtAlt(undefined), "—", "undefined");
  eq(fmtAlt(null), "—", "null, in case a caller ever sends one");
});

// ------------------------------------------------------------------- altTone
test("altTone warns below 20 degrees", () => {
  eq(altTone(19.9), "text-warn", "just under the floor");
  eq(altTone(0), "text-warn", "on the horizon");
});
test("altTone reads good above 40 degrees", () => {
  eq(altTone(40.1), "text-good", "just over the ceiling");
  eq(altTone(89), "text-good", "near the zenith");
});
test("altTone is the caller's default in the 20-40 middle ground", () => {
  eq(altTone(30), "", "MountView's default: unstyled");
  eq(altTone(30, "text-dim"), "text-dim", "CatalogSearch's default: dimmed");
});
test("altTone treats an absent altitude as unknown, not as 'good' or 'warn'", () => {
  eq(altTone(undefined), "text-dim", "undefined");
  eq(altTone(null), "text-dim", "null");
  // The `normal` override is for the 20-40 range only; an unknown value must
  // never inherit it, or a caller passing "" would make an absent alt read
  // identically to a healthy one.
  eq(altTone(undefined, "text-dim"), "text-dim", "unknown ignores the normal override");
});

// ------------------------------------------------------------------ report
console.log(`\ncatalogFormat: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log(f);
if (failed > 0) throw new Error(`${failed} test(s) failed`);
