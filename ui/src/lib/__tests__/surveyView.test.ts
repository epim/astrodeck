// surveyView.test.ts — transform math for the pan/zoom-tracking survey frame.
// Run with:  npx tsx src/lib/__tests__/surveyView.test.ts   (from ui/)

import { surveyTransform, type SurveyGeom } from "../surveyView";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function near(a: number, b: number, tol: number, msg: string): void {
  assert(Math.abs(a - b) <= tol, `${msg} (got ${a}, want ${b} ±${tol})`);
}

const W = 768;
const g = (raDeg: number, decDeg: number, fovDeg: number): SurveyGeom =>
  ({ raDeg, decDeg, fovDeg });

test("identity: same geometry -> no transform", () => {
  const t = surveyTransform(g(150, 20, 2), g(150, 20, 2), W);
  near(t.dx, 0, 1e-9, "dx"); near(t.dy, 0, 1e-9, "dy"); near(t.scale, 1, 1e-12, "scale");
});

test("pure zoom: shown fov 2°, view fov 1° -> scale 2, no translate", () => {
  const t = surveyTransform(g(150, 20, 2), g(150, 20, 1), W);
  near(t.dx, 0, 1e-9, "dx"); near(t.dy, 0, 1e-9, "dy"); near(t.scale, 2, 1e-12, "scale");
});

test("view north of shown -> frame slides DOWN (dy positive)", () => {
  // view center 0.5° north; eta ~= +0.5 -> dy ~= 0.5 * 768/2 = 192
  const t = surveyTransform(g(150, 20, 2), g(150, 20.5, 2), W);
  near(t.dy, 192, 2, "dy"); near(t.dx, 0, 2, "dx");
});

test("view east of shown at dec 60 -> cos-dec-scaled dx, positive (E is screen-left)", () => {
  // +1° of RA at dec 60 is ~cos(60°)=0.5° on the sky -> dx ~= 0.5 * 768/2 = 192
  const t = surveyTransform(g(150, 60, 2), g(151, 60, 2), W);
  near(t.dx, 192, 4, "dx");  // TAN-exact vs small-angle: allow ~2%
});

test("RA wrap: shown 359.9°, view 0.1° -> small positive dx, not a full-circle jump", () => {
  const t = surveyTransform(g(359.9, 0, 2), g(0.1, 0, 2), W);
  near(t.dx, 0.2 * (W / 2), 1, "dx");   // 0.2° east -> ~76.8 px
  assert(Math.abs(t.dx) < 200, "wrap must not explode");
});

test("residual scale from fov snapping stays tiny", () => {
  // server snaps fov on a 2% grid -> worst residual ~1%
  const t = surveyTransform(g(150, 20, 1.4859), g(150, 20, 1.5), W);
  assert(Math.abs(t.scale - 1) < 0.011, `scale residual ${t.scale}`);
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nsurveyView.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}
export const result = { passed, failed, total };
