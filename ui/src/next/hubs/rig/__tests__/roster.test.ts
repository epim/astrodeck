// roster.test.ts - the device list and the rig summary, without a DOM.
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/roster.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH PINNING HERE, and why each one is a way the screen could look
// perfect and still lie:
//
//  1. A DOWN LINK IS NAMED AND EXPLAINED. FAILED with a red lamp and no
//     sentence is the exact defect `BackendLinkGrid` was fixed for, and that
//     grid is not on this screen any more - the row's third line is the only
//     place the reason now appears.
//  2. THE FALLBACK CHAIN. A rig that never went through the RigSpec path
//     publishes `status.connected` and NO `backend_links` (the live tablet, the
//     legacy connect routes). Reading only the links would grey out a rig that
//     is running.
//  3. NOTHING CONNECTED IS SEVEN ROWS, NOT ZERO. An empty device list reads as
//     "this app has no devices"; seven greyed rows read as "none of them are
//     here yet", which is true and is somewhere to press.
//  4. A ROTATOR WITH NO LINK IS STILL A ROTATOR. `status.rotator` alone earns a
//     row - and its absence earns none, because a rotator sheet on a rig that
//     has never had one is a promise nothing keeps.
//  5. THE SUMMARY DROPS WHAT IT CANNOT MEASURE. The prototype's line is four
//     claims a disconnected rig would happily print.

// roster.ts is pure, but it imports `lib/caps.ts` (for `resolveRoleConnected`,
// the ONE join of backend_links / connected / equipConnected) and
// `BackendLinkGrid` (for `linkReason`), and caps pulls in the real store, which
// reads `window.location` at MODULE LOAD (lib/base.ts). Same convention as
// `next/lib/__tests__/gate.test.ts`: install minimal browser stubs BEFORE
// anything imports the store, then dynamic-`import()` the runtime values.
// Copying those two helpers into this hub instead would be the drift they exist
// to prevent.
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null { return this.m.has(k) ? (this.m.get(k) as string) : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
}
const g = globalThis as unknown as { localStorage?: Storage; document?: unknown; window?: unknown };
if (typeof g.localStorage === "undefined") g.localStorage = new MemStorage() as unknown as Storage;
if (typeof g.document === "undefined") {
  const classList = { toggle() {}, add() {}, remove() {}, contains() { return false; } };
  const style = { setProperty() {}, getPropertyValue() { return ""; } };
  g.document = { documentElement: { classList, style } };
}
if (typeof g.window === "undefined") {
  g.window = {
    location: { pathname: "/", protocol: "http:", host: "test" },
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
    setInterval: globalThis.setInterval.bind(globalThis),
    clearInterval: globalThis.clearInterval.bind(globalThis),
    addEventListener() {},
    removeEventListener() {},
  };
}

const { deviceRows, rigSummary } = await import("../devices/roster");
import type { RosterInput } from "../devices/roster";
import type { BackendLink, RigStatus, SafetyState } from "../../../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

function link(role: string, over: Partial<BackendLink> = {}): BackendLink {
  return { role, ok: true, error: null, attempted: true, connected: true, ...over };
}

function input(over: Partial<RosterInput> = {}): RosterInput {
  return {
    connected: undefined,
    links: [],
    equipConnected: false,
    status: null,
    safety: null,
    dome: null,
    sequenceRunning: false,
    ...over,
  };
}

const SAFE: SafetyState = {
  connected: true,
  reading: { is_safe: true, reason: "", source: "Alpaca safety monitor", stale: false, ts: 0 },
  streak: 0,
};

// ------------------------------------------------------ 1. a down link talks

test("a failed link is FAILED, red, and carries the backend's own reason", () => {
  const rows = deviceRows(input({
    links: [link("camera", { ok: false, connected: false, error: "USB device not found" })],
    status: { connected: {} } as unknown as RigStatus,
  }));
  const cam = rows.find((r) => r.role === "camera");
  assert(cam != null, "no camera row for a role the server reported a link for");
  eq(cam!.state, "FAILED", "state:");
  eq(cam!.led, "bad", "led:");
  eq(cam!.reason, "USB device not found", "reason:");
});

test("a failed link with NO reason still gets a sentence, never a bare colour", () => {
  const rows = deviceRows(input({
    links: [link("focuser", { ok: false, connected: false, error: null })],
  }));
  const f = rows.find((r) => r.role === "focuser")!;
  eq(f.state, "FAILED", "state:");
  assert(f.reason != null && f.reason.length > 0,
    "an alarm word with no stated cause is the defect, not the fix");
});

test("a degraded link is DEGRADED and amber, not failed and not fine", () => {
  const rows = deviceRows(input({
    // ok=true (it attached at connect) but connected=false now.
    links: [link("guider", { connected: false })],
  }));
  const g = rows.find((r) => r.role === "guider")!;
  eq(g.state, "DEGRADED", "state:");
  eq(g.led, "warn", "led:");
  assert(g.reason != null, "a degraded row with no reason");
});

test("a link that was never attempted reads NOT CONNECTED, not FAILED", () => {
  const rows = deviceRows(input({
    links: [link("switch", { attempted: false, connected: false, ok: false })],
  }));
  const p = rows.find((r) => r.role === "switch")!;
  eq(p.state, "NOT CONNECTED", "a role the connect never asked for is not a failure");
  eq(p.led, "off", "led:");
});

// ------------------------------------------------------- 2. the fallback chain

test("a device map with no backend_links still reads as connected", () => {
  const rows = deviceRows(input({
    connected: { telescope: { name: "AM5N", kind: "telescope", connected: true } },
    status: {
      connected: { telescope: { name: "AM5N", kind: "telescope", connected: true } },
      mount: { tracking: true, parked: false, slewing: false },
    } as unknown as RigStatus,
  }));
  const m = rows.find((r) => r.role === "telescope");
  assert(m != null, "no mount row for a device the server says is connected");
  eq(m!.state, "TRACKING", "state:");
  eq(m!.led, "on", "led:");
  assert(/AM5N/.test(m!.driverLine), `the driver line lost the device name (${m!.driverLine})`);
  assert(/tracking sidereal/.test(m!.driverLine),
    `the driver line lost the live clause (${m!.driverLine})`);
});

test("a clause with no evidence is dropped, never defaulted", () => {
  const rows = deviceRows(input({
    connected: { camera: { name: "ASI2600MM", kind: "camera", connected: true } },
    // No `status.camera` at all: no temperature has been reported.
    status: {
      connected: { camera: { name: "ASI2600MM", kind: "camera", connected: true } },
    } as unknown as RigStatus,
  }));
  const c = rows.find((r) => r.role === "camera")!;
  eq(c.driverLine, "ASI2600MM", "an unreported temperature was printed anyway");
  assert(!/°C/.test(c.driverLine), "the row invented a temperature");
});

// --------------------------------------------------- 3. nothing connected yet

test("nothing connected produces the seven greyed rows", () => {
  const rows = deviceRows(input());
  eq(rows.length, 7, "row count:");
  eq(rows.map((r) => r.role).join(","),
    "camera,telescope,focuser,filterwheel,guider,safety,switch",
    "the not-connected roster is not the design's seven, in the design's order");
  assert(rows.every((r) => r.state === "NOT CONNECTED" && r.led === "off"),
    "a row claimed a state on a rig with nothing on it");
  assert(rows.every((r) => r.sheet != null),
    "a greyed row with no sheet is a dead end - the sheets explain themselves");
});

// ------------------------------------------------------------- 4. the rotator

test("a status.rotator with no link still produces a ROTATOR row", () => {
  const rows = deviceRows(input({
    equipConnected: true,
    status: {
      connected: {},
      rotator: { name: "ZWO CAA", sky_deg: 41.25, mech_deg: 0, moving: false, synced: true, can_reverse: false, reverse: false },
    } as unknown as RigStatus,
  }));
  const r = rows.find((x) => x.role === "rotator");
  assert(r != null, "a rotator the rig is reporting had no row");
  eq(r!.sheet, "rotator", "the rotator row does not open the rotator sheet");
  assert(/PA 41\.3/.test(r!.driverLine), `the rotator row lost its PA (${r!.driverLine})`);
});

test("a rig that has never had a rotator is offered no rotator row", () => {
  const rows = deviceRows(input({ links: [link("camera")] }));
  assert(!rows.some((r) => r.role === "rotator"),
    "a rotator sheet was offered for a rotator that does not exist");
});

test("a role this build has no sheet for is still listed, with no chevron target", () => {
  const rows = deviceRows(input({
    links: [link("camera"), link("guide_camera", { connected: false, ok: false, error: "no camera" })],
  }));
  const gc = rows.find((r) => r.role === "guide_camera");
  assert(gc != null, "a role the server reported was dropped, and with it its error");
  eq(gc!.sheet, null, "an unbuilt sheet must not be offered as pressable");
  eq(gc!.reason, "no camera", "the reason a dropped row would have taken with it");
});

// ------------------------------------------------------------- 5. the summary

test("the summary names the profile only when there is one", () => {
  eq(rigSummary(input(), "Backyard").text, "not connected · Backyard ready", "with a profile:");
  eq(rigSummary(input(), null).text, "not connected", "with none:");
});

test("the summary is built clause by clause from evidence", () => {
  const s = rigSummary(input({
    equipConnected: true,
    links: [link("camera"), link("telescope"), link("filterwheel"), link("safety")],
    status: {
      connected: {},
      camera: { temperature: -10, can_cool: true, cooler: { on: true, at_target: true, target_c: -10, power: 62, can_report_power: true } },
      mount: { tracking: true, parked: false, slewing: false },
      filterwheel: { position: 0, names: ["L", "R", "G", "B", "Ha", "OIII", "SII"] },
    } as unknown as RigStatus,
    safety: SAFE,
  }), "Backyard");
  eq(s.text, "cooled -10.0°C · tracking · 7 filters · safe", "summary:");
  eq(s.tone, "good", "tone:");
});

test("an unsafe reading turns the whole line bad and says why", () => {
  const s = rigSummary(input({
    equipConnected: true,
    links: [link("safety")],
    status: { connected: {} } as unknown as RigStatus,
    safety: {
      connected: true, streak: 3,
      reading: { is_safe: false, reason: "rain", source: "monitor", stale: false, ts: 0 },
    },
  }), null);
  eq(s.tone, "bad", "tone:");
  assert(/UNSAFE - rain/.test(s.text), `the line does not say what is unsafe (${s.text})`);
});

test("a parked mount and a warm camera are warnings, not failures", () => {
  const s = rigSummary(input({
    equipConnected: true,
    links: [link("camera"), link("telescope")],
    status: {
      connected: {},
      camera: { temperature: 8.4, can_cool: true, cooler: { on: false, at_target: false, target_c: -10, power: 0, can_report_power: true } },
      mount: { tracking: false, parked: true, slewing: false },
    } as unknown as RigStatus,
  }), null);
  eq(s.tone, "warn", "tone:");
  eq(s.text, "cooler off · parked", "summary:");
});

const total = passed + failed;
console.log(`roster.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
