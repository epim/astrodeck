// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16ClassicGoogleReturn.test.tsx - SIGN IN WITH GOOGLE, pressed from the
// CLASSIC People panel, asks the rig to bring the person back to the classic
// Settings screen, and the shared copy says People (#733; WP-145 item 4, wave
// 16 integration). Mounted: the real classic UsersPanel on a tunnelled origin.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/settings/__tests__/w16ClassicGoogleReturn.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE GAP. WP-145 made the #/next People editor send `/auth/login?return=`
// with the People route and built the rig half (auth/routes.py). The classic
// panel presses the SHARED `useStepUp().signInGoogle`, which sent the bare
// `/auth/login`, so a classic step-up still landed on the home screen while
// the shared sentence (`STEP_UP_GOOGLE_NOTE`) said otherwise once the rig half
// landed. `signInGoogle` takes the route now, and the classic panel asks for
// `CLASSIC_PEOPLE_RETURN`, `#/classic/settings`.
//
// WHAT IS GUARDED
//
//   1. The press sends the browser to the login route under the relay's mount
//      with `return=%23%2Fclassic%2Fsettings`.
//   2. The return is a route the classic app really has: it is a bare
//      fragment the rig's validator takes (`#/` and letters, digits, `_`, `-`
//      and `/`), the router reads it as the classic root, and as the Settings
//      view.
//   3. The shared hook with no route is the bare `/auth/login`, as before.
//   4. The shared copy says Google returns to People, in both places.
//
// NAMED MUTANTS (each from a byte backup, restored and sha256-compared; the
// first failure is quoted):
//   C1 "the classic press sends no return" (UsersPanel.tsx `stepUp.
//     signInGoogle(CLASSIC_PEOPLE_RETURN)` made `stepUp.signInGoogle()`), 3/4:
//     x a Google sign-in pressed from the classic People panel asks to come
//       back to Settings: the browser was not sent to the login route with the
//       classic return (expected /h/home-1/auth/login?return=%23%2Fclassic%2Fsettings,
//       got /h/home-1/auth/login)
//   C2 "the hook drops the return" (gateHook.ts `googleLoginHref(returnTo)`
//     made `googleLoginHref()`), 3/4: the same case, the same got.
//   C3 "the return is not encoded" (gateHook.ts `encodeURIComponent(returnTo)`
//     made `returnTo`), 3/4: the same case, got
//     /h/home-1/auth/login?return=#/classic/settings
//   C4 "the return is another view" (`CLASSIC_PEOPLE_RETURN` made
//     `#/classic/mount`), 2/4: x the classic return is a bare fragment the
//     rig's validator takes and the classic router reads: the classic return
//     (expected #/classic/settings, got #/classic/mount), and the press case.
//   C5 "the copy says the home screen again" (peopleModel.ts
//     `STEP_UP_GOOGLE_NOTE` put back), 3/4: x the shared copy says Google
//     returns to People, where it said the home screen: the note: Google
//     sends you back to the home screen. Open Settings and People again, ...

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

// jsdom cannot navigate, so the page's own assignment to `location.href` is
// caught the way a browser would receive it (see w16PeopleGoogleReturn.test).
const assigned: string[] = [];
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
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver",
]) {
  const v = k === "window" ? windowView : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

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
  if (u.includes("/api/me")) return ok({ role: "admin", email: "admin@rig", caps: ["admin.users"] });
  if (u.includes("/api/users")) {
    if (method === "PATCH") return refuse(STEP_UP_BODY);
    return ok({ users: USERS });
  }
  return ok({ ok: true });
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { noteRemoteStatus, resetRelayForTests } = await import("../../../next/lib/relay");
const { googleLoginHref } = await import("../../../next/lib/gateHook");
const { rootForHash, classicViewForHash } = await import("../../../rootChoice");
const { applyClassicView } = await import("../../../AppRoot");
const UsersPanelModule = await import("../UsersPanel");
const UsersPanel = UsersPanelModule.default;
const { CLASSIC_PEOPLE_RETURN } = UsersPanelModule;
const { STEP_UP_GOOGLE_NOTE, STEP_UP_RETRY_HINT } = await import(
  "../../../next/hubs/settings/tuning/people/peopleModel");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const q = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const tick = () => new Promise((r) => setTimeout(r, 0));
const run = async (fn: () => void) => {
  await act(async () => { fn(); for (let i = 0; i < 6; i++) await tick(); });
};
const click = (el: any) => run(() => {
  el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
});
const typeInto = (el: any, value: string) => run(() => {
  Object.getOwnPropertyDescriptor(win.HTMLSelectElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new win.Event("change", { bubbles: true }));
});

// ================================================================ the builder
test("with no route the hook sends the bare login address, as before", () => {
  eq(googleLoginHref(), "/h/home-1/auth/login", "the bare address under the relay's mount");
});

test("the classic return is a bare fragment the rig's validator takes and the classic router reads", () => {
  eq(CLASSIC_PEOPLE_RETURN, "#/classic/settings", "the classic return");
  assert(/^#\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*\/?$/.test(CLASSIC_PEOPLE_RETURN),
    `the return is not a bare in-app fragment: ${CLASSIC_PEOPLE_RETURN}`);
  eq(rootForHash(CLASSIC_PEOPLE_RETURN), "classic", "the return does not open the classic root");
  eq(classicViewForHash(CLASSIC_PEOPLE_RETURN), "settings", "the return does not open Settings");
  applyClassicView(CLASSIC_PEOPLE_RETURN);
  eq(useStore.getState().view, "settings", "the classic app is not told to open Settings");
});

test("the shared copy says Google returns to People, where it said the home screen", () => {
  assert(/back to People/.test(STEP_UP_GOOGLE_NOTE), `the note: ${STEP_UP_GOOGLE_NOTE}`);
  assert(!/home screen/.test(STEP_UP_GOOGLE_NOTE), `the note still says the home screen`);
  assert(/Google returns to People/.test(STEP_UP_RETRY_HINT), `the hint: ${STEP_UP_RETRY_HINT}`);
  assert(!/home screen/.test(STEP_UP_RETRY_HINT), `the hint still says the home screen`);
});

// ============================================================ the press, mounted
await testAsync("a Google sign-in pressed from the classic People panel asks to come back to Settings", async () => {
  noteRemoteStatus({ via: "relay" });
  await act(async () => {
    useStore.setState({
      principal: { role: "admin", email: "admin@rig", caps: ["admin.users"] },
      authMethods: { methods: ["local", "google"], google_configured: true, first_run: false },
      authGate: "open",
      toasts: [],
    } as never);
  });
  await act(async () => { root.render(createElement(UsersPanel)); });
  for (let i = 0; i < 3; i++) await act(async () => { await tick(); });
  // A refusal for want of a recent sign-in opens the sign-in form.
  const sel = container.querySelector('select[aria-label="Role for guest@rig"]');
  assert(sel != null, "the role select for the second person is missing");
  await typeInto(sel, "operator");
  const google = q("users-stepup-google");
  assert(google != null, "no SIGN IN WITH GOOGLE although Google is enabled");
  assigned.length = 0;
  await click(google);
  eq(assigned.length, 1, "the press did not send the browser anywhere, or sent it twice");
  eq(assigned[0], "/h/home-1/auth/login?return=%23%2Fclassic%2Fsettings",
    "the browser was not sent to the login route with the classic return");
  eq(new URL(assigned[0], "http://local").searchParams.get("return"), CLASSIC_PEOPLE_RETURN,
    "the return does not survive the query string");
});

await act(async () => { root.render(null); });
resetRelayForTests();

const total = passed + failed;
console.log(`w16ClassicGoogleReturn.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
if (failed) (globalThis as any).process.exitCode = 1;
