// authGate.test.ts — #117: the console must not describe the observatory to
// someone who is looking at the login screen.
//
// The reported failure was a modal reading "High cloud forecast tonight —
// Forecast peak N% total cloud … at/above your N% threshold" rendered OVER the
// sign-in form. Three claims are pinned here, in the order they matter:
//   1. an alert edge while the gate is up raises no dialog,
//   2. once the gate engages the store is holding nothing that describes the
//      rig — the load-bearing half, because the dialog was only the first
//      consumer of that state and will not be the last,
//   3. a normal, signed-in alert still fires (the feature is not "fixed" by
//      being removed).
//
// Run: npx tsx src/lib/__tests__/authGate.test.ts
// No test runner is wired into this UI (build is `tsc -b && vite build`), so
// this uses the inline-assert idiom the other lane-1 tests use, and compiles
// under `tsc -b` so the build gate covers it too.

// The store reads localStorage / `document` / `window.location` at import time,
// so install the same minimal browser stubs caps.test.ts uses BEFORE importing
// it. authGate.ts itself needs none of this (it is store-free and React-free) —
// the stubs exist purely so the store-level claims above can be made against the
// REAL store rather than a mock of it.
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null { return this.m.has(k) ? (this.m.get(k) as string) : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
}
const g = globalThis as unknown as {
  localStorage?: Storage; document?: unknown; window?: unknown;
};
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

const {
  clearedRigState,
  dialogBlockedReason,
  dialogsBlocked,
  gateEngaged,
  RIG_STATE_KEYS,
} = await import("../authGate");
const { useStore } = await import("../../store");
import type { AuthGate } from "../authGate";
import type { WeatherState } from "../../types";

// The cold-boot value of every rig slice, captured before this file touches the
// store. Test D compares the CLEARED state against it.
const COLD_BOOT = useStore.getState();
const coldBootRig: Record<string, string> = {};
for (const k of RIG_STATE_KEYS) {
  coldBootRig[k] = JSON.stringify((COLD_BOOT as unknown as Record<string, unknown>)[k]);
}

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void | Promise<void>): void {
  try {
    const r = fn();
    if (r instanceof Promise) throw new Error("use testAsync for async cases");
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try {
    await fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(got: T, want: T, msg: string): void {
  assert(got === want, `${msg} (got ${JSON.stringify(got)}, want ${JSON.stringify(want)})`);
}

// --------------------------------------------------------------- fixtures
// A weather payload shaped like the one the field report showed: a fresh fetch
// carrying a high-cloud alert and the operator's own threshold. Coordinates are
// obvious placeholders — the point of the test is that NONE of this reaches an
// unauthenticated viewer.
function alertingWeather(): WeatherState {
  return {
    enabled: true,
    fetched_ts: Date.now() / 1000,
    stale: false,
    ignore_tonight: false,
    threshold_pct: 30,
    sustain_minutes: 30,
    site_lat: 12.34,
    site_lon: -56.78,
    forecast: {
      times: ["2026-08-01T22:00", "2026-08-01T23:00"],
      cloud: [70, 80],
      cloud_low: [5, 5],
      cloud_mid: [10, 10],
      cloud_high: [70, 80],
    },
    astrospheric: null,
    alert: {
      kind: "high_cloud",
      start_iso: "2026-08-01T22:00",
      end_iso: "2026-08-02T03:00",
      peak_pct: 80,
      dominant_layer: "high",
    },
  };
}

/** Feed a weather frame the way the WS does — the only path that bumps the
 *  once-per-night alert latch (store §9). */
function deliverWeatherAlert(): void {
  useStore.getState().handleEvent({
    type: "weather",
    data: alertingWeather() as unknown as Record<string, unknown>,
    ts: Date.now() / 1000,
  });
}

/** What App's weather-alert effect does, minus React (there is no DOM test
 *  runner here). Kept in the same shape as App.tsx: the early return on the
 *  gate, then the confirmDialog() call — which is store.pushConfirm(). Returns
 *  the promise the effect would have fired, or null when it declined to fire. */
function weatherAlertEffect(): Promise<boolean> | null {
  const st = useStore.getState();
  if (st.weatherAlertKey === 0) return null;
  if (dialogsBlocked(st.authGate)) return null;
  const w = st.weather;
  if (!w?.alert) return null;
  return st.pushConfirm({ title: "High cloud forecast tonight", mode: "ok" });
}

/** Populate the store the way a live night does, so the clear has something
 *  real to drop. */
function populateRig(): void {
  const st = useStore.getState();
  st.handleEvent({
    type: "status",
    data: {
      mode: "zwo-usb",
      site: { name: "Test Site", latitude: 12.34, longitude: -56.78, is_default: false, horizon_min_deg: 20 },
      mount: { tracking: true, parked: false, slewing: false, ra_str: "01:02:03", dec_str: "+40:00:00", alt: 62 },
    } as unknown as Record<string, unknown>,
    ts: Date.now() / 1000,
  });
  st.handleEvent({
    type: "preview",
    data: { id: 7, url: "/api/preview/7.jpg" } as unknown as Record<string, unknown>,
    ts: Date.now() / 1000,
  });
  st.handleEvent({
    type: "log",
    data: { level: "error", source: "mount", message: "guiding lost" },
    ts: Date.now() / 1000,
  });
  deliverWeatherAlert();
  useStore.setState({
    config: { site: { name: "Test Site", is_default: false, horizon_min_deg: 20 } } as never,
    lastReportId: "sess-2026-08-01",
    framing: { center: { ra_hours: 1, dec_deg: 40 } } as never,
  });
}

// ============================================================ A. pure module
test("dialogsBlocked: only the operational console may speak", () => {
  eq(dialogsBlocked("open"), false, "open");
  eq(dialogsBlocked("login"), true, "login");
  // The pre-decision splash counts too: a method is enabled and we do not yet
  // know who is looking, and "we don't know" is not "anyone".
  eq(dialogsBlocked("resolving"), true, "resolving");
});

test("a blocked dialog can say what blocked it", () => {
  assert((dialogBlockedReason("login") ?? "").includes("sign-in screen"), "login reason names the screen");
  assert((dialogBlockedReason("resolving") ?? "").length > 0, "resolving has a reason");
  eq(dialogBlockedReason("open"), null, "nothing is blocked when the console is up");
});

test("gateEngaged fires on the edge into login, not on the level", () => {
  eq(gateEngaged("open", "login"), true, "open -> login");
  eq(gateEngaged("resolving", "login"), true, "resolving -> login");
  eq(gateEngaged("login", "login"), false, "login -> login is not a new edge");
  eq(gateEngaged("login", "open"), false, "signing IN never clears");
  eq(gateEngaged("open", "resolving"), false, "the splash is not the gate");
});

test("clearedRigState hands back fresh containers, never the dropped ones", () => {
  const a = clearedRigState();
  const b = clearedRigState();
  assert(a.previews !== b.previews, "previews array is not shared between clears");
  assert(a.logs !== b.logs, "logs array is not shared between clears");
  assert(a.toasts !== b.toasts, "toasts array is not shared between clears");
});

test("the cleared list covers the slices that describe the observatory", () => {
  // Named explicitly so adding a rig slice without adding it here is a visible
  // omission rather than a silent one.
  for (const k of [
    "status", "site", "config", "weather", "weatherAlertKey", "previews",
    "preview", "logs", "toasts", "safety", "sequence", "guide", "focus",
    "equipConnected", "lastReportId", "framing", "masters", "runBanner",
  ]) {
    assert(RIG_STATE_KEYS.includes(k as never), `${k} is cleared when the gate engages`);
  }
});

// ================================================= B. no dialog over the gate
await testAsync("an alert edge while the login gate is up raises no dialog", async () => {
  useStore.setState({ authGate: "open" });
  populateRig();
  assert(useStore.getState().weatherAlertKey > 0, "precondition: the alert latch fired");

  // The session ends AFTER the weather landed — expiry, an auth-epoch bump, a
  // relay reconnect. This is the exact sequence from the field report.
  useStore.getState().setAuthGate("login");

  const fired = weatherAlertEffect();
  eq(fired, null, "the effect declines to fire under the gate");
  eq(useStore.getState().confirm, null, "no dialog is mounted over the login screen");
});

await testAsync("even a call site that ignores the gate cannot get a dialog up", async () => {
  // The choke point, not the call site: pushConfirm is where EVERY confirm in
  // the app arrives, so a future effect that forgets the gate check still
  // cannot speak over the login screen.
  useStore.setState({ authGate: "login" });
  const p = useStore.getState().pushConfirm({ title: "Park the mount?", mode: "confirm" });
  eq(useStore.getState().confirm, null, "nothing was mounted");
  eq(await p, false, "the refused confirm resolves false (never a hung awaiter)");
});

await testAsync("the splash refuses dialogs too", async () => {
  useStore.setState({ authGate: "resolving" });
  const p = useStore.getState().pushConfirm({ title: "High cloud forecast tonight", mode: "ok" });
  eq(useStore.getState().confirm, null, "nothing over the boot splash");
  eq(await p, false, "resolves false");
});

// ============================== C. the store stops holding the rig (load-bearing)
test("once the gate engages the store holds nothing describing the rig", () => {
  useStore.setState({ authGate: "open" });
  populateRig();
  const live = useStore.getState();
  assert(live.status !== null, "precondition: a rig status is held");
  assert(live.weather !== null, "precondition: tonight's weather is held");
  assert(live.site !== null, "precondition: the site is held");
  assert(live.previews.length > 0, "precondition: preview frames are held");
  assert(live.logs.length > 0, "precondition: rig log lines are held");
  assert(live.toasts.length > 0, "precondition: a rig toast is up");

  useStore.getState().setAuthGate("login");

  const s = useStore.getState() as unknown as Record<string, unknown>;
  const pristine = clearedRigState() as unknown as Record<string, unknown>;
  for (const k of RIG_STATE_KEYS) {
    assert(
      JSON.stringify(s[k]) === JSON.stringify(pristine[k]),
      `${k} still describes the rig after the gate engaged: ${JSON.stringify(s[k])}`,
    );
  }
  // The specific leak from the report, stated in its own terms.
  eq(useStore.getState().weather, null, "tonight's forecast is gone");
  eq(useStore.getState().weatherAlertKey, 0, "the once-per-night latch went with it");
});

test("what the gate clears is exactly what a cold boot has", () => {
  // "Cleared" and "never had a rig" must be the same state — otherwise signing
  // out leaves the console somewhere a fresh load can never reach, which is how
  // half-cleared state turns into a wrong reading later.
  const pristine = clearedRigState() as unknown as Record<string, unknown>;
  for (const k of RIG_STATE_KEYS) {
    eq(JSON.stringify(pristine[k]), coldBootRig[k], `${k} matches its cold-boot value`);
  }
});

test("the gate takes the rig, not the user's own things", () => {
  useStore.setState({ authGate: "open" });
  useStore.getState().setPlan({ ...useStore.getState().plan, name: "Veil mosaic" });
  useStore.setState({
    principal: { role: "admin", email: "someone@example.com", caps: [] },
    authMethods: { methods: ["local"], google_configured: false, first_run: false },
    night: true,
  });
  populateRig();
  useStore.getState().setAuthGate("login");

  const s = useStore.getState();
  eq(s.plan.name, "Veil mosaic", "the user's target draft survives (it is on disk anyway)");
  assert(s.authMethods !== null, "authMethods survives — it IS the gate");
  assert(s.principal !== null, "principal survives — clearing it would dissolve the gate");
  eq(s.night, true, "night mode is a property of the person's eyes, not the rig");
});

test("clearing is a one-shot edge: what the login screen itself raises survives", () => {
  useStore.setState({ authGate: "open" });
  populateRig();
  useStore.getState().setAuthGate("login");
  useStore.getState().enqueueToast({ level: "error", title: "Invalid username or password." });
  // A second, redundant report of the same gate must not wipe it.
  useStore.getState().setAuthGate("login");
  const t = useStore.getState().toasts;
  eq(t.length, 1, "the login error is still on screen");
  eq(t[0].title, "Invalid username or password.", "and it is the login's own message");
});

await testAsync("a dialog already open when the gate engages is closed, not orphaned", async () => {
  useStore.setState({ authGate: "open" });
  const p = useStore.getState().pushConfirm({ title: "Abort the run?", mode: "confirm" });
  assert(useStore.getState().confirm !== null, "precondition: a confirm is up");
  useStore.getState().setAuthGate("login");
  eq(useStore.getState().confirm, null, "it is gone from the screen");
  eq(await p, false, "and its awaiter was answered instead of being wedged forever");
});

// ================================ D. the feature still works when signed in
await testAsync("a normal authenticated alert still fires the dialog", async () => {
  useStore.setState({ authGate: "open" });
  useStore.setState({ weather: null, weatherAlertKey: 0 });
  deliverWeatherAlert();
  eq(useStore.getState().weatherAlertKey, 1, "the null -> alert edge bumped the latch");

  const fired = weatherAlertEffect();
  assert(fired !== null, "the effect fired");
  eq(useStore.getState().confirm?.title, "High cloud forecast tonight", "the warning is on screen");
  useStore.getState().resolveConfirm(false); // acknowledge-only ("ok" mode)
  eq(await (fired as Promise<boolean>), false, "acknowledged");
});

await testAsync("signing back in re-arms the alert for the NEW session", async () => {
  // After a clear the latch is 0, so the next real alert edge (0 -> 1) fires
  // for the newly signed-in viewer instead of being swallowed as a repeat.
  useStore.setState({ authGate: "open" });
  deliverWeatherAlert();
  useStore.getState().setAuthGate("login");
  eq(useStore.getState().weatherAlertKey, 0, "latch reset by the gate");
  useStore.getState().setAuthGate("open"); // signed in again
  deliverWeatherAlert();
  eq(useStore.getState().weatherAlertKey, 1, "the new session gets its own edge");
  const fired = weatherAlertEffect();
  assert(fired !== null, "and the dialog fires for them");
  useStore.getState().resolveConfirm(false);
  await fired;
});

test("gate values round-trip through the store", () => {
  const gates: AuthGate[] = ["open", "resolving", "login", "open"];
  for (const gate of gates) {
    useStore.getState().setAuthGate(gate);
    eq(useStore.getState().authGate, gate, `store holds ${gate}`);
  }
});

// ---------------------------------------------------------------- report
if (failed > 0) {
  // eslint-disable-next-line no-console
  console.error(`\n${failed} failing:\n${failures.join("\n")}`);
  (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1);
} else {
  // eslint-disable-next-line no-console
  console.log(`authGate: ${passed} passed`);
}
