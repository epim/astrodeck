import assert from "node:assert/strict";
import { bandForAltitude, cameraElevation, cameraPose, projectSweepColumns, traceSkyCoverage, OVERHEAD_BAND, type SweepFrame } from "../photosphere";
import { altFromY, SKY_Y } from "../horizonStrip";
import { isObstructed, movePoint } from "../../../../lib/horizonModel";

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }
const frame = (bin: number, band: number, altitude: number, obstacle: (alt: number) => boolean): SweepFrame => ({
  bin, band, altitude, verticalFov: 45,
  column: Array.from({ length: 181 }, (_, i) => obstacle(altitude + (0.5 - i / 180) * 45) ? 25 : 240),
});
const scan = (obstacle: (alt: number) => boolean) => [
  ...[0, 35, 70].flatMap((alt, band) => [0, 1].map(bin => frame(bin, band, alt, bin === 0 ? obstacle : () => false))),
  frame(0, OVERHEAD_BAND, 90, () => false),
];

test("Vertical orientation remains valid at the zenith and distinguishes three rings", () => {
  assert.equal(bandForAltitude(0), 0); assert.equal(bandForAltitude(35), 1);
  assert.equal(bandForAltitude(70), 2); assert.equal(bandForAltitude(90), OVERHEAD_BAND);
  assert.equal(bandForAltitude(-30), null);
  const top = cameraPose({ alpha: 150, beta: 180, gamma: 0, absolute: true });
  assert.ok(top); assert.ok(top.alt > 89.99); assert.ok(Number.isFinite(top.az));
});
test("A nearby house reaching 75 degrees is retained above the low sweep", () => {
  const columns = projectSweepColumns(scan(alt => alt <= 75), 2);
  assert.ok(columns.every(col => col.slice(0, 91).every(Number.isFinite)));
  const trace = traceSkyCoverage(columns);
  assert.equal(trace.uncertainBins.length, 0);
  assert.ok(trace.points[0].alt >= 75 && trace.points[0].alt <= 77);
  assert.equal(trace.points[1].alt, 0);
  assert.ok(isObstructed(trace.points, 70, trace.points[0].az));
});
test("Overhead tilt needs no heading and accepts either side of the beta wrap", () => {
  for (const beta of [180, -180, 176, -176]) {
    assert.equal(bandForAltitude(cameraElevation({ beta, gamma: 0 })!), OVERHEAD_BAND);
  }
  assert.notEqual(bandForAltitude(cameraElevation({ beta: 170, gamma: 0 })!), OVERHEAD_BAND);
  assert.equal(cameraElevation({ beta: null, gamma: 0 }), null);
  assert.equal(cameraElevation({ beta: 180, gamma: NaN }), null);
  assert.equal(cameraElevation({ beta: 0, gamma: 0 }), -90);
});
test("Near-overhead branches survive even when sky is visible beneath them", () => {
  const trace = traceSkyCoverage(projectSweepColumns(scan(alt => alt >= 81 && alt <= 88), 2));
  assert.ok(trace.points[0].alt >= 88);
  assert.equal(trace.points[1].alt, 0);
});
test("Returning to the lower elevation cannot erase a high obstruction", () => {
  const frames = scan(alt => alt <= 82);
  frames.push(frame(0, 0, 0, () => false));
  const trace = traceSkyCoverage(projectSweepColumns(frames, 2));
  assert.ok(trace.points[0].alt >= 82);
});
test("An unscanned upper sky is unknown and cannot be accepted as open sky", () => {
  const trace = traceSkyCoverage(projectSweepColumns([frame(0, 0, 0, () => false)], 2));
  assert.deepEqual(trace.uncertainBins, [0, 1]);
  assert.ok(trace.points.every(p => p.alt === 90));
});
test("A blocked zenith remains blocked even if subsequent high views look clear", () => {
  const frames = scan(() => false);
  frames.splice(frames.length - 1, 1);
  frames.unshift(frame(0, OVERHEAD_BAND, 90, () => true));
  const trace = traceSkyCoverage(projectSweepColumns(frames, 2));
  assert.ok(trace.points.every(p => p.alt === 90));
});
test("Manual review can retain obstructions all the way to 90 degrees", () => {
  assert.equal(altFromY(SKY_Y), 90);
  assert.equal(movePoint([{ az: 180, alt: 90 }], 0, 180, 90)[0].alt, 90);
});
console.log(`photosphereVertical.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
