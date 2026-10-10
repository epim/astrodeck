// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w18EquipmentViewLiveRun.test.tsx - CLASSIC Equipment, MOUNTED: the Disconnect
// and Activate confirms say "a sequence is running" for EVERY live run state,
// not only running and paused (#821, WP-158, wave 18).
//
//   Run directly:  npx tsx src/views/__tests__/w18EquipmentViewLiveRun.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `EquipmentView` asked `seqState === "running" || seqState ===
// "paused"` in two places: `doDisconnect` (which swaps its body for "A sequence
// is running. Disconnecting aborts it ...") and the profile Activate
// (`profileActivateConfirm`'s `sequenceRunning`). They are the classic copies of
// the two rig-teardown confirms the issue names in `rigConnect.ts`,
// `ProfilesEditor.tsx` and `ProfileList.tsx`, on the page the classic root (the
// production root today) shows. A cloud hold ("holding") and an abort's
// wind-down ("aborting") are `engine.running` on the rig, so during either the
// dialogs said nothing about the run they were about to abort.
//
// WHAT IS WORTH ASSERTING. The dialog each action opens on the mounted page, per
// state in the union, with a control row (a run that is not live says nothing
// about a run); a cancelled dialog posts nothing.
//
// MUTANT "running or paused only" (the two comparisons restored). Run from a
// byte backup, restored byte-identically (md5sum compared): see the report for
// the failing lines.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
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
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- stub server
const ROWS = [
  { id: "a", name: "Deep sky rig", mode: "alpaca", devices_count: 4, active: true, site_name: null },
  { id: "b", name: "Planetary rig", mode: "mixed", devices_count: 3, active: false, site_name: null },
];
const writes: string[] = [];
const ok = (json: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => json } as any);
g.fetch = async (url: string, init?: any) => {
  const path = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  if (method !== "GET") writes.push(`${method} ${path}`);
  if (path.match(/\/api\/profiles\/[^/]+$/) && method === "GET") {
    // A simulator rig that stores a device: not "connects nothing", not real
    // motion, so the only escalation left to the hold is a live run.
    const id = path.split("/").pop()!;
    return ok({
      id, name: id, primary_backend: "sim", devices: [{ role: "camera", backend: "sim" }],
      nina_port: 0, phd2_port: 0, optics: null, providers: null,
    });
  }
  if (path.includes("/api/profiles")) return ok(ROWS.map((r) => ({ ...r })));
  if (path.includes("/api/drivers")) return ok({ roles: ["camera", "mount"], drivers: [] });
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const EquipmentView = (await import("../EquipmentView")).default;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve(); }); };

const ADMIN = { role: "admin", email: null, caps: ["view.status", "control.reconnect", "config.backend"] };
const container = win.document.getElementById("root") as any;
let root: ReturnType<typeof createRoot> | null = null;

async function mountWith(sequence: Record<string, unknown>): Promise<void> {
  writes.length = 0;
  if (root) await act(async () => { root!.unmount(); });
  root = createRoot(container);
  useStore.setState({
    principal: ADMIN, authGate: "open", wsPhase: "up", toasts: [], confirm: null,
    // One live camera, so both dialogs exist for every state (with nothing live
    // and a simulator profile Activate asks nothing, which would grade nothing).
    status: {
      connected: { camera: { name: "sim", connected: true } },
      backend_links: [{ role: "camera", connected: true, error: null }],
      busy_lanes: [], looping: false, mode: "none",
    },
    sequence,
  } as never);
  await act(async () => { root!.render(createElement(EquipmentView)); });
  await flush();
}

const buttonNamed = (name: string): any =>
  ([...container.querySelectorAll("button")] as any[]).find((b) => (b.textContent || "").trim() === name);
function activateOn(name: string): any {
  const row = [...container.querySelectorAll("div")].find(
    (d: any) => d.className?.includes?.("border-line") && (d.textContent || "").includes(name)
      && [...d.querySelectorAll("button")].some((b: any) => (b.textContent || "").trim() === "Activate"),
  ) as any;
  if (!row) throw new Error(`no profile row rendered for "${name}"`);
  return [...row.querySelectorAll("button")].find((b: any) => (b.textContent || "").trim() === "Activate");
}
const confirmReq = () => (useStore.getState() as any).confirm;
async function pressAndRead(node: any): Promise<any> {
  await act(async () => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  const until = Date.now() + 3000;
  while (confirmReq() == null && Date.now() < until) { await act(async () => { await sleep(10); }); }
  const req = confirmReq();
  if (req) {
    const seen = { title: String(req.title), body: String(req.body), mode: String(req.mode) };
    act(() => { useStore.getState().resolveConfirm(false); });
    await flush();
    return seen;
  }
  return null;
}

const SENTENCE_ACTIVATE = "A sequence is running, and that teardown aborts it.";
const SENTENCE_DISCONNECT = "A sequence is running. Disconnecting aborts it";
/** The whole `SequenceState["state"]` union, with whether the rig's
 *  `engine.running` is true in it. */
const STATES: Array<[string, boolean]> = [
  ["running", true], ["paused", true], ["holding", true], ["aborting", true],
  ["idle", false], ["complete", false], ["aborted", false], ["error", false],
  ["nina_native", false],
];

for (const [state, live] of STATES) {
  await testAsync(`run ${state}: Disconnect's dialog ${live ? "says" : "does not say"} a sequence is running`, async () => {
    await mountWith({ state });
    const b = buttonNamed("Disconnect");
    assert(b != null, "no Disconnect button (nothing below grades a blank page)");
    const dialog = await pressAndRead(b);
    assert(dialog !== null, "Disconnect opened no confirm");
    eq(dialog.body.includes(SENTENCE_DISCONNECT), live, `the disconnect dialog with the run ${state}: ${dialog.body}`);
    eq(writes.length, 0, "a cancelled Disconnect still wrote to the rig");
  });
}

for (const [state, live] of STATES) {
  await testAsync(`run ${state}: Activate's dialog ${live ? "says" : "does not say"} a sequence is running`, async () => {
    await mountWith({ state });
    const dialog = await pressAndRead(activateOn("Planetary rig"));
    assert(dialog !== null, "Activate fired with no confirm over a live rig");
    eq(dialog.body.includes(SENTENCE_ACTIVATE), live, `the activate dialog with the run ${state}: ${dialog.body}`);
    eq(dialog.mode, live ? "hold" : "confirm", `the activate dialog's mode with the run ${state}`);
    eq(writes.length, 0, "a cancelled Activate still wrote to the rig");
  });
}

if (root) await act(async () => { root!.unmount(); });

console.log(`w18EquipmentViewLiveRun: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
