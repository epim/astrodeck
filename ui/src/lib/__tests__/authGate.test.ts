// authGate.test.ts — #117: the console must not describe the observatory to
// someone who is looking at the login screen.
//
// The reported failure was a modal reading "High cloud forecast tonight —
// Forecast peak N% total cloud … at/above your N% threshold" rendered OVER the
// sign-in form. Four claims are pinned here, in the order they matter:
//   1. an alert edge while the gate is up raises no dialog — and neither does
//      any other thing that can speak (toast, OS notification, beep),
//   2. once the gate engages the store is holding nothing that describes the
//      rig AND does not take any more of it in — the load-bearing half, because
//      the socket stays open across a sign-out and the server keeps serving the
//      pre-logout principal for up to WS_AUTH_RECHECK_S (60s),
//   3. a normal, signed-in alert still fires (the feature is not "fixed" by
//      being removed), and
//   4. an alert that arrives while the gate is up is not eaten — it is either
//      re-offered when the gate lifts, or never consumed in the first place.
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

// A recording stand-in for the browser Notification constructor. lib/notify.ts
// fires one only when permission is "granted", so without this the OS-level
// channel is a silent no-op under node and the "does it beep at a stranger?"
// claim below could not be made at all. (beep() needs window.AudioContext,
// which the stub above does not provide, so it self-cancels — the Notification
// is the observable half.)
const notified: { title: string; body: string }[] = [];
class FakeNotification {
  static permission = "granted";
  constructor(title: string, opts: { body: string }) {
    notified.push({ title, body: opts.body });
  }
}
(g as unknown as { Notification?: unknown }).Notification = FakeNotification;

const {
  announcementsBlocked,
  clearedRigState,
  gateBlockedReason,
  gateEngaged,
  intakeBlocked,
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

// App's weather-alert effect, minus React. There is no DOM test runner in this
// UI, so this is a MODEL of App.tsx's effect and not the effect itself — if
// App's dependency array and this stop agreeing, only a human reading both will
// notice. That is exactly why the dep array is modelled here rather than the
// body alone: the bug this replaces was a missing DEPENDENCY (the gate), not a
// missing statement, and a harness that re-runs the body on demand cannot tell
// the difference between "React re-ran it" and "the test called it again".
//
// So: render() re-runs the body only when a dep actually changed, the way React
// does. `deps` mirrors App.tsx's `[weatherAlertKey, gate]` exactly.
let lastDeps: unknown[] | null = null;
function resetEffect(): void { lastDeps = null; }
function render(): Promise<boolean> | null {
  const st = useStore.getState();
  const deps: unknown[] = [st.weatherAlertKey, st.authGate];
  if (lastDeps && deps.every((d, i) => d === lastDeps![i])) return null; // no re-run
  lastDeps = deps;
  // ---- effect body, same shape as App.tsx ----
  if (st.weatherAlertKey === 0) return null;
  if (announcementsBlocked(st.authGate)) return null;
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
test("announcementsBlocked: only the operational console may speak", () => {
  eq(announcementsBlocked("open"), false, "open");
  eq(announcementsBlocked("login"), true, "login");
  // The pre-decision splash counts too: a method is enabled and we do not yet
  // know who is looking, and "we don't know" is not "anyone".
  eq(announcementsBlocked("resolving"), true, "resolving");
});

test("intake stops at the login screen but not at the boot splash", () => {
  eq(intakeBlocked("login"), true, "the sign-in screen refuses rig telemetry");
  eq(intakeBlocked("open"), false, "a signed-in console takes everything");
  // Deliberately NARROWER than announcementsBlocked. The splash renders none of
  // these slices, and the one-shot `hello` (config/site/safety) lands in exactly
  // that window and never comes again on this socket — refusing it would leave
  // an entitled viewer with a console that never got its cold snapshot, to
  // protect a screen that displays nothing. A splash that resolves INTO login
  // has everything dropped by gateEngaged anyway.
  eq(intakeBlocked("resolving"), false, "the splash may hold what it may not say");
});

test("a blocked thing can say what blocked it", () => {
  assert((gateBlockedReason("login") ?? "").includes("sign-in screen"), "login reason names the screen");
  assert((gateBlockedReason("resolving") ?? "").length > 0, "resolving has a reason");
  eq(gateBlockedReason("open"), null, "nothing is blocked when the console is up");
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

  resetEffect();
  const fired = render();
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

// The confirm host is not the only overlay the gate screens keep mounted —
// App's login branch also mounts <Toasts/>. A toast is a sentence about the rig
// ("UNSAFE: rain detected", "Sequence failed: mount lost"), so gating only the
// confirm would have moved the leak one overlay to the left.
test("no toast reaches the toast host while a gate screen is up", () => {
  for (const gate of ["login", "resolving"] as AuthGate[]) {
    useStore.setState({ authGate: "open", toasts: [] });
    useStore.setState({ authGate: gate });
    useStore.getState().enqueueToast({ level: "error", title: "UNSAFE: rain detected", ttl: 0 });
    useStore.getState().enqueueToast({ level: "error", title: "Sequence failed: mount lost" });
    eq(useStore.getState().toasts.length, 0, `${gate}: nothing was queued`);
  }
});

test("the OS-level channel is gated too — no beep at a stranger", () => {
  // notifyAndBeep leaves the page entirely: a Web Notification plus an audible
  // beep, which no overlay can cover. And the 30s link-down alert needs NO open
  // socket — it runs off a timer armed when wsPhase goes "down", which is
  // exactly what happens once the gate engages and reconnects start being
  // refused. Drive that timer directly rather than waiting 30s for it.
  const realSetTimeout = globalThis.setTimeout;
  let armed: (() => void) | null = null;
  (globalThis as unknown as { setTimeout: unknown }).setTimeout =
    ((fn: () => void) => { armed = fn; return 0; }) as unknown as typeof setTimeout;
  try {
    for (const [gate, want] of [["login", 0], ["resolving", 0], ["open", 1]] as const) {
      notified.length = 0;
      armed = null;
      useStore.setState({ authGate: "open", notifyEnabled: true, wsPhase: "up" });
      useStore.setState({ authGate: gate });
      useStore.getState().setWsPhase("down");
      assert(armed !== null, `${gate}: the link-down timer was armed`);
      (armed as unknown as () => void)();
      eq(notified.length, want, `${gate}: OS notifications fired`);
    }
  } finally {
    (globalThis as unknown as { setTimeout: unknown }).setTimeout = realSetTimeout;
    useStore.setState({ notifyEnabled: false, wsPhase: "up" });
  }
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

// THE FINDING THAT SENT THIS BACK. Clearing the slices is worth about two
// seconds on its own. Pressing Sign out does not close the websocket (nothing on
// that path calls reconnectWs, and api.ts has no 401 interceptor), and the
// server re-authenticates an already-open /ws only every WS_AUTH_RECHECK_S =
// 60s — until then it is still serving the PRE-logout principal, unredacted,
// while hub publishes `status` every 2s.
test("the rig cannot refill the store through the socket that is still open", () => {
  useStore.setState({ authGate: "open" });
  populateRig();
  useStore.getState().setAuthGate("login");

  // Everything the open socket would deliver in the next minute.
  const ts = Date.now() / 1000;
  const st = useStore.getState();
  st.handleEvent({
    type: "status",
    data: {
      mode: "zwo-usb",
      site: { name: "Test Site", latitude: 12.34, longitude: -56.78, is_default: false, horizon_min_deg: 20 },
      mount: { tracking: true, parked: false, slewing: false, ra_str: "01:02:03", dec_str: "+40:00:00", alt: 62 },
    } as unknown as Record<string, unknown>,
    ts,
  });
  deliverWeatherAlert();
  st.handleEvent({ type: "preview", data: { id: 9, url: "/api/preview/9.jpg" } as unknown as Record<string, unknown>, ts });
  st.handleEvent({ type: "log", data: { level: "error", source: "mount", message: "guiding lost" }, ts });
  st.handleEvent({ type: "safety", data: { is_safe: false, reason: "rain detected", stale: false } as unknown as Record<string, unknown>, ts });
  st.handleEvent({ type: "hello", data: { mode: "zwo-usb", site: { name: "Test Site" } } as unknown as Record<string, unknown>, ts });
  // Not a WS frame: ws.onopen fetches /api/logs and AWAITS it, so this is the
  // one rig-state write that can land without passing through handleEvent.
  st.reconcileLogs([
    { type: "log", ts, data: { level: "error", source: "mount", message: "guiding lost" } },
  ] as unknown as Parameters<typeof st.reconcileLogs>[0]);

  const s = useStore.getState() as unknown as Record<string, unknown>;
  const pristine = clearedRigState() as unknown as Record<string, unknown>;
  for (const k of RIG_STATE_KEYS) {
    assert(
      JSON.stringify(s[k]) === JSON.stringify(pristine[k]),
      `${k} was refilled behind the login screen: ${JSON.stringify(s[k])}`,
    );
  }
  // Named individually because each is a distinct thing the field report was
  // about: where this observatory is, what its sky is doing, and what it saw.
  eq(useStore.getState().status, null, "no site name, coordinates, or mount pointing");
  eq(useStore.getState().weather, null, "no forecast, threshold, or site_lat/site_lon");
  eq(useStore.getState().previews.length, 0, "no frames of tonight's target");
  eq(useStore.getState().toasts.length, 0, "and no 'UNSAFE: rain detected' over the sign-in form");
});

test("clearing is a one-shot edge, not a per-commit sweep", () => {
  useStore.setState({ authGate: "open" });
  populateRig();
  useStore.getState().setAuthGate("login");
  const before = useStore.getState();
  // A second, redundant report of the same gate is a no-op, not a second clear.
  useStore.getState().setAuthGate("login");
  const after = useStore.getState();
  eq(after.toasts, before.toasts, "the same array object — nothing was rebuilt");
  eq(after.previews, before.previews, "same here");
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

  resetEffect();
  const fired = render();
  assert(fired !== null, "the effect fired");
  eq(useStore.getState().confirm?.title, "High cloud forecast tonight", "the warning is on screen");
  useStore.getState().resolveConfirm(false); // acknowledge-only ("ok" mode)
  eq(await (fired as Promise<boolean>), false, "acknowledged");

  // Still once per alert: a re-render that changes neither dep says nothing.
  eq(render(), null, "the acknowledged alert does not come back on the next render");
});

// ==================== E. the mandatory notice is deferred, never eaten
// A notice suppressed because the gate was up must be DELIVERED when the gate
// lifts, or the "user-required hard notice" is quietly downgraded to "shown if
// the timing was lucky". Both routes to that are pinned here; note that render()
// re-runs the body ONLY on a dep change, so the second delivery below happens
// because the gate is a dependency, not because the test called it twice.
await testAsync("an alert consumed under the splash is delivered when it lifts", async () => {
  resetEffect();
  useStore.setState({ authGate: "open", weather: null, weatherAlertKey: 0, confirm: null });
  render();
  // The splash HOLDS rig state (intakeBlocked is false there) but may not speak,
  // so the latch moves while the dialog cannot fire — a one-way door unless the
  // effect also watches the gate.
  useStore.setState({ authGate: "resolving" });
  deliverWeatherAlert();
  eq(useStore.getState().weatherAlertKey, 1, "the latch moved under the splash");
  eq(render(), null, "and nothing was said over it");

  useStore.setState({ authGate: "open" });   // splash lifts; the KEY never changes again
  const fired = render();
  assert(fired !== null, "the notice the splash was sitting on is delivered");
  eq(useStore.getState().confirm?.title, "High cloud forecast tonight", "on screen");
  useStore.getState().resolveConfirm(false);
  await fired;
});

await testAsync("an alert that arrives under the login gate is not consumed at all", async () => {
  resetEffect();
  useStore.setState({ authGate: "open", weather: null, weatherAlertKey: 0, confirm: null });
  useStore.getState().setAuthGate("login");
  deliverWeatherAlert();                     // dropped at intake — never consumed
  eq(useStore.getState().weatherAlertKey, 0, "the latch never moved, so it is not spent");
  eq(useStore.getState().weather, null, "and nothing about tonight is held");

  // Signing back in. App re-fetches /api/weather on the login -> open edge
  // precisely because nothing else re-delivers it (ws.onopen fetches
  // config/logs/snapshot, not weather, and the server republishes only every
  // 15 minutes) — this is that fetch, routed through handleEvent the same way.
  useStore.getState().setAuthGate("open");
  deliverWeatherAlert();
  eq(useStore.getState().weatherAlertKey, 1, "the fresh session gets the edge");
  const fired = render();
  assert(fired !== null, "and the high-cloud warning is finally shown");
  useStore.getState().resolveConfirm(false);
  await fired;
});

await testAsync("signing back in re-arms the alert for the NEW session", async () => {
  // After a clear the latch is 0, so the next real alert edge (0 -> 1) fires
  // for the newly signed-in viewer instead of being swallowed as a repeat.
  resetEffect();
  useStore.setState({ authGate: "open" });
  deliverWeatherAlert();
  useStore.getState().setAuthGate("login");
  eq(useStore.getState().weatherAlertKey, 0, "latch reset by the gate");
  useStore.getState().setAuthGate("open"); // signed in again
  deliverWeatherAlert();
  eq(useStore.getState().weatherAlertKey, 1, "the new session gets its own edge");
  const fired = render();
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
