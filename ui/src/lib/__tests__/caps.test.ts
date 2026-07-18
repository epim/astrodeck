// caps.test.ts — capability-gate logic (W2.5 / spec §T8). VIEWER-READ-ONLY: the
// pure helpers in lib/caps decide whether a control surface is operable; the
// React hooks are thin wrappers over them, so testing the helpers tests the gate.
//
// No vitest/jest is wired into this UI yet (build is `tsc -b && vite build`), so
// these use the same inline assert harness as the other lane-1 tests. Run with:
//     npx tsx src/lib/__tests__/caps.test.ts
// They also compile under `tsc -b`, so the build gate covers them.

// The caps module imports the store, which reads localStorage + touches `document`
// at import time, so install minimal browser stubs BEFORE importing (mirrors
// src/__tests__/store.test.ts). Import dynamically AFTER the stubs are in place.
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null { return this.m.has(k) ? (this.m.get(k) as string) : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
}
const g = globalThis as unknown as {
  localStorage?: Storage;
  document?: unknown;
  window?: unknown;
};
if (typeof g.localStorage === "undefined") g.localStorage = new MemStorage() as unknown as Storage;
if (typeof g.document === "undefined") {
  const classList = { toggle() {}, add() {}, remove() {}, contains() { return false; } };
  const style = { setProperty() {}, getPropertyValue() { return ""; } };
  g.document = { documentElement: { classList, style } };
}
// lib/base.ts reads window.location.pathname at import time (H1 pulled it into
// the store graph via ws.ts) — stub it like the other browser globals.
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
  capAllowed,
  roleOf,
  isViewerRole,
  resolveRoleConnected,
  VIEW_REQUIRED_ROLE,
  shouldShowLogin,
  rolesHolding,
  accessPhrase,
} = await import("../caps");
const { useStore } = await import("../../store");
import type {
  AuthMethods,
  BackendLink,
  Capability,
  DeviceInfo,
  Principal,
} from "../../types";

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
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

// ---------------------------------------------------------------- fixtures
const ALL_CAPS: Capability[] = [
  "view.status", "view.preview", "view.media", "view.site_precise",
  "view.weather",
  "control.capture", "control.mount", "control.guide", "control.power",
  "config.safety", "config.solar_override", "config.backend",
  "config.site_optics", "config.alerts", "admin.users", "system.update",
];
const admin: Principal = { role: "admin", email: null, caps: ALL_CAPS };
const operator: Principal = {
  role: "operator", email: "op@x.io",
  caps: [
    "view.status", "view.preview", "view.weather",
    "control.capture", "control.guide", "control.mount",
  ],
};
const viewer: Principal = {
  role: "viewer", email: "v@x.io", caps: ["view.status", "view.preview"],
};
const viewerSentinel: Principal = { role: "viewer", email: null, caps: [] };

// ============================================================ capAllowed (fail-closed)
test("null principal denies every cap (unresolved → fail-closed viewer)", () => {
  for (const c of ALL_CAPS) assert(capAllowed(null, c) === false, `null should deny ${c}`);
});

test("admin (none-provider default) is granted ALL caps — LAN unchanged", () => {
  for (const c of ALL_CAPS) assert(capAllowed(admin, c) === true, `admin should allow ${c}`);
});

test("viewer is denied every control/config cap, allowed view caps", () => {
  assert(capAllowed(viewer, "view.status"), "viewer can view.status");
  assert(capAllowed(viewer, "view.preview"), "viewer can view.preview");
  assert(!capAllowed(viewer, "control.mount"), "viewer cannot control.mount");
  assert(!capAllowed(viewer, "control.capture"), "viewer cannot control.capture");
  assert(!capAllowed(viewer, "control.guide"), "viewer cannot control.guide");
  assert(!capAllowed(viewer, "control.power"), "viewer cannot control.power");
  assert(!capAllowed(viewer, "config.backend"), "viewer cannot config.backend");
});

test("operator can capture+guide+mount but NOT power/config", () => {
  assert(capAllowed(operator, "control.capture"), "operator can capture");
  assert(capAllowed(operator, "control.guide"), "operator can guide");
  assert(capAllowed(operator, "control.mount"), "operator can mount (2026-07-17 I1)");
  assert(!capAllowed(operator, "control.power"), "operator cannot power");
  assert(!capAllowed(operator, "config.backend"), "operator cannot config.backend");
});

test("operator can view.weather but NOT view.site_precise (2026-07-17 I2)", () => {
  assert(capAllowed(operator, "view.weather"), "operator can view.weather");
  assert(!capAllowed(operator, "view.site_precise"), "operator cannot view.site_precise");
  assert(!capAllowed(viewer, "view.weather"), "viewer cannot view.weather");
});

test("empty-caps viewer sentinel denies all (the 401 fail-closed path)", () => {
  for (const c of ALL_CAPS) assert(!capAllowed(viewerSentinel, c), `sentinel should deny ${c}`);
});

// ============================================================ role helpers
test("roleOf defaults to viewer when unresolved", () => {
  eq(roleOf(null), "viewer", "null →");
  eq(roleOf(admin), "admin", "admin →");
  eq(roleOf(operator), "operator", "operator →");
});

test("isViewerRole true for viewer + unresolved, false for op/admin", () => {
  assert(isViewerRole(null), "null is viewer (fail-closed)");
  assert(isViewerRole(viewer), "viewer is viewer");
  assert(!isViewerRole(operator), "operator is not viewer");
  assert(!isViewerRole(admin), "admin is not viewer");
});

// ============================================================ resolveRoleConnected (tri-state)
test("backend_links win: connected link → connected, error surfaced", () => {
  const links: BackendLink[] = [
    { role: "camera", ok: true, error: null, attempted: true, connected: true },
    { role: "telescope", ok: false, error: "timeout", attempted: true, connected: false },
  ];
  const cam = resolveRoleConnected("camera", links, undefined, false);
  eq(cam.connected, true, "camera connected");
  eq(cam.error, null, "camera no error");
  const tel = resolveRoleConnected("telescope", links, undefined, false);
  eq(tel.connected, false, "telescope not connected");
  eq(tel.error, "timeout", "telescope error surfaced");
});

test("attempted:false link is NOT a red LED — connected reads false, no error", () => {
  // never-attempted role (unfillable): connected=false but error stays null so the
  // grid renders an OFF/idle LED, not a failure.
  const links: BackendLink[] = [
    { role: "safety", ok: false, error: null, attempted: false, connected: false },
  ];
  const r = resolveRoleConnected("safety", links, undefined, false);
  eq(r.connected, false, "not connected");
  eq(r.error, null, "no error (never attempted)");
});

test("legacy fallback: [] backend_links → per-device connected flag", () => {
  const connected: Record<string, DeviceInfo> = {
    camera: { name: "ASI", kind: "camera", connected: true },
  };
  const r = resolveRoleConnected("camera", [], connected, false);
  eq(r.connected, true, "uses device flag");
  eq(r.error, null, "no error from legacy path");
});

test("legacy fallback: no link, no device → sticky equipConnected", () => {
  eq(resolveRoleConnected("camera", [], {}, true).connected, true, "sticky true");
  eq(resolveRoleConnected("camera", undefined, undefined, false).connected, false, "sticky false");
});

// ============================================================ view→role map
test("VIEW_REQUIRED_ROLE maps gated views to device roles", () => {
  eq(VIEW_REQUIRED_ROLE.capture, "camera", "capture →");
  eq(VIEW_REQUIRED_ROLE.focus, "camera", "focus →");
  eq(VIEW_REQUIRED_ROLE.mount, "telescope", "mount →");
  eq(VIEW_REQUIRED_ROLE.polar, "telescope", "polar →");
  eq(VIEW_REQUIRED_ROLE.guide, "guider", "guide →");
  eq(VIEW_REQUIRED_ROLE.power, "switch", "power →");
  assert(VIEW_REQUIRED_ROLE.monitor === undefined, "monitor is not equipment-gated");
});

// ============================================================ store integration
// Drive the REAL store principal slice and read it the way the hooks do (selector
// against getState) to prove the wiring matches the pure helpers end-to-end.
test("store: setting a viewer principal denies control caps via the slice", () => {
  useStore.setState({ principal: viewer });
  const s = useStore.getState();
  assert(!capAllowed(s.principal, "control.mount"), "viewer slice denies mount");
  assert(capAllowed(s.principal, "view.status"), "viewer slice allows view.status");
  assert(isViewerRole(s.principal), "slice resolves to viewer");
});

test("store: admin principal grants control caps via the slice", () => {
  useStore.setState({ principal: admin });
  const s = useStore.getState();
  assert(capAllowed(s.principal, "control.mount"), "admin slice allows mount");
  assert(capAllowed(s.principal, "control.power"), "admin slice allows power");
  assert(!isViewerRole(s.principal), "admin slice is not viewer");
});

test("store: null principal (pre-resolution) fails closed via the slice", () => {
  useStore.setState({ principal: null });
  const s = useStore.getState();
  assert(!capAllowed(s.principal, "control.capture"), "null slice denies capture");
  assert(isViewerRole(s.principal), "null slice is viewer (fail-closed)");
});

// ============================================================ login gate (W2.6)
// The HARD non-breaking guarantee: no method enabled ⇒ NEVER a login screen.
const M = (methods: string[], extra: Partial<AuthMethods> = {}): AuthMethods => ({
  methods, google_configured: false, first_run: false, ...extra,
});

test("login gate: null signal never gates (fail open until it loads)", () => {
  assert(shouldShowLogin(null, null) === false, "null authMethods → no login");
  assert(shouldShowLogin(null, admin) === false, "null authMethods + admin → no login");
});

test("login gate: methods==[] NEVER shows login (open LAN, unchanged)", () => {
  assert(shouldShowLogin(M([]), null) === false, "open + null principal");
  assert(shouldShowLogin(M([]), viewerSentinel) === false, "open + viewer sentinel");
  assert(shouldShowLogin(M([]), admin) === false, "open + admin");
  assert(shouldShowLogin(M([], { first_run: true }), null) === false, "open wins over first_run");
});

test("login gate: first_run shows login regardless of principal", () => {
  assert(shouldShowLogin(M(["local"], { first_run: true }), null) === true, "first_run → login");
  assert(shouldShowLogin(M(["local"], { first_run: true }), admin) === true, "first_run even if admin sentinel stale");
});

test("login gate: method enabled + anonymous sentinel shows login", () => {
  assert(shouldShowLogin(M(["local"]), viewerSentinel) === true, "local + anon → login");
  assert(shouldShowLogin(M(["google"], { google_configured: true }), viewerSentinel) === true, "google + anon → login");
});

test("login gate: method enabled + signed-in identity does NOT gate", () => {
  assert(shouldShowLogin(M(["local"]), admin) === false, "admin (caps) → no login");
  assert(shouldShowLogin(M(["local"]), operator) === false, "operator (email) → no login");
  assert(shouldShowLogin(M(["local"]), viewer) === false, "named viewer (email) → no login");
});

test("login gate: method enabled + principal not yet resolved waits", () => {
  assert(shouldShowLogin(M(["local"]), null) === false, "null principal → wait, don't flash login");
});

// ============================================================ lock-note copy
// accessPhrase derives "who can do this" from the role table mirrored off
// server/astrodeck/auth/capabilities.py — the R4B-PLAN-01/SESS-01 fix: lock
// notes must name the SAME policy the control enforces, never a role promise
// the server would deny.

test("rolesHolding: admin-only caps resolve to exactly [admin]", () => {
  for (const c of [
    "control.power", "config.backend", "config.safety",
    "config.solar_override", "config.site_optics", "config.alerts",
    "admin.users", "system.update", "view.media", "view.site_precise",
  ] as Capability[]) {
    eq(rolesHolding(c).join(","), "admin", `${c} →`);
  }
});

test("rolesHolding: operator caps resolve to [operator, admin]", () => {
  eq(rolesHolding("control.capture").join(","), "operator,admin", "capture →");
  eq(rolesHolding("control.guide").join(","), "operator,admin", "guide →");
  // 2026-07-17 decisions wave I1: operator holds control.mount.
  eq(rolesHolding("control.mount").join(","), "operator,admin", "mount →");
  // 2026-07-17 decisions wave I2: operator holds view.weather (but NOT
  // view.site_precise, which stays admin-only — see the admin-only test above).
  eq(rolesHolding("view.weather").join(","), "operator,admin", "weather →");
});

test("rolesHolding: view caps are held by every role", () => {
  eq(rolesHolding("view.status").join(","), "viewer,operator,admin", "status →");
  eq(rolesHolding("view.preview").join(","), "viewer,operator,admin", "preview →");
});

test("accessPhrase: admin-only caps say 'admin access' — NEVER 'operator or admin'", () => {
  eq(accessPhrase("config.backend"), "admin access", "backend →");
  eq(accessPhrase("config.solar_override"), "admin access", "solar →");
});

test("accessPhrase: operator-held caps say 'operator or admin access'", () => {
  eq(accessPhrase("control.capture"), "operator or admin access", "capture →");
  eq(accessPhrase("control.guide"), "operator or admin access", "guide →");
  // 2026-07-17 decisions wave I1: operator now holds control.mount, so the
  // lock-note copy self-updates from "admin access" back to this phrase.
  eq(accessPhrase("control.mount"), "operator or admin access", "mount →");
  // 2026-07-17 decisions wave I2: same self-update for the new view.weather cap.
  eq(accessPhrase("view.weather"), "operator or admin access", "weather →");
});

test("accessPhrase mirror never promises a cap the principal model denies", () => {
  // Cross-check the mirror against the SAME fixtures capAllowed uses: if the
  // phrase names a role, a principal of that role must actually hold the cap.
  const byRole: Record<string, Principal> = { viewer, operator, admin };
  for (const c of ALL_CAPS) {
    for (const r of rolesHolding(c)) {
      assert(capAllowed(byRole[r], c), `${r} must hold ${c} (phrase promised it)`);
    }
  }
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ncaps.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
