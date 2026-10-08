// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15ProfilesPopoverRelay.test.tsx - the PROFILE popover over the RELAY (#685,
// WP-105).
//
//   Run directly:  node --import tsx src/next/hubs/rig/devices/__tests__/w15ProfilesPopoverRelay.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// THE RULE UNDER TEST. `app.py` fences every unsafe method under `/api/profiles`
// to the LAN, and since #685 allow-lists exactly one route through it:
// `POST /api/profiles/<id>/activate`, without `force`. The popover therefore
// must NOT lock ACTIVATE on the relay (it was refused for every role before),
// and must keep locking SAVE and DELETE, which the rig still refuses 403
// `local_only`. The two halves are asserted on the SAME mount, with the relay
// reason passed in exactly as `DevicesScreen` passes it, so neither can pass by
// the other being broken.
//
// WP-146 (#759, owner ruling 2026-10-07: "This should be permitted for Admin and
// operator roles") narrows ACTIVATE's capability from `config.backend` to
// `control.reconnect`, which an operator holds. The popover reads it from the
// store, so every mount below names the principal it is for. SAVE and DELETE
// stay `config.backend` and stay locked for an operator, on the LAN and on the
// relay; a viewer's ACTIVATE stays locked and names "operator or admin access".
//
// NAMED SABOTAGES:
//   * put `p.lanReason ??` back at the head of `activateLock` -> "ACTIVATE is not
//     locked over the relay" fails on `aria-disabled` ("true", not null).
//   * drop `p.lanReason ??` from `deleteLock` -> "SAVE and DELETE keep the LAN
//     sentence" fails on the delete control's title.
//   * drop the capability branch from `activateLock` -> "a caller without
//     control.reconnect is still told so" fails (the lock was cut, not narrowed).
//   * ACTIVATE_GATE_IS_BACKEND (WP-146): `const canReconnect = useCan(
//     RECONNECT_CAP)` made `const canReconnect = p.canConfig` -> "an operator's
//     ACTIVATE is armed, on the relay and on the LAN" fails on `aria-disabled`.
//   * DELETE_RIDES_RECONNECT (WP-146): `profileDeleteLock(p.canConfig)` made
//     `profileDeleteLock(canReconnect)` -> the same case fails on DELETE's title.
//
// Run 2026-10-07 from a byte backup, each restored byte-identically (sha256
// compared). Observed:
//   * ACTIVATE_GATE_IS_BACKEND: "w15ProfilesPopoverRelay.test: 4/6 passed",
//     "x pressing ACTIVATE over the relay as an operator sends the unforced
//     activate and nothing else that writes: unexpected writes: [] (expected 1,
//     got 0)" and "x an operator's ACTIVATE is armed, on the relay and on the LAN,
//     while SAVE and DELETE stay locked: an operator's ACTIVATE renders locked
//     over the relay (control.reconnect is held) (expected null, got true)";
//   * DELETE_RIDES_RECONNECT: "5/6 passed", "x an operator's ACTIVATE is armed
//     ...: DELETE does not name config.backend for an operator: "null"".
//
// Run 2026-10-07 from a byte backup, each restored byte-identically (sha256
// compared). Observed:
//   * lanReason back at the head of activateLock: "w15ProfilesPopoverRelay.test:
//     0/4 passed", "x ACTIVATE is not locked over the relay, and SAVE and DELETE
//     keep the LAN sentence: ACTIVATE renders locked over the relay, but the rig
//     allows an unforced activate (#685) (expected null, got true)";
//   * lanReason dropped from deleteLock: "3/4 passed", "x ACTIVATE is not locked
//     ...: DELETE renders armed over the relay - the rig 403s it local_only
//     (expected true, got null)";
//   * capability branch cut: "3/4 passed", "x a caller without config.backend is
//     still told so, relay or not: a caller without config.backend can press
//     ACTIVATE over the relay (expected true, got null)".

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
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
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
interface Ask { url: string; method: string }
const asks: Ask[] = [];

const ROW = {
  id: "p1", name: "Backyard", mode: "alpaca", devices_count: 6, site_name: null, active: false,
};
const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asks.push({ url: u, method });
  if (u.endsWith("/api/profiles/p1/activate")) return ok({ started: "profile" });
  // The follow-up poll answers "active" at once, so `waitForProfileActive`
  // resolves on its first look instead of spending its whole budget.
  if (u.endsWith("/api/profiles")) return ok([{ ...ROW, active: true }]);
  return { ok: false, status: 404, statusText: "Not Found", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { LOCAL_ONLY_REASON } = await import("../../../../lib/gate");
const { ProfilesPopover } = await import("../ProfilesPopover");

// The caps each role really holds (server `auth/capabilities.py`). ACTIVATE
// answers to `control.reconnect`; SAVE and DELETE answer to `config.backend`,
// which only an admin holds.
const ADMIN = {
  role: "admin", email: "a@rig",
  caps: ["view.status", "view.preview", "control.reconnect", "config.backend"],
};
const OPERATOR = {
  role: "operator", email: "o@rig",
  caps: ["view.status", "view.preview", "control.reconnect"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };
type Who = { role: string; email: string | null; caps: string[] };

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
const toastMessages = () =>
  ((useStore.getState() as any).toasts as Array<{ title?: string; message?: string }>)
    .map((t) => t.title ?? t.message);

async function mountPopover(over: Record<string, unknown> = {}, who: Who = ADMIN): Promise<void> {
  await act(async () => { root.render(null); });
  await act(async () => { useStore.setState({ toasts: [], principal: who } as never); });
  asks.length = 0;
  await act(async () => {
    root.render(createElement(ProfilesPopover, {
      rows: [ROW as any],
      liveDevices: 0,
      // What `DevicesScreen` derives from the same principal.
      canConfig: who.caps.includes("config.backend"),
      // Exactly what `DevicesScreen` hands down while the tab is on the relay.
      lanReason: LOCAL_ONLY_REASON,
      busy: null,
      setBusy: () => {},
      onRows: () => {},
      reload: () => {},
      onClose: () => {},
      ...over,
    } as any));
  });
  await settle();
}

// Toasts are dropped while the auth gate is engaged; "open" is a signed-in tab.
useStore.setState({ authGate: "open", toasts: [] } as never);
await mountPopover();

await testAsync("ACTIVATE is not locked over the relay, and SAVE and DELETE keep the LAN sentence", async () => {
  const pick = q("profile-pick-p1");
  assert(pick != null, "the profile row is not on the page - the fixture is wrong");
  eq(pick.getAttribute("aria-disabled"), null,
    "ACTIVATE renders locked over the relay, but the rig allows an unforced activate (#685)");
  eq(pick.getAttribute("title"), null, "ACTIVATE carries a lock sentence over the relay");

  const del = q("profile-delete-p1");
  eq(del.getAttribute("aria-disabled"), "true",
    "DELETE renders armed over the relay - the rig 403s it local_only");
  eq(del.getAttribute("title"), LOCAL_ONLY_REASON, "DELETE names the wrong blocker");
  const save = q("profile-save");
  eq(save.getAttribute("aria-disabled"), "true",
    "SAVE renders armed over the relay - the rig 403s it local_only");
  eq(save.getAttribute("title"), LOCAL_ONLY_REASON, "SAVE names the wrong blocker");
});

for (const [label, who] of [["an admin", ADMIN], ["an operator", OPERATOR]] as Array<[string, Who]>) {
  await testAsync(`pressing ACTIVATE over the relay as ${label} sends the unforced activate and nothing else that writes`, async () => {
    await mountPopover({}, who);
    await act(async () => {
      q("profile-pick-p1").dispatchEvent(
        new win.MouseEvent("click", { bubbles: true, cancelable: true }));
      // `waitForProfileActive` waits one poll interval (1.5 s) before it looks.
      await new Promise((r) => setTimeout(r, 1800));
    });
    await settle();
    const writes = asks.filter((a) => a.method !== "GET");
    eq(writes.length, 1, `unexpected writes: ${JSON.stringify(writes)}`);
    eq(writes[0].method, "POST", "the activate was not a POST");
    assert(writes[0].url.endsWith("/api/profiles/p1/activate"),
      `the write was not the activate: ${writes[0].url}`);
    assert(toastMessages().some((m) => /is active/.test(String(m))),
      `the activate never reported landing: ${JSON.stringify(toastMessages())}`);
  });
}

// DELIBERATE PIN CHANGE (WP-146, #759): this case was "a caller without
// config.backend is still told so" and built the caller as `canConfig: false`.
// An operator has no config.backend and now MAY activate, so the caller that is
// still refused is a viewer, and the sentence names the capability that gates
// ACTIVATE now.
await testAsync("a caller without control.reconnect is still told so, relay or not", async () => {
  for (const lanReason of [LOCAL_ONLY_REASON, null]) {
    await mountPopover({ lanReason }, VIEWER);
    const pick = q("profile-pick-p1");
    eq(pick.getAttribute("aria-disabled"), "true",
      "a viewer can press ACTIVATE (control.reconnect not held)");
    eq(pick.getAttribute("title"), "Activating a profile needs operator or admin access.",
      `the lock does not name the capability: "${pick.getAttribute("title")}"`);
    assert(pick.getAttribute("title") !== LOCAL_ONLY_REASON,
      "a missing capability was reported as the relay");
  }
});

// The headline of #759: an operator holds control.reconnect and not
// config.backend, so ACTIVATE is armed and SAVE / DELETE are not, on the relay
// (where they name the LAN, as for an admin) and on the LAN (where they name the
// capability they need). Asserted on the SAME mounts so neither half can pass by
// the other being broken.
await testAsync("an operator's ACTIVATE is armed, on the relay and on the LAN, while SAVE and DELETE stay locked", async () => {
  // Relay: SAVE and DELETE name the LAN, exactly as for an admin.
  await mountPopover({}, OPERATOR);
  let pick = q("profile-pick-p1");
  eq(pick.getAttribute("aria-disabled"), null,
    "an operator's ACTIVATE renders locked over the relay (control.reconnect is held)");
  eq(pick.getAttribute("title"), null, "an operator's ACTIVATE carries a lock sentence");
  eq(q("profile-delete-p1").getAttribute("aria-disabled"), "true",
    "DELETE renders armed for an operator over the relay");
  eq(q("profile-delete-p1").getAttribute("title"), LOCAL_ONLY_REASON,
    "DELETE names the wrong blocker for an operator on the relay");
  eq(q("profile-save").getAttribute("aria-disabled"), "true",
    "SAVE renders armed for an operator over the relay");
  eq(q("profile-save").getAttribute("title"), LOCAL_ONLY_REASON,
    "SAVE names the wrong blocker for an operator on the relay");

  // LAN: nothing fences them, so the capability is the reason.
  await mountPopover({ lanReason: null }, OPERATOR);
  pick = q("profile-pick-p1");
  eq(pick.getAttribute("aria-disabled"), null,
    "an operator's ACTIVATE renders locked on the LAN (control.reconnect is held)");
  assert(/admin access/.test(String(q("profile-delete-p1").getAttribute("title"))),
    `DELETE does not name config.backend for an operator: "${q("profile-delete-p1").getAttribute("title")}"`);
  eq(q("profile-save").getAttribute("aria-disabled"), "true",
    "SAVE renders armed for an operator on the LAN");
  assert(/admin access/.test(String(q("profile-save").getAttribute("title"))),
    `SAVE does not name config.backend for an operator: "${q("profile-save").getAttribute("title")}"`);
});

await testAsync("a busy lane still locks ACTIVATE over the relay", async () => {
  await mountPopover({ busy: "save" });
  const pick = q("profile-pick-p1");
  eq(pick.getAttribute("aria-disabled"), "true", "ACTIVATE is armed while a rig action runs");
  assert(/already running/.test(String(pick.getAttribute("title"))),
    `the lock does not say why: "${pick.getAttribute("title")}"`);
});

await act(async () => { root.render(null); });

const total = passed + failed;
console.log(`w15ProfilesPopoverRelay.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
