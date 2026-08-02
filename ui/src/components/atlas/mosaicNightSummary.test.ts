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
  panelSetText,
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
  eq(missingAltitudeReason(s),
     `2 of 4 panels have no peak altitude (row 2) — ${OSERR}`);
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
     "2 of 4 panels never clear 30° tonight — row 1. "
     + "Their whole night is below your horizon limit.");
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
     "2 of 4 panels never clear 30° tonight — row 1. "
     + "Their whole night is below your horizon limit.");
});

// ------------------------------------------------- which panels, not how many

test("a below-limit warning names the panels, not just the count", () => {
  // The count alone named a harm the user could not act on. Row 3 of a 3x3 is a
  // grid edge they can pull in with the row stepper on this very screen.
  const s = summarisePanelNight([
    at(0, 0, 61), at(0, 1, 62), at(0, 2, 60),
    at(1, 0, 45), at(1, 1, 47), at(1, 2, 44),
    at(2, 0, 18), at(2, 1, 19), at(2, 2, 17),
  ], 30);
  eq(s.belowLimit, 3, "belowLimit");
  eq(s.grid.rows, 3, "grid rows read off the payload");
  eq(s.grid.cols, 3, "grid cols read off the payload");
  eq(belowLimitText(s, 30),
     "3 of 9 panels never clear 30° tonight — row 3. "
     + "Their whole night is below your horizon limit.");
});

test("a scattered loss is listed panel by panel, in the Plan's own row-col name", () => {
  // AtlasView names each Plan target "<target> row+1-col+1", so "1-3" here is
  // the row the user will find in the Plan if they send anyway.
  const s = summarisePanelNight(
    [at(0, 0, 61), at(0, 1, 55), at(0, 2, 12), at(1, 0, 40), at(1, 1, 9), at(1, 2, 44)],
    30);
  eq(belowLimitText(s, 30),
     "2 of 6 panels never clear 30° tonight — panels 1-3, 2-2. "
     + "Their whole night is below your horizon limit.");
});

test("panelSetText collapses whole rows and lists the remainder", () => {
  const grid = { rows: 4, cols: 3 };
  eq(panelSetText([{ row: 2, col: 0 }, { row: 2, col: 1 }, { row: 2, col: 2 }], grid),
     "row 3");
  eq(panelSetText([
    { row: 2, col: 0 }, { row: 2, col: 1 }, { row: 2, col: 2 },
    { row: 3, col: 0 }, { row: 3, col: 1 }, { row: 3, col: 2 },
  ], grid), "rows 3 and 4");
  eq(panelSetText([
    { row: 2, col: 0 }, { row: 2, col: 1 }, { row: 2, col: 2 }, { row: 1, col: 1 },
  ], grid), "row 3 and panel 2-2");
  eq(panelSetText([], grid), null, "an empty set names nothing");
});

test("a full column is named as a column, and never alongside a full row", () => {
  // A full row and a full column always intersect; reporting both would name the
  // shared panel twice and inflate what the user thinks they are losing.
  const grid = { rows: 3, cols: 3 };
  eq(panelSetText(
    [{ row: 0, col: 0 }, { row: 1, col: 0 }, { row: 2, col: 0 }], grid), "column 1");
  const cross = panelSetText([
    { row: 1, col: 0 }, { row: 1, col: 1 }, { row: 1, col: 2 },  // row 2, full
    { row: 0, col: 1 }, { row: 2, col: 1 },                      // + rest of col 2
  ], grid);
  eq(cross, "row 2 and panels 1-2, 3-2");
});

test("a single-row grid never collapses to 'column N', which would name it all", () => {
  // "column 2" of a 1x3 IS one panel; saying it as a column implies a band that
  // does not exist. Same for "row N" when there is one column.
  eq(panelSetText([{ row: 0, col: 1 }], { rows: 1, cols: 3 }), "panel 1-2");
  eq(panelSetText([{ row: 1, col: 0 }], { rows: 3, cols: 1 }), "panel 2-1");
});

test("a long roll-call is capped and says how many it stopped naming", () => {
  const refs = Array.from({ length: 9 }, (_, i) => ({ row: 0, col: i }));
  // cols is 10, so row 0 is NOT full — these list individually.
  eq(panelSetText(refs, { rows: 4, cols: 10 }),
     "panels 1-1, 1-2, 1-3, 1-4, 1-5, 1-6 and 3 more");
});

test("a partial loss of altitude says which panels are unmeasured", () => {
  // Without coordinates a partial loss makes every panel suspect when only some
  // are — and the answered half of the mosaic is still trustworthy.
  const s = summarisePanelNight(
    [at(0, 0, 61), at(0, 1, 62), at(1, 0, 59), lost(1, 1, OSERR)], 30);
  eq(missingAltitudeReason(s),
     `1 of 4 panels have no peak altitude (panel 2-2) — ${OSERR}`);
});

test("a total loss names no panels, because the answer is all of them", () => {
  const s = summarisePanelNight([lost(0, 0, NOVIS), lost(0, 1, NOVIS)], 30);
  eq(missingAltitudeReason(s),
     `No panel of this mosaic has a peak altitude — ${NOVIS}`);
  const all = summarisePanelNight([at(0, 0, 11), at(0, 1, 14)], 30);
  eq(belowLimitText(all, 30),
     "No panel of this mosaic clears 30° tonight — "
     + "their whole night is below your horizon limit.");
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
