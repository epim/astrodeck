// ascomLocalRig.test.ts — buildRigSpec compiles an ascom-local assignment to a
// backend:"ascom-local" ConnSpec with NO driver_id (implicit backend, like
// sim), carrying the picked dev_type/dev_num. Run: npx tsx src/lib/__tests__/ascomLocalRig.test.ts
import { buildRigSpec, type AssignmentMap } from "../equipment";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

test("ascom-local compiles to backend ascom-local, no driver_id", () => {
  const map: AssignmentMap = {
    telescope: { driverId: "ascom-local", devType: "telescope", devNum: 0, name: "Sim Scope" },
  };
  const spec = buildRigSpec(map);
  const t = spec.roles.telescope;
  assert(t.backend === "ascom-local", `backend was ${t.backend}`);
  assert(t.driver_id === undefined, "implicit backend carries no driver_id");
  assert(t.dev_type === "telescope" && t.dev_num === 0, "addressing carried");
  assert((t.extra as { name?: string })?.name === "Sim Scope", "name folded into extra");
});

test("sim still compiles to backend sim (unchanged)", () => {
  const spec = buildRigSpec({ camera: { driverId: "sim" } });
  assert(spec.roles.camera.backend === "sim", "sim branch intact");
});

console.log(`ascomLocalRig.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
