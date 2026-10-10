// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w19SystemEditorsLiveRun.test.tsx - SETTINGS -> SYSTEM in the new UI, MOUNTED:
// the Update and Factory-reset editors block on "a sequence is running" for
// EVERY live run state, not only running and paused (#922, WP-161, wave 19).
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/w19SystemEditorsLiveRun.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. Both editors take their rig-idle verdict from `systemModel.ts`
// `rigBlocker(seqState, busyWord)`, which answered "a sequence is running" only
// for "running" and "paused". The server's `hub.restart_blocker` refuses the
// update and the reset whenever `engine.running` is true, which it is in a cloud
// hold ("holding") and in an abort's wind-down ("aborting") too. So both editors
// armed their button over a live run and the operator met the server's 409.
//
// WHAT IS WORTH ASSERTING. The function over the whole union with a quiet rig
// (`busy` null, the between-subs moment the run was invisible), the busy word
// still winning where no run is live, and then each editor mounted per state:
// the sentence on screen and whether the action is armed.
//
// MUTANT "running or paused only" (`rigBlocker`'s `runIsLive({ state: seqState })`
// made `seqState === "running" || seqState === "paused"`, the unfixed text).
// Run from a byte backup, restored byte-identically (md5sum compared): see the
// report for the failing lines.

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
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "Element",
  "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage",
  "sessionStorage", "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "location", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const UPDATE_STATUS = {
  current: "0.3.28", latest: "0.3.29", update_available: true, channel: "stable",
  phase: "idle", progress: 0, supervised: true, can_apply: true,
  apply_blocked_reason: "", last_check_ts: 1_756_000_000, notes_md: "", last_result: null,
};
const UPDATE_CONFIG = {
  enabled: true, auto_check: true, check_interval_hours: 24, channel: "stable",
  repo: "epim/astrodeck", signing_pubkey: "AAAApubkey", health_timeout_s: 60,
  last_check_ts: null,
};
const RESET_PREVIEW = {
  site_is_default: false, profiles: 2, plans: 1, drivers: 3, alert_sinks: 0, users: 1,
  remote_paired: false, update_credential: false,
  captures: { frames: 40, entries: 3, bytes: 4096 }, preserved_capture_entries: [],
  can_reset: true, blocked_reason: "",
};
const ok = (body: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => body });
g.fetch = async (url: string) => {
  const u = String(url);
  if (u.includes("/api/update/status")) return ok(UPDATE_STATUS);
  if (u.includes("/api/system/factory-reset")) return ok(RESET_PREVIEW);
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { UpdateEditor, FactoryResetEditor, rigBlocker } = await import("../tuning/system");

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
const flush = async () => { for (let i = 0; i < 8; i++) await act(async () => { await Promise.resolve(); }); };

/** The whole `SequenceState["state"]` union, with whether the rig's
 *  `engine.running` is true in it. */
const STATES: Array<[string, boolean]> = [
  ["running", true], ["paused", true], ["holding", true], ["aborting", true],
  ["idle", false], ["complete", false], ["aborted", false], ["error", false],
  ["nina_native", false],
];

// ------------------------------------------------------------ the pure verdict
for (const [state, live] of STATES) {
  test(`rigBlocker, run ${state}: ${live ? "a sequence is running" : "no run to name"}`, () => {
    eq(rigBlocker(state as never, null), live ? "a sequence is running" : null,
      `rigBlocker(${state}, null)`);
  });
}
test("rigBlocker: a busy word still names the rig where no run is live, and the run wins where one is", () => {
  eq(rigBlocker("idle", "capturing"), "rig is capturing", "idle + busy");
  eq(rigBlocker("holding", "capturing"), "a sequence is running", "holding + busy");
  eq(rigBlocker(undefined, null), null, "no sequence slice at all");
  eq(rigBlocker(null, "slewing"), "rig is slewing", "null sequence + busy");
});

// ------------------------------------------------------------- the editors
function seed(sequence: Record<string, unknown>): void {
  act(() => {
    useStore.setState({
      principal: {
        role: "admin", email: null,
        caps: ["view.status", "system.update", "admin.users", "config.safety"],
      },
      authGate: "open",
      wsPhase: "up",
      toasts: [],
      confirm: null,
      sequence,
      config: { update: UPDATE_CONFIG } as never,
      update: UPDATE_STATUS as never,
      status: { connected: {}, looping: false, busy: null, busy_lanes: [] },
    } as never);
  });
}
const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
async function mount(el: unknown): Promise<void> {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  await act(async () => { rootRef!.render(el as never); });
  await flush();
}
const q = (id: string): any => container.querySelector(`[data-testid="${id}"]`);
const armed = (node: any): boolean =>
  node != null && node.getAttribute("aria-disabled") !== "true" && !node.disabled;

for (const [state, live] of STATES) {
  await testAsync(`UPDATE editor, run ${state}: UPGRADE is ${live ? "blocked on the run" : "not blocked by a run"}`, async () => {
    seed({ state });
    await mount(createElement(UpdateEditor as never));
    assert(q("update-apply") != null, "no UPGRADE button (nothing below grades a blank page)");
    if (live) {
      assert(!armed(q("update-apply")), `UPGRADE is ARMED while the run is ${state}`);
      assert(/a sequence is running/.test(String(q("update-blocked")?.textContent ?? "")),
        `the editor does not say why: "${q("update-blocked")?.textContent ?? ""}"`);
    } else {
      assert(armed(q("update-apply")), `UPGRADE is blocked by a run that is ${state}`);
      assert(q("update-blocked") == null, `the editor names a block while the run is ${state}`);
    }
  });
}

for (const [state, live] of STATES) {
  await testAsync(`FACTORY RESET editor, run ${state}: ${live ? "refuses while a sequence is running" : "names no run"}`, async () => {
    seed({ state });
    await mount(createElement(FactoryResetEditor as never));
    assert(q("reset-run") != null, "no reset button (nothing below grades a blank page)");
    const reason = String(q("reset-reason")?.textContent ?? "");
    if (live) {
      assert(/Cannot reset while a sequence is running\./.test(reason),
        `the editor does not block on the run (${state}): "${reason}"`);
    } else {
      assert(!/a sequence is running/.test(reason), `the editor claims a run while it is ${state}: "${reason}"`);
    }
  });
}

if (rootRef) act(() => { rootRef!.unmount(); });

console.log(`w19SystemEditorsLiveRun: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
