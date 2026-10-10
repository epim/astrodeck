// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w19UpdateResetLiveRun.test.tsx - CLASSIC Settings -> System, MOUNTED: the
// Update panel and the Factory-reset panel block on "a sequence is running" for
// EVERY live run state, not only running and paused (#922, WP-161, wave 19).
//
//   Run directly:  npx tsx src/components/settings/__tests__/w19UpdateResetLiveRun.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. Both panels derived `rigBlocker` as `seqState === "running" ||
// seqState === "paused" ? "a sequence is running" : rig.busy ? ... : null`. A
// cloud hold ("holding", the long-lived state on a cloudy night) and an abort's
// wind-down ("aborting") are `engine.running` on the rig, and the server's own
// `hub.restart_blocker` refuses the update and the reset in both, but the UI
// blocked only on `rig.busy`, which flickers to null between subs. So an
// operator was offered a live Upgrade or a live Reset over a run that was still
// going, and learned it from the server's 409 after the press.
//
// WHAT IS WORTH ASSERTING. Per state in the union, with an idle-run control row
// (a run that is not live says nothing about a run, so a predicate that is
// always true fails): the sentence the operator reads, and, for the Update
// panel, that the Upgrade button is disabled exactly then.
//
// MUTANT "running or paused only" (each panel's `runIsLive(sequence)` made
// `sequence.state === "running" || sequence.state === "paused"`, the unfixed
// text). Run from a byte backup, restored byte-identically (md5sum compared):
// see the report for the failing lines.

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
  "Node", "Event", "CustomEvent", "MouseEvent", "localStorage", "sessionStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle", "location",
  "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fixture
const UPDATE_STATUS = {
  current: "0.2.6", latest: "0.2.7", update_available: true, phase: "idle",
  progress: 0, last_check_ts: null, channel: "stable", notes_md: null,
  can_apply: true, apply_blocked_reason: "", supervised: true,
  last_result: null, error: null,
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
const json = (body: unknown) => ({
  ok: true, status: 200, statusText: "OK", json: async () => body,
});
g.fetch = async (url: string) => {
  const u = String(url);
  if (u.includes("/api/update/status")) return json(UPDATE_STATUS);
  if (u.includes("/api/system/factory-reset")) return json(RESET_PREVIEW);
  return json({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const UpdatePanel = (await import("../UpdatePanel")).default;
const FactoryResetPanel = (await import("../FactoryResetPanel")).default;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const flush = async () => { for (let i = 0; i < 8; i++) await act(async () => { await Promise.resolve(); }); };

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
      // A rig that is not busy with a single exposure: between subs `busy` is
      // null, which is the very moment the run was invisible to the UI.
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
const text = (): string => (container.textContent || "").replace(/\s+/g, " ");
const upgradeButton = (): any =>
  ([...container.querySelectorAll("button")] as any[]).find((b) => /^\s*Upgrade/.test(b.textContent || ""));

/** The whole `SequenceState["state"]` union, with whether the rig's
 *  `engine.running` is true in it. */
const STATES: Array<[string, boolean]> = [
  ["running", true], ["paused", true], ["holding", true], ["aborting", true],
  ["idle", false], ["complete", false], ["aborted", false], ["error", false],
  ["nina_native", false],
];

// ------------------------------------------------------------- Update panel
for (const [state, live] of STATES) {
  await testAsync(`Update, run ${state}: Upgrade is ${live ? "blocked on" : "not blocked by"} a running sequence`, async () => {
    seed({ state });
    await mount(createElement(UpdatePanel as never));
    const btn = upgradeButton();
    assert(btn != null, `no Upgrade button (nothing below grades a blank page): ${text().slice(0, 200)}`);
    if (live) {
      assert(btn.disabled === true, `Upgrade is ARMED while the run is ${state}: the server refuses it`);
      assert(/Can't install now: a sequence is running/.test(text()),
        `the panel does not say why: ${text().slice(-300)}`);
    } else {
      assert(btn.disabled === false, `Upgrade is disabled by a run that is ${state}: ${text().slice(-300)}`);
      assert(!/a sequence is running/.test(text()), `the panel claims a run while it is ${state}`);
    }
  });
}

// --------------------------------------------------------- Factory reset
for (const [state, live] of STATES) {
  await testAsync(`Factory reset, run ${state}: the panel ${live ? "refuses" : "does not refuse"} while a sequence is running`, async () => {
    seed({ state });
    await mount(createElement(FactoryResetPanel as never));
    assert(/Factory reset|FACTORY RESET|RESET/.test(text()), `the panel did not render: ${text().slice(0, 200)}`);
    if (live) {
      assert(/Can't reset while a sequence is running\./.test(text()),
        `the panel does not block on the run (${state}): ${text().slice(-400)}`);
    } else {
      assert(!/a sequence is running/.test(text()), `the panel claims a run while it is ${state}`);
    }
  });
}

if (rootRef) act(() => { rootRef!.unmount(); });

console.log(`w19UpdateResetLiveRun: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
