// usersAboutDom.test.tsx - the USERS and ABOUT screens and their sheets
// (T-SET-3, plan section E.5), MOUNTED and pressed; extended for wave R7's
// rebuild of the three PEOPLE panels (T-R7-11, plan sections 3.F4-3.F6).
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/usersAboutDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally
// plus the `{ passed, failed, total }` export (shell-and-tests.md section 4).
//
// What each test guards, and what goes RED if the guard is removed:
//   1. screen + sheet markers - if a screen or a rebuilt editor stopped
//      rendering its `data-testid` root, the `assert(... != null, ...)` calls
//      fail first, before any content assertion gets a chance to run.
//   2. signed-in line  - if `AccountBody` stopped mounting the real
//      `AccountIdentity`, the email/role assertions against the rendered text
//      fail.
//   3. ADD USER posts exactly once - if the submit gate stopped honouring
//      `newUserBlocker` (or fired twice), the `posts("/api/users").length`
//      assertions fail. The pre-typing half of the test is what makes "once"
//      mean once: a form that posts an empty draft would already be at 1.
//   4. DELETE goes through `confirmDialog` - if `UserRow.onDelete` called
//      `deleteUser` directly, the "no DELETE before the confirm resolves"
//      assertion fails; if it ignored a FALSE answer, the "cancel deletes
//      nothing" assertion fails. A REAL sabotage of both is run and reported.
//   5. non-admin honest-disabled - if any control in the rebuilt PEOPLE editor
//      shipped a native `disabled`, or shipped no lock at all, the
//      "every control carries aria-disabled" assertion fails; if the editor
//      fetched without the capability, the `asked` assertion fails.
//   6. ROLE HONESTY - the default-role picker's options are compared against
//      the keys of `ROLE_CAPS` read out of `lib/caps.ts` ITSELF, not against a
//      list this test also types out. A picker that hard-codes four roles goes
//      red the moment the role table gains or loses one.
//   7. SECRET HYGIENE - the break-glass input is asserted EMPTY while the
//      seeded config carries a token value, so a rebuild that echoed the
//      server's block into the box fails even though the box would look right.
//   8. the help topic deep link and the update sheet's read-only rendering,
//      unchanged from T-SET-3.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css hook
// The PEOPLE area root imports `people.css` (wave R7's rule: one stylesheet per
// area, imported by that area, never by `NextApp.tsx`). Node has no idea what a
// `.css` file is, so a synchronous load hook answers with an empty module.
// Verbatim from `next/__tests__/shellDom.test.tsx`; it has to run before any
// `await import` below, which is why the whole file uses dynamic imports.
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

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
  "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- network
// Honest stub: every request is recorded (`asked`) so a test can assert what
// was, or was NOT, requested - the point of the non-admin test below.
interface Ask { method: string; url: string; body: any }
const asked: Ask[] = [];
const ok = (data: unknown) => ({
  ok: true, status: 200, statusText: "OK", json: async () => data,
});

/** The account list the stubbed `GET /api/users` answers with. Mutable so one
 *  test can seed a person to delete without a second fetch stub. */
let usersFixture: any[] = [];

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const u = String(url);
  const method = init?.method ?? "GET";
  asked.push({ method, url: u, body: init?.body ? JSON.parse(init.body) : undefined });
  if (u.includes("/api/update/status")) {
    return ok({
      current: "0.3.28", latest: null, update_available: false,
      notes_md: "", channel: "stable", last_check_ts: null,
      phase: "idle", progress: 0, error: null, last_result: null,
      supervised: true, can_apply: false, apply_blocked_reason: "",
    });
  }
  if (u.includes("/api/users")) {
    if (method === "POST") return ok({ id: "new", username: "made", email: null, role: "operator", enabled: true, created: 0 });
    if (method === "DELETE") return ok({ ok: true });
    if (method === "PATCH") return ok(usersFixture[0] ?? {});
    return ok({ users: usersFixture });
  }
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { accessPhrase } = await import("../../../../lib/caps");

const { UsersScreen, AboutScreen } = await import("../sheets/set3");
const { UsersSheet } = await import("../sheets/UsersSheet");
const { AuthMethodsSheet } = await import("../sheets/AuthMethodsSheet");
const { AccountSheet } = await import("../sheets/AccountSheet");
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
const qa = (host: any, sel: string): any[] => Array.from(host.querySelectorAll(sel));
const byId = (host: any, id: string): any => host.querySelector(`[data-testid="${id}"]`);
const buttons = (host: any): any[] => Array.from(host.querySelectorAll("button"));

const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const typeInto = (el: any, value: string): void => {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
    setter.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
};
const reqs = (method: string, match: string): Ask[] =>
  asked.filter((a) => a.method === method && a.url.includes(match));

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
 *  throws on `status.busy` / `sequence.state` / `config.update` being absent.
 *  `wsPhase: "up"` matters: `next/lib/gate.ts` puts "the rig is not reachable"
 *  AHEAD of the capability, so a store left on the default "connecting" would
 *  lock every control for the WRONG reason and the admin.users assertions would
 *  pass while proving nothing. */
const baseSeed = () => ({
  wsPhase: "up",
  status: { connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [] },
  sequence: { state: "idle" },
  config: {
    version: 1,
    auth: {
      methods: [], google_configured: false, admin_token_configured: false,
      session_signing_configured: false, role_allowlist: {}, default_role: null,
      provider: "none", session_ttl_s: 28800, local_enabled_first_run: true,
      trust_loopback: true,
    },
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
  confirm: null,
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
  assert(byId(uHost, "account-identity") != null,
    "the rebuilt AccountIdentity did not mount inside AccountBody");
  assert(uHost.textContent.includes("night-owner@example.test"),
    "the identity line (the seeded principal's email) is not on screen");
  assert(/admin/i.test(uHost.textContent),
    "the role word (the seeded principal's role) is not on screen");
});

test("ROLE_DESCRIPTIONS are printed on the USERS screen", () => {
  assert(/Live status and preview frames only/.test(uHost.textContent),
    "the viewer ROLE_DESCRIPTIONS sentence is not on screen");
});

act(() => { uRoot.unmount(); });
act(() => { aRoot.unmount(); });

// ================================================ PEOPLE - the non-admin path
{
  const host = win.document.getElementById("s") as any;
  const root = createRoot(host);
  const asked0 = asked.length;

  seed(VIEWER);
  act(() => { root.render(createElement(UsersSheet, { params: {}, depth: 0 })); });
  await settle();

  test("the PEOPLE sheet renders its marker and the rebuilt editor for a viewer", () => {
    assert(byId(host, "settings-users") != null, "no [data-testid=settings-users]");
    assert(byId(host, "users-editor") != null,
      "the rebuilt UsersEditor did not render for a viewer - the screen was hidden instead");
  });

  test("a viewer sees every PEOPLE control honest-disabled, naming admin.users", () => {
    const editor = byId(host, "users-editor");
    const controls = [
      ...qa(editor, "button"),
      ...qa(editor, "input"),
    ];
    assert(controls.length > 0,
      "no controls inside users-editor at all - the assertion below would pass vacuously");
    for (const el of controls) {
      // The native attribute is checked FIRST: it strips the element from the
      // accessibility tree, so it would also fail the honest check below and
      // report the wrong cause.
      assert(el.hasAttribute("disabled") === false,
        `a control in users-editor uses the native disabled attribute: `
        + `<${el.tagName.toLowerCase()} data-testid=${el.getAttribute("data-testid")}>`);
      const honest = el.getAttribute("aria-disabled") === "true"
        || (el.tagName === "INPUT" && el.readOnly === true);
      assert(honest,
        `a control in users-editor is live for a viewer: <${el.tagName.toLowerCase()} `
        + `data-testid=${el.getAttribute("data-testid")}>`);
    }
    assert(host.textContent.includes(accessPhrase("admin.users")),
      `the admin.users accessPhrase ("${accessPhrase("admin.users")}") is not on screen for a viewer`);
  });

  test("a viewer's press of ADD USER opens nothing and fires nothing", () => {
    const add = byId(host, "users-add");
    assert(add != null, "no ADD USER button - the fixture is wrong, not the component");
    click(add);
    assert(byId(host, "users-add-form") == null,
      "the add-user form opened for a viewer - the lock did not refuse the press");
    const fired = asked.slice(asked0).filter((a) => a.url.includes("/api/users"));
    assert(fired.length === 0,
      `a /api/users request was fired for a viewer: ${JSON.stringify(fired)}`);
  });

  act(() => { root.unmount(); });
}

// ==================================================== PEOPLE - adding a user
{
  const host = win.document.getElementById("s") as any;
  const root = createRoot(host);
  usersFixture = [];

  seed(ADMIN);
  act(() => { root.render(createElement(UsersSheet, { params: {}, depth: 0 })); });
  await settle();
  const posts0 = reqs("POST", "/api/users").length;

  click(byId(host, "users-add"));

  test("an admin's ADD USER opens the form", () => {
    assert(byId(host, "users-add-form") != null, "the add-user form did not open for an admin");
  });

  test("an incomplete draft posts nothing and says why", () => {
    click(byId(host, "users-create"));
    assert(reqs("POST", "/api/users").length === posts0,
      "an empty add-user draft was POSTed");
    assert(byId(host, "users-create").getAttribute("aria-disabled") === "true",
      "CREATE USER is live with an empty draft - newUserBlocker is not gating it");
  });

  typeInto(byId(host, "users-add-username"), "clubnight");
  typeInto(byId(host, "users-add-email"), "club@example.test");
  typeInto(byId(host, "users-add-password"), "a-long-enough-password");

  test("a complete draft leaves CREATE USER live", () => {
    assert(byId(host, "users-create").getAttribute("aria-disabled") !== "true",
      "CREATE USER is still locked with a complete draft");
  });

  click(byId(host, "users-create"));
  await settle();

  test("adding a user POSTs /api/users exactly once, with the typed draft", () => {
    const made = reqs("POST", "/api/users").slice(posts0);
    assert(made.length === 1, `expected exactly one POST /api/users, saw ${made.length}`);
    assert(made[0].body?.username === "clubnight",
      `the POST body did not carry the typed username: ${JSON.stringify(made[0].body)}`);
    assert(made[0].body?.role === "operator",
      `the POST body did not carry the picked role: ${JSON.stringify(made[0].body)}`);
  });

  act(() => { root.unmount(); });
}

// =================================================== PEOPLE - deleting a user
{
  const host = win.document.getElementById("s") as any;
  const root = createRoot(host);
  usersFixture = [{
    id: "u1", username: "olduser", email: "old@example.test",
    role: "operator", enabled: true, created: 0,
  }];

  seed(ADMIN);
  act(() => { root.render(createElement(UsersSheet, { params: {}, depth: 0 })); });
  await settle();

  test("the seeded account renders as a row", () => {
    assert(byId(host, "users-row") != null, "no users-row - the fixture is wrong, not the component");
    assert(host.textContent.includes("olduser"), "the seeded username is not on screen");
  });

  const dels0 = reqs("DELETE", "/api/users/").length;
  click(byId(host, "users-row-delete"));
  await settle();

  test("DELETE raises a confirm and issues no request before it is answered", () => {
    assert(useStore.getState().confirm != null,
      "pressing DELETE did not push a confirm - it went straight to the API");
    assert(/olduser/.test(useStore.getState().confirm!.title),
      "the confirm does not name the account being deleted");
    assert(useStore.getState().confirm!.mode === "hold",
      "the delete confirm is not a hold - a destructive action needs the hold pattern");
    assert(reqs("DELETE", "/api/users/").length === dels0,
      "a DELETE was issued before the confirm was answered");
  });

  act(() => { useStore.getState().resolveConfirm(false); });
  await settle();

  test("cancelling the confirm deletes nothing", () => {
    assert(reqs("DELETE", "/api/users/").length === dels0,
      "a DELETE was issued after the confirm was CANCELLED");
  });

  click(byId(host, "users-row-delete"));
  await settle();
  act(() => { useStore.getState().resolveConfirm(true); });
  await settle();

  test("confirming the delete issues exactly one DELETE for that account", () => {
    const made = reqs("DELETE", "/api/users/").slice(dels0);
    assert(made.length === 1, `expected exactly one DELETE, saw ${made.length}`);
    assert(made[0].url.endsWith("/api/users/u1"),
      `the DELETE went to the wrong account: ${made[0].url}`);
  });

  act(() => { root.unmount(); });
  usersFixture = [];
}

// ========================================== SIGN-IN METHODS - role honesty
{
  const host = win.document.getElementById("s") as any;
  const root = createRoot(host);

  seed(ADMIN);
  act(() => { root.render(createElement(AuthMethodsSheet, { params: {}, depth: 0 })); });
  await settle();

  test("the SIGN-IN METHODS sheet renders its marker and the rebuilt editor", () => {
    assert(byId(host, "settings-authMethods") != null, "no [data-testid=settings-authMethods]");
    assert(byId(host, "auth-editor") != null, "the rebuilt AuthMethodsEditor did not render");
    assert(byId(host, "auth-local") != null, "no Enable local accounts switch");
    assert(byId(host, "auth-google") != null, "no Enable Google sign-in switch");
    assert(byId(host, "auth-ttl") != null, "no Session length field");
    assert(byId(host, "auth-firstrun") != null, "no Allow first-run setup switch");
    assert(byId(host, "auth-loopback") != null, "no Trust loopback as admin switch");
    assert(byId(host, "auth-breakglass") != null, "no Break-glass admin token disclosure");
  });

  // ROLE HONESTY. The expected list is read out of `lib/caps.ts` ITSELF rather
  // than typed here: a picker that hard-codes four roles and a test that
  // hard-codes the same four agree with each other and with nothing else.
  // `ROLE_CAPS` is module-private, so the source is parsed - the same idiom
  // `shellCss.test.ts` uses to assert against `next.css`.
  const { readFileSync } = await import("node:fs");
  const capsSrc = readFileSync(new URL("../../../../lib/caps.ts", import.meta.url), "utf8");
  const block = /const ROLE_CAPS[^{]*\{([\s\S]*?)\n\};/.exec(capsSrc);
  const roleCapsKeys = block
    ? Array.from(block[1].matchAll(/^ {2}(\w+):/gm)).map((m) => m[1])
    : [];

  test("ROLE HONESTY: the default-role picker lists exactly the roles in ROLE_CAPS", () => {
    assert(roleCapsKeys.length >= 4,
      `could not read ROLE_CAPS out of lib/caps.ts (found ${roleCapsKeys.length} keys) - `
      + "the parse is broken, so the comparison below would be vacuous");
    const picker = byId(host, "auth-default-role");
    assert(picker != null, "no default-role picker on screen");
    const shown = qa(picker, "[data-value]").map((el: any) => el.getAttribute("data-value"));
    // `deny` is not a role: it is `default_role: null` on the wire, the refusal
    // to grant one. It is the only option in the picker that is not in the
    // table, and it is asserted separately so it cannot hide a missing role.
    assert(shown[0] === "deny",
      `the picker's first option should be the refusal "deny", saw "${shown[0]}"`);
    const roles = shown.slice(1);
    assert(JSON.stringify(roles) === JSON.stringify(roleCapsKeys),
      `the default-role picker offers ${JSON.stringify(roles)} but lib/caps.ts's `
      + `ROLE_CAPS holds ${JSON.stringify(roleCapsKeys)}`);
  });

  act(() => { root.unmount(); });

  // Same source, second picker: the legacy panels each declared their own ROLES
  // array, which is exactly how two pickers drift apart.
  const addHost = win.document.getElementById("u") as any;
  const addRoot = createRoot(addHost);
  act(() => { addRoot.render(createElement(UsersSheet, { params: {}, depth: 0 })); });
  await settle();
  click(byId(addHost, "users-add"));

  test("ROLE HONESTY: the add-user role picker uses the same table", () => {
    const picker = byId(addHost, "users-add-role");
    assert(picker != null, "no role picker in the add-user form");
    const shown = qa(picker, "[data-value]").map((el: any) => el.getAttribute("data-value"));
    assert(JSON.stringify(shown) === JSON.stringify(roleCapsKeys),
      `the add-user role picker offers ${JSON.stringify(shown)} but ROLE_CAPS holds `
      + `${JSON.stringify(roleCapsKeys)}`);
  });

  act(() => { addRoot.unmount(); });
}

// ====================================== SIGN-IN METHODS - secret hygiene
{
  const host = win.document.getElementById("s") as any;
  const root = createRoot(host);

  // The rig says a token EXISTS; the payload also carries a value, standing in
  // for a redaction that fails or a field a later server adds. Neither may
  // reach the box.
  const LEAKED = "a-token-the-server-should-never-have-sent-back";
  seed(ADMIN, {
    config: {
      ...baseSeed().config,
      auth: {
        ...baseSeed().config.auth,
        admin_token_configured: true,
        admin_token: LEAKED,
      },
    },
  });
  act(() => { root.render(createElement(AuthMethodsSheet, { params: {}, depth: 0 })); });
  await settle();

  test("the break-glass disclosure says a token is stored without showing it", () => {
    const disc = byId(host, "auth-breakglass");
    assert(disc != null, "no break-glass disclosure");
    assert(/SET ON THE RIG/.test(disc.textContent),
      "the disclosure does not say a token is stored, which is the only thing the rig will tell us");
    assert(!host.textContent.includes(LEAKED),
      "the seeded token value is rendered somewhere on the sheet");
  });

  test("SECRET HYGIENE: the break-glass input never echoes a server value", () => {
    const head = q(byId(host, "auth-breakglass"), "button[aria-expanded]");
    assert(head != null, "the disclosure has no summary button to press");
    click(head);
    const input = byId(host, "auth-breakglass-input");
    assert(input != null, "the break-glass input did not render when the disclosure opened");
    assert(input.value === "",
      `the break-glass input was seeded from the server block: "${input.value}"`);
    assert(input.type === "password",
      `the break-glass input is not masked (type=${input.type})`);
    assert(!host.textContent.includes(LEAKED),
      "the seeded token value reached the DOM once the disclosure was open");
  });

  test("a too-short break-glass token is refused before the round trip", () => {
    const input = byId(host, "auth-breakglass-input");
    typeInto(input, "short");
    assert(byId(host, "auth-breakglass-error") != null,
      "a 5-byte token raised no error - the 32-byte server rule is not stated");
    assert(byId(host, "auth-save").getAttribute("aria-disabled") === "true",
      "SAVE METHODS is live with a token the server would refuse");
  });

  act(() => { root.unmount(); });
}

// ================================================ SIGN-IN METHODS - non-admin
{
  const host = win.document.getElementById("s") as any;
  const root = createRoot(host);
  const asked0 = asked.length;

  seed(OPERATOR);
  act(() => { root.render(createElement(AuthMethodsSheet, { params: {}, depth: 0 })); });
  await settle();

  test("a non-admin sees the sign-in editor read-only and fires no request", () => {
    assert(byId(host, "auth-editor") != null, "the sign-in editor did not render for a non-admin");
    assert(host.textContent.includes(accessPhrase("admin.users")),
      "the admin.users accessPhrase is not on screen for a non-admin");
    const fired = asked.slice(asked0).filter((a) => a.url.includes("/api/users") || a.url.includes("/api/auth/config"));
    assert(fired.length === 0, `a request was fired for a non-admin: ${JSON.stringify(fired)}`);
  });

  act(() => { root.unmount(); });
}

// ============================================================ ACCOUNT SHEET
{
  const host = win.document.getElementById("s") as any;
  const root = createRoot(host);

  seed(ADMIN, { authMethods: { methods: ["local"], google_configured: false, first_run: false } });
  act(() => { root.render(createElement(AccountSheet, { params: {}, depth: 0 })); });
  await settle();

  test("the SIGNED IN sheet renders its marker, identity and sign-out", () => {
    assert(byId(host, "settings-account") != null, "no [data-testid=settings-account]");
    assert(byId(host, "account-identity") != null, "the rebuilt AccountIdentity did not render");
    assert(byId(host, "account-identity-line").textContent.includes("night-owner@example.test"),
      "the identity line does not carry the signed-in email");
    assert(byId(host, "account-signout") != null,
      "no sign-out control for a principal that carries an identity");
  });

  act(() => { root.unmount(); });
}

{
  const host = win.document.getElementById("s") as any;
  const root = createRoot(host);

  seed(VIEWER);
  act(() => { root.render(createElement(AccountSheet, { params: {}, depth: 0 })); });
  await settle();

  test("a viewer's SIGNED IN sheet carries the read-only session explainer", () => {
    assert(byId(host, "account-readonly") != null, "no read-only session card for a viewer");
    assert(/You're viewing in read-only mode/.test(host.textContent),
      "the read-only session headline is not on screen");
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
    assert(checkBtn.disabled === true || checkBtn.getAttribute("aria-disabled") === "true",
      "Check now is not refused for a principal without system.update");
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
