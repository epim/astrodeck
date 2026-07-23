// Unit tests for the star-ellipse geometry helper (PRO-7 §3 Task 3).
//
// No vitest/jest is wired into this UI (build is `tsc -b && vite build`), so
// these use the same tiny inline-assert harness as eta.test.ts. They compile
// under `tsc -b` and run directly with a TS-aware runner:
//   npx tsx src/lib/__tests__/starEllipse.test.ts

import { ellipseGeom } from "../starEllipse";

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
function near(a: number, b: number, tol: number, msg = ""): void {
  if (Math.abs(a - b) > tol) throw new Error(`${msg} expected ~${b}, got ${a}`);
}

// ---------------------------------------------------------------- tests
test("no ecc -> null (plain circle)", () => {
  assert(ellipseGeom(10, undefined, undefined) === null, "undefined ecc");
});
test("round star -> near-circular", () => {
  const g = ellipseGeom(10, 0.0, 0.0)!;
  near(g.rx, 10, 1e-9, "rx");
  near(g.ry, 10, 1e-9, "ry");
  near(g.rotDeg, 0, 1e-9, "rot");
});
test("ecc 0.866 -> minor = major/2", () => {
  const g = ellipseGeom(10, 0.866, 0.0)!; // sqrt(1-0.866^2)=0.5
  near(g.rx, 10, 1e-6, "major");
  near(g.ry, 5, 0.02, "minor");
});
test("theta maps to degrees", () => {
  const g = ellipseGeom(10, 0.7, Math.PI / 4)!;
  near(g.rotDeg, 45, 1e-6, "rot deg");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nstarEllipse.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
