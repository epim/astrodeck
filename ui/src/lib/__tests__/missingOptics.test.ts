// missingOptics.test.ts — the merged-optics gate names the ACTUAL missing
// fields (wave-1 §3.2). Run with:  npx tsx src/lib/__tests__/missingOptics.test.ts
// (from ui/)

import { missingOpticsFields } from "../framing";

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

test("null optics -> all three named", () => {
  assert(missingOpticsFields(null).join(",") === "focal length,pixel size,sensor size", "all missing");
});
test("complete optics -> empty", () => {
  assert(missingOpticsFields({ focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176 }).length === 0, "none missing");
});
test("camera-pending optics -> pixel + sensor named, focal not", () => {
  const m = missingOpticsFields({ focal_length_mm: 530, pixel_size_um: 0, sensor_width_px: 0, sensor_height_px: 0 });
  assert(m.join(",") === "pixel size,sensor size", `got ${m.join(",")}`);
});
test("half a sensor is still a missing sensor", () => {
  const m = missingOpticsFields({ focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 0 });
  assert(m.join(",") === "sensor size", `got ${m.join(",")}`);
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nmissingOptics.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}
export const result = { passed, failed, total };
