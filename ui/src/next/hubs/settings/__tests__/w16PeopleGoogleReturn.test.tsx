// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16PeopleGoogleReturn.test.tsx - SIGN IN WITH GOOGLE, pressed from PEOPLE,
// asks the rig to bring the person back to PEOPLE (#733; WP-145, wave 16, the
// UI half). Mounted: the real UsersEditor on a tunnelled origin.
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/w16PeopleGoogleReturn.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// THE DEFECT. The Google callback redirects to the home screen
// (`auth/routes.py::_post_login_path`), so after a step-up the person had to
// open Settings, then People, and repeat the change. The rig half (the callback
// reads a validated return from the signed pre-auth cookie) is in `auth/routes.py`,
// which is outside this work package; it is in WP-145's `blocked_on` with its own
// tests, run in a scratch copy. THIS file is what the page does: it sends the
// person to `/auth/login?return=<the People route>` instead of the bare `/auth/login`
// `useStepUp().signInGoogle` sends.
//
// WHAT IS WORTH ASSERTING
//
//   THE PRESS CARRIES THE RETURN. The address the browser is sent to is the
//   login route with `return` set to the People route, percent-encoded, and it
//   keeps the relay's mount prefix (`u()`), because the rig has to be reached
//   through the relay for the callback to be. The test runs ON that mount
//   (`/h/home-1/`), the one place the prefix is not empty.
//
//   THE RETURN IS A BARE FRAGMENT, never a path or an address. `#/settings/users/users`
//   has no scheme, host or path, so the rig can only ever append it to a base it
//   chose itself: the shape that cannot be an open redirect, and the one the rig's
//   validator accepts.
//
//   THE RETURN IS A ROUTE THE APP REALLY HAS. The constant is parsed by the
//   router: settings hub, USERS sub-nav, the `users` sheet that holds the
//   PEOPLE editor. A router that renames any of them breaks this file instead
//   of sending the person to the sky screen.
//
// MUTANTS RUN (each from a byte backup of UsersEditor.tsx, restored
// byte-identically with sha256 compared, the mutant text grepped out). The
// failing assertion each produced, verbatim (the file is 3 cases):
//
//   G1 "the button is the shared one" (`onPress={signInGoogleToPeople}` made
//      `onPress={stepUp.signInGoogle}`: the bare /auth/login, as before), 2/3:
//      "x a Google sign-in pressed from PEOPLE goes to /auth/login with the
//      People return: the browser was not sent to the login route with the People
//      return (expected /h/home-1/auth/login?return=%23%2Fsettings%2Fusers%2Fusers,
//      got /h/home-1/auth/login)"
//   G2 "the return is dropped from the address" (`?return=` made `?r=`), 1/3:
//      "x the address keeps the mount prefix and carries the return,
//      percent-encoded: the address the browser is sent to (expected
//      /h/home-1/auth/login?return=%23%2Fsettings%2Fusers%2Fusers, got
//      /h/home-1/auth/login?r=%23%2Fsettings%2Fusers%2Fusers)"
//   G3 "the return is not encoded" (`encodeURIComponent(PEOPLE_RETURN)` made
//      `PEOPLE_RETURN`), 1/3: "... (expected
//      /h/home-1/auth/login?return=%23%2Fsettings%2Fusers%2Fusers, got
//      /h/home-1/auth/login?return=#/settings/users/users)"
//   G4 "the return is a different route" (the constant made `#/sky`), 0/3: "x the
//      People route is a bare fragment the router reads as the People sheet: the
//      return is not the People route (expected #/settings/users/users, got
//      #/sky)"
//   G5 "the mount prefix is dropped" (`u("/auth/login")` made `"/auth/login"`),
//      2/3: "x the address keeps the mount prefix and carries the return,
//      percent-encoded: the address the browser is sent to (expected
//      /h/home-1/auth/login?return=%23%2Fsettings%2Fusers%2Fusers, got
//      /auth/login?return=%23%2Fsettings%2Fusers%2Fusers)"
//
// THE RIG HALF (`auth/routes.py`) is not in this tree: `server/tests/
// test_w16_google_return_path.py` (48 cases, the open-redirect refusals among
// them) and the change itself are in WP-145's `blocked_on`, run in a scratch copy
// of server/. Until it lands, `return` is an ignored query parameter.

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
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/h/home-1/", pretendToBeVisual: true },
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

// jsdom cannot navigate away from a document, and `window.location.href = ...`
// is a silent "not implemented" there, so the page's own assignment is caught
// the way a browser would receive it: the code under test reads the GLOBAL
// `window`, which is this view of jsdom's with `location` swapped for a recorder.
const assigned: string[] = [];
// ON THE RELAY'S MOUNT, because that is where a step-up is ever needed, and
// because it is the only place the mount prefix `u()` adds is not empty.
const fakeLocation = {
  pathname: "/h/home-1/", hash: "", search: "", origin: "http://local",
  href: "http://local/h/home-1/",
};
Object.defineProperty(fakeLocation, "href", {
  get: () => "http://local/h/home-1/",
  set: (v: string) => { assigned.push(String(v)); },
  enumerable: true,
});
const windowView = new Proxy(win, {
  get(target: any, prop: string | symbol) {
    if (prop === "location") return fakeLocation;
    const v = target[prop];
    return typeof v === "function" ? v.bind(target) : v;
  },
  set(target: any, prop: string | symbol, value: unknown) { target[prop] = value; return true; },
});

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLAnchorElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "PointerEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "Image",
]) {
  const v = k === "window" ? windowView : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
g.fetch = async (url: any) => {
  const u = String(url);
  if (u.includes("/api/users")) {
    return ok({ users: [{ id: "u1", username: "root", email: "root@rig", role: "admin", enabled: true }] });
  }
  return ok({ ok: true });
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { noteRemoteStatus, resetRelayForTests } = await import("../../../lib/relay");
const { parseHash } = await import("../../../router");
const { UsersEditor } = await import("../tuning/people");
const { PEOPLE_RETURN, googleStepUpHref } = await import("../tuning/people/UsersEditor");

// -------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
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

const ALL_CAPS = [
  "view.status", "view.preview", "view.media", "view.weather", "view.site_precise",
  "view.site_derived", "control.capture", "control.guide", "control.mount",
  "control.power", "config.backend", "config.safety", "config.solar_override",
  "config.site_optics", "config.alerts", "admin.users", "system.update",
];

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const q = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const click = async (el: any) => {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
};

// =============================================================== the builder
test("the People route is a bare fragment the router reads as the People sheet", () => {
  eq(PEOPLE_RETURN, "#/settings/users/users", "the return is not the People route");
  assert(/^#\/[A-Za-z0-9/_-]*$/.test(PEOPLE_RETURN),
    `the return is not a bare fragment (a scheme, host or path would make it an address): ${PEOPLE_RETURN}`);
  const route = parseHash(PEOPLE_RETURN);
  eq(route.hub, "settings", "the return does not open the Settings hub");
  eq(route.sub, "users", "the return does not open the USERS sub-nav");
  eq(route.sheets.join(","), "users", "the return does not open the PEOPLE sheet");
});

test("the address keeps the mount prefix and carries the return, percent-encoded", () => {
  eq(googleStepUpHref(), "/h/home-1/auth/login?return=%23%2Fsettings%2Fusers%2Fusers",
    "the address the browser is sent to");
  const url = new URL(googleStepUpHref(), "http://local");
  eq(url.pathname, "/h/home-1/auth/login", "the login route, under the relay's mount");
  eq(url.searchParams.get("return"), PEOPLE_RETURN, "the return does not survive the query string");
});

// ============================================================ the press, mounted
await testAsync("a Google sign-in pressed from PEOPLE goes to /auth/login with the People return", async () => {
  noteRemoteStatus({ via: "relay" });
  useStore.setState({
    principal: { role: "admin", email: "me@rig", caps: ALL_CAPS },
    authGate: "open", wsPhase: "up", wsConnected: true, equipConnected: true,
    status: { connected: {}, looping: false, busy: null, busy_lanes: [] },
    sequence: { state: "idle" },
    authMethods: { methods: ["local", "google"], google_configured: true, first_run: false },
    toasts: [],
  } as never);
  await act(async () => { root.render(createElement(UsersEditor)); });
  await settle();
  const open = q("users-stepup-open");
  assert(open != null, "the relay card has no SIGN IN AGAIN button");
  await click(open);
  const google = q("users-stepup-google");
  assert(google != null, "no SIGN IN WITH GOOGLE although Google is enabled");
  assigned.length = 0;
  await click(google);
  eq(assigned.length, 1, "the press did not send the browser anywhere, or sent it twice");
  eq(assigned[0], googleStepUpHref(),
    "the browser was not sent to the login route with the People return");
  assert(/[?&]return=%23%2Fsettings%2Fusers%2Fusers$/.test(assigned[0]),
    `the address carries no People return: ${assigned[0]}`);
});

await act(async () => { root.render(null); });
resetRelayForTests();

const total = passed + failed;
console.log(`w16PeopleGoogleReturn.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
if (failed) process.exitCode = 1;
