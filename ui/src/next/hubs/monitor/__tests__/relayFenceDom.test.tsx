// relayFenceDom.test.tsx - MONITOR > ALERTS, mounted OVER THE RELAY and pressed
// (wave-2 review R8's FIX-U-settings P1, the monitor half).
//
//   Run directly:  npx tsx src/next/hubs/monitor/__tests__/relayFenceDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by
//   `npx tsc --noEmit -p tsconfig.json`.
//
// SIX WRITES ON THE RIG'S LAN-ONLY FENCE. Adding, testing, editing and deleting
// a sink are `/api/alerts` requests and the dead-man's-switch save is
// `POST /api/config`; both prefixes are on `app.py`'s
// `_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES`, so over the relay the rig answers 403
// `code: "local_only"` to EVERY role - an admin included. Before `needsLan` all
// six rendered armed and the refusal arrived as a toast reading "config.alerts
// is required to change alert sinks", which is a true sentence about the wrong
// blocker told to somebody who holds `config.alerts`.
//
// THE PRINCIPAL IS AN ADMIN WITH `config.alerts`. A viewer would be locked by
// the capability rule whether or not the fence existed, and every assertion here
// would then be about the capability. An admin has exactly one possible blocker,
// which is what makes LOCAL_ONLY_REASON an assertion about `needsLan`.
//
// NAMED SABOTAGES:
//   * drop `needsLan: true` from `AlertsEditor`'s `useLock` -> the five control
//     assertions fail on `aria-disabled` (null, not "true").
//   * put the `e.status === 403` branch back ahead of `isLocalOnly(e)` in
//     `save()` (or drop the `isLocalOnly` line from `toastError`) -> "a refusal
//     that slipped past the gate names the relay" fails, with the capability
//     sentence in the message.
//   * make `lockNote` return the capability note unconditionally -> "the two
//     footers name the relay" fails.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
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
// A LAN pathname on purpose: `relay.ts` would otherwise answer "relay" from the
// mount point alone and this file would be grading its fallback rather than the
// rig's own `via`, which is what a real session gets.
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

const CONFIG: any = {
  version: 4,
  alerts: [{
    id: "sink-1", kind: "ntfy", enabled: true, url: "https://ntfy.sh/astrodeck",
    events: ["run_start"], min_level: "warning", heartbeat_min: 0,
    token_configured: false, verified: false,
  }],
  deadman_configured: false,
  weather: { enabled: true },
  site: { is_default: false },
};

const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asks.push({ url: u, method });
  if (u.includes("/api/alerts/health")) {
    return ok({
      undelivered: 0, undelivered_by_sink: {},
      deadman: { configured: false, healthy: false, last_ping_age_s: null },
    });
  }
  if (u.includes("/api/config")) return ok(CONFIG);
  return ok({ ok: true });
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { LOCAL_ONLY_REASON } = await import("../../../lib/gate");
const { noteRemoteStatus, resetRelayForTests, onRelay } = await import("../../../lib/relay");
const { AlertsScreen } = await import("../alerts/AlertsScreen");
const { accessPhrase } = await import("../../../../lib/caps");

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

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const q = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const text = () => String(container.textContent ?? "");
const click = async (el: any) => {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  // Three turns, not one: a refused write awaits the fetch, then `loadConfig`,
  // then clears its busy flag, and a state update landing outside `act` is a
  // warning that reads like a broken test.
  await settle();
  await settle();
};
const writes = () => asks.filter((a) => a.method !== "GET");
const toastTitles = () =>
  ((useStore.getState() as any).toasts as Array<{ title?: string }>).map((t) => t.title);

// `config.alerts` is admin-only in the role table, so the case under test - a
// principal who HOLDS the capability and is still refused - is an admin.
const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: ["view.status", "view.preview", "view.weather", "control.capture",
    "config.alerts", "config.site_optics", "admin.users", "system.update"],
};

// ================================================================ the mount
noteRemoteStatus({ via: "relay" });

test("precondition: this tab counts as tunnelled, from the rig's own answer", () => {
  eq(onRelay(), true,
    "the relay derivation says LAN - every assertion in this file would be vacuous");
});

useStore.setState({
  principal: ADMIN,
  authGate: "open",
  wsPhase: "up",
  wsConnected: true,
  equipConnected: true,
  config: CONFIG,
  status: { connected: {}, looping: false, busy: null, busy_lanes: [] },
  toasts: [],
} as never);

asks.length = 0;
await act(async () => { root.render(createElement(AlertsScreen)); });
await settle();
await act(async () => { useStore.setState({ toasts: [] } as never); });

await testAsync("ALERTS: all five writes refuse over the relay, and fire nothing", async () => {
  assert(q("alerts-sinks") != null,
    "the alerts screen never rendered - every assertion below would be vacuous");
  assert(q("alerts-sink-row") != null,
    "no sink row, so TEST / EDIT / DELETE are not on the page to be graded");

  const controls: Array<[string, string]> = [
    ["alerts-sink-add", "POST /api/alerts"],
    ["alerts-sink-test", "POST /api/alerts/{id}/test"],
    ["alerts-sink-edit", "the edit form, which saves to POST /api/alerts"],
    ["alerts-sink-delete", "DELETE /api/alerts/{id}"],
    ["alerts-deadman-save", "POST /api/config {deadman_url}"],
  ];
  for (const [id, route] of controls) {
    const el = q(id);
    assert(el != null, `${id} (${route}) is not on the page`);
    eq(el.hasAttribute("disabled"), false, `${id} uses the native disabled attribute`);
    eq(el.getAttribute("aria-disabled"), "true",
      `${id} (${route}) renders ARMED over the relay - the rig answers 403 local_only`);
    eq(el.getAttribute("title"), LOCAL_ONLY_REASON,
      `${id} does not name the relay as the blocker`);
  }

  // One press at a time with the queue emptied first: the store caps the toast
  // list at three, so pressing all five and then looking for the fourth
  // sentence would be reading an eviction rule rather than a refusal.
  for (const [id] of controls) {
    await act(async () => { useStore.setState({ toasts: [] } as never); });
    await click(q(id));
    assert(toastTitles().includes(LOCAL_ONLY_REASON),
      `${id} refused in silence: toasts were ${JSON.stringify(toastTitles())}`);
  }
  eq(writes().length, 0, `a locked press reached the rig: ${JSON.stringify(writes())}`);
});

test("ALERTS: both read-only footers name the relay, not the capability the admin holds", () => {
  const sinks = q("alerts-lock-note");
  const dead = q("alerts-deadman-lock-note");
  assert(sinks != null, "no read-only note on a card where nothing can be saved");
  assert(dead != null, "the dead-man's-switch has no read-only note of its own");
  for (const [what, el] of [["sinks", sinks], ["dead-man", dead]] as const) {
    assert(String(el.textContent).includes(LOCAL_ONLY_REASON),
      `the ${what} footer names the wrong blocker: "${el.textContent}"`);
  }
  const phrase = accessPhrase("config.alerts");
  assert(!text().includes(`changing alerts needs ${phrase}`),
    "an admin on the relay was told that changing alerts needs admin access");
});

// ------------------------------------------------- the refusal that slips past
// The gate is a claim made BEFORE the press; the catch is what happens when the
// claim is wrong - a tab that was on the LAN when it rendered and whose rig has
// since started terminating through the relay, or any future route that joins
// the fence before this UI knows. `isLocalOnly(e)` has to be read FIRST in those
// catches, or a `local_only` 403 falls into the `status === 403` branch below it
// and an admin is told they need `config.alerts`.
//
// So this half runs on a DIRECT origin, where every control is live, and the rig
// answers the fence's exact error anyway.
noteRemoteStatus({ via: "direct" });
let refuseFence = false;
const fenceError = {
  ok: false, status: 403, statusText: "Forbidden",
  json: async () => ({ detail: "this security-sensitive operation is LAN-only", code: "local_only" }),
};
const realFetch = g.fetch;
g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  if (refuseFence && method !== "GET" && u.includes("/api/alerts")) {
    asks.push({ url: u, method });
    return fenceError;
  }
  return realFetch(url, init);
};

await act(async () => { root.render(null); });
await settle();
await act(async () => { root.render(createElement(AlertsScreen)); });
await settle();

test("precondition: on a DIRECT origin the same controls are live", () => {
  eq(onRelay(), false, "the origin did not switch back - the presses below would be refused early");
  eq(q("alerts-sink-test").getAttribute("aria-disabled"), null,
    "TEST is still locked on the LAN, so the catch under test can never be reached");
});

refuseFence = true;

await testAsync("ALERTS: a 403 local_only on TEST is toasted as the relay, not as a fault", async () => {
  await act(async () => { useStore.setState({ toasts: [] } as never); });
  await click(q("alerts-sink-test"));
  assert(toastTitles().includes(LOCAL_ONLY_REASON),
    `the fence's refusal reached the user as ${JSON.stringify(toastTitles())}`);
  assert(!toastTitles().some((t) => /security-sensitive/.test(String(t))),
    "the server's own wording was shown verbatim - it says nothing the user can act on");
});

await testAsync("ALERTS: a 403 local_only on SAVE names the relay, never the capability", async () => {
  await click(q("alerts-sink-edit"));
  assert(q("alerts-sink-form") != null, "EDIT did not open the form - SAVE is not on the page");
  await act(async () => { useStore.setState({ toasts: [] } as never); });
  await click(q("alerts-sink-save"));
  assert(toastTitles().includes(LOCAL_ONLY_REASON),
    `the save refusal reached the user as ${JSON.stringify(toastTitles())}`);
  assert(!toastTitles().some((t) => /config\.alerts is required/.test(String(t))),
    "an admin who holds config.alerts was told they need config.alerts - the "
    + "wrong-blocker defect this branch exists to prevent");
});

await act(async () => { root.render(null); });
resetRelayForTests();

// =================================================================== summary
const total = passed + failed;
console.log(`monitorRelayFenceDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
