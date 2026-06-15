// Self-contained regression tests for FIX-D (frontend store) — same inline-assert
// harness as src/lib/__tests__/foundation.test.ts (no vitest/jest wired in yet).
//
// Run directly:  npx tsx src/__tests__/store.test.ts
// Compiles under `tsc -b`; each test() maps 1:1 to an it() when a runner lands.
//
// The store module reads localStorage at import time (loadPlan + night check),
// so we install a minimal in-memory localStorage stub BEFORE importing it.

// ------------------------------------------------------------ browser stubs
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null {
    return this.m.has(k) ? (this.m.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.m.set(k, String(v));
  }
  removeItem(k: string): void {
    this.m.delete(k);
  }
  clear(): void {
    this.m.clear();
  }
}
const g = globalThis as unknown as { localStorage?: Storage };
if (typeof g.localStorage === "undefined") {
  g.localStorage = new MemStorage() as unknown as Storage;
}

// Import AFTER the stub is installed so the store's top-level loadPlan() succeeds.
const { useStore } = await import("../store");
import type { RigStatus, SiteInfo } from "../types";

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
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

// --------------------------------------------------------- P2-7 default plan
// The store's defaultPlan() is now the single source of truth for the seeded
// sequence plan; the reconciled values must match SequenceView's old intent.
test("P2-7: defaultPlan reconciled values (Tonight/guide/cool_to/flip/dither)", () => {
  // Fresh store with no persisted plan → seeded from defaultPlan().
  localStorage.removeItem("astrodeck-plan");
  const plan = useStore.getState().plan;
  eq(plan.name, "Tonight", "name");
  eq(plan.guide, true, "guide");
  eq(plan.cool_to, -10, "cool_to");
  eq(plan.meridian_flip, true, "meridian_flip");
  eq(plan.dither_pixels, 3, "dither_pixels");
});

// --------------------------------------------------------- P2-7 setPlan SSOT
test("P2-7: setPlan persists to the single astrodeck-plan key and updates store", () => {
  const next = { ...useStore.getState().plan, name: "Custom run" };
  useStore.getState().setPlan(next);
  eq(useStore.getState().plan.name, "Custom run", "store plan");
  const raw = localStorage.getItem("astrodeck-plan");
  assert(!!raw && JSON.parse(raw).name === "Custom run", "persisted to astrodeck-plan");
});

// ----------------------------------------------------- P2-6 status site refresh
// poll_status carries the full 6-field site; the status handler must refresh
// store.site (is_default/horizon_min_deg) live, not only on hello.
test("P2-6: status event refreshes store.site (is_default/horizon_min_deg)", () => {
  const site = {
    name: "Backyard",
    latitude: 51.5,
    longitude: -0.13,
    elevation_m: 40,
    is_default: false,
    horizon_min_deg: 22,
  };
  const status: Partial<RigStatus> = { connected: {}, looping: false, mode: "alpaca", site };
  useStore.getState().handleEvent({ type: "status", data: status as unknown as Record<string, unknown>, ts: 0 });
  const s = useStore.getState().site;
  assert(!!s, "site set");
  eq(s!.is_default, false, "is_default");
  eq(s!.horizon_min_deg, 22, "horizon_min_deg");
});

test("P2-6: status without a site does NOT clobber an existing store.site", () => {
  const seed: SiteInfo = { latitude: 10, longitude: 20, is_default: true, horizon_min_deg: 15 };
  useStore.getState().setSite(seed);
  const status: Partial<RigStatus> = { connected: {}, looping: false, mode: "sim" };
  useStore.getState().handleEvent({ type: "status", data: status as unknown as Record<string, unknown>, ts: 0 });
  const s = useStore.getState().site;
  assert(!!s, "site retained");
  eq(s!.latitude, 10, "latitude retained");
  eq(s!.horizon_min_deg, 15, "horizon_min_deg retained");
});

// ------------------------------------------------------- P3-6 hello unwrapped
// Backend sends {"data": hub.summary()} (no inner summary wrapper); the hello
// handler must read data directly.
test("P3-6: hello reads site/mode directly from data (no summary wrapper)", () => {
  const site: SiteInfo = { latitude: 33, longitude: -111, is_default: false, horizon_min_deg: 18 };
  useStore.getState().handleEvent({
    type: "hello",
    data: { site, mode: "nina" } as unknown as Record<string, unknown>,
    ts: 0,
  });
  const st = useStore.getState();
  eq(st.site!.latitude, 33, "hello site");
  eq(st.equipConnected, true, "hello mode → equipConnected");
});

// ------------------------------------------- P3-4 dead compat toast field gone
test("P3-4: store has no backward-compat `toast` mirror field", () => {
  const st = useStore.getState() as unknown as Record<string, unknown>;
  assert(!("toast" in st), "`toast` field removed");
  // The real queue still works.
  useStore.getState().enqueueToast({ level: "error", title: "Boom" });
  assert(useStore.getState().toasts.length > 0, "enqueue still populates toasts");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nstore.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
