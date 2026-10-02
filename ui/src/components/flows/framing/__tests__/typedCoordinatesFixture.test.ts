// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// typedCoordinatesFixture.test.ts - the modal's reading of a TARGET's typed
// coordinates, graded against the server's own cases (#387; spec 2026-09-23
// flows mosaic, 3.1 and 3.3; S7 orchestrator ruling 6).
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
// this mirror trimmed and previewed it as placed by its name. S5 made the
// server read whitespace as blank too.
//
// RULING 6 (S7) closed the two residuals #387 recorded. A value that is not
// text: `String(v).trim()` made the number 0 the text "0" (typed, as ruling
// 6 now says on both sides, where the server read it as blank), and made
// `true` the text "true" and NaN the text "NaN", typed here as on the server
// then, and blank on both sides under ruling 6. And the characters JavaScript's trim() and
// Python's str.strip() disagree on: trim() strips a BOM that Python keeps,
// and keeps NEL (U+0085) and U+001C to U+001F, which Python strips. The
// mirror now reads text through `pyStrip`, a finite number as its text, and
// anything else as blank, and the fixture holds every one of those values.
//
// What is graded: `typedCoordinates` for every case, and `draftCentre` for
// every case, so padding round a typed field cannot move the preview off the
// place the run slews to, and a typed field that does not parse draws no
// centre. The draft is made by `draftFromParams` from the case as a node's
// params, which is how the sheet opens on a stored block.
//
// Every mutant below was run in a private scratch copy of ui/ (the session
// scratchpad's S7-COMPILE-mut; s5-compile-mut for the S5 ones), never in the
// shared tree (#254), and the failure it produced is quoted verbatim,
// wrapped at spaces, with each non-ASCII character written <U+XXXX> so this
// file stays ASCII.

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
/** JSON for a message, with NaN and the infinities spelled, which
 *  JSON.stringify would write as null, and every character past ASCII
 *  escaped, so a lone BOM or NEL shows as what it is. */
const js = (v: unknown) => (typeof v === "number" && !Number.isFinite(v) ? String(v) : JSON.stringify(v))
  .replace(/[\u007f-\uffff]/g, (ch) => `\\u${ch.charCodeAt(0).toString(16).padStart(4, "0")}`);

// ------------------------------------------------------------- the fixture
const FIXTURE_REL = "../../../../../../server/tests/fixtures/typed_coordinates_cases.json";

/** A field as the fixture writes it: text, a number, a bool, null, a list or
 *  an object, or the marker {"number": "NaN"} for what JSON cannot carry. */
type Written = string | number | boolean | null | unknown[] | Record<string, unknown>;

interface CoordCase {
  id: string;
  kind: string;
  ra: Written;
  dec: Written;
  typed: boolean;
  ra_hours?: number;
  dec_deg?: number;
}

/** The shapes the fixture must hold, one kind each: #387's four and ruling
 *  6's three. */
const KINDS = [
  "whitespace only", "blank", "typed", "typed with padding",
  "a number", "neither text nor a finite number", "typed, does not parse",
];

/** A field as a node holds it: the marker {"number": <text>} decoded into
 *  that number, as the server's grader decodes it; anything else itself. */
function decoded(v: Written): unknown {
  if (v !== null && typeof v === "object" && !Array.isArray(v)) {
    const keys = Object.keys(v);
    if (keys.length === 1 && keys[0] === "number") return Number((v as { number: string }).number);
  }
  return v;
}

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
  return { name: "M31", ra: decoded(c.ra), dec: decoded(c.dec), rows: 2, cols: 3, fovX: 2, fovY: 1.33 } as Params;
}

function byId(fragment: string): CoordCase {
  const hits = readCases().filter((c) => c.id.includes(fragment));
  if (hits.length !== 1) throw new Error(`${hits.length} cases are ${js(fragment)}`);
  return hits[0];
}

// THE PREMISE, checked before the verdicts: a fixture that lost a kind would
// leave them green for no reason, and so would one whose ruling-6 values no
// longer decode to what they stand for (1e999 rewritten as a finite number by
// a formatter, or the NaN marker dropped).
// MUTANT "no blank case" (a scratch copy of the fixture with the four blank
// cases removed). Observed (S5):
//   x the fixture holds every shape #387 names: the fixture no longer holds: ["blank"]
// MUTANT "no number case" (a scratch copy of the fixture with the four
// `a number` cases removed). Observed:
//   x the fixture holds every shape #387 and ruling 6 name: the fixture no longer
//     holds: ["a number"]
test("the fixture holds every shape #387 and ruling 6 name", () => {
  const cases = readCases();
  const held = new Set(cases.map((c) => c.kind));
  const missing = KINDS.filter((k) => !held.has(k));
  assert(missing.length === 0, `the fixture no longer holds: ${js(missing)}`);
  for (const c of cases) {
    const placed = c.ra_hours !== undefined && c.dec_deg !== undefined;
    assert(placed === (c.typed && c.kind !== "typed, does not parse"), `${c.id}: placement and kind disagree`);
  }
  assert(decoded(byId("RA the number 0:").ra) === 0, "RA 0 decodes to 0");
  assert(decoded(byId("RA true").ra) === true, "RA true decodes to true");
  assert(Number.isNaN(decoded(byId("RA NaN").ra) as number), "the NaN marker decodes to NaN");
  assert(decoded(byId("RA Infinity").ra) === Infinity, "1e999 reads as Infinity");
  assert(decoded(byId("minus Infinity").dec) === -Infinity, "-1e999 reads as -Infinity");
  assert(decoded(byId("400-digit").ra) === Infinity, "a 400-digit integer reads as Infinity here");
  assert(byId("lone BOM").ra === String.fromCharCode(0xfeff), "the lone BOM case");
  assert(byId("lone NEL").ra === String.fromCharCode(0x85), "the lone NEL case");
  assert(byId("lone file separator").ra === String.fromCharCode(0x1c), "the lone U+001C case");
});

// MUTANT "mirror reads 0 as blank" (textOf reading a number only when it is
// finite and not 0, the server's reading before ruling 6). Observed:
//   x the mirror reads every server case as typed or not, as the server does: RA the
//     number 0: 0h is a real RA (ruling 6) (0, "+41 16 09"): expected true, got false;
//     RA the number 0.0 (0, "+41 16 09"): expected true, got false; Dec the number 0:
//     the equator ("00h 42m 44s", 0): expected true, got false
//   and the centre test below on the same three ("expected [0,41.269167], got null").
// MUTANT "bool read as typed" (textOf reading a bool as its String, "true"
// and "false", as `String(v).trim()` did before ruling 6). Observed:
//   x the mirror reads every server case as typed or not, as the server does: RA true
//     (true, "+41 16 09"): expected false, got true; RA false (false, "+41 16 09"):
//     expected false, got true
// MUTANT "trim, not pyStrip" (textOf trimming text with JavaScript's trim(),
// as it did before ruling 6). Observed:
//   x the mirror reads every server case as typed or not, as the server does: RA a
//     lone NEL (Python strips it, JS trim keeps it) ("\u0085", "+41 16 09"): expected
//     false, got true; RA a lone file separator U+001C (Python strips it, JS trim keeps
//     it) ("\u001c", "+41 16 09"): expected false, got true; RA a lone BOM (Python
//     keeps it, JS trim strips it) ("\ufeff", "+41 16 09"): expected true, got false
// MUTANT "raw truthiness in the mirror" (typedCoordinates testing the raw
// field, `String(draft.ra ?? "") !== "" && String(draft.dec ?? "") !== ""`,
// the server's reading before #387, mirrored). Observed:
//   the nine whitespace-only cases, the lone NEL and U+001C and every value that is
//   neither text nor a finite number, the modal's own case first (cut after three):
//   x the mirror reads every server case as typed or not, as the server does: the
//     modal's case from #387: RA of three spaces ("   ", "+41 16 09"): expected
//     false, got true; Dec of three spaces ("00h 42m 44s", "   "): expected false, got
//     true; both only spaces (" ", "  "): expected false, got true; ...
test("the mirror reads every server case as typed or not, as the server does", () => {
  const bad: string[] = [];
  for (const c of readCases()) {
    const got = typedCoordinates(draftFromParams(paramsOf(c)));
    if (got !== c.typed) bad.push(`${c.id} (${js(decoded(c.ra))}, ${js(decoded(c.dec))}): expected ${c.typed}, got ${got}`);
  }
  assert(bad.length === 0, bad.join("; "));
});

// A typed case is placed where the server places it, padding or not; a case
// that is not typed has no centre here, because the modal places it by name;
// and a typed case that does not parse has none either, because the run drops
// it.
// MUTANT "RA read unstripped" (parseRaHours: `pyStrip(fold(String(text)))`
// made `fold(String(text))`). Observed:
//   the four padded cases and nothing else:
//   x a typed case is centred where the server places it, and every other case not
//     at all: typed, spaces round both: expected [0.712222,41.269167], got null; typed,
//     a tab before and a newline after: expected [0.712222,41.269167], got null; typed,
//     a no-break space before and an em space after: expected [0.712222,41.269167], got
//     null; typed, a unit separator (U+001F) round the RA: expected
//     [0.712222,41.269167], got null
test("a typed case is centred where the server places it, and every other case not at all", () => {
  const bad: string[] = [];
  for (const c of readCases()) {
    const centre = draftCentre(draftFromParams(paramsOf(c)));
    if (c.ra_hours === undefined || c.dec_deg === undefined) {
      if (centre !== null) bad.push(`${c.id}: expected no centre, got ${js(centre)}`);
      continue;
    }
    if (centre === null
        || Math.abs(centre.ra_hours - c.ra_hours) > 1e-6
        || Math.abs(centre.dec_deg - c.dec_deg) > 1e-6) {
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
