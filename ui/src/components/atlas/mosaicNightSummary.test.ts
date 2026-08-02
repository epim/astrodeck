// Tests for components/atlas/mosaicNightSummary.ts — the reduction that decides what
// the Atlas says about a mosaic's panels, and what it says about the ones it
// could not answer for.
//
// The defect under test: "a mosaic answering for some panels and saying nothing
// at all about the others". Every case below is a claim that the silence is
// gone — a missing altitude always produces words, and the words name a cause.
//
// Same inline-assert harness as lib/__tests__/framing.test.ts (no vitest in this
// UI). Run it directly:  npx tsx src/components/atlas/mosaicNightSummary.test.ts

import {
  summarisePanelNight,
  peakSpreadText,
  belowLimitText,
  missingAltitudeReason,
  missingAltitudeLabel,
  type PanelNight,
} from "./mosaicNightSummary";

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
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

// --------------------------------------------------------------- fixtures
/** A panel that got an answer. */
const at = (row: number, col: number, alt: number): PanelNight =>
  ({ row, col, transit_alt: alt });
/** A panel the server tried and failed on — it always carries a cause. */
const lost = (row: number, col: number, why: string): PanelNight =>
  ({ row, col, transit_alt_error: why });

const OSERR = "OSError: ephemeris table unreadable";
const NOVIS = "visibility unavailable (ImportError: no module named astropy)";

// =============================================================== tests

test("every panel answered: the spread is the min and max, not the centre", () => {
  const s = summarisePanelNight(
    [at(0, 0, 58.2), at(0, 1, 64.0), at(1, 0, 66.4)], 30);
  eq(s.total, 3, "total");
  eq(s.answered, 3, "answered");
  eq(s.missing, 0, "missing");
  eq(s.peak?.min, 58.2, "min");
  eq(s.peak?.max, 66.4, "max");
  eq(peakSpreadText(s), "3 panels peak 58°–66° tonight");
  eq(missingAltitudeReason(s), null, "nothing missing => no reason line");
});

test("a sub-degree spread prints one value, not a fake range", () => {
  // fmtAlt rounds to whole degrees; "64°–64°" would claim a measured range that
  // the rounding has already thrown away.
  const s = summarisePanelNight([at(0, 0, 63.6), at(0, 1, 64.2)], 30);
  eq(peakSpreadText(s), "All 2 panels peak 64° tonight");
});

test("a lost panel is counted AND its server-stated cause is carried", () => {
  const s = summarisePanelNight(
    [at(0, 0, 61), at(0, 1, 62), lost(1, 0, OSERR), lost(1, 1, OSERR)], 30);
  eq(s.answered, 2, "answered");
  eq(s.missing, 2, "missing");
  eq(s.reasons.length, 1, "one distinct cause, not one per panel");
  eq(s.reasons[0], OSERR);
  eq(missingAltitudeLabel(s), "2 of 4 panels: no peak altitude");
  eq(missingAltitudeReason(s), `2 of 4 panels have no peak altitude — ${OSERR}`);
  // the answered half still reports, so a partial loss is not a total blackout
  eq(peakSpreadText(s), "2 of 4 panels peak 61°–62° tonight");
});

test("a panel with no altitude AND no reason still says something", () => {
  // The original bug one layer up: an absent key used to mean an absent row.
  // We do not know the cause here, and saying exactly that is the honest read.
  const s = summarisePanelNight([at(0, 0, 61), { row: 0, col: 1 }], 30);
  eq(s.missing, 1, "missing");
  eq(s.reasons[0], "no reason was given");
  assert(
    (missingAltitudeReason(s) ?? "").includes("no reason was given"),
    "an unexplained panel must still produce words",
  );
});

test("an empty transit_alt_error is treated as no reason, not as a blank one", () => {
  // A bare OSError stringifies to nothing; if that ever reaches the wire as ""
  // the chip must not render "panels have no peak altitude — ".
  const s = summarisePanelNight([lost(0, 0, "   ")], 30);
  eq(s.reasons[0], "no reason was given");
  eq(missingAltitudeReason(s),
     "No panel of this mosaic has a peak altitude — no reason was given");
});

test("nothing answered at all: the label and reason speak for the whole mosaic", () => {
  const s = summarisePanelNight([lost(0, 0, NOVIS), lost(0, 1, NOVIS)], 30);
  eq(s.answered, 0, "answered");
  eq(s.peak, null, "no peak without an answer");
  eq(peakSpreadText(s), null, "no spread sentence to print");
  eq(missingAltitudeLabel(s), "No peak altitude for any panel");
  eq(missingAltitudeReason(s),
     `No panel of this mosaic has a peak altitude — ${NOVIS}`);
});

test("distinct causes are all reported, with a count past the second", () => {
  const s = summarisePanelNight([
    lost(0, 0, "a"), lost(0, 1, "b"), lost(1, 0, "c"), lost(1, 1, "d"),
    lost(2, 0, "a"),
  ], 30);
  eq(s.reasons.join("|"), "a|b|c|d", "first-seen order, de-duplicated");
  eq(missingAltitudeReason(s),
     "No panel of this mosaic has a peak altitude — a; b; and 2 more");
});

test("a non-finite altitude is not an answer", () => {
  // JSON cannot carry NaN and the server guards it, but a number-shaped
  // non-measurement must never be averaged into a range if one ever arrives.
  const s = summarisePanelNight(
    [at(0, 0, 61), { row: 0, col: 1, transit_alt: Number.NaN }], 30);
  eq(s.answered, 1, "answered");
  eq(s.missing, 1, "missing");
  eq(s.peak?.max, 61, "NaN never widened the range");
});

test("below-limit counts panels whose PEAK never reaches the limit", () => {
  // transit_alt is the peak for the night, so alt < limit means the panel is
  // under the horizon limit for the ENTIRE night — not merely low right now.
  const s = summarisePanelNight(
    [at(0, 0, 18), at(0, 1, 24), at(1, 0, 41), at(1, 1, 55)], 30);
  eq(s.belowLimit, 2, "belowLimit");
  eq(belowLimitText(s, 30),
     "2 of 4 panels never clear 30° tonight — their whole night is below your horizon limit.");
});

test("no below-limit line when every answered panel clears the limit", () => {
  const s = summarisePanelNight([at(0, 0, 41), at(0, 1, 55)], 30);
  eq(s.belowLimit, 0, "belowLimit");
  eq(belowLimitText(s, 30), null, "silence is right when there is nothing to warn about");
});

test("a wholly below-limit mosaic says so about itself, not about a subset", () => {
  const s = summarisePanelNight([at(0, 0, 11), at(0, 1, 14)], 30);
  eq(belowLimitText(s, 30),
     "No panel of this mosaic clears 30° tonight — their whole night is below your horizon limit.");
});

test("a partly-lost mosaic never claims 'no panel clears' from the answered half", () => {
  // Both answered panels are below the limit, but two more are unknown — the
  // absolute phrasing would be a claim about panels we have no measurement for.
  const s = summarisePanelNight(
    [at(0, 0, 11), at(0, 1, 14), lost(1, 0, OSERR), lost(1, 1, OSERR)], 30);
  eq(belowLimitText(s, 30),
     "2 of 4 panels never clear 30° tonight — their whole night is below your horizon limit.");
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nmosaicNightSummary.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
