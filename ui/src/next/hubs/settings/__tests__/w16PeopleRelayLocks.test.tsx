// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16PeopleRelayLocks.test.tsx - an administrator's row and the add form's
// PASSWORD method are locked with the LAN sentence over the relay, BEFORE the
// press (#761; WP-145, wave 16). Mounted: the real UsersEditor, UserRow and
// AddUserForm on a tunnelled origin.
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/w16PeopleRelayLocks.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// WHAT THE RIG REFUSES OVER THE RELAY (`auth/local_routes.py`, 403 `local_only`):
//   - PATCH /api/users/<id> when the TARGET is an admin, whatever the field
//     (role, enabled);
//   - DELETE /api/users/<id> when the target is an admin;
//   - POST /api/users carrying a password, and any role but viewer or operator;
//   - the password reset (the fence middleware).
// Wave 15 locked the last, and the admin and syncer role OPTIONS, and nothing
// else. So an administrator's role, enabled switch and DELETE, and the add form's
// password method, rendered armed and failed after the press with the rig's
// sentence. The classic UsersPanel had locked them from the start.
//
// WHAT IS WORTH ASSERTING:
//
//   THE ADMIN ROW'S THREE CONTROLS ARE LOCKED WITH THE LAN SENTENCE, and a press
//   explains and sends nothing. The sentence is `LOCAL_ONLY_REASON`, the one the
//   rig's own 403 raises as a toast, so a locked press and a refused press read
//   the same.
//
//   THE ROW NEXT TO IT IS NOT LOCKED. The rig does a non-admin's role, enable and
//   delete behind a recent sign-in, so locking them would be the over-lock that
//   takes the relay's one useful job away. Both rows are graded on ONE mount.
//
//   THE ADD FORM IS BORN ON THE METHOD THE RIG ALLOWS. The password method is
//   locked and GOOGLE ONLY is the selected one, so there is no password field to
//   type into: a password typed here would cross the relay on its way to being
//   refused, which is the whole reason the control is locked rather than
//   discovered. The create that goes out carries no password.
//
//   WITH GOOGLE OFF THE FORM SAYS SO, in the sentence written for the relay
//   (`PEOPLE_RELAY_NO_GOOGLE`), not the generic "set a password instead" the
//   relay cannot follow.
//
//   ON THE LAN NOTHING IS LOCKED FOR THE LAN'S SAKE. Same rows, same form.
//
// MUTANTS RUN (each from a byte backup, restored byte-identically with sha256
// compared, the mutant text grepped out). The failing assertion each produced,
// verbatim (the file is 8 cases; "N/8" is what the mutant left standing):
//
//   X1 "the admin row's role is armed" (UserRow: the role Segmented's
//      `lockedReason={rowLock}` made `lockedReason={lockedReason}`), 7/8:
//      "x relay: an administrator's role, enabled switch and DELETE are locked
//      with the LAN sentence: the admin's role control renders ARMED over the
//      relay - the rig answers 403 local_only for an administrator (expected
//      true, got null)"
//   X2 "the admin row's enabled switch is armed" (the Switch, same change), 7/8:
//      "... the admin's enabled switch renders ARMED over the relay - the rig
//      answers 403 local_only for an administrator (expected true, got null)"
//   X3 "the admin row's DELETE is armed" (the DELETE ActionButton, same change),
//      7/8: "... the admin's DELETE renders ARMED over the relay - the rig
//      answers 403 local_only for an administrator (expected true, got null)"
//   X4 "every row is locked" (`adminLock` made to ignore the role), 7/8:
//      "x relay: the row beside it is not locked (the rig does a non-admin's
//      role, enable and delete behind a sign-in): the guest's role control is
//      locked over the relay, but the rig does it behind a recent sign-in"
//   X5 "the password method is armed" (AddUserForm: the password option's
//      `lockedReason: lanOnlyReason` made `null`), 7/8:
//      "x relay: the password method is locked with the LAN sentence and GOOGLE
//      ONLY is the one selected: the PASSWORD method renders ARMED over the relay
//      - the rig refuses a create with a password (expected true, got null)"
//   X6 "the form is born on the password method" (`shownMethod` made `method`),
//      5/8: "x relay: the password method is locked ...: the form opened on the
//      locked PASSWORD method instead of on GOOGLE ONLY (expected true, got
//      false)", and "x relay: a password typed on the LAN is not sent once the
//      origin is the relay: the password typed on the LAN crossed the relay:
//      {"username":"new@rig","email":"new@rig.example","password":"correct-horse",
//      "role":"operator"}"
//   X7 "the generic no-Google sentence" (the relay blocker removed), 7/8:
//      "x relay with Google off: CREATE says what the relay can do, not 'set a
//      password instead': CREATE does not say why nothing can be created over the
//      relay (expected Over the relay an account can only be created for Google
//      sign-in, and Google sign-in is not configured on this rig. Add this person
//      at the rig, or set up Google first., got Google sign-in is not configured,
//      so this account would have no way to sign in. Set a password instead, or
//      enable Google first.)"
//
// Before the change the file read 3/8 (the three cases that assert what is NOT
// locked passed), every relay lock case failing with the ARMED messages above.

/* eslint-disable @typescript-eslint/no-explicit-any */

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
// The URL is the LAN one on purpose (`relayFenceDom.test.tsx` says why):
// `noteRemoteStatus` supplies the rig's own `via`, the way a real session does.
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLAnchorElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "PointerEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "Image",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body?: string }
const asks: Ask[] = [];
const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asks.push({ url: u, method, body: typeof init?.body === "string" ? init.body : undefined });
  if (u.includes("/api/users")) {
    if (method === "POST") {
      return { ok: true, status: 201, statusText: "Created", json: async () => (
        { id: "u9", username: "new@rig", email: "new@rig.example", role: "operator", enabled: true }) };
    }
    if (method === "PATCH" || method === "DELETE") return ok({ ok: true });
    return ok({ users: [
      { id: "u1", username: "root", email: "root@rig", role: "admin", enabled: true },
      { id: "u2", username: "guest@rig", email: "guest@rig", role: "viewer", enabled: true },
    ] });
  }
  if (u.includes("/api/me")) return ok({ role: "admin", email: "me@rig", caps: ALL_CAPS });
  return ok({ ok: true });
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { LOCAL_ONLY_REASON } = await import("../../../lib/gate");
const { noteRemoteStatus, resetRelayForTests } = await import("../../../lib/relay");
const { UsersEditor } = await import("../tuning/people");
const { PEOPLE_RELAY_NO_GOOGLE } = await import("../tuning/people/peopleModel");

// -------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const q = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const click = async (el: any) => {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
};
const typeInto = async (el: any, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
};

const ALL_CAPS = [
  "view.status", "view.preview", "view.media", "view.weather", "view.site_precise",
  "view.site_derived", "control.capture", "control.guide", "control.mount",
  "control.power", "config.backend", "config.safety", "config.solar_override",
  "config.site_optics", "config.alerts", "admin.users", "system.update",
];

function seed(googleOn: boolean): void {
  useStore.setState({
    principal: { role: "admin", email: "me@rig", caps: ALL_CAPS },
    authGate: "open",
    wsPhase: "up",
    wsConnected: true,
    equipConnected: true,
    status: { connected: {}, looping: false, busy: null, busy_lanes: [] },
    sequence: { state: "idle" },
    authMethods: googleOn
      ? { methods: ["local", "google"], google_configured: true, first_run: false }
      : { methods: ["local"], google_configured: false, first_run: false },
    toasts: [],
  } as never);
}

const mount = async (googleOn = true): Promise<void> => {
  await act(async () => { root.render(null); });
  await settle();
  seed(googleOn);
  asks.length = 0;
  await act(async () => { root.render(createElement(UsersEditor)); });
  await settle();
  await act(async () => { useStore.setState({ toasts: [] } as never); });
};

const writes = () => asks.filter((a) => a.method !== "GET");
const toastTitles = () =>
  ((useStore.getState() as any).toasts as Array<{ title?: string }>).map((t) => t.title);
const rows = () => container.querySelectorAll('[data-testid="users-row"]');
const part = (row: any, id: string) => row.querySelector(`[data-testid="${id}"]`) as any;
const optionOf = (scope: any, group: string, value: string) =>
  scope.querySelector(`[data-testid="${group}"] [data-value="${value}"]`) as any;
const locked = (el: any): boolean => el?.getAttribute("aria-disabled") === "true";

/** One control that must be locked with the LAN sentence and must send nothing. */
async function refusesWithLanSentence(what: string, el: any, pressOn: any = el): Promise<void> {
  assert(el != null, `${what} is not on the row - nothing may be hidden`);
  eq(el.hasAttribute("disabled"), false, `${what} uses the native disabled attribute`);
  eq(el.getAttribute("aria-disabled"), "true",
    `${what} renders ARMED over the relay - the rig answers 403 local_only for an administrator`);
  eq(el.getAttribute("title"), LOCAL_ONLY_REASON, `${what} names the wrong blocker`);
  await act(async () => { useStore.setState({ toasts: [] } as never); });
  asks.length = 0;
  await click(pressOn);
  assert(toastTitles().includes(LOCAL_ONLY_REASON),
    `${what} refused in silence: toasts were ${JSON.stringify(toastTitles())}`);
  eq(writes().length, 0, `${what} reached the rig anyway: ${JSON.stringify(writes())}`);
}

// ====================================================== the origin under test
noteRemoteStatus({ via: "relay" });

// ====================================================== the administrator's row
await mount();

await testAsync("relay: an administrator's role, enabled switch and DELETE are locked with the LAN sentence", async () => {
  eq(rows().length, 2, "the list did not render both people");
  const admin = rows()[0];
  await refusesWithLanSentence("the admin's role control", part(admin, "users-row-role"),
    optionOf(admin, "users-row-role", "viewer"));
  await refusesWithLanSentence("the admin's enabled switch", part(admin, "users-row-enabled"));
  await refusesWithLanSentence("the admin's DELETE", part(admin, "users-row-delete"));
});

await testAsync("relay: the row beside it is not locked (the rig does a non-admin's role, enable and delete behind a sign-in)", async () => {
  const guest = rows()[1];
  const armed: Array<[string, any]> = [
    ["the guest's role control", part(guest, "users-row-role")],
    ["the guest's OPERATOR option", optionOf(guest, "users-row-role", "operator")],
    ["the guest's enabled switch", part(guest, "users-row-enabled")],
    ["the guest's DELETE", part(guest, "users-row-delete")],
  ];
  for (const [what, el] of armed) {
    assert(el != null, `${what} is not on the row`);
    assert(!locked(el), `${what} is locked over the relay, but the rig does it behind a recent sign-in`);
  }
  // And the pair still holds: the admin's RESET was locked before this change and must stay so.
  eq(part(rows()[0], "users-row-reset").getAttribute("title"), LOCAL_ONLY_REASON,
    "the admin's RESET lost its LAN lock");
});

await testAsync("LAN: the same administrator row locks nothing for the LAN's sake", async () => {
  act(() => { noteRemoteStatus({ via: "direct" }); });
  try {
    await mount();
    const admin = rows()[0];
    for (const [what, id] of [
      ["the role control", "users-row-role"], ["the enabled switch", "users-row-enabled"],
      ["DELETE", "users-row-delete"], ["RESET", "users-row-reset"],
    ] as const) {
      assert(part(admin, id) != null, `${what} is not on the row`);
      assert(!locked(part(admin, id)), `${what} is locked on the LAN, where the rig does it`);
    }
  } finally {
    act(() => { noteRemoteStatus({ via: "relay" }); });
  }
});

// ============================================================ the add form
const openForm = async (): Promise<any> => {
  await click(q("users-add"));
  const form = q("users-add-form");
  assert(form != null, "ADD USER did not open its form");
  return form;
};

await testAsync("relay: the password method is locked with the LAN sentence and GOOGLE ONLY is the one selected", async () => {
  await mount();
  const form = await openForm();
  const password = optionOf(form, "users-add-method", "password");
  const google = optionOf(form, "users-add-method", "google");
  assert(password != null && google != null, "the method control is missing an option - nothing may be hidden");
  eq(password.getAttribute("aria-disabled"), "true",
    "the PASSWORD method renders ARMED over the relay - the rig refuses a create with a password");
  eq(password.getAttribute("title"), LOCAL_ONLY_REASON, "the PASSWORD method names the wrong blocker");
  assert(!locked(google), "GOOGLE ONLY is locked over the relay, where it is the one method the rig allows");
  eq(google.getAttribute("aria-checked"), "true",
    "the form opened on the locked PASSWORD method instead of on GOOGLE ONLY");
  eq(password.getAttribute("aria-checked"), "false", "PASSWORD is shown as the selected method");
  eq(q("users-add-password"), null,
    "a password field is on the form over the relay: a typed password would cross it before the refusal");
  // A press on the locked option explains and leaves the choice where it was.
  await act(async () => { useStore.setState({ toasts: [] } as never); });
  await click(password);
  assert(toastTitles().includes(LOCAL_ONLY_REASON),
    `the locked PASSWORD method refused in silence: ${JSON.stringify(toastTitles())}`);
  eq(q("users-add-password"), null, "pressing the locked PASSWORD method opened its password field");
  eq(optionOf(form, "users-add-method", "google").getAttribute("aria-checked"), "true",
    "pressing the locked PASSWORD method moved the selection");
});

await testAsync("relay: CREATE sends a Google-only account, with no password", async () => {
  await mount();
  await openForm();
  await typeInto(q("users-add-username"), "new@rig");
  await typeInto(q("users-add-email"), "new@rig.example");
  asks.length = 0;
  await click(q("users-create"));
  const posts = writes().filter((w) => w.method === "POST" && w.url.endsWith("/api/users"));
  eq(posts.length, 1, "CREATE did not reach the rig once");
  const body = JSON.parse(posts[0].body ?? "{}");
  eq(body.password, "", `the create carried a password over the relay: ${JSON.stringify(body)}`);
  eq(body.role, "operator", "the default role changed");
});

await testAsync("relay: a password typed on the LAN is not sent once the origin is the relay", async () => {
  // The origin can change under an open form (the rig's own `via` arrives late).
  act(() => { noteRemoteStatus({ via: "direct" }); });
  await mount();
  await openForm();
  await typeInto(q("users-add-username"), "new@rig");
  await typeInto(q("users-add-email"), "new@rig.example");
  await typeInto(q("users-add-password"), "correct-horse");
  act(() => { noteRemoteStatus({ via: "relay" }); });
  await settle();
  asks.length = 0;
  await click(q("users-create"));
  const posts = writes().filter((w) => w.method === "POST" && w.url.endsWith("/api/users"));
  eq(posts.length, 1, "CREATE did not reach the rig once");
  assert(!/correct-horse/.test(posts[0].body ?? ""),
    `the password typed on the LAN crossed the relay: ${posts[0].body}`);
});

await testAsync("relay with Google off: CREATE says what the relay can do, not 'set a password instead'", async () => {
  await mount(false);
  await openForm();
  await typeInto(q("users-add-username"), "new@rig");
  await typeInto(q("users-add-email"), "new@rig.example");
  const create = q("users-create");
  eq(create.getAttribute("aria-disabled"), "true", "CREATE is armed with no way to create an account");
  eq(create.getAttribute("title"), PEOPLE_RELAY_NO_GOOGLE,
    "CREATE does not say why nothing can be created over the relay");
  assert(!/Set a password/.test(String(create.getAttribute("title"))),
    "CREATE tells the relay to set a password, advice it cannot follow");
});

await testAsync("LAN: the add form is born on PASSWORD, with its field, and locks nothing for the LAN's sake", async () => {
  act(() => { noteRemoteStatus({ via: "direct" }); });
  try {
    await mount();
    const form = await openForm();
    eq(optionOf(form, "users-add-method", "password").getAttribute("aria-checked"), "true",
      "the LAN form is not born on PASSWORD");
    assert(!locked(optionOf(form, "users-add-method", "password")), "PASSWORD is locked on the LAN");
    assert(q("users-add-password") != null, "the LAN form has no password field");
  } finally {
    act(() => { noteRemoteStatus({ via: "relay" }); });
  }
});

await act(async () => { root.render(null); });
resetRelayForTests();

const total = passed + failed;
console.log(`w16PeopleRelayLocks.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
if (failed) process.exitCode = 1;
