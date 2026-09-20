// Run directly; no background service or physical equipment is involved.
import nodeAssert from "node:assert/strict";

// A counting shim over the three assertions this file uses. Bare asserts threw
// on the first failure, so the run ended there and printed no tally at all -
// which the runner scores as "cannot be scored", not as a pass. Counting gives
// the runner its "N/M passed" line AND reports every failure rather than only
// the earliest one.
// Explicit signatures rather than a generic wrapper: node's assertions are
// declared as assertion functions, and their narrowing cannot be forwarded
// through a generic rest parameter without making `message` mandatory.
let passed = 0;
const failures: string[] = [];
const record = (label: string, run: () => void): void => {
  try { run(); passed++; } catch (e) {
    failures.push(`${label}: ${e instanceof Error ? e.message : String(e)}`);
  }
};
// The message is passed only when present: node's overloads accept either no
// message or a required one, never `string | undefined`.
const assert = {
  equal: (a: unknown, b: unknown, m?: string): void =>
    record(m ?? "equal", () => m === undefined ? nodeAssert.equal(a, b) : nodeAssert.equal(a, b, m)),
  deepEqual: (a: unknown, b: unknown, m?: string): void =>
    record(m ?? "deepEqual", () => m === undefined ? nodeAssert.deepEqual(a, b) : nodeAssert.deepEqual(a, b, m)),
  match: (s: string, re: RegExp, m?: string): void =>
    record(m ?? `match ${re}`, () => m === undefined ? nodeAssert.match(s, re) : nodeAssert.match(s, re, m)),
};
const { JSDOM } = await import("jsdom");
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: "http://local/", pretendToBeVisual: true });
// Test globals must precede the store/UI imports.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const win = dom.window as any;
win.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
for (const name of ["window", "document", "navigator", "HTMLElement", "Element", "Node", "Event", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia"]) {
  Object.defineProperty(globalThis, name, { value: name === "window" ? win : win[name], configurable: true, writable: true });
}
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const writes: Record<string, unknown>[] = [];
let failSave = false;
const config = {
  site: { is_default: false },
  dusk: { enabled: false, profile_id: "test-rig", sun_alt_deg: -12 },
  cooling: { setpoint_c: -10, warm_ramp: true, warm_rate_c_per_min: 2, warm_ambient_c: null, cool_timeout_s: 600 },
};
globalThis.fetch = async (url, init) => {
  const path = String(url);
  if (path.includes("/api/profiles")) return Response.json([{ id: "test-rig", name: "Test rig" }]);
  if (path.includes("/api/dusk/state")) return Response.json({ state: "waiting", detail: "Waiting for dusk." });
  if (init?.method === "POST") {
    if (failSave) return Response.json({ detail: "offline" }, { status: 503 });
    const body = JSON.parse(String(init.body));
    writes.push(body);
    Object.assign(config, body);
  }
  return Response.json(config);
};
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const Panel = (await import("../DuskStartupPanel")).default;
useStore.setState({ config, principal: { caps: ["config.backend", "config.safety"] } } as never);
const host = document.getElementById("root")!;
const root = createRoot(host);
const flush = async () => { for (let i = 0; i < 5; i++) await act(async () => { await Promise.resolve(); }); };
const button = () => host.querySelector('button:not([role="switch"])') as HTMLButtonElement;
const toggle = () => host.querySelector('button[role="switch"]') as HTMLButtonElement;
const click = async (node: HTMLElement) => { await act(async () => { node.click(); }); await flush(); };
await act(async () => { root.render(createElement(Panel)); });
await flush();
assert.match(host.textContent!, /Waiting for dusk/);
assert.equal(button().disabled, true, "unchanged settings cannot be saved");
await click(toggle());
await click(button());
assert.equal(writes.length, 1);
assert.deepEqual(writes[0].dusk, { enabled: true, profile_id: "test-rig", sun_alt_deg: -12 });
assert.deepEqual(writes[0].cooling, config.cooling, "saving retains warm-down policy and explicit imaging target");
assert.equal(toggle().getAttribute("aria-checked"), "true", "acknowledged settings survive the save refresh");
assert.match(host.textContent!, /Saved/);
assert.equal(button().disabled, true);

await click(toggle());
failSave = true;
await click(button());
assert.match(host.querySelector('[role="alert"]')!.textContent!, /changes are still here/);
assert.equal(toggle().getAttribute("aria-checked"), "false", "failed save preserves the user's draft");
assert.equal(button().disabled, false, "failed save can be retried");

// A live config update must not overwrite a draft while it is being edited.
await act(async () => { useStore.setState({ config: { ...config, dusk: { ...config.dusk, enabled: true } } } as never); });
assert.equal(toggle().getAttribute("aria-checked"), "false");
await act(async () => { useStore.setState({ principal: { caps: ["view.status"] } } as never); });
assert.equal((host.querySelector("fieldset") as HTMLFieldSetElement).disabled, true);
assert.match(host.textContent!, /administrator/);
await act(async () => { root.unmount(); });
dom.window.close();
console.log(`dusk startup: ${passed}/${passed + failures.length} passed`);
for (const failure of failures) console.log(failure);
export const result = { passed, failed: failures.length, total: passed + failures.length };
if (failures.length) process.exitCode = 1;
