// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15UsersPanelStepUp.test.tsx - the CLASSIC Users panel over the relay (#685,
// WP-105), MOUNTED and pressed.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/settings/__tests__/w15UsersPanelStepUp.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The rig used to refuse every /api/users request on a relayed session. It now
// answers the list and four changes behind a sign-in under five minutes old
// (`server/astrodeck/auth/deps.py`'s `require_recent_signin`), and this panel -
// the one `#/classic` serves, and the root UI today - has to say so and recover:
//
//   * the notice appears ONLY on the relay, saying what the rule is for;
//   * a `step_up_required` refusal (from a ROW or from the ADD form) opens a
//     sign-in form, and a local sign-in posts /auth/local and closes it;
//   * a capability 403 (no `code`) does NOT, because signing in again cannot
//     give anyone a role;
//   * what the rig still refuses over the relay is LOCKED, so a password is never
//     typed into a form the relay would carry only to have it refused: RESET is
//     off, ADD USER is Google-only with viewer and operator, an administrator's
//     row is off, and the admin and syncer options are off on the other rows. The
//     LAN case beside them pins that none of this applies off the relay.
//
// NAMED SABOTAGES, each of which turns a named case RED:
//   * drop `markRequired()` from `report` -> "a step_up_required refusal from a
//     row opens the sign-in form";
//   * drop the `onStepUp?.()` call in AddUserForm's catch -> "...from the ADD
//     form" goes red;
//   * make `isStepUpRequired` test `status === 403` -> "a capability refusal does
//     not open it" goes red;
//   * render the notice unconditionally -> "on the LAN there is no notice" goes
//     red;
//   * `resetLock` always undefined -> "over the relay RESET is locked" goes red;
//     `resetLock` always set -> "on the LAN nothing is locked" goes red;
//   * `adminLock` always undefined -> "...an administrator's row is locked" goes
//     red; `refused` always false in the role options -> the same case, on the
//     options;
//   * `shownMethod` back to the typed method -> "over the relay ADD USER is
//     Google-only" goes red (a password field is on the page); `roleChoices` back
//     to every role -> the same case, on the role options;
//   * drop the Google-off blocker -> "...with Google off, ADD USER says there is
//     nothing it can create" goes red.
//
// Run 2026-10-07 from a byte backup, each restored byte-identically (sha256
// compared). Observed:
//   * drop `markRequired()` from `report`: "w15UsersPanelStepUp.test: 4/6 passed",
//     "x a step_up_required refusal from a row opens the sign-in form: the
//     refusal did not open the sign-in form (expected required, got idle)";
//   * drop `onStepUp?.()` from AddUserForm: "5/6 passed", "x a step_up_required
//     refusal from the ADD form opens the sign-in form: the ADD form's refusal
//     did not open the sign-in form (expected required, got idle)";
//   * `isStepUpRequired` by status: "5/6 passed", "x a capability refusal does
//     not open it: a capability refusal opened the sign-in form (expected idle,
//     got required)";
//   * notice unconditional: "5/6 passed", "x on the LAN there is no notice, and
//     the list is read: a LAN session was shown the relay's sign-in notice
//     (expected null, got [object HTMLDivElement])";
//   * `resetLock` undefined: "10/11 passed", "x over the relay RESET is locked and
//     opens no password field: RESET is armed over the relay, where the rig
//     refuses every reset (expected true, got false)"; `resetLock` always set:
//     "10/11", "x on the LAN nothing is locked ...: RESET is locked on the LAN
//     (expected false, got true)";
//   * `adminLock` undefined: "10/11", "x over the relay an administrator's row is
//     locked ...: an administrator's role is armed over the relay (expected true,
//     got false)"; `refused` false: "10/11", "x ...: the guest's role options are
//     not viewer and operator only (expected [["viewer",false],["syncer",true],...
//     got [["viewer",false],["syncer",false],...)";
//   * `shownMethod` = typed method: "10/11", "x over the relay ADD USER is
//     Google-only ...: the add form has a password field over the relay (expected
//     null, got [object HTMLInputElement])"; `roleChoices` = ROLES: "10/11", "x
//     ...: the add form offers a role the rig refuses over the relay";
//   * Google-off blocker dropped: "10/11", "x over the relay with Google off, ADD
//     USER says there is nothing it can create: the blocker is not the relay's own
//     sentence (expected Over the relay an account can only be created for Google
//     sign-in, ..., got Pick a username.)".

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import("jsdom");
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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- network
interface Ask { url: string; method: string; body?: string }
const asks: Ask[] = [];
type Answer = "ok" | "step_up" | "capability";
const scenario: { patch: Answer; create: Answer } = { patch: "ok", create: "ok" };
const ok = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
const refuse = (data: unknown) =>
  ({ ok: false, status: 403, statusText: "Forbidden", json: async () => data });
const STEP_UP_BODY = { detail: {
  code: "step_up_required",
  detail: "Sign in again to manage people from outside the LAN.",
} };
const USERS = [
  { id: "u1", username: "bear", email: "bear@rig", role: "admin", enabled: true, created: 1 },
  { id: "u2", username: "guest@rig", email: "guest@rig", role: "viewer", enabled: true, created: 2 },
];

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asks.push({ url: u, method, body: typeof init?.body === "string" ? init.body : undefined });
  if (u.includes("/api/me")) return ok({ role: "admin", email: "admin@rig", caps: ["admin.users"] });
  if (u.includes("/api/users")) {
    const answer = method === "PATCH" ? scenario.patch : method === "POST" ? scenario.create : "ok";
    if (answer === "step_up") return refuse(STEP_UP_BODY);
    if (answer === "capability") return refuse({ detail: "capability not held" });
    if (method === "PATCH") return ok({ ...USERS[1], role: "operator" });
    if (method === "POST") return ok({ ...USERS[1], id: "u3", username: "new@rig", email: "new@rig" });
    return ok({ users: USERS });
  }
  return ok({ ok: true });
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { noteRemoteStatus, resetRelayForTests } = await import("../../../next/lib/relay");
const UsersPanel = (await import("../UsersPanel")).default;
const { LOCAL_ONLY_REASON } = await import("../../../next/lib/gate");
const { PEOPLE_RELAY_NO_GOOGLE } = await import(
  "../../../next/hubs/settings/tuning/people/peopleModel");

// ------------------------------------------------------------------ harness
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

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const q = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const tick = () => new Promise((r) => setTimeout(r, 0));
/** Run `fn` and keep ONE act scope open across the chain of awaits it starts. */
const run = async (fn: () => void) => {
  await act(async () => { fn(); for (let i = 0; i < 6; i++) await tick(); });
};
const click = (el: any) => run(() => {
  el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
});
const typeInto = (el: any, value: string) => run(() => {
  const proto = el instanceof win.HTMLSelectElement
    ? win.HTMLSelectElement.prototype : win.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
  el.dispatchEvent(new win.Event(el instanceof win.HTMLSelectElement ? "change" : "input",
    { bubbles: true }));
});

async function mount(methods: string[] = ["local", "google"]): Promise<void> {
  await act(async () => { root.render(null); });
  await act(async () => {
    useStore.setState({
      principal: { role: "admin", email: "admin@rig", caps: ["admin.users"] },
      authMethods: { methods, google_configured: methods.includes("google"), first_run: false },
      authGate: "open",
      toasts: [],
    } as never);
  });
  scenario.patch = "ok";
  scenario.create = "ok";
  asks.length = 0;
  await act(async () => { root.render(createElement(UsersPanel)); });
  for (let i = 0; i < 3; i++) await act(async () => { await tick(); });
}
const state = () => q("users-stepup")?.getAttribute("data-state");
/** The `<li>` of the person whose username is `name`. */
const rowOf = (name: string): any => {
  const li = Array.from(container.querySelectorAll("li"))
    .find((el: any) => String(el.textContent).includes(name)) as any;
  assert(li != null, `no row for ${name}`);
  return li;
};
const resetOf = (row: any): any => row.querySelector("button[aria-expanded]");
const deleteOf = (row: any): any => row.querySelector("button.btn-danger");
const toggleOf = (row: any): any => row.querySelector('button[role="switch"]');
const roleOptions = (sel: any): string =>
  JSON.stringify(Array.from(sel.options).map((o: any) => [o.value, o.disabled]));
const openAddForm = async (): Promise<any> => {
  const add = Array.from(container.querySelectorAll("button"))
    .find((b: any) => /Add user/.test(String(b.textContent))) as any;
  assert(add != null, "no Add user button");
  await click(add);
  const form = container.querySelector("form:not([data-testid])");
  assert(form != null, "the add form did not open");
  return form;
};
/** Change the SECOND person's role through the real <select>. */
const changeGuestRole = async () => {
  const sel = container.querySelector('select[aria-label="Role for guest@rig"]');
  assert(sel != null, "the role select for the second person is missing");
  await typeInto(sel, "operator");
};

// ---------------------------------------------------------------------- LAN
await mount();
await testAsync("on the LAN there is no notice, and the list is read", async () => {
  assert(asks.some((a) => a.method === "GET" && a.url.includes("/api/users")),
    "the list was not requested");
  eq(q("users-stepup"), null, "a LAN session was shown the relay's sign-in notice");
});

await testAsync("on the LAN nothing is locked: RESET, all four roles, an administrator's row, a password", async () => {
  const guest = rowOf("guest@rig");
  eq(resetOf(guest).disabled, false, "RESET is locked on the LAN");
  eq(roleOptions(guest.querySelector("select")),
    JSON.stringify([["viewer", false], ["syncer", false], ["operator", false], ["admin", false]]),
    "a role option is off on the LAN");
  const admin = rowOf("bear");
  eq(admin.querySelector("select").disabled, false, "an administrator's role is locked on the LAN");
  eq(toggleOf(admin).disabled, false, "an administrator's enabled switch is locked on the LAN");
  eq(deleteOf(admin).disabled, false, "an administrator's DELETE is locked on the LAN");
  const form = await openAddForm();
  eq(roleOptions(form.querySelectorAll("select")[0]),
    JSON.stringify([["password", false], ["google", false]]),
    "a sign-in method is off on the LAN");
  assert(form.querySelector('input[type="password"]') != null,
    "the LAN add form has no password field");
  eq(roleOptions(form.querySelectorAll("select")[1]),
    JSON.stringify([["viewer", false], ["syncer", false], ["operator", false], ["admin", false]]),
    "a role is missing from the LAN add form");
});

// -------------------------------------------------------------------- relay
await act(async () => { noteRemoteStatus({ via: "relay" }); });
await mount();

await testAsync("on the relay the list is read and the notice says what the rule is for", async () => {
  assert(asks.some((a) => a.method === "GET" && a.url.includes("/api/users")),
    "the list was not requested over the relay");
  eq(state(), "idle", "the notice is not on the page over the relay");
  const text = String(q("users-stepup").textContent);
  assert(/less than 5 minutes old/.test(text), `the window is not stated: "${text}"`);
  assert(/replay/.test(text), `the notice does not say why: "${text}"`);
  assert(/Passwords, administrators and email changes need the LAN/.test(text),
    `the notice does not say what stays on the LAN: "${text}"`);
});

await testAsync("a step_up_required refusal from a row opens the sign-in form", async () => {
  scenario.patch = "step_up";
  await changeGuestRole();
  eq(state(), "required", "the refusal did not open the sign-in form");
  const text = String(q("users-stepup").textContent);
  assert(/SIGN IN AGAIN/.test(text), `the notice does not say SIGN IN AGAIN: "${text}"`);
  assert(q("users-stepup-google") != null, "no Google sign-in offered");
  assert(q("users-stepup-password") != null, "no local sign-in offered");
  assert(/Sign in again to manage people from outside the LAN/.test(String(container.textContent)),
    "the failed change did not say what happened");
});

await testAsync("a local sign-in posts /auth/local and closes the form", async () => {
  scenario.patch = "ok";
  asks.length = 0;
  await typeInto(q("users-stepup-username"), "bear");
  await typeInto(q("users-stepup-password"), "correct-horse");
  await click(q("users-stepup-submit"));
  const post = asks.find((a) => a.method === "POST" && a.url.includes("/auth/local"));
  assert(post != null, "the sign-in never reached /auth/local");
  eq(JSON.parse(post!.body ?? "{}").username, "bear", "the typed username was not sent");
  eq(JSON.parse(post!.body ?? "{}").password, "correct-horse", "the typed password was not sent");
  eq(state(), "fresh", "a successful sign-in did not leave the form");
  assert(q("users-stepup-password") == null, "the password field is still on the page");
});

await mount();
await testAsync("a step_up_required refusal from the ADD form opens the sign-in form", async () => {
  scenario.create = "step_up";
  const add = Array.from(container.querySelectorAll("button"))
    .find((b: any) => /Add user/.test(String(b.textContent))) as any;
  assert(add != null, "no Add user button");
  await click(add);
  const form = container.querySelector("form:not([data-testid])");
  assert(form != null, "the add form did not open");
  // A real-looking address: the form's own validity rule (`emailLooksValid`)
  // needs a dot in the domain, and a refused-by-the-form submit would make this
  // case fail for the wrong reason.
  await typeInto(form.querySelector('input[type="text"]'), "new@rig.example");
  await typeInto(form.querySelector('input[type="email"]'), "new@rig.example");
  await typeInto(form.querySelectorAll("select")[0], "google");
  await click(form.querySelector('button[type="submit"]'));
  assert(asks.some((a) => a.method === "POST" && a.url.endsWith("/api/users")),
    "the create never reached the rig");
  eq(state(), "required", "the ADD form's refusal did not open the sign-in form");
});

await mount();
await testAsync("a capability refusal does not open it", async () => {
  scenario.patch = "capability";
  await changeGuestRole();
  eq(state(), "idle", "a capability refusal opened the sign-in form");
  assert(/capability not held/.test(String(container.textContent)),
    "the capability refusal lost its sentence");
});

// What the rig refuses over the relay is LOCKED, so a password is never typed
// into a form whose submit the relay would carry, only to be refused.
await mount();
await testAsync("over the relay RESET is locked and opens no password field", async () => {
  const reset = resetOf(rowOf("guest@rig"));
  assert(reset != null, "the row has no RESET control");
  eq(reset.disabled, true, "RESET is armed over the relay, where the rig refuses every reset");
  eq(reset.getAttribute("title"), LOCAL_ONLY_REASON, "RESET does not say why it is locked");
  await click(reset);
  eq(container.querySelector('input[autocomplete="new-password"]'), null,
    "a password field opened over the relay");
  eq(asks.filter((a) => a.url.includes("/password")).length, 0,
    "a password reset was sent over the relay");
});

await testAsync("over the relay an administrator's row is locked and the others lose admin and syncer", async () => {
  const admin = rowOf("bear");
  eq(admin.querySelector("select").disabled, true, "an administrator's role is armed over the relay");
  eq(toggleOf(admin).disabled, true, "an administrator's enabled switch is armed over the relay");
  eq(deleteOf(admin).disabled, true, "an administrator's DELETE is armed over the relay");
  eq(deleteOf(admin).getAttribute("title"), LOCAL_ONLY_REASON, "DELETE does not say why it is locked");
  const guest = rowOf("guest@rig");
  eq(roleOptions(guest.querySelector("select")),
    JSON.stringify([["viewer", false], ["syncer", true], ["operator", false], ["admin", true]]),
    "the guest's role options are not viewer and operator only");
  eq(guest.querySelector("select").disabled, false, "a non-administrator's role is locked");
  eq(toggleOf(guest).disabled, false, "a non-administrator's enabled switch is locked");
  eq(deleteOf(guest).disabled, false, "a non-administrator's DELETE is locked");
});

await testAsync("over the relay ADD USER is Google-only, viewer or operator, and sends no password", async () => {
  const form = await openAddForm();
  eq(form.querySelector('input[type="password"]'), null, "the add form has a password field over the relay");
  const method = form.querySelectorAll("select")[0];
  eq(method.value, "google", "the add form defaults to a password account over the relay");
  eq(roleOptions(method), JSON.stringify([["password", true], ["google", false]]),
    "the password method is offered over the relay");
  eq(roleOptions(form.querySelectorAll("select")[1]),
    JSON.stringify([["viewer", false], ["operator", false]]),
    "the add form offers a role the rig refuses over the relay");
  await typeInto(form.querySelector('input[type="text"]'), "new@rig.example");
  await typeInto(form.querySelector('input[type="email"]'), "new@rig.example");
  asks.length = 0;
  await click(form.querySelector('button[type="submit"]'));
  const post = asks.find((a) => a.method === "POST" && a.url.endsWith("/api/users"));
  assert(post != null, "the create never reached the rig");
  const body = JSON.parse(post!.body ?? "{}");
  eq(body.password, "", "a password went over the relay");
  eq(body.role, "operator", "the new account was not an operator");
});

await mount(["local"]);
await testAsync("over the relay with Google off, ADD USER says there is nothing it can create", async () => {
  const form = await openAddForm();
  const submit = form.querySelector('button[type="submit"]');
  eq(submit.getAttribute("title"), PEOPLE_RELAY_NO_GOOGLE,
    "the blocker is not the relay's own sentence");
  assert(!/Set a password instead/.test(String(submit.getAttribute("title"))),
    "the blocker advises a password, which the relay cannot set");
});

await act(async () => { root.render(null); });
resetRelayForTests();

const total = passed + failed;
console.log(`w15UsersPanelStepUp.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
