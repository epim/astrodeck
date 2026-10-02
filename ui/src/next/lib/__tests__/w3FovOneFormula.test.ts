// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// WP-29 (a) / #168: one FOV function serves both UIs, and a recorded-but-
// never-applied focal reducer must not make them disagree.
//
// Before the fix, `next/lib/fov.ts`'s `fovDeg` multiplied the optics'
// `reducer` into the effective focal length while `lib/framing.ts`'s
// `fovFromOptics` (and the server's `config.fov_deg`) never did -- so the
// Settings Optics-sheet preview and the Atlas overlay computed two different
// fields for the identical rig config whenever a reducer was recorded,
// exactly the "two UIs disagree about the focal reducer" #168 reported.
//
// Named mutant: re-introduce the reducer multiply in `fovDeg`
// (`const eff = o.flMm * (o.reducer ?? 1); fovDegFromSensorMm(o.sensorWmm, eff)`)
// and the second test below goes red (the 0.8x-reducer case stops matching
// `fovFromOptics`, which never saw a reducer at all).
import { fovFromOptics, type OpticsLike } from "../../../lib/framing";
import { fovDeg } from "../fov";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function near(a: number, b: number, eps: number, m = ""): void {
  if (Math.abs(a - b) > eps) throw new Error(`${m} expected ~${b}, got ${a}`);
}

// The SAME config `framing.test.ts`'s "fovFromOptics sanity" fixture uses
// (500mm, 3.76um, 6248x4176 APS-C) -- so this test and that one, and the
// server's `test_w3_fov_reducer_consistency.py`, all reason about one rig.
const OPTICS: OpticsLike = {
  focal_length_mm: 500,
  pixel_size_um: 3.76,
  sensor_width_px: 6248,
  sensor_height_px: 4176,
};

function asMm(o: OpticsLike): { sensorWmm: number; sensorHmm: number; flMm: number } {
  return {
    sensorWmm: (o.sensor_width_px * o.pixel_size_um) / 1000,
    sensorHmm: (o.sensor_height_px * o.pixel_size_um) / 1000,
    flMm: o.focal_length_mm,
  };
}

// (a) With NO reducer recorded, the two UI functions already have to agree --
// this pins that they go on agreeing now that `fovDeg` is a thin wrapper
// around the same `fovDegFromSensorMm` `fovFromOptics` uses.
test("fovFromOptics and next/lib/fov's fovDeg: same config, same field (no reducer)", () => {
  const viaFraming = fovFromOptics(OPTICS);
  const viaNext = fovDeg({ ...asMm(OPTICS), reducer: 1 });
  near(viaNext.wDeg, viaFraming.fov_x_deg, 1e-9, "wDeg");
  near(viaNext.hDeg, viaFraming.fov_y_deg, 1e-9, "hDeg");
  // Anchors both to the known worked value (framing.test.ts's own tolerance).
  near(viaFraming.fov_x_deg, 2.69, 0.05, "fov_x sanity");
});

// (b) THE #168 CASE: a 0.8x reducer is recorded (as it would be after the
// operator fills in `Optics.reducer` without pressing "USE THE REDUCED FOCAL
// LENGTH"). `fovFromOptics` has no reducer input at all -- config.py's
// `Optics.reducer` is recorded, never applied (config.py:97-111). `fovDeg`
// must now agree with it exactly, not multiply the reducer in.
test("fovDeg ignores a recorded reducer, matching fovFromOptics exactly (#168)", () => {
  const viaFraming = fovFromOptics(OPTICS); // no reducer parameter to give it
  const viaNextNoReducer = fovDeg(asMm(OPTICS));
  const viaNextWithReducer = fovDeg({ ...asMm(OPTICS), reducer: 0.8 });
  near(viaNextNoReducer.wDeg, viaFraming.fov_x_deg, 1e-9, "no-reducer wDeg");
  near(
    viaNextWithReducer.wDeg, viaFraming.fov_x_deg, 1e-9,
    "a recorded 0.8x reducer changed fovDeg's answer -- it must be ignored, same as fovFromOptics and config.fov_deg",
  );
  near(
    viaNextWithReducer.hDeg, viaFraming.fov_y_deg, 1e-9,
    "a recorded 0.8x reducer changed fovDeg's height -- it must be ignored",
  );
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export { passed, failed };
export const total = passed + failed;
