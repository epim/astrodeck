// typedCoordinatesFixture.test.ts - the modal's reading of a TARGET's typed
// coordinates, graded against the server's own cases (#387; spec 2026-09-23
// flows mosaic, 3.1 and 3.3).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/typedCoordinatesFixture.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE CASES ARE server/tests/fixtures/typed_coordinates_cases.json, READ, NOT
// COPIED. test_flows_typed_coordinates.py grades the server's
// identity.typed_coordinates and to_plan._coords against the same file, so
// the modal's "placed by its name" and the run's placement are held to one
// table. #387 was the two readings apart: the server tested raw truthiness,
// so an RA of three spaces was typed and the run dropped the block, while
// this mirror trimmed and previewed it as placed by its name. The server now
// reads whitespace as blank too, so the fixture's answer is the one this
// mirror already gave, and this file is what keeps it given.
//
// What is graded: `typedCoordinates` for every case, and `draftCentre` for
// the typed ones, so padding round a typed field cannot move the preview off
// the place the run slews to. The draft is made by `draftFromParams` from the
// case as a node's params, which is how the sheet opens on a stored block.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// s5-compile-mut), never in the shared tree (#254), and the failure it
// produced is quoted verbatim, wrapped at spaces, with each non-ASCII
// character written <U+XXXX> so this file stays ASCII.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";

import { draftCentre, draftFromParams, typedCoordinates } from "../framingModel";
import type { Params } from "../framingModel";

// ------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const js = (v: unknown) => JSON.stringify(v);

// ------------------------------------------------------------- the fixture
const FIXTURE_REL = "../../../../../../server/tests/fixtures/typed_coordinates_cases.json";

interface CoordCase {
  id: string;
  kind: string;
  ra: string | null;
  dec: string | null;
  typed: boolean;
  ra_hours?: number;
  dec_deg?: number;
}

/** The shapes #387 asks the fixture to hold, one kind each. */
const KINDS = ["whitespace only", "blank", "typed", "typed with padding"];

/** The server's cases. A missing or unreadable file must FAIL, never skip:
 *  a skipped fixture reads as a green mirror. */
function readCases(): CoordCase[] {
  let text: string;
  try {
    text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE_REL}, the cases this mirror is graded against: ${(e as Error).message}`);
  }
  const cases = (JSON.parse(text) as { cases?: CoordCase[] }).cases;
  if (!Array.isArray(cases) || cases.length < 10) throw new Error(`${FIXTURE_REL} holds too few cases`);
  return cases;
}

/** The case as a stored TARGET's params: M31's name, the case's fields. */
function paramsOf(c: CoordCase): Params {
  return { name: "M31", ra: c.ra, dec: c.dec, rows: 2, cols: 3, fovX: 2, fovY: 1.33 } as Params;
}

// THE PREMISE, checked before the verdicts: a fixture that lost a kind would
// leave them green for no reason.
// MUTANT "no blank case" (a scratch copy of the fixture with the four blank
// cases removed). Observed:
//   x the fixture holds every shape #387 names: the fixture no longer holds: ["blank"]
// MUTANT "no whitespace-only case" (the nine whitespace-only cases removed,
// eight left) trips the size floor first, in every test of this file:
//   x the fixture holds every shape #387 names:
//     ../../../../../../server/tests/fixtures/typed_coordinates_cases.json holds too few cases
test("the fixture holds every shape #387 names", () => {
  const held = new Set(readCases().map((c) => c.kind));
  const missing = KINDS.filter((k) => !held.has(k));
  assert(missing.length === 0, `the fixture no longer holds: ${js(missing)}`);
});

// MUTANT "raw truthiness in the mirror" (typedCoordinates testing the raw
// field, `String(draft.ra ?? "") !== "" && String(draft.dec ?? "") !== ""`,
// the server's reading before #387, mirrored: the UI side of the server's
// mutant "raw truthiness on the server"). Observed, the nine whitespace-only
// cases and nothing else, the modal's own case first (cut after three):
//   x the mirror reads every server case as typed or not, as the server does: the modal's case
//     from #387: RA of three spaces ("   ", "+41 16 09"): expected false, got true; Dec of three
//     spaces ("00h 42m 44s", "   "): expected false, got true; both only spaces (" ", "  "):
//     expected false, got true; ...
test("the mirror reads every server case as typed or not, as the server does", () => {
  const bad: string[] = [];
  for (const c of readCases()) {
    const got = typedCoordinates(draftFromParams(paramsOf(c)));
    if (got !== c.typed) bad.push(`${c.id} (${js(c.ra)}, ${js(c.dec)}): expected ${c.typed}, got ${got}`);
  }
  assert(bad.length === 0, bad.join("; "));
});

// A typed case is placed where the server places it, padding or not; a case
// that is not typed has no centre here, because the modal places it by name.
// (Green under "raw truthiness in the mirror": a whitespace field reads as
// typed there but does not parse, so it has no centre either way. The test
// above is the one that mutant turns red.)
// MUTANT "RA read unstripped" (parseRaHours: `pyStrip(fold(String(text)))`
// made `fold(String(text))`). Observed, the three padded cases and nothing
// else:
//   x a typed case is centred where the server places it, and an untyped one not at all: typed,
//     spaces round both: expected [0.712222,41.269167], got null; typed, a tab before and a
//     newline after: expected [0.712222,41.269167], got null; typed, a no-break space before and
//     an em space after: expected [0.712222,41.269167], got null
test("a typed case is centred where the server places it, and an untyped one not at all", () => {
  const bad: string[] = [];
  for (const c of readCases()) {
    const centre = draftCentre(draftFromParams(paramsOf(c)));
    if (!c.typed) {
      if (centre !== null) bad.push(`${c.id}: expected no centre, got ${js(centre)}`);
      continue;
    }
    if (centre === null
        || Math.abs(centre.ra_hours - (c.ra_hours as number)) > 1e-6
        || Math.abs(centre.dec_deg - (c.dec_deg as number)) > 1e-6) {
      bad.push(`${c.id}: expected ${js([c.ra_hours, c.dec_deg])}, got ${js(centre)}`);
    }
  }
  assert(bad.length === 0, bad.join("; "));
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`typedCoordinatesFixture.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
