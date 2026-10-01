// w5NamingPreviewSample.test.ts - the #/next naming editor's preview must not
// drop GAIN/EXPOSURE/BINNING/SENSORTEMP (#278 remainder, W5 integration).
//
// Run directly:  npx tsx src/next/hubs/settings/tuning/files/__tests__/w5NamingPreviewSample.test.ts
//
// WP-42 (#278) taught `lib/naming.ts` about the four capture-settings tokens
// and gave it one shared `PREVIEW_SAMPLE` for exactly the reason naming.ts's
// own header spells out: two hand-copied samples drift apart. But
// `filesModel.ts` (the #/next FILES-AND-STANDARDS "naming" editor's pure
// half) carried a SECOND sample of its own, `NAMING_SAMPLE`, copied from the
// legacy `NamingPanel.tsx` before WP-42 existed - so `namingPreview()`, the
// function the #/next editor actually calls to build its preview line, kept
// rendering GAIN/EXPOSURE/BINNING/SENSORTEMP as empty right up until W5
// integration, even though `lib/naming.ts` had the values all along.
//
// This drives the issue's own golden vector through `namingPreview()`
// itself, not through `lib/naming.ts`'s `renderTemplatePreview` directly
// (that half is already covered by w5NamingCaptureTokens.test.ts) - so a
// future `filesModel.ts` that reintroduces a private sample fails HERE, at
// the one function the editor renders from.
import { namingPreview } from "../filesModel";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

// #278's measured repro, through the actual editor function (note the
// leading "captures/" namingPreview adds, which renderTemplatePreview alone
// does not).
test("namingPreview renders GAIN+EXPOSURE, the server's measured output (#278)", () => {
  eq(namingPreview("$$TARGET$$/g$$GAIN$$_$$EXPOSURE$$s/$$FRAMENR$$"),
     "captures/M42/g100_300s/0001.fits");
});

test("namingPreview renders BINNING+SENSORTEMP", () => {
  eq(namingPreview("$$TARGET$$_bin$$BINNING$$_$$SENSORTEMP$$/$$FRAMENR$$"),
     "captures/M42_bin1_-10C/0001.fits");
});

// The default template itself contains none of the four tokens, so this
// guards that the ordinary preview line did not change shape for everyone
// else while the fix landed.
test("namingPreview on the default template is unaffected", () => {
  eq(namingPreview(""),
     "captures/M42/Light_M42_Ha_2026-07-23_213045_0001.fits");
});

console.log(`\nw5NamingPreviewSample.test: ${passed}/${passed + failed} passed`);
if (failures.length) console.error(failures.join("\n"));

export const result = { passed, failed, total: passed + failed };
