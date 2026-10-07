// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Tests that components/atlas/mosaicNightSummary.ts reads a panel's reason from
// the TYPE the route answer is declared with (#174), not from a copy of it.
//
//   Run directly:  npx tsx src/components/atlas/__tests__/mosaicNightSummary.test.ts
//   Also run by `npm test` (run-tests.mjs), which runs `tsc -b` first; half of
//   what this file checks is checked there.
//
// THE CHAIN THIS HOLDS ONE LINK OF. `server/tests/test_types_mirror_status.py`
// holds `MosaicPanel` to what `POST /api/framing/mosaic` really sends, both
// ways round. This file holds `mosaicNightSummary`'s `PanelNight` to
// `MosaicPanel`, and feeds the summary a route answer AS a `MosaicPanel[]`,
// with no cast. Until #174 the summary declared `transit_alt_error` itself,
// because `MosaicPanel` lacked it: a second truth that would have kept
// compiling while the wire and the type moved on without it.
//
// The summary's own rules (the spread, the counts, the sentences) are
// mosaicNightSummary.test.ts's, beside the module; this file is about the type.
//
// Every mutation named below was run in a private scratch copy of ui/ (never the
// shared tree), from a byte-for-byte backup of the mutated file, and each quoted
// failure is the one observed there.
//
// Same inline-assert harness as mosaicNightSummary.test.ts: printed tally plus
// the `{ passed, failed, total }` export.

import {
  missingAltitudeReason,
  summarisePanelNight,
  type PanelNight,
} from "../mosaicNightSummary";
import type { MosaicPanel, MosaicResult } from "../../../types";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

// ------------------------------------------------------------ the type link
/** True exactly when A and B are the same type, optional modifiers included
 *  (the invariant-position trick; a plain `A extends B` would let a required
 *  field pass for an optional one). */
type Same<A, B> =
  (<T>() => T extends A ? 1 : 2) extends (<T>() => T extends B ? 1 : 2) ? true : false;

type Read = "row" | "col" | "transit_alt" | "transit_alt_error";

/** `PanelNight` is `MosaicPanel`'s four fields, as `MosaicPanel` declares them.
 *  (The type check is `tsc -p tsconfig.json`, the project `tsc -b` builds.)
 *
 *  RED under the named mutation "drop transit_alt_error from types.ts" (its
 *  line deleted from `MosaicPanel` in a scratch copy of ui/src/types.ts): 11
 *  errors across the owned files and the summary's two callers; the two on
 *  the Pick itself, observed:
 *
 *    src/components/atlas/mosaicNightSummary.ts(39,44): error TS2344: Type
 *    '"row" | "col" | "transit_alt" | "transit_alt_error"' does not satisfy
 *    the constraint 'keyof MosaicPanel'.
 *    src/components/atlas/__tests__/mosaicNightSummary.test.ts(76,62): error
 *    TS2344: Type 'Read' does not satisfy the constraint 'keyof MosaicPanel'.
 *
 *  RED under "PanelNight's reason made required" (`& { transit_alt_error:
 *  string }` added to the `Pick` in a scratch copy of mosaicNightSummary.ts),
 *  observed first of 8:
 *
 *    src/components/atlas/__tests__/mosaicNightSummary.test.ts(76,7): error
 *    TS2322: Type 'true' is not assignable to type 'false'. */
const PICKED: Same<Pick<PanelNight, Read>, Pick<MosaicPanel, Read>> = true;

/** A route answer's panels, as the route sends them for a night asked about
 *  (`transit_alt: true`): one panel answered, one whose altitude could not be
 *  found, carrying the server's reason (`framing._stamp_transit_alt`). Typed
 *  as the answer type, so a `MosaicPanel` without `transit_alt_error` refuses
 *  the second panel at compile time.
 *
 *  RED under "drop transit_alt_error from types.ts", observed (beside the
 *  two TS2344 quoted at PICKED):
 *
 *    src/components/atlas/__tests__/mosaicNightSummary.test.ts(106,7): error
 *    TS2353: Object literal may only specify known properties, and
 *    'transit_alt_error' does not exist in type 'MosaicPanel'.
 *
 *  And it is this file that holds the link: the same mutation run again with
 *  mosaicNightSummary.ts as it was at 812fcf9e (its own copy of the field put
 *  back) left the summary compiling, and failed here alone, observed:
 *
 *    src/components/atlas/__tests__/mosaicNightSummary.test.ts(76,7): error
 *    TS2322: Type 'true' is not assignable to type 'false'.
 *    src/components/atlas/__tests__/mosaicNightSummary.test.ts(76,62): error
 *    TS2344: Type 'Read' does not satisfy the constraint 'keyof MosaicPanel'.
 *    src/components/atlas/__tests__/mosaicNightSummary.test.ts(106,7): error
 *    TS2353: Object literal may only specify known properties, and
 *    'transit_alt_error' does not exist in type 'MosaicPanel'. */
const ROUTE_ANSWER: MosaicResult = {
  panels: [
    { row: 0, col: 0, ra_hours: 0.7, dec_deg: 41.27, rotation_deg: 0, convergence_deg: 0, pa_deg: 0,
      transit_alt: 63.6 },
    { row: 0, col: 1, ra_hours: 0.72, dec_deg: 41.27, rotation_deg: 0, convergence_deg: 0, pa_deg: 0,
      transit_alt_error: "OSError: ephemeris table unreadable" },
  ],
  total_fov_x_deg: 2.2,
  total_fov_y_deg: 0.8,
  frame_fov_x_deg: 1.2,
  frame_fov_y_deg: 0.8,
  pixel_scale_arcsec: 0,
};

// =============================================================== tests

test("PanelNight is picked from MosaicPanel, optional modifiers and all", () => {
  // The check is the compile; this only makes the constant a used local.
  eq(PICKED, true, "Same<PanelNight, MosaicPanel>");
});

test("a route answer's panels go into the summary as they are, reason included", () => {
  // RED under the mutation "the summary drops the server's reason" (`const
  // why = "no reason was given";` in summarisePanelNight, in a scratch copy of
  // mosaicNightSummary.ts), observed (2/3 passed; the control below stayed
  // green):
  //
  //   x a route answer's panels go into the summary as they are, reason
  //     included: the summary's reasons (expected OSError: ephemeris table
  //     unreadable, got no reason was given)
  const panels: MosaicPanel[] = ROUTE_ANSWER.panels;
  const s = summarisePanelNight(panels, 30);
  eq(s.answered, 1, "answered panels");
  eq(s.missing, 1, "panels with no altitude");
  eq(s.reasons.join(" | "), "OSError: ephemeris table unreadable", "the summary's reasons");
  const said = missingAltitudeReason(s) ?? "";
  assert(/1 of 2 panels have no peak altitude \(panel 1-2\)/.test(said), `the sentence lost the panel: "${said}"`);
  assert(said.endsWith("OSError: ephemeris table unreadable"), `the sentence lost the reason: "${said}"`);
});

test("control: a night nobody asked about carries neither key, and says no reason was given", () => {
  // The route's plain answer (no night asked for) has neither key on any
  // panel. The summary is not asked about such an answer by either caller,
  // but if it were it must say so rather than invent a cause.
  const plain: MosaicPanel[] = [{ row: 0, col: 0, ra_hours: 0.7, dec_deg: 41.27, rotation_deg: 0,
    convergence_deg: 0, pa_deg: 0 }];
  const s = summarisePanelNight(plain, 30);
  eq(s.reasons.join(" | "), "no reason was given", "the reasons for a panel with neither key");
});

const total = passed + failed;
console.log(`atlas/__tests__/mosaicNightSummary.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
