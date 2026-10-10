// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w18ProfileListLiveRun.test.tsx - CLASSIC Settings -> Profiles, MOUNTED:
// Activate's confirm says "a sequence is running" for EVERY live run state, not
// only running and paused (#821, WP-158, wave 18).
//
//   Run directly:  npx tsx src/components/settings/__tests__/w18ProfileListLiveRun.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `ProfileList.onActivate` passed `profileActivateConfirm` a
// `sequenceRunning` of `seqState === "running" || seqState === "paused"`. A cloud
// hold ("holding") and an abort's wind-down ("aborting") are `engine.running` on
// the rig, and every activate tears the rig down first, so the dialog for a live
// rig omitted "A sequence is running, and that teardown aborts it" and stayed a
// tap-confirm instead of a hold-to-confirm. The classic root is the production
// root today.
//
// WHAT IS WORTH ASSERTING. The dialog the mounted panel opens, per state in the
// union, with a control row (a run that is not live says nothing about a run);
// a cancelled dialog posts nothing.
//
// MUTANT "running or paused only" (the comparison restored). Run from a byte
// backup, restored byte-identically (md5sum compared): see the report for the
// failing lines.

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

// ------------------------------------------------------------- the fixture
interface Asked { method: string; url: string }
const asked: Asked[] = [];
const ROWS = [
  { id: "a", name: "Deep sky rig", mode: "alpaca", devices_count: 4, active: true, site_name: null },
  { id: "b", name: "Planetary rig", mode: "mixed", devices_count: 3, active: false, site_name: null },
];
const ok = (json: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });
g.fetch = async (url: string, init?: { method?: string }) => {
  const method = (init?.method ?? "GET").toUpperCase();
  const u = String(url);
  asked.push({ method, url: u });
  if (method === "GET" && /\/api\/profiles\/[^/]+$/.test(u)) {
    const id = u.split("/").pop()!;
    // A simulator rig that stores a device: not "connects nothing", not real
    // motion, so the only escalation left to the hold is a live run.
    return ok({
      id, name: id, primary_backend: "sim", devices: [{ role: "camera", backend: "sim" }],
      nina_host: null, nina_port: 0, phd2_host: null, phd2_port: 0,
      optics: null, site_name: null, providers: null,
    });
  }
  if (method === "GET" && /\/api\/profiles$/.test(u)) return ok(JSON.parse(JSON.stringify(ROWS)));
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const ProfileList = (await import("../ProfileList")).default;

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
const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve(); }); };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function seed(sequence: Record<string, unknown>): void {
  asked.length = 0;
  act(() => {
    useStore.setState({
      principal: { role: "admin", email: null, caps: ["view.status", "control.reconnect", "config.backend"] },
      authGate: "open",
      wsPhase: "up",
      toasts: [],
      confirm: null,
      sequence,
      // One live camera, so the dialog exists for every state (with nothing
      // live and a simulator profile there is no dialog, which grades nothing).
      status: {
        connected: { camera: { name: "sim", connected: true } },
        backend_links: [{ role: "camera", connected: true, error: null }],
        busy_lanes: [],
        looping: false,
      },
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
async function mount(): Promise<void> {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  await act(async () => { rootRef!.render(createElement(ProfileList)); });
  await flush();
}
function card(name: string): any {
  const rename = container.querySelector(`[aria-label="Rename ${name}"]`);
  if (!rename) throw new Error(`no card rendered for "${name}"`);
  return rename.parentElement.parentElement;
}
const activateBtn = (name: string): any =>
  [...card(name).querySelectorAll("button")]
    .find((b: any) => /activate|reconnect|connecting/i.test(b.textContent || ""));
const confirmReq = () => (useStore.getState() as any).confirm;
const commands = () => asked.filter((a) => a.method !== "GET");

const SENTENCE = "A sequence is running, and that teardown aborts it.";
/** The whole `SequenceState["state"]` union, with whether the rig's
 *  `engine.running` is true in it. */
const STATES: Array<[string, boolean]> = [
  ["running", true], ["paused", true], ["holding", true], ["aborting", true],
  ["idle", false], ["complete", false], ["aborted", false], ["error", false],
  ["nina_native", false],
];

for (const [state, live] of STATES) {
  await testAsync(`run ${state}: Activate's dialog ${live ? "says" : "does not say"} a sequence is running`, async () => {
    seed({ state });
    await mount();
    assert(activateBtn("Planetary rig") != null,
      "no Activate control on the profile row (nothing below grades a blank page)");
    await act(async () => {
      activateBtn("Planetary rig").dispatchEvent(
        new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    // The pre-flight getProfile round trip comes first; wait for the dialog.
    const until = Date.now() + 3000;
    while (confirmReq() == null && Date.now() < until) { await act(async () => { await sleep(10); }); }
    const req = confirmReq();
    assert(req != null, "Activate fired with no confirm over a live rig");
    eq(String(req.body).includes(SENTENCE), live, `the dialog with the run ${state}: ${JSON.stringify(req.body)}`);
    eq(req.mode, live ? "hold" : "confirm", `the dialog's mode with the run ${state}`);
    act(() => { useStore.getState().resolveConfirm(false); });
    await flush();
    eq(commands().length, 0, "a cancelled activate still POSTed");
  });
}

if (rootRef) act(() => { rootRef!.unmount(); });

console.log(`w18ProfileListLiveRun: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
