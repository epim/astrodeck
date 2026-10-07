// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Per-panel position angle in the client mirror (#175, backlog WP-124): the
// two keys `mosaicGrid` now carries per panel, `convergence_deg` (local north
// on the grid's tangent plane, from +eta toward +xi) and `pa_deg` (the layout
// angle plus it, wrapped into [0,360)), against GOLDEN VECTORS shared with the
// server. The same table is in `server/tests/test_catalog_frame_id.py`
// (`_PANEL_PA_GOLDEN`), checked there against `framing.compute_mosaic`; they
// were printed from THIS mirror at full precision and agree with the server to
// 1.3e-10 deg (the trig libraries' last place, taken up by the projection's
// 1e-4 deg probe), so both sides hold them to 1e-8.
//
// The convention under them is the rig's, confirmed on a real solve (#175,
// 2026-10-07): image up is north rotated toward WEST by CROTA2, so a frame at
// sky PA theta_i lies on the grid when theta_i = theta + n_i. The sign is not
// taken from the formula: the "lies on the grid" case projects the camera-up
// direction of each panel at its `pa_deg` into the grid plane and compares it
// with the grid's own up.
//
// Same tiny inline-assert harness as framing.test.ts (no framework): run with
//   npx tsx src/lib/__tests__/w16PanelPa.test.ts
// Each case names the mutant it was shown RED under, with the failure seen.

import { mosaicGrid, panelConvergenceDeg, project, type MosaicGridSpec } from "../framing";

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
    failures.push(`FAIL ${name}: ${(e as Error).message}`);
  }
}

function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function near(a: number, b: number, tol: number, msg = ""): void {
  if (!(Math.abs(a - b) <= tol)) throw new Error(`${msg} expected ~${b}, got ${a}`);
}
/** The distance between two angles, in [0, 180]. */
function circ(a: number, b: number): number {
  return Math.abs((((a - b + 180) % 360) + 360) % 360 - 180);
}

// ------------------------------------------------------------ golden vectors
// A 3x3 of the spec's reference panels (2.0 x 1.33 deg, 25% overlap) at RA 6 h,
// laid out at the angle each case names. (row, col, convergence_deg, pa_deg).
const BASE: MosaicGridSpec = {
  ra_hours: 6.0,
  dec_deg: 75.0,
  rows: 3,
  cols: 3,
  overlap: 0.25,
  rotation_deg: 0.0,
  fov_x_deg: 2.0,
  fov_y_deg: 1.33,
};

type Row = [number, number, number, number];

const GOLDEN: { name: string; spec: MosaicGridSpec; rows: Row[] }[] = [
  {
    // Issue #175's own figures: 5.97 deg at the corners at Dec 75.
    name: "Dec 75, layout 0",
    spec: BASE,
    rows: [
      [0, 0, 5.9654296824786535, 5.9654296824786535],
      [0, 1, 0, 0],
      [0, 2, -5.9654296824786535, 354.03457031752134],
      [1, 2, -5.580364025849719, 354.41963597415025],
      [1, 1, 0, 0],
      [1, 0, 5.580364025976295, 5.580364025976295],
      [2, 0, 5.2418652672010495, 5.2418652672010495],
      [2, 1, 0, 0],
      [2, 2, -5.2418652672010495, 354.75813473279896],
    ],
  },
  {
    // ...and 1.32 deg at Dec 41.
    name: "Dec 41, layout 0",
    spec: { ...BASE, dec_deg: 41.0 },
    rows: [
      [0, 0, 1.3237314315092013, 1.3237314315092013],
      [0, 1, 0, 0],
      [0, 2, -1.3237314315092013, 358.6762685684908],
      [1, 2, -1.303705065822304, 358.6962949341777],
      [1, 1, 0, 0],
      [1, 0, 1.303705065822304, 1.303705065822304],
      [2, 0, 1.2842755162870618, 1.2842755162870618],
      [2, 1, 0, 0],
      [2, 2, -1.2842755162870618, 358.71572448371296],
    ],
  },
  {
    // The wrap: 358 + 5.85 is 363.85, which reads 3.85.
    name: "Dec 75, layout 358 (the wrap)",
    spec: { ...BASE, rotation_deg: 358.0 },
    rows: [
      [0, 0, 5.845261522679594, 3.845261522679607],
      [0, 1, -0.13894296730754027, 357.8610570326925],
      [0, 2, -6.076987218756715, 351.9230127812433],
    ],
  },
  {
    // A layout angle that is neither 0 nor a right angle, on a 2 x 3 grid.
    name: "Dec 60, layout 37, 2 rows by 3 columns",
    spec: { ...BASE, dec_deg: 60.0, rows: 2, cols: 3, rotation_deg: 37.0 },
    rows: [
      [0, 0, 2.5541353140949665, 39.554135314094964],
      [0, 1, 0.5262056577787382, 37.52620565777874],
      [0, 2, -1.6182654363403373, 35.38173456365966],
      [1, 2, -2.6331231655053107, 34.36687683449469],
      [1, 1, -0.5136848230263885, 36.48631517697361],
      [1, 0, 1.4958461166961985, 38.4958461166962],
    ],
  },
  {
    // A row of four at Dec 80 and 10%: where convergence is worst (15 deg).
    name: "Dec 80, 4 columns by 1 row, 10% overlap",
    spec: { ...BASE, dec_deg: 80.0, rows: 1, cols: 4, overlap: 0.1 },
    rows: [
      [0, 0, 14.962769174968981, 14.962769174968981],
      [0, 1, 5.0907153626880515, 5.0907153626880515],
      [0, 2, -5.090715362751404, 354.9092846372486],
      [0, 3, -14.962769174968981, 345.03723082503103],
    ],
  },
];

/** The golden tolerance, in degrees: see the header. */
const TOL = 1e-8;

// ================================================================== tests
for (const g of GOLDEN) {
  // Mutant "n_i sign flipped" (the atan2 arguments swapped for their
  // negatives, i.e. `panelConvergenceDeg` returns -n): the first row of
  // "Dec 75, layout 0" fails with
  //   Dec 75, layout 0 (0,0) convergence_deg expected ~5.9654..., got -5.9654...
  // Mutant "pa_deg = rotation_deg" (the key written from the layout angle)
  // fails "Dec 75, layout 0 (0,0) pa_deg expected ~5.9654..., got 0".
  test(`golden: ${g.name}`, () => {
    const panels = mosaicGrid(g.spec);
    for (const [row, col, conv, pa] of g.rows) {
      const p = panels.find((q) => q.row === row && q.col === col);
      assert(p !== undefined, `${g.name}: no panel (${row},${col})`);
      near(p!.convergence_deg, conv, TOL, `${g.name} (${row},${col}) convergence_deg`);
      near(p!.pa_deg, pa, TOL, `${g.name} (${row},${col}) pa_deg`);
    }
  });
}

test("rotation_deg stays the layout angle on every panel", () => {
  // Mutant "layout angle carries the correction" (rotation_deg written as
  // pa_deg) fails: rotation_deg expected ~0, got 5.9654... The Atlas draws
  // each panel's rectangle at rotation_deg (PanelLayer), so a corrected angle
  // there would draw the wedges the correction removes.
  for (const g of GOLDEN) {
    for (const p of mosaicGrid(g.spec)) {
      assert(p.rotation_deg === g.spec.rotation_deg, `${g.name}: rotation_deg ${p.rotation_deg}`);
    }
  }
});

test("every pa_deg is in [0, 360)", () => {
  // Mutant "pa_deg not wrapped" fails the layout-0 case: -5.9654... is not
  // in [0, 360).
  for (const g of GOLDEN) {
    for (const p of mosaicGrid(g.spec)) {
      assert(p.pa_deg >= 0 && p.pa_deg < 360, `${g.name}: pa_deg ${p.pa_deg}`);
    }
  }
});

test("a tiny negative sum wraps to 0, never to 360", () => {
  // A middle-column panel can come out a hair either side of the layout
  // angle, and adding 360 to -1e-15 rounds to exactly 360, which is not a
  // position angle in [0,360). One panel at the tangent point turns by
  // nothing, so a layout angle of -1e-15 IS that sum.
  //   Mutant "wrap hole open" (the `pa >= 360` guard deleted) fails:
  //   pa_deg 360
  const spec: MosaicGridSpec = { ...BASE, rows: 1, cols: 1, rotation_deg: -1e-15 };
  const [p] = mosaicGrid(spec);
  assert(p.pa_deg >= 0 && p.pa_deg < 360, `pa_deg ${p.pa_deg}`);
  assert(p.pa_deg === 0, `pa_deg ${p.pa_deg} is not 0`);
});

test("a frame at each panel's pa_deg lies on the grid", () => {
  // THE SIGN, DERIVED FROM THE PROJECTION. Put the camera's up direction at a
  // panel on the sky (north rotated toward WEST by its pa_deg, the rig's
  // convention), step a small way along it, project the step into the grid
  // plane: it must point where the grid's own up points, -layout from +eta
  // toward +xi. Uses nothing of the formula the keys are made with.
  //   Mutant "n_i sign flipped": the camera up lands 11.9 deg from the grid's.
  // 0.05 deg, not rounding: the gnomonic is not conformal, so a step's
  // direction on the grid plane is off the sky's by up to 0.027 deg at the
  // 3 deg of these corners (0.002 at layout 0).
  const eps = 1e-4;
  for (const dec of [75.0, 41.0]) {
    for (const layout of [0.0, 37.0, 90.0, 215.0]) {
      const spec: MosaicGridSpec = { ...BASE, dec_deg: dec, rotation_deg: layout };
      for (const p of mosaicGrid(spec)) {
        const pa = (p.pa_deg * Math.PI) / 180;
        const dec1 = p.dec_deg + eps * Math.cos(pa);
        const ra1 =
          p.ra_hours + (eps * -Math.sin(pa)) / Math.cos((p.dec_deg * Math.PI) / 180) / 15;
        const a = project(p.ra_hours, p.dec_deg, spec.ra_hours, spec.dec_deg);
        const b = project(ra1, dec1, spec.ra_hours, spec.dec_deg);
        const up = (Math.atan2(b.xi - a.xi, b.eta - a.eta) * 180) / Math.PI;
        const off = circ(up, -layout);
        assert(
          off < 0.05,
          `Dec ${dec}, layout ${layout}, panel (${p.row},${p.col}): camera up ${off.toFixed(3)} deg from the grid's up`,
        );
      }
    }
  }
});

test("at the pole local north turns by the RA difference", () => {
  // Within NORTH_PROBE_DEG of the pole the probe goes south and the answer is
  // reversed; the mirror and the server agree to the bit on this vector, and
  // the value is the analytic one: at the pole, local north is rotated from
  // the grid's up by 15 deg per hour of RA from the tangent point.
  //   Mutant "pole probe not reversed": the answer is -165 deg, not 15.
  const n = panelConvergenceDeg({ ra_hours: 3.0, dec_deg: 89.99995 }, 4.0, 89.5);
  near(n, 14.999454586703967, TOL, "pole vector");
  near(n, 15.0, 0.001, "analytic: 15 deg per hour of RA at the pole");
});

test("a column along the centre meridian is not turned", () => {
  // Mutant "convergence from the row": a 4x1 at layout 0 reads 11.9 deg.
  const spec: MosaicGridSpec = { ...BASE, rows: 4, cols: 1 };
  for (const p of mosaicGrid(spec)) {
    near(p.convergence_deg, 0, 1e-9, `(${p.row},${p.col}) convergence_deg`);
    assert(circ(p.pa_deg, 0) < 1e-9, `(${p.row},${p.col}) pa_deg ${p.pa_deg}`);
  }
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw16PanelPa.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
