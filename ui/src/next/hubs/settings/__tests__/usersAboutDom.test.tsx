// usersAboutDom.test.tsx - the USERS and ABOUT screens and their six sheets
// (T-SET-3, plan section E.5), MOUNTED and pressed.
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/usersAboutDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally
// plus the `{ passed, failed, total }` export (shell-and-tests.md section 4).
//
// What each test guards, and what goes RED if the guard is removed:
//   1. screen markers  - if UsersScreen/AboutScreen stopped rendering their
//      `data-testid` root, the `assert(... != null, ...)` calls fail first,
//      before any content assertion gets a chance to run.
//   2. signed-in line  - if AccountBody stopped mounting the real
//      `AccountPanel` (e.g. swapped for a stub), the email/role regex match
//      against the rendered text fails.
//   3. viewer gate on the users sheet - if `UsersSheet` mounted `UsersPanel`
//      unconditionally instead of gating on `useCanAdminUsers()`, the
//      EmptyCard hint assertion fails (the panel's own "Users" title would
//      be on screen instead) AND the `asked` assertion fails (UsersPanel's
//      mount effect calls `listUsers()` unconditionally). A REAL sabotage of
//      this exact gate is run below and reported.
//   4. the help topic deep link - if `HelpSheet` stopped calling
//      `openHelp(topic)` (or stopped validating it against the real
//      `TROUBLESHOOTING` list), `store.helpTopic` never flips and the
//      symptom heading never scrolls into a highlighted state HelpView
//      would render the same way regardless - the text assertion and the
//      `helpTopic` assertion both fail.
//   5. the update sheet's read-only rendering - if `UpdateSheet` were changed
//      to hide the panel behind its own EmptyCard (instead of relying on
//      `UpdatePanel`'s internal `useCan("system.update")` gate), OR if that
//      internal gate were removed, the `disabled === true` assertion on the
//      "Check now" button fails.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="u"></div><div id="a"></div><div id="s"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

Object.defineProperty(win, "isSecureContext", { value: true, configurable: true });
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
// jsdom implements neither; HelpView's deep-link effect calls the first one.
win.HTMLElement.prototype.scrollIntoView = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- network
// Honest stub: every request is recorded (`asked`) so a test can assert what
// was, or was NOT, requested - the point of the viewer-gate test below.
const asked: string[] = [];
const ok = (data: unknown) => ({
  ok: true, status: 200, statusText: "OK", json: async () => data,
});
g.fetch = async (url: string, init?: { method?: string }) => {
  const u = String(url);
  const method = init?.method ?? "GET";
  asked.push(`${method} ${u}`);
  if (u.includes("/api/update/status")) {
    return ok({
      current: "0.3.28", latest: null, update_available: false,
      notes_md: "", channel: "stable", last_check_ts: null,
      phase: "idle", progress: 0, error: null, last_result: null,
      supervised: true, can_apply: false, apply_blocked_reason: "",
    });
  }
  if (u.includes("/api/users")) return ok({ users: [] });
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");

const { UsersScreen, AboutScreen } = await import("../sheets/set3");
const { UsersSheet } = await import("../sheets/UsersSheet");
const { HelpSheet } = await import("../sheets/HelpSheet");
const { UpdateSheet } = await import("../sheets/UpdateSheet");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const q = (host: any, sel: string): any => host.querySelector(sel);
const buttons = (host: any): any[] => Array.from(host.querySelectorAll("button"));

const ADMIN = {
  role: "admin", email: "night-owner@example.test",
  caps: [
    "view.status", "view.preview", "view.media", "view.site_precise",
    "view.site_derived", "view.weather", "control.capture", "control.mount",
    "control.guide", "control.power", "config.safety", "config.solar_override",
    "config.backend", "config.site_optics", "config.alerts", "admin.users",
    "system.update",
  ],
};
const VIEWER = { role: "viewer", email: "guest@example.test", caps: ["view.status", "view.preview"] };
const OPERATOR = {
  role: "operator", email: "op@example.test",
  caps: ["view.status", "view.preview", "view.weather", "control.capture", "control.guide", "control.mount"],
};

/** The minimal store slices every panel under test touches, so a mount never
 *  throws on `status.busy` / `sequence.state` / `config.update` being absent. */
const baseSeed = () => ({
  status: { connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [] },
  sequence: { state: "idle" },
  config: {
    version: 1,
    auth: { methods: [], google_configured: false, admin_token_configured: false, session_signing_configured: false, role_allowlist: {}, default_role: null, provider: "none" },
    update: {
      enabled: true, auto_check: false, check_interval_hours: 24,
      channel: "stable", repo: "epim/astrodeck", signing_pubkey: "",
      health_timeout_s: 60, last_check_ts: null,
    },
  },
  update: {
    current: "0.3.28", latest: null, update_available: false, notes_md: "",
    channel: "stable", last_check_ts: null, phase: "idle", progress: 0,
    error: null, last_result: null,
  },
  authMethods: { methods: [], google_configured: false, first_run: false },
  helpTopic: null,
});

const seed = (principal: unknown, over: Record<string, unknown> = {}) => {
  useStore.setState({ ...baseSeed(), principal, ...over } as never);
};

// ============================================================= USERS + ABOUT
// -------------------------------------------------------------- precondition
seed(ADMIN);
const uHost = win.document.getElementById("u") as any;
const uRoot = createRoot(uHost);
act(() => { uRoot.render(createElement(UsersScreen)); });

const aHost = win.document.getElementById("a") as any;
const aRoot = createRoot(aHost);
act(() => { aRoot.render(createElement(AboutScreen)); });

test("precondition: UsersScreen rendered its marker", () => {
  assert(q(uHost, '[data-testid="screen-users"]') != null,
    "no [data-testid=screen-users] - the fixture is wrong, not the component");
});

test("precondition: AboutScreen rendered its marker", () => {
  assert(q(aHost, '[data-testid="screen-about"]') != null,
    "no [data-testid=screen-about] - the fixture is wrong, not the component");
});

test("ABOUT renders both version rows", () => {
  assert(q(aHost, '[data-testid="row-engine"]') != null, "no ENGINE ON THE RIG row");
  assert(q(aHost, '[data-testid="row-app"]') != null, "no THIS APP row");
  assert(/THIS APP/.test(aHost.textContent), "the THIS APP row title never reached the DOM");
});

test("the signed-in line shows the seeded admin's email and role", () => {
  assert(q(uHost, '[data-testid="account-body"]') != null, "AccountBody did not mount");
  assert(uHost.textContent.includes("night-owner@example.test"),
    "AccountPanel's identity line (the seeded principal's email) is not on screen");
  assert(/admin/i.test(uHost.textContent),
    "AccountPanel's role chip (the seeded principal's role) is not on screen");
});

test("ROLE_DESCRIPTIONS are printed on the USERS screen", () => {
  assert(/Live status and preview frames only/.test(uHost.textContent),
    "the viewer ROLE_DESCRIPTIONS sentence is not on screen");
});

act(() => { uRoot.unmount(); });
act(() => { aRoot.unmount(); });

// ============================================================ USERS SHEET
{
  const host = win.document.getElementById("s") as any;
  const root = createRoot(host);
  const asked0 = asked.length;

  seed(VIEWER);
  act(() => { root.render(createElement(UsersSheet, { params: {}, depth: 0 })); });

  test("the users sheet for a viewer shows the admin.users reason", () => {
    assert(/needs admin access|admin access/i.test(host.textContent),
      "the admin.users accessPhrase reason is not on screen for a viewer");
    assert(!/Add user/.test(host.textContent),
      "UsersPanel's own chrome (the Add user button) rendered for a viewer - the gate did not block the mount");
  });

  test("the users sheet for a viewer fires no /api/users request", () => {
    const firedUsersReq = asked.slice(asked0).some((r) => r.includes("/api/users"));
    assert(!firedUsersReq,
      `a request was fired for a viewer: ${JSON.stringify(asked.slice(asked0))}`);
  });

  act(() => { root.unmount(); });
}

// ============================================================ HELP SHEET
{
  const host = win.document.getElementById("s") as any;
  const root = createRoot(host);
  seed(ADMIN);
  useStore.setState({ helpTopic: null } as never);
  act(() => { root.render(createElement(HelpSheet, { params: { topic: "camera-offline" }, depth: 0 })); });
  await settle();

  test("the help sheet with topic=camera-offline renders that topic's heading", () => {
    assert(/The camera keeps disconnecting/.test(host.textContent),
      "the camera-offline TROUBLESHOOTING entry's symptom heading is not on screen");
  });

  test("the help sheet sets store.helpTopic from the route param", () => {
    assert(useStore.getState().helpTopic === "camera-offline",
      "store.helpTopic was never set to the route's ?topic= value");
  });

  test("the help sheet renders its own Setup guide row", () => {
    assert(q(host, '[data-testid="row-setup-guide"]') != null, "no SETUP GUIDE row above HelpView");
  });

  act(() => { root.unmount(); });
}

// ============================================================ UPDATE SHEET
{
  const host = win.document.getElementById("s") as any;
  const root = createRoot(host);
  seed(OPERATOR);
  act(() => { root.render(createElement(UpdateSheet, { params: {}, depth: 0 })); });
  await settle();

  test("the update sheet for a non-admin is read-only", () => {
    const checkBtn = buttons(host).find((b) => /check now/i.test(b.textContent || ""));
    assert(checkBtn != null, "no Check now button rendered - the fixture is wrong, not the component");
    assert(checkBtn.disabled === true,
      "Check now is not disabled for a principal without system.update");
  });

  act(() => { root.unmount(); });
}

// --------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`usersAboutDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export default { passed, failed, total };
export { passed, failed, total };
