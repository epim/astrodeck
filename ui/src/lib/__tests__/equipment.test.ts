// equipment.test.ts — pure-logic tests for the Equipment assignment lane
// (spec §4.1). Inline-assert harness (no vitest); runs via `npx tsx`.
import {
  buildRigSpec,
  deviceChoices,
  eligibleDrivers,
  eligibleTaskDrivers,
  hasRealMotion,
  simAssignments,
  slotState,
  type AssignmentMap,
} from "../equipment";
import type { DriverInfo } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

const drv = (over: Partial<DriverInfo>): DriverInfo => ({
  id: "alpaca-ab12", type: "alpaca", label: "Alpaca", enabled: true,
  implicit: false,
  status: { reachable: true, error: null, detail: null, probed_at: 0 },
  offers: { devices: [], tasks: [] },
  ...over,
});

const alpaca = drv({
  offers: {
    devices: [
      { role: "camera", name: "ASI2600MM", dev_type: "camera", dev_num: 0 },
      { role: "camera", name: "ASI220MM", dev_type: "camera", dev_num: 1 },
      { role: "focuser", name: "EAF", dev_type: "focuser", dev_num: 0 },
    ],
    tasks: [],
  },
});
const sim = drv({
  id: "sim", type: "sim", label: "Simulator", implicit: true,
  offers: {
    devices: [
      { role: "camera", name: "Simulated camera" },
      { role: "telescope", name: "Simulated telescope" },
    ],
    tasks: ["autofocus", "polar_align"],
  },
});

test("eligibleDrivers applies the one rule", () => {
  eq(eligibleDrivers("camera", [alpaca, sim]).length, 2);
  eq(eligibleDrivers("telescope", [alpaca, sim]).map((d) => d.id).join(","), "sim");
  const down = { ...alpaca, status: { ...alpaca.status, reachable: false } };
  eq(eligibleDrivers("camera", [down, sim]).map((d) => d.id).join(","), "sim");
  const off = { ...alpaca, enabled: false };
  eq(eligibleDrivers("camera", [off, sim]).map((d) => d.id).join(","), "sim");
});

test("deviceChoices filters the driver's offers by role", () => {
  eq(deviceChoices("camera", alpaca).length, 2);
  eq(deviceChoices("focuser", alpaca)[0].name, "EAF");
  eq(deviceChoices("guider", alpaca).length, 0);
});

test("slotState is sticky and honest", () => {
  const a = { driverId: "alpaca-ab12" };
  eq(slotState("camera", null, [alpaca]), "unassigned");
  eq(slotState("camera", a, [alpaca]), "ok");
  eq(slotState("camera", a, []), "driver-removed");
  eq(slotState("camera", a, [{ ...alpaca, enabled: false }]), "driver-disabled");
  eq(slotState("camera", a, [{ ...alpaca, status: { ...alpaca.status, reachable: false } }]),
     "driver-unreachable");
});

test("slotState reads device-missing when the probe drops the assigned role's offer (amended post-review)", () => {
  const a = { driverId: "alpaca-ab12" };
  const noCamera = {
    ...alpaca,
    offers: { devices: alpaca.offers.devices.filter((o) => o.role !== "camera"), tasks: [] },
  };
  // enabled + reachable, but the camera offer is gone (unplugged mid-session)
  eq(slotState("camera", a, [noCamera]), "device-missing");
  // a role the driver still offers stays ok
  eq(slotState("focuser", a, [noCamera]), "ok");
  // offer present -> ok unchanged
  eq(slotState("camera", a, [alpaca]), "ok");
});

test("slotState device-missing matches devNum granularity when the assignment pins one", () => {
  // pinned to dev_num 1 ("ASI220MM"); only dev_num 0 remains offered
  const pinned = { driverId: "alpaca-ab12", devType: "camera", devNum: 1 };
  const onlyDevZero = {
    ...alpaca,
    offers: {
      devices: alpaca.offers.devices.filter((o) => !(o.role === "camera" && o.dev_num === 1)),
      tasks: [],
    },
  };
  eq(slotState("camera", pinned, [onlyDevZero]), "device-missing");
  eq(slotState("camera", pinned, [alpaca]), "ok");
});

test("buildRigSpec compiles explicit-only rigs with driver_id", () => {
  const m: AssignmentMap = {
    camera: { driverId: "alpaca-ab12", devType: "camera", devNum: 1, name: "ASI220MM" },
    telescope: { driverId: "sim", name: "Simulated telescope" },
    guider: null,
  };
  const rs = buildRigSpec(m);
  eq(rs.primary, "none");
  eq(Object.keys(rs.roles).sort().join(","), "camera,telescope");
  eq(rs.roles.camera.driver_id, "alpaca-ab12");
  eq(rs.roles.camera.backend, "native");
  eq(rs.roles.camera.dev_num, 1);
  eq((rs.roles.camera.extra as { name?: string }).name, "ASI220MM");
  eq(rs.roles.telescope.backend, "sim");
  eq(rs.roles.telescope.driver_id, undefined);
});

test("hasRealMotion gates on non-sim motion roles", () => {
  eq(hasRealMotion({ telescope: { driverId: "sim" } }, [sim]), false);
  eq(hasRealMotion({ focuser: { driverId: "alpaca-ab12" } }, [alpaca]), true);
  eq(hasRealMotion({ camera: { driverId: "alpaca-ab12" } }, [alpaca]), false);
  // a removed driver can't drive motion — no hold-confirm needed
  eq(hasRealMotion({ telescope: { driverId: "alpaca-gone" } }, [alpaca]), false);
});

test("hasRealMotion counts a real rotator", () => {
  eq(hasRealMotion({ rotator: { driverId: "alpaca-ab12" } }, [alpaca]), true);
  eq(hasRealMotion({ rotator: { driverId: "sim" } }, [sim]), false);
});

test("eligibleTaskDrivers applies the one rule (task edition)", () => {
  const mk = (id: string, tasks: string[], opts?: { enabled?: boolean; reachable?: boolean }) =>
    ({
      id,
      type: id.split("-")[0],
      label: id,
      enabled: opts?.enabled ?? true,
      implicit: !id.includes("-"),
      status: { reachable: opts?.reachable ?? true, error: null, detail: null, probed_at: 0 },
      offers: { devices: [], tasks },
    }) as unknown as DriverInfo;

  const drivers = [
    mk("nina-a1b2", ["autofocus", "polar_align"]),
    mk("astrodeck", ["autofocus", "polar_align"]),
    mk("astap", ["solve"]),
    mk("sim", ["polar_align", "solve"]),
    mk("nina-dead", ["autofocus"], { reachable: false }),
    mk("nina-off", ["autofocus"], { enabled: false }),
  ];

  eq(
    eligibleTaskDrivers("autofocus", drivers).map((d) => d.id).join(","),
    ["nina-a1b2", "astrodeck"].join(","),
  );
  eq(
    eligibleTaskDrivers("solve", drivers).map((d) => d.id).join(","),
    ["astap", "sim"].join(","),
  );
  eq(
    eligibleTaskDrivers("polar_align", drivers).map((d) => d.id).join(","),
    ["nina-a1b2", "astrodeck", "sim"].join(","),
  );
});

test("simAssignments matches pickDriver('sim')'s shape for every role (sim-connect desync root-cause fix)", () => {
  const map = simAssignments(["camera", "telescope"], [alpaca, sim]);
  eq(map.camera?.driverId, "sim");
  eq(map.camera?.name, "Simulated camera");
  eq(map.telescope?.name, "Simulated telescope");
  // slotState reads this exactly like a hand-picked assignment: ASSIGNED, not
  // driver-removed/unassigned — the dropdown, Connect Rig count, and (once
  // connected via the RigSpec path) Link Status all agree.
  eq(slotState("camera", map.camera ?? null, [alpaca, sim]), "ok");
  eq(buildRigSpec(map).roles.camera.backend, "sim");
});

test("simAssignments degrades to a bare sim id when the driver row is missing", () => {
  const map = simAssignments(["camera"], []);
  eq(map.camera?.driverId, "sim");
  eq(map.camera?.name, undefined);
});

console.log(`equipment.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
