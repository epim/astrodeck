// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w18ProfilesEditorLiveRun.test.tsx - RIG - PROFILES, MOUNTED: ACTIVATE's confirm
// says "a sequence is running" for EVERY live run state, not only running and
// paused (#821, WP-158, wave 18).
//
//   Run directly:  npx tsx src/next/hubs/rig/profiles/__tests__/w18ProfilesEditorLiveRun.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The `sequenceRunning` argument `ProfilesEditor.onActivate` hands
// `profileActivateConfirm` was an IIFE reading `state === "running" || state ===
// "paused"`. A cloud hold ("holding") and an abort's wind-down ("aborting") are
// `engine.running` on the rig, and every activate tears the rig down first, so
// the dialog for a live rig omitted "A sequence is running, and that teardown
// aborts it" and stayed a tap-confirm instead of a hold-to-confirm.
//
// WHAT IS WORTH ASSERTING. The dialog the mounted sheet opens, per state in the
// union, with a control row (a run that is not live says nothing about a run);
// a cancelled dialog posts nothing.
//
// MUTANT "running or paused only" (the IIFE restored). Run from a byte backup,
// restored byte-identically (md5sum compared): see the report for the failing
// lines.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The editor is an area root and imports its own `profiles.css`; Node does not
// know what a `.css` file is (see rigProfilesDom.test.tsx).
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
  { url: "http://local/#/rig/devices/profiles", pretendToBeVisual: true },
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
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "PointerEvent", "Blob", "File",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fixture
interface Asked { method: string; url: string }
const asked: Asked[] = [];
const ROWS = [
  { id: "p-a", name: "Backyard SCT", mode: "alpaca", devices_count: 4,
    site_name: null, active: true, providers: null, optics: null },
  { id: "p-b", name: "Dark site refractor", mode: "mixed", devices_count: 6,
    site_name: null, active: false, providers: null, optics: null },
];
const ok = (json: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });
g.fetch = async (url: string, init?: { method?: string }) => {
  const method = init?.method ?? "GET";
  const u = String(url);
  asked.push({ method, url: u });
  if (method === "GET" && /\/api\/profiles$/.test(u)) return ok(JSON.parse(JSON.stringify(ROWS)));
  const one = /\/api\/profiles\/([^/]+)$/.exec(u);
  if (method === "GET" && one) {
    return ok({
      id: one[1], name: one[1], devices: [{ role: "camera", backend: "sim" }],
      primary_backend: "sim", nina_host: null, site_name: null, optics: null, providers: null,
    });
  }
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { ProfilesSheet } = await import("../../sheets/profiles");

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
const settle = async (rounds = 3) => {
  for (let i = 0; i < rounds; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount",
    "control.reconnect", "config.backend", "config.site_optics"],
};

function seed(sequence: Record<string, unknown>): void {
  asked.length = 0;
  act(() => {
    useStore.setState({
      principal: ADMIN,
      authGate: "open",
      wsPhase: "up",
      equipConnected: true,
      toasts: [],
      confirm: null,
      sequence,
      status: {
        connected: { camera: { name: "sim", connected: true } },
        backend_links: [{ role: "camera", connected: true, error: null }],
        busy_lanes: [],
      },
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(ProfilesSheet as any, {})); });
}
function click(node: any): void {
  assert(node != null, "click on a control that is not rendered");
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
function card(profileId: string): any {
  return q(`[data-profile-id="${profileId}"]`)?.closest('[data-testid="profile-card"]');
}
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
  await testAsync(`run ${state}: ACTIVATE's dialog ${live ? "says" : "does not say"} a sequence is running`, async () => {
    seed({ state });
    mount();
    await settle();
    assert(card("p-b") != null, "the profile cards did not render (nothing below grades a blank page)");
    click(card("p-b").querySelector('[data-testid="profile-activate"]'));
    await settle();
    const req = confirmReq();
    assert(req != null, "ACTIVATE fired with no confirm over three live devices");
    eq(String(req.body).includes(SENTENCE), live, `the dialog with the run ${state}: ${JSON.stringify(req.body)}`);
    eq(req.mode, live ? "hold" : "confirm", `the dialog's mode with the run ${state}`);
    act(() => { useStore.getState().resolveConfirm(false); });
    await settle();
    eq(commands().length, 0, "a cancelled activate still POSTed");
  });
}

if (rootRef) act(() => { rootRef!.unmount(); });

console.log(`w18ProfilesEditorLiveRun: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
