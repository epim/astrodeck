// Unit tests for the Bahtinov live-overlay geometry (polish grab-bag (a)).
//
// Same tiny inline-assert harness as reportChart.test.ts / photometry.test.ts —
// no vitest/jest in this UI. Compiles under `tsc -b`; run directly with:
//   npx tsx src/lib/__tests__/bahtinovOverlay.test.ts
//
// What matters here is the COORDINATE CONTRACT: the server's data-space geometry
// must scale by the same `displayScale` StarOverlay uses, so the drawn spikes land
// on the star instead of somewhere plausible-looking nearby.

import { bahtinovSpikeSegments } from "../bahtinovOverlay";
import type { BahtinovGeom } from "../../types";

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
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function near(a: number, b: number, tol: number, msg = ""): void {
  if (!(Math.abs(a - b) <= tol)) throw new Error(`${msg} expected ~${b}, got ${a}`);
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

const geomAt = (x: number, y: number, angles: number[]): BahtinovGeom => ({
  center: [x, y],
  vertex: [x, y],
  spikes: angles.map((a, i) => ({ x, y, angle_deg: a, central: i === 1 })),
});

// ------------------------------------------------------------- scale + clip
test("bahtinovSpikeSegments: data coords scale by displayScale (lines land on the star)", () => {
  // star at data (100, 50); a 400-wide display over an 800-wide sensor => 0.5.
  const g = bahtinovSpikeSegments(geomAt(100, 50, [0, 45, 90]), 400, 200, 0.5);
  eq(g.vertex!.x, 50, "vertex x");
  eq(g.vertex!.y, 25, "vertex y");
  eq(g.spikes.length, 3, "three spikes");
  // the horizontal spike (0 deg) spans the full display width at the star's row.
  const horiz = g.spikes[0];
  near(Math.min(horiz.x1, horiz.x2), 0, 1e-9, "clipped to left edge");
  near(Math.max(horiz.x1, horiz.x2), 400, 1e-9, "clipped to right edge");
  near(horiz.y1, 25, 1e-9, "stays on the star's display row");
  near(horiz.y2, 25, 1e-9, "stays on the star's display row");
  // exactly one central spike survives the transform, and it is the middle one.
  eq(g.spikes.filter((s) => s.central).length, 1, "one central");
  eq(g.spikes[1].central, true, "middle spike is the central one");
});

test("bahtinovSpikeSegments: every angle clips to the box through the star", () => {
  const cases: [number, [number, number], [number, number]][] = [
    // angle, expected endpoint A, expected endpoint B (order-insensitive below)
    // star is at display (50, 25) after displayScale=0.5; box is 200x200.
    [90, [50, 0], [50, 200]],        // vertical through x=50
    [45, [25, 0], [200, 175]],       // y = x - 25   (y grows with x in svg space)
    [135, [0, 75], [75, 0]],         // y = 75 - x
  ];
  for (const [angle, a, b] of cases) {
    const g = bahtinovSpikeSegments(geomAt(100, 50, [angle, angle, angle]), 200, 200, 0.5);
    const s = g.spikes[0];
    const pts = [[s.x1, s.y1], [s.x2, s.y2]].sort((p, q) => p[0] - q[0] || p[1] - q[1]);
    const want = [a, b].sort((p, q) => p[0] - q[0] || p[1] - q[1]);
    near(pts[0][0], want[0][0], 1e-6, `angle ${angle} x1`);
    near(pts[0][1], want[0][1], 1e-6, `angle ${angle} y1`);
    near(pts[1][0], want[1][0], 1e-6, `angle ${angle} x2`);
    near(pts[1][1], want[1][1], 1e-6, `angle ${angle} y2`);
  }
});

// ------------------------------------------------------------------ abstain
test("bahtinovSpikeSegments: abstains on missing geom / degenerate stage", () => {
  const g = geomAt(100, 50, [0, 45, 90]);
  for (const [name, res] of [
    ["null geom", bahtinovSpikeSegments(null, 400, 200, 0.5)],
    ["undefined geom", bahtinovSpikeSegments(undefined, 400, 200, 0.5)],
    ["zero-width stage", bahtinovSpikeSegments(g, 0, 200, 0.5)],
    ["zero scale", bahtinovSpikeSegments(g, 400, 200, 0)],
  ] as const) {
    eq(res.spikes.length, 0, `${name}: no spikes`);
    eq(res.vertex, null, `${name}: no vertex`);
  }
  // a line that misses the frame entirely is dropped, not drawn off-canvas.
  const off = bahtinovSpikeSegments(
    { center: [0, 0], vertex: [500, 500], spikes: [{ x: 500, y: 500, angle_deg: 0, central: true }] },
    100, 100, 1,
  );
  eq(off.spikes.length, 0, "off-frame spike dropped");
  assert(off.vertex !== null, "vertex still reported (the stage may pan to it)");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nbahtinovOverlay.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
