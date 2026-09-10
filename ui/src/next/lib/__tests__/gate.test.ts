// Pure-lib test for gate.ts. Sabotage check: reordering the priority checks
// (e.g. testing cap before link) turns "link down beats a cap block" red;
// dropping the roleLabel translation table turns the telescope/switch/
// filterwheel mapping tests red; hardcoding the busy-lane sentence instead of
// deferring to lib/humanize.ts turns "known lane uses the humanize sentence" red.
//
// gate.ts is pure, but it imports ui/src/lib/caps.ts (ARCHITECTURE.md #8:
// "using lib/caps helpers"), and caps.ts imports the real zustand store,
// which reads `window.location` at MODULE LOAD (lib/base.ts). So — same
// convention as lib/__tests__/authGate.test.ts — install minimal browser
// stubs on globalThis BEFORE anything imports the store, then dynamic-
// `import()` the runtime values (type-only imports are erased, so they stay
// static).
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

const { lockReason, roleLabel } = await import("../gate");
const { humanizeLaneConflict } = await import("../../../lib/humanize");
import type { GateInput, GateStoreSlice } from "../gate";
import type { Principal } from "../../../types";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = ""): void { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

function viewer(): Principal { return { role: "viewer", email: null, caps: [] }; }
function admin(): Principal { return { role: "admin", email: "a@x.test", caps: ["view.status", "control.power", "control.mount"] }; }

function slice(over: Partial<GateStoreSlice> = {}): GateStoreSlice {
  return {
    principal: viewer(),
    status: null,
    equipConnected: false,
    wsPhase: "up",
    ...over,
  };
}

test("unlocked when nothing in the input is blocked", () => {
  eq(lockReason({}, slice()), null);
});

test("link down: \"the rig is not reachable\", and it wins over every other reason (priority order)", () => {
  const inp: GateInput = { cap: "control.power", needsRole: "camera", busyLane: "solve", extra: "a flow owns the mount" };
  eq(lockReason(inp, slice({ wsPhase: "down", principal: viewer() })), "the rig is not reachable");
  eq(lockReason(inp, slice({ wsPhase: "reconnecting" })), "the rig is not reachable");
});

test("cap: \"needs <accessPhrase>\" when the principal lacks it, and it beats role/busy/extra", () => {
  const inp: GateInput = { cap: "control.power", needsRole: "camera", busyLane: "solve" };
  eq(lockReason(inp, slice({ principal: viewer() })), "needs admin access");
});

test("cap: null (unlocked by the cap check) once the principal holds it", () => {
  const inp: GateInput = { cap: "control.power" };
  eq(lockReason(inp, slice({ principal: admin() })), null);
});

test("role: telescope maps to \"mount\", switch to \"power switch\", filterwheel to \"filter wheel\"", () => {
  eq(roleLabel("telescope"), "mount");
  eq(roleLabel("switch"), "power switch");
  eq(roleLabel("filterwheel"), "filter wheel");
  eq(roleLabel("camera"), "camera", "unmapped roles pass through unchanged");
  eq(roleLabel("guider"), "guider");
  eq(roleLabel("focuser"), "focuser");
  eq(roleLabel("rotator"), "rotator");
});

test("role: \"connect a <role> first\" when the role is not connected, via lib/caps.ts's equipConnected fallback", () => {
  eq(
    lockReason({ needsRole: "telescope" }, slice({ status: null, equipConnected: false })),
    "connect a mount first",
  );
  eq(
    lockReason({ needsRole: "switch" }, slice({ status: null, equipConnected: false })),
    "connect a power switch first",
  );
});

test("role: connected (via equipConnected) unlocks the role check", () => {
  eq(lockReason({ needsRole: "telescope" }, slice({ status: null, equipConnected: true })), null);
});

test("role: a mapped backend_links entry overrides the equipConnected fallback", () => {
  const withLink = slice({
    status: { connected: {}, looping: false, backend_links: [{ role: "telescope", connected: true, error: null }] } as unknown as GateStoreSlice["status"],
    equipConnected: false,
  });
  eq(lockReason({ needsRole: "telescope" }, withLink), null);
});

test("busy lane: a lane lib/humanize.ts knows uses that exact sentence, not a made-up one", () => {
  const expected = humanizeLaneConflict("'solve' is already running");
  eq(lockReason({ busyLane: "solve" }, slice()), expected);
});

test("busy lane: an unmapped lane falls back to \"<lane> is running\"", () => {
  eq(lockReason({ busyLane: "some_future_lane" }, slice()), "some_future_lane is running");
});

test("extra: returned verbatim when nothing else blocks", () => {
  eq(lockReason({ extra: "a flow owns the mount" }, slice()), "a flow owns the mount");
});

test("extra: does not override link/cap/role/busy (lowest priority)", () => {
  eq(
    lockReason({ cap: "control.power", extra: "should not show" }, slice({ principal: viewer() })),
    "needs admin access",
  );
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export { passed, failed };
export const total = passed + failed;
