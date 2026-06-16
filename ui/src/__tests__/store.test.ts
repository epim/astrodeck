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
const g = globalThis as unknown as {
  localStorage?: Storage;
  document?: unknown;
};
if (typeof g.localStorage === "undefined") {
  g.localStorage = new MemStorage() as unknown as Storage;
}

// The store touches `document` at import time (applyTouchSizing on the root class +
// applyBrightnessVars on the root style). Install a minimal stub so the module
// loads under tsx/node where there is no DOM — mirrors the localStorage stub above.
if (typeof g.document === "undefined") {
  const classList = {
    toggle(_c: string, _on?: boolean): void {},
    add(_c: string): void {},
    remove(_c: string): void {},
    contains(_c: string): boolean {
      return false;
    },
  };
  const style = {
    setProperty(_k: string, _v: string): void {},
    getPropertyValue(_k: string): string {
      return "";
    },
  };
  g.document = { documentElement: { classList, style } };
}

// Import AFTER the stub is installed so the store's top-level loadPlan() succeeds.
const { useStore } = await import("../store");
import type { PreviewInfo, RigStatus, SiteInfo } from "../types";

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

// ====================================================================
// BATCH-2 (lane 2A) — live-preview ring + monitor slices
// ====================================================================
function mkPreview(id: number): PreviewInfo {
  return {
    id,
    stats: { min: 0, max: 100, mean: 10, median: 8, std: 5 },
    histogram: [],
    histogram_domain: "display",
    exposure_s: 2,
    gain: 100,
    binning: 1,
    data_width: 1000,
    data_height: 800,
    display_width: 1000,
    display_height: 800,
    mime: "image/jpeg",
    source: "sim",
    is_stretched: false,
    data_is_linear: true,
    has_lossless: true,
    full_well: 65535,
    auto_levels: { black: 0, mid: 0.5, white: 1 },
    ts: id,
  };
}

// --------------------------------- live-preview: pushPreview ring (cap 24)
test("preview: pushPreview appends, caps at 24, tracks livePreviewId", () => {
  // reset ring
  useStore.setState({ previews: [], selectedPreviewId: null, livePreviewId: null });
  for (let i = 1; i <= 30; i++) useStore.getState().pushPreview(mkPreview(i));
  const s = useStore.getState();
  eq(s.previews.length, 24, "capped at 24");
  eq(s.previews[0].id, 7, "oldest trimmed (30-24+1=7)");
  eq(s.previews[s.previews.length - 1].id, 30, "newest last");
  eq(s.livePreviewId, 30, "livePreviewId follows newest");
});

// --------------------------------- live-preview: "preview" event -> pushPreview + liveness
test("preview: 'preview' event pushes ring, sets preview + lastFrameAtMs", () => {
  useStore.setState({ previews: [], preview: null, livePreviewId: null, lastFrameAtMs: null });
  const before = Date.now();
  useStore.getState().handleEvent({
    type: "preview",
    data: mkPreview(42) as unknown as Record<string, unknown>,
    ts: 0,
  });
  const s = useStore.getState();
  eq(s.preview!.id, 42, "single preview set");
  eq(s.previews.length, 1, "ring received frame");
  eq(s.livePreviewId, 42, "livePreviewId set");
  assert(s.lastFrameAtMs !== null && s.lastFrameAtMs >= before, "lastFrameAtMs stamped");
});

// --------------------------------- live-preview: pinning is sticky
test("preview: selectPreview pins; new live frame does not move the pin", () => {
  useStore.setState({ previews: [], selectedPreviewId: null, livePreviewId: null });
  useStore.getState().pushPreview(mkPreview(1));
  useStore.getState().selectPreview(1);
  useStore.getState().pushPreview(mkPreview(2)); // new live arrival
  const s = useStore.getState();
  eq(s.selectedPreviewId, 1, "pin held");
  eq(s.livePreviewId, 2, "live advanced underneath");
});

// --------------------------------- live-preview: persistence of toggles only
test("preview: setStretch persists auto/advancedOpen only, never absolute levels", () => {
  localStorage.removeItem("astrodeck-preview");
  useStore.getState().setStretch({ auto: false, advancedOpen: true, black: 0.3, mid: 0.7 });
  const raw = localStorage.getItem("astrodeck-preview");
  assert(!!raw, "persisted");
  const p = JSON.parse(raw as string);
  eq(p.auto, false, "auto persisted");
  eq(p.advancedOpen, true, "advancedOpen persisted");
  assert(!("black" in p) && p.stretch === undefined, "absolute levels NOT persisted");
  // store still holds the in-memory absolute levels for the session
  eq(useStore.getState().stretch.black, 0.3, "black kept in-memory");
});

// --------------------------------- P2-8: persistPreview no-op on unchanged subset
// Dragging a B/M/W handle fires setStretch dozens/sec but never changes the
// persisted subset {auto, advancedOpen, overlays}; persistPreview must skip the
// localStorage.setItem on those moves (only write when the subset actually
// changes).
test("P2-8: setStretch B/M/W drag does not re-write localStorage; toggle change does", () => {
  // Establish a known persisted baseline (auto:false, advancedOpen:false).
  useStore.getState().setStretch({ auto: false, advancedOpen: false });

  // Count setItem calls hitting the preview key.
  const store = globalThis.localStorage as unknown as {
    setItem: (k: string, v: string) => void;
  };
  const orig = store.setItem.bind(store);
  let previewWrites = 0;
  store.setItem = (k: string, v: string) => {
    if (k === "astrodeck-preview") previewWrites++;
    orig(k, v);
  };
  try {
    // Pure B/M/W/brightness drag — persisted subset unchanged → zero writes.
    useStore.getState().setStretch({ black: 0.1 });
    useStore.getState().setStretch({ black: 0.2, mid: 0.6 });
    useStore.getState().setStretch({ white: 0.9, brightness: 0.3 });
    eq(previewWrites, 0, "no writes during B/M/W drag");

    // A real toggle change must still persist.
    useStore.getState().setStretch({ advancedOpen: true });
    eq(previewWrites, 1, "toggle change writes once");

    // Re-applying the same toggle value is also a no-op.
    useStore.getState().setStretch({ advancedOpen: true });
    eq(previewWrites, 1, "idempotent toggle write skipped");
  } finally {
    store.setItem = orig;
  }
});

// --------------------------------- monitor: guide event stamps lastGuideAtMs
test("monitor: 'guide' event stamps lastGuideAtMs", () => {
  useStore.setState({ lastGuideAtMs: null });
  const before = Date.now();
  useStore.getState().handleEvent({
    type: "guide",
    data: { guiding: true, rms_ra: 0.5, rms_dec: 0.4, rms_total: 0.6, snr: 30, recent: [] } as unknown as Record<string, unknown>,
    ts: 0,
  });
  const t = useStore.getState().lastGuideAtMs;
  assert(t !== null && t >= before, "lastGuideAtMs stamped");
});

// --------------------------------- monitor: runBanner rising edge + clear
test("monitor: sequence rising-edge raises runBanner; terminal clears it", () => {
  useStore.setState({ sequence: { state: "idle" }, runBanner: null, view: "capture", autoMonitor: false });
  // idle -> running raises the banner
  useStore.getState().handleEvent({
    type: "sequence",
    data: { state: "running", plan_name: "Tonight", progress: { frames_done: 0, frames_total: 10, percent: 0, elapsed_s: 0, rejected: 0 } } as unknown as Record<string, unknown>,
    ts: 0,
  });
  let s = useStore.getState();
  assert(!!s.runBanner && s.runBanner.active, "banner active on rising edge");
  eq(s.runBanner!.plan_name, "Tonight", "plan_name carried");
  // progress updates percent
  useStore.getState().handleEvent({
    type: "sequence",
    data: { state: "running", plan_name: "Tonight", progress: { frames_done: 5, frames_total: 10, percent: 50, elapsed_s: 100, rejected: 0 } } as unknown as Record<string, unknown>,
    ts: 0,
  });
  eq(useStore.getState().runBanner!.percent, 50, "percent updated");
  // complete clears the banner
  useStore.getState().handleEvent({
    type: "sequence",
    data: { state: "complete", progress: { frames_done: 10, frames_total: 10, percent: 100, elapsed_s: 200, rejected: 0 } } as unknown as Record<string, unknown>,
    ts: 0,
  });
  s = useStore.getState();
  eq(s.runBanner, null, "banner cleared on complete");
  eq(s.view, "capture", "no forced redirect (autoMonitor off, not on connect)");
});

// --------------------------------- monitor: guarded auto-select only from connect+pref
test("monitor: auto-select to monitor only when autoMonitor on AND view is connect", () => {
  // pref off → no switch even from connect
  useStore.setState({ sequence: { state: "idle" }, view: "connect", autoMonitor: false, runBanner: null });
  useStore.getState().handleEvent({
    type: "sequence",
    data: { state: "running", plan_name: "P" } as unknown as Record<string, unknown>,
    ts: 0,
  });
  eq(useStore.getState().view, "connect", "no switch when pref off");

  // pref on + on connect → switches to monitor on rising edge
  useStore.setState({ sequence: { state: "idle" }, view: "connect", autoMonitor: true, runBanner: null });
  useStore.getState().handleEvent({
    type: "sequence",
    data: { state: "running", plan_name: "P" } as unknown as Record<string, unknown>,
    ts: 0,
  });
  eq(useStore.getState().view, "monitor", "switches to monitor");

  // pref on but mid-workflow (view=mount) → never hijacks
  useStore.setState({ sequence: { state: "idle" }, view: "mount", autoMonitor: true, runBanner: null });
  useStore.getState().handleEvent({
    type: "sequence",
    data: { state: "running", plan_name: "P" } as unknown as Record<string, unknown>,
    ts: 0,
  });
  eq(useStore.getState().view, "mount", "no hijack mid-workflow");
});

// --------------------------------- monitor: setAutoMonitor persists
test("monitor: setAutoMonitor persists to localStorage", () => {
  useStore.getState().setAutoMonitor(true);
  eq(localStorage.getItem("astrodeck-monitor-auto"), "1", "persisted on");
  eq(useStore.getState().autoMonitor, true, "store updated");
  useStore.getState().setAutoMonitor(false);
  eq(localStorage.getItem("astrodeck-monitor-auto"), "0", "persisted off");
});

// ====================================================================
// BATCH-3 (lane FIX-C) — safety/regression tests
// ====================================================================

// --------------------------------- F-B2: lockAvailable enabled from init
// The screen-lock / TouchGuard feature is gated on `lockAvailable`. Its unblock
// precondition (the reliability sequence-error render) shipped in Batch 1, so the
// flag MUST initialize true — otherwise the lock UI is permanently inert and
// setLocked(true)'s /api/mount/stop backstop can never fire from the lock control.
test("F-B2: lockAvailable initializes true (screen-lock feature enabled)", () => {
  eq(useStore.getState().lockAvailable, true, "lockAvailable true at init");
});

// --------------------------------- F-D1: pushConfirm resolves a stale pending false
// Only one confirm can be live at a time. If a new confirm arrives while one is
// pending, the stale one MUST resolve false (cancel) so its awaiter never hangs —
// a leaked pending promise on a GOTO/abort guard would wedge that call site forever.
test("F-D1: pushConfirm resolves the previous pending confirm false before replacing", async () => {
  useStore.setState({ confirm: null });
  const firstResolved: { value: boolean | "pending" } = { value: "pending" };
  // First request — left pending (no user response).
  const p1 = useStore.getState().pushConfirm({ title: "First" });
  void p1.then((ok) => {
    firstResolved.value = ok;
  });
  // Second request arrives before the first is answered.
  const p2 = useStore.getState().pushConfirm({ title: "Second" });
  void p2.then(() => {});
  // Let the microtask for p1's resolution flush.
  await Promise.resolve();
  assert(firstResolved.value === false, "stale pending confirm resolved false");
  // The live confirm is now the second request.
  eq(useStore.getState().confirm?.title, "Second", "second confirm is live");
  // Cleanup: answer the live one.
  useStore.getState().resolveConfirm(true);
  eq(await p2, true, "second confirm resolves on user response");
});

// --------------------------------- F-dimmer: store-owned dimmer single source
// Day/night brightness lives in the store; setBrightness writes the ACTIVE mode's
// value + persists; resetBrightness returns the active mode to 1.0. (CSS-var
// application is exercised by the app at runtime; here we assert the state + the
// persisted localStorage key, the parts the test harness can observe.)
test("F-dimmer: setBrightness/resetBrightness write the active mode's slice + persist", () => {
  // Day mode.
  useStore.setState({ night: false });
  useStore.getState().setBrightness(0.5);
  eq(useStore.getState().brightDay, 0.5, "day brightness set");
  eq(localStorage.getItem("astrodeck-bright-day"), "0.5", "day persisted");

  // Clamp floor (0.08).
  useStore.getState().setBrightness(0.01);
  eq(useStore.getState().brightDay, 0.08, "clamped to floor 0.08");

  // Night mode is a separate memory.
  useStore.setState({ night: true });
  useStore.getState().setBrightness(0.3);
  eq(useStore.getState().brightNight, 0.3, "night brightness set");
  eq(useStore.getState().brightDay, 0.08, "day memory untouched by night change");

  // Reset hatch returns the ACTIVE mode to 1.0.
  useStore.getState().resetBrightness();
  eq(useStore.getState().brightNight, 1, "reset night → 1");
  eq(localStorage.getItem("astrodeck-bright-night"), "1", "reset persisted");
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
