// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_9SuggestEllipse.test.ts - SUGGEST GRID and FIT OBJECT use the object's
// ellipse, not a circle across its long axis (#181, consolidation WP 3.9).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w3_9SuggestEllipse.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE NUMBERS ARE WORKED BY HAND. A galaxy 178 x 69.3 arcminutes with its major
// axis 35 degrees east of north, on a 2.00 x 1.33 degree camera at 25 percent
// overlap. The grid's rows run along the frame's up axis, which lies at
// -rotation east of north, so the major axis is `pa + rotation` from it:
//
//   rotation 0   -> 35 from up : reach along rows hypot(178 cos 35, 69.3 sin 35)
//                    = 151.1' = 2.518 deg, across hypot(178 sin 35, 69.3 cos 35)
//                    = 116.8' = 1.947 deg. rows (2.518 - 0.3325) / 0.9975 = 2.19
//                    -> 3, cols (1.947 - 0.5) / 1.5 = 0.96 -> 1: 1 x 3, against
//                    the circle rule's 2 x 3.
//   rotation 145 -> 180: the major axis lies along the rows. 178' = 2.967 deg
//                    gives 3 rows, 69.3' = 1.155 deg gives 1 column: 1 x 3.
//   rotation 55  -> 90: the major axis lies along the columns. 1.155 deg gives
//                    1 row, 2.967 deg gives (2.967 - 0.5) / 1.5 = 1.64 -> 2
//                    columns: 2 x 1. The same galaxy asks for a different grid
//                    when the camera is turned, which a circle cannot say.
//
// NAMED MUTANTS (each from a byte backup of framingModel.ts, restored and
// sha256 compared; the failure is the first line of the test that caught it):
//   "ellipse ignored" (suggestGridFor always takes the circle branch):
//     x SUGGEST GRID for an ellipse is sized along the grid's own axes: M31 at
//       rotation 0: expected 1x3, the circle rule's 2x3 over-tiles it: expected
//       {"cols":1,"rows":3,"ellipse":true}, got {"cols":2,"rows":3,"ellipse":false}
//   "angle not carried to the frame" (suggestGridFor passes pa_deg alone, not
//   pa_deg + rotation): the rotation 55 case, same test:
//     x ...: the major axis along the columns: expected
//       {"cols":2,"rows":1,"ellipse":true}, got {"cols":1,"rows":3,"ellipse":true}

/* eslint-disable @typescript-eslint/no-explicit-any */

import { draftFromParams, suggestGrid, suggestGridFor } from "../framingModel";
import type { FramingDraft, Params } from "../framingModel";
import type { CatalogEntry } from "../../../../types";
import { ellipseExtents, fitSizeArcmin, objectShape } from "../../../../lib/objectShape";

const proc = (globalThis as any).process;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const js = (v: unknown) => JSON.stringify(v);
function eq(got: unknown, want: unknown, what: string): void {
  assert(js(got) === js(want), `${what}: expected ${js(want)}, got ${js(got)}`);
}
function near(got: number, want: number, tol: number, what: string): void {
  assert(Math.abs(got - want) <= tol, `${what}: expected ${want} +- ${tol}, got ${got}`);
}

// ------------------------------------------------------------- fixtures

const GALAXY: CatalogEntry = {
  id: "TEST 1", name: "Test galaxy", type: "Galaxy", ra_hours: 1.0, dec_deg: 30.0,
  mag: 4.0, size_arcmin: 178, minor_arcmin: 69.3, pa_deg: 35,
};

const BLOCK: Params = {
  name: "Test galaxy", ra: "01h 00m 00s", dec: "+30 00 00",
  rotation: 0, angle: "Rotate to PA", rows: 2, cols: 3, overlap: 25,
  fovX: 2.0, fovY: 1.33, fovFrom: "test camera", skip: "",
};
const draftAt = (rotation: number, over: Partial<Params> = {}): FramingDraft =>
  draftFromParams({ ...BLOCK, rotation, ...over });

// ------------------------------------------------------------------ tests

test("the shape of a row is its ellipse, or nothing when it is a circle", () => {
  eq(objectShape(GALAXY), { major_arcmin: 178, minor_arcmin: 69.3, pa_deg: 35 }, "an ellipse");
  eq(objectShape({ ...GALAXY, minor_arcmin: null, pa_deg: null }), null, "no minor axis");
  eq(objectShape({ ...GALAXY, minor_arcmin: 178 }), null, "a minor axis as long as the major");
  eq(objectShape({ ...GALAXY, minor_arcmin: 200 }), null, "a minor axis longer than the major");
  eq(objectShape({ ...GALAXY, size_arcmin: 0 }), null, "no size to scale");
  eq(objectShape({ ...GALAXY, pa_deg: null }),
    { major_arcmin: 178, minor_arcmin: 69.3, pa_deg: null }, "an axis ratio with no angle");
  eq(objectShape({ ...GALAXY, pa_deg: 0 })?.pa_deg, 0, "an angle of 0 is due north, an angle");
});

test("an ellipse reaches hypot(a cos d, b sin d) along an axis d from its major axis", () => {
  const shape = objectShape(GALAXY)!;
  const reach = ellipseExtents(shape, 35);
  near(reach.along, 151.1, 0.1, "along");
  near(reach.across, 116.8, 0.1, "across");
  eq(ellipseExtents(shape, 0), { along: 178, across: 69.3 }, "the major axis along the first");
  const quarter = ellipseExtents(shape, 90);
  near(quarter.along, 69.3, 1e-9, "a quarter turn, along");
  near(quarter.across, 178, 1e-9, "a quarter turn, across");
});

test("SUGGEST GRID for an ellipse is sized along the grid's own axes", () => {
  eq(suggestGrid(178, draftAt(0)), { cols: 2, rows: 3 }, "premise: the circle rule");
  eq(suggestGridFor(GALAXY, draftAt(0)), { cols: 1, rows: 3, ellipse: true },
    "M31 at rotation 0: expected 1x3, the circle rule's 2x3 over-tiles it");
  eq(suggestGridFor(GALAXY, draftAt(145)), { cols: 1, rows: 3, ellipse: true },
    "the major axis along the rows");
  eq(suggestGridFor(GALAXY, draftAt(55)), { cols: 2, rows: 1, ellipse: true },
    "the major axis along the columns");
});

test("an ellipse never suggests more panels than its circle", () => {
  for (let rotation = 0; rotation < 360; rotation += 15) {
    const e = suggestGridFor(GALAXY, draftAt(rotation))!;
    const c = suggestGrid(178, draftAt(rotation))!;
    assert(e.cols <= c.cols && e.rows <= c.rows,
      `rotation ${rotation}: ellipse ${e.cols}x${e.rows} against circle ${c.cols}x${c.rows}`);
  }
});

test("with no ellipse, no angle on the object or no camera angle it is the circle rule", () => {
  const circle = { cols: 2, rows: 3, ellipse: false };
  eq(suggestGridFor({ ...GALAXY, minor_arcmin: null, pa_deg: null }, draftAt(0)), circle,
    "no minor axis");
  eq(suggestGridFor({ ...GALAXY, pa_deg: null }, draftAt(0)), circle, "no position angle");
  eq(suggestGridFor(GALAXY, draftAt(0, { angle: "Any angle", rotation: -1 })), circle,
    "no camera angle yet");
  eq(suggestGridFor(GALAXY, draftAt(0, { fovX: 0 })), null, "no camera field, no suggestion");
  eq(suggestGridFor({ ...GALAXY, size_arcmin: 0, minor_arcmin: null, pa_deg: null }, draftAt(0)),
    null, "no size, no suggestion");
});

test("FIT OBJECT shows the ellipse's north-up reach, the long axis with no angle", () => {
  near(fitSizeArcmin(GALAXY), 151.1, 0.1, "major axis 35 east of north");
  eq(fitSizeArcmin({ ...GALAXY, pa_deg: 0 }), 178, "due north");
  eq(fitSizeArcmin({ ...GALAXY, pa_deg: 90 }), 178, "due east");
  eq(fitSizeArcmin({ ...GALAXY, pa_deg: null }), 178, "no angle");
  eq(fitSizeArcmin({ ...GALAXY, minor_arcmin: null, pa_deg: null }), 178, "a circle");
  eq(fitSizeArcmin({ ...GALAXY, size_arcmin: 0, minor_arcmin: null, pa_deg: null }), 0, "no size");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w3_9SuggestEllipse.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) proc.exitCode = 1;
export const result = { passed, failed, total };
export default result;
